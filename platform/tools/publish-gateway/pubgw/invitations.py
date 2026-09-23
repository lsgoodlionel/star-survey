"""把引擎生成的邀请码配回平台的参与者（ADR 0016）。

网关的 ``add_participants`` 固定让引擎生成 token，定义里写的会被替换，所以平台要发的
邀请码只能在发布后回读。对应关系按位置：引擎
``foreach ($aParticipantData as &$aParticipant)`` 逐条原地替换
（``remotecontrol_handle.php``），返回列表与提交列表同序同长。成功的条目被整条换成
token 行属性，平台传的非列字段已被 ``array_intersect_key`` 丢掉，姓名邮箱在开了字段
加密的问卷上还是密文——按这些字段对不回来。

拿不到码却报发布成功是最糟的结果：平台会登记路由、发出一批打不开的邀请，且无从察觉。
所以对不上就抛错，由发布编排回滚。
"""

from typing import Any, Dict, List, Mapping, Optional, Sequence


class InvitationError(Exception):
    """引擎返回的参与者行无法当作邀请码用。"""


def collect(
    rows: Sequence[Any], refs: Sequence[Optional[str]]
) -> List[Dict[str, Optional[str]]]:
    """按位置把 ``rows`` 里的 token 配到 ``refs`` 上，配不齐就抛 InvitationError。"""
    if len(rows) != len(refs):
        raise InvitationError(
            "engine returned {} participant rows for {} participants".format(len(rows), len(refs))
        )

    invitations: List[Dict[str, Optional[str]]] = []
    seen: Dict[str, int] = {}
    for index, (row, ref) in enumerate(zip(rows, refs)):
        token = _token(row, index)
        if token in seen:
            # 同一个码发给两个人：谁先交卷谁占掉 usesleft，另一个人被挡在门外。
            raise InvitationError(
                "participants {} and {} came back with the same token".format(seen[token], index)
            )
        seen[token] = index
        invitations.append({"index": index, "ref": ref, "token": token, "tid": _tid(row)})
    return invitations


def _token(row: Any, index: int) -> str:
    if not isinstance(row, Mapping):
        raise InvitationError("participant {} is not an object: {!r}".format(index, row))
    token = row.get("token")
    if not isinstance(token, str) or not token.strip():
        # 引擎的 generateToken 失败时会留下空串而不是报错。
        raise InvitationError("participant {} came back without a token".format(index))
    return token


def _tid(row: Mapping[str, Any]) -> Optional[str]:
    tid = row.get("tid")
    return None if tid is None else str(tid)
