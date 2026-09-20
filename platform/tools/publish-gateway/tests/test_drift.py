"""漂移检查：激活后改题目代码，引擎接受、答卷表不变、平台必须自己发现。"""

import unittest

from pubgw.binding import BindingRecord
from pubgw.drift import check_drift
from pubgw.fieldmap import parse_fieldmap
from pubgw.publish import Publisher
from pubgw.rpc import RemoteControlClient

from .fakes import FakeEngine
from .fixtures import sample_definition


def published():
    definition = sample_definition()
    engine = FakeEngine(definition)
    client = RemoteControlClient(engine.transport)
    client.login("admin", "password")
    result = Publisher(client, engine_instance="survey-test-web").publish(definition)
    return definition, engine, result.binding


def fieldmap_of(engine, record, rename=None):
    engine.rename = dict(rename or {})
    return parse_fieldmap(engine._get_fieldmap("k", record.survey_id))


class NoDriftTest(unittest.TestCase):
    def test_an_untouched_survey_does_not_drift(self):
        _, engine, record = published()

        report = check_drift(record, fieldmap_of(engine, record))

        self.assertFalse(report.drifted)
        self.assertEqual((), report.renamed)

    def test_the_report_carries_both_fingerprints(self):
        _, engine, record = published()

        report = check_drift(record, fieldmap_of(engine, record))

        self.assertEqual(record.fingerprint, report.recorded_fingerprint)
        self.assertEqual(record.fingerprint, report.current_fingerprint)


class RenameDriftTest(unittest.TestCase):
    def setUp(self):
        _, self.engine, self.record = published()
        self.report = check_drift(
            self.record, fieldmap_of(self.engine, self.record, {"QSINGLE": "HACKED"})
        )

    def test_a_post_publication_rename_is_detected(self):
        self.assertTrue(self.report.drifted)

    def test_the_fingerprint_changes(self):
        self.assertNotEqual(self.report.recorded_fingerprint, self.report.current_fingerprint)

    def test_the_rename_is_reported_per_field_name(self):
        """字段名在一次激活内稳定，所以能精确说出是哪道题被改了名。"""
        self.assertEqual((("q-single", "QSINGLE", "HACKED"),), self.report.renamed)

    def test_the_response_columns_are_unchanged_which_is_exactly_the_danger(self):
        before = {field.fieldname for item in self.record.questions for field in item.fields}
        after = {row.fieldname for row in fieldmap_of(self.engine, self.record, {"QSINGLE": "HACKED"})}

        self.assertEqual(before, after)
        self.assertTrue(self.report.drifted)

    def test_the_report_serialises(self):
        payload = self.report.to_dict()

        self.assertTrue(payload["drifted"])
        self.assertEqual([{"uuid": "q-single", "from": "QSINGLE", "to": "HACKED"}], payload["renamed"])


class StructuralDriftTest(unittest.TestCase):
    def test_a_disappeared_field_is_reported(self):
        _, engine, record = published()
        engine.drop_fields = (("QMULTI", "SQ002", 0),)

        report = check_drift(record, fieldmap_of(engine, record))

        self.assertTrue(report.drifted)
        self.assertIn("E_FIELD_DISAPPEARED", [issue.code for issue in report.issues])

    def test_an_empty_fieldmap_is_reported_as_a_vanished_survey(self):
        _, _, record = published()

        report = check_drift(record, ())

        self.assertTrue(report.drifted)
        self.assertIn("E_SURVEY_EMPTY", [issue.code for issue in report.issues])


class RecordRoundTripTest(unittest.TestCase):
    def test_a_binding_record_survives_a_json_round_trip(self):
        _, _, record = published()

        restored = BindingRecord.from_dict(record.to_dict())

        self.assertEqual(record, restored)


if __name__ == "__main__":
    unittest.main()
