"""HMAC 认证：缺头、签名不符、时间戳偏差超限一律拒绝。"""

import hashlib
import hmac
import unittest

from pubgw.auth import MAX_SKEW_SECONDS, AuthError, check_secret, sign, verify

SECRET = b"0123456789abcdef0123456789abcdef-test"
NOW = 1_800_000_000
BODY = b'{"requestId":"x"}'


class SignTest(unittest.TestCase):
    def test_signs_timestamp_dot_raw_body_with_hmac_sha256(self):
        expected = hmac.new(SECRET, b"1800000000." + BODY, hashlib.sha256).hexdigest()

        self.assertEqual(expected, sign(SECRET, "1800000000", BODY))


class VerifyTest(unittest.TestCase):
    def reason(self, timestamp, signature, body=BODY, now=NOW):
        with self.assertRaises(AuthError) as caught:
            verify(SECRET, timestamp, signature, body, now)
        return caught.exception.reason

    def test_accepts_a_fresh_valid_signature(self):
        verify(SECRET, str(NOW), sign(SECRET, str(NOW), BODY), BODY, NOW)

    def test_accepts_an_uppercase_hex_signature(self):
        verify(SECRET, str(NOW), sign(SECRET, str(NOW), BODY).upper(), BODY, NOW)

    def test_accepts_the_edge_of_the_window(self):
        stamp = str(NOW - MAX_SKEW_SECONDS)

        verify(SECRET, stamp, sign(SECRET, stamp, BODY), BODY, NOW)

    def test_rejects_a_missing_timestamp(self):
        self.assertEqual("missing_timestamp", self.reason(None, sign(SECRET, str(NOW), BODY)))

    def test_rejects_a_missing_signature(self):
        self.assertEqual("missing_signature", self.reason(str(NOW), None))

    def test_rejects_a_timestamp_that_is_not_an_integer(self):
        for stamp in ("abc", "1.5", "-1", " 1800000000", "1800000000\n"):
            with self.subTest(stamp=stamp):
                self.assertEqual("invalid_timestamp", self.reason(stamp, sign(SECRET, stamp, BODY)))

    def test_rejects_a_stale_timestamp(self):
        stamp = str(NOW - MAX_SKEW_SECONDS - 1)

        self.assertEqual("stale_timestamp", self.reason(stamp, sign(SECRET, stamp, BODY)))

    def test_rejects_a_timestamp_from_the_future(self):
        stamp = str(NOW + MAX_SKEW_SECONDS + 1)

        self.assertEqual("stale_timestamp", self.reason(stamp, sign(SECRET, stamp, BODY)))

    def test_rejects_a_signature_over_a_different_body(self):
        signature = sign(SECRET, str(NOW), b'{"requestId":"y"}')

        self.assertEqual("bad_signature", self.reason(str(NOW), signature))

    def test_rejects_a_signature_made_with_another_secret(self):
        signature = sign(b"x" * 32, str(NOW), BODY)

        self.assertEqual("bad_signature", self.reason(str(NOW), signature))

    def test_rejects_a_signature_that_is_not_hex(self):
        self.assertEqual("bad_signature", self.reason(str(NOW), "zz" * 32))


class SecretTest(unittest.TestCase):
    def test_a_short_secret_is_refused(self):
        with self.assertRaises(ValueError):
            check_secret("x" * 31)

    def test_an_empty_secret_is_refused(self):
        with self.assertRaises(ValueError):
            check_secret("")

    def test_a_32_byte_secret_is_accepted_as_bytes(self):
        self.assertEqual(b"x" * 32, check_secret("x" * 32))

    def test_length_is_counted_in_utf8_bytes(self):
        self.assertEqual(33, len(check_secret("密" * 11)))


if __name__ == "__main__":
    unittest.main()
