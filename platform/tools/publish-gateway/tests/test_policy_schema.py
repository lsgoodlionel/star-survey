"""访问策略的发布前校验：任何一处不对都是 422 validate，绝不静默丢掉一条限制。"""

import copy
import unittest

from pubgw.model import SurveyDefinition
from pubgw.validate import validate_definition

from .fixtures import sample_payload
from .policy_fixtures import full_policy, password_hash, policy_definition, with_participants


def issue_codes(definition):
    return [issue.code for issue in validate_definition(definition).issues]


def issues_for(policy, participants=None, **settings):
    return issue_codes(policy_definition(policy, participants, **settings))


def minimal(**blocks):
    policy = {"policyVersion": 1}
    policy.update(blocks)
    return policy


class AcceptedPolicyTest(unittest.TestCase):
    def test_full_policy_with_participants_is_valid(self):
        self.assertEqual([], issues_for(full_policy(), with_participants()))

    def test_definition_without_policy_is_unaffected(self):
        self.assertIsNone(SurveyDefinition.from_dict(sample_payload()).policy)

    def test_policy_is_allowed_in_logic_definitions_too(self):
        payload = copy.deepcopy(sample_payload())
        payload["definitionVersion"] = 2
        payload["policy"] = minimal(access={"captcha": True})
        self.assertEqual([], issue_codes(SurveyDefinition.from_dict(payload)))

    def test_open_ended_window_needs_only_one_bound(self):
        self.assertEqual([], issues_for(minimal(window={"closesAt": "2026-10-01T09:00", "timezone": "UTC"})))


class StructureTest(unittest.TestCase):
    def test_policy_must_be_an_object(self):
        self.assertEqual(["E_POLICY_INVALID"], issues_for(["not", "an", "object"]))

    def test_policy_version_is_required(self):
        self.assertIn("E_POLICY_VERSION", issues_for({"access": {"captcha": True}}))

    def test_unknown_policy_version_is_rejected(self):
        self.assertIn("E_POLICY_VERSION", issues_for(minimal(policyVersion=2, access={"captcha": True})))

    def test_boolean_is_not_a_version(self):
        self.assertIn("E_POLICY_VERSION", issues_for({"policyVersion": True, "access": {"captcha": True}}))

    def test_unknown_key_anywhere_is_rejected(self):
        self.assertIn("E_POLICY_UNKNOWN_KEY", issues_for(minimal(access={"captcha": True, "sms": True})))
        self.assertIn("E_POLICY_UNKNOWN_KEY", issues_for(minimal(quota={"slots": 3})))

    def test_policy_without_any_rule_is_rejected(self):
        self.assertIn("E_POLICY_EMPTY", issues_for(minimal()))

    def test_all_problems_are_reported_at_once(self):
        codes = issues_for(minimal(
            window={"opensAt": "tomorrow", "timezone": "Mars/Olympus"},
            access={"password": "plain"},
            limits={"responses": [{"by": "phone", "max": 0}]},
        ))
        for expected in ("E_POLICY_DATETIME", "E_POLICY_TIMEZONE", "E_POLICY_PLAINTEXT_PASSWORD",
                         "E_POLICY_IDENTITY", "E_POLICY_RANGE"):
            self.assertIn(expected, codes)


class WindowTest(unittest.TestCase):
    def test_timezone_is_required(self):
        self.assertIn("E_POLICY_TIMEZONE", issues_for(minimal(window={"opensAt": "2026-10-01T09:00"})))

    def test_unknown_timezone_is_rejected(self):
        self.assertIn("E_POLICY_TIMEZONE", issues_for(
            minimal(window={"opensAt": "2026-10-01T09:00", "timezone": "Asia/Beijing"})))

    def test_needs_at_least_one_bound(self):
        self.assertIn("E_POLICY_WINDOW_EMPTY", issues_for(minimal(window={"timezone": "Asia/Shanghai"})))

    def test_offsets_are_rejected_the_timezone_is_the_single_source(self):
        self.assertIn("E_POLICY_DATETIME", issues_for(
            minimal(window={"opensAt": "2026-10-01T09:00+08:00", "timezone": "Asia/Shanghai"})))

    def test_impossible_calendar_date_is_rejected(self):
        self.assertIn("E_POLICY_DATETIME", issues_for(
            minimal(window={"opensAt": "2026-02-30T09:00", "timezone": "Asia/Shanghai"})))

    def test_opening_must_precede_closing(self):
        self.assertIn("E_POLICY_WINDOW_ORDER", issues_for(minimal(window={
            "opensAt": "2026-10-02T09:00", "closesAt": "2026-10-01T09:00", "timezone": "Asia/Shanghai"})))

    def test_local_time_skipped_by_dst_is_rejected(self):
        # 2026-03-08 02:30 在纽约不存在（02:00 直接跳到 03:00）。
        self.assertIn("E_POLICY_LOCAL_TIME", issues_for(minimal(window={
            "opensAt": "2026-03-08T02:30", "timezone": "America/New_York"})))

    def test_ambiguous_local_time_is_rejected(self):
        # 2026-11-01 01:30 在纽约出现两次。
        self.assertIn("E_POLICY_LOCAL_TIME", issues_for(minimal(window={
            "closesAt": "2026-11-01T01:30", "timezone": "America/New_York"})))

    def test_window_conflicts_with_raw_engine_dates(self):
        self.assertIn("E_POLICY_SETTING_CONFLICT", issues_for(
            minimal(window={"closesAt": "2026-10-01T09:00", "timezone": "UTC"}), expires="2026-10-01 00:00:00"))


