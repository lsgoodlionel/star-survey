"""End-to-end fault drills against the real Harness boundaries."""

import importlib.util
import io
import json
import os
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
        custom = Path("config/custom-host.json")
        with patch.object(self.runner, "ensure_no_production_secret_paths") as guard, \
                patch.object(self.runner, "run_real_codex_smoke", return_value=result) as real, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.runner.main([
                "--allow-real-codex", "--host-config", custom.as_posix()
            ]), 0)
        real.assert_called_once_with(REPO, custom)
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
                self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())
            subprocess.run(["git", "add", ".aws/credentials"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "commit", "-m", "bad secret fixture"], cwd=repo,
                           check=True, capture_output=True)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())

    @staticmethod
    def secret_policy():
        return {
            "patterns": [".env", ".env.*", "**/.env", "**/.env.*", ".aws", ".aws/**",
                         "**/.aws", "**/.aws/**", "**/secrets/**", "**/*.key"],
            "allowlist": [".env.example"],
        }

    def test_secret_inventory_covers_ignored_directories_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            (repo / ".gitignore").write_text("/var/\n", encoding="utf-8")
            secret = repo / "var/secrets/prod.key"
            secret.parent.mkdir(parents=True)
            secret.write_text("fixture\n", encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())
            secret.unlink()
            secret.parent.rmdir()
            (repo / "var").rmdir()
            (repo / ".aws").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())

    def test_secret_templates_are_allowed_only_by_host_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            (repo / ".env.example").write_text("TOKEN=replace-me\n", encoding="utf-8")
            self.runner.ensure_no_production_secret_paths(repo, self.secret_policy())
            policy = {**self.secret_policy(), "allowlist": []}
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.ensure_no_production_secret_paths(repo, policy)

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

    def test_forbidden_scan_covers_yaml_and_codex_equivalent_overrides_only(self):
        dangerous = (
            "codex exec --sandbox danger-full-access task",
            "codex exec --add-dir /tmp task",
            "codex exec --config approval_policy=never task",
            "codex exec -c sandbox_mode=danger-full-access task",
            "codex exec --dangerously-bypass-hook-trust task",
        )
        for index, command in enumerate(dangerous):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                matrix = repo / "docs/agent/GATE_MATRIX.yaml"
                matrix.parent.mkdir(parents=True)
                matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
                workflow = repo / f".github/workflows/bad-{index}.yaml"
                workflow.parent.mkdir(parents=True)
                workflow.write_text("jobs:\n  bad:\n    steps:\n      - run: " + command + "\n",
                                    encoding="utf-8")
                with self.assertRaises(self.runner.DrillRefused):
                    self.runner.check_forbidden_options(repo)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            matrix = repo / "docs/agent/GATE_MATRIX.yaml"
            matrix.parent.mkdir(parents=True)
            matrix.write_text('{"version":1,"gates":[],"profiles":{}}', encoding="utf-8")
            workflow = repo / ".github/workflows/benign.yaml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("jobs:\n  ok:\n    steps:\n      - run: docker --config /tmp build .\n",
                                encoding="utf-8")
            self.runner.check_forbidden_options(repo)

    def test_smoke_inputs_refuse_symlinks_without_touching_external_files(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            repo = Path(directory)
            external = Path(outside) / "sentinel.txt"
            external.write_text("keep\n", encoding="utf-8")
            plan = repo / "plan.md"
            plan.symlink_to(external)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.atomic_create(repo, plan, "replacement\n")
            self.assertEqual(external.read_text(encoding="utf-8"), "keep\n")
            plan.unlink()
            fixture = repo / "fixture.txt"
            fixture.symlink_to(external)
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.atomic_create(repo, fixture, "replacement\n")
            self.assertEqual(external.read_text(encoding="utf-8"), "keep\n")

    def test_completed_smoke_requires_exact_fixture_and_nonempty_head_bound_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.txt"
            fixture.write_text("baseline\nwrong\n", encoding="utf-8")
            history = root / "history.md"
            history.write_text("sanitized\n", encoding="utf-8")
            state = self.runner._state(root, self.runner.RunStatus.COMPLETED)
            with self.assertRaises(AssertionError):
                self.runner.validate_real_smoke_terminal(
                    root, state, ("fixture.txt",), fixture, history,
                    "baseline\nreal-codex-smoke-ok\n",
                )

    def test_paused_smoke_rejects_unsanitized_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture.txt"
            fixture.write_text("baseline\n", encoding="utf-8")
            history = root / "history.md"
            history.write_text("Authorization: Bearer fake-secret\n", encoding="utf-8")
            state = self.runner._state(root, self.runner.RunStatus.PAUSED)
            with self.assertRaises(AssertionError):
                self.runner.validate_real_smoke_terminal(root, state, (), fixture, history,
                                                        "baseline\nreal-codex-smoke-ok\n")

    def test_cleanup_failure_is_typed_and_never_claims_completion(self):
        failed = subprocess.CompletedProcess([], 1, "", "failed")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(self.runner, "_run", return_value=failed):
            root = Path(directory)
            worktree = root / "worktree"
            worktree.mkdir()
            errors = self.runner.cleanup_real_smoke(root, worktree, "drill/test")
        self.assertTrue(errors)
        self.assertNotIn("removed", " ".join(errors).lower())

    def test_success_drill_runs_service_adapter_gate_review_finalize_and_history(self):
        result = self.runner.run_named_drill("success")
        self.assertEqual(result.status, "passed", result.detail)
        detail = json.loads(result.detail)
        self.assertEqual(detail["flow"], ["init", "fake-adapter", "gate", "review", "finalize", "history"])
        self.assertEqual(detail["status"], "completed")

    def test_old_python_gets_concise_version_diagnostic_before_core_imports(self):
        candidate = Path("/usr/bin/python3")
        if not candidate.is_file():
            self.skipTest("system Python is unavailable")
        version = subprocess.run([str(candidate), "--version"], text=True, capture_output=True,
                                 check=False).stdout
        if not version.startswith(("Python 3.8", "Python 3.9", "Python 3.10")):
            self.skipTest("system Python already satisfies the 3.11 floor")
        result = subprocess.run([str(candidate), str(RUNNER), "--help"], text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Python 3.11", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_manifest_drives_plan_ledger_and_host_document_status(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo,
                           check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo,
                           check=True, capture_output=True)
            plan = repo / "plans/custom.md"
            plan.parent.mkdir(parents=True)
            plan.write_text("# Plan\n\n### Task 1: Base\n- [x] done\n\n"
                            "### Task 8: Delivery\n- [ ] sync\n", encoding="utf-8")
            marker = "<!-- harness-delivery-status: local_validated_sync_pending -->"
            for relative in ("README.md", "AGENTS.md", "docs/AUTONOMY.md", "docs/MEMORY.md"):
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(marker + "\n", encoding="utf-8")
            ledger = repo / ".sdd/progress.md"
            ledger.parent.mkdir()
            ledger.write_text("Task 1: complete\nTask 8: in_progress\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "host fixture"], cwd=repo, check=True,
                           capture_output=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                                  text=True, capture_output=True).stdout.strip()
            manifest = repo / "docs/DELIVERY.json"
            manifest.write_text(json.dumps({
                "version": 1,
                "planPath": "plans/custom.md",
                "implementationCommit": head,
                "evidence": {"headCommit": head, "status": "passed", "commands": ["unit"]},
                "taskSteps": {"1": {"completed": [1], "pending": []},
                              "8": {"completed": [], "pending": [1]}},
                "deliveryStatus": "local_validated_sync_pending",
                "externalSync": {"independentReview": "pending", "github": "pending",
                                 "obsidian": "pending"},
            }, sort_keys=True), encoding="utf-8")
            config = repo / "docs/HOST.json"
            config.write_text(json.dumps({
                "version": 1,
                "activePlan": "plans/custom.md",
                "deliveryManifest": "docs/DELIVERY.json",
                "ledger": ".sdd/progress.md",
                "secretPolicy": self.secret_policy(),
                "fixture": {"path": "fixture.txt", "planPath": "plans/smoke.md",
                            "baseline": "baseline\\n", "expected": "baseline\\nok\\n"},
                "documentation": {"statusMarkerPaths": ["README.md", "AGENTS.md",
                                                          "docs/AUTONOMY.md", "docs/MEMORY.md"]},
            }, sort_keys=True), encoding="utf-8")
            host = self.runner.load_host_config(repo, config.relative_to(repo))
            self.runner.check_docs(repo, host)
            plan.write_text(plan.read_text(encoding="utf-8").replace("- [ ] sync", "- [x] sync"),
                            encoding="utf-8")
            with self.assertRaises(self.runner.DrillRefused):
                self.runner.check_docs(repo, host)

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
