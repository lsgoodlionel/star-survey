"""收口被取代的已发布版本：让旧的引擎问卷不再接收新的答卷（ADR 0012 决定 3）。

做法是**设过期时间**，不停用、不删除：

- RemoteControl 没有停用接口；即使有，引擎停用会把答卷表改名归档（``lime_old_survey_*``），
  旧版本的答卷与平台投影就对不上了；
- ``delete_survey`` 会连答卷一起删，绝不能用在接收过答卷的问卷上（ADR 0009 已知限制 2）；
- 过期只是一列设置：答卷表、参与者表、结构全部原样保留，出错时改回去也只是一列。

引擎作答入口按 ``gmdate() > expires`` 判定过期（SurveyIndex.php），管理端则按时区偏移后的时间显示。
网关与引擎的时钟也未必一致。所以过期时间写成网关的 UTC 当下**回拨一天**：无论时区偏移还是
几分钟的时钟差，都保证写入即生效。平台自己记录精确的收口时刻，引擎里这一列只是闸门。

已经过期的问卷不再改写（保留更早的那个时刻），因此重复收口是无害的。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from .rpc import RemoteControlClient, RpcError

#: 过期时间相对网关 UTC 当下的回拨量，吸收时区偏移与主机间的时钟差。
CLOSE_BACKDATE = timedelta(days=1)

_ENGINE_DATETIME = "%Y-%m-%d %H:%M:%S"
_DATETIME_PREFIX = re.compile(r"\A(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?")
_INVALID_SURVEY = "ERR_INVALID_SURVEY"


class CloseError(Exception):
    """收口没有生效。code 是机器可读的原因，detail 只进日志与 502 应答（已脱敏）。"""

    def __init__(self, code: str, detail: str):
        super().__init__("{}: {}".format(code, detail))
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CloseResult:
    survey_id: int
    #: 引擎里此刻的过期时间（引擎原样格式）。
    expires: str
    #: 调用前就已经过期：本次没有改写。
    already_closed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"surveyId": self.survey_id, "expires": self.expires, "alreadyClosed": self.already_closed}


def close_survey(client: RemoteControlClient, survey_id: int, now: datetime) -> CloseResult:
    """让 ``survey_id`` 不再接收新答卷。失败抛 :class:`CloseError` 或 :class:`RpcError`。"""
    current = _expires_of(_properties(client, survey_id))
    if current is not None and _is_past(current, now):
        return CloseResult(survey_id=survey_id, expires=current, already_closed=True)

    target = (now.astimezone(timezone.utc) - CLOSE_BACKDATE).strftime(_ENGINE_DATETIME)
    saved = client.set_survey_properties(survey_id, {"expires": target})
    if not isinstance(saved, dict) or saved.get("expires") is not True:
        raise CloseError("E_CLOSE_NOT_APPLIED", "engine did not store expires={}: {!r}".format(target, saved))

    stored = _expires_of(_properties(client, survey_id))
    if stored is None or not _is_past(stored, now):
        raise CloseError("E_CLOSE_NOT_APPLIED", "engine reads back expires={!r} after writing {}".format(stored, target))
    return CloseResult(survey_id=survey_id, expires=stored, already_closed=False)


def is_invalid_survey(error: RpcError) -> bool:
    """引擎说这个 sid 不存在（被删或从未存在）。"""
    return isinstance(error.detail, dict) and error.detail.get("error_code") == _INVALID_SURVEY


def _properties(client: RemoteControlClient, survey_id: int) -> Dict[str, Any]:
    try:
        return client.get_survey_properties(survey_id)
    except RpcError as error:
        if is_invalid_survey(error):
            raise CloseError("E_SURVEY_MISSING", "engine survey {} does not exist".format(survey_id)) from None
        raise


def _expires_of(properties: Dict[str, Any]) -> Optional[str]:
    value = properties.get("expires")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_past(engine_value: str, now: datetime) -> bool:
    """引擎的过期时间按 UTC 解读（与作答入口的 gmdate 比较一致）；认不出的格式按"未过期"处理。"""
    parsed = parse_engine_datetime(engine_value)
    return parsed is not None and parsed <= now.astimezone(timezone.utc)


def parse_engine_datetime(value: str) -> Optional[datetime]:
    match = _DATETIME_PREFIX.match(value)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(part or 0) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None
