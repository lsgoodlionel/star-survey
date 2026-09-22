"""RemoteControl 客户端：错误形状不统一，客户端必须逐种识别。"""

import http.server
import json
import threading
import unittest

from pubgw.rpc import HttpTransport, RemoteControlClient, RpcError

from .fakes import FakeEngine
from .fixtures import sample_definition


def client_for(engine):
    return RemoteControlClient(engine.transport)


class CallTest(unittest.TestCase):
    def test_sends_a_json_rpc_envelope(self):
        captured = []

        def transport(payload):
            captured.append(json.loads(payload.decode("utf-8")))
            return json.dumps({"id": 1, "result": "ok", "error": None}).encode("utf-8")

        RemoteControlClient(transport).call("get_session_key", ["admin", "password"])

        self.assertEqual("get_session_key", captured[0]["method"])
        self.assertEqual(["admin", "password"], captured[0]["params"])

    def test_returns_the_result_field(self):
        engine = FakeEngine(sample_definition())

        self.assertEqual("fake-session-key", client_for(engine).call("get_session_key", ["admin", "p"]))

    def test_raises_on_a_transport_level_json_rpc_error(self):
        def transport(payload):
            return json.dumps({"id": 1, "result": None, "error": "boom"}).encode("utf-8")

        with self.assertRaises(RpcError):
            RemoteControlClient(transport).call("whatever", [])

    def test_raises_on_a_body_that_is_not_json(self):
        with self.assertRaises(RpcError):
            RemoteControlClient(lambda payload: b"<html>500</html>").call("whatever", [])


class CheckedCallTest(unittest.TestCase):
    """引擎的失败应答有三种形状，全部必须被识别。"""

    def test_accepts_a_scalar_result(self):
        engine = FakeEngine(sample_definition())
        key = client_for(engine).checked("get_session_key", ["admin", "password"])

        self.assertEqual("fake-session-key", key)

    def test_rejects_a_result_carrying_an_error_code(self):
        engine = FakeEngine(sample_definition(), fail_activate=True)
        engine._import_survey("k", "data", "lss")

        with self.assertRaises(RpcError) as caught:
            client_for(engine).checked("activate_survey", ["k", 511001])

        self.assertIn("activate_survey", str(caught.exception))

    def test_rejects_a_status_that_is_not_ok(self):
        engine = FakeEngine(sample_definition())

        with self.assertRaises(RpcError):
            client_for(engine).checked("get_session_key", ["nobody", "password"])

    def test_accepts_a_per_field_boolean_table(self):
        engine = FakeEngine(sample_definition())
        result = client_for(engine).checked("set_survey_properties", ["k", 1, {"admin": "X"}])

        self.assertEqual({"admin": True}, result)

    def test_accepts_the_fieldmap_dictionary(self):
        engine = FakeEngine(sample_definition())
        result = client_for(engine).checked("get_fieldmap", ["k", 1])

        self.assertIn("id", result)


class SessionTest(unittest.TestCase):
    def test_login_stores_the_session_key_and_logout_releases_it(self):
        engine = FakeEngine(sample_definition())
        client = client_for(engine)

        client.login("admin", "password")
        self.assertEqual("fake-session-key", client.session_key)
        client.logout()

        self.assertIsNone(client.session_key)
        self.assertIn("release_session_key", engine.methods())

    def test_session_scoped_calls_inject_the_key(self):
        engine = FakeEngine(sample_definition())
        client = client_for(engine)
        client.login("admin", "password")

        client.import_survey("<document/>")

        method, params = engine.calls[-1]
        self.assertEqual("import_survey", method)
        self.assertEqual("fake-session-key", params[0])

    def test_calling_without_a_session_is_an_error(self):
        engine = FakeEngine(sample_definition())

        with self.assertRaises(RpcError):
            client_for(engine).import_survey("<document/>")

    def test_logout_is_safe_when_never_logged_in(self):
        engine = FakeEngine(sample_definition())
        client_for(engine).logout()

        self.assertEqual([], engine.methods())


class HelperMethodTest(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine(sample_definition())
        self.client = client_for(self.engine)
        self.client.login("admin", "password")

    def test_import_survey_base64_encodes_the_document(self):
        self.client.import_survey("<document/>")

        _, params = self.engine.calls[-1]
        self.assertEqual("PGRvY3VtZW50Lz4=", params[1])
        self.assertEqual("lss", params[2])

    def test_import_survey_returns_an_integer_sid(self):
        sid = self.client.import_survey("<document/>")

        self.assertIsInstance(sid, int)

    def test_import_survey_rejects_a_result_that_is_not_a_survey_id(self):
        """引擎返回了没有 status 的怪东西时不能炸在 int() 上，要变成 RpcError。"""
        self.engine.import_result = {"unexpected": "shape"}

        with self.assertRaises(RpcError):
            self.client.import_survey("<document/>")

    def test_add_participants_rejects_a_per_participant_error(self):
        """失败的参与者藏在列表里，外层看起来是成功的。"""
        self.engine.participant_errors = True

        with self.assertRaises(RpcError) as caught:
            self.client.add_participants(1, [{"email": "bad"}])

        self.assertIn("add_participants", str(caught.exception))

    def test_add_participants_accepts_a_clean_list(self):
        created = self.client.add_participants(1, [{"email": "p0@example.invalid"}])

        self.assertEqual(1, len(created))

    def test_delete_survey_reports_success(self):
        sid = self.client.import_survey("<document/>")
        self.client.delete_survey(sid)

        self.assertIn(sid, self.engine.deleted)


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass


class HttpTransportTest(unittest.TestCase):
    """真实套接字上的传输层：任何网络异常都必须变成 RpcError，发布编排才会回滚。"""

    def serve(self, handler_class):
        httpd = http.server.HTTPServer(("127.0.0.1", 0), handler_class)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return "http://127.0.0.1:{}".format(httpd.server_address[1])

    def test_posts_to_the_full_endpoint_when_no_path_is_appended(self):
        seen = []

        class Handler(_QuietHandler):
            def do_POST(self):
                seen.append(self.path)
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"id":1,"result":"OK","error":null}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        transport = HttpTransport(self.serve(Handler) + "/custom/rpc", rpc_path="")

        self.assertEqual("OK", RemoteControlClient(transport).call("x", []))
        self.assertEqual(["/custom/rpc"], seen)

    def test_a_read_timeout_becomes_an_rpc_error(self):
        release = threading.Event()

        class Handler(_QuietHandler):
            def do_POST(self):
                release.wait(timeout=5)

        transport = HttpTransport(self.serve(Handler), timeout=0.2)
        self.addCleanup(release.set)  # 后注册先执行：先放行处理器，再关服务器

        with self.assertRaises(RpcError):
            transport(b"{}")

    def test_a_refused_connection_becomes_an_rpc_error(self):
        with self.assertRaises(RpcError):
            HttpTransport("http://127.0.0.1:9", timeout=2)(b"{}")


if __name__ == "__main__":
    unittest.main()
