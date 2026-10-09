"""Durable, tenant-scoped preview operations and signed public access."""

import json
import shutil
import tempfile
import time
import unittest
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .fixtures import sample_payload
from .gateway_support import INSTANCE, encode, make_service, new_engine, signed_headers
from .gateway_support import MovableClock
from .fakes import FakeEngine
from pubgw.service import PreviewOperationStore
from .test_server import RunningServer, now_headers


class PreviewGatewayTest(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.engine = new_engine()
        self.service = make_service(self.engine, self.state_dir)

    def tearDown(self):
        shutil.rmtree(self.state_dir)

    def payload(self, request_id=None, tenant_id=None, session_id=None, expires_at=None):
        return {
            "tenantId": tenant_id or str(uuid.uuid4()),
            "sessionId": session_id or str(uuid.uuid4()),
            "requestId": request_id or str(uuid.uuid4()),
            "engineInstanceId": INSTANCE,
            "generation": "preview-4b3129a8",
            "expiresAt": expires_at or "2027-01-15T08:30:00Z",
            "definition": sample_payload(),
        }

    def send(self, method, payload):
        body = encode(payload)
        response = getattr(self.service, method)(signed_headers(body), body)
        return response.status, json.loads(response.body.decode("utf-8"))

    @staticmethod
    def identity(payload):
        return {name: payload[name] for name in ("tenantId", "sessionId", "requestId")}

    def test_prepare_registers_sid_before_activation_then_activate_is_idempotent(self):
        payload = self.payload()

        prepared = self.send("preview", payload)
        self.assertEqual(200, prepared[0])
        self.assertEqual("prepared", prepared[1]["status"])
        self.assertNotIn("activate_survey", self.engine.methods())

        activated = self.send("activate_preview", self.identity(payload))
        repeated = self.send("activate_preview", self.identity(payload))

        self.assertEqual(200, activated[0])
        self.assertEqual("ready", activated[1]["status"])
        self.assertEqual(activated, repeated)
        self.assertEqual(1, self.engine.methods().count("import_survey"))
        self.assertEqual(1, self.engine.methods().count("activate_survey"))
        self.assertNotIn("token=", activated[1]["result"]["previewUrl"])

    def test_prepared_operation_survives_gateway_restart(self):
        payload = self.payload()
        self.assertEqual("prepared", self.send("preview", payload)[1]["status"])

        restarted = make_service(self.engine, self.state_dir)
        body = encode(self.identity(payload))
        response = restarted.activate_preview(signed_headers(body), body)

        self.assertEqual(200, response.status)
        self.assertEqual("ready", json.loads(response.body)["status"])
        self.assertEqual(1, self.engine.methods().count("import_survey"))

    def test_same_request_id_is_namespaced_by_tenant_and_session(self):
        request_id = str(uuid.uuid4())
        first = self.payload(request_id=request_id)
        second = self.payload(request_id=request_id)

        self.assertEqual(200, self.send("preview", first)[0])
        self.assertEqual(200, self.send("preview", second)[0])
        self.assertEqual(2, self.engine.methods().count("import_survey"))

    def test_schema_requires_tenant_and_session_and_rejects_unknown_fields(self):
        for payload in (
            {k: v for k, v in self.payload().items() if k != "tenantId"},
            {k: v for k, v in self.payload().items() if k != "sessionId"},
            dict(self.payload(), extra=True),
        ):
            status, body = self.send("preview", payload)
            self.assertEqual(400, status)
            self.assertEqual("invalid_request", body["error"])
        self.assertNotIn("import_survey", self.engine.methods())

    def test_unknown_prepare_result_is_reconciled_by_marker_without_second_import(self):
        payload = self.payload()
        clock = MovableClock()
        operations = PreviewOperationStore(self.state_dir + "/prepare-crash.sqlite3", now=clock)
        service = make_service(self.engine, self.state_dir, clock=clock, preview_operations=operations)
        operations.fail_after_import_once = True

        first = self.send_with(service, "preview", payload)
        clock.advance(PreviewOperationStore.LEASE_SECONDS)
        second = self.send_with(service, "preview", payload)

        self.assertEqual(503, first[0])
        self.assertEqual("creating", first[1]["status"])
        self.assertEqual(200, second[0])
        self.assertEqual("prepared", second[1]["status"])
        self.assertEqual(1, self.engine.methods().count("import_survey"))

    def test_unknown_activation_result_is_reconciled_without_second_activation_or_participant(self):
        payload = self.payload()
        clock = MovableClock()
        operations = PreviewOperationStore(self.state_dir + "/activate-crash.sqlite3", now=clock)
        service = make_service(self.engine, self.state_dir, clock=clock, preview_operations=operations)
        self.assertEqual("prepared", self.send_with(service, "preview", payload)[1]["status"])
        operations.fail_after_activate_once = True

        first = self.send_with(service, "activate_preview", self.identity(payload))
        clock.advance(PreviewOperationStore.LEASE_SECONDS)
        second = self.send_with(service, "activate_preview", self.identity(payload))

        self.assertEqual(503, first[0])
        self.assertEqual("ready", second[1]["status"])
        self.assertEqual(1, self.engine.methods().count("activate_survey"))
        self.assertEqual(1, self.engine.methods().count("add_participants"))

    def test_concurrent_prepare_and_activate_are_single_flight_with_one_durable_result(self):
        engine = BlockingPreviewEngine()
        service = make_service(engine, self.state_dir)
        payload = self.payload()

        with ThreadPoolExecutor(max_workers=8) as pool:
            first = pool.submit(self.send_with, service, "preview", payload)
            self.assertTrue(engine.import_entered.wait(5))
            followers = [pool.submit(self.send_with, service, "preview", payload) for _ in range(5)]
            engine.import_release.set()
            prepared = [first.result(5)] + [future.result(5) for future in followers]

        self.assertTrue(all(status == 200 for status, _ in prepared), prepared)
        self.assertEqual(1, len({body["result"]["surveyId"] for _, body in prepared}))
        self.assertEqual(1, engine.methods().count("import_survey"))

        identity = self.identity(payload)
        with ThreadPoolExecutor(max_workers=8) as pool:
            first = pool.submit(self.send_with, service, "activate_preview", identity)
            self.assertTrue(engine.activate_entered.wait(5))
            followers = [pool.submit(self.send_with, service, "activate_preview", identity) for _ in range(5)]
            engine.activate_release.set()
            activated = [first.result(5)] + [future.result(5) for future in followers]

        self.assertTrue(all(status == 200 for status, _ in activated), activated)
        self.assertEqual(1, len({json.dumps(body, sort_keys=True) for _, body in activated}))
        operation = service._preview_operations.get(
            (payload["tenantId"], payload["sessionId"], payload["requestId"])
        )
        self.assertEqual("tok1", operation.invitation)
        self.assertEqual(1, engine.methods().count("activate_survey"))
        self.assertEqual(1, engine.methods().count("add_participants"))

    def test_concurrent_close_is_single_flight_and_followers_replay_closed(self):
        engine = BlockingPreviewEngine()
        service = make_service(engine, self.state_dir)
        payload = self.payload()
        engine.import_release.set()
        engine.activate_release.set()
        self.assertEqual(200, self.send_with(service, "preview", payload)[0])
        self.assertEqual(200, self.send_with(service, "activate_preview", self.identity(payload))[0])
        identity = self.identity(payload)

        with ThreadPoolExecutor(max_workers=8) as pool:
            first = pool.submit(self.send_with, service, "close_preview", identity)
            self.assertTrue(engine.close_entered.wait(5))
            followers = [pool.submit(self.send_with, service, "close_preview", identity) for _ in range(5)]
            engine.close_release.set()
            closed = [first.result(5)] + [future.result(5) for future in followers]

        self.assertTrue(all(status == 200 for status, _ in closed), closed)
        self.assertEqual(1, len({json.dumps(body, sort_keys=True) for _, body in closed}))
        self.assertEqual(1, engine.close_writes)

    def test_operation_lease_cannot_be_taken_at_120_seconds_but_can_at_its_boundary(self):
        clock = MovableClock()
        operations = PreviewOperationStore(
            self.state_dir + "/lease.sqlite3", now=clock, sleep=lambda seconds: None
        )
        service = make_service(self.engine, self.state_dir, clock=clock, preview_operations=operations)
        payload = self.payload(expires_at="2027-01-15T08:30:00Z")
        self.assertEqual(200, self.send_with(service, "preview", payload)[0])
        self.assertEqual(200, self.send_with(service, "activate_preview", self.identity(payload))[0])
        key = (payload["tenantId"], payload["sessionId"], payload["requestId"])

        self.assertIsNotNone(operations.claim(key, ("ready",), "closing", "owner-a"))
        clock.advance(120)
        self.assertIsNone(operations.claim(key, ("ready",), "closing", "owner-b"))
        clock.advance(PreviewOperationStore.LEASE_SECONDS - 121)
        self.assertIsNone(operations.claim(key, ("ready",), "closing", "owner-b"))
        clock.advance(1)
        takeover = operations.claim(key, ("ready",), "closing", "owner-b")
        self.assertIsNotNone(takeover)
        self.assertEqual("owner-b", takeover.operation_owner)

    @staticmethod
    def send_with(service, method, payload):
        body = encode(payload)
        response = getattr(service, method)(signed_headers(body), body)
        return response.status, json.loads(response.body.decode("utf-8"))


class BlockingPreviewEngine(FakeEngine):
    def __init__(self):
        from .fixtures import sample_definition
        super().__init__(sample_definition())
        self.import_entered = threading.Event()
        self.import_release = threading.Event()
        self.activate_entered = threading.Event()
        self.activate_release = threading.Event()
        self.close_entered = threading.Event()
        self.close_release = threading.Event()
        self.close_writes = 0

    def _import_survey(self, key, data, kind, name=None):
        self.import_entered.set()
        if not self.import_release.wait(5):
            raise AssertionError("import barrier was not released")
        return super()._import_survey(key, data, kind, name)

    def _activate_survey(self, key, sid):
        self.activate_entered.set()
        if not self.activate_release.wait(5):
            raise AssertionError("activate barrier was not released")
        return super()._activate_survey(key, sid)

    def _set_survey_properties(self, key, sid, properties):
        if "expires" in properties:
            self.close_writes += 1
            self.close_entered.set()
            if not self.close_release.wait(5):
                raise AssertionError("close barrier was not released")
        return super()._set_survey_properties(key, sid, properties)


class PreviewHttpRouteTest(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.engine = new_engine()
        self.server = RunningServer(self.engine, self.state_dir)

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.state_dir)

    def payload(self, expires):
        return PreviewGatewayTest.payload(self, expires_at=expires)

    def post(self, path, payload):
        body = encode(payload)
        return self.server.request("POST", path, body, now_headers(body))

    def ready_url(self):
        expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 1800))
        payload = self.payload(expires)
        status, _, raw = self.post("/v1/preview", payload)
        self.assertEqual(200, status, raw)
        status, _, raw = self.post("/v1/preview/activate", PreviewGatewayTest.identity(payload))
        self.assertEqual(200, status, raw)
        return payload, json.loads(raw)["result"]["previewUrl"]

    def test_public_access_validates_signature_at_runtime_and_redirects(self):
        _, url = self.ready_url()
        path = urlsplit(url).path + "?" + urlsplit(url).query

        status, headers, raw = self.server.request("GET", path)

        self.assertEqual(302, status, raw)
        self.assertRegex(headers["Location"], r"/index\.php/\d+\?")
        self.assertIn("token=tok1", headers["Location"])

    def test_tampered_and_expired_public_access_is_rejected(self):
        _, url = self.ready_url()
        split = urlsplit(url)
        query = parse_qs(split.query)
        query["sid"] = [str(int(query["sid"][0]) + 1)]
        tampered = urlunsplit(("", "", split.path, urlencode(query, doseq=True), ""))
        self.assertEqual(403, self.server.request("GET", tampered)[0])

        self.server.httpd.service._now = lambda: time.time() + 7200
        self.assertEqual(410, self.server.request("GET", split.path + "?" + split.query)[0])

    def test_closed_preview_cannot_be_accessed_and_close_is_idempotent(self):
        payload, url = self.ready_url()
        close = PreviewGatewayTest.identity(payload)

        first = self.post("/v1/preview/close", close)
        second = self.post("/v1/preview/close", close)
        split = urlsplit(url)
        status, _, _ = self.server.request("GET", split.path + "?" + split.query)

        self.assertEqual(200, first[0])
        self.assertEqual(first, second)
        self.assertEqual(410, status)


if __name__ == "__main__":
    unittest.main()
