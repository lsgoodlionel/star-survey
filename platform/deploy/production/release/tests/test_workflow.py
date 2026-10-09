from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[5]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
POLICY_PATH = ROOT / ".github" / "release" / "vulnerability-policy.json"
SEMVER_TAG = r"^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$"
IMAGE_NAMES = {"admin", "platform", "publish-gateway"}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_MAJOR = re.compile(r"^v[0-9]+$")


def load_workflow() -> dict:
    document = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    # PyYAML 1.1 treats the unquoted key `on` as a boolean.
    if True in document and "on" not in document:
        document["on"] = document.pop(True)
    return document


def action_steps(job: dict) -> list[dict]:
    return [step for step in job.get("steps", []) if "uses" in step]


class ReleaseWorkflowPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = load_workflow()
        cls.policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    def test_triggers_are_review_or_manual_builds_and_semver_release_tags(self):
        triggers = self.workflow["on"]
        self.assertIn("pull_request", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertIn("push", triggers)
        self.assertTrue(triggers["pull_request"].get("paths"))
        self.assertTrue(triggers["push"].get("tags"))
        self.assertNotIn("branches", triggers["push"])

        policy_script = "\n".join(
            step.get("run", "") for step in self.workflow["jobs"]["policy"].get("steps", [])
        )
        self.assertIn(SEMVER_TAG, policy_script)
        self.assertIn("GITHUB_EVENT_NAME", policy_script)
        self.assertIn("GITHUB_REF", policy_script)

    def test_concurrency_cancels_branches_but_never_in_progress_tags(self):
        concurrency = self.workflow["concurrency"]
        self.assertIn("github.ref", concurrency["group"])
        self.assertIn("startsWith(github.ref, 'refs/tags/')", concurrency["cancel-in-progress"])

    def test_top_level_and_job_permissions_are_minimal(self):
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        jobs = self.workflow["jobs"]
        self.assertEqual({"contents": "read"}, jobs["policy"]["permissions"])
        self.assertEqual({"contents": "read"}, jobs["quality"]["permissions"])
        self.assertEqual(
            {"contents": "read", "packages": "write"},
            jobs["build-images"]["permissions"],
        )
        self.assertEqual(
            {
                "contents": "read",
                "packages": "write",
                "id-token": "write",
                "attestations": "write",
                "artifact-metadata": "write",
            },
            jobs["secure-images"]["permissions"],
        )
        self.assertNotIn("contents: write", self.workflow_text)

    def test_build_waits_for_quality_and_covers_three_multiarch_images(self):
        jobs = self.workflow["jobs"]
        self.assertEqual({"policy", "quality"}, set(jobs["build-images"]["needs"]))
        matrix = jobs["build-images"]["strategy"]["matrix"]["include"]
        self.assertEqual(IMAGE_NAMES, {entry["id"] for entry in matrix})
        self.assertEqual(len(IMAGE_NAMES), len(matrix))
        self.assertTrue(all(entry["context"].startswith("platform/") for entry in matrix))

        build_steps = [
            step for step in jobs["build-images"]["steps"]
            if str(step.get("uses", "")).startswith("docker/build-push-action@")
        ]
        self.assertEqual(1, len(build_steps))
        build = build_steps[0]
        self.assertEqual("linux/amd64,linux/arm64", build["with"]["platforms"])
        self.assertEqual(True, build["with"]["sbom"])
        self.assertEqual("mode=max", build["with"]["provenance"])
        self.assertIn("needs.policy.outputs.release", build["with"]["push"])
        self.assertIn("steps.build.outputs.digest", self.workflow_text)
        self.assertIn("git show -s --format=%cI", self.workflow_text)
        self.assertIn("steps.source.outputs.created", build["with"]["build-args"])

    def test_non_release_events_cannot_push_sign_attest_or_create_release(self):
        jobs = self.workflow["jobs"]
        release_condition = "needs.policy.outputs.release == 'true'"
        self.assertEqual(release_condition, jobs["secure-images"]["if"])
        self.assertNotIn("gh release", self.workflow_text)
        self.assertNotRegex(self.workflow_text, r"(?i)(?:^|[-_:])latest(?:$|[\s'\"@])")

        build = next(
            step for step in jobs["build-images"]["steps"]
            if str(step.get("uses", "")).startswith("docker/build-push-action@")
        )
        self.assertEqual("${{ needs.policy.outputs.release == 'true' }}", build["with"]["push"])
        login = next(
            step for step in jobs["build-images"]["steps"]
            if str(step.get("uses", "")).startswith("docker/login-action@")
        )
        self.assertEqual("needs.policy.outputs.release == 'true'", login["if"])

    def test_security_job_scans_digest_signs_bundle_and_attests_sbom(self):
        secure = self.workflow["jobs"]["secure-images"]
        self.assertEqual("build-images", secure["needs"][2])
        self.assertEqual(IMAGE_NAMES, set(secure["strategy"]["matrix"]["image"]))
        text = json.dumps(secure, sort_keys=True)
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
        self.assertIn("vulnerability-policy.json", text)

    def test_every_action_is_pinned_to_full_sha_or_an_explicit_stable_major(self):
        for job_name, job in self.workflow["jobs"].items():
            for step in action_steps(job):
                action, separator, ref = step["uses"].partition("@")
                with self.subTest(job=job_name, action=action):
                    self.assertEqual("@", separator)
                    self.assertTrue(FULL_SHA.fullmatch(ref) or ALLOWED_MAJOR.fullmatch(ref), ref)

    def test_versioned_vulnerability_policy_is_fail_closed(self):
        self.assertEqual(1, self.policy["schemaVersion"])
        self.assertRegex(self.policy["trivyVersion"], r"^v[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertEqual(["HIGH", "CRITICAL"], self.policy["blockSeverities"])
        self.assertFalse(self.policy["ignoreUnfixed"])
        self.assertIn("exceptions", self.policy)
        requirements = self.policy["exceptionRequirements"]
        self.assertEqual(
            {"id", "rootCause", "owner", "expiresAt"},
            set(requirements["requiredFields"]),
        )
        self.assertEqual(90, requirements["maximumLifetimeDays"])
        secure_text = json.dumps(self.workflow["jobs"]["secure-images"], sort_keys=True)
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
