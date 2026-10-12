"""Integrated acceptance fixture for the token-efficiency control plane."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.config import GateDefinition  # noqa: E402
from agent_harness.context_capsule import build_context_capsule  # noqa: E402
from agent_harness.evidence_cache import RuntimeIdentity  # noqa: E402
from agent_harness.gate_lease import acquire_gate_lease  # noqa: E402
from agent_harness.gate_runner import run_gate  # noqa: E402
from agent_harness.github_evidence import GitHubEvidenceError, import_github_evidence  # noqa: E402
from agent_harness.memory_projection import (  # noqa: E402
    apply_memory_sync, configure_memory, load_projection_config,
    plan_memory_sync, render_projection,
)
from agent_harness.state import load_state  # noqa: E402
from agent_harness.token_report import TokenReport, TokenUsage  # noqa: E402


class TokenEfficiencyIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        (self.repo / "docs/agent").mkdir(parents=True)
        (self.repo / "src").mkdir()
        (self.repo / "src/input.txt").write_text("stable\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text(
            "# Rules\n\n## 实现与验证\nverify\n\n## 安全与权限\nsecure\n"
            "\n## 自治执行与停止条件\nstop\n", encoding="utf-8")
        (self.repo / "docs/agent/AUTONOMY.md").write_text(
            "# SOP\n\n## 修复预算与人工升级\nthree failures\n", encoding="utf-8")
        (self.repo / "docs/agent/PROJECT_MEMORY.md").write_text(
            "# Memory\n\n## 身份与权威入口\ntruth\n"
            "\n## 已确认边界与维护\nstable\n", encoding="utf-8")
        self.state = replace(
            load_state(HARNESS / "tests/fixtures/valid_state.json"),
            worktree_path=self.repo,
            updated_at=datetime(2026, 10, 12, 8, tzinfo=timezone.utc),
        )
        self.runtime = RuntimeIdentity("test-os", "test-arch", None,
                                       {"python": "test"})
        self.evidence = self.repo / "var/agent-harness/runs/run/evidence"

    def test_ten_agents_share_prefix_execute_one_gate_and_recover_other_boundaries(self):
        capsules = [build_context_capsule(self.repo, self.state, f"task-{index}")
                    for index in range(10)]
        self.assertEqual(len({item.stable_prefix for item in capsules}), 1)
        self.assertTrue(all(item.total_bytes <= 24576 for item in capsules))
        self.assertTrue(all(item.manifest.stable_bytes <= 12288 for item in capsules))
        self.assertTrue(all(item.manifest.task_bytes <= 8192 for item in capsules))

        counter = self.repo / "counter.txt"
        script = ("from pathlib import Path; import time; p=Path('counter.txt'); "
                  "p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); "
                  "time.sleep(0.15); print('PASS_LOG_MUST_NOT_APPEAR')")
        gate = GateDefinition("unit", (sys.executable, "-c", script), ".", 5,
                              "local_fast", "content", ("src/**",), "generic")
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(
                lambda _: run_gate(self.repo, gate, self.evidence, "d" * 40,
                                   runtime_identity=self.runtime), range(10)))
        self.assertEqual(counter.read_text(), "1")
        self.assertEqual(sum(not item.reused for item in results), 1)
        self.assertEqual(sum(item.reused for item in results), 9)
        summary = (self.repo / results[0].summary_path).read_text(encoding="utf-8")
        self.assertNotIn("PASS_LOG_MUST_NOT_APPEAR", summary)

        failed_gate = GateDefinition(
            "failure", (sys.executable, "-c",
                        "print('TOKEN=fake-secret'); raise SystemExit(3)"),
            ".", 5, "local_fast", "content", ("src/**",), "generic")
        failed = run_gate(self.repo, failed_gate, self.evidence, "d" * 40,
                          runtime_identity=self.runtime)
        failure_summary = json.loads((self.repo / failed.summary_path).read_text())
        self.assertLessEqual(len(failure_summary["diagnostic"].encode()), 4096)
        self.assertNotIn("fake-secret", failure_summary["diagnostic"])

        stale = acquire_gate_lease(self.repo / "var/agent-harness/cache/evidence",
                                   "e" * 64, "old", 1)
        owner = stale.path / "owner.json"
        document = json.loads(owner.read_text())
        document["expiresAt"] = "2000-01-01T00:00:00Z"
        document["pid"] = 99_999_999
        owner.write_text(json.dumps(document), encoding="utf-8")
        self.assertTrue(acquire_gate_lease(
            self.repo / "var/agent-harness/cache/evidence", "e" * 64,
            "new", 30).acquired)

        hostile = self.root / "hostile.json"
        hostile.write_text("{}", encoding="utf-8")
        with self.assertRaises(GitHubEvidenceError):
            import_github_evidence(
                hostile, expected_artifact_sha256=hashlib.sha256(hostile.read_bytes()).hexdigest(),
                repository="owner/repo", workflow=".github/workflows/agent-governance.yml",
                commit_sha="d" * 40, job="unit", gate_id="unit")

        (self.repo / "docs/agent/MEMORY_PROJECTIONS.json").write_text(json.dumps({
            "version": 1, "projections": [{"id": "progress",
                "notePath": "Project/Progress.md", "startMarker": "<!-- s -->",
                "endMarker": "<!-- e -->"}]}), encoding="utf-8")
        vault = self.root / "offline-vault"
        configure_memory(self.repo, vault)
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        report = TokenReport(now, now.replace(day=13), "docs/plan.md", 1, 9, 1,
                             TokenUsage(100, 70, 30, 5, 2), TokenUsage())
        projection = render_projection(self.state, {"deliveryStatus": "pending"}, report)
        pending = apply_memory_sync(plan_memory_sync(
            load_projection_config(self.repo), projection))
        self.assertEqual(pending.pending, ("progress",))
        vault.mkdir()
        replayed = apply_memory_sync(plan_memory_sync(
            load_projection_config(self.repo), projection))
        self.assertEqual(replayed.updated, ("progress",))

        serialized = json.dumps(report.to_dict())
        self.assertNotIn("message", serialized)
        self.assertNotIn("prompt", serialized)
        baseline = json.loads((HARNESS / "tests/fixtures/token-baseline.json").read_text())
        raw_reduction = 100 * (1 - capsules[0].total_bytes / baseline["rawContextBytes"])
        active_estimate = capsules[0].total_bytes / 4
        startup_reduction = 100 * (1 - active_estimate / baseline["startupActiveTokens"])
        self.assertGreaterEqual(raw_reduction, baseline["minimumRawReductionPercent"])
        self.assertGreaterEqual(startup_reduction,
                                baseline["minimumStartupReductionPercent"])


if __name__ == "__main__":
    unittest.main()
