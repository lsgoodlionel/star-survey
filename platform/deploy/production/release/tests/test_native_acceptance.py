from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[5]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
SCRIPT_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "native_acceptance.sh"


def load_workflow() -> dict:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    if True in workflow and "on" not in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def job_text(job: dict) -> str:
    return json.dumps(job, sort_keys=True)


class NativeAcceptanceWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load_workflow()
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_matrix_uses_all_four_official_native_ubuntu_labels(self):
        job = self.workflow["jobs"]["native-acceptance"]
        matrix = job["strategy"]["matrix"]["include"]
        self.assertEqual(
            {
                ("ubuntu-22.04", "22.04", "amd64"),
                ("ubuntu-24.04", "24.04", "amd64"),
                ("ubuntu-22.04-arm", "22.04", "arm64"),
                ("ubuntu-24.04-arm", "24.04", "arm64"),
            },
            {(entry["runner"], entry["ubuntu"], entry["architecture"]) for entry in matrix},
        )
        self.assertEqual("${{ matrix.runner }}", job["runs-on"])
        self.assertFalse(job["strategy"].get("fail-fast", True))
        self.assertNotIn("continue-on-error", job_text(job))

    def test_candidate_is_built_once_and_every_matrix_job_consumes_same_identity(self):
        jobs = self.workflow["jobs"]
        candidate = jobs["candidate-assets"]
        native = jobs["native-acceptance"]
        self.assertEqual("needs.policy.outputs.release == 'true'", candidate["if"])
        self.assertEqual({"policy", "quality", "publish-images", "secure-images"}, set(candidate["needs"]))
        self.assertIn("release-candidate", job_text(candidate))
        self.assertIn("identity.json", job_text(candidate))

        text = job_text(native)
        self.assertIn("actions/download-artifact@", text)
        self.assertIn("release-candidate", text)
        self.assertIn("CANDIDATE_IDENTITY", text)
        self.assertIn("CANDIDATE_MANIFEST_SHA256", text)
        self.assertIn("CANDIDATE_BUNDLE_SHA256", text)
        for image_path in ("images.admin", "images.platform", 'images[\\"publish-gateway\\"]'):
            self.assertIn(image_path, text)
        self.assertNotIn("docker/build-push-action@", text)
        self.assertNotIn("build_release.py", text)

    def test_manual_dispatch_can_reverify_an_existing_candidate_but_never_publish(self):
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertIn("candidate_run_id", inputs)
        native_text = job_text(self.workflow["jobs"]["native-acceptance"])
        self.assertIn("inputs.candidate_run_id", native_text)
        self.assertIn("github-token", native_text)
        self.assertNotIn("gh release", self.workflow_text)

    def test_native_job_runs_real_lifecycle_and_always_uploads_redacted_evidence(self):
        job = self.workflow["jobs"]["native-acceptance"]
        text = job_text(job)
        self.assertIn("native_acceptance.sh", text)
        self.assertIn("sudo -E", text)
        self.assertIn("NATIVE_ACCEPTANCE_REAL=1", text)
        upload = next(step for step in job["steps"] if step.get("name") == "Preserve redacted native acceptance evidence")
        self.assertEqual("always()", upload["if"])
        self.assertIn("native-acceptance-${{ matrix.ubuntu }}-${{ matrix.architecture }}", text)
        self.assertIn("if-no-files-found", text)
        self.assertNotIn("SURVEY_PRODUCTION_E2E", text)
        self.assertNotIn("skip", text.lower())

    def test_tag_publication_gate_depends_on_the_complete_native_matrix(self):
        gate = self.workflow["jobs"]["release-ready"]
        self.assertEqual("needs.policy.outputs.release == 'true'", gate["if"])
        self.assertIn("native-acceptance", gate["needs"])
        self.assertIn("candidate-assets", gate["needs"])


class NativeAcceptanceScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_script_is_valid_strict_shell_without_skip_or_fake_success_path(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT_PATH)], capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("set -Eeuo pipefail", self.text)
        self.assertIn('[[ "${NATIVE_ACCEPTANCE_REAL:-}" == "1" ]]', self.text)
        self.assertNotRegex(self.text.lower(), r"\bskip(?:ped)?\b")
        self.assertNotIn("continue-on-error", self.text)
        self.assertNotIn("|| true", self.text)

    def test_script_verifies_shared_candidate_identity_before_installing(self):
        for required in (
            "CANDIDATE_IDENTITY",
            "CANDIDATE_MANIFEST_SHA256",
            "CANDIDATE_BUNDLE_SHA256",
            "ADMIN_IMAGE_DIGEST",
            "PLATFORM_IMAGE_DIGEST",
            "PUBLISH_GATEWAY_IMAGE_DIGEST",
            "sha256sum --check",
            "docker buildx imagetools inspect",
        ):
            self.assertIn(required, self.text)

    def test_script_executes_full_lifecycle_and_restores_into_second_project(self):
        required_in_order = (
            "install",
            "setup-probe",
            "doctor",
            "minimal-probe",
            "backup",
            "upgrade",
            "doctor",
            "restore-target",
            "restore",
            "doctor",
            "uninstall",
        )
        position = -1
        for marker in required_in_order:
            next_position = self.text.find(marker, position + 1)
            self.assertGreater(next_position, position, marker)
            position = next_position
        self.assertIn("survey-restore-", self.text)

    def test_script_redacts_diagnostics_and_writes_success_or_failure_summary(self):
        self.assertIn("redact", self.text)
        self.assertIn("trap collect_evidence EXIT", self.text)
        self.assertIn("summary.json", self.text)
        self.assertIn("doctor", self.text)
        self.assertIn("logs", self.text)


if __name__ == "__main__":
    unittest.main()
