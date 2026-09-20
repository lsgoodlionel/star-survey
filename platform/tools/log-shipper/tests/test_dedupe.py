"""去重与限流测试：错误风暴不得刷爆仓库，但新错误必须立刻能传上去。"""

import tempfile
import unittest
from pathlib import Path

from logship.dedupe import ShipGate, GateLimits


class ShipGateTest(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.state_path = Path(self._tempdir.name) / "state.json"

    def tearDown(self):
        self._tempdir.cleanup()

    def gate(self, limits=None, now=1000.0):
        return ShipGate(self.state_path, limits or GateLimits(cooldown_seconds=600, daily_limit=5), now=lambda: now)

    def test_first_occurrence_is_allowed(self):
        decision = self.gate().evaluate("fingerprint-a")

        self.assertTrue(decision.allowed)

    def test_same_fingerprint_is_blocked_during_cooldown(self):
        gate = self.gate()
        gate.record("fingerprint-a")

        decision = self.gate(now=1500.0).evaluate("fingerprint-a")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "cooldown")
        self.assertEqual(decision.suppressed_count, 1)

    def test_same_fingerprint_is_allowed_again_after_cooldown(self):
        gate = self.gate()
        gate.record("fingerprint-a")

        decision = self.gate(now=1000.0 + 601).evaluate("fingerprint-a")

        self.assertTrue(decision.allowed)

    def test_a_different_fingerprint_is_not_blocked_by_another_ones_cooldown(self):
        gate = self.gate()
        gate.record("fingerprint-a")

        decision = self.gate(now=1100.0).evaluate("fingerprint-b")

        self.assertTrue(decision.allowed)

    def test_daily_limit_stops_uploads(self):
        limits = GateLimits(cooldown_seconds=0, daily_limit=3)
        for index in range(3):
            self.gate(limits, now=1000.0 + index).record(f"fingerprint-{index}")

        decision = self.gate(limits, now=1010.0).evaluate("fingerprint-new")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "daily_limit")

    def test_daily_limit_resets_after_24h(self):
        limits = GateLimits(cooldown_seconds=0, daily_limit=1)
        self.gate(limits).record("fingerprint-a")

        decision = self.gate(limits, now=1000.0 + 86401).evaluate("fingerprint-b")

        self.assertTrue(decision.allowed)

    def test_suppressed_occurrences_are_counted_and_reported_on_next_upload(self):
        gate = self.gate()
        gate.record("fingerprint-a")
        for moment in (1100.0, 1200.0, 1300.0):
            self.gate(now=moment).evaluate("fingerprint-a")

        decision = self.gate(now=1000.0 + 601).evaluate("fingerprint-a")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.suppressed_count, 3)

    def test_state_file_is_created_with_restrictive_permissions(self):
        self.gate().record("fingerprint-a")

        self.assertTrue(self.state_path.exists())
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_state_file_does_not_block_shipping(self):
        self.state_path.write_text("{ not json", encoding="utf-8")

        decision = self.gate().evaluate("fingerprint-a")

        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
