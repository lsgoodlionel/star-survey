"""POST /v1/publish 的业务语义：逐条对照 platform/contracts/publish-gateway-v1.md。"""

import json
import shutil
import tempfile
import threading
import unittest

from .gateway_support import (
    ENGINE_PASSWORD,
    INSTANCE,
    NOW,
    BlockingEngine,
    PasswordEchoEngine,
    encode,
    envelope,
    invalid_definition,
    make_service,
    new_engine,
    signed_headers,
)
from .fixtures import sample_payload

CONTRACT_RESULT_KEYS = {
    "ok", "surveyId", "failedStage", "failures", "rolledBack",
    "orphanSurveyId", "steps", "binding", "verification",
}


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.bodies = []

    def tearDown(self):
        shutil.rmtree(self.state_dir)
        for body in self.bodies:
            self.assert_no_password(body)

    def assert_no_password(self, body):
        text = body.decode("utf-8")
        self.assertNotIn(ENGINE_PASSWORD, text)
        self.assertNotIn(json.dumps(ENGINE_PASSWORD)[1:-1], text)

    def send(self, service, payload=None, body=None, headers=None):
        body = encode(payload if payload is not None else envelope()) if body is None else body
        response = service.publish(signed_headers(body) if headers is None else headers, body)
        self.bodies.append(response.body)
        return response.status, json.loads(response.body.decode("utf-8"))


class PublishedTest(ServiceTestCase):
    def test_200_carries_the_publish_result_and_binding(self):
        engine = new_engine()
        status, body = self.send(make_service(engine, self.state_dir))

        self.assertEqual(200, status)
        self.assertEqual("published", body["status"])
        self.assertEqual(CONTRACT_RESULT_KEYS, set(body["result"]))
        self.assertTrue(body["result"]["ok"])
        self.assertEqual(INSTANCE, body["result"]["binding"]["engineInstance"])
        self.assertEqual("def-0001", body["result"]["binding"]["definitionUuid"])
        self.assertEqual("Y", engine.surveys[body["result"]["surveyId"]]["active"])

    def test_logs_in_with_the_configured_account_and_releases_the_session(self):
        engine = new_engine()
        self.send(make_service(engine, self.state_dir))

        method, params = engine.calls[0]
        self.assertEqual(("get_session_key", ["admin", ENGINE_PASSWORD]), (method, params))
        self.assertEqual("release_session_key", engine.methods()[-1])


class RejectedTest(ServiceTestCase):
    def test_422_when_validation_fails_and_the_engine_is_never_called(self):
        engine = new_engine()
        status, body = self.send(
            make_service(engine, self.state_dir), envelope(definition=invalid_definition())
        )

        self.assertEqual(422, status)
        self.assertEqual("rejected", body["status"])
        self.assertEqual("validate", body["result"]["failedStage"])
        self.assertIsNone(body["result"]["surveyId"])
        self.assertEqual([], engine.calls)


class FailedTest(ServiceTestCase):
    def test_502_rolled_back_when_activation_fails(self):
        engine = new_engine(fail_activate=True)
        status, body = self.send(make_service(engine, self.state_dir))

        self.assertEqual(502, status)
        self.assertEqual("failed", body["status"])
        self.assertEqual("activate", body["result"]["failedStage"])
        self.assertTrue(body["result"]["rolledBack"])
        self.assertIsNone(body["result"]["orphanSurveyId"])
        self.assertEqual([body["result"]["surveyId"]], engine.deleted)

    def test_502_with_an_orphan_when_the_rollback_also_fails(self):
        engine = new_engine(fail_activate=True, fail_delete=True)
        status, body = self.send(make_service(engine, self.state_dir))

        self.assertEqual(502, status)
        self.assertFalse(body["result"]["rolledBack"])
        self.assertEqual(body["result"]["surveyId"], body["result"]["orphanSurveyId"])

    def test_502_when_the_engine_refuses_the_login(self):
        engine = new_engine(PasswordEchoEngine)
        status, body = self.send(make_service(engine, self.state_dir))

        self.assertEqual(502, status)
        self.assertIsNone(body["result"]["surveyId"])
        self.assertFalse(body["result"]["rolledBack"])
        self.assertNotIn("import_survey", engine.methods())

    def test_the_engine_password_is_redacted_even_when_the_engine_echoes_it(self):
        engine = new_engine(PasswordEchoEngine)
        status, body = self.send(make_service(engine, self.state_dir))

        self.assertIn("***", " ".join(body["result"]["failures"]))
        # tearDown 断言原文与 JSON 转义形式都不在响应里


