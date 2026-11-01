"""Regression coverage for W2's New York deadline fixture."""

import unittest
from datetime import datetime, timedelta, timezone

from access_policy import closed_window_instant, local
from pubgw.policy.timewindow import AMBIGUOUS, LocalTimeError, engine_utc, load_zone


class ClosedWindowInstantTest(unittest.TestCase):
    def test_previous_hour_reproduces_reported_ambiguous_time(self):
        now = datetime(2026, 11, 1, 6, 49, 34, tzinfo=timezone.utc)
        with self.assertRaises(LocalTimeError) as error:
            engine_utc(local("America/New_York", now - timedelta(hours=1)), load_zone("America/New_York"))
        self.assertEqual(AMBIGUOUS, error.exception.reason)

    def test_deadline_is_unambiguous_and_in_the_past_at_dst_transitions(self):
        for now in (datetime(2026, 11, 1, 6, 49, 34, tzinfo=timezone.utc),
                    datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)):
            with self.subTest(now=now):
                closes = closed_window_instant(now)
                self.assertLess(closes, now)
                self.assertEqual(closes, engine_utc(local("America/New_York", closes), load_zone("America/New_York"))[0])


if __name__ == "__main__":
    unittest.main()
