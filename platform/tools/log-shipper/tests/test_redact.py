"""脱敏规则测试：上传前必须清除凭证与个人信息，且保留可定位问题的上下文。"""

import unittest

from logship.redact import Redactor, RedactionRules


class RedactorTest(unittest.TestCase):
    def setUp(self):
        self.redactor = Redactor(RedactionRules.default())

    def test_removes_common_credentials(self):
        text = (
            'password="hunter2" api_key=sk-live-abcdefghijklmnop\n'
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature\n"
            "Cookie: PHPSESSID=8f14e45fceea167a5a36dedd4bea2543; other=1\n"
        )

        result = self.redactor.redact(text)

        for secret in ["hunter2", "sk-live-abcdefghijklmnop", "eyJhbGciOiJIUzI1NiJ9", "8f14e45fceea167a5a36dedd4bea2543"]:
            self.assertNotIn(secret, result.text)
        self.assertIn("password=", result.text)
        self.assertGreaterEqual(result.counts["credential"], 4)

    def test_masks_personal_data_but_keeps_shape(self):
        text = "user zhang@example.com phone 13800138000 id 11010119900307123X"

        result = self.redactor.redact(text)

        self.assertNotIn("zhang@example.com", result.text)
        self.assertNotIn("13800138000", result.text)
        self.assertNotIn("11010119900307123X", result.text)
        self.assertIn("[EMAIL]", result.text)
        self.assertIn("[PHONE]", result.text)
        self.assertIn("[ID_NUMBER]", result.text)

    def test_keeps_the_information_developers_need(self):
        text = (
            "2026-09-20 08:15:03 [error] CDbException: SQLSTATE[42S02] table lime_foo missing\n"
            "  at /var/www/html/application/models/Response.php:242\n"
            "traceId=8b0c1f2e requestId=42\n"
        )

        result = self.redactor.redact(text)

        self.assertIn("CDbException", result.text)
        self.assertIn("application/models/Response.php:242", result.text)
        self.assertIn("traceId=8b0c1f2e", result.text)
        self.assertEqual(result.counts.get("credential", 0), 0)

    def test_extra_patterns_from_configuration_are_applied(self):
        rules = RedactionRules.default().with_extra_patterns([("internal-host", r"srv-\d{4}\.intra")])

        result = Redactor(rules).redact("connecting to srv-0421.intra now")

        self.assertNotIn("srv-0421.intra", result.text)
        self.assertEqual(result.counts["internal-host"], 1)

    def test_ip_addresses_are_masked_when_enabled(self):
        rules = RedactionRules.default().with_ip_masking(True)

        result = Redactor(rules).redact("client 203.0.113.42 connected")

        self.assertNotIn("203.0.113.42", result.text)
        self.assertIn("[IP]", result.text)

    def test_binary_safe_and_idempotent(self):
        text = "token=abc123secret456token\n"

        once = self.redactor.redact(text).text
        twice = self.redactor.redact(once).text

        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
