from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[5]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
SCRIPT_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "native_acceptance.sh"
EVIDENCE_HELPER_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "native_acceptance_evidence.py"
BASELINE_POLICY_PATH = ROOT / ".github" / "release" / "baseline-policy.json"


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
        self.assertIn("baseline-policy.json", job_text(candidate))
        self.assertIn("gh release download", job_text(candidate))
        self.assertIn('--arg manifestSha256 "$BASELINE_MANIFEST_SHA256"', self.workflow_text)
        self.assertIn("manifestSha256: $manifestSha256", job_text(candidate))
        self.assertNotIn("baseline-version", job_text(candidate))
        self.assertEqual(1, job_text(candidate).count("build_release.py"))

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
        self.assertNotIn("actions/checkout@", native_text)
        self.assertIn("orchestrator/release/native_acceptance.sh", native_text)
        self.assertNotIn("gh release create", self.workflow_text)
        self.assertNotIn("gh release upload", self.workflow_text)

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
        self.assertNotIn("SURVEY_ACCEPTANCE_DNS_SUFFIX", text)
        self.assertIn("survey-native.test", text)

    def test_tag_publication_gate_depends_on_the_complete_native_matrix(self):
        gate = self.workflow["jobs"]["release-ready"]
        self.assertEqual("needs.policy.outputs.release == 'true'", gate["if"])
        self.assertEqual({"policy", "candidate-assets", "native-acceptance"}, set(gate["needs"]))
        self.assertEqual(1, len(gate["steps"]))
        self.assertEqual("test '${{ needs.native-acceptance.result }}' = success", gate["steps"][0]["run"])

    def test_baseline_policy_is_explicit_and_bootstrap_never_claims_upgrade(self):
        policy = json.loads(BASELINE_POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(1, policy["schemaVersion"])
        self.assertIn("releases", policy)
        for version, rule in policy["releases"].items():
            with self.subTest(version=version):
                self.assertIn(rule["mode"], {"predecessor", "bootstrap"})
                if rule["mode"] == "predecessor":
                    self.assertRegex(rule["version"], r"^[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?$")
                    self.assertRegex(rule["manifestSha256"], r"^[0-9a-f]{64}$")
                else:
                    self.assertNotIn("version", rule)
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("upgrade_verified=false", script)
        self.assertIn('"upgradeVerified": sys.argv[5] == "true"', script)


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
        self.assertNotIn("compose-logs", self.text)

    def test_script_verifies_shared_candidate_identity_before_installing(self):
        for required in (
            "CANDIDATE_IDENTITY",
            "CANDIDATE_MANIFEST_SHA256",
            "CANDIDATE_BUNDLE_SHA256",
            'baseline.get("manifestSha256")',
            "ADMIN_IMAGE_DIGEST",
            "PLATFORM_IMAGE_DIGEST",
            "PUBLISH_GATEWAY_IMAGE_DIGEST",
            "sha256sum --check",
            "docker buildx imagetools inspect",
        ):
            self.assertIn(required, self.text)

    def test_script_executes_full_lifecycle_and_restores_into_second_project(self):
        calls = re.findall(r"^\s*run_ctl\s+([a-z-]+)", self.text, re.MULTILINE)
        self.assertEqual(1, calls.count("setup-probe"))
        for command in ("install", "setup-probe", "doctor", "backup", "restore", "uninstall"):
            self.assertIn(command, calls)
        self.assertIn("survey-restore-", self.text)
        self.assertIn("BASELINE_MODE", self.text)

    def test_script_redacts_diagnostics_and_writes_success_or_failure_summary(self):
        self.assertIn("native_acceptance_evidence.py", self.text)
        self.assertIn("trap collect_evidence EXIT", self.text)
        self.assertIn("summary.json", self.text)
        self.assertIn("doctor", self.text)
        self.assertIn("scan", self.text)
        self.assertNotRegex(self.text, r">\s*\"?\$[^\n]*(?:\.log|\.raw)")

    def test_script_uses_local_trusted_ca_and_records_public_acme_as_separate(self):
        self.assertIn("/etc/hosts", self.text)
        self.assertIn("SSL_CERT_FILE", self.text)
        self.assertIn('"publicAcmeVerified": False', self.text)
        self.assertIn('"preflightDisk": "separately-tested"', self.text)


class NativeAcceptanceEvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("native_acceptance_evidence", EVIDENCE_HELPER_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def test_nested_keys_authorization_uris_multiline_and_encoded_variants_are_redacted(self):
        payload = {
            "password": "json-secret",
            "nested": {
                "apiToken": "json-token",
                "private_key": "line-one\nline-two",
                "Authorization": "Bearer bearer-secret",
                "database": "postgres://user:uri-secret@db/app?token=query-secret&safe=yes",
                "encoded": "https://user:encoded%2Dsecret@example.test/path?password=url%2Dsecret",
            },
            "safe": "retained",
        }
        sanitized = self.module.sanitize(payload, secret_values={"line-one\nline-two"})
        rendered = json.dumps(sanitized, sort_keys=True)
        for secret in ("json-secret", "json-token", "line-one", "line-two", "bearer-secret", "uri-secret", "query-secret", "encoded%2Dsecret", "url%2Dsecret"):
            self.assertNotIn(secret, rendered)
        self.assertEqual("retained", sanitized["safe"])

    def test_evidence_writer_keeps_only_allowlisted_doctor_fields_and_scanner_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "doctor-test.json"
            self.module.write_doctor_evidence({
                "status": "ok",
                "checks": [{"id": "tls", "status": "ok", "summary": "secret=hidden", "raw": "forbidden"}],
                "unexpected": "forbidden",
            }, output, secret_values={"hidden"})
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual({"schemaVersion", "status", "checks"}, set(evidence))
            self.assertEqual({"id", "status", "summary"}, set(evidence["checks"][0]))
            self.module.scan_directory(root, secret_values={"hidden"})
            (root / "services.json").write_text('{"safe":"Bearer mutation-secret"}', encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.scan_directory(root, secret_values={"hidden"})


if __name__ == "__main__":
    unittest.main()
