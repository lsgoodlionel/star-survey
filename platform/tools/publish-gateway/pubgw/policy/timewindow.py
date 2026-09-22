"""作者写的本地时刻＋IANA 时区 → UTC（ADR 0016 决定 3）。

夏令时缺口里不存在的时刻、回拨重叠里出现两次的时刻都不替作者猜，直接报错。
"""

import re
from datetime import datetime, timezone
from typing import Optional, Tuple

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover — Python 3.8 以下没有 zoneinfo，网关要求 3.9 起
    ZoneInfo = None
    ZoneInfoNotFoundError = KeyError

ENGINE_DATETIME = "%Y-%m-%d %H:%M:%S"
_LOCAL = re.compile(r"\A(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?\Z")

#: 换算失败的原因，由 schema 映射成问题代码。
BAD_FORMAT = "format"
NONEXISTENT = "nonexistent"
AMBIGUOUS = "ambiguous"


class LocalTimeError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def load_zone(name: str) -> Optional["ZoneInfo"]:
    """认识的 IANA 时区返回 ZoneInfo，否则 None。"""
    if ZoneInfo is None or not isinstance(name, str) or not name or name.startswith("/") or ".." in name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def parse_local(text: str) -> datetime:
    """``YYYY-MM-DDTHH:MM[:SS]``，不带偏移。"""
    match = _LOCAL.match(text) if isinstance(text, str) else None
    if match is None:
        raise LocalTimeError(BAD_FORMAT)
    try:
        return datetime(*(int(part or 0) for part in match.groups()))
    except ValueError:
        raise LocalTimeError(BAD_FORMAT) from None


def to_utc(naive: datetime, zone: "ZoneInfo") -> datetime:
    """把某时区的墙上时刻换成 UTC；不存在或有歧义时抛 LocalTimeError。"""
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    if not (_round_trips(first, naive) and _round_trips(second, naive)):
        raise LocalTimeError(NONEXISTENT)
    if first.utcoffset() != second.utcoffset():
        raise LocalTimeError(AMBIGUOUS)
    return first.astimezone(timezone.utc)


def engine_utc(text: str, zone: "ZoneInfo") -> Tuple[datetime, str]:
    """本地时刻文本 → (UTC 时刻, 引擎格式的 UTC 文本)。"""
    moment = to_utc(parse_local(text), zone)
    return moment, moment.strftime(ENGINE_DATETIME)


def _round_trips(aware: datetime, naive: datetime) -> bool:
    back = aware.astimezone(timezone.utc).astimezone(aware.tzinfo)
    return back.replace(tzinfo=None) == naive