class AccessTest(unittest.TestCase):
    def test_plaintext_password_never_reaches_the_engine(self):
        self.assertIn("E_POLICY_PLAINTEXT_PASSWORD", issues_for(minimal(access={"password": "s3cret"})))

    def test_malformed_hash_is_rejected(self):
        for bad in ("s3cret", "pbkdf2-sha256$1000$AAAA$BBBB", "bcrypt$12$x$y", "pbkdf2-sha256$abc$AAAA$BBBB",
                    "pbkdf2-sha256$310000$not base64!$BBBB"):
            self.assertIn("E_POLICY_PASSWORD_HASH", issues_for(minimal(access={"passwordHash": bad})), bad)

    def test_well_formed_hash_is_accepted(self):
        self.assertEqual([], issues_for(minimal(access={"passwordHash": password_hash("x")})))

    def test_captcha_must_be_boolean(self):
        self.assertIn("E_POLICY_TYPE", issues_for(minimal(access={"captcha": "yes"})))

    def test_captcha_conflicts_with_raw_setting(self):
        self.assertIn("E_POLICY_SETTING_CONFLICT", issues_for(minimal(access={"captcha": True}), usecaptcha="A"))

    def test_invitation_conflicts_with_raw_access_mode(self):
        self.assertIn("E_POLICY_SETTING_CONFLICT", issues_for(
            minimal(access={"invitationRequired": True}), with_participants(), access_mode="O"))

    def test_invitation_requires_participants(self):
        self.assertIn("E_POLICY_INVITATION", issues_for(minimal(access={"invitationRequired": True})))
        self.assertEqual([], issues_for(minimal(access={"invitationRequired": True}), with_participants()))


class LimitsTest(unittest.TestCase):
    def test_identity_must_be_known(self):
        self.assertIn("E_POLICY_IDENTITY", issues_for(minimal(limits={"responses": [{"by": "wechat", "max": 1}]})))

    def test_identity_may_appear_once(self):
        self.assertIn("E_POLICY_IDENTITY", issues_for(minimal(limits={
            "responses": [{"by": "ip", "max": 1}, {"by": "ip", "max": 2}]})))

    def test_token_identity_requires_participants(self):
        self.assertIn("E_POLICY_IDENTITY", issues_for(minimal(limits={"responses": [{"by": "token", "max": 1}]})))

    def test_token_identity_requires_closed_access(self):
        # 开放访问（access_mode=O）的问卷没有 token 也能答，按 token 限次就形同虚设。
        codes = issues_for(minimal(limits={"responses": [{"by": "token", "max": 1}]}), with_participants())
        self.assertIn("E_POLICY_IDENTITY", codes)
        self.assertEqual([], issues_for(minimal(
            access={"invitationRequired": True}, limits={"responses": [{"by": "token", "max": 1}]}),
            with_participants()))

    def test_limit_bounds(self):
        for bad in (0, -1, 10001, 1.5, True, "1"):
            self.assertTrue(
                {"E_POLICY_RANGE", "E_POLICY_TYPE"} & set(
                    issues_for(minimal(limits={"responses": [{"by": "device", "max": bad}]}))), bad)

    def test_duration_bounds(self):
        for bad in (59, 604801, "600", False):
            self.assertTrue(
                {"E_POLICY_RANGE", "E_POLICY_TYPE"} & set(issues_for(minimal(limits={"maxDurationSeconds": bad}))),
                bad)
        self.assertEqual([], issues_for(minimal(limits={"maxDurationSeconds": 60})))

    def test_limits_need_a_rule(self):
        self.assertIn("E_POLICY_EMPTY", issues_for(minimal(limits={})))


class NetworkTest(unittest.TestCase):
    def test_invalid_cidr_is_rejected(self):
        for bad in ("10.0.0.0/33", "not-an-ip", "", 10):
            self.assertTrue({"E_POLICY_CIDR", "E_POLICY_TYPE"} & set(
                issues_for(minimal(network={"allowIps": [bad]}))), bad)

    def test_ipv6_is_accepted(self):
        self.assertEqual([], issues_for(minimal(network={"denyIps": ["2001:db8::/48", "::1"]})))

    def test_region_codes_are_iso_3166(self):
        self.assertIn("E_POLICY_REGION", issues_for(minimal(network={"allowRegions": ["china"]})))
        self.assertEqual([], issues_for(minimal(network={"allowRegions": ["CN", "CN-BJ"], "denyRegions": ["US"]})))

    def test_region_unknown_is_deny_or_allow(self):
        self.assertIn("E_POLICY_TYPE", issues_for(minimal(network={"allowRegions": ["CN"], "regionUnknown": "maybe"})))


if __name__ == "__main__":
    unittest.main()
