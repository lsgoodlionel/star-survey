"""隔离运行时预览：复用发布编译链，但不进入正式发布语义。"""

import json
import shutil
import tempfile
import time
import unittest
import uuid

from pubgw.store import ResultStore

from .fixtures import sample_payload
from .gateway_support import INSTANCE, NOW, encode, make_service, new_engine, signed_headers
from .test_server import RunningServer, now_headers


class PreviewGatewayTest(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.engine = new_engine()
        self.service = make_service(self.engine, self.state_dir)

    def tearDown(self):
        shutil.rmtree(self.state_dir)

    def payload(self, request_id=None, generation=None, expires_at=None):
        return {
            "requestId": request_id or str(uuid.uuid4()),
            "engineInstanceId": INSTANCE,
            "generation": generation or "preview-4b3129a8",
            "expiresAt": expires_at or "2027-01-15T08:30:00Z",
            "definition": sample_payload(),
        }

    def send(self, payload):
        body = encode(payload)
        response = self.service.preview(signed_headers(body), body)
        return response.status, json.loads(response.body.decode("utf-8"))

    def test_preview_reuses_publish_stages_and_returns_isolated_binding(self):
        status, body = self.send(self.payload())

        self.assertEqual(200, status)
        self.assertEqual("ready", body["status"])
        self.assertEqual("preview-4b3129a8", body["result"]["generation"])
        self.assertEqual(INSTANCE, body["result"]["engineInstanceId"])
        self.assertGreater(body["result"]["surveyId"], 0)
        self.assertEqual("2027-01-15T08:30:00Z", body["result"]["expiresAt"])
        self.assertRegex(body["result"]["previewUrl"], r"[?&]token=tok1(?:&|$)")
        self.assertRegex(body["result"]["previewUrl"], r"^https?://.+[?&]preview=[0-9a-f]{64}$")
        self.assertIn("activate_survey", self.engine.methods())
        self.assertIn("get_fieldmap", self.engine.methods())

    def test_same_request_id_is_idempotent_and_does_not_import_twice(self):
        payload = self.payload()
        first = self.send(payload)
        second = self.send(payload)

        self.assertEqual(first, second)
        self.assertEqual(1, self.engine.methods().count("import_survey"))

    def test_same_request_id_with_changed_generation_is_rejected(self):
        request_id = str(uuid.uuid4())
        self.assertEqual(200, self.send(self.payload(request_id=request_id))[0])

        status, body = self.send(self.payload(request_id=request_id, generation="preview-other"))

        self.assertEqual(400, status)
        self.assertEqual("invalid_request", body["error"])
        self.assertEqual(1, self.engine.methods().count("import_survey"))

    def test_invalid_ttl_or_generation_never_reaches_the_engine(self):
        for payload in (
            self.payload(generation="formal"),
            self.payload(expires_at="not-a-time"),
            self.payload(expires_at="2027-01-15T07:59:59Z"),
            self.payload(expires_at="2027-01-15T09:00:01Z"),
        ):
            status, body = self.send(payload)
            self.assertEqual(400, status)
            self.assertEqual("invalid_request", body["error"])
        self.assertNotIn("import_survey", self.engine.methods())

    def test_failed_preview_is_rolled_back(self):
        engine = new_engine(fail_activate=True)
        service = make_service(engine, self.state_dir, store=ResultStore(self.state_dir + "/other.sqlite3"))
        payload = self.payload()
        body = encode(payload)

        response = service.preview(signed_headers(body), body)
        result = json.loads(response.body.decode("utf-8"))

        self.assertEqual(502, response.status)
        self.assertEqual("failed", result["status"])
        self.assertTrue(result["result"]["rolledBack"])


class PreviewHttpRouteTest(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.engine = new_engine()
        self.server = RunningServer(self.engine, self.state_dir)

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.state_dir)

    def test_post_preview_is_routed_and_get_is_rejected(self):
        expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 1800))
        payload = PreviewGatewayTest.payload(self, expires_at=expires)
        body = encode(payload)

        status, _, raw = self.server.request("POST", "/v1/preview", body, now_headers(body))
        get_status, headers, _ = self.server.request("GET", "/v1/preview")

        self.assertEqual(200, status, raw)
        self.assertEqual("ready", json.loads(raw)["status"])
        self.assertEqual(405, get_status)
        self.assertEqual("POST", headers["Allow"])


if __name__ == "__main__":
    unittest.main()
