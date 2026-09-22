"""``policy`` 块的校验与解析（契约 survey-access-policy-v1 §2）。

一次遍历同时做两件事：收集全部问题（校验）、构造不可变的 :class:`AccessPolicy`（解析）。
只要有一个问题，调用方就拿不到策略——半个策略比没有策略更危险。

未知键一律报错：一个拼错的 ``denyIps`` 被静默忽略，就等于一条限制悄悄失效。
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from ..model import SurveyDefinition
from ..validate import ValidationIssue
from .password import check_password_hash
from .timewindow import AMBIGUOUS, BAD_FORMAT, LocalTimeError, engine_utc, load_zone, parse_local

POLICY_VERSION = 1
IDENTITIES = ("token", "device", "ip")
MAX_RESPONSES = 10_000
MIN_DURATION_SECONDS = 60
MAX_DURATION_SECONDS = 7 * 24 * 3600
REGION_UNKNOWN_CHOICES = ("deny", "allow")

_TOP_KEYS = frozenset({"policyVersion", "window", "access", "limits", "network"})
_WINDOW_KEYS = frozenset({"opensAt", "closesAt", "timezone"})
_ACCESS_KEYS = frozenset({"password", "passwordHash", "captcha", "invitationRequired"})
_LIMIT_KEYS = frozenset({"responses", "maxDurationSeconds"})
_RESPONSE_KEYS = frozenset({"by", "max"})
_NETWORK_KEYS = frozenset({"allowIps", "denyIps", "allowRegions", "denyRegions", "regionUnknown"})
_REGION = re.compile(r"\A[A-Z]{2}(?:-[A-Z0-9]{1,3})?\Z")


@dataclass(frozen=True)
class Window:
    timezone: str
    opens_local: Optional[str]
    closes_local: Optional[str]
    opens_utc: Optional[str]
    closes_utc: Optional[str]


@dataclass(frozen=True)
class ResponseLimit:
    by: str
    max: int


@dataclass(frozen=True)
class Network:
    allow_ips: Tuple[str, ...]
    deny_ips: Tuple[str, ...]
    allow_regions: Tuple[str, ...]
    deny_regions: Tuple[str, ...]
    region_unknown: str


@dataclass(frozen=True)
class AccessPolicy:
    window: Optional[Window] = None
    password_hash: Optional[str] = None
    captcha: bool = False
    invitation_required: bool = False
    responses: Tuple[ResponseLimit, ...] = ()
    max_duration_seconds: Optional[int] = None
    network: Optional[Network] = None


class PolicyError(ValueError):
    """在校验不通过的定义上解析策略。"""


def check_policy(definition: SurveyDefinition) -> List[ValidationIssue]:
    return _Reader(definition).read()[1]


def parse_policy(definition: SurveyDefinition) -> Optional[AccessPolicy]:
    """定义没有策略返回 None；有问题抛 PolicyError（调用方应先校验）。"""
    policy, issues = _Reader(definition).read()
    if issues:
        raise PolicyError(", ".join(sorted({issue.code for issue in issues})))
    return policy


class _Reader:
    def __init__(self, definition: SurveyDefinition):
        self._definition = definition
        self._issues: List[ValidationIssue] = []
        self._identities: List[str] = []

    def read(self) -> Tuple[Optional[AccessPolicy], List[ValidationIssue]]:
        raw = self._definition.policy
        if raw is None:
            return None, []
        if not isinstance(raw, Mapping):
            self._issue("E_POLICY_INVALID", "policy", "policy 必须是对象")
            return None, self._issues
        self._keys(raw, _TOP_KEYS, "policy")
        version = raw.get("policyVersion")
        if isinstance(version, bool) or version != POLICY_VERSION:
            self._issue("E_POLICY_VERSION", "policy.policyVersion", "policyVersion 必须是 {}".format(POLICY_VERSION))
        if not (_TOP_KEYS - {"policyVersion"}) & set(raw):
            self._issue("E_POLICY_EMPTY", "policy", "策略里没有任何规则")
        access = self._block(raw, "access", "policy.access", _ACCESS_KEYS)
        limits = self._block(raw, "limits", "policy.limits", _LIMIT_KEYS)
        responses, duration = self._limits(limits)
        policy = AccessPolicy(
            window=self._window(self._block(raw, "window", "policy.window", _WINDOW_KEYS)),
            password_hash=self._password(access),
            captcha=self._flag(access, "captcha", "policy.access"),
            invitation_required=self._invitation(access),
            responses=responses,
            max_duration_seconds=duration,
            network=self._network(self._block(raw, "network", "policy.network", _NETWORK_KEYS)),
        )
        self._conflicts(policy)
        return (None if self._issues else policy), self._issues

    # ------------------------------------------------------------ 时间窗

    def _window(self, block: Optional[Mapping[str, Any]]) -> Optional[Window]:
        if block is None:
            return None
        where = "policy.window"
        zone_name = block.get("timezone")
        zone = load_zone(zone_name)
        if zone is None:
            self._issue("E_POLICY_TIMEZONE", where + ".timezone", "timezone 必须是 IANA 时区名，如 Asia/Shanghai")
        if block.get("opensAt") is None and block.get("closesAt") is None:
            self._issue("E_POLICY_WINDOW_EMPTY", where, "时间窗至少要有 opensAt 或 closesAt")
        opens = self._moment(block, "opensAt", zone)
        closes = self._moment(block, "closesAt", zone)
        if opens and closes and opens[0] >= closes[0]:
            self._issue("E_POLICY_WINDOW_ORDER", where, "opensAt 必须早于 closesAt")
        if zone is None:
            return None
        return Window(
            timezone=zone_name,
            opens_local=block.get("opensAt"),
            closes_local=block.get("closesAt"),
            opens_utc=opens[1] if opens else None,
            closes_utc=closes[1] if closes else None,
        )

    def _moment(self, block: Mapping[str, Any], key: str, zone) -> Optional[tuple]:
        text = block.get(key)
        if text is None:
            return None
        path = "policy.window." + key
        try:
            parse_local(text)
            return engine_utc(text, zone) if zone is not None else None
        except LocalTimeError as error:
            if error.reason == BAD_FORMAT:
                self._issue("E_POLICY_DATETIME", path, "{} 必须写成 YYYY-MM-DDTHH:MM[:SS]，不带时区偏移".format(key))
            else:
                kind = "有歧义（夏令时回拨，出现两次）" if error.reason == AMBIGUOUS else "不存在（夏令时拨快跳过）"
                self._issue("E_POLICY_LOCAL_TIME", path, "{} 在该时区{}".format(text, kind))
            return None

    # ------------------------------------------------------------ 访问

    def _password(self, access: Optional[Mapping[str, Any]]) -> Optional[str]:
        if access is None:
            return None
        if "password" in access:
            self._issue("E_POLICY_PLAINTEXT_PASSWORD", "policy.access.password",
                        "明文密码不能发往引擎；平台保存草稿时应换成 passwordHash")
        value = access.get("passwordHash")
        if value is None:
            return None
        problem = check_password_hash(value) if isinstance(value, str) else "必须是字符串"
        if problem is not None:
            self._issue("E_POLICY_PASSWORD_HASH", "policy.access.passwordHash", problem)
            return None
        return value

    def _invitation(self, access: Optional[Mapping[str, Any]]) -> bool:
        required = self._flag(access, "invitationRequired", "policy.access")
        if required and not self._definition.participants:
            self._issue("E_POLICY_INVITATION", "policy.access.invitationRequired",
                        "要求邀请码的问卷必须带参与者（participants）")
        return required

    # ------------------------------------------------------------ 限次与时长

    def _limits(self, block: Optional[Mapping[str, Any]]) -> Tuple[Tuple[ResponseLimit, ...], Optional[int]]:
        if block is None:
            return (), None
        if not block:
            self._issue("E_POLICY_EMPTY", "policy.limits", "limits 里没有任何规则")
        duration = self._integer(block, "maxDurationSeconds", "policy.limits",
                                 MIN_DURATION_SECONDS, MAX_DURATION_SECONDS)
        entries = block.get("responses")
        if entries is None:
            return (), duration
        if not isinstance(entries, list) or not entries:
            self._issue("E_POLICY_TYPE", "policy.limits.responses", "responses 必须是非空数组")
            return (), duration
        limits = [self._response(entry, index) for index, entry in enumerate(entries)]
        return tuple(limit for limit in limits if limit is not None), duration

    def _response(self, entry: Any, index: int) -> Optional[ResponseLimit]:
        where = "policy.limits.responses[{}]".format(index)
        if not isinstance(entry, Mapping):
            self._issue("E_POLICY_TYPE", where, "必须是对象 {by, max}")
            return None
        self._keys(entry, _RESPONSE_KEYS, where)
        by = entry.get("by")
        maximum = self._integer(entry, "max", where, 1, MAX_RESPONSES, required=True)
        if by not in IDENTITIES:
            self._issue("E_POLICY_IDENTITY", where + ".by", "by 必须是 {} 之一".format(", ".join(IDENTITIES)))
            return None
        if by in self._identities:
            self._issue("E_POLICY_IDENTITY", where + ".by", "身份维度 {} 重复出现".format(by))
            return None
        self._identities.append(by)
        if by == "token" and not self._definition.participants:
            self._issue("E_POLICY_IDENTITY", where + ".by", "按 token 限次的问卷必须带参与者（participants）")
        return ResponseLimit(by=by, max=maximum) if maximum is not None else None

    # ------------------------------------------------------------ 网络

    def _network(self, block: Optional[Mapping[str, Any]]) -> Optional[Network]:
        if block is None:
            return None
        where = "policy.network"
        unknown = block.get("regionUnknown", "deny")
        if unknown not in REGION_UNKNOWN_CHOICES:
            self._issue("E_POLICY_TYPE", where + ".regionUnknown", "regionUnknown 必须是 deny 或 allow")
        network = Network(
            allow_ips=self._cidrs(block, "allowIps"),
            deny_ips=self._cidrs(block, "denyIps"),
            allow_regions=self._regions(block, "allowRegions"),
            deny_regions=self._regions(block, "denyRegions"),
            region_unknown=unknown if unknown in REGION_UNKNOWN_CHOICES else "deny",
        )
        if not (network.allow_ips or network.deny_ips or network.allow_regions or network.deny_regions):
            self._issue("E_POLICY_EMPTY", where, "network 里没有任何规则")
        return network

    def _cidrs(self, block: Mapping[str, Any], key: str) -> Tuple[str, ...]:
        values = self._string_list(block, key)
        result = []
        for index, value in enumerate(values):
            try:
                result.append(str(ipaddress.ip_network(value.strip(), strict=False)))
            except ValueError:
                self._issue("E_POLICY_CIDR", "policy.network.{}[{}]".format(key, index),
                            "{!r} 不是合法的 IP 或 CIDR".format(value))
        return tuple(result)

    def _regions(self, block: Mapping[str, Any], key: str) -> Tuple[str, ...]:
        values = self._string_list(block, key)
        for index, value in enumerate(values):
            if not _REGION.match(value):
                self._issue("E_POLICY_REGION", "policy.network.{}[{}]".format(key, index),
                            "{!r} 不是 ISO 3166 地区码（如 CN、CN-BJ）".format(value))
        return tuple(value for value in values if _REGION.match(value))

    # ------------------------------------------------------------ 与原生设置冲突

    def _conflicts(self, policy: AccessPolicy) -> None:
        settings = self._definition.settings
        clashes = []
        if policy.captcha and "usecaptcha" in settings:
            clashes.append("usecaptcha")
        if policy.window is not None:
            clashes.extend(name for name in ("startdate", "expires") if name in settings)
        for name in clashes:
            self._issue("E_POLICY_SETTING_CONFLICT", "settings." + name,
                        "{} 由 policy 决定，不能再在 settings 里直写".format(name))

    # ------------------------------------------------------------ 零件

    def _block(self, raw: Mapping[str, Any], key: str, where: str,
               allowed: frozenset) -> Optional[Mapping[str, Any]]:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            self._issue("E_POLICY_TYPE", where, "{} 必须是对象".format(key))
            return None
        self._keys(value, allowed, where)
        return value

    def _keys(self, block: Mapping[str, Any], allowed: frozenset, where: str) -> None:
        for key in sorted(set(block) - allowed):
            self._issue("E_POLICY_UNKNOWN_KEY", "{}.{}".format(where, key), "不认识的键 {!r}".format(key))

    def _flag(self, block: Optional[Mapping[str, Any]], key: str, where: str) -> bool:
        value = None if block is None else block.get(key)
        if value is None:
            return False
        if not isinstance(value, bool):
            self._issue("E_POLICY_TYPE", "{}.{}".format(where, key), "{} 必须是布尔值".format(key))
            return False
        return value

    def _integer(self, block: Mapping[str, Any], key: str, where: str, low: int, high: int,
                 required: bool = False) -> Optional[int]:
        value = block.get(key)
        path = "{}.{}".format(where, key)
        if value is None:
            if required:
                self._issue("E_POLICY_TYPE", path, "{} 必填".format(key))
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self._issue("E_POLICY_TYPE", path, "{} 必须是整数".format(key))
            return None
        if not low <= value <= high:
            self._issue("E_POLICY_RANGE", path, "{} 必须在 {} 到 {} 之间".format(key, low, high))
            return None
        return value

    def _string_list(self, block: Mapping[str, Any], key: str) -> Sequence[str]:
        value = block.get(key)
        if value is None:
            return ()
        if not isinstance(value, list):
            self._issue("E_POLICY_TYPE", "policy.network." + key, "{} 必须是字符串数组".format(key))
            return ()
        strings = []
        for index, item in enumerate(value):
            if isinstance(item, str) and item.strip():
                strings.append(item)
            else:
                self._issue("E_POLICY_TYPE", "policy.network.{}[{}]".format(key, index), "必须是非空字符串")
        return strings

    def _issue(self, code: str, path: str, message: str) -> None:
        self._issues.append(ValidationIssue(code, path, message))
