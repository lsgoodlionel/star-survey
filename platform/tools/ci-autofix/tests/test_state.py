import unittest
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("ci_autofix_state", Path(__file__).resolve().parents[1] / "state.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
attempt, plan = module.attempt, module.plan


class StateTests(unittest.TestCase):
    def setUp(self):
        self.event = {"repository": {"id": 1}, "workflow_run": {
            "name": "MJY 平台 - 质量闸门", "conclusion": "failure", "event": "pull_request", "head_repository": {"id": 1},
            "pull_requests": [{"number": 7}], "head_branch": "feature", "head_sha": "abc", "id": 99}}

    def test_three_attempt_limit(self):
        self.assertEqual(plan(self.event, "ordinary")["next_attempt"], 1)
        self.assertEqual(plan(self.event, "fix [codex-fix-attempt: 2]")["next_attempt"], 3)
        self.assertEqual(plan(self.event, "fix [codex-fix-attempt: 3]")["decision"], "report")

    def test_fork_is_skipped(self):
        self.event["workflow_run"]["head_repository"]["id"] = 2
        self.assertEqual(plan(self.event, "")["decision"], "skip")

    def test_non_pr_is_skipped(self):
        self.event["workflow_run"]["event"] = "push"
        self.assertEqual(plan(self.event, "")["decision"], "skip")

    def test_marker(self):
        self.assertEqual(attempt("fix [codex-fix-attempt: 10]"), 10)
