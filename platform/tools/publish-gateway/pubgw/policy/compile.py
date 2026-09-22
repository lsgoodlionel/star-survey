"""策略 → 引擎（ADR 0016 决定 2、4）。

两份产物：

- ``native_settings``：引擎自己权威执行的设置（验证码 ``usecaptcha``、时间窗 ``startdate/expires``），
  写进 LSS 的问卷行；
- ``payload``：插件执行的规则，规范 JSON（键排序、紧凑、纯 ASCII），作为一行
  ``plugin_settings`` 写进 LSS，发布时按 ``digest``（SHA-256）回读核对。

只有验证码／邀请码的策略不需要插件，``payload`` 与 ``digest`` 为 None。
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..model import SurveyDefinition
from .schema import IDENTITIES, AccessPolicy, parse_policy

PLUGIN_NAME = "MjyRuntimePolicy"
POLICY_KEY = "mjy_access_policy"
PAYLOAD_SCHEMA = "mjy-access-policy/1"

#: 引擎"只在进入问卷时"要验证码（common_helper.php isCaptchaEnabled 'surveyaccessscreen'）。
CAPTCHA_ON_ACCESS = "X"
#: 引擎 7.x 的访问模式（Survey.php access_mode）：C＝必须凭参与者 token 进入。
CLOSED_ACCESS = "C"


@dataclass(frozen=True)
class CompiledPolicy:
    native_settings: Dict[str, str]
    payload: Optional[str]
    digest: Optional[str]


def compile_policy(definition: SurveyDefinition) -> Optional[CompiledPolicy]:
    """定义没有策略返回 None。调用方保证定义已通过校验。"""
    policy = parse_policy(definition)
    if policy is None:
        return None
    payload = _payload(policy)
    text = None if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return CompiledPolicy(
        native_settings=_native(policy),
        payload=text,
        digest=None if text is None else hashlib.sha256(text.encode("ascii")).hexdigest(),
    )


def _native(policy: AccessPolicy) -> Dict[str, str]:
    settings: Dict[str, str] = {}
    if policy.captcha:
        settings["usecaptcha"] = CAPTCHA_ON_ACCESS
    if policy.invitation_required:
        settings["access_mode"] = CLOSED_ACCESS
    if policy.window is not None:
        if policy.window.opens_utc:
            settings["startdate"] = policy.window.opens_utc
        if policy.window.closes_utc:
            settings["expires"] = policy.window.closes_utc
    return settings


def _needs_plugin(policy: AccessPolicy) -> bool:
    return any((
        policy.window is not None,
        policy.password_hash is not None,
        bool(policy.responses),
        policy.max_duration_seconds is not None,
        policy.network is not None,
    ))


def _payload(policy: AccessPolicy) -> Optional[Dict[str, Any]]:
    if not _needs_plugin(policy):
        return None
    return {
        "schema": PAYLOAD_SCHEMA,
        "window": None if policy.window is None else {
            "opensAt": policy.window.opens_utc,
            "closesAt": policy.window.closes_utc,
            "timezone": policy.window.timezone,
            "opensAtLocal": policy.window.opens_local,
            "closesAtLocal": policy.window.closes_local,
        },
        "passwordHash": policy.password_hash,
        # 按可靠程度排序（token → device → ip）：插件依次预留，时长挂在最可靠的维度上。
        "responses": [
            {"by": limit.by, "max": limit.max}
            for limit in sorted(policy.responses, key=lambda limit: IDENTITIES.index(limit.by))
        ],
        "maxDurationSeconds": policy.max_duration_seconds,
        "network": None if policy.network is None else {
            "allowIps": list(policy.network.allow_ips),
            "denyIps": list(policy.network.deny_ips),
            "allowRegions": list(policy.network.allow_regions),
            "denyRegions": list(policy.network.deny_regions),
            "regionUnknown": policy.network.region_unknown,
        },
    }
