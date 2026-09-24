"""HTTP 外壳：真起一个 ThreadingHTTPServer，用真实线程与真实套接字验证契约。"""

import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid

from pubgw import server
from pubgw.server import MAX_BODY_BYTES, build_server, load_settings

from .gateway_support import (
    ENGINE_PASSWORD,
    SECRET,
    BlockingEngine,
    encode,
    envelope,
    make_service,
    new_engine,
    signed_headers,
)


def now_headers(body):
    """HTTP 测试里服务端走真实时钟，签名也取当下时间。"""
    return signed_headers(body, timestamp=int(time.time()))


class RunningServer:
    def __init__(self, engine, state_dir):
        service = make_service(engine, state_dir, clock=time.time)
        self.httpd = build_server(service, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            connection.close()

    def publish(self, payload=None, body=None):
        body = encode(payload if payload is not None else envelope()) if body is None else body
        status, headers, raw = self.request("POST", "/v1/publish", body, now_headers(body))
        return status, headers, json.loads(raw.decode("utf-8"))


class ServerTestCase(unittest.TestCase):
    engine_class = None

    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.engine = new_engine(self.engine_class) if self.engine_class else new_engine()
        self.server = RunningServer(self.engine, self.state_dir)

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.state_dir)


class HealthTest(ServerTestCase):
    def test_healthz_needs_no_authentication(self):
        status, headers, raw = self.server.request("GET", "/healthz")

        self.assertEqual(200, status)
        self.assertEqual({"status": "ok"}, json.loads(raw))
        self.assertTrue(headers["Content-Type"].startswith("application/json"))

    def test_healthz_exposes_no_configuration(self):
        _, _, raw = self.server.request("GET", "/healthz")

        self.assertNotIn(b"hd-engine-01", raw)

    def test_the_server_header_does_not_leak_the_python_version(self):
        _, headers, _ = self.server.request("GET", "/healthz")

        self.assertNotIn("Python", headers.get("Server", ""))


class PublishOverHttpTest(ServerTestCase):
    def test_publishes_and_answers_200(self):
        status, headers, body = self.server.publish()

        self.assertEqual(200, status)
        self.assertEqual("published", body["status"])
        self.assertTrue(headers["Content-Type"].startswith("application/json"))

    def test_unsigned_request_is_401(self):
        body = encode(envelope())
        status, _, raw = self.server.request(
            "POST", "/v1/publish", body, {"Content-Type": "application/json"}
        )

        self.assertEqual(401, status)
        self.assertEqual({"error": "missing_timestamp"}, json.loads(raw))
        self.assertEqual([], self.engine.calls)

    def raw_post(self, headers, payload=b""):
        """只发请求头（和可选的少量正文），不让客户端替我们补 Content-Length。"""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=15)
        try:
            connection.putrequest("POST", "/v1/publish")
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            if payload:
                connection.send(payload)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_a_body_over_one_mebibyte_is_refused_before_reading_it(self):
        # 只声明长度、不发正文：若服务端试图读正文就会卡到读超时，而不是立刻 400
        status, raw = self.raw_post(
            {"Content-Type": "application/json", "Content-Length": str(MAX_BODY_BYTES + 1)}
        )

        self.assertEqual(400, status)
        self.assertEqual({"error": "invalid_request"}, json.loads(raw))
        self.assertEqual([], self.engine.calls)

    def test_a_body_of_exactly_one_mebibyte_is_read(self):
        body = b"{" + b" " * (MAX_BODY_BYTES - 2) + b"}"
        status, _, raw = self.server.request("POST", "/v1/publish", body, now_headers(body))

        self.assertEqual(400, status)  # 读到了、验签通过了，只是缺字段
        self.assertEqual({"error": "invalid_request"}, json.loads(raw))

    def test_a_request_without_content_length_is_refused(self):
        status, _ = self.raw_post(
            {"Content-Type": "application/json", "Transfer-Encoding": "chunked"}, b"0\r\n\r\n"
        )

        self.assertEqual(400, status)

    def test_unknown_path_is_404(self):
        status, _, raw = self.server.request("GET", "/v1/instances")

        self.assertEqual(404, status)
        self.assertEqual({"error": "not_found"}, json.loads(raw))

    def test_wrong_method_is_405(self):
        status, _, _ = self.server.request("GET", "/v1/publish")

        self.assertEqual(405, status)

    def test_no_response_contains_the_engine_password(self):
        responses = [
            self.server.publish(),
            self.server.publish(envelope(instance="nowhere-01")),
            self.server.publish(body=b"{bad"),
        ]
        for status, _, body in responses:
            self.assertNotIn(ENGINE_PASSWORD, json.dumps(body))


class OperationsOverHttpTest(ServerTestCase):
    """契约 v1.2 的两个接口走同一个 HTTP 外壳（同样的大小限制、同样的认证）。"""

    def post(self, path, payload):
        body = encode(payload)
        status, _, raw = self.server.request("POST", path, body, now_headers(body))
        return status, json.loads(raw)

    def test_close_and_drift_check_are_routed(self):
        _, _, published = self.server.publish()
        binding = published["result"]["binding"]

        status, closed = self.post("/v1/close", {"requestId": str(uuid.uuid4()),
                                                 "engineInstanceId": binding["engineInstance"],
                                                 "surveyId": binding["surveyId"]})
        self.assertEqual((200, "closed"), (status, closed["status"]))

        status, checked = self.post("/v1/drift-check", {"engineInstanceId": binding["engineInstance"],
                                                        "surveyId": binding["surveyId"],
                                                        "expectedFingerprint": binding["fingerprint"],
                                                        "binding": binding})
        self.assertEqual((200, "match"), (status, checked["status"]))

    def test_get_on_the_new_paths_is_405(self):
        for path in ("/v1/close", "/v1/drift-check"):
            with self.subTest(path=path):
                status, _, _ = self.server.request("GET", path)
                self.assertEqual(405, status)

    def test_unsigned_close_is_401(self):
        body = encode({"requestId": str(uuid.uuid4()), "engineInstanceId": "hd-engine-01", "surveyId": 1})
        status, _, _ = self.server.request("POST", "/v1/close", body, {"Content-Type": "application/json"})

        self.assertEqual(401, status)
        self.assertEqual([], self.engine.calls)


