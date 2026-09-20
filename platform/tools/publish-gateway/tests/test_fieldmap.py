"""get_fieldmap 的归一化与结构指纹。"""

import unittest

from pubgw.fieldmap import (
    FINGERPRINT_VERSION,
    binding_map,
    fingerprint,
    parse_fieldmap,
    signature_lines,
)

from .fixtures import fieldmap_for, meta_rows, sample_definition


class ParseTest(unittest.TestCase):
    def test_drops_the_response_table_meta_rows(self):
        rows = parse_fieldmap(meta_rows())

        self.assertEqual((), rows)

    def test_keeps_question_rows_in_order(self):
        rows = parse_fieldmap(fieldmap_for(sample_definition()))

        self.assertEqual("QSINGLE", rows[0].code)
        self.assertEqual("", rows[0].aid)
        self.assertEqual("other", rows[1].aid)

    def test_missing_scale_id_normalises_to_zero(self):
        rows = parse_fieldmap(fieldmap_for(sample_definition()))

        self.assertEqual(0, rows[0].scale)
        dual = [row for row in rows if row.code == "QDUAL"]
        self.assertEqual([0, 1], [row.scale for row in dual])


class FingerprintTest(unittest.TestCase):
    def test_signature_drops_every_numeric_identifier(self):
        lines = signature_lines(parse_fieldmap(fieldmap_for(sample_definition())))

        self.assertEqual("L|QSINGLE||0", lines[0])
        self.assertTrue(all("Q1" not in line.split("|")[0] for line in lines))

    def test_fingerprint_is_stable_across_different_engine_identifiers(self):
        definition = sample_definition()
        first = fingerprint(signature_lines(parse_fieldmap(fieldmap_for(definition, qid_base=100))))
        second = fingerprint(signature_lines(parse_fieldmap(fieldmap_for(definition, qid_base=900))))

        self.assertEqual(first, second)

    def test_fingerprint_changes_when_a_question_code_changes(self):
        definition = sample_definition()
        before = fingerprint(signature_lines(parse_fieldmap(fieldmap_for(definition))))
        after = fingerprint(
            signature_lines(parse_fieldmap(fieldmap_for(definition, renamed={"QSINGLE": "QDRIFT"})))
        )

        self.assertNotEqual(before, after)

    def test_fingerprint_carries_its_algorithm_version(self):
        value = fingerprint(("L|Q1||0",))

        self.assertTrue(value.startswith(FINGERPRINT_VERSION + ":"))

    def test_meta_rows_do_not_influence_the_fingerprint(self):
        """activate_tokens 会往 fieldmap 里加 token 列，指纹不能因此漂移。"""
        definition = sample_definition()
        rows = fieldmap_for(definition)
        with_token = dict(rows)
        with_token["token"] = {"fieldname": "token", "type": "token", "sid": 1, "gid": "", "qid": ""}

        self.assertEqual(
            fingerprint(signature_lines(parse_fieldmap(rows))),
            fingerprint(signature_lines(parse_fieldmap(with_token))),
        )


class BindingMapTest(unittest.TestCase):
    def test_maps_every_platform_uuid_to_its_code_and_field_names(self):
        definition = sample_definition()
        bindings = binding_map(definition, parse_fieldmap(fieldmap_for(definition)))

        by_uuid = {binding.uuid: binding for binding in bindings}
        self.assertEqual({"q-single", "q-text", "q-multi", "q-dual"}, set(by_uuid))
        self.assertEqual("QDUAL", by_uuid["q-dual"].code)
        self.assertEqual(2, len(by_uuid["q-dual"].fields))
        self.assertEqual((0, 1), tuple(item.scale for item in by_uuid["q-dual"].fields))
        self.assertTrue(all(item.fieldname for item in by_uuid["q-single"].fields))

    def test_a_question_the_engine_never_returned_yields_no_fields(self):
        definition = sample_definition()
        rows = parse_fieldmap(fieldmap_for(definition, renamed={"QMULTI": "QOTHER"}))
        bindings = binding_map(definition, rows)

        by_uuid = {binding.uuid: binding for binding in bindings}
        self.assertEqual((), by_uuid["q-multi"].fields)


if __name__ == "__main__":
    unittest.main()
