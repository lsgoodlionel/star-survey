from __future__ import annotations

import hashlib
import importlib.util
import json
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


def shell_without_heredocs(source: str) -> str:
    kept = []
    delimiter = None
    for line in source.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        match = re.search(r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        kept.append(line)
        if match:
            delimiter = match.group(1)
    return "\n".join(kept)


REQUIRED_LIFECYCLE_COMMANDS = (
    'run_ctl install',
    'run_ctl setup-probe',
    'write_doctor_evidence "doctor-installed"',
    'run_ctl backup',
    'run_ctl upgrade',
    'write_doctor_evidence "doctor-upgraded"',
    'run_ctl restore',
    'write_doctor_evidence "doctor-restored"',
    'run_ctl uninstall',
    'acceptance_status="passed"',
)


def assert_native_acceptance_contract(testcase: unittest.TestCase, source: str) -> None:
    shell = shell_without_heredocs(source)
    identifiers = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", shell)
    testcase.assertFalse(
        [word for word in identifiers if re.search(r"(?i)(skip|bypass|disable|opt_?out)", word)],
        "native acceptance contains a success-bypass identifier",
    )
    testcase.assertNotIn("||", shell)
    testcase.assertNotRegex(shell, r"(?m)(?:^|[;&]\s*)continue(?:\s|;|$)")
    testcase.assertNotRegex(shell, r"(?m)\b(?:exit|return)\s+0\b")
    testcase.assertNotRegex(shell, r"(?m)\b(?:exit|return)(?=\s*(?:;|$))")
    testcase.assertNotRegex(shell, r"(?m)\bexec\b")
    exits = re.findall(r"(?m)\bexit\s+([^;\n]+)", shell)
    testcase.assertEqual(["2", "2", '"$result"'], exits, "native acceptance exit propagation changed")
    testcase.assertEqual(1, shell.count('acceptance_status="passed"'))
    testcase.assertIn("trap collect_evidence EXIT", shell)
    testcase.assertIn("local result=$?", shell)
    testcase.assertIn('exit "$result"', shell)
    positions = []
    for command in REQUIRED_LIFECYCLE_COMMANDS:
        expected_count = 2 if command == "run_ctl uninstall" else 1
        testcase.assertEqual(expected_count, shell.count(command), f"required lifecycle command count changed: {command}")
        positions.append(shell.rindex(command))
    testcase.assertEqual(sorted(positions), positions, "native lifecycle command order changed")


def assert_native_workflow_invocation(testcase: unittest.TestCase, workflow: dict) -> None:
    job = workflow["jobs"]["native-acceptance"]
    step = next(item for item in job["steps"] if item.get("name") == "Execute real clean-host install upgrade restore and uninstall")
    testcase.assertEqual(
        "sudo -E NATIVE_ACCEPTANCE_REAL=1 bash orchestrator/release/native_acceptance.sh",
        step["run"],
    )


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
        self.assertIn("commit: $commit", job_text(candidate))
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
        text = job_text(gate)
        self.assertIn("test '${{ needs.native-acceptance.result }}' = success", text)
        self.assertIn("native-acceptance-*", text)
        self.assertIn("release-readiness.json", text)
        self.assertIn("native-release-readiness", text)
        self.assertIn("readiness", text)
        self.assertIn("verify-readiness", text)
        self.assertIn("if-no-files-found", text)
        self.assertEqual("${{ steps.readiness.outputs.promotable }}", gate["outputs"]["promotable"])
        self.assertEqual("native-release-readiness", gate["outputs"]["readiness-artifact"])

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
                    self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+-rc\.1$")
                    self.assertNotIn("version", rule)
        candidate_text = job_text(self.workflow["jobs"]["candidate-assets"])
        self.assertIn("bootstrap is only valid for the first prerelease RC", candidate_text)
        self.assertIn("gh release list --limit 1", candidate_text)
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("upgrade_verified=false", script)
        helper = EVIDENCE_HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('"upgradeVerified": upgrade_verified', helper)


class NativeAcceptanceScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT_PATH.read_text(encoding="utf-8")

    def test_script_is_valid_strict_shell_without_skip_or_fake_success_path(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT_PATH)], capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("set -Eeuo pipefail", self.text)
        self.assertIn('[[ "${NATIVE_ACCEPTANCE_REAL:-}" == "1" ]]', self.text)
        self.assertNotIn("continue-on-error", self.text)
        self.assertNotIn("compose-logs", self.text)
        assert_native_acceptance_contract(self, self.text)
        assert_native_workflow_invocation(self, load_workflow())

    def test_success_bypass_mutations_are_rejected(self):
        mutations = (
            'ALLOW_SKIP=1',
            'SKIP=1',
            'continue',
            'false || true',
            'false || exit 0',
            '[[ "${ALLOW_SKIP:-}" != "1" ]] || exit 0',
            'if [[ -n "${BYPASS:-}" ]]; then exit 0; fi',
            'if [[ -n "${FAST:-}" ]]; then exit; fi',
            'if [[ -n "${FAST:-}" ]]; then exec /usr/bin/true; fi',
            'if [[ -n "${FAST:-}" ]]; then acceptance_status="passed"; exit; fi',
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                assert_native_acceptance_contract(self, self.text + "\n" + mutation + "\n")

    def test_deleting_any_required_lifecycle_command_is_rejected(self):
        for command in REQUIRED_LIFECYCLE_COMMANDS:
            with self.subTest(command=command), self.assertRaises(AssertionError):
                assert_native_acceptance_contract(self, self.text.replace(command, "", 1))

    def test_workflow_cannot_swallow_native_acceptance_failure(self):
        workflow = load_workflow()
        step = next(
            item for item in workflow["jobs"]["native-acceptance"]["steps"]
            if item.get("name") == "Execute real clean-host install upgrade restore and uninstall"
        )
        step["run"] += " || exit 0"
        with self.assertRaises(AssertionError):
            assert_native_workflow_invocation(self, workflow)

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
        helper = EVIDENCE_HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn('"publicAcmeVerified": False', helper)
        self.assertIn('"preflightDisk": "separately-tested"', helper)


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
            output = root / "doctor-installed.json"
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

    def test_unknown_headers_cookies_and_pem_blocks_are_never_preserved(self):
        attack = (
            "X-Api-Key: unknown-header-secret\n"
            "Cookie: session=unknown-cookie-secret\n"
            "-----BEGIN PRIVATE KEY-----\nunknown-pem-secret\n-----END PRIVATE KEY-----"
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "doctor-installed.json"
            self.module.write_doctor_evidence({
                "status": "ok",
                "checks": [{"id": "tls", "status": "ok", "summary": attack}],
            }, output)
            rendered = output.read_text(encoding="utf-8")
            for secret in ("unknown-header-secret", "unknown-cookie-secret", "unknown-pem-secret"):
                self.assertNotIn(secret, rendered)
            self.module.scan_directory(output.parent)
            output.write_text(json.dumps({
                "schemaVersion": 1,
                "status": "ok",
                "checks": [{"id": "tls", "status": "ok", "summary": attack}],
            }), encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.scan_directory(output.parent)

    def test_strict_schema_rejects_unknown_fields_wrong_types_and_long_strings(self):
        valid = {
            "schemaVersion": 1,
            "status": "ok",
            "checks": [{"id": "tls", "status": "ok", "summary": "trusted HTTPS marker verified"}],
        }
        self.module.validate_evidence("doctor", valid)
        mutations = (
            {**valid, "unexpected": "value"},
            {**valid, "status": 1},
            {**valid, "checks": [{"id": "tls", "status": "ok", "summary": "x" * 4097}]},
            {**valid, "checks": [{**valid["checks"][0], "raw": "forbidden"}]},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(self.module.EvidenceError):
                self.module.validate_evidence("doctor", mutation)

    def test_release_readiness_blocks_stable_bootstrap_or_unverified_upgrade(self):
        runners = (
            ("22.04", "amd64"),
            ("24.04", "amd64"),
            ("22.04", "arm64"),
            ("24.04", "arm64"),
        )

        def summaries(mode: str, verified: bool) -> list[dict]:
            return [
                {
                    "schemaVersion": 1,
                    "status": "passed",
                    "exitCode": 0,
                    "lastStep": "uninstall",
                    "runner": {"ubuntu": ubuntu, "architecture": architecture},
                    "candidate": {
                        "manifestSha256": "a" * 64,
                        "bundleSha256": ("b" if architecture == "amd64" else "f") * 64,
                        "images": {
                            "admin": "sha256:" + "c" * 64,
                            "platform": "sha256:" + "d" * 64,
                            "publish-gateway": "sha256:" + "e" * 64,
                        },
                    },
                    "baselineMode": mode,
                    "upgradeVerified": verified,
                    "tls": {"scope": "ci-local-trusted-ca", "httpsMarkerVerified": True, "publicAcmeVerified": False},
                    "preflightDisk": "separately-tested",
                    "completedAt": "2026-10-09T00:00:00+00:00",
                }
                for ubuntu, architecture in runners
            ]

        def identity(version: str, mode: str) -> dict:
            return {
                "version": version,
                "commit": "1" * 40,
                "manifestSha256": "a" * 64,
                "bundles": {
                    "amd64": {"sha256": "b" * 64},
                    "arm64": {"sha256": "f" * 64},
                },
                "images": {
                    "admin": {"digest": "sha256:" + "c" * 64},
                    "platform": {"digest": "sha256:" + "d" * 64},
                    "publish-gateway": {"digest": "sha256:" + "e" * 64},
                },
                "baseline": {"mode": mode},
            }

        def evidence_hashes() -> dict[str, str]:
            return {
                f"ubuntu-{ubuntu}-{architecture}": hashlib.sha256(
                    json.dumps(summary, sort_keys=True).encode("utf-8")
                ).hexdigest()
                for summary, (ubuntu, architecture) in zip(summaries("predecessor", True), runners)
            }

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "release-readiness.json"
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_release_readiness(
                    identity("1.0.0", "bootstrap"),
                    summaries("bootstrap", False), evidence_hashes(), output,
                )
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_release_readiness(
                    identity("1.0.0", "predecessor"),
                    summaries("predecessor", False), evidence_hashes(), output,
                )
            mismatched = summaries("predecessor", True)
            mismatched[0]["candidate"]["manifestSha256"] = "9" * 64
            with self.assertRaises(self.module.EvidenceError):
                self.module.write_release_readiness(
                    identity("1.0.0", "predecessor"), mismatched, evidence_hashes(), output,
                )
            self.module.write_release_readiness(
                identity("1.0.0", "predecessor"), summaries("predecessor", True), evidence_hashes(), output,
            )
            stable = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(stable["promotable"])
            self.assertEqual("upgrade-verified", stable["reason"])
            self.assertEqual({
                "schemaVersion", "candidateVersion", "candidateCommit", "manifestSha256",
                "amd64BundleSha256", "arm64BundleSha256", "adminManifestDigest",
                "platformManifestDigest", "gatewayManifestDigest", "matrixEvidenceSha256",
                "baselineMode", "nativeMatrixCount", "promotable", "reason",
            }, set(stable))
            self.assertEqual("1" * 40, stable["candidateCommit"])
            self.assertEqual("a" * 64, stable["manifestSha256"])
            self.assertEqual("b" * 64, stable["amd64BundleSha256"])
            self.assertEqual("f" * 64, stable["arm64BundleSha256"])
            self.assertEqual(evidence_hashes(), stable["matrixEvidenceSha256"])
            self.module.write_release_readiness(
                identity("0.3.0-rc.1", "bootstrap"),
                summaries("bootstrap", False), evidence_hashes(), output,
            )
            bootstrap = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(bootstrap["promotable"])
            self.assertEqual("bootstrap-not-promotable", bootstrap["reason"])

    def test_downloaded_assets_are_recomputed_before_readiness_is_trusted(self):
        runners = (("22.04", "amd64"), ("24.04", "amd64"), ("22.04", "arm64"), ("24.04", "arm64"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            evidence = root / "evidence"
            candidate.mkdir()
            evidence.mkdir()
            bundle_hashes = {}
            bundles = {}
            for architecture in ("amd64", "arm64"):
                name = f"survey-1.0.0-linux-{architecture}.tar.gz"
                payload = f"bundle-{architecture}".encode("utf-8")
                (candidate / name).write_bytes(payload)
                digest = hashlib.sha256(payload).hexdigest()
                bundle_hashes[architecture] = digest
                bundles[architecture] = {"name": name, "sha256": digest}
            image_digests = {
                "admin": "sha256:" + "c" * 64,
                "platform": "sha256:" + "d" * 64,
                "publish-gateway": "sha256:" + "e" * 64,
            }
            manifest = {
                "version": "1.0.0", "commit": "1" * 40,
                "assets": {"bundles": bundles},
                "images": {
                    "ADMIN_IMAGE": "ghcr.io/example/admin@" + image_digests["admin"],
                    "PLATFORM_IMAGE": "ghcr.io/example/platform@" + image_digests["platform"],
                    "PUBLISH_GATEWAY_IMAGE": "ghcr.io/example/gateway@" + image_digests["publish-gateway"],
                },
            }
            manifest_path = candidate / "release.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            identity = {
                "schemaVersion": 1, "version": "1.0.0", "commit": "1" * 40,
                "manifestSha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "bundles": bundles, "baseline": {"mode": "predecessor"},
                "images": {
                    name: {"reference": manifest["images"][key], "digest": image_digests[name]}
                    for name, key in (("admin", "ADMIN_IMAGE"), ("platform", "PLATFORM_IMAGE"), ("publish-gateway", "PUBLISH_GATEWAY_IMAGE"))
                },
            }
            identity_path = root / "identity.json"
            identity_path.write_text(json.dumps(identity), encoding="utf-8")
            for ubuntu, architecture in runners:
                directory = evidence / f"native-acceptance-{ubuntu}-{architecture}"
                directory.mkdir()
                summary = {
                    "schemaVersion": 1, "status": "passed", "exitCode": 0, "lastStep": "uninstall",
                    "runner": {"ubuntu": ubuntu, "architecture": architecture},
                    "candidate": {
                        "manifestSha256": identity["manifestSha256"],
                        "bundleSha256": bundle_hashes[architecture], "images": image_digests,
                    },
                    "baselineMode": "predecessor", "upgradeVerified": True,
                    "tls": {"scope": "ci-local-trusted-ca", "httpsMarkerVerified": True, "publicAcmeVerified": False},
                    "preflightDisk": "separately-tested", "completedAt": "2026-10-09T00:00:00+00:00",
                }
                (directory / "summary.json").write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
            readiness_path = root / "release-readiness.json"
            self.module.write_release_readiness_from_assets(identity_path, candidate, evidence, readiness_path)
            self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)

            original = readiness_path.read_text(encoding="utf-8")
            mutations = {
                "candidateVersion": "1.0.1", "candidateCommit": "2" * 40,
                "manifestSha256": "9" * 64, "amd64BundleSha256": "9" * 64,
                "arm64BundleSha256": "9" * 64,
                "adminManifestDigest": "sha256:" + "9" * 64,
                "platformManifestDigest": "sha256:" + "9" * 64,
                "gatewayManifestDigest": "sha256:" + "9" * 64,
            }
            for field, replacement in mutations.items():
                mutated = json.loads(original)
                mutated[field] = replacement
                readiness_path.write_text(json.dumps(mutated), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(self.module.EvidenceError):
                    self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            mutated = json.loads(original)
            mutated["matrixEvidenceSha256"]["ubuntu-22.04-amd64"] = "9" * 64
            readiness_path.write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            mutated = json.loads(original)
            mutated["unexpected"] = "forbidden"
            readiness_path.write_text(json.dumps(mutated), encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            readiness_path.write_text(original, encoding="utf-8")
            amd64_bundle = candidate / bundles["amd64"]["name"]
            original_bundle = amd64_bundle.read_bytes()
            amd64_bundle.write_bytes(b"mutated bundle")
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)
            amd64_bundle.write_bytes(original_bundle)
            summary_path = evidence / "native-acceptance-22.04-amd64" / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["completedAt"] = "2026-10-09T00:00:01+00:00"
            summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
            with self.assertRaises(self.module.EvidenceError):
                self.module.verify_release_readiness_assets(readiness_path, identity_path, candidate, evidence)


if __name__ == "__main__":
    unittest.main()
