"""Governance workflow policy and delivery-drift contracts."""

from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github/workflows/agent-governance.yml"
HOST_CONFIG = REPO / "docs/agent/HARNESS_HOST.json"


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

    def test_workflow_uses_one_controlled_python_entry_and_prepares_linux_fixture(self):
        run_lines = re.findall(r"(?m)^\s+run:\s*([^>].*)$", self.text)
        python_lines = [line for line in run_lines if "agent-harness" in line or "run_drills.py" in line]
        self.assertTrue(python_lines)
        self.assertTrue(all("scripts/agent-harness --python" in line for line in python_lines))
        self.assertNotRegex(self.text, r"(?m)^\s+run:\s*python3(?:\.11)?\s")
        self.assertIn("AGENT_HARNESS_CI: '1'", self.text)
        self.assertRegex(self.text, r"docker\s+pull\s+python:3\.11[^\s]*@sha256:[0-9a-f]{64}")
        self.assertIn("--fail-on-skip", self.text)

    def test_host_config_owns_portability_and_delivery_contracts(self):
        document = json.loads(HOST_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 1)
        self.assertIn("activePlan", document)
        self.assertIn("deliveryManifest", document)
        self.assertIn("secretPolicy", document)
        self.assertIn("fixture", document)
        self.assertIn("documentation", document)
        self.assertIn(".env.example", document["secretPolicy"]["allowlist"])

    def test_ci_test_runner_fails_when_any_test_is_skipped(self):
        runner = REPO / "tools/agent-harness/run_tests.py"
        with tempfile.TemporaryDirectory() as directory:
            test_file = Path(directory) / "test_skip.py"
            test_file.write_text(
                "import unittest\n"
                "class Skip(unittest.TestCase):\n"
                "    @unittest.skip('fixture missing')\n"
                "    def test_skip(self): pass\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(runner), "--start-directory", directory,
                 "--pattern", "test_*.py", "--fail-on-skip"],
                text=True, capture_output=True, check=False,
            )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unexpected skips: 1", result.stderr)

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
