"""发布时回读插件里的策略（ADR 0016 决定 4）。

LSS 导入遇到没注册的插件只写一条导入警告，而 RemoteControl 的 ``import_survey`` 不返回警告：
策略会静默丢失。所以激活之前必须问插件本人——它存的是不是正好一份、能不能解析、
摘要是不是网关编译出的那个。

状态端点：``GET <engine>/index.php/plugins/direct?plugin=MjyRuntimePolicy&function=policyStatus&sid=<sid>``，
应答 ``{"plugin", "active", "surveyId", "rows", "valid", "policyDigest"}``，不含策略内容。
"""

import json
from typing import Any, Callable, List, Mapping, Optional

from ..rpc import RpcError
from .compile import PLUGIN_NAME

PolicyProbe = Callable[[int], Optional[Mapping[str, Any]]]
Fetch = Callable[[str], bytes]

_RPC_SUFFIX = "/admin/remotecontrol"
_STATUS_QUERY = "/plugins/direct?plugin={}&function=policyStatus&sid={{}}".format(PLUGIN_NAME)
_TIMEOUT_SECONDS = 30


class HttpPolicyProbe:
    """GET 插件状态端点。传输失败抛 RpcError（发布编排据此回滚）；应答不是 JSON 返回 None。"""

    def __init__(self, index_url: str, fetch: Optional[Fetch] = None):
        self._template = index_url.rstrip("/") + _STATUS_QUERY
        self._fetch = fetch or _urlopen

    @classmethod
    def from_rpc_url(cls, rpc_url: str, fetch: Optional[Fetch] = None) -> Optional["HttpPolicyProbe"]:
        """``…/index.php/admin/remotecontrol`` → ``…/index.php``；认不出的形状返回 None。"""
        trimmed = rpc_url.rstrip("/")
        if not trimmed.endswith(_RPC_SUFFIX):
            return None
        return cls(trimmed[: -len(_RPC_SUFFIX)], fetch)

    @classmethod
    def from_engine_url(cls, engine_url: str, fetch: Optional[Fetch] = None) -> "HttpPolicyProbe":
        """命令行用的引擎根地址。"""
        return cls(engine_url.rstrip("/") + "/index.php", fetch)

    def __call__(self, survey_id: int) -> Optional[Mapping[str, Any]]:
        body = self._fetch(self._template.format(int(survey_id)))
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None


def enforcement_failures(status: Optional[Mapping[str, Any]], survey_id: int, digest: str) -> List[str]:
    """插件回读结果 → 失败清单（空＝策略确已生效）。"""
    if not isinstance(status, Mapping) or status.get("plugin") != PLUGIN_NAME or status.get("active") is not True:
        return ["E_POLICY_NOT_ENFORCED sid={}: 插件 {} 未在引擎上激活或没有应答".format(survey_id, PLUGIN_NAME)]
    if status.get("surveyId") != survey_id or status.get("rows") != 1:
        return ["E_POLICY_NOT_ENFORCED sid={}: 插件里的策略份数是 {!r}，应为 1".format(survey_id, status.get("rows"))]
    if status.get("valid") is not True:
        return ["E_POLICY_REJECTED sid={}: 插件无法解析这份策略".format(survey_id)]
    if status.get("policyDigest") != digest:
        return ["E_POLICY_DIGEST_MISMATCH sid={}: 插件里的策略与编译结果不一致".format(survey_id)]
    return []


def _urlopen(url: str) -> bytes:
    from http.client import HTTPException
    from urllib.request import Request, urlopen

    try:
        with urlopen(Request(url, method="GET"), timeout=_TIMEOUT_SECONDS) as response:
            return response.read()
    except (OSError, HTTPException) as error:
        raise RpcError("policyStatus", "{} ({})".format(error, url)) from None
