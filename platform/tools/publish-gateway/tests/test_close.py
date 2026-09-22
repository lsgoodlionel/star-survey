"""收口旧版本：给已被新版本取代的引擎问卷设过期时间，不停用、不删除（ADR 0012 决定 3）。"""

import unittest
from datetime import datetime, timezone

from pubgw.close import CloseError, close_survey
from pubgw.rpc import RemoteControlClient

from .fakes import FakeEngine
from .fixtures import sample_definition

NOW = datetime(2026, 9, 22, 8, 30, 0, tzinfo=timezone.utc)


class RefusingEngine(FakeEngine):
    """引擎接受请求但逐字段报告没存上（例如管理员设了晚于过期时间的开始时间）。"""

    def _set_survey_properties(self, key, sid, properties):
        return {name: False for name in properties}


class MissingSurveyEngine(FakeEngine):
    def _get_survey_properties(self, key, sid, properties=None):
        return {"status": "Error: Invalid survey ID", "error_code": "ERR_INVALID_SURVEY"}


def logged_in(engine):
    client = RemoteControlClient(engine.transport)
    client.login("admin", "pw")
    return client


def published(engine):
    sid = engine._import_survey("key", "lss", "lss")
    engine._activate_survey("key", sid)
    return sid


class CloseSurveyTest(unittest.TestCase):
    def test_sets_an_expiry_in_the_past_so_the_runtime_refuses_new_respondents(self):
        engine = FakeEngine(sample_definition())
        sid = published(engine)

        result = close_survey(logged_in(engine), sid, NOW)

        self.assertEqual(sid, result.survey_id)
        self.assertFalse(result.already_closed)
        self.assertEqual("2026-09-21 08:30:00", result.expires)
        self.assertEqual({"expires": "2026-09-21 08:30:00"}, engine.applied_settings[sid])

    def test_never_deactivates_or_deletes_the_old_survey(self):
        engine = FakeEngine(sample_definition())
        sid = published(engine)

        close_survey(logged_in(engine), sid, NOW)

        self.assertEqual("Y", engine.surveys[sid]["active"])
        self.assertNotIn("delete_survey", engine.methods())
        self.assertEqual([], engine.deleted)

    def test_an_already_expired_survey_is_left_alone(self):
        engine = FakeEngine(sample_definition())
        sid = published(engine)
        engine.applied_settings[sid] = {"expires": "2026-09-01 00:00:00"}

        result = close_survey(logged_in(engine), sid, NOW)

        self.assertTrue(result.already_closed)
        self.assertEqual("2026-09-01 00:00:00", result.expires)
        self.assertNotIn("set_survey_properties", engine.methods())

    def test_an_expiry_still_in_the_future_is_brought_forward(self):
        engine = FakeEngine(sample_definition())
        sid = published(engine)
        engine.applied_settings[sid] = {"expires": "2030-01-01 00:00:00"}

        result = close_survey(logged_in(engine), sid, NOW)

        self.assertFalse(result.already_closed)
        self.assertEqual("2026-09-21 08:30:00", result.expires)

    def test_the_engine_not_storing_the_expiry_is_a_failure(self):
        engine = RefusingEngine(sample_definition())
        sid = published(engine)

        with self.assertRaises(CloseError) as caught:
            close_survey(logged_in(engine), sid, NOW)

        self.assertEqual("E_CLOSE_NOT_APPLIED", caught.exception.code)

    def test_a_missing_engine_survey_is_reported_as_such(self):
        engine = MissingSurveyEngine(sample_definition())

        with self.assertRaises(CloseError) as caught:
            close_survey(logged_in(engine), 424242, NOW)

        self.assertEqual("E_SURVEY_MISSING", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
