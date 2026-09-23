"""跨语言向量：网关编译出来的 policyDigest 必须和平台重算的一致（ADR 0016）。

平台在发布收尾时自己算一遍摘要再比对，于是同一份规范化规则有了两套实现
（``pubgw/policy/compile.py`` 与 Java 的 ``AccessPolicyDigest``）。两边读同一张向量表：
``platform/services/business/src/test/resources/policy/digest-vectors.json``。
改了任何一端而没同步另一端，这里或 Java 的 ``AccessPolicyDigestTest`` 就会红。

表里刻意包含容易分歧的写法：夏令时两侧的本地时刻、乱序的限次、IPv6 的压缩与前导零、
CIDR 的主机位、以及"引擎自己执行、不需要插件"因而没有摘要的策略。
"""

import json
import unittest
from pathlib import Path

from pubgw.model import SurveyDefinition
from pubgw.policy.compile import compile_policy

from .policy_fixtures import policy_payload

REPO_ROOT = Path(__file__).resolve().parents[4]
VECTORS = REPO_ROOT / "platform/services/business/src/test/resources/policy/digest-vectors.json"


def definition_for(policy):
    payload = policy_payload(policy)
    payload["settings"] = {}
    if policy.get("access", {}).get("invitationRequired"):
        payload["participants"] = [{"token": "tok-1"}]
    return SurveyDefinition.from_dict(payload)


class PolicyDigestVectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vectors = json.loads(VECTORS.read_text(encoding="utf-8"))

    def test_the_table_covers_the_cases_that_are_easy_to_get_wrong(self):
        self.assertGreaterEqual(len(self.vectors), 9)
        for name in ("captchaOnly", "windowAcrossDst", "limitsOutOfOrder", "networkIpv6", "everything"):
            self.assertIn(name, self.vectors)

    def test_every_vector_still_compiles_to_the_recorded_payload_and_digest(self):
        for name, vector in sorted(self.vectors.items()):
            with self.subTest(name=name):
                compiled = compile_policy(definition_for(vector["policy"]))
                self.assertEqual(vector["payload"], compiled.payload)
                self.assertEqual(vector["digest"], compiled.digest)


if __name__ == "__main__":
    unittest.main()
