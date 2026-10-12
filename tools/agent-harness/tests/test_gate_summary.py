"""Deterministic Gate summaries expose counts and bounded diagnostics, not logs."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.config import GateDefinition  # noqa: E402
from agent_harness.gate_summary import (  # noqa: E402
    GateSummary, build_gate_summary, write_gate_summary_atomic,
)
from agent_harness.state import GateEvidence, GateStatus  # noqa: E402


class GateSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.attempt = self.repo / "var/agent-harness/runs/run/evidence/gate-one"
        self.attempt.mkdir(parents=True)
        self.metadata = self.attempt / "metadata.json"
        self.metadata.write_text("{}\n", encoding="utf-8")
        self.started = datetime(2026, 10, 12, tzinfo=timezone.utc)
        self.ended = self.started + timedelta(milliseconds=1250)

    def evidence(self, status=GateStatus.PASSED, exit_code=0):
        return GateEvidence(
            "unit", status, exit_code, self.started, self.ended,
            self.metadata.relative_to(self.repo), "a" * 40,
            evidence_key="b" * 64,
        )

    def gate(self, summary_format="generic"):
        return GateDefinition(
            "unit", ("check",), ".", 10, "local_fast", "content",
            ("src/**",), summary_format,
        )

    def logs(self, stdout="", stderr=""):
        (self.attempt / "stdout.log").write_text(stdout, encoding="utf-8")
        (self.attempt / "stderr.log").write_text(stderr, encoding="utf-8")

    def test_passing_summary_has_counts_but_never_log_payload(self):
        marker = "SUCCESS_LOG_MUST_STAY_LOCAL"
        self.logs(stdout="Ran 3 tests in 0.1s\nOK\n" + marker)
        summary = build_gate_summary(self.repo, self.gate("unittest"), self.evidence())
        self.assertIsInstance(summary, GateSummary)
        self.assertEqual(summary.status, "passed")
        self.assertEqual(summary.duration_ms, 1250)
        self.assertEqual(dict(summary.counts), {
            "total": 3, "passed": 3, "failed": 0, "skipped": 0, "errors": 0,
        })
        self.assertIsNone(summary.diagnostic)
        self.assertNotIn(marker, repr(summary))
        with self.assertRaises(FrozenInstanceError):
            summary.status = "failed"

    def test_supported_formats_extract_only_conservative_counts(self):
        cases = (
            ("unittest", "Ran 8 tests in 1.2s\nFAILED (failures=2, errors=1, skipped=1)",
             {"total": 8, "passed": 4, "failed": 2, "skipped": 1, "errors": 1}),
            ("surefire", "Tests run: 7, Failures: 1, Errors: 2, Skipped: 1",
             {"total": 7, "passed": 3, "failed": 1, "skipped": 1, "errors": 2}),
            ("vitest", "Tests  2 failed | 5 passed | 1 skipped (8)",
             {"total": 8, "passed": 5, "failed": 2, "skipped": 1, "errors": 0}),
            ("playwright", "  1 failed\n  2 skipped\n  6 passed (12.3s)",
             {"total": 9, "passed": 6, "failed": 1, "skipped": 2, "errors": 0}),
            ("generic", "999 passed and everything is wonderful",
             {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}),
        )
        for summary_format, output, expected in cases:
            with self.subTest(summary_format=summary_format):
                self.logs(stdout=output)
                summary = build_gate_summary(
                    self.repo, self.gate(summary_format),
                    self.evidence(GateStatus.FAILED, 1),
                )
                self.assertEqual(dict(summary.counts), expected)
        self.logs(stdout="not a recognized report")
        summary = build_gate_summary(self.repo, self.gate("vitest"), self.evidence())
        self.assertEqual(dict(summary.counts), {
            "total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0,
        })

    def test_failure_summary_is_redacted_related_bounded_and_utf8_safe(self):
        secret = "sk-" + "x" * 48
        output = "\n".join((
            "ERROR: 根因 TOKEN=" + secret,
            "ERROR: 关联错误一 " + "测" * 3000,
            "ERROR: 关联错误二",
            "ERROR: 关联错误三",
            "ERROR: 不应出现的第四关联错误",
        ))
        self.logs(stderr=output)
        summary = build_gate_summary(
            self.repo, self.gate("unittest"),
            self.evidence(GateStatus.FAILED, 1), max_diagnostic_bytes=4096,
        )
        encoded = summary.diagnostic.encode("utf-8")
        self.assertLessEqual(len(encoded), 4096)
        encoded.decode("utf-8")
        self.assertNotIn(secret, summary.diagnostic)
        self.assertIn("[REDACTED]", summary.diagnostic)
        self.assertIn("根因", summary.diagnostic)
        self.assertLessEqual(summary.diagnostic.count("Related:"), 3)
        self.assertNotIn("不应出现的第四关联错误", summary.diagnostic)

    def test_atomic_writer_matches_schema_shape_and_preserves_old_file_on_failure(self):
        self.logs(stdout="Ran 1 test in 0.1s\nOK")
        summary = build_gate_summary(self.repo, self.gate("unittest"), self.evidence())
        path = self.attempt / "summary.json"
        write_gate_summary_atomic(path, summary)
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["schemaVersion"], 1)
        self.assertEqual(document["source"], "local")
        self.assertFalse(document["reused"])
        self.assertEqual(document["diagnostic"], None)
        original = path.read_bytes()
        with patch("agent_harness.gate_summary.os.replace", side_effect=OSError("replace")):
            with self.assertRaises(OSError):
                write_gate_summary_atomic(path, summary)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual({item.name for item in self.attempt.iterdir()},
                         {"metadata.json", "stdout.log", "stderr.log", "summary.json"})


if __name__ == "__main__":
    unittest.main()
