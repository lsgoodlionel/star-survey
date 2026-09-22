"""契约 v1.2 增补的两个接口：POST /v1/close 与 POST /v1/drift-check（platform/contracts/publish-gateway-v1.2.md）。"""

import json
import shutil
import tempfile
import unittest
import uuid

from .fakes import FakeEngine
from .fixtures import sample_payload
from .gateway_support import (
    ENGINE_PASSWORD,
    INSTANCE,
    NOW,
    PasswordEchoEngine,
    encode,
    make_service,
    new_engine,
    signed_headers,
)

#: NOW（1_800_000_000）是 2027-01-15 08:00:00 UTC；过期时间回拨一天。
EXPECTED_EXPIRES = "2027-01-14 08:00:00"


class MissingSurveyEngine(FakeEngine):
    def _get_fieldmap(self, key, sid, language=None):
        return {"status": "Error: Invalid survey ID", "error_code": "ERR_INVALID_SURVEY"}

    def _get_survey_properties(self, key, sid, properties=None):
        return {"status": "Error: Invalid survey ID", "error_code": "ERR_INVALID_SURVEY"}


class BrokenFieldmapEngine(FakeEngine):
    def _get_fieldmap(self, key, sid, language=None):
        return {"status": "No permission", "error_code": "ERR_NO_PERMISSION"}


