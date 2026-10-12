"""Content-addressed Gate evidence is reusable only across proven-safe inputs."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.config import GateDefinition  # noqa: E402
from agent_harness.evidence_cache import (  # noqa: E402
    RuntimeIdentity, compute_evidence_key, lookup_cached_evidence,
    publish_cached_evidence,
)
from agent_harness.state import GateEvidence, GateStatus  # noqa: E402


class EvidenceCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        (self.repo / "src").mkdir()
        self.source = self.repo / "src/app.py"
        self.source.write_text("print('one')\n", encoding="utf-8")
        contracts = self.repo / "docs/agent"
        contracts.mkdir(parents=True)
        for name in ("GATE_MATRIX.yaml", "PROTECTED_PATHS.yaml",
                     "GATE_SUMMARY_SCHEMA.json", "STATE_SCHEMA.json"):
            (contracts / name).write_text("{}\n", encoding="utf-8")
        self.runtime = RuntimeIdentity(
            "linux", "x86_64", None,
            {"python": "3.12.13", "harness": "2"},
        )
        self.gate = GateDefinition(
            "unit", ("python3", "-m", "unittest"), ".", 30,
            "local_fast", "content", ("src/**",), "unittest",
        )
        self.head = "a" * 40

    def key(self, gate=None, runtime=None):
        return compute_evidence_key(
            self.repo, gate or self.gate, runtime or self.runtime
        )

    def evidence(self, key, head=None):
        attempt = self.repo / "var/agent-harness/runs/run/evidence/gate-one"
        attempt.mkdir(parents=True, exist_ok=True)
        metadata = attempt / "metadata.json"
        metadata.write_text("{}\n", encoding="utf-8")
        summary = attempt / "summary.json"
        summary.write_text(json.dumps({
            "schemaVersion": 1, "gateId": "unit", "status": "passed",
            "exitCode": 0, "durationMs": 1,
            "counts": {"total": 1, "passed": 1, "failed": 0,
                       "skipped": 0, "errors": 0},
            "evidenceKey": key, "headCommit": head or self.head,
            "source": "local", "reused": False, "diagnostic": None,
        }) + "\n", encoding="utf-8")
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        return GateEvidence(
            "unit", GateStatus.PASSED, 0, now, now,
            metadata.relative_to(self.repo), head or self.head,
            summary.relative_to(self.repo), key, "local", False, 0,
        )

    def test_key_changes_with_content_runtime_command_and_dirty_files(self):
        original = self.key()
        self.source.write_text("print('two')\n", encoding="utf-8")
        self.assertNotEqual(original, self.key())
        self.source.write_text("print('one')\n", encoding="utf-8")
        changed_runtime = RuntimeIdentity(
            "linux", "x86_64", None,
            {"python": "3.13.0", "harness": "2"},
        )
        self.assertNotEqual(original, self.key(runtime=changed_runtime))
        changed_command = GateDefinition(
            **{**self.gate.__dict__, "command": ("python3", "-m", "unittest", "-v")}
        )
        self.assertNotEqual(original, self.key(gate=changed_command))

    def test_key_includes_executable_bits_and_rejects_symlink_inputs(self):
        original = self.key()
        self.source.chmod(self.source.stat().st_mode | 0o100)
        executable = self.key()
        self.assertNotEqual(original, executable)
        link = self.repo / "src/link.py"
        link.symlink_to("app.py")
        with self.assertRaises(ValueError):
            self.key()
        link.unlink()
        link.symlink_to("missing.py")
        with self.assertRaises(ValueError):
            self.key()

    def test_key_changes_with_policy_schema_and_harness_contract(self):
        original = self.key()
        policy = self.repo / "docs/agent/PROTECTED_PATHS.yaml"
        policy.write_text('{"version":2}\n', encoding="utf-8")
        self.assertNotEqual(original, self.key())

    def test_missing_inputs_and_unsafe_runtime_identity_fail_closed(self):
        missing = GateDefinition(
            **{**self.gate.__dict__, "input_patterns": ("missing/**",)}
        )
        with self.assertRaises(ValueError):
            self.key(gate=missing)
        for tools in ({}, {"python": ""}, {"TOKEN": "fake-secret"}):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                RuntimeIdentity("linux", "x86_64", None, tools)

    def test_content_gate_reuses_summary_at_new_head_but_strict_gate_does_not(self):
        key = self.key()
        publish_cached_evidence(self.repo, key, self.evidence(key))
        reused = lookup_cached_evidence(self.repo, key, self.gate, "b" * 40)
        self.assertIsNotNone(reused)
        self.assertEqual(reused.head_commit, "b" * 40)
        self.assertEqual(reused.source, "reused")
        self.assertTrue(reused.reused)
        self.assertEqual(reused.status, GateStatus.PASSED)

        for gate in (
            GateDefinition(**{**self.gate.__dict__, "execution_tier": "local_integration"}),
            GateDefinition(**{**self.gate.__dict__, "id": "production-release"}),
            GateDefinition(**{**self.gate.__dict__, "id": "browser-e2e"}),
            GateDefinition(**{**self.gate.__dict__, "id": "migration-mysql"}),
        ):
            with self.subTest(gate=gate.id):
                self.assertIsNone(lookup_cached_evidence(
                    self.repo, key, gate, "b" * 40
                ))

    def test_malformed_or_tampered_cache_entries_are_never_reused(self):
        key = self.key()
        publish_cached_evidence(self.repo, key, self.evidence(key))
        record = self.repo / "var/agent-harness/cache/evidence" / key / "record.json"
        for content in ("{", "{}", json.dumps({"version": 1, "gateId": "other"})):
            record.write_text(content, encoding="utf-8")
            with self.subTest(content=content):
                self.assertIsNone(lookup_cached_evidence(
                    self.repo, key, self.gate, self.head
                ))

    def test_only_passing_complete_evidence_can_be_published(self):
        key = self.key()
        failed = self.evidence(key)
        object.__setattr__(failed, "status", GateStatus.FAILED)
        with self.assertRaises(ValueError):
            publish_cached_evidence(self.repo, key, failed)
        self.assertFalse((self.repo / "var/agent-harness/cache").exists())


if __name__ == "__main__":
    unittest.main()
