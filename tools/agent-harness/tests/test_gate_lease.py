"""Atomic Gate leases deduplicate concurrent execution without trusting stale owners."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.config import GateDefinition  # noqa: E402
from agent_harness.evidence_cache import RuntimeIdentity, compute_evidence_key  # noqa: E402
from agent_harness.gate_lease import (  # noqa: E402
    acquire_gate_lease, release_gate_lease, wait_for_gate_summary,
)
from agent_harness.gate_runner import run_gate  # noqa: E402


class GateLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        (self.repo / "src").mkdir()
        (self.repo / "src/input.txt").write_text("stable\n", encoding="utf-8")
        self.cache = self.repo / "var/agent-harness/cache/evidence"
        self.key = "a" * 64

    def test_only_one_owner_acquires_live_lease(self):
        first = acquire_gate_lease(self.cache, self.key, "owner-one", 30)
        second = acquire_gate_lease(self.cache, self.key, "owner-two", 30)
        self.assertTrue(first.acquired)
        self.assertFalse(second.acquired)
        self.assertEqual(second.owner, "owner-one")
        release_gate_lease(first)
        third = acquire_gate_lease(self.cache, self.key, "owner-three", 30)
        self.assertTrue(third.acquired)

    def test_expired_lease_reclaims_incomplete_completed_evidence(self):
        first = acquire_gate_lease(self.cache, self.key, "owner-one", 1)
        metadata = first.path / "owner.json"
        document = json.loads(metadata.read_text())
        document["expiresAt"] = "2000-01-01T00:00:00Z"
        document["host"] = "other-host"
        metadata.write_text(json.dumps(document), encoding="utf-8")
        record_dir = self.cache / self.key
        record_dir.mkdir(parents=True)
        (record_dir / "record.json").write_text(json.dumps({
            "version": 1, "key": self.key, "gateId": "unit", "status": "passed",
            "exitCode": 0, "startedAt": "2026-10-12T00:00:00Z",
            "endedAt": "2026-10-12T00:00:01Z", "originalHead": "b" * 40,
            "summarySha256": "c" * 64,
        }), encoding="utf-8")
        reclaimed = acquire_gate_lease(self.cache, self.key, "owner-two", 30)
        self.assertTrue(reclaimed.acquired)

    def test_expired_lease_is_not_reclaimed_while_local_owner_process_is_alive(self):
        first = acquire_gate_lease(self.cache, self.key, "owner-one", 1)
        metadata = first.path / "owner.json"
        document = json.loads(metadata.read_text())
        document["expiresAt"] = "2000-01-01T00:00:00Z"
        metadata.write_text(json.dumps(document), encoding="utf-8")
        blocked = acquire_gate_lease(self.cache, self.key, "owner-two", 30)
        self.assertFalse(blocked.acquired)
        self.assertEqual(blocked.owner, "owner-one")

    def test_interrupted_owner_metadata_write_does_not_leave_live_lease(self):
        with patch("agent_harness.gate_lease.os.replace", side_effect=OSError("replace")):
            with self.assertRaises(OSError):
                acquire_gate_lease(self.cache, self.key, "owner-one", 30)
        lease_path = self.cache / ".leases" / self.key
        self.assertFalse(lease_path.exists())
        self.assertTrue(acquire_gate_lease(
            self.cache, self.key, "owner-two", 30
        ).acquired)

    def test_ten_concurrent_gate_requests_execute_one_process(self):
        counter = self.repo / "counter.txt"
        script = ("from pathlib import Path; import time; p=Path('counter.txt'); "
                  "p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); "
                  "time.sleep(0.15); print('done')")
        gate = GateDefinition(
            "unit", (sys.executable, "-c", script), ".", 5,
            "local_fast", "content", ("src/**",), "generic",
        )
        runtime = RuntimeIdentity("test-os", "test-arch", None, {"python": "test"})
        evidence_dir = self.repo / "var/agent-harness/runs/run/evidence"

        def execute(_):
            return run_gate(self.repo, gate, evidence_dir, "d" * 40,
                            runtime_identity=runtime)

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(execute, range(10)))
        self.assertEqual(counter.read_text(), "1")
        self.assertEqual(sum(not result.reused for result in results), 1)
        self.assertEqual(sum(result.reused for result in results), 9)
        self.assertEqual(len({result.evidence_key for result in results}), 1)
        summary = wait_for_gate_summary(
            self.cache, compute_evidence_key(self.repo, gate, runtime), 1
        )
        self.assertEqual(summary.status, "passed")


if __name__ == "__main__":
    unittest.main()