class OperationsTestCase(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.bodies = []

    def tearDown(self):
        shutil.rmtree(self.state_dir)
        for body in self.bodies:
            text = body.decode("utf-8")
            self.assertNotIn(ENGINE_PASSWORD, text)
            self.assertNotIn(json.dumps(ENGINE_PASSWORD)[1:-1], text)

    def call(self, operation, service, payload=None, body=None, headers=None):
        body = encode(payload) if body is None else body
        handler = getattr(service, operation)
        response = handler(signed_headers(body) if headers is None else headers, body)
        self.bodies.append(response.body)
        return response.status, json.loads(response.body.decode("utf-8"))

    def publish(self, service):
        payload = {
            "requestId": str(uuid.uuid4()),
            "engineInstanceId": INSTANCE,
            "definition": sample_payload(),
        }
        status, body = self.call("publish", service, payload)
        self.assertEqual(200, status, body)
        return body["result"]["binding"]


def close_payload(sid, instance=INSTANCE):
    return {"requestId": str(uuid.uuid4()), "engineInstanceId": instance, "surveyId": sid}


def drift_payload(record, **overrides):
    payload = {
        "engineInstanceId": record["engineInstance"],
        "surveyId": record["surveyId"],
        "expectedFingerprint": record["fingerprint"],
        "binding": record,
    }
    payload.update(overrides)
    return payload


class CloseTest(OperationsTestCase):
    def test_200_closed_with_the_expiry_the_engine_now_holds(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        sid = self.publish(service)["surveyId"]

        status, body = self.call("close", service, close_payload(sid))

        self.assertEqual(200, status, body)
        self.assertEqual("closed", body["status"])
        self.assertEqual({"surveyId": sid, "expires": EXPECTED_EXPIRES, "alreadyClosed": False}, body["result"])
        self.assertEqual("Y", engine.surveys[sid]["active"])
        self.assertEqual([], engine.deleted)

    def test_closing_twice_is_harmless_and_reports_already_closed(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        sid = self.publish(service)["surveyId"]
        self.call("close", service, close_payload(sid))

        status, body = self.call("close", service, close_payload(sid))

        self.assertEqual(200, status)
        self.assertTrue(body["result"]["alreadyClosed"])
        self.assertEqual(EXPECTED_EXPIRES, body["result"]["expires"])

    def test_502_when_the_engine_survey_does_not_exist(self):
        engine = new_engine(MissingSurveyEngine)

        status, body = self.call("close", make_service(engine, self.state_dir), close_payload(987654))

        self.assertEqual(502, status)
        self.assertEqual({"status": "failed", "error": "E_SURVEY_MISSING"}, {k: body[k] for k in ("status", "error")})

    def test_502_with_the_password_redacted_when_the_engine_refuses_the_login(self):
        engine = new_engine(PasswordEchoEngine)

        status, body = self.call("close", make_service(engine, self.state_dir), close_payload(511001))

        self.assertEqual(502, status)
        self.assertEqual("engine_error", body["error"])
        self.assertIn("***", body["detail"])

    def test_404_for_an_unknown_instance_without_calling_any_engine(self):
        engine = new_engine()

        status, body = self.call("close", make_service(engine, self.state_dir), close_payload(1, "nowhere-01"))

        self.assertEqual((404, {"error": "unknown_engine_instance"}), (status, body))
        self.assertEqual([], engine.calls)

    def test_401_unsigned_without_calling_any_engine(self):
        engine = new_engine()
        body = encode(close_payload(1))

        status, answer = self.call("close", make_service(engine, self.state_dir), body=body,
                                   headers={"Content-Type": "application/json"})

        self.assertEqual(401, status)
        self.assertEqual([], engine.calls)

    def test_400_for_malformed_bodies(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        for payload in (
            {"requestId": str(uuid.uuid4()), "engineInstanceId": INSTANCE},
            dict(close_payload(1), surveyId="1"),
            dict(close_payload(1), surveyId=0),
            dict(close_payload(1), surveyId=True),
            dict(close_payload(1), requestId="not-a-uuid"),
            dict(close_payload(1), extra=1),
        ):
            with self.subTest(payload=payload):
                self.assertEqual((400, {"error": "invalid_request"}), self.call("close", service, payload))
        self.assertEqual([], engine.calls)


class RepublishTest(OperationsTestCase):
    """重新发布＝同一 definition.uuid 换新 requestId 再发一次：新 sid，旧 sid 原样在线，直到平台收口它。"""

    def test_a_new_version_is_a_new_engine_survey_and_the_old_one_stays_live_until_closed(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        old = self.publish(service)["surveyId"]

        new = self.publish(service)["surveyId"]

        self.assertNotEqual(old, new)
        self.assertEqual(("Y", "Y"), (engine.surveys[old]["active"], engine.surveys[new]["active"]))
        self.assertEqual([], engine.deleted)
        self.assertNotIn("expires", engine.applied_settings.get(old, {}))

        self.call("close", service, close_payload(old))

        self.assertEqual(EXPECTED_EXPIRES, engine.applied_settings[old]["expires"])
        self.assertNotIn("expires", engine.applied_settings.get(new, {}))


class DriftCheckTest(OperationsTestCase):
    def setUp(self):
        super().setUp()
        self.engine = new_engine()
        self.service = make_service(self.engine, self.state_dir)
        self.binding = self.publish(self.service)

    def test_an_untouched_survey_matches(self):
        status, body = self.call("drift_check", self.service, drift_payload(self.binding))

        self.assertEqual(200, status, body)
        self.assertEqual("match", body["status"])
        result = body["result"]
        self.assertFalse(result["drifted"])
        self.assertEqual(self.binding["fingerprint"], result["currentFingerprint"])
        self.assertEqual(self.binding["fingerprint"], result["expectedFingerprint"])
        self.assertEqual([], result["issues"])
        self.assertEqual("Y", result["active"])

    def test_a_question_code_edited_in_the_engine_admin_is_named(self):
        self.engine.rename = {"QSINGLE": "QDRIFTED"}

        status, body = self.call("drift_check", self.service, drift_payload(self.binding))

        self.assertEqual(200, status, body)
        self.assertEqual("drift", body["status"])
        result = body["result"]
        self.assertTrue(result["drifted"])
        self.assertNotEqual(self.binding["fingerprint"], result["currentFingerprint"])
        self.assertEqual([{"uuid": "q-single", "from": "QSINGLE", "to": "QDRIFTED"}], result["renamed"])
        self.assertIn("E_CODE_DRIFT", [issue["code"] for issue in result["issues"]])

    def test_the_check_never_writes_to_the_engine(self):
        self.engine.rename = {"QSINGLE": "QDRIFTED"}
        before = len(self.engine.calls)

        self.call("drift_check", self.service, drift_payload(self.binding))

        methods = [method for method, _ in self.engine.calls[before:]]
        self.assertTrue(set(methods) <= {"get_session_key", "get_fieldmap", "get_survey_properties",
                                         "release_session_key"}, methods)

    def test_without_a_binding_the_fingerprint_alone_decides(self):
        self.engine.rename = {"QSINGLE": "QDRIFTED"}
        payload = drift_payload(self.binding)
        del payload["binding"]

        status, body = self.call("drift_check", self.service, payload)

        self.assertEqual("drift", body["status"])
        self.assertEqual(["E_FINGERPRINT_DRIFT"], [issue["code"] for issue in body["result"]["issues"]])
        self.assertEqual([], body["result"]["renamed"])

    def test_a_deactivated_survey_is_drift(self):
        self.engine.applied_settings[self.binding["surveyId"]] = {"active": "N"}

        status, body = self.call("drift_check", self.service, drift_payload(self.binding))

        self.assertEqual("drift", body["status"])
        self.assertIn("E_SURVEY_NOT_ACTIVE", [issue["code"] for issue in body["result"]["issues"]])

    def test_an_expired_survey_is_not_drift(self):
        self.call("close", self.service, close_payload(self.binding["surveyId"]))

        status, body = self.call("drift_check", self.service, drift_payload(self.binding))

        self.assertEqual("match", body["status"])

    def test_a_deleted_engine_survey_is_drift(self):
        engine = new_engine(MissingSurveyEngine)

        status, body = self.call("drift_check", make_service(engine, self.state_dir), drift_payload(self.binding))

        self.assertEqual(200, status)
        self.assertEqual("drift", body["status"])
        self.assertIsNone(body["result"]["currentFingerprint"])
        self.assertEqual(["E_SURVEY_MISSING"], [issue["code"] for issue in body["result"]["issues"]])

    def test_502_when_the_engine_cannot_be_read(self):
        engine = new_engine(BrokenFieldmapEngine)

        status, body = self.call("drift_check", make_service(engine, self.state_dir), drift_payload(self.binding))

        self.assertEqual(502, status)
        self.assertEqual("failed", body["status"])
        self.assertEqual("engine_error", body["error"])

    def test_400_when_the_binding_does_not_describe_the_named_survey(self):
        for overrides in (
            {"surveyId": self.binding["surveyId"] + 1},
            {"expectedFingerprint": "fm1:0000000000000000"},
            {"engineInstanceId": "other-engine"},
            {"binding": {"surveyId": 1}},
            {"expectedFingerprint": "not a fingerprint"},
        ):
            with self.subTest(overrides=overrides):
                status, body = self.call("drift_check", self.service, drift_payload(self.binding, **overrides))
                self.assertEqual((400, {"error": "invalid_request"}), (status, body))

    def test_404_for_an_unknown_instance(self):
        payload = drift_payload(self.binding, engineInstanceId="nowhere-01")
        payload["binding"] = dict(self.binding, engineInstance="nowhere-01")

        status, body = self.call("drift_check", self.service, payload)

        self.assertEqual((404, {"error": "unknown_engine_instance"}), (status, body))

    def test_401_with_a_stale_signature(self):
        body = encode(drift_payload(self.binding))

        status, _ = self.call("drift_check", self.service, body=body,
                              headers=signed_headers(body, timestamp=NOW - 3600))

        self.assertEqual(401, status)


if __name__ == "__main__":
    unittest.main()
