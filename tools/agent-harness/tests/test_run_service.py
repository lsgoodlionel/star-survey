"""Lifecycle contracts exercised in disposable linked Git worktrees."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.run_service import RunService, ServiceError
from agent_harness.state import GateStatus, RunStatus, load_state, read_events, save_state_atomic
from agent_harness.doctor import DoctorReport


class WorktreeCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.primary = self.root / "primary"
        self.primary.mkdir()
        self.git("init", "-b", "main", repo=self.primary)
        self.git("config", "user.email", "test@example.invalid", repo=self.primary)
        self.git("config", "user.name", "Harness Test", repo=self.primary)
        self.git("config", "commit.gpgsign", "false", repo=self.primary)
        self.write(self.primary / ".gitignore", "/var/agent-harness/\n")
        self.plan = Path("docs/superpowers/plans/approved.md")
        self.write(self.primary / self.plan,
                   "# Plan\n\n**Status:** approved\n\n"
                   "## Milestone m1: Fixture change\n\nAcceptance: gate passes.\n")
        self.write(self.primary / "src/example.txt", "initial\n")
        self.write(self.primary / "docs/agent/GATE_MATRIX.yaml", json.dumps({
            "version": 1,
            "gates": [{"id": "unit", "command": [sys.executable, "-c", "print('ok')"],
                       "cwd": ".", "timeout_seconds": 10},
                      {"id": "extra", "command": [sys.executable, "-c", "print('extra')"],
                       "cwd": ".", "timeout_seconds": 10}],
            "profiles": {"fixture": {"paths": ["src/**", "docs/**"], "gates": ["unit"]}}
        }))
        self.write(self.primary / "docs/agent/PROTECTED_PATHS.yaml", json.dumps({
            "version": 1, "rules": [
                {"id": "review", "patterns": ["**"], "operations": ["add", "modify", "delete"],
                 "action": "review_required"},
                {"id": "sensitive", "patterns": ["src/auth.txt"], "operations": ["modify"],
                 "action": "approval_required"},
                {"id": "secret", "patterns": ["src/secret.txt"], "operations": ["add"],
                 "action": "deny"}]}))
        self.git("add", ".", repo=self.primary)
        self.git("commit", "-m", "fixture", repo=self.primary)
        self.repo = self.root / "worktree"
        self.git("worktree", "add", "-b", "feat/test", str(self.repo), repo=self.primary)
        self.addCleanup(self.remove_worktree)
        self.service = RunService(self.repo)
        # External tool availability is tested separately; real Git stays live.
        self.doctor_patch = patch("agent_harness.run_service.run_doctor",
                                  return_value=DoctorReport((), (), ()))
        self.doctor_patch.start()
        self.addCleanup(self.doctor_patch.stop)

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def git(self, *args, repo=None):
        return subprocess.run(["git", *args], cwd=repo or self.repo,
                              text=True, capture_output=True, check=True).stdout.strip()

    def remove_worktree(self):
        self.git("worktree", "remove", "--force", str(self.repo), repo=self.primary)
        self.assertFalse(self.repo.exists())
        self.assertNotIn(str(self.repo), self.git("worktree", "list", "--porcelain", repo=self.primary))

    def init_run(self):
        self.state = self.service.init(self.plan, "m1")
        return self.state

    def state_path(self):
        return self.repo / "var/agent-harness/runs" / self.state.run_id / "state.json"

    def reload(self):
        return load_state(self.state_path())

    def changed_commit(self, path="src/example.txt", own=True):
        self.write(self.repo / path, "changed\n")
        # Register the actual modified path before an attributed checkpoint.
        self.service.run_gates(self.state.run_id, ())
        self.git("add", "--", path)
        message = "checkpoint"
        if own:
            message += "\n\nAgent-Run-Id: " + self.state.run_id
        self.git("commit", "-m", message)


class RunServiceTests(WorktreeCase):
    def test_init_binds_plan_digest_milestone_and_git_identity(self):
        state = self.init_run()
        self.assertEqual(state.status, RunStatus.PLANNED)
        self.assertEqual(state.plan_sha256, hashlib.sha256((self.repo / self.plan).read_bytes()).hexdigest())
        self.assertEqual(state.milestone_title, "Fixture change")
        self.assertEqual(state.branch, "feat/test")
        self.assertEqual(state.head_commit, self.git("rev-parse", "HEAD"))
        self.assertEqual(self.reload(), state)

    def test_init_refuses_dirty_primary_detached_unapproved_missing_acceptance(self):
        cases = ["dirty", "primary", "detached", "unapproved", "acceptance", "milestone"]
        for case in cases:
            with self.subTest(case=case):
                service, milestone = self.service, "m1"
                original = (self.repo / self.plan).read_text()
                if case == "dirty":
                    self.write(self.repo / "unknown.txt", "dirty")
                elif case == "primary":
                    service = RunService(self.primary)
                elif case == "detached":
                    self.git("checkout", "--detach")
                elif case == "milestone":
                    milestone = "m2"
                else:
                    text = original.replace("approved", "unapproved") if case == "unapproved" else original.replace("Acceptance: gate passes.", "")
                    self.write(self.repo / self.plan, text)
                    self.git("add", ".")
                    self.git("commit", "-m", case)
                with self.assertRaises(ServiceError):
                    service.init(self.plan, milestone)
                self.assertFalse((self.repo / "var/agent-harness/runs").exists())
                if case == "dirty":
                    (self.repo / "unknown.txt").unlink()
                elif case == "detached":
                    self.git("checkout", "feat/test")
                elif case in ("unapproved", "acceptance"):
                    self.write(self.repo / self.plan, original)
                    self.git("add", ".")
                    self.git("commit", "-m", "restore fixture")

    def test_next_is_read_only_for_every_status(self):
        self.init_run()
        for status in (RunStatus.PLANNED, RunStatus.ACTIVE, RunStatus.VERIFYING,
                       RunStatus.REPAIRING, RunStatus.PAUSED, RunStatus.BLOCKED):
            save_state_atomic(self.state_path(), replace(self.state, status=status))
            before = {p: p.read_bytes() for p in self.state_path().parent.rglob("*") if p.is_file()}
            action = self.service.next_action(self.state.run_id)
            self.assertTrue(action.operation)
            after = {p: p.read_bytes() for p in self.state_path().parent.rglob("*") if p.is_file()}
            self.assertEqual(after, before)

    def test_gate_and_finalize_create_completed_sanitized_history(self):
        self.init_run()
        self.changed_commit()
        state = self.service.run_gates(self.state.run_id, ("extra",))
        self.assertEqual(state.status, RunStatus.VERIFYING)
        self.assertEqual(state.required_gates, ("unit", "extra"))
        self.assertEqual(state.gates["unit"].status, GateStatus.PASSED)
        self.service.record_decision(self.state.run_id, "review", "TOKEN=fake-secret")
        history = self.service.finalize(self.state.run_id)
        self.assertEqual(history, self.repo / "docs/agent/run-history" / (self.state.run_id + ".md"))
        self.assertEqual(self.reload().status, RunStatus.COMPLETED)
        content = history.read_text()
        self.assertIn("completed", content)
        self.assertIn(state.head_commit, content)
        self.assertIn("extra", content)
        self.assertNotIn("fake-secret", content)

    def test_pause_creates_sanitized_history_and_resume_rechecks_doctor(self):
        self.init_run()
        history = self.service.pause(self.state.run_id, "TOKEN=fake-stop")
        self.assertEqual(self.reload().status, RunStatus.PAUSED)
        self.assertNotIn("fake-stop", history.read_text())
        with patch("agent_harness.run_service.run_doctor", return_value=DoctorReport((), (), ({"id": "git", "message": "missing"},))):
            with self.assertRaises(ServiceError) as caught:
                self.service.resume(self.state.run_id)
            self.assertEqual(caught.exception.exit_code, 5)
        resumed = self.service.resume(self.state.run_id)
        self.assertEqual(resumed.status, RunStatus.ACTIVE)
        self.assertTrue(history.exists())

    def test_resume_refuses_plan_branch_dirty_and_unattributed_head_drift(self):
        self.init_run()
        self.service.pause(self.state.run_id, "manual")
        self.write(self.repo / self.plan, "changed plan")
        with self.assertRaises(ServiceError):
            self.service.resume(self.state.run_id)
        self.git("restore", "--", str(self.plan))
        self.git("checkout", "-b", "feat/other")
        with self.assertRaises(ServiceError):
            self.service.resume(self.state.run_id)
        self.git("checkout", "feat/test")
        self.write(self.repo / "unknown.txt", "unknown")
        with self.assertRaises(ServiceError):
            self.service.resume(self.state.run_id)
        (self.repo / "unknown.txt").unlink()
        self.write(self.repo / "src/example.txt", "foreign")
        self.git("add", "src/example.txt")
        self.git("commit", "-m", "foreign")
        with self.assertRaises(ServiceError):
            self.service.resume(self.state.run_id)
        self.assertEqual(self.reload().status, RunStatus.PAUSED)

    def test_owned_head_change_invalidates_gate_evidence_on_resume(self):
        self.init_run()
        self.changed_commit()
        self.service.pause(self.state.run_id, "checkpoint")
        state = self.service.resume(self.state.run_id)
        self.assertEqual(state.head_commit, self.git("rev-parse", "HEAD"))
        self.assertEqual(state.gates["unit"].status, GateStatus.PENDING)

    def test_finalize_refuses_active_stale_missing_evidence_and_dirty_code(self):
        self.init_run()
        with self.assertRaises(ServiceError):
            self.service.finalize(self.state.run_id)
        self.service.run_gates(self.state.run_id, ())
        self.write(self.repo / "src/example.txt", "dirty")
        with self.assertRaises(ServiceError):
            self.service.finalize(self.state.run_id)
        self.git("restore", "src/example.txt")
        evidence = self.repo / self.reload().gates["unit"].evidence_path
        evidence.unlink()
        with self.assertRaises(ServiceError):
            self.service.finalize(self.state.run_id)
        self.assertNotEqual(self.reload().status, RunStatus.COMPLETED)

    def test_finalize_refuses_old_head_even_with_passing_old_gates(self):
        self.init_run()
        self.changed_commit()
        with self.assertRaises(ServiceError):
            self.service.finalize(self.state.run_id)
        self.assertNotEqual(self.reload().status, RunStatus.COMPLETED)

    def test_protected_path_cannot_be_approved_by_record_decision(self):
        self.init_run()
        self.write(self.repo / "src/auth.txt", "auth")
        self.service.record_decision(self.state.run_id, "approval", "approved src/auth.txt")
        with self.assertRaises(ServiceError) as caught:
            self.service.run_gates(self.state.run_id, ())
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertEqual(self.reload().status, RunStatus.PAUSED)
        self.assertTrue((self.repo / "docs/agent/run-history" / (self.state.run_id + ".md")).exists())

    def test_gate_failure_has_exit_four_and_persistent_failure(self):
        self.init_run()
        matrix = json.loads((self.repo / "docs/agent/GATE_MATRIX.yaml").read_text())
        matrix["gates"][0]["command"] = [sys.executable, "-c", "raise SystemExit(7)"]
        # Config is versioned before a new run, never silently changed mid-run.
        self.write(self.repo / "docs/agent/GATE_MATRIX.yaml", json.dumps(matrix))
        self.git("add", "docs/agent/GATE_MATRIX.yaml")
        self.git("commit", "-m", "failure fixture")
        self.init_run()
        with self.assertRaises(ServiceError) as caught:
            self.service.run_gates(self.state.run_id, ())
        self.assertEqual(caught.exception.exit_code, 4)
        self.assertEqual(self.reload().gates["unit"].exit_code, 7)
        self.assertEqual(self.reload().status, RunStatus.REPAIRING)

    def test_completion_save_failure_never_leaves_completed_history(self):
        self.init_run()
        self.service.run_gates(self.state.run_id, ())
        with patch("agent_harness.run_service.save_state_atomic", side_effect=OSError("disk full")):
            with self.assertRaises((OSError, ServiceError)):
                self.service.finalize(self.state.run_id)
        self.assertEqual(self.reload().status, RunStatus.VERIFYING)
        history = self.repo / "docs/agent/run-history" / (self.state.run_id + ".md")
        self.assertFalse(history.exists())

    def test_rejects_run_id_traversal_and_symlink_storage(self):
        with self.assertRaises(ServiceError):
            self.service.next_action("../../outside")
        (self.repo / "var").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ServiceError):
            self.service.init(self.plan, "m1")
        self.assertFalse((self.root / "agent-harness").exists())

    def test_dirty_gate_evidence_cannot_finalize_after_restore_at_same_head(self):
        self.init_run()
        self.write(self.repo / "src/example.txt", "uncommitted code")
        self.service.run_gates(self.state.run_id, ())
        self.git("restore", "src/example.txt")
        with self.assertRaises(ServiceError) as caught:
            self.service.finalize(self.state.run_id)
        self.assertEqual(caught.exception.exit_code, 4)
        self.service.run_gates(self.state.run_id, ())
        self.service.finalize(self.state.run_id)
        self.assertEqual(self.reload().status, RunStatus.COMPLETED)

    def test_gate_detects_content_change_even_when_dirty_path_names_do_not_change(self):
        matrix_path = self.repo / "docs/agent/GATE_MATRIX.yaml"
        matrix = json.loads(matrix_path.read_text())
        matrix["gates"][0]["command"] = [sys.executable, "-c", "from pathlib import Path; Path('src/example.txt').write_text('gate changed code')"]
        self.write(matrix_path, json.dumps(matrix))
        self.git("add", str(matrix_path))
        self.git("commit", "-m", "mutating gate fixture")
        self.init_run()
        self.write(self.repo / "src/example.txt", "before gate")
        with self.assertRaises(ServiceError) as caught:
            self.service.run_gates(self.state.run_id, ())
        self.assertEqual(caught.exception.exit_code, 5)
        self.assertEqual(self.reload().status, RunStatus.PAUSED)

    def test_finalize_rechecks_git_after_evidence_reads(self):
        self.init_run()
        self.service.run_gates(self.state.run_id, ())
        real_verify = self.service._verify_evidence
        def mutate_after_read(*args):
            real_verify(*args)
            self.write(self.repo / "src/example.txt", "concurrent change")
        with patch.object(self.service, "_verify_evidence", side_effect=mutate_after_read):
            with self.assertRaises(ServiceError):
                self.service.finalize(self.state.run_id)
        self.assertNotEqual(self.reload().status, RunStatus.COMPLETED)

    def test_next_reports_gate_after_head_change_without_saving(self):
        self.init_run()
        self.changed_commit()
        before = self.state_path().read_bytes()
        self.assertEqual(self.service.next_action(self.state.run_id).operation, "gate")
        self.assertEqual(self.state_path().read_bytes(), before)

    def test_resume_rejects_modified_own_history_and_policy_drift(self):
        self.init_run()
        history = self.service.pause(self.state.run_id, "manual")
        original = history.read_text()
        history.write_text(original + "external change")
        with self.assertRaises(ServiceError):
            self.service.resume(self.state.run_id)
        history.write_text(original)
        policy = self.repo / "docs/agent/PROTECTED_PATHS.yaml"
        policy.write_text(policy.read_text() + "\n")
        with self.assertRaises(ServiceError):
            self.service.resume(self.state.run_id)
        self.assertEqual(self.reload().status, RunStatus.PAUSED)

    def test_finalize_reinfers_missing_required_gates(self):
        self.init_run()
        self.service.run_gates(self.state.run_id, ())
        state = self.reload()
        save_state_atomic(self.state_path(), replace(state, required_gates=(), gates={}))
        with self.assertRaises(ServiceError) as caught:
            self.service.finalize(self.state.run_id)
        self.assertEqual(caught.exception.exit_code, 4)

    def test_approved_chinese_task_heading_and_fenced_marker_rejection(self):
        plan_path = self.repo / self.plan
        self.write(plan_path, "# Plan\n\n**Status:** 已于 2026-10-10 获用户书面确认\n\n### Task 6: CLI lifecycle\n\nExpected: gates pass\n")
        self.git("add", str(self.plan))
        self.git("commit", "-m", "Chinese plan")
        state = self.service.init(self.plan, "task-6")
        self.assertEqual(state.milestone_title, "CLI lifecycle")
        self.write(plan_path, "# Plan\n\n```text\n**Status:** approved\n```\n\n## Milestone m1: Fixture\n\nAcceptance: ok\n")
        self.git("add", str(self.plan))
        self.git("commit", "-m", "unapproved example")
        with self.assertRaises(ServiceError):
            self.service.init(self.plan, "m1")

    def test_history_publish_failure_rolls_back_completion(self):
        self.init_run()
        self.service.run_gates(self.state.run_id, ())
        with patch.object(self.service, "_atomic_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.finalize(self.state.run_id)
        self.assertEqual(self.reload().status, RunStatus.VERIFYING)

    def test_finalize_refuses_tampered_metadata_and_symlink_evidence(self):
        self.init_run()
        self.service.run_gates(self.state.run_id, ())
        metadata = self.repo / self.reload().gates["unit"].evidence_path
        original = metadata.read_text()
        content = json.loads(original)
        content["exitCode"] = 7
        metadata.write_text(json.dumps(content))
        with self.assertRaises(ServiceError):
            self.service.finalize(self.state.run_id)
        metadata.unlink()
        external = self.root / "outside.json"
        external.write_text(original)
        metadata.symlink_to(external)
        with self.assertRaises(ServiceError):
            self.service.finalize(self.state.run_id)

    def test_resume_edit_checkpoint_gate_finalize_with_uncommitted_history(self):
        self.init_run()
        self.service.pause(self.state.run_id, "review checkpoint")
        self.service.resume(self.state.run_id)
        self.changed_commit()
        state = self.service.run_gates(self.state.run_id, ())
        self.assertEqual(state.head_commit, self.git("rev-parse", "HEAD"))
        self.service.finalize(self.state.run_id)
        self.assertEqual(self.reload().status, RunStatus.COMPLETED)

    def test_trusted_history_never_bypasses_base_ancestry(self):
        self.init_run()
        self.service.pause(self.state.run_id, "manual")
        state = self.reload()
        save_state_atomic(self.state_path(), replace(state, base_commit="0" * 40))
        with self.assertRaises(ServiceError):
            self.service.resume(self.state.run_id)

    def test_protected_pause_history_names_the_refused_path(self):
        self.init_run()
        self.write(self.repo / "src/secret.txt", "fake protected content")
        with self.assertRaises(ServiceError):
            self.service.run_gates(self.state.run_id, ())
        history = self.repo / "docs/agent/run-history" / (self.state.run_id + ".md")
        self.assertIn("src/secret.txt", history.read_text())
        self.assertNotIn("fake protected content", history.read_text())

    def test_interrupted_history_publication_can_recover_without_false_completion(self):
        self.init_run()
        self.service.run_gates(self.state.run_id, ())
        original_write = self.service._atomic_text
        def interrupt_history(path, text):
            if path.parent.name == "run-history":
                raise SystemExit("simulated termination")
            original_write(path, text)
        with patch.object(self.service, "_atomic_text", side_effect=interrupt_history):
            with self.assertRaises(SystemExit):
                self.service.finalize(self.state.run_id)
        with self.assertRaises(ServiceError) as caught:
            self.service.status(self.state.run_id)
        self.assertEqual(caught.exception.exit_code, 5)
        history = self.service.finalize(self.state.run_id)
        self.assertEqual(self.service.status(self.state.run_id).status, RunStatus.COMPLETED)
        content = history.read_bytes()
        self.assertEqual(self.service.finalize(self.state.run_id).read_bytes(), content)

    def test_completed_history_has_gate_command_cwd_and_timestamps(self):
        self.init_run()
        state = self.service.run_gates(self.state.run_id, ())
        history = self.service.finalize(self.state.run_id).read_text()
        self.assertIn("print('ok')", history)
        self.assertIn('cwd=.', history)
        self.assertIn(state.gates["unit"].started_at.isoformat().replace("+00:00", "Z"), history)
        self.assertIn(state.gates["unit"].ended_at.isoformat().replace("+00:00", "Z"), history)

    def test_init_refuses_unignored_raw_storage(self):
        self.write(self.repo / ".gitignore", "")
        self.git("add", ".gitignore")
        self.git("commit", "-m", "unsafe storage fixture")
        with self.assertRaises(ServiceError) as caught:
            self.init_run()
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertFalse((self.repo / "var/agent-harness/runs").exists())

    def test_resume_owned_descendant_records_reconciled_event(self):
        self.init_run()
        self.write(self.repo / "src/example.txt", "checkpoint before resume")
        self.service.run_gates(self.state.run_id, ())
        self.service.pause(self.state.run_id, "checkpoint pending")
        self.git("add", "src/example.txt")
        self.git("commit", "-m", "owned\n\nAgent-Run-Id: " + self.state.run_id)
        resumed = self.service.resume(self.state.run_id)
        self.assertEqual(resumed.gates["unit"].status, GateStatus.PENDING)
        events = read_events(self.state_path().parent / "events.jsonl")
        self.assertIn("reconciled", [event["type"] for event in events])
