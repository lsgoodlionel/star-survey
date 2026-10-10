"""End-to-end fault drills against the real Harness boundaries."""

import importlib.util
import io
import json
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[3]
RUNNER = REPO / "tools/agent-harness/drills/run_drills.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("agent_harness_drills", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FaultInjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def assert_drill_passes(self, name):
        result = self.runner.run_named_drill(name)
        self.assertEqual(result.status, "passed", result.detail)
        self.assertNotIn("fake-secret", result.detail)

    def test_process_termination_before_atomic_replace_keeps_old_state(self):
        self.assert_drill_passes("atomic-before-replace")

    def test_process_termination_after_atomic_replace_keeps_new_state(self):
        self.assert_drill_passes("atomic-after-replace")

    def test_truncated_event_is_rejected(self):
        self.assert_drill_passes("truncated-events")

    def test_changed_plan_digest_pauses_resume(self):
        result = self.runner.run_named_drill("plan-digest-drift")
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual(result.detail, "paused: plan digest drift")

    def test_rewritten_git_history_pauses_resume(self):
        self.assert_drill_passes("rewritten-history")

    def test_missing_docker_node_and_java_are_typed(self):
        self.assert_drill_passes("missing-runtimes")

    def test_fake_secret_is_redacted_from_diagnostics(self):
        self.assert_drill_passes("fake-secret-diagnostics")

    def test_repeated_gate_failure_stops_at_existing_threshold(self):
        self.assert_drill_passes("repeated-gate-failure")

    def test_codex_timeout_is_bounded_and_typed(self):
        self.assert_drill_passes("codex-timeout")

    def test_default_cli_runs_only_fake_drills(self):
        with patch.object(self.runner, "run_real_codex_smoke") as real, redirect_stdout(io.StringIO()):
            self.assertEqual(self.runner.main([]), 0)
        real.assert_not_called()

    def test_real_codex_flag_invokes_one_smoke_only(self):
        result = self.runner.DrillResult("real-codex-smoke", "paused", "sanitized pause")
        with patch.object(self.runner, "ensure_no_production_secret_paths") as guard, \
                patch.object(self.runner, "run_real_codex_smoke", return_value=result) as real, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.runner.main(["--allow-real-codex"]), 0)
        real.assert_called_once_with(REPO)
        guard.assert_not_called()

    def test_real_codex_refuses_production_secret_paths_even_if_tracked(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"],
                           cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"],
                           cwd=repo, check=True, capture_output=True)
            secret = repo / ".aws/credentials"
            secret.parent.mkdir()
            secret.write_text("token=fake-secret\n", encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo)
            subprocess.run(["git", "add", ".aws/credentials"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "bad secret fixture"], cwd=repo,
                           check=True, capture_output=True)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo)

    def test_forbidden_scan_reads_multiline_workflow_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            matrix = repo / "docs/agent/GATE_MATRIX.yaml"
            matrix.parent.mkdir(parents=True)
            matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
            workflow = repo / ".github/workflows/bad.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  bad:\n    steps:\n      - run: |\n"
                "          codex exec --dangerously-bypass-approvals-and-sandbox task\n",
                encoding="utf-8",
            )
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_forbidden_options(repo)

    def test_real_smoke_summary_keeps_sanitized_stop_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.runner._state(Path(directory), self.runner.RunStatus.PAUSED)
            state = replace(state, decisions=({
                "type": "transition",
                "summary": "Codex adapter timeout PASSWORD=fake-secret",
                "createdAt": "2026-10-10T00:00:00Z",
            },))
            detail = self.runner.real_smoke_detail(state, (), Path("history.md"))
        self.assertNotIn("fake-secret", detail)
        self.assertEqual(json.loads(detail)["stopReason"],
                         "Codex adapter timeout PASSWORD=[REDACTED]")

    def test_real_smoke_branch_names_are_unique_and_disposable(self):
        first = self.runner.real_smoke_branch_name()
        second = self.runner.real_smoke_branch_name()
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^drill/real-codex-smoke-[0-9a-f]{12}$")

    def test_real_smoke_infrastructure_failure_is_not_reported_as_pause(self):
        identity = ("a" * 40, b"")
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(self.runner, "_primary_identity", return_value=identity), \
                patch.object(self.runner, "_git", side_effect=OSError("PASSWORD=fake-secret")), \
                patch.object(self.runner, "_run", return_value=completed):
            result = self.runner.run_real_codex_smoke(REPO)
        self.assertEqual(result.status, "failed")
        self.assertNotIn("fake-secret", result.detail)


if __name__ == "__main__":
    unittest.main()
