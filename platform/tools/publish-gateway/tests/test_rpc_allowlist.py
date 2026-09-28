"""网关可调的 RemoteControl 方法是白名单（ADR 0007 第 3 条、ADR 0021）。

要钉住的事实：``add_response`` 与 ``update_response`` 以**作答者的名义**写答卷，
却绕开 ``beforeSurveyPage``——访问规则、限次、服务端计时、题型服务端校验对它们全部失效
（P0 发现 12、21）。引擎管理员口令只存在网关侧，所以"网关发不出这两个方法"
就等于平台这条路径上它们不存在。

判据是**一个字节都不许出门**：只断言"抛异常"不够，抛之前把请求发出去了照样是漏洞。
因此每个用例都检查假传输层收到的请求列表是空的。

同时钉住白名单没有把现有能力关掉：发布、回读、收口、导出、撤销用到的每个方法都必须在名单里，
且 :mod:`pubgw` 里实际发出的每个方法名也都必须在名单里（漏加立刻变红，而不是上线才发现）。
"""

import json
import re
import unittest
from pathlib import Path

from pubgw.rpc import ALLOWED_METHODS, FORBIDDEN_METHODS, ForbiddenMethod, RemoteControlClient, RpcError

PUBGW_DIR = Path(__file__).resolve().parents[1] / "pubgw"
#: 网关源码里 self.call("x", ...) / client.checked("x", ...) 形式的方法名字面量。
_CALL_LITERAL = re.compile(r"\.(?:call|checked)\(\s*\"([a-z_]+)\"")


class RecordingTransport:
    """记录收到的每一个请求；白名单生效时它应该一条都收不到。"""

    def __init__(self):
        self.requests = []

    def __call__(self, payload: bytes) -> bytes:
        self.requests.append(json.loads(payload.decode("utf-8")))
        return json.dumps({"id": 1, "result": {"status": "OK"}, "error": None}).encode("utf-8")


class ForbiddenMethodTest(unittest.TestCase):
    def test_add_response_never_reaches_the_engine(self):
        transport = RecordingTransport()
        client = RemoteControlClient(transport)
        client.session_key = "session"

        with self.assertRaises(ForbiddenMethod):
            client.call("add_response", ["session", 511001, {"QNOTE": "smuggled"}])

        self.assertEqual([], transport.requests, "禁用方法的请求居然发出去了")

    def test_update_response_never_reaches_the_engine(self):
        transport = RecordingTransport()
        client = RemoteControlClient(transport)
        client.session_key = "session"

        with self.assertRaises(ForbiddenMethod):
            client.call("update_response", ["session", 511001, {"id": 1, "QNOTE": "rewritten"}])

        self.assertEqual([], transport.requests, "禁用方法的请求居然发出去了")

    def test_checked_is_guarded_too(self):
        """``checked`` 走的也是 ``call``，不能只堵一个入口。"""
        transport = RecordingTransport()

        with self.assertRaises(ForbiddenMethod):
            RemoteControlClient(transport).checked("add_response", ["session", 1, {}])

        self.assertEqual([], transport.requests)

    def test_any_unlisted_method_is_refused(self):
        """白名单而不是黑名单：没想到的写接口（删答卷、装插件、建账号）同样发不出去。"""
        transport = RecordingTransport()
        client = RemoteControlClient(transport)
        for method in ("delete_response", "set_question_properties", "add_user", "import_plugin", ""):
            with self.subTest(method=method):
                with self.assertRaises(ForbiddenMethod):
                    client.call(method, [])
        self.assertEqual([], transport.requests)

    def test_forbidden_method_is_an_rpc_error(self):
        """发布编排只认 RpcError；换成别的异常会绕过回滚，在引擎里留下孤儿问卷。"""
        with self.assertRaises(RpcError):
            RemoteControlClient(RecordingTransport()).call("add_response", [])

    def test_the_reason_never_leaks_the_parameters(self):
        """参数里有会话 key 与答案值，异常消息不得回显。"""
        try:
            RemoteControlClient(RecordingTransport()).call("add_response", ["secret-session-key", 1, {"Q": "answer"}])
        except ForbiddenMethod as error:
            message = str(error)
        self.assertNotIn("secret-session-key", message)
        self.assertNotIn("answer", message)


class AllowlistShapeTest(unittest.TestCase):
    def test_the_two_bypass_methods_are_not_allowed(self):
        self.assertEqual(frozenset(), ALLOWED_METHODS & FORBIDDEN_METHODS)
        self.assertEqual({"add_response", "update_response"}, set(FORBIDDEN_METHODS))

    def test_every_method_the_gateway_actually_calls_is_allowed(self):
        """源码里出现的每个方法名都必须在白名单里——否则那条能力上线就会 500。

        **包含 rpc.py**：业务代码调的是 ``client.get_fieldmap(...)`` 这类包装方法，
        真正写下方法名字面量的地方几乎全在 rpc.py 里。把它排除掉，这条扫描就几乎
        扫不到任何东西，成了一条"看起来在守、其实在验空集"的测试。
        """
        called = set()
        for path in sorted(PUBGW_DIR.rglob("*.py")):
            called |= set(_CALL_LITERAL.findall(path.read_text(encoding="utf-8")))
        missing = called - ALLOWED_METHODS
        self.assertEqual(set(), missing, "这些方法网关会发，但不在白名单里：{}".format(sorted(missing)))
        # 下限按发布链路的规模定：真扫到了就不止一两个，正则写坏了会立刻掉到下限以下。
        self.assertGreaterEqual(len(called), 10, "只扫到 {}：正则或扫描范围坏了".format(sorted(called)))

    def test_the_publish_and_read_paths_are_still_complete(self):
        """白名单不能把既有功能关掉：发布七阶段、回读、收口、导出、撤销用到的方法都在。"""
        required = {
            "get_session_key", "release_session_key",
            "import_survey", "activate_survey", "activate_tokens", "add_participants", "delete_survey",
            "get_fieldmap", "list_questions", "get_survey_properties", "set_survey_properties",
            "export_responses",
            "get_participant_properties", "delete_participants",
        }
        self.assertEqual(set(), required - ALLOWED_METHODS)


if __name__ == "__main__":
    unittest.main()
