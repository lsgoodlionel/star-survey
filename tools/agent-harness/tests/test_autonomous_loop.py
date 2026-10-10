"""Fake Codex with real worktree, disk state, policy, Gates and history."""

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_run_service import WorktreeCase
from agent_harness.state import RunStatus, read_events, save_state_atomic


class LoopCase(WorktreeCase):
    def setUp(self):
        super().setUp()
        self.write(self.repo / self.plan, "# Plan\n\n**Status:** approved\n\n## Milestone m1: Fixture change\n\n"
                   "**Files:**\n- Modify: `src/example.txt`\n\nAcceptance: unit passes.\n")
        self.write(self.repo / "src/auth.txt", "initial")
        self.write(self.repo / "gate.py", "from pathlib import Path\nimport sys\nx=Path('src/example.txt').read_text().strip()\nprint('Error: '+x if x != 'fixed' else 'ok')\nsys.exit(0 if x == 'fixed' else 1)\n")
        matrix = json.loads((self.repo / "docs/agent/GATE_MATRIX.yaml").read_text())
        matrix["gates"][0]["command"] = [sys.executable, "gate.py"]
        self.write(self.repo / "docs/agent/GATE_MATRIX.yaml", json.dumps(matrix))
        policy_path = self.repo / "docs/agent/PROTECTED_PATHS.yaml"
        policy = json.loads(policy_path.read_text())
        policy["rules"] = [r for r in policy["rules"] if r["id"] != "review"]
        self.write(policy_path, json.dumps(policy))
        self.git("add", ".")
        self.git("commit", "-m", "loop fixture")
        self.init_run()
        self.prompts = []
        self.session = "11111111-1111-4111-8111-111111111111"

    def fake(self, changes, *, commit=False, needs_human=False, mutation=None):
        from agent_harness.codex_adapter import CodexResult
        sequence = iter(changes)
        def execute(command, timeout, event_log):
            self.prompts.append(command[-1])
            value = next(sequence)
            if value is not None:
                self.write(self.repo / "src/example.txt", value)
            if mutation:
                mutation()
            if commit:
                self.git("add", "src/example.txt")
                self.git("commit", "-m", "fix\n\nAgent-Run-Id: " + self.state.run_id)
            return CodexResult(status="completed", summary="done", changed_paths=("src/example.txt",),
                               tests_requested=("unit",), needs_human=needs_human, session_id=self.session)
        return execute

    def run_loop(self, fake, cycles=10):
        with patch("agent_harness.run_service.run_codex", side_effect=fake):
            return self.service.run_autonomous(self.state.run_id, cycles)

    def test_changed_checkpoint_runs_current_head_gates_before_completion(self):
        original = self.service.run_gates
        def independently_reviewed_gates(run_id, extra):
            result = original(run_id, extra)
            self.service.record_review(run_id, "independent-reviewer", self.review_report())
            return result
        with patch.object(self.service, "run_gates", side_effect=independently_reviewed_gates):
            result = self.run_loop(self.fake(["fixed"], commit=True))
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.head_commit, self.git("rev-parse", "HEAD"))
        self.assertEqual(result.gates["unit"].head_commit, result.head_commit)
        events = read_events(self.state_path().parent / "events.jsonl")
        self.assertLess(next(i for i,e in enumerate(events) if e["type"] == "codex_finished"),
                        next(i for i,e in enumerate(events) if e["type"] == "gate_finished"))

    def test_gate_failure_produces_bounded_focused_repair_prompt(self):
        result = self.run_loop(self.fake(["broken", "fixed"], commit=False), cycles=2)
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(result.attempts.total, 1)
        self.assertIn("Error: broken", self.prompts[1])
        self.assertIn("src/example.txt", self.prompts[1])
        self.assertIn("4", self.prompts[1])
        self.assertLessEqual(len(self.prompts[1]), 16384)
        self.assertIn("Acceptance: unit passes.", self.prompts[0])

    def test_same_failure_stops_on_three(self):
        # Whitespace changes advance the diff while preserving the failure identity.
        result = self.run_loop(self.fake(["broken", "broken\n", "broken\n\n", "never"]))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(result.attempts.total, 3)
        self.assertEqual(len(self.prompts), 3)
        self.assertIn("same_failure_limit", result.decisions[-2]["summary"])

    def test_total_failure_budget_stops_on_five(self):
        result = self.run_loop(self.fake(["err1", "err2", "err3", "err4", "err5", "never"]))
        self.assertEqual(result.attempts.total, 5)
        self.assertEqual(len(self.prompts), 5)
        self.assertEqual(result.status, RunStatus.PAUSED)

    def test_two_no_progress_cycles_pause(self):
        result = self.run_loop(self.fake(["broken", None, "never"]))
        self.assertEqual(result.attempts.total, 2)
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(result.status, RunStatus.PAUSED)

    def test_empty_checkpoints_do_not_count_as_code_progress(self):
        from agent_harness.codex_adapter import CodexResult
        def execute(command, timeout, event_log):
            self.prompts.append(command[-1])
            self.write(self.repo / "src/example.txt", "broken")
            self.git("add", "src/example.txt")
            self.git("commit", "--allow-empty", "-m", "checkpoint\n\nAgent-Run-Id: " + self.state.run_id)
            return CodexResult("repairing", "fix", ("src/example.txt",), ("unit",), False, self.session)
        result = self.run_loop(execute)
        self.assertEqual(result.attempts.total, 2)
        self.assertEqual(len(self.prompts), 2)

    def test_approval_required_scope_pauses_before_adapter(self):
        text = (self.repo / self.plan).read_text().replace("src/example.txt", "src/auth.txt")
        self.write(self.repo / self.plan, text)
        self.git("add", str(self.plan))
        self.git("commit", "-m", "protected scope")
        self.init_run()
        result = self.run_loop(self.fake(["never"]))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertFalse(self.prompts)

    def test_approval_required_change_pauses_before_gate(self):
        result = self.run_loop(self.fake([None], mutation=lambda: self.write(self.repo / "src/auth.txt", "modified")))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertFalse(result.gates)
        self.assertEqual(result.attempts.total, 0)

    def test_passing_dirty_gate_cannot_complete(self):
        result = self.run_loop(self.fake(["fixed"]))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(result.gates["unit"].status.value, "passed")
        self.assertEqual(len(self.prompts), 1)

    def test_unknown_paths_and_preexisting_drift_pause_without_execution(self):
        self.write(self.repo / "src/other.txt", "foreign")
        result = self.run_loop(self.fake(["never"]))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(self.prompts, [])

    def test_adapter_response_is_not_scope_authority(self):
        result = self.run_loop(self.fake([None], mutation=lambda: self.write(self.repo / "src/other.txt", "foreign")))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertFalse(result.gates)

    def test_plan_drift_is_reread_after_adapter(self):
        result = self.run_loop(self.fake(["fixed"], mutation=lambda: self.write(self.repo / self.plan, "changed")))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertFalse(result.gates)

    def test_human_request_and_adapter_failure_pause_without_gates(self):
        result = self.run_loop(self.fake([None], needs_human=True))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertFalse(result.gates)

    def test_typed_adapter_failure_is_persisted_without_gate_or_raw_logs(self):
        from agent_harness.codex_adapter import CodexResult, CodexFailure
        result = self.run_loop(lambda *args: CodexResult(failure=CodexFailure.MALFORMED_JSONL))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertFalse(result.gates)
        history = (self.repo / "docs/agent/run-history" / (self.state.run_id + ".md")).read_text()
        self.assertIn("malformed_jsonl", history)

    def test_state_mutation_during_adapter_cannot_replace_bound_scope(self):
        def mutate():
            save_state_atomic(self.state_path(), replace(self.reload(), milestone_title="tampered"))
        result = self.run_loop(self.fake(["fixed"], mutation=mutate))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(result.milestone_title, "Fixture change")
        self.assertFalse(result.gates)

    def test_git_branch_drift_after_adapter_pauses_before_gate(self):
        result = self.run_loop(self.fake([None], mutation=lambda: self.git("checkout", "-b", "feat/foreign")))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertFalse(result.gates)

    def test_unattributed_head_change_pauses_before_gate(self):
        def mutate():
            self.git("add", "src/example.txt")
            self.git("commit", "-m", "foreign")
        result = self.run_loop(self.fake(["fixed"], mutation=mutate))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertFalse(result.gates)

    def test_milestone_without_explicit_file_scope_never_executes(self):
        self.write(self.repo / self.plan, "# Plan\n\nStatus: approved\n\n## Milestone m1: Fixture change\n\nAcceptance: passes\n")
        self.git("add", str(self.plan))
        self.git("commit", "-m", "scope missing")
        self.init_run()
        result = self.run_loop(self.fake(["never"]))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(self.prompts, [])

    def test_budget_cannot_be_forged_with_record_decision(self):
        from agent_harness.run_service import ServiceError
        for kind in ("failure_cycle", "codex_session", "autonomous_snapshot"):
            with self.subTest(kind=kind), self.assertRaises(ServiceError):
                self.service.record_decision(self.state.run_id, kind, "fake")

    def test_gate_checkpoint_invalidates_preceding_success_evidence(self):
        # The Gate creates an attributed empty checkpoint once, during verification.
        original = self.service.run_gates
        def gates(run_id, extra):
            result = original(run_id, extra)
            self.git("commit", "--allow-empty", "-m", "checkpoint\n\nAgent-Run-Id: " + run_id)
            return result
        with patch.object(self.service, "run_gates", side_effect=gates):
            result = self.run_loop(self.fake(["fixed"], commit=True))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertNotEqual(result.gates["unit"].status.value, "passed")

    def test_review_required_checkpoint_needs_independent_evidence(self):
        policy_path = self.repo / "docs/agent/PROTECTED_PATHS.yaml"
        policy = json.loads(policy_path.read_text())
        policy["rules"].append({"id": "review", "patterns": ["src/example.txt"], "operations": ["modify"], "action": "review_required"})
        self.write(policy_path, json.dumps(policy))
        self.git("add", str(policy_path))
        self.git("commit", "-m", "review policy")
        self.init_run()
        result = self.run_loop(self.fake(["fixed"], commit=True))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(result.gates["unit"].status.value, "passed")

    def test_state_content_is_reread_between_cycles(self):
        original = self.service._save
        def save(state, event_type, payload=None):
            result = original(state, event_type, payload)
            if event_type == "cycle_finished":
                self.write(self.repo / "src/example.txt", "foreign edit")
            return result
        with patch.object(self.service, "_save", side_effect=save):
            result = self.run_loop(self.fake(["broken", "never"]))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(len(self.prompts), 1)

    def test_resume_does_not_reset_failure_budget(self):
        result = self.run_loop(self.fake(["broken", "broken\n", "broken\n\n"]))
        self.git("add", "src/example.txt")
        self.git("commit", "-m", "checkpoint\n\nAgent-Run-Id: " + result.run_id)
        self.service.resume(result.run_id)
        self.prompts.clear()
        result = self.run_loop(self.fake(["never"]))
        self.assertEqual(result.status, RunStatus.PAUSED)
        self.assertEqual(result.attempts.total, 3)
        self.assertFalse(self.prompts)

    def test_cli_help_and_fake_run_have_stable_exit_and_single_json(self):
        from agent_harness.cli import main
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["run-codex", "--help"]), 0)
        self.assertIn("--max-cycles", output.getvalue())
        output = io.StringIO()
        with patch("agent_harness.run_service.run_codex", side_effect=self.fake(["fixed"])), redirect_stdout(output):
            code = main(["--repo", str(self.repo), "run-codex", "--run-id", self.state.run_id, "--max-cycles", "1", "--json"])
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(output.getvalue())["status"], "paused")