class ConflictTest(ServiceTestCase):
    def test_409_for_the_same_definition_on_the_same_instance_at_the_same_time(self):
        engine = new_engine(BlockingEngine)
        service = make_service(engine, self.state_dir)
        first = {}

        worker = threading.Thread(target=lambda: first.update(zip(("status", "body"), self.send(service))))
        worker.start()
        try:
            self.assertTrue(engine.entered.wait(timeout=5), "first publish never reached the engine")
            status, body = self.send(service)
        finally:
            engine.release.set()
            worker.join(timeout=10)

        self.assertEqual(409, status)
        self.assertEqual({"status": "conflict", "error": "publish_in_progress"}, body)
        self.assertEqual(200, first["status"])
        self.assertEqual(1, engine.methods().count("import_survey"))

    def test_the_lock_is_released_after_the_publish_finishes(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        self.send(service)

        status, _ = self.send(service)

        self.assertEqual(200, status)

    def test_the_lock_is_released_after_a_failed_publish(self):
        engine = new_engine(fail_activate=True)
        service = make_service(engine, self.state_dir)
        self.send(service)
        engine.fail_activate = False

        status, _ = self.send(service)

        self.assertEqual(200, status)

    def test_different_definitions_publish_concurrently(self):
        engine = new_engine(BlockingEngine)
        service = make_service(engine, self.state_dir)
        other = sample_payload()
        other["uuid"] = "def-0002"
        results = []

        worker = threading.Thread(target=lambda: results.append(self.send(service)[0]))
        worker.start()
        try:
            self.assertTrue(engine.entered.wait(timeout=5))
            second = threading.Thread(
                target=lambda: results.append(self.send(service, envelope(definition=other))[0])
            )
            second.start()
        finally:
            engine.release.set()
            worker.join(timeout=10)
            second.join(timeout=10)

        self.assertEqual([200, 200], results)


class IdempotencyTest(ServiceTestCase):
    def test_a_repeated_request_id_returns_the_first_result_without_publishing(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        payload = envelope(request_id="0b0d3f2e-1111-4111-8111-000000000001")

        first = self.send(service, payload)
        imports = engine.methods().count("import_survey")
        calls = len(engine.calls)
        second = self.send(service, payload)

        self.assertEqual(first, second)
        self.assertEqual(1, imports)
        self.assertEqual(calls, len(engine.calls))

    def test_replay_survives_a_restart(self):
        engine = new_engine()
        payload = envelope()
        first = self.send(make_service(engine, self.state_dir), payload)
        calls = len(engine.calls)

        second = self.send(make_service(engine, self.state_dir), payload)

        self.assertEqual(first, second)
        self.assertEqual(calls, len(engine.calls))

    def test_a_failed_result_is_replayed_too(self):
        engine = new_engine(fail_activate=True)
        service = make_service(engine, self.state_dir)
        payload = envelope()
        first = self.send(service, payload)
        engine.fail_activate = False

        second = self.send(service, payload)

        self.assertEqual(first, second)
        self.assertEqual(502, second[0])

    def test_a_rejected_result_is_replayed_too(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        payload = envelope(definition=invalid_definition())

        self.assertEqual(self.send(service, payload), self.send(service, payload))

    def test_replay_does_not_depend_on_json_formatting(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        payload = envelope()
        first = self.send(service, payload)

        reformatted = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        second = self.send(service, body=reformatted)

        self.assertEqual(first, second)
        self.assertEqual(1, engine.methods().count("import_survey"))

    def test_a_reused_request_id_with_a_different_body_is_rejected(self):
        engine = new_engine()
        service = make_service(engine, self.state_dir)
        payload = envelope()
        self.send(service, payload)
        changed = dict(payload, definition=dict(payload["definition"], title="another"))

        status, body = self.send(service, changed)

        self.assertEqual(400, status)
        self.assertEqual({"error": "invalid_request"}, body)
        self.assertEqual(1, engine.methods().count("import_survey"))

    def test_a_retry_while_the_first_is_still_running_gets_409_and_does_not_publish(self):
        engine = new_engine(BlockingEngine)
        service = make_service(engine, self.state_dir)
        payload = envelope()
        first = {}

        worker = threading.Thread(target=lambda: first.update(status=self.send(service, payload)[0]))
        worker.start()
        try:
            self.assertTrue(engine.entered.wait(timeout=5))
            status, _ = self.send(service, payload)
        finally:
            engine.release.set()
            worker.join(timeout=10)

        self.assertEqual(409, status)
        self.assertEqual(200, first["status"])
        self.assertEqual(200, self.send(service, payload)[0])
        self.assertEqual(1, engine.methods().count("import_survey"))


class UnknownInstanceTest(ServiceTestCase):
    def test_404_without_touching_any_engine(self):
        engine = new_engine()
        status, body = self.send(
            make_service(engine, self.state_dir), envelope(instance="nowhere-01")
        )

        self.assertEqual(404, status)
        self.assertEqual({"error": "unknown_engine_instance"}, body)
        self.assertEqual([], engine.calls)


class InvalidRequestTest(ServiceTestCase):
    def assert_invalid(self, payload=None, body=None, headers=None):
        engine = new_engine()
        status, response = self.send(
            make_service(engine, self.state_dir), payload=payload, body=body, headers=headers
        )
        self.assertEqual(400, status)
        self.assertEqual({"error": "invalid_request"}, response)
        self.assertEqual([], engine.calls)

    def test_body_is_not_json(self):
        self.assert_invalid(body=b"{not json")

    def test_body_is_not_utf8(self):
        self.assert_invalid(body=b'{"requestId": "\xff"}')

    def test_body_is_not_an_object(self):
        self.assert_invalid(payload=[envelope()])

    def test_content_type_is_not_json(self):
        body = encode(envelope())
        headers = dict(signed_headers(body), **{"Content-Type": "text/plain"})
        self.assert_invalid(body=body, headers=headers)

    def test_content_type_with_charset_is_accepted(self):
        engine = new_engine()
        body = encode(envelope())
        headers = dict(signed_headers(body), **{"Content-Type": "application/json; charset=utf-8"})

        status, _ = self.send(make_service(engine, self.state_dir), body=body, headers=headers)

        self.assertEqual(200, status)

    def test_request_id_is_missing(self):
        payload = envelope()
        del payload["requestId"]
        self.assert_invalid(payload)

    def test_request_id_is_not_a_uuid(self):
        self.assert_invalid(envelope(request_id="not-a-uuid"))

    def test_engine_instance_is_missing(self):
        payload = envelope()
        del payload["engineInstanceId"]
        self.assert_invalid(payload)

    def test_engine_instance_is_not_a_string(self):
        self.assert_invalid(dict(envelope(), engineInstanceId=7))

    def test_definition_is_missing(self):
        payload = envelope()
        del payload["definition"]
        self.assert_invalid(payload)

    def test_definition_is_structurally_broken(self):
        definition = sample_payload()
        del definition["uuid"]
        self.assert_invalid(envelope(definition=definition))

    def test_definition_has_an_unknown_version(self):
        self.assert_invalid(envelope(definition=dict(sample_payload(), definitionVersion=99)))

    def test_unexpected_top_level_fields(self):
        self.assert_invalid(dict(envelope(), extra=True))


class AuthenticationTest(ServiceTestCase):
    def assert_unauthorized(self, headers, reason, body=None):
        engine = new_engine()
        body = body if body is not None else encode(envelope())
        status, response = self.send(make_service(engine, self.state_dir), body=body, headers=headers)
        self.assertEqual(401, status)
        self.assertEqual({"error": reason}, response)
        self.assertEqual([], engine.calls)

    def test_401_without_signature_headers(self):
        self.assert_unauthorized({"Content-Type": "application/json"}, "missing_timestamp")

    def test_401_without_the_signature(self):
        body = encode(envelope())
        headers = signed_headers(body)
        del headers["X-Pubgw-Signature"]
        self.assert_unauthorized(headers, "missing_signature", body)

    def test_401_with_a_bad_signature(self):
        body = encode(envelope())
        headers = signed_headers(b"something else")
        self.assert_unauthorized(headers, "bad_signature", body)

    def test_401_with_a_stale_timestamp(self):
        body = encode(envelope())
        self.assert_unauthorized(signed_headers(body, timestamp=NOW - 301), "stale_timestamp", body)

    def test_header_names_are_case_insensitive(self):
        engine = new_engine()
        body = encode(envelope())
        headers = {name.lower(): value for name, value in signed_headers(body).items()}

        status, _ = self.send(make_service(engine, self.state_dir), body=body, headers=headers)

        self.assertEqual(200, status)

    def test_authentication_is_checked_before_the_body_is_parsed(self):
        self.assert_unauthorized({}, "missing_timestamp", body=b"{not json")


class UnexpectedErrorTest(ServiceTestCase):
    def test_a_gateway_bug_is_a_500_without_a_traceback_and_is_not_repeated(self):
        def broken(*args):
            raise RuntimeError("simulated gateway bug")

        engine = new_engine()
        engine._list_questions = broken  # 让编排在 apply 阶段抛出非 RpcError 的异常
        service = make_service(engine, self.state_dir)
        payload = envelope()

        with self.assertLogs("pubgw", level="ERROR"):
            status, body = self.send(service, payload)
        imports = engine.methods().count("import_survey")
        replay = self.send(service, payload)

        self.assertEqual(500, status)
        self.assertEqual({"error": "internal_error"}, body)
        self.assertEqual((status, body), replay)
        self.assertEqual(imports, engine.methods().count("import_survey"))


if __name__ == "__main__":
    unittest.main()
