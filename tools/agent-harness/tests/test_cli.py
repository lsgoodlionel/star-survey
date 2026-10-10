"""CLI contracts use the real service and disposable Git worktrees."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_run_service import WorktreeCase
from agent_harness.cli import main


class CliTests(WorktreeCase):
    def call(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--repo", str(self.repo), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_lifecycle_and_single_json_objects(self):
        code, output, _ = self.call("init", "--plan", str(self.plan), "--milestone", "m1", "--json")
        self.assertEqual(code, 0)
        run_id = json.loads(output)["runId"]
        for command in (("status",), ("next",), ("record-decision", "--type", "review", "--summary", "ok"), ("gate",)):
            with self.subTest(command=command):
                code, output, _ = self.call(*command, "--run-id", run_id, "--json")
                self.assertEqual(code, 0)
                self.assertIsInstance(json.loads(output), dict)
                self.assertNotIn("\x1b", output)
        self.assertEqual(self.call("finalize", "--run-id", run_id)[0], 0)

    def test_exit_codes_invalid_policy_gate_paused_and_success(self):
        self.assertEqual(self.call("nonsense")[0], 2)
        self.assertEqual(self.call("status", "--run-id", "../../escape", "--json")[0], 2)
        self.write(self.repo / "unknown.txt", "dirty")
        self.assertEqual(self.call("init", "--plan", str(self.plan), "--milestone", "m1")[0], 3)
        (self.repo / "unknown.txt").unlink()
        self.init_run()
        self.assertEqual(self.call("pause", "--reason", "manual", "--run-id", self.state.run_id)[0], 5)
        self.assertEqual(self.call("status", "--run-id", self.state.run_id, "--json")[0], 5)
        self.assertEqual(self.call("next", "--run-id", self.state.run_id)[0], 5)
        self.assertEqual(self.call("resume", "--run-id", self.state.run_id)[0], 0)
        self.assertEqual(self.call("gate", "--extra-gate", "unknown", "--run-id", self.state.run_id)[0], 2)

    def test_current_run_selection_and_ambiguity(self):
        self.init_run()
        self.assertEqual(json.loads(self.call("status", "--json")[1])["runId"], self.state.run_id)
        self.init_run()
        self.assertEqual(self.call("status", "--json")[0], 2)

    def test_doctor_json_and_wrapper_from_nested_directory(self):
        repo = Path(__file__).resolve().parents[3]
        wrapper = repo / "scripts/agent-harness"
        result = subprocess.run([str(wrapper), "doctor", "--json"], cwd=repo / "tools/agent-harness",
                                capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertIn(result.returncode, (0, 2))
        self.assertEqual(set(json.loads(result.stdout)), {"ok", "warning", "error"})
        self.assertEqual(result.stderr, "")

    def test_json_error_is_one_sanitized_object(self):
        code, output, stderr = self.call("init", "--plan", "TOKEN=fake-secret", "--milestone", "m1", "--json")
        self.assertEqual(code, 2)
        self.assertIsInstance(json.loads(output), dict)
        self.assertNotIn("fake-secret", output + stderr)

    def test_json_status_redacts_values_without_breaking_json_or_truncating(self):
        self.init_run()
        self.service.record_decision(self.state.run_id, "review", 'TOKEN="fake value"\nAuthorization: Bearer fake-bearer')
        code, output, stderr = self.call("status", "--json")
        self.assertEqual(code, 0)
        document = json.loads(output)
        self.assertEqual(document["runId"], self.state.run_id)
        self.assertNotIn("fake value", output)
        self.assertNotIn("fake-bearer", output)
        self.assertEqual(stderr, "")

    def test_gate_failure_exit_four_from_real_cli(self):
        path = self.repo / "docs/agent/GATE_MATRIX.yaml"
        matrix = json.loads(path.read_text())
        matrix["gates"][0]["command"] = [sys.executable, "-c", "raise SystemExit(9)"]
        self.write(path, json.dumps(matrix))
        self.git("add", str(path))
        self.git("commit", "-m", "failed gate")
        self.init_run()
        code, output, _ = self.call("gate", "--json")
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(output)["exitCode"], 4)

    def test_subprocess_cli_lifecycle_works_from_nested_fixture_directory(self):
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-m", "agent_harness.cli", "init", "--plan", str(self.plan), "--milestone", "m1", "--json"],
                                cwd=self.repo / "src", env=environment, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        run_id = json.loads(result.stdout)["runId"]
        result = subprocess.run([sys.executable, "-m", "agent_harness.cli", "status", "--json", "--run-id", run_id],
                                cwd=self.repo / "src", env=environment, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["runId"], run_id)

    def test_large_json_status_is_not_cut_mid_object(self):
        self.init_run()
        for _ in range(12):
            self.service.record_decision(self.state.run_id, "note", "a" * 6000)
        code, output, _ = self.call("status", "--json")
        self.assertEqual(code, 0)
        self.assertGreater(len(output), 65536)
        self.assertEqual(len(json.loads(output)["decisions"]), 13)

    def test_human_status_shows_run_identity_and_chinese_state(self):
        self.init_run()
        code, output, _ = self.call("status")
        self.assertEqual(code, 0)
        self.assertIn(self.state.run_id, output)
        self.assertIn("待执行", output)
