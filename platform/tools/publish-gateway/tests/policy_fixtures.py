"""访问策略测试的共用零件（WP-04，契约 survey-access-policy-v1）。"""

import base64
import copy
import hashlib

from pubgw.model import SurveyDefinition

from .fixtures import sample_payload

#: 测试专用的低成本哈希：迭代次数取网关允许的下限，免得单元测试变慢。
TEST_ITERATIONS = 100_000
TEST_SALT = b"0123456789abcdef"


def password_hash(password, salt=TEST_SALT, iterations=TEST_ITERATIONS):
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, 32)
    return "pbkdf2-sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")
    )


def full_policy():
    """每一类规则都用上的策略。"""
    return {
        "policyVersion": 1,
        "window": {"opensAt": "2026-10-01T09:00", "closesAt": "2026-10-07T18:30:00", "timezone": "Asia/Shanghai"},
        "access": {"passwordHash": password_hash("s3cret"), "captcha": True, "invitationRequired": True},
        "limits": {"responses": [{"by": "ip", "max": 5}, {"by": "token", "max": 1}], "maxDurationSeconds": 1800},
        "network": {
            "allowIps": ["10.0.0.0/8", "2001:db8::/32"],
            "denyIps": ["10.9.9.9"],
            "allowRegions": ["CN"],
            "regionUnknown": "deny",
        },
    }


def policy_payload(policy, participants=None, **settings_overrides):
    payload = copy.deepcopy(sample_payload())
    payload["policy"] = policy
    if participants is not None:
        payload["participants"] = participants
    payload["settings"].update(settings_overrides)
    return payload


def policy_definition(policy, participants=None, **settings_overrides):
    return SurveyDefinition.from_dict(policy_payload(policy, participants, **settings_overrides))


def with_participants():
    return [{"token": "tok-a", "firstname": "A"}, {"token": "tok-b", "firstname": "B"}]
