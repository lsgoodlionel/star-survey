"""回读校验：引擎改过的任何一个代码都必须让发布失败。"""

import unittest

from pubgw.compiler import LssCompiler
from pubgw.fieldmap import parse_fieldmap
from pubgw.verify import verify_publication

from .fixtures import fieldmap_for, sample_definition


def verify(definition, raw_fieldmap):
    compiled = LssCompiler().compile(definition)
    return verify_publication(definition, compiled, parse_fieldmap(raw_fieldmap))


def codes(report):
    return [issue.code for issue in report.issues]


class HappyPathTest(unittest.TestCase):
    def test_accepts_a_fieldmap_that_matches_the_definition(self):
        definition = sample_definition()
        report = verify(definition, fieldmap_for(definition))

        self.assertTrue(report.ok, codes(report))

    def test_reports_the_engine_fingerprint_and_it_matches_the_compiled_one(self):
        definition = sample_definition()
        compiled = LssCompiler().compile(definition)
        report = verify(definition, fieldmap_for(definition))

        self.assertEqual(compiled.fingerprint, report.fingerprint)


class RenameTest(unittest.TestCase):
    def test_an_engine_rename_fails_verification(self):
        definition = sample_definition()
        report = verify(definition, fieldmap_for(definition, renamed={"QSINGLE": "r7q0"}))

        self.assertFalse(report.ok)
        self.assertIn("E_CODE_RENAMED", codes(report))

    def test_the_report_names_both_sides_of_the_rename(self):
        definition = sample_definition()
        report = verify(definition, fieldmap_for(definition, renamed={"QSINGLE": "r7q0"}))

        self.assertEqual((("QSINGLE", "r7q0"),), report.renamed)

    def test_a_missing_question_is_reported_even_without_a_rename_partner(self):
        definition = sample_definition()
        rows = fieldmap_for(definition)
        trimmed = {name: row for name, row in rows.items() if row.get("title") != "QTEXT"}
        report = verify(definition, trimmed)

        self.assertIn("E_CODE_MISSING", codes(report))

    def test_an_unexpected_question_code_is_reported(self):
        definition = sample_definition()
        rows = dict(fieldmap_for(definition))
        rows["Q999"] = {
            "fieldname": "Q999",
            "type": "S",
            "sid": 1,
            "gid": 1,
            "qid": 999,
            "aid": "",
            "title": "QGHOST",
        }
        report = verify(definition, rows)

        self.assertIn("E_CODE_UNEXPECTED", codes(report))


class ShapeTest(unittest.TestCase):
    def test_a_missing_subquestion_column_fails_verification(self):
        definition = sample_definition()
        rows = {
            name: row
            for name, row in fieldmap_for(definition).items()
            if not (row.get("title") == "QMULTI" and row.get("aid") == "SQ002")
        }
        report = verify(definition, rows)

        self.assertFalse(report.ok)
        self.assertIn("E_FIELD_MISSING", codes(report))

    def test_a_missing_answer_scale_fails_verification(self):
        definition = sample_definition()
        rows = {
            name: row
            for name, row in fieldmap_for(definition).items()
            if not (row.get("title") == "QDUAL" and row.get("scale_id") == 1)
        }
        report = verify(definition, rows)

        self.assertIn("E_FIELD_MISSING", codes(report))

    def test_a_fingerprint_mismatch_is_reported_even_when_the_sets_match(self):
        """顺序也是结构的一部分：题目顺序变了，作答体验就变了。"""
        definition = sample_definition()
        rows = fieldmap_for(definition)
        reordered = dict(reversed(list(rows.items())))
        report = verify(definition, reordered)

        self.assertIn("E_FINGERPRINT_MISMATCH", codes(report))


class BindingTest(unittest.TestCase):
    def test_successful_verification_exposes_the_uuid_to_field_map(self):
        definition = sample_definition()
        report = verify(definition, fieldmap_for(definition))

        by_uuid = {binding.uuid: binding for binding in report.bindings}
        self.assertEqual("QMULTI", by_uuid["q-multi"].code)
        self.assertEqual(2, len(by_uuid["q-multi"].fields))


if __name__ == "__main__":
    unittest.main()