class ConcurrentOverHttpTest(ServerTestCase):
    engine_class = BlockingEngine

    def test_second_concurrent_publish_of_the_same_definition_is_409(self):
        first = {}

        def publish_first():
            status, _, body = self.server.publish()
            first.update(status=status, body=body)

        worker = threading.Thread(target=publish_first)
        worker.start()
        try:
            self.assertTrue(self.engine.entered.wait(timeout=5))
            status, _, body = self.server.publish()
        finally:
            self.engine.release.set()
            worker.join(timeout=15)

        self.assertEqual(409, status)
        self.assertEqual({"status": "conflict", "error": "publish_in_progress"}, body)
        self.assertEqual(200, first["status"])
        self.assertEqual(1, self.engine.methods().count("import_survey"))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.config = os.path.join(self.directory, "engines.json")
        with open(self.config, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "hd-engine-01": {
                        "rpcUrl": "http://engine/index.php/admin/remotecontrol",
                        "user": "admin",
                        "passwordEnv": "ENGINE_PW",
                    }
                },
                handle,
            )
        self.env = {
            "PUBGW_SHARED_SECRET": SECRET.decode(),
            "PUBGW_ENGINES_CONFIG": self.config,
            "PUBGW_STATE_DIR": os.path.join(self.directory, "state"),
            "ENGINE_PW": ENGINE_PASSWORD,
        }

    def tearDown(self):
        shutil.rmtree(self.directory)

    def refused(self, **overrides):
        env = {k: v for k, v in dict(self.env, **overrides).items() if v is not None}
        with self.assertRaises(server.StartupError) as caught:
            load_settings(env)
        return str(caught.exception)

    def test_loads_a_complete_configuration(self):
        settings = load_settings(self.env)

        self.assertEqual(SECRET, settings.secret)
        self.assertEqual(["hd-engine-01"], sorted(settings.engines))
        self.assertEqual(8080, settings.port)
        self.assertEqual("127.0.0.1", settings.host)

    def test_refuses_to_start_without_a_secret(self):
        self.refused(PUBGW_SHARED_SECRET=None)

    def test_refuses_to_start_with_a_short_secret(self):
        message = self.refused(PUBGW_SHARED_SECRET="x" * 31)

        self.assertNotIn("x" * 31, message)

    def test_refuses_to_start_without_an_engines_config(self):
        self.refused(PUBGW_ENGINES_CONFIG=None)

    def test_refuses_to_start_without_a_state_dir(self):
        self.refused(PUBGW_STATE_DIR=None)

    def test_refuses_to_start_with_a_bad_port(self):
        self.refused(PUBGW_PORT="http")

    def test_retention_defaults_to_a_week_of_receipts_and_a_quarter_of_tombstones(self):
        retention = load_settings(self.env).retention

        self.assertEqual(7 * 24 * 3600, retention.result_seconds)
        self.assertEqual(90 * 24 * 3600, retention.tombstone_seconds)

    def test_an_explicit_retention_window_is_honoured(self):
        retention = load_settings(dict(self.env, PUBGW_RESULT_TTL_SECONDS="172800",
                                       PUBGW_TOMBSTONE_TTL_SECONDS="864000")).retention

        self.assertEqual(172800, retention.result_seconds)
        self.assertEqual(864000, retention.tombstone_seconds)

    def test_refuses_to_start_with_a_non_numeric_retention_window(self):
        self.refused(PUBGW_RESULT_TTL_SECONDS="a week")

    def test_refuses_to_start_with_a_receipt_window_below_the_floor(self):
        message = self.refused(PUBGW_RESULT_TTL_SECONDS="3600")

        self.assertIn("result retention window", message)
        self.assertIn("86400", message)

    def test_refuses_to_start_when_tombstones_are_shorter_than_receipts(self):
        message = self.refused(PUBGW_RESULT_TTL_SECONDS="604800", PUBGW_TOMBSTONE_TTL_SECONDS="86400")

        self.assertIn("tombstone retention window", message)

    def test_the_invitation_window_defaults_to_a_day(self):
        self.assertEqual(24 * 3600, load_settings(self.env).retention.invitation_seconds)

    def test_an_explicit_invitation_window_is_honoured(self):
        settings = load_settings(dict(self.env, PUBGW_INVITATION_TTL_SECONDS="7200"))

        self.assertEqual(7200, settings.retention.invitation_seconds)

    def test_refuses_to_start_when_codes_would_outlive_the_receipt(self):
        message = self.refused(PUBGW_RESULT_TTL_SECONDS="86400", PUBGW_INVITATION_TTL_SECONDS="172800")

        self.assertIn("invitation retention window", message)

    def test_a_bad_retention_value_names_the_variable_that_is_wrong(self):
        self.assertIn("PUBGW_TOMBSTONE_TTL_SECONDS", self.refused(PUBGW_TOMBSTONE_TTL_SECONDS="forever"))

    def test_main_exits_with_2_when_misconfigured(self):
        with self.assertLogs("pubgw", level="ERROR"):
            self.assertEqual(2, server.main({}))


if __name__ == "__main__":
    unittest.main()
