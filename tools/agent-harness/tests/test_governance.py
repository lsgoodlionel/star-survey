"""Governance workflow policy and delivery-drift contracts."""

from pathlib import Path
import re
import unittest


REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github/workflows/agent-governance.yml"


class GovernanceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_read_only_on_ubuntu_with_python_311(self):
        self.assertRegex(self.text, r"(?m)^permissions:\s*\n\s+contents:\s*read\s*$")
        self.assertRegex(self.text, r"(?m)^\s*runs-on:\s*ubuntu-(?:22\.04|24\.04|latest)\s*$")
        self.assertRegex(self.text, r"(?m)^\s*python-version:\s*['\"]3\.11['\"]\s*$")
        self.assertNotRegex(self.text, r"(?mi)^\s*[a-z-]+:\s*write\s*$")

    def test_every_action_is_pinned_to_a_full_commit(self):
        uses = re.findall(r"(?m)^\s*-?\s*uses:\s*([^\s#]+)", self.text)
        self.assertGreaterEqual(len(uses), 2)
        for action in uses:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@\s]+@[0-9a-f]{40}$")

    def test_workflow_runs_all_governance_checks(self):
        required = (
            "test_config.py",
            "unittest discover -s tools/agent-harness/tests",
            "tools/agent-harness/drills/run_drills.py",
            "--check-forbidden-options",
            "--check-docs",
        )
        for command in required:
            with self.subTest(command=command):
                self.assertIn(command, self.text)

    def test_workflow_cannot_merge_release_or_deploy(self):
        forbidden = (
            r"\bgh\s+pr\s+merge\b",
            r"\bgh\s+release\s+create\b",
            r"\bgit\s+push\b",
            r"\bdeploy(?:ment)?\b",
            r"\bauto-?merge\b",
            r"pull-requests:\s*write",
            r"packages:\s*write",
            r"deployments:\s*write",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(self.text.lower(), pattern)


if __name__ == "__main__":
    unittest.main()
