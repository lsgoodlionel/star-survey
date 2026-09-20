"""RemoteControl（JSON-RPC）客户端。

引擎的失败应答有三种形状，任何一种被当成成功都会让发布链路继续往下走：

1. ``{"status": "...", "error_code": N}``——大多数守卫；
2. ``{"status": "Invalid session key"}``——没有 error_code 的老式写法；
3. HTTP 200 ＋ 非 JSON 正文——RPC 接口没开或被重定向到登录页；
4. **成功形状里夹着失败**——``add_participants`` 返回的是一个列表，
   每个没建成的参与者在自己那一条里多一个 ``errors`` 键
   （``remotecontrol_handle.php:2130-2134``），外层看起来完全正常。

传输层是一个 ``callable(bytes) -> bytes``，便于单元测试替换成假引擎，
也便于端到端脚本把请求转发进容器。
"""

import base64
import json
from typing import Any, Callable, Dict, List, Optional, Sequence

Transport = Callable[[bytes], bytes]

_OK_STATUSES = ("OK", "success")
_RPC_PATH = "/index.php/admin/remotecontrol"


class RpcError(RuntimeError):
    """引擎拒绝了这次调用，或者应答根本不是 RemoteControl 的形状。"""

    def __init__(self, method: str, detail: Any):
        super().__init__("RemoteControl {} failed: {}".format(method, detail))
        self.method = method
        self.detail = detail


class HttpTransport:
    """标准库 urllib 传输，生产部署用这个。"""

    def __init__(self, base_url: str, timeout: int = 180):
        self._url = base_url.rstrip("/") + _RPC_PATH
        self._timeout = timeout

    def __call__(self, payload: bytes) -> bytes:
        from urllib.error import URLError
        from urllib.request import Request, urlopen

        request = Request(self._url, data=payload, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return response.read()
        except URLError as error:
            raise RpcError("transport", "{} ({})".format(error, self._url)) from None


class RemoteControlClient:
    """一次会话内的 RemoteControl 调用。"""

    def __init__(self, transport: Transport):
        self._transport = transport
        self.session_key: Optional[str] = None

    # --------------------------------------------------------- 底层调用

    def call(self, method: str, params: Sequence[Any]) -> Any:
        payload = json.dumps({"method": method, "params": list(params), "id": 1}).encode("utf-8")
        body = self._transport(payload)
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise RpcError(method, "response is not JSON: {!r}".format(body[:200])) from None
        if not isinstance(decoded, dict) or "result" not in decoded:
            raise RpcError(method, "response has no result field: {!r}".format(decoded))
        if decoded.get("error") is not None:
            raise RpcError(method, decoded["error"])
        return decoded["result"]

    def checked(self, method: str, params: Sequence[Any]) -> Any:
        result = self.call(method, params)
        if not is_accepted(result):
            raise RpcError(method, result)
        return result

    # ----------------------------------------------------------- 会话

    def login(self, username: str, password: str) -> str:
        key = self.checked("get_session_key", [username, password])
        if not isinstance(key, str):
            raise RpcError("get_session_key", key)
        self.session_key = key
        return key

    def logout(self) -> None:
        if self.session_key is None:
            return
        try:
            self.call("release_session_key", [self.session_key])
        finally:
            self.session_key = None

    def _session(self) -> str:
        if self.session_key is None:
            raise RpcError("session", "not logged in")
        return self.session_key

    # ------------------------------------------------------- 发布链路

    def import_survey(self, lss: str, name: Optional[str] = None) -> int:
        encoded = base64.b64encode(lss.encode("utf-8")).decode("ascii")
        params: List[Any] = [self._session(), encoded, "lss"]
        if name:
            params.append(name)
        result = self.checked("import_survey", params)
        try:
            return int(result)
        except (TypeError, ValueError):
            # 不能让类型错误穿透到发布编排：那一层只认 RpcError，
            # 别的异常会在设置 survey_id 之前逃逸，连回滚都不会尝试。
            raise RpcError("import_survey", "result is not a survey id: {!r}".format(result)) from None

    def activate_survey(self, survey_id: int) -> Any:
        return self.checked("activate_survey", [self._session(), survey_id])

    def activate_tokens(self, survey_id: int) -> Any:
        return self.checked("activate_tokens", [self._session(), survey_id, []])

    def add_participants(
        self, survey_id: int, participants: Sequence[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        created = self.checked(
            "add_participants", [self._session(), survey_id, list(participants), True]
        )
        if not isinstance(created, list):
            raise RpcError("add_participants", created)
        rejected = [entry for entry in created if isinstance(entry, dict) and entry.get("errors")]
        if rejected:
            raise RpcError(
                "add_participants",
                "{} of {} participants were rejected: {}".format(
                    len(rejected), len(created), [entry.get("errors") for entry in rejected]
                ),
            )
        return created

    def delete_survey(self, survey_id: int) -> Any:
        return self.checked("delete_survey", [self._session(), survey_id])

    def get_fieldmap(self, survey_id: int) -> Dict[str, Dict[str, Any]]:
        result = self.checked("get_fieldmap", [self._session(), survey_id])
        if not isinstance(result, dict):
            raise RpcError("get_fieldmap", result)
        return result

    def list_questions(self, survey_id: int) -> List[Dict[str, Any]]:
        result = self.checked("list_questions", [self._session(), survey_id])
        if not isinstance(result, list):
            raise RpcError("list_questions", result)
        return result

    def get_survey_properties(self, survey_id: int) -> Dict[str, Any]:
        result = self.checked("get_survey_properties", [self._session(), survey_id])
        if not isinstance(result, dict):
            raise RpcError("get_survey_properties", result)
        return result

    def set_survey_properties(self, survey_id: int, properties: Dict[str, str]) -> Any:
        return self.checked("set_survey_properties", [self._session(), survey_id, properties])


def is_accepted(result: Any) -> bool:
    """引擎成功时返回标量、列表，或者逐字段布尔表；失败时返回带 status 的字典。"""
    if not isinstance(result, dict):
        return True
    if "error_code" in result:
        return False
    if "status" in result:
        return result["status"] in _OK_STATUSES
    return True
