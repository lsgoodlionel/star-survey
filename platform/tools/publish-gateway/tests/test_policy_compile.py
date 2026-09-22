"""访问策略的编译：本地时刻→UTC、原生设置、插件设置（LSS plugin_settings）与摘要。"""

import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ElementTree

from pubgw.compiler import LssCompiler
from pubgw.policy.compile import PLUGIN_NAME, POLICY_KEY, PAYLOAD_SCHEMA, compile_policy

from .fixtures import sample_definition
from .policy_fixtures import full_policy, password_hash, policy_definition, with_participants


def minimal(**blocks):
    policy = {"policyVersion": 1}
    policy.update(blocks)
    return policy


def survey_row(lss):
    root = ElementTree.fromstring(lss)
    row = root.find("surveys/rows/row")
    return {child.tag: (child.text or "") for child in row}


def plugin_rows(lss):
    root = ElementTree.fromstring(lss)
    return [{child.tag: (child.text or "") for child in row} for row in root.findall("plugin_settings/rows/row")]


class WindowConversionTest(unittest.TestCase):
    def compile_window(self, **window):
        return compile_policy(policy_definition(minimal(window=window)))

    def test_shanghai_local_time_becomes_utc(self):
        compiled = self.compile_window(opensAt="2026-10-01T09:00", closesAt="2026-10-07T18:30:15",
                                       timezone="Asia/Shanghai")
        self.assertEqual("2026-10-01 01:00:00", compiled.native_settings["startdate"])
        self.assertEqual("2026-10-07 10:30:15", compiled.native_settings["expires"])

    def test_new_york_uses_the_offset_in_force_on_that_date(self):
        winter = self.compile_window(opensAt="2026-01-15T09:00", timezone="America/New_York")
        summer = self.compile_window(opensAt="2026-07-15T09:00", timezone="America/New_York")
        self.assertEqual("2026-01-15 14:00:00", winter.native_settings["startdate"])
        self.assertEqual("2026-07-15 13:00:00", summer.native_settings["startdate"])

    def test_window_crossing_a_dst_change_keeps_real_duration(self):
        # 纽约 2026-03-08 02:00 拨快一小时：本地 01:00→04:00 实际只过了 2 小时。
        compiled = self.compile_window(opensAt="2026-03-08T01:00", closesAt="2026-03-08T04:00",
                                       timezone="America/New_York")
        self.assertEqual("2026-03-08 06:00:00", compiled.native_settings["startdate"])
        self.assertEqual("2026-03-08 08:00:00", compiled.native_settings["expires"])

    def test_plugin_payload_keeps_local_text_for_messages(self):
        compiled = self.compile_window(closesAt="2026-10-07T18:30", timezone="Asia/Shanghai")
        window = json.loads(compiled.payload)["window"]
        self.assertEqual(
            {"opensAt": None, "closesAt": "2026-10-07 10:30:00", "timezone": "Asia/Shanghai",
             "opensAtLocal": None, "closesAtLocal": "2026-10-07T18:30"},
            window,
        )
        self.assertNotIn("startdate", compiled.native_settings)


class NativeAndPluginSplitTest(unittest.TestCase):
    def test_captcha_is_native_only(self):
        compiled = compile_policy(policy_definition(minimal(access={"captcha": True})))
        self.assertEqual({"usecaptcha": "X"}, compiled.native_settings)
        self.assertIsNone(compiled.payload)
        self.assertIsNone(compiled.digest)

    def test_invitation_alone_needs_no_plugin(self):
        compiled = compile_policy(policy_definition(minimal(access={"invitationRequired": True}),
                                                    with_participants()))
        self.assertIsNone(compiled.payload)

    def test_definition_without_policy_compiles_to_nothing(self):
        self.assertIsNone(compile_policy(sample_definition()))

    def test_full_payload_shape(self):
        compiled = compile_policy(policy_definition(full_policy(), with_participants()))
        payload = json.loads(compiled.payload)
        self.assertEqual(PAYLOAD_SCHEMA, payload["schema"])
        self.assertEqual(full_policy()["access"]["passwordHash"], payload["passwordHash"])
        self.assertEqual(1800, payload["maxDurationSeconds"])
        # 维度按可靠程度排序：token → device → ip。
        self.assertEqual([{"by": "token", "max": 1}, {"by": "ip", "max": 5}], payload["responses"])
        self.assertEqual(
            {"allowIps": ["10.0.0.0/8", "2001:db8::/32"], "denyIps": ["10.9.9.9/32"],
             "allowRegions": ["CN"], "denyRegions": [], "regionUnknown": "deny"},
            payload["network"],
        )

    def test_payload_is_canonical_ascii_and_digest_matches(self):
        compiled = compile_policy(policy_definition(full_policy(), with_participants()))
        self.assertTrue(compiled.payload.isascii())
        self.assertNotIn(", ", compiled.payload)
        self.assertNotIn('": ', compiled.payload)
        self.assertEqual(hashlib.sha256(compiled.payload.encode("ascii")).hexdigest(), compiled.digest)
        again = compile_policy(policy_definition(full_policy(), with_participants()))
        self.assertEqual(compiled.payload, again.payload)

    def test_region_unknown_defaults_to_deny(self):
        compiled = compile_policy(policy_definition(minimal(network={"allowRegions": ["CN"]})))
        self.assertEqual("deny", json.loads(compiled.payload)["network"]["regionUnknown"])


class LssOutputTest(unittest.TestCase):
    def test_policy_is_carried_as_one_plugin_setting(self):
        definition = policy_definition(full_policy(), with_participants())
        compiled = LssCompiler().compile(definition)

        rows = plugin_rows(compiled.lss)
        self.assertEqual([{"name": PLUGIN_NAME, "key": POLICY_KEY, "value": compiled.policy.payload}], rows)
        self.assertEqual(compiled.policy.digest, hashlib.sha256(rows[0]["value"].encode()).hexdigest())

    def test_native_settings_land_in_the_survey_row(self):
        compiled = LssCompiler().compile(policy_definition(full_policy(), with_participants()))
        row = survey_row(compiled.lss)
        self.assertEqual("X", row["usecaptcha"])
        self.assertEqual("2026-10-01 01:00:00", row["startdate"])
        self.assertEqual("2026-10-07 10:30:00", row["expires"])

    def test_no_policy_means_byte_identical_output(self):
        compiled = LssCompiler().compile(sample_definition())
        self.assertNotIn("plugin_settings", compiled.lss)
        self.assertNotIn("<startdate", compiled.lss)
        self.assertIsNone(compiled.policy)

    def test_password_hash_is_the_only_secret_material_in_the_lss(self):
        compiled = LssCompiler().compile(policy_definition(minimal(access={"passwordHash": password_hash("s3cret")})))
        self.assertNotIn("s3cret", compiled.lss)
        self.assertEqual(1, len(re.findall(r"pbkdf2-sha256\$", compiled.lss)))

    def test_policy_does_not_change_the_structure_fingerprint(self):
        plain = LssCompiler().compile(sample_definition())
        guarded = LssCompiler().compile(policy_definition(full_policy(), with_participants()))
        self.assertEqual(plain.fingerprint, guarded.fingerprint)


if __name__ == "__main__":
    unittest.main()
