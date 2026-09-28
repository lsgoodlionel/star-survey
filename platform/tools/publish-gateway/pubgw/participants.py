"""撤销一个邀请码：把引擎里的那一行参与者删掉（ADR 0016 缺口「邀请码撤销」、契约 v1.4）。

为什么是"删"而不是"停用"：引擎的作答入口按 ``tokens_<sid>`` 里有没有这一行来放人
（非匿名问卷凭 token 进场），行没了就立刻进不去，**不需要等缓存或会话过期**。
参与者表里别的字段（``usesleft``、``validuntil``）也能拦人，但它们的语义是
"用完了/过期了"，撤销要的是"这个码作废"——而且 ``validuntil`` 与问卷时区、
引擎时钟纠缠在一起，撤销不该依赖时钟。

引擎的 ``delete_participants`` 只认参与者行号 ``tid``，所以是两步：
先 ``get_participant_properties`` 按 token 查出 tid，再删。

三种结局，平台据此决定要不要写 ``revoked_at``：

- ``revoked``：确实删掉了，或者**引擎里本来就没有这个码**（幂等重试、或人已在引擎侧删过）；
- ``absent``：作为 ``revoked`` 的一种，只在回执里标出来，便于运维看出差异；
- 失败（抛 :class:`RevokeError` / :class:`~pubgw.rpc.RpcError`）：问卷不存在、
  没有参与者表、删除没生效、引擎读不出来。**平台此时绝不能写 revoked_at**——
  那正是这个缺口的本体："平台以为撤销了，令牌照样能开问卷"。

令牌是能直接进入问卷的凭据：本模块**不把它写进任何日志**，日志里只有 sid 与 tid。
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .rpc import RemoteControlClient, RpcError

log = logging.getLogger("pubgw.participants")

_INVALID_SURVEY = "ERR_INVALID_SURVEY"
_NO_PARTICIPANT_TABLE = "ERR_NO_PARTICIPANT_TABLE"
#: 引擎在"按属性查不到"与"tid 不存在"两种情况下给的码，对撤销都等于"本来就没有"。
_ABSENT_CODES = frozenset({"ERR_NOT_FOUND", "ERR_INVALID_TOKEN"})
_DELETED = "Deleted"


class RevokeError(Exception):
    """撤销没有生效。code 机器可读，detail 只进日志与 502 应答（已脱敏）。"""

    def __init__(self, code: str, detail: str):
        super().__init__("{}: {}".format(code, detail))
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RevokeResult:
    survey_id: int
    #: 被删掉的参与者行号；本来就不存在时为 None。
    token_id: Optional[int]
    #: 调用前引擎里就没有这个码：本次什么都没删，但结论同样是"这个码进不去"。
    already_absent: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "surveyId": self.survey_id,
            "tokenId": self.token_id,
            "alreadyAbsent": self.already_absent,
        }


def revoke_participant(client: RemoteControlClient, survey_id: int, token: str) -> RevokeResult:
    """让 ``token`` 再也打不开 ``survey_id``。失败抛 :class:`RevokeError` 或 ``RpcError``。"""
    token_id = _token_id(client, survey_id, token)
    if token_id is None:
        log.info("revoke sid=%s: the engine has no such participant (already absent)", survey_id)
        return RevokeResult(survey_id=survey_id, token_id=None, already_absent=True)

    outcome = _delete(client, survey_id, token_id)
    verdict = outcome.get(str(token_id), outcome.get(token_id))
    if verdict != _DELETED:
        raise RevokeError("E_REVOKE_NOT_APPLIED",
                          "engine reports {!r} for tid {} on sid {}".format(verdict, token_id, survey_id))

    remaining = _token_id(client, survey_id, token)
    if remaining is not None:
        raise RevokeError("E_REVOKE_NOT_APPLIED",
                          "tid {} on sid {} is still present after deletion".format(remaining, survey_id))
    log.info("revoked sid=%s tid=%s", survey_id, token_id)
    return RevokeResult(survey_id=survey_id, token_id=token_id, already_absent=False)


def _token_id(client: RemoteControlClient, survey_id: int, token: str) -> Optional[int]:
    """按 token 查参与者行号；引擎里没有这个码时返回 None。"""
    try:
        # 只要 tid：把 token 也读回来毫无用处，却让这份凭据多在内存里走一趟。
        properties = client.get_participant_properties(survey_id, {"token": token}, ["tid"])
    except RpcError as error:
        code = _error_code(error)
        if code in _ABSENT_CODES:
            return None
        raise _translated(error, code, survey_id) from None
    value = properties.get("tid")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise RevokeError("E_REVOKE_NOT_APPLIED",
                          "engine returned no usable tid for sid {}".format(survey_id)) from None


def _delete(client: RemoteControlClient, survey_id: int, token_id: int) -> Dict[str, Any]:
    try:
        return client.delete_participants(survey_id, [token_id])
    except RpcError as error:
        raise _translated(error, _error_code(error), survey_id) from None


def _error_code(error: RpcError) -> Optional[str]:
    return error.detail.get("error_code") if isinstance(error.detail, dict) else None


def _translated(error: RpcError, code: Optional[str], survey_id: int) -> Exception:
    """把引擎的两个"结构性缺失"翻成机器可读的失败码；其余原样上抛。

    问卷不存在、没有参与者表都**算失败**而不是"已撤销"：它们同样可能是实例 id
    配错了——那时真正的令牌还在另一台引擎上活着。把它报成成功，平台就会写下
    ``revoked_at``，于是又回到"平台以为撤销了"的原状。
    """
    if code == _INVALID_SURVEY:
        return RevokeError("E_SURVEY_MISSING", "engine survey {} does not exist".format(survey_id))
    if code == _NO_PARTICIPANT_TABLE:
        return RevokeError("E_NO_PARTICIPANT_TABLE",
                           "engine survey {} has no participant table".format(survey_id))
    return error
