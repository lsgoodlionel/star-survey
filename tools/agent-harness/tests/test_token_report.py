"""Token telemetry reads numeric metadata without exposing session content."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.token_report import collect_token_usage  # noqa: E402


class TokenReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "docs/agent").mkdir(parents=True)
        (self.project / "docs/agent/HARNESS_HOST.json").write_text(json.dumps({
            "activePlan": "docs/superpowers/plans/current.md"
        }), encoding="utf-8")
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        self.since = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.until = datetime(2026, 10, 13, 0, 0, tzinfo=timezone.utc)

    def event(self, timestamp, event_type, payload):
        return {"timestamp": timestamp, "type": event_type, "payload": payload}

    def usage(self, total, *, cached=0, output=0, reasoning=0):
        return {"input_tokens": total - output - reasoning,
                "cached_input_tokens": cached, "output_tokens": output,
                "reasoning_output_tokens": reasoning, "total_tokens": total}

    def write_session(self, name, source, records, cwd=None):
        path = self.sessions / (name + ".jsonl")
        meta = self.event("2026-10-12T01:00:00Z", "session_meta", {
            "id": name, "cwd": str(cwd or self.project), "source": source,
            "model": "gpt-test",
        })
        lines = [json.dumps(meta), *[json.dumps(item) for item in records]]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def token(self, timestamp, usage):
        return self.event(timestamp, "event_msg", {
            "type": "token_count", "info": {"total_token_usage": usage}
        })

    def test_aggregates_main_deltas_subagent_finals_and_separate_review_usage(self):
        self.write_session("main", "cli", [
            self.token("2026-10-11T23:59:00Z", self.usage(100, cached=70, output=10)),
            self.token("2026-10-12T12:00:00Z", self.usage(180, cached=130, output=20,
                                                          reasoning=5)),
        ])
        self.write_session("sub", {"subagent": "worker"}, [
            self.token("2026-10-12T02:00:00Z", self.usage(50, cached=30, output=8,
                                                          reasoning=2)),
        ])
        self.write_session("review", {"subagent": "guardian_review"}, [
            self.token("2026-10-12T03:00:00Z", self.usage(30, cached=20, output=5)),
        ])
        self.write_session("other", "cli", [
            self.token("2026-10-12T04:00:00Z", self.usage(999)),
        ], cwd=self.root / "unrelated")
        report = collect_token_usage((self.sessions,), since=self.since,
                                     until=self.until, project_root=self.project)
        self.assertEqual((report.main_sessions, report.subagent_sessions,
                          report.reviewer_sessions), (1, 1, 1))
        self.assertEqual(report.usage.raw_tokens, 130)
        self.assertEqual(report.usage.cached_input_tokens, 90)
        self.assertEqual(report.usage.active_tokens, 40)
        self.assertEqual(report.review_usage.raw_tokens, 30)
        self.assertEqual(report.review_usage.active_tokens, 10)

    def test_missing_cache_values_malformed_lines_and_messages_never_leak(self):
        path = self.write_session("main", "cli", [
            self.token("2026-10-12T12:00:00Z", self.usage(12, output=2)),
            self.event("2026-10-12T12:01:00Z", "response_item", {
                "message": "TOKEN=fake-secret source code and prompt"
            }),
        ])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("{malformed\n")
        report = collect_token_usage((self.sessions,), since=self.since,
                                     until=self.until, project_root=self.project)
        serialized = json.dumps(report.to_dict(), ensure_ascii=False)
        self.assertEqual(report.usage.cached_input_tokens, 0)
        for forbidden in ("fake-secret", "source code", "prompt", "message"):
            self.assertNotIn(forbidden, serialized)

    def test_rejects_invalid_windows_roots_and_does_not_follow_session_symlinks(self):
        outside = self.root / "outside.jsonl"
        outside.write_text("TOKEN=fake-outside", encoding="utf-8")
        (self.sessions / "escape.jsonl").symlink_to(outside)
        report = collect_token_usage((self.sessions,), since=self.since,
                                     until=self.until, project_root=self.project)
        self.assertEqual(report.usage.raw_tokens, 0)
        with self.assertRaises(ValueError):
            collect_token_usage((self.sessions,), since=self.until,
                                until=self.since, project_root=self.project)


if __name__ == "__main__":
    unittest.main()
