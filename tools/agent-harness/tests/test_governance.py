"""Governance workflow policy and delivery-drift contracts."""

from pathlib import Path
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch



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

    def test_workflows_cancel_only_obsolete_pull_request_runs(self):
        platform = (REPO / ".github/workflows/platform-quality.yml").read_text(
            encoding="utf-8")
        for workflow in (self.text, platform):
            self.assertIn("concurrency:", workflow)
            self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
                          workflow)

    def test_workflows_emit_and_upload_sanitized_gate_summary(self):
        platform = (REPO / ".github/workflows/platform-quality.yml").read_text(
            encoding="utf-8")
        for workflow in (self.text, platform):
            self.assertIn("agent_harness.github_evidence", workflow)
            self.assertIn("gate-summaries/", workflow)
            self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", workflow)
            self.assertNotIn("stdout.log", workflow)
        wrapper = (REPO / "scripts/agent-harness").read_text(encoding="utf-8")
        self.assertIn('["-m", "agent_harness.github_evidence"]', wrapper)

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
            self.assertIn("fetch-depth: 0", block)

    def test_pull_request_checks_out_head_sha(self):
        checkout = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
        blocks = self.text.split("- uses: " + checkout)[1:]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            self.assertIn("${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}", block)
            self.assertIn("github.sha", block)
            self.assertIn("fetch-depth: 0", block)

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
        self.assertEqual(document["historyBoundary"]["allowedShallowCommits"],
                         ["4c20c68033c8e37140f26af80a65f659b40f8a45"])
        self.assertEqual(document["historyBoundary"]["trustedHeadRefs"],
                         ["refs/remotes/limesurvey-fork/master"])
        self.assertIn("fixture", document)
        self.assertIn("documentation", document)
        self.assertIn("docs/agent/MEMORY_PROJECTIONS.json",
                      document["deliverySyncPaths"])
        self.assertIn(".env.example", document["secretPolicy"]["allowlist"])

    def test_delivery_ledger_is_available_in_a_fresh_checkout(self):
        document = json.loads(HOST_CONFIG.read_text(encoding="utf-8"))
        ledger = REPO / document["ledger"]
        self.assertTrue(ledger.is_file())
        self.assertTrue(ledger.resolve().is_relative_to(REPO.resolve()))
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", document["ledger"]],
            cwd=REPO,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(tracked.returncode, 0, tracked.stdout + tracked.stderr)

    def test_docs_gate_compares_delivery_to_checked_out_head(self):
        source = (REPO / "tools/agent-harness/drills/run_drills.py").read_text(
            encoding="utf-8")
        function = source.split("def check_docs(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"implementationCommit"', function)
        self.assertIn('"headCommit"', function)
        self.assertRegex(function, r"commit\s*!=\s*checked_out_head")
        self.assertRegex(function, r"evidence\.get\(\"headCommit\"\)\s*!=\s*checked_out_head")
        self.assertNotRegex(function, r"os\.environ|getenv\(")

    def test_generic_host_loader_accepts_optional_structural_transition(self):
        runner_path = REPO / "tools/agent-harness/drills/run_drills.py"
        spec = importlib.util.spec_from_file_location("generic_host_runner", runner_path)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        host = json.loads(HOST_CONFIG.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "host.json"
            host_without_transition = dict(host)
            host_without_transition.pop("governanceTransition")
            config.write_text(json.dumps(host_without_transition), encoding="utf-8")
            runner.load_host_config(root, Path("host.json"))

            custom_transition = dict(host_without_transition, governanceTransition={
                "version": 1,
                "id": "fixture-migration-v1",
                "sourcePlan": "docs/superpowers/plans/fixture-source.md",
                "targetPlan": "docs/superpowers/plans/fixture-target.md",
            })
            config.write_text(json.dumps(custom_transition), encoding="utf-8")
            runner.load_host_config(root, Path("host.json"))

            malformed = dict(custom_transition, governanceTransition={
                **custom_transition["governanceTransition"],
                "targetPlan": custom_transition["governanceTransition"]["sourcePlan"],
            })
            config.write_text(json.dumps(malformed), encoding="utf-8")
            with self.assertRaises(runner.DrillRefused):
                runner.load_host_config(root, Path("host.json"))

    def test_docs_gate_transition_and_checked_out_head_behavior(self):
        runner_path = REPO / "tools/agent-harness/drills/run_drills.py"
        spec = importlib.util.spec_from_file_location("governance_behavior_runner", runner_path)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo,
                           check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=repo,
                           check=True, capture_output=True)
            plan_rel = "docs/superpowers/plans/2026-10-10-business-dashboard-v1.md"
            target_plan_rel = "docs/superpowers/plans/2026-10-11-template-branding-wave2.md"
            manifest_rel = "docs/DELIVERY.json"
            ledger_rel = ".sdd/progress.md"
            marker_paths = ["README.md", "AGENTS.md", "docs/AUTONOMY.md", "docs/MEMORY.md"]
            plan = repo / plan_rel
            plan.parent.mkdir(parents=True)
            plan.write_text((REPO / plan_rel).read_text(encoding="utf-8"), encoding="utf-8")
            target_plan = repo / target_plan_rel
            target_plan.parent.mkdir(parents=True, exist_ok=True)
            target_plan.write_text((REPO / target_plan_rel).read_text(encoding="utf-8"),
                                   encoding="utf-8")
            ledger = repo / ledger_rel
            ledger.parent.mkdir()
            source_steps = runner._plan_checkboxes(plan.read_text(encoding="utf-8"))
            ledger.write_text("".join(
                f"Task {task}: {'in_progress' if steps['pending'] else 'complete'}\n"
                for task, steps in source_steps.items() if task.isdigit()), encoding="utf-8")
            for relative in marker_paths:
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("<!-- harness-delivery-status: transition_pending -->\n",
                                  encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True,
                           capture_output=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                                  text=True, capture_output=True).stdout.strip()
            host = {
                "activePlan": plan_rel,
                "deliveryManifest": manifest_rel,
                "ledger": ledger_rel,
                "deliverySyncPaths": [*marker_paths, manifest_rel, ledger_rel, plan_rel],
                "documentation": {"statusMarkerPaths": marker_paths},
                "governanceTransition": {
                    "version": 1,
                    "id": "dashboard-head-binding-to-wave2",
                    "sourcePlan": plan_rel,
                    "targetPlan": target_plan_rel,
                },
            }
            task_steps = source_steps
            manifest = {
                "version": 1,
                "planPath": plan_rel,
                "implementationCommit": None,
                "evidence": {"commands": [], "headCommit": None, "status": "pending"},
                "taskSteps": task_steps,
                "deliveryStatus": "transition_pending",
                "externalSync": {"independentReview": "pending", "github": "pending",
                                 "obsidian": "pending"},
                "reviewEvidence": {"requiredReviewers": ["security", "dx_ci", "whole_branch"],
                                   "reports": []},
            }
            manifest_path = repo / manifest_rel

            def publish(document):
                manifest_path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")

            with patch.object(runner, "_validate_review_evidence"), \
                    patch.object(runner, "_validate_repository_attestation"):
                publish(manifest)
                runner.check_docs(repo, host)

                for key, value in (
                    ("implementationCommit", head),
                    ("evidence", {"commands": ["unit"], "headCommit": head,
                                  "status": "passed"}),
                    ("externalSync", {"independentReview": "complete", "github": "pending",
                                       "obsidian": "pending"}),
                    ("reviewEvidence", {"requiredReviewers": ["security", "dx_ci", "whole_branch"],
                                        "reports": [{"invalid": True}]}),
                    ("taskSteps", {"1": {"completed": [1], "pending": []}}),
                    ("transitionId", "custom-transition"),
                ):
                    invalid = dict(manifest)
                    test_host = dict(host)
                    if key == "transitionId":
                        test_host["governanceTransition"] = dict(
                            host["governanceTransition"], id=value)
                    else:
                        invalid[key] = value
                    with self.subTest(transition_field=key):
                        publish(invalid)
                        with self.assertRaises(runner.DrillRefused):
                            runner.check_docs(repo, test_host)

                authorization_cases = (
                    ("missing", None, host["activePlan"], plan_rel),
                    ("custom-source", dict(host["governanceTransition"],
                                            sourcePlan="docs/superpowers/plans/custom.md"),
                     host["activePlan"], "docs/superpowers/plans/custom.md"),
                    ("swapped", dict(host["governanceTransition"],
                                     sourcePlan=host["governanceTransition"]["targetPlan"],
                                     targetPlan=host["governanceTransition"]["sourcePlan"]),
                     host["activePlan"], host["governanceTransition"]["targetPlan"]),
                    ("target-active", host["governanceTransition"],
                     host["governanceTransition"]["targetPlan"],
                     host["governanceTransition"]["targetPlan"]),
                )
                for name, authorization, active_plan, manifest_plan in authorization_cases:
                    altered = dict(manifest, planPath=manifest_plan)
                    test_host = dict(host, activePlan=active_plan)
                    if authorization is None:
                        test_host.pop("governanceTransition")
                    else:
                        test_host["governanceTransition"] = authorization
                    with self.subTest(governance_transition=name):
                        publish(altered)
                        with self.assertRaises(runner.DrillRefused):
                            runner.check_docs(repo, test_host)

                for relative in marker_paths:
                    target = repo / relative
                    target.write_text("transition marker missing\n", encoding="utf-8")
                    with self.subTest(marker=relative):
                        publish(manifest)
                        with self.assertRaises(runner.DrillRefused):
                            runner.check_docs(repo, host)
                    target.write_text("<!-- harness-delivery-status: transition_pending -->\n",
                                      encoding="utf-8")

                activated = dict(manifest)
                activated["deliveryStatus"] = "plan_activated"
                activated["planPath"] = target_plan_rel
                activated["taskSteps"] = runner._plan_checkboxes(
                    target_plan.read_text(encoding="utf-8"))
                (repo / ledger_rel).write_text("".join(
                    f"Task {task}: {'in_progress' if steps['pending'] else 'complete'}\n"
                    for task, steps in activated["taskSteps"].items() if task.isdigit()),
                    encoding="utf-8")
                activated_host = dict(host, activePlan=target_plan_rel)
                activated_marker = "<!-- harness-delivery-status: plan_activated -->\n"
                for relative in marker_paths:
                    (repo / relative).write_text(activated_marker, encoding="utf-8")
                publish(activated)
                runner.check_docs(repo, activated_host)

                marker_path = repo / marker_paths[0]
                marker_path.write_text(
                    "<!-- harness-delivery-status: transition_pending -->\n",
                    encoding="utf-8")
                with self.assertRaises(runner.DrillRefused):
                    runner.check_docs(repo, activated_host)
                marker_path.write_text(activated_marker, encoding="utf-8")

                for invalid, invalid_host, case in (
                    (dict(activated, deliveryStatus="transition_pending"), activated_host,
                     "transition-after-activation"),
                    (dict(activated, taskSteps=task_steps), activated_host,
                     "target-task-steps-mismatch"),
                    (activated, dict(activated_host, activePlan=plan_rel),
                     "target-marker-with-source-active"),
                ):
                    with self.subTest(activation_case=case):
                        publish(invalid)
                        with self.assertRaises(runner.DrillRefused):
                            runner.check_docs(repo, invalid_host)

                missing_activation_auth = dict(activated_host)
                missing_activation_auth.pop("governanceTransition")
                malformed_activation_auth = dict(activated_host, governanceTransition={
                    **host["governanceTransition"], "id": "arbitrary-activation",
                })
                for invalid_host, case in (
                    (missing_activation_auth, "activation-missing-authorization"),
                    (malformed_activation_auth, "activation-altered-authorization"),
                ):
                    with self.subTest(activation_authorization=case):
                        publish(activated)
                        with self.assertRaises(runner.DrillRefused):
                            runner.check_docs(repo, invalid_host)
                for relative in marker_paths:
                    (repo / relative).write_text(
                        "<!-- harness-delivery-status: transition_pending -->\n",
                        encoding="utf-8")
                publish(manifest)

                manifest_path = repo / manifest_rel
                publish(manifest)
                formal = dict(manifest)
                formal["implementationCommit"] = head
                formal["evidence"] = {"commands": ["unit"], "headCommit": head,
                                      "status": "passed"}
                formal["deliveryStatus"] = "local_validated_sync_pending"
                marker = "<!-- harness-delivery-status: local_validated_sync_pending -->\n"
                for relative in marker_paths:
                    (repo / relative).write_text(marker, encoding="utf-8")
                (repo / ledger_rel).write_text("".join(
                    f"Task {task}: {'in_progress' if steps['pending'] else 'complete'}\n"
                    for task, steps in task_steps.items() if task.isdigit()), encoding="utf-8")
                publish(formal)
                runner.check_docs(repo, host)

                stale = dict(formal)
                stale["implementationCommit"] = "0" * 40
                stale["evidence"] = dict(formal["evidence"], headCommit="0" * 40)
                publish(stale)
                with self.assertRaises(runner.DrillRefused):
                    runner.check_docs(repo, host)

                merge = subprocess.run(
                    ["git", "commit-tree", head + "^{tree}", "-p", head,
                     "-m", "temporary merge head"], cwd=repo, check=True, text=True,
                    capture_output=True,
                ).stdout.strip()
                subprocess.run(["git", "update-ref", "HEAD", merge], cwd=repo, check=True,
                               capture_output=True)
                source_evidence = dict(formal)
                source_evidence["implementationCommit"] = head
                source_evidence["evidence"] = dict(formal["evidence"], headCommit=head)
                publish(source_evidence)
                with self.assertRaises(runner.DrillRefused):
                    runner.check_docs(repo, host)

                with patch.dict("os.environ", {"GITHUB_SHA": head,
                                                "GITHUB_EVENT_PULL_REQUEST_HEAD_SHA": head}):
                    with self.assertRaises(runner.DrillRefused):
                        runner.check_docs(repo, host)

                publish(activated)
                for relative in marker_paths:
                    (repo / relative).write_text(activated_marker, encoding="utf-8")
                (repo / ledger_rel).write_text("".join(
                    f"Task {task}: {'in_progress' if steps['pending'] else 'complete'}\n"
                    for task, steps in activated["taskSteps"].items() if task.isdigit()),
                    encoding="utf-8")
                subprocess.run(["git", "add", manifest_rel, ledger_rel, *marker_paths],
                               cwd=repo, check=True, capture_output=True)
                subprocess.run(
                    ["git", "commit", "-m", "Activate Wave 2 plan", "-m",
                     "Governance-Transition-Activation: dashboard-head-binding-to-wave2"],
                    cwd=repo, check=True, capture_output=True)

                publish(manifest)
                for relative in marker_paths:
                    (repo / relative).write_text(
                        "<!-- harness-delivery-status: transition_pending -->\n",
                        encoding="utf-8")
                (repo / ledger_rel).write_text("".join(
                    f"Task {task}: {'in_progress' if steps['pending'] else 'complete'}\n"
                    for task, steps in source_steps.items() if task.isdigit()),
                    encoding="utf-8")
                with self.assertRaises(runner.DrillRefused):
                    runner.check_docs(repo, host)

    def test_delivery_status_matches_external_sync_and_review_evidence(self):
        manifest = json.loads((REPO / "docs/agent/HARNESS_DELIVERY.json").read_text(
            encoding="utf-8"))
        if manifest["deliveryStatus"] in {"transition_pending", "plan_activated"}:
            runner_path = REPO / "tools/agent-harness/drills/run_drills.py"
            spec = importlib.util.spec_from_file_location("delivery_state_runner", runner_path)
            runner = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(runner)
            host = runner.load_host_config(REPO)
            self.assertEqual(manifest["taskSteps"], runner._plan_checkboxes(
                (REPO / host["activePlan"]).read_text(encoding="utf-8")))
            for relative in host["documentation"]["statusMarkerPaths"]:
                with self.subTest(status_marker=relative):
                    self.assertIn("harness-delivery-status: " + manifest["deliveryStatus"],
                                  (REPO / relative).read_text(encoding="utf-8"))
            runner.check_docs(REPO, host)
        external = manifest["externalSync"]
        repository = external.get("repository", external.get("github"))
        knowledge_base = external.get("knowledgeBase", external.get("obsidian"))
        migration_statuses = {"transition_pending", "plan_activated"}
        expected_status = {
            ("pending", "pending", "pending"): "local_validated_sync_pending",
            ("complete", "pending", "pending"): "locally_reviewed_sync_pending",
            ("complete", "complete", "pending"): "repository_synced_knowledge_base_pending",
            ("complete", "complete", "complete"): "fully_synchronized",
        }
        key = (external["independentReview"], repository, knowledge_base)
        if manifest["deliveryStatus"] in migration_statuses:
            self.assertEqual(key, ("pending", "pending", "pending"))
        else:
            self.assertEqual(manifest["deliveryStatus"], expected_status[key])
        evidence = manifest["reviewEvidence"]
        self.assertEqual(evidence["requiredReviewers"],
                         ["security", "dx_ci", "whole_branch"])
        if external["independentReview"] == "complete":
            self.assertEqual(len(evidence["reports"]), 3)
            self.assertEqual(len({report["path"] for report in evidence["reports"]}), 3)
            self.assertEqual(len({report["sha256"] for report in evidence["reports"]}), 3)
            self.assertTrue(all(report["specVerdict"] == "APPROVED"
                                and report["codeQualityVerdict"] == "APPROVED"
                                for report in evidence["reports"]))
        else:
            self.assertEqual(evidence["reports"], [])

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
