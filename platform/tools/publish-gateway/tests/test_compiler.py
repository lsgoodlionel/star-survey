"""LSS 编译：结构、显式设置、以及「不能让引擎有改名的借口」。"""

import unittest
import xml.etree.ElementTree as ElementTree

from pubgw.compiler import COMPILER_VERSION, CompileError, LssCompiler

from .fixtures import sample_definition, sample_payload
from pubgw.model import SurveyDefinition


def compile_sample(payload=None):
    definition = SurveyDefinition.from_dict(payload) if payload else sample_definition()
    return LssCompiler().compile(definition)


def parse(compiled):
    return ElementTree.fromstring(compiled.lss)


def rows_of(tree, section):
    node = tree.find(section)
    if node is None:
        return []
    return [{child.tag: (child.text or "") for child in row} for row in node.findall("rows/row")]


class DocumentTest(unittest.TestCase):
    def test_emits_a_survey_document(self):
        tree = parse(compile_sample())

        self.assertEqual("document", tree.tag)
        self.assertEqual("Survey", tree.findtext("LimeSurveyDocType"))
        self.assertTrue(tree.findtext("DBVersion"))

    def test_declares_the_base_language(self):
        tree = parse(compile_sample())

        self.assertEqual(["en"], [node.text for node in tree.findall("languages/language")])

    def test_carries_the_compiler_version(self):
        self.assertEqual(COMPILER_VERSION, compile_sample().compiler_version)


class StructureTest(unittest.TestCase):
    def test_writes_one_group_row_per_group_in_order(self):
        rows = rows_of(parse(compile_sample()), "groups")

        self.assertEqual(2, len(rows))
        self.assertEqual(["1", "2"], [row["group_order"] for row in rows])

    def test_writes_parent_questions_and_subquestions_into_separate_sections(self):
        tree = parse(compile_sample())

        self.assertEqual(
            ["QSINGLE", "QTEXT", "QMULTI", "QDUAL"],
            [row["title"] for row in rows_of(tree, "questions")],
        )
        self.assertEqual(
            ["SQ001", "SQ002", "SQ001"],
            [row["title"] for row in rows_of(tree, "subquestions")],
        )

    def test_subquestions_point_at_their_parent(self):
        tree = parse(compile_sample())
        parents = {row["title"]: row["qid"] for row in rows_of(tree, "questions")}
        subquestions = rows_of(tree, "subquestions")

        self.assertEqual(parents["QMULTI"], subquestions[0]["parent_qid"])
        self.assertEqual(parents["QDUAL"], subquestions[2]["parent_qid"])

    def test_writes_answers_with_their_scale(self):
        rows = rows_of(parse(compile_sample()), "answers")

        dual = [row for row in rows if row["code"] in {"L1", "R1"}]
        self.assertEqual(["0", "1"], [row["scale_id"] for row in dual])

    def test_writes_one_l10n_row_per_question_and_subquestion(self):
        tree = parse(compile_sample())

        self.assertEqual(7, len(rows_of(tree, "question_l10ns")))

    def test_writes_question_attributes(self):
        rows = rows_of(parse(compile_sample()), "question_attributes")

        self.assertEqual([("maximum_chars", "200")], [(row["attribute"], row["value"]) for row in rows])

    def test_marks_mandatory_questions(self):
        rows = rows_of(parse(compile_sample()), "questions")

        flags = {row["title"]: row["mandatory"] for row in rows}
        self.assertEqual("Y", flags["QSINGLE"])
        self.assertEqual("N", flags["QTEXT"])

    def test_marks_the_other_option(self):
        rows = rows_of(parse(compile_sample()), "questions")

        self.assertEqual("Y", {row["title"]: row["other"] for row in rows}["QSINGLE"])

    def test_identifiers_are_unique_within_the_document(self):
        tree = parse(compile_sample())
        qids = [row["qid"] for row in rows_of(tree, "questions") + rows_of(tree, "subquestions")]

        self.assertEqual(len(qids), len(set(qids)))


class ExplicitSettingsTest(unittest.TestCase):
    def test_never_writes_an_inherit_marker(self):
        row = rows_of(parse(compile_sample()), "surveys")[0]

        self.assertNotIn("I", [row[name] for name in row if name != "language"])
        self.assertNotIn("inherit", row.values())
        self.assertNotIn("-1", row.values())

    def test_fills_unspecified_settings_with_explicit_defaults(self):
        row = rows_of(parse(compile_sample()), "surveys")[0]

        self.assertEqual("N", row["usecaptcha"])
        self.assertEqual("15", row["tokenlength"])

    def test_platform_settings_win_over_the_defaults(self):
        payload = sample_payload()
        payload["settings"]["allowprev"] = "Y"

        row = rows_of(parse(compile_sample(payload)), "surveys")[0]

        self.assertEqual("Y", row["allowprev"])

    def test_writes_the_theme_explicitly(self):
        row = rows_of(parse(compile_sample()), "surveys")[0]

        self.assertEqual("fruity_twentythree", row["template"])

    def test_writes_the_survey_title_into_the_language_settings(self):
        row = rows_of(parse(compile_sample()), "surveys_languagesettings")[0]

        self.assertEqual("P0 发布网关样例", row["surveyls_title"])


class FingerprintTest(unittest.TestCase):
    def test_compiles_the_expected_structure_fingerprint(self):
        compiled = compile_sample()

        self.assertTrue(compiled.fingerprint.startswith("fm1:"))
        self.assertEqual("L|QSINGLE||0", compiled.signature[0])

    def test_two_definitions_with_the_same_structure_share_a_fingerprint(self):
        payload = sample_payload()
        payload["title"] = "改了标题不改结构"
        payload["groups"][0]["questions"][0]["text"] = "换了题干"

        self.assertEqual(compile_sample().fingerprint, compile_sample(payload).fingerprint)

    def test_a_code_change_changes_the_fingerprint(self):
        payload = sample_payload()
        payload["groups"][0]["questions"][0]["code"] = "QSINGLE2"

        self.assertNotEqual(compile_sample().fingerprint, compile_sample(payload).fingerprint)


class SafetyTest(unittest.TestCase):
    def test_refuses_to_compile_a_definition_that_does_not_validate(self):
        payload = sample_payload()
        payload["groups"][0]["questions"][0]["code"] = "Q-BAD"

        with self.assertRaises(CompileError):
            compile_sample(payload)

    def test_escapes_a_cdata_terminator_inside_question_text(self):
        payload = sample_payload()
        payload["groups"][0]["questions"][1]["text"] = "危险 ]]> 文本"

        tree = parse(compile_sample(payload))

        texts = [row["question"] for row in rows_of(tree, "question_l10ns")]
        self.assertIn("危险 ]]> 文本", texts)


if __name__ == "__main__":
    unittest.main()
