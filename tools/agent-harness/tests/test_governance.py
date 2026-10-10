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
PLAN = REPO / "docs/superpowers/plans/2026-10-10-autonomous-engineering-control-plane.md"
README = REPO / "tools/agent-harness/README.md"


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

    def test_checkout_fetches_history_for_review_ancestry_validation(self):
        checkout = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
        blocks = self.text.split("- uses: " + checkout)[1:]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            self.assertRegex(block, r"(?m)^\s+with:\s*\n\s+fetch-depth:\s*0\s*$")

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
        self.assertRegex(self.text, r"docker\s+pull\s+python:3\.11[^\s]*@sha256:[0-9a-f]{64}")
        self.assertIn("--fail-on-skip", self.text)

    def test_workflow_passes_explicit_safe_linux_fixture_parameters(self):
        image = ("python:3.11-slim@sha256:"
                 "e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce")
        self.assertIn("AGENT_HARNESS_CI: '1'", self.text)
        self.assertNotIn("AGENT_HARNESS_LINUX_IMAGE", self.text)
        self.assertIn("--ci", self.text)
        self.assertIn("--linux-image " + image, self.text)
        self.assertIn("--docker /usr/bin/docker", self.text)

    def test_workflow_hardens_setup_python_directory_chain_before_wrapper(self):
        harden = (
            'chmod go-w /opt/hostedtoolcache/Python "${pythonLocation%/*}" '
            '"$pythonLocation" "$pythonLocation/bin" "$pythonLocation/bin/python3.11"'
        )
        self.assertIn(harden, self.text)
        self.assertEqual(self.text.count(harden), 2)
        self.assertLess(self.text.index(harden),
                        self.text.index("scripts/agent-harness --python --version"))

    def test_host_config_owns_portability_and_delivery_contracts(self):
        document = json.loads(HOST_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(document["version"], 1)
        self.assertIn("activePlan", document)
        self.assertIn("deliveryManifest", document)
        self.assertIn("secretPolicy", document)
        self.assertIn("fixture", document)
        self.assertIn("documentation", document)
        self.assertIn(".env.example", document["secretPolicy"]["allowlist"])

    def test_independent_review_is_complete_only_with_three_controller_approvals(self):
        manifest = json.loads((REPO / "docs/agent/HARNESS_DELIVERY.json").read_text(
            encoding="utf-8"))
        self.assertEqual(manifest["externalSync"]["independentReview"], "complete")
        self.assertEqual(manifest["deliveryStatus"], "locally_reviewed_sync_pending")
        evidence = manifest["reviewEvidence"]
        self.assertEqual(evidence["requiredReviewers"],
                         ["security", "dx_ci", "whole_branch"])
        self.assertEqual(len(evidence["reports"]), 3)
        self.assertEqual(len({report["path"] for report in evidence["reports"]}), 3)
        self.assertEqual(len({report["sha256"] for report in evidence["reports"]}), 3)
        self.assertTrue(all(report["specVerdict"] == "APPROVED"
                            and report["codeQualityVerdict"] == "APPROVED"
                            for report in evidence["reports"]))
        task8 = PLAN.read_text(encoding="utf-8").split("### Task 8:", 1)[1]
        self.assertIn("- [x] **Step 7: Perform independent review", task8)

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

    def test_ci_test_runner_partitions_unit_and_integration_without_skips(self):
        runner = REPO / "tools/agent-harness/run_tests.py"
        with tempfile.TemporaryDirectory() as directory:
            test_file = Path(directory) / "test_partition.py"
            test_file.write_text(
                "import unittest\n"
                "class Partition(unittest.TestCase):\n"
                "    def test_unit(self): pass\n"
                "    @unittest.skip('integration unavailable')\n"
                "    def test_linux_wrapper_fixture(self): pass\n",
                encoding="utf-8",
            )
            unit = subprocess.run([
                sys.executable, str(runner), "--start-directory", directory,
                "--exclude-substring", "linux_wrapper", "--fail-on-skip",
            ], text=True, capture_output=True, check=False)
            integration = subprocess.run([
                sys.executable, str(runner), "--start-directory", directory,
                "--include-substring", "linux_wrapper", "--fail-on-skip",
            ], text=True, capture_output=True, check=False)
        self.assertEqual(unit.returncode, 0, unit.stdout + unit.stderr)
        self.assertEqual(integration.returncode, 2, integration.stdout + integration.stderr)

    def test_ci_test_runner_transmits_only_validated_linux_fixture_parameters(self):
        runner = REPO / "tools/agent-harness/run_tests.py"
        image = ("python:3.11-slim@sha256:"
                 "e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce")
        with tempfile.TemporaryDirectory() as directory:
            test_file = Path(directory) / "test_parameters.py"
            test_file.write_text(
                "import os,unittest\n"
                "class Parameters(unittest.TestCase):\n"
                " def test_values(self):\n"
                "  self.assertEqual(os.environ['HARNESS_TEST_CI_MODE'], '1')\n"
                "  self.assertEqual(os.environ['HARNESS_TEST_LINUX_IMAGE'], " + repr(image) + ")\n"
                "  self.assertEqual(os.environ['HARNESS_TEST_DOCKER'], '/usr/bin/docker')\n",
                encoding="utf-8",
            )
            valid = subprocess.run([
                sys.executable, str(runner), "--start-directory", directory, "--ci",
                "--linux-image", image, "--docker", "/usr/bin/docker",
            ], text=True, capture_output=True, check=False)
            bad_image = subprocess.run([
                sys.executable, str(runner), "--start-directory", directory,
                "--linux-image", "latest", "--docker", "/usr/bin/docker",
            ], text=True, capture_output=True, check=False)
            bad_docker = subprocess.run([
                sys.executable, str(runner), "--start-directory", directory,
                "--linux-image", image, "--docker", "docker",
            ], text=True, capture_output=True, check=False)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertEqual(bad_image.returncode, 2, bad_image.stdout + bad_image.stderr)
        self.assertEqual(bad_docker.returncode, 2, bad_docker.stdout + bad_docker.stderr)

    def test_workflow_separates_fake_tool_units_from_real_linux_integration(self):
        self.assertIn("agent-governance-unit:", self.text)
        self.assertIn("agent-governance-integration:", self.text)
        self.assertIn("needs: agent-governance-unit", self.text)
        self.assertIn("--exclude-substring linux_wrapper", self.text)
        self.assertIn("--include-substring linux_wrapper", self.text)
        unit, integration = self.text.split("agent-governance-integration:", 1)
        self.assertNotIn("docker pull", unit)
        self.assertIn("docker pull", integration)

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

    def test_plan_readme_and_workflow_use_the_controlled_python_entry(self):
        task8 = PLAN.read_text(encoding="utf-8").split("### Task 8:", 1)[1]
        readme = README.read_text(encoding="utf-8")
        self.assertNotRegex(task8, r"(?m)^Run: `python3(?:\.11)?\s")
        self.assertNotRegex(readme, r"(?m)^python3(?:\.11)?\s+tools/agent-harness")
        for command in ("tools/agent-harness/drills/run_drills.py",
                        "platform/tools/productization/render_capabilities.py --check"):
            self.assertIn("scripts/agent-harness --python " + command, task8 + readme + self.text)


if __name__ == "__main__":
    unittest.main()
