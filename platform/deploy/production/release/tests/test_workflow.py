from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[5]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
QUALITY_PATH = ROOT / ".github" / "workflows" / "platform-quality.yml"
POLICY_PATH = ROOT / ".github" / "release" / "vulnerability-policy.json"
IMAGE_NAMES = {"admin", "platform", "publish-gateway"}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_ACTIONS = {
    "actions/attest",
    "actions/checkout",
    "actions/download-artifact",
    "actions/setup-java",
    "actions/setup-node",
    "actions/setup-python",
    "actions/upload-artifact",
    "anchore/sbom-action",
    "aquasecurity/trivy-action",
    "docker/build-push-action",
    "docker/login-action",
    "docker/setup-buildx-action",
    "docker/setup-qemu-action",
    "sigstore/cosign-installer",
}


def load_yaml(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in document and "on" not in document:
        document["on"] = document.pop(True)
    return document


def action_uses(workflow: dict):
    for job_name, job in workflow["jobs"].items():
        if "uses" in job:
            yield job_name, job["uses"]
        for step in job.get("steps", []):
            if "uses" in step:
                yield job_name, step["uses"]


def job_text(job: dict) -> str:
    return json.dumps(job, sort_keys=True)


class ReleaseWorkflowPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = load_yaml(WORKFLOW_PATH)
        cls.quality = load_yaml(QUALITY_PATH)
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    def run_event_policy(self, event_name: str, ref: str) -> subprocess.CompletedProcess[str]:
        policy_step = next(
            step for step in self.workflow["jobs"]["policy"]["steps"]
            if step.get("id") == "event"
        )
        body = policy_step["run"]
        script = body.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "github-output"
            environment = dict(
                os.environ,
                GITHUB_EVENT_NAME=event_name,
                GITHUB_REF=ref,
                GITHUB_REF_NAME=ref.removeprefix("refs/tags/"),
                GITHUB_SHA="0123456789abcdef0123456789abcdef01234567",
                GITHUB_OUTPUT=str(output),
            )
            result = subprocess.run(
                ["python3", "-c", script],
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            if output.exists():
                result.stdout += output.read_text(encoding="utf-8")
            return result

    def test_triggers_are_review_or_manual_builds_and_release_tags(self):
        triggers = self.workflow["on"]
        self.assertIn("pull_request", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertIn("push", triggers)
        self.assertTrue(triggers["pull_request"].get("paths"))
        self.assertEqual(["v*"], triggers["push"].get("tags"))
        self.assertNotIn("branches", triggers["push"])

    def test_event_policy_accepts_only_canonical_release_semver(self):
        for tag in ("v0.0.0", "v1.2.3", "v10.20.30-rc.0", "v10.20.30-rc.42"):
            with self.subTest(valid=tag):
                result = self.run_event_policy("push", f"refs/tags/{tag}")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("release=true", result.stdout)
                self.assertIn(f"version={tag[1:]}", result.stdout)

        invalid = (
            "v01.2.3",
            "v1.02.3",
            "v1.2.03",
            "v1.2.3-rc.01",
            "v1.2",
            "v1.2.3-rc",
            "v1.2.3+build.1",
            "v1.2.3foo",
        )
        for tag in invalid:
            with self.subTest(invalid=tag):
                result = self.run_event_policy("push", f"refs/tags/{tag}")
                self.assertNotEqual(0, result.returncode)

        for event in ("pull_request", "workflow_dispatch"):
            with self.subTest(event=event):
                result = self.run_event_policy(event, "refs/heads/feature")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("release=false", result.stdout)

    def test_concurrency_cancels_branches_but_never_in_progress_tags(self):
        concurrency = self.workflow["concurrency"]
        self.assertIn("github.ref", concurrency["group"])
        self.assertIn("startsWith(github.ref, 'refs/tags/')", concurrency["cancel-in-progress"])

    def test_release_reuses_the_complete_platform_quality_workflow(self):
        jobs = self.workflow["jobs"]
        quality = jobs["quality"]
        self.assertEqual("./.github/workflows/platform-quality.yml", quality["uses"])
        self.assertEqual(["policy"], quality["needs"])
        self.assertEqual({"contents": "read"}, quality["permissions"])
        self.assertIn("workflow_call", self.quality["on"])

        required_jobs = {
            "parity-tables",
            "admin-web",
            "admin-web-e2e",
            "gateway-parity-e2e",
            "platform-java",
            "access-policy-e2e",
            "p1-e2e",
            "productization-production",
        }
        self.assertTrue(required_jobs <= set(self.quality["jobs"]))

        admin = job_text(self.quality["jobs"]["admin-web"])
        for command in ("npm run lint", "npm run typecheck", "npm test -- --run", "npm run build"):
            self.assertIn(command, admin)
        self.assertIn("run-platform-tests.sh", job_text(self.quality["jobs"]["platform-java"]))
        self.assertIn("publish-gateway", job_text(self.quality["jobs"]["parity-tables"]))

        product = job_text(self.quality["jobs"]["productization-production"])
        for required in (
            "platform/tools/productization",
            "render_capabilities.py --check",
            "platform/deploy/production/release/tests",
            "platform/deploy/production/tests",
        ):
            self.assertIn(required, product)

        all_quality = job_text(self.quality)
        for script in (
            "run-admin-web-e2e.sh",
            "run-publish-gateway-parity.sh",
            "run-access-policy.sh",
            "run-p1-e2e.sh",
        ):
            self.assertIn(script, all_quality)

    def test_read_only_local_build_and_tag_only_publish_have_separate_permissions(self):
        jobs = self.workflow["jobs"]
        self.assertEqual({"contents": "read"}, jobs["policy"]["permissions"])
        self.assertEqual({"contents": "read"}, jobs["quality"]["permissions"])
        self.assertEqual({"contents": "read"}, jobs["build-local"]["permissions"])
        self.assertEqual(
            {"contents": "read", "packages": "write"},
            jobs["publish-images"]["permissions"],
        )
        self.assertEqual("needs.policy.outputs.release != 'true'", jobs["build-local"]["if"])
        self.assertEqual("needs.policy.outputs.release == 'true'", jobs["publish-images"]["if"])
        self.assertEqual({"policy", "quality"}, set(jobs["build-local"]["needs"]))
        self.assertEqual({"policy", "quality"}, set(jobs["publish-images"]["needs"]))
        self.assertNotIn("docker/login-action@", job_text(jobs["build-local"]))

        local_build = next(
            step for step in jobs["build-local"]["steps"]
            if str(step.get("uses", "")).startswith("docker/build-push-action@")
        )
        publish_build = next(
            step for step in jobs["publish-images"]["steps"]
            if str(step.get("uses", "")).startswith("docker/build-push-action@")
        )
        self.assertEqual(False, local_build["with"]["push"])
        self.assertEqual(True, publish_build["with"]["push"])

    def test_both_build_paths_cover_three_multiarch_images_with_attestations(self):
        for job_name in ("build-local", "publish-images"):
            job = self.workflow["jobs"][job_name]
            matrix = job["strategy"]["matrix"]["include"]
            self.assertEqual(IMAGE_NAMES, {entry["id"] for entry in matrix})
            build = next(
                step for step in job["steps"]
                if str(step.get("uses", "")).startswith("docker/build-push-action@")
            )
            self.assertEqual("linux/amd64,linux/arm64", build["with"]["platforms"])
            self.assertEqual(True, build["with"]["sbom"])
            self.assertEqual("mode=max", build["with"]["provenance"])

        self.assertIn("steps.build.outputs.digest", self.workflow_text)
        self.assertIn("git show -s --format=%cI", self.workflow_text)
        self.assertNotRegex(self.workflow_text, r"(?i)(?:^|[-_:])latest(?:$|[\s'\"@])")

    def test_security_job_depends_on_tag_publish_and_scans_signs_and_attests(self):
        secure = self.workflow["jobs"]["secure-images"]
        self.assertEqual("needs.policy.outputs.release == 'true'", secure["if"])
        self.assertEqual({"policy", "quality", "publish-images"}, set(secure["needs"]))
        self.assertEqual(IMAGE_NAMES, set(secure["strategy"]["matrix"]["image"]))
        text = job_text(secure)
        for required in (
            "aquasecurity/trivy-action@",
            "exit-code",
            "sigstore/cosign-installer@",
            "cosign sign",
            "--bundle",
            "actions/attest@",
            "sbom-path",
            "push-to-registry",
            "actions/upload-artifact@",
        ):
            self.assertIn(required, text)
        self.assertGreaterEqual(text.count("actions/attest@"), 2)
        self.assertIn("@${DIGEST}", text)
        self.assertNotIn("gh release", self.workflow_text)

    def test_every_external_action_is_allowlisted_and_pinned_to_a_full_sha(self):
        for workflow_name, workflow in (("release", self.workflow), ("quality", self.quality)):
            for job_name, uses in action_uses(workflow):
                if uses.startswith("./"):
                    continue
                action, separator, ref = uses.partition("@")
                with self.subTest(workflow=workflow_name, job=job_name, action=action):
                    self.assertEqual("@", separator)
                    self.assertIn(action, ALLOWED_ACTIONS)
                    self.assertRegex(ref, FULL_SHA)

    def test_versioned_vulnerability_policy_is_fail_closed(self):
        self.assertEqual(1, self.policy["schemaVersion"])
        self.assertRegex(self.policy["trivyVersion"], r"^v[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertEqual(["HIGH", "CRITICAL"], self.policy["blockSeverities"])
        self.assertFalse(self.policy["ignoreUnfixed"])
        requirements = self.policy["exceptionRequirements"]
        self.assertEqual(
            {"id", "rootCause", "owner", "expiresAt"},
            set(requirements["requiredFields"]),
        )
        self.assertEqual(90, requirements["maximumLifetimeDays"])
        secure_text = job_text(self.workflow["jobs"]["secure-images"])
        self.assertIn("maximumLifetimeDays", secure_text)
        self.assertIn("vulnerabilities: []", secure_text)
        for exception in self.policy["exceptions"]:
            with self.subTest(exception=exception.get("id")):
                self.assertRegex(exception["id"], r"^CVE-[0-9]{4}-[0-9]{4,}$")
                self.assertTrue(exception["rootCause"].strip())
                self.assertTrue(exception["owner"].strip())
                self.assertGreater(date.fromisoformat(exception["expiresAt"]), date.today())


if __name__ == "__main__":
    unittest.main()
