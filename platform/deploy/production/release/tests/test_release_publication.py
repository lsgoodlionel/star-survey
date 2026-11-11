from __future__ import annotations

import json
import importlib.util
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import unicodedata

import yaml


ROOT = Path(__file__).resolve().parents[5]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
VERIFY_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "verify_release.sh"
RENDER_NOTES_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "render_notes.py"
RENDER_PUBLICATION_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "render_publication.py"
PUBLISH_RELEASE_PATH = ROOT / "platform" / "deploy" / "production" / "release" / "publish_release.sh"
STRICT_TAG = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-rc\.(?:0|[1-9][0-9]*))?$")


def load_workflow(path: Path = WORKFLOW_PATH) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in document and "on" not in document:
        document["on"] = document.pop(True)
    return document


def job_text(job: dict) -> str:
    return json.dumps(job, sort_keys=True)


def shell_without_heredocs(source: str) -> str:
    kept: list[str] = []
    delimiter: str | None = None
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


def assert_publication_contract(testcase: unittest.TestCase, workflow: dict) -> None:
    jobs = workflow["jobs"]
    publication = jobs["publication"]
    text = job_text(publication)
    testcase.assertEqual({"actions": "read", "contents": "write", "packages": "read"}, publication["permissions"])
    testcase.assertNotIn("id-token", publication["permissions"])
    testcase.assertNotIn("attestations", publication["permissions"])
    for dependency in (
        "policy", "quality", "publish-images", "secure-images", "candidate-assets",
        "native-acceptance", "release-ready", "attest-release-assets", "stable-promotion",
    ):
        testcase.assertIn(dependency, publication["needs"])
    for required in ("verify_release.sh", "render_publication.py", "publish_release.sh", "verified-release-assets"):
        testcase.assertIn(required, text)
    testcase.assertNotIn("--clobber", text)
    testcase.assertNotIn("continue-on-error", text)
    condition = str(publication["if"])
    for gate in (
        "needs.policy.outputs.release == 'true'",
        "needs.quality.result == 'success'",
        "needs.publish-images.result == 'success'",
        "needs.secure-images.result == 'success'",
        "needs.candidate-assets.result == 'success'",
        "needs.native-acceptance.result == 'success'",
        "needs.release-ready.result == 'success'",
        "needs.attest-release-assets.result == 'success'",
        "needs.stable-promotion.result == 'success'",
    ):
        testcase.assertIn(gate, condition)
    shell = "\n".join(
        shell_without_heredocs(str(step.get("run", ""))) for step in publication.get("steps", [])
    )
    testcase.assertNotRegex(shell, r"\|\|\s*(?:true|exit\s+0)\b")
    testcase.assertNotRegex(shell, r"\b(?:exit|return)\s+0\b")

    steps = publication.get("steps", [])
    named = {step.get("name"): step for step in steps}
    render_name = "Render reviewed channel-specific Release metadata"
    tag_name = "Validate publication tag commit identity"
    verify_name = "Reverify every asset immediately before publication"
    publish_name = "Create or reconcile the immutable Release without clobbering"
    testcase.assertEqual(1, sum(step.get("name") == tag_name for step in steps))
    testcase.assertEqual(1, sum(step.get("name") == render_name for step in steps))
    testcase.assertEqual(1, sum(step.get("name") == verify_name for step in steps))
    testcase.assertEqual(1, sum(step.get("name") == publish_name for step in steps))
    for name in (tag_name, render_name, verify_name, publish_name):
        step = named[name]
        testcase.assertNotIn("if", step)
        testcase.assertNotIn("continue-on-error", step)
        testcase.assertEqual("bash", step.get("shell"))

    testcase.assertEqual(
        'test "$(git rev-parse "$GITHUB_REF_NAME^{commit}")" = "$GITHUB_SHA"',
        named[tag_name]["run"],
    )

    render_argv = shlex.split(named[render_name]["run"])
    testcase.assertEqual([
        "python3", "platform/deploy/production/release/render_publication.py",
        "--assets", "publication-assets", "--repository", "$GITHUB_REPOSITORY",
        "--target-tag", "$GITHUB_REF_NAME", "--source-tag", "$SOURCE_TAG",
        "--target-commit", "$GITHUB_SHA", "--output-body", "publication-metadata/body.md",
        "--output-metadata", "publication-metadata/expected.json",
    ], render_argv)
    verify_argv = shlex.split(named[verify_name]["run"])
    testcase.assertEqual([
        "platform/deploy/production/release/verify_release.sh", "--assets", "publication-assets",
        "--repository", "$GITHUB_REPOSITORY", "--target-tag", "$GITHUB_REF_NAME",
        "--source-tag", "$SOURCE_TAG", "--target-commit", "$GITHUB_SHA",
    ], verify_argv)
    publish_argv = shlex.split(named[publish_name]["run"])
    testcase.assertEqual([
        "platform/deploy/production/release/publish_release.sh", "--assets", "publication-assets",
        "--metadata", "publication-metadata/expected.json", "--body", "publication-metadata/body.md",
        "--repository", "$GITHUB_REPOSITORY",
    ], publish_argv)
    testcase.assertEqual(steps.index(named[publish_name]) - 1, steps.index(named[verify_name]))


class ReleasePublicationWorkflowTest(unittest.TestCase):
    def test_real_registry_preflight_has_only_scoped_read_permissions(self):
        job = load_workflow(ROOT / '.github/workflows/production-smoke.yml')['jobs']['cosign-contract']
        self.assertEqual({'contents': 'read', 'packages': 'read'}, job['permissions'])
        text = job_text(job)
        self.assertIn('--real-registry', text)
        self.assertIn('docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9', text)

    def test_real_registry_preflight_binds_all_four_images_and_source(self):
        def success(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, 'GitVersion: v3.1.4', '')
        with patch('subprocess.run', side_effect=success) as run, patch('builtins.print'):
            real_registry_preflight()
        calls = [call.args[0] for call in run.call_args_list]
        signatures = [cmd for cmd in calls if cmd[:2] == ['cosign', 'verify']]
        attestations = [cmd for cmd in calls if cmd[:3] == ['gh', 'attestation', 'verify']]
        self.assertEqual(4, len(signatures))
        self.assertEqual(8, len(attestations))
        self.assertEqual({'admin', 'platform', 'publish-gateway', 'engine'},
                         {cmd[-1].split('star-survey-')[1].split('@')[0] for cmd in signatures})
        for cmd in attestations:
            self.assertEqual('refs/tags/v0.3.16-rc.1', cmd[cmd.index('--source-ref') + 1])
            self.assertEqual('ba2cd00091690173f2fe6a5f2014665a4a1dc9c8', cmd[cmd.index('--source-digest') + 1])
            self.assertIn('@sha256:', cmd[3])

    def test_real_registry_preflight_stops_on_signature_failure(self):
        def fail_signature(args, **kwargs):
            return subprocess.CompletedProcess(args, 1 if args[:2] == ['cosign', 'verify'] else 0,
                                               'GitVersion: v3.1.4\n', 'signature rejected')
        with patch('subprocess.run', side_effect=fail_signature) as run, patch('builtins.print'):
            with self.assertRaisesRegex(AssertionError, 'admin/registry-signature failed'):
                real_registry_preflight()
        self.assertFalse(any(call.args[0][:3] == ['gh', 'attestation', 'verify'] for call in run.call_args_list))

    @classmethod
    def setUpClass(cls):
        cls.workflow = load_workflow()

    def test_rc_and_stable_paths_are_separate_and_stable_never_rebuilds(self):
        jobs = self.workflow["jobs"]
        for name in ("publish-images", "secure-images", "candidate-assets", "native-acceptance", "release-ready", "attest-release-assets"):
            self.assertIn("needs.policy.outputs.channel == 'candidate'", str(jobs[name]["if"]))
        promotion = jobs["stable-promotion"]
        self.assertIn("needs.policy.outputs.channel == 'stable'", str(promotion["if"]))
        promotion_text = job_text(promotion)
        self.assertIn("gh release download", promotion_text)
        self.assertIn("-rc.", promotion_text)
        self.assertIn("verify_release.sh", promotion_text)
        for forbidden in ("docker/build-push-action@", "build_release.py", "cosign sign", "actions/attest@"):
            self.assertNotIn(forbidden, promotion_text)

    def test_asset_attestation_is_after_native_readiness_and_has_only_needed_permissions(self):
        job = self.workflow["jobs"]["attest-release-assets"]
        self.assertEqual({
            "actions": "read", "contents": "read", "id-token": "write",
            "attestations": "write", "artifact-metadata": "write", "packages": "read",
        }, job["permissions"])
        self.assertEqual({"policy", "candidate-assets", "secure-images", "release-ready"}, set(job["needs"]))
        text = job_text(job)
        for required in (
            "actions/attest@", "subject-path", "cosign sign-blob", "RELEASE_SHA256SUMS",
            "release-readiness.json", "sbom-admin.spdx.json", "sbom-platform.spdx.json",
            "sbom-publish-gateway.spdx.json", "cosign-admin.sigstore.json",
        ):
            self.assertIn(required, text)
        self.assertNotIn("contents\": \"write", text)

    def test_candidate_cannot_publish_before_explicit_promotion_is_implemented(self):
        condition = str(self.workflow["jobs"]["publication"]["if"]).strip()
        self.assertTrue(condition.startswith("false &&"), condition)

    def test_publication_is_tag_only_fail_closed_and_idempotent_without_clobber(self):
        assert_publication_contract(self, self.workflow)
        writers = {
            name for name, job in self.workflow["jobs"].items()
            if job.get("permissions", {}).get("contents") == "write"
        }
        self.assertEqual({"publication", "manual-promotion"}, writers)
        condition = str(self.workflow["jobs"]["publication"]["if"])
        self.assertIn("needs.policy.outputs.release == 'true'", condition)
        self.assertIn("needs.quality.result == 'success'", condition)
        self.assertIn("needs.release-ready.result == 'success'", condition)
        self.assertIn("needs.stable-promotion.result == 'success'", condition)

    def test_publication_contract_rejects_failure_bypasses_and_permission_expansion(self):
        mutations = []
        for snippet in (" || true", " || exit 0", "\nexit 0"):
            changed = json.loads(json.dumps(self.workflow))
            step = next(item for item in changed["jobs"]["publication"]["steps"] if "run" in item)
            step["run"] += snippet
            mutations.append(changed)
        for permission in ("id-token", "attestations", "packages"):
            changed = json.loads(json.dumps(self.workflow))
            changed["jobs"]["publication"]["permissions"][permission] = "write"
            mutations.append(changed)
        verify_name = "Reverify every asset immediately before publication"
        render_name = "Render reviewed channel-specific Release metadata"
        tag_name = "Validate publication tag commit identity"
        publish_name = "Create or reconcile the immutable Release without clobbering"
        for name, mutation in (
            (verify_name, lambda run: f"if false; then\n{run}\nfi"),
            (verify_name, lambda run: f"{run} || true"),
            (verify_name, lambda run: f"{run} &\nwait || true"),
            (verify_name, lambda run: "true"),
            (render_name, lambda run: run.replace("publication-metadata/body.md", "/tmp/unreviewed.md")),
            (publish_name, lambda run: run.replace("publication-metadata/body.md", "/tmp/unreviewed.md")),
        ):
            changed = json.loads(json.dumps(self.workflow))
            step = next(item for item in changed["jobs"]["publication"]["steps"] if item.get("name") == name)
            step["run"] = mutation(step["run"])
            mutations.append(changed)
        for name in (tag_name, verify_name, render_name, publish_name):
            changed = json.loads(json.dumps(self.workflow))
            step = next(item for item in changed["jobs"]["publication"]["steps"] if item.get("name") == name)
            step["if"] = "false"
            mutations.append(changed)
            changed = json.loads(json.dumps(self.workflow))
            step = next(item for item in changed["jobs"]["publication"]["steps"] if item.get("name") == name)
            step["continue-on-error"] = True
            mutations.append(changed)
        for gate in (
            "needs.quality.result == 'success'", "needs.publish-images.result == 'success'",
            "needs.secure-images.result == 'success'", "needs.candidate-assets.result == 'success'",
            "needs.native-acceptance.result == 'success'", "needs.release-ready.result == 'success'",
            "needs.attest-release-assets.result == 'success'", "needs.stable-promotion.result == 'success'",
        ):
            changed = json.loads(json.dumps(self.workflow))
            changed["jobs"]["publication"]["if"] = changed["jobs"]["publication"]["if"].replace(gate, "true")
            mutations.append(changed)
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index), self.assertRaises(AssertionError):
                assert_publication_contract(self, mutation)

    def test_upgrade_instructions_use_verified_target_bundle_cli(self):
        for path in (RENDER_NOTES_PATH, RENDER_PUBLICATION_PATH):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                upgrade = text.split("## Upgrade", 1)[1].split("## ", 1)[0]
                self.assertIn("Verify the target release assets", upgrade)
                self.assertIn("matching architecture bundle", upgrade)
                self.assertIn("Do not use the installed release's older CLI", upgrade)
                self.assertIn("./surveyctl upgrade --manifest /path/to/verified/release.json", upgrade)

    def test_release_notes_cover_operational_limits_and_verification_commands(self):
        text = RENDER_NOTES_PATH.read_text(encoding="utf-8") + RENDER_PUBLICATION_PATH.read_text(encoding="utf-8")
        for phrase in (
            "Known issues", "single-host", "not high availability", "verify_release.sh",
            "gh attestation verify", "cosign verify", "surveyctl install", "surveyctl upgrade",
            "restore-only",
        ):
            self.assertIn(phrase, text)


class PublicationMetadataAndReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.commit = "a" * 40
        manifest = {
            "version": "1.2.3-rc.4", "commit": self.commit,
            "minimumSourceVersion": "1.1.0",
            "database": {"rollback": "restore-only"},
            "assets": {"bundles": {
                "amd64": {"name": "survey-1.2.3-rc.4-linux-amd64.tar.gz", "sha256": "1" * 64},
                "arm64": {"name": "survey-1.2.3-rc.4-linux-arm64.tar.gz", "sha256": "2" * 64},
            }},
            "images": {
                "ADMIN_IMAGE": "ghcr.io/example/admin@sha256:" + "3" * 64,
                "PLATFORM_IMAGE": "ghcr.io/example/platform@sha256:" + "4" * 64,
                "PUBLISH_GATEWAY_IMAGE": "ghcr.io/example/gateway@sha256:" + "5" * 64,
                "ENGINE_IMAGE": "ghcr.io/example/engine@sha256:" + "6" * 64,
            },
        }
        readiness = {"promotable": True, "reason": "upgrade-verified", "candidateCommit": self.commit}
        (self.assets / "release.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        (self.assets / "release-readiness.json").write_text(json.dumps(readiness) + "\n", encoding="utf-8")
        # Candidate notes rendered at build time by the candidate's own source; a
        # controller with the controlled-recovery block says so in these notes.
        (self.assets / "release-notes.md").write_text(
            "This release declares rollback policy `restore-only`, but recovery is currently disabled.\n",
            encoding="utf-8")
        (self.assets / "asset.bin").write_bytes(b"immutable")

    def tearDown(self):
        self.temporary.cleanup()

    def render(self, target: str, source: str) -> tuple[Path, Path, dict]:
        output = self.root / target
        body = output / "body.md"
        metadata = output / "expected.json"
        result = subprocess.run([
            "python3", str(RENDER_PUBLICATION_PATH), "--assets", str(self.assets),
            "--repository", "example/project", "--target-tag", target, "--source-tag", source,
            "--target-commit", self.commit, "--output-body", str(body),
            "--output-metadata", str(metadata),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        return body, metadata, json.loads(metadata.read_text(encoding="utf-8"))

    def test_verified_identity_lists_every_application_image_digest(self):
        # The engine image is pinned in release.json and verified by verify_release.sh;
        # the published notes must let operators compare all four application digests.
        manifest = json.loads((self.assets / "release.json").read_text(encoding="utf-8"))
        body, _, _ = self.render("v1.2.3-rc.4", "v1.2.3-rc.4")
        text = body.read_text(encoding="utf-8")
        for key in ("ADMIN_IMAGE", "PLATFORM_IMAGE", "PUBLISH_GATEWAY_IMAGE", "ENGINE_IMAGE"):
            with self.subTest(image=key):
                self.assertIn(f"- `{key}`: `{manifest['images'][key]}`", text)
        del manifest["images"]["ENGINE_IMAGE"]
        (self.assets / "release.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        result = subprocess.run([
            "python3", str(RENDER_PUBLICATION_PATH), "--assets", str(self.assets),
            "--repository", "example/project", "--target-tag", "v1.2.3-rc.4", "--source-tag", "v1.2.3-rc.4",
            "--target-commit", self.commit, "--output-body", str(self.root / "missing.md"),
            "--output-metadata", str(self.root / "missing.json"),
        ], text=True, capture_output=True, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "missing.md").exists())

    def test_rc_and_stable_metadata_are_channel_specific_and_commit_bound(self):
        rc_body, _, rc = self.render("v1.2.3-rc.4", "v1.2.3-rc.4")
        stable_body, _, stable = self.render("v1.2.3", "v1.2.3-rc.4")
        rc_text = rc_body.read_text(encoding="utf-8")
        stable_text = stable_body.read_text(encoding="utf-8")
        self.assertEqual("Star Survey v1.2.3-rc.4", rc["name"])
        self.assertTrue(rc["isPrerelease"])
        self.assertEqual("Star Survey v1.2.3", stable["name"])
        self.assertFalse(stable["isPrerelease"])
        self.assertNotEqual(rc["bodySha256"], stable["bodySha256"])
        for required in (
            "Stable promotion", "v1.2.3-rc.4", "same bytes and digests", "upgradeVerified: `true`",
            "--target-tag v1.2.3", "--source-tag v1.2.3-rc.4", f"--target-commit {self.commit}",
        ):
            self.assertIn(required, stable_text)
        self.assertIn("--target-tag v1.2.3-rc.4", rc_text)
        self.assertNotIn("Stable promotion", rc_text)
        for body in (rc_text, stable_text):
            self.assertIn("recovery is currently disabled", body)
            self.assertIn("Preserve the installation, operation marker and verified pre-upgrade backup", body)
            self.assertIn("Do not run older images against a migrated database or bypass the block with an older controller or helper", body)
            self.assertNotIn("restore the verified pre-upgrade backup", body)

    def test_recovery_guidance_follows_the_candidate_controller_not_the_promoting_source(self):
        # rc19 was built before the restore block: its controller still restores, and a
        # failed restore-only upgrade restores automatically. The published body must not
        # claim recovery is disabled for such a candidate.
        (self.assets / "release-notes.md").write_text(
            "This release declares rollback policy `restore-only`. When it is `restore-only`, recover with "
            "the verified pre-upgrade backup instead of starting older images against a migrated database.\n",
            encoding="utf-8")
        for target in ("v1.2.3-rc.4", "v1.2.3"):
            with self.subTest(target=target):
                body, _, _ = self.render(target, "v1.2.3-rc.4")
                text = body.read_text(encoding="utf-8")
                self.assertNotIn("recovery is currently disabled", text)
                self.assertIn("this candidate's controller still runs recovery", text)
                self.assertIn("a failed upgrade automatically restores the pre-upgrade backup", text)
                self.assertIn("Preserve the installation, operation marker and verified pre-upgrade backup", text)
                self.assertIn("Do not run older images against a migrated database", text)
        (self.assets / "release-notes.md").unlink()
        result = subprocess.run([
            "python3", str(RENDER_PUBLICATION_PATH), "--assets", str(self.assets),
            "--repository", "example/project", "--target-tag", "v1.2.3-rc.4", "--source-tag", "v1.2.3-rc.4",
            "--target-commit", self.commit, "--output-body", str(self.root / "nonotes.md"),
            "--output-metadata", str(self.root / "nonotes.json"),
        ], text=True, capture_output=True, check=False)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "nonotes.md").exists())

    def test_renderer_rejects_stable_tag_or_readiness_identity_drift(self):
        for target, source, commit in (
            ("v1.2.4", "v1.2.3-rc.4", self.commit),
            ("v1.2.3", "v1.2.3-rc.4", "b" * 40),
        ):
            result = subprocess.run([
                "python3", str(RENDER_PUBLICATION_PATH), "--assets", str(self.assets),
                "--repository", "example/project", "--target-tag", target, "--source-tag", source,
                "--target-commit", commit, "--output-body", str(self.root / "bad.md"),
                "--output-metadata", str(self.root / "bad.json"),
            ], text=True, capture_output=True, check=False)
            self.assertNotEqual(0, result.returncode)

    def make_fake_gh(self, release: dict | None, *, create_unknown: bool = False) -> tuple[dict[str, str], Path, Path]:
        state = self.root / "gh-state.json"
        remote = self.root / "remote"
        remote.mkdir(exist_ok=True)
        state.write_text(json.dumps({"release": release, "createUnknown": create_unknown}) + "\n", encoding="utf-8")
        log = self.root / "gh.log"
        log.write_text("", encoding="utf-8")
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir(exist_ok=True)
        gh = fake_bin / "gh"
        gh.write_text("""#!/usr/bin/env python3
import json, os, shutil, sys
from pathlib import Path
state_path, remote, log = map(Path, (os.environ['GH_STATE'], os.environ['GH_REMOTE'], os.environ['GH_LOG']))
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with log.open('a') as handle: handle.write(json.dumps(args) + '\\n')
release = state.get('release')
def save(): state_path.write_text(json.dumps(state) + '\\n')
if args[:2] == ['release', 'view']:
    if release is None: raise SystemExit(1)
    print(json.dumps(release)); raise SystemExit(0)
if args[:2] == ['release', 'download']:
    name = args[args.index('--pattern') + 1]; destination = Path(args[args.index('--dir') + 1])
    destination.mkdir(parents=True, exist_ok=True); shutil.copy2(remote / name, destination / name); raise SystemExit(0)
if args[:2] == ['release', 'upload']:
    path = Path(args[3]); shutil.copy2(path, remote / path.name)
    release['assets'].append({'name': path.name}); state['release'] = release; save(); raise SystemExit(0)
if args[:2] == ['release', 'create']:
    title = args[args.index('--title') + 1]; notes = Path(args[args.index('--notes-file') + 1]).read_text()
    target = args[args.index('--target') + 1]
    paths = []
    index = 3
    while index < len(args):
        value = args[index]
        if value in {'--title', '--notes-file', '--target', '--repo'}: index += 2; continue
        if value.startswith('--'): index += 1; continue
        paths.append(Path(value)); index += 1
    for path in paths: shutil.copy2(path, remote / path.name)
    release = {'tagName': args[2], 'name': title, 'body': notes, 'isDraft': True,
               'isPrerelease': '--prerelease' in args, 'targetCommitish': target,
               'assets': [{'name': path.name} for path in paths]}
    state['release'] = release; save()
    raise SystemExit(1 if state.get('createUnknown') else 0)
if args[:2] == ['release', 'edit']:
    if '--title' in args: release['name'] = args[args.index('--title') + 1]
    if '--notes-file' in args: release['body'] = Path(args[args.index('--notes-file') + 1]).read_text()
    if '--prerelease' in args: release['isPrerelease'] = args[args.index('--prerelease') + 1] == 'true'
    for value in args:
        if value.startswith('--prerelease='): release['isPrerelease'] = value.split('=', 1)[1] == 'true'
    if '--draft' in args: release['isDraft'] = args[args.index('--draft') + 1] == 'true'
    for value in args:
        if value.startswith('--draft='): release['isDraft'] = value.split('=', 1)[1] == 'true'
    state['release'] = release; save(); raise SystemExit(0)
raise SystemExit('unsupported fake gh command: ' + repr(args))
""", encoding="utf-8")
        gh.chmod(0o755)
        python = fake_bin / "python3"
        python.write_text("#!/bin/sh\ncase \"$1\" in *candidate_provenance.py) printf 'gate-check\\n' >> \"$GH_LOG\"; [ -z \"${GATE_FAIL_REASON:-}\" ] && exit 0; exit 1;; esac\nexec " + sys.executable + " \"$@\"\n", encoding="utf-8")
        python.chmod(0o755)
        return {
            "PATH": f"{fake_bin}:{os.environ['PATH']}", "GH_STATE": str(state),
            "GH_REMOTE": str(remote), "GH_LOG": str(log),
        }, state, log

    def run_publisher(self, body: Path, metadata: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            str(PUBLISH_RELEASE_PATH), "--assets", str(self.assets), "--metadata", str(metadata),
            "--body", str(body), "--repository", "example/project", "--run-id", "123",
            "--manifest-sha256", "a" * 64, "--approval-comment-id", "124", "--evidence-comment-id", "125",
        ], text=True, capture_output=True, check=False, env={**os.environ, **environment})

    def test_existing_draft_repairs_reviewed_metadata_then_publishes(self):
        body, metadata, expected = self.render("v1.2.3", "v1.2.3-rc.4")
        release = {"tagName": expected["tagName"], "name": "wrong", "body": "unsafe\r\n",
                   "isDraft": True, "isPrerelease": True, "targetCommitish": self.commit,
                   "assets": [{"name": "asset.bin"}]}
        environment, state, log = self.make_fake_gh(release)
        shutil.copy2(self.assets / "asset.bin", Path(environment["GH_REMOTE"]) / "asset.bin")
        result = self.run_publisher(body, metadata, environment)
        self.assertEqual(0, result.returncode, result.stderr)
        final = json.loads(state.read_text())["release"]
        self.assertEqual(expected["name"], final["name"])
        self.assertEqual(body.read_text(), final["body"])
        self.assertFalse(final["isDraft"])
        self.assertFalse(final["isPrerelease"])
        self.assertIn("release\", \"edit", log.read_text())

    def test_existing_published_metadata_or_target_drift_fails_without_edit(self):
        body, metadata, expected = self.render("v1.2.3", "v1.2.3-rc.4")
        for field, value in (
            ("name", "wrong"), ("body", "wrong\n"), ("isPrerelease", True),
            ("isDraft", None), ("targetCommitish", "b" * 40),
        ):
            with self.subTest(field=field):
                release = {"tagName": expected["tagName"], "name": expected["name"], "body": body.read_text(),
                           "isDraft": False, "isPrerelease": False, "targetCommitish": self.commit,
                           "assets": [{"name": "asset.bin"}]}
                release[field] = value
                environment, _, log = self.make_fake_gh(release)
                shutil.copy2(self.assets / "asset.bin", Path(environment["GH_REMOTE"]) / "asset.bin")
                result = self.run_publisher(body, metadata, environment)
                self.assertNotEqual(0, result.returncode)
                self.assertNotIn("release\", \"edit", log.read_text())

    def test_revocation_or_tag_move_during_upload_leaves_draft_private(self):
        body, metadata, _ = self.render("v1.2.3-rc.4", "v1.2.3-rc.4")
        for reason in ("approval-revoked", "tag-moved"):
            with self.subTest(reason=reason):
                environment, state, log = self.make_fake_gh(None)
                environment["GATE_FAIL_REASON"] = reason
                result = self.run_publisher(body, metadata, environment)
                self.assertNotEqual(0, result.returncode)
                draft = json.loads(state.read_text())["release"]
                self.assertTrue(draft["isDraft"])
                self.assertEqual(len(list(self.assets.iterdir())), len(draft["assets"]))
                self.assertIn("gate-check", log.read_text())
                self.assertNotIn("--draft=false", log.read_text())

    def test_unknown_create_outcome_is_reconciled_without_clobber(self):
        body, metadata, expected = self.render("v1.2.3-rc.4", "v1.2.3-rc.4")
        environment, state, log = self.make_fake_gh(None, create_unknown=True)
        result = self.run_publisher(body, metadata, environment)
        self.assertEqual(0, result.returncode, result.stderr)
        final = json.loads(state.read_text())["release"]
        self.assertFalse(final["isDraft"])
        self.assertEqual(expected["name"], final["name"])
        self.assertLess(log.read_text().index("gate-check"), log.read_text().index("--draft=false"))
        self.assertNotIn("--clobber", log.read_text())


class VerifyReleaseScriptTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def make_fixture(self, *, promotable: bool = True) -> tuple[Path, dict[str, str]]:
        assets = self.root / "assets"
        assets.mkdir()
        version = "1.2.3-rc.1"
        raw_manifest = b"oci-index"
        image_digest = "sha256:" + hashlib.sha256(raw_manifest).hexdigest()
        bundles = {}
        for architecture in ("amd64", "arm64"):
            name = f"survey-{version}-linux-{architecture}.tar.gz"
            (assets / name).write_bytes(f"bundle-{architecture}".encode())
            bundles[architecture] = {"name": name, "sha256": hashlib.sha256((assets / name).read_bytes()).hexdigest()}
        images = {
            "CADDY_IMAGE": "example.invalid/caddy@sha256:" + "1" * 64,
            "ADMIN_IMAGE": f"ghcr.io/example/admin@{image_digest}",
            "PLATFORM_IMAGE": f"ghcr.io/example/platform@{image_digest}",
            "PUBLISH_GATEWAY_IMAGE": f"ghcr.io/example/gateway@{image_digest}",
            "ENGINE_IMAGE": f"ghcr.io/example/engine@{image_digest}",
            "POSTGRES_IMAGE": "example.invalid/postgres@sha256:" + "3" * 64,
            "MARIADB_IMAGE": "example.invalid/mariadb@sha256:" + "4" * 64,
        }
        manifest = {
            "schemaVersion": 1,
            "version": version,
            "channel": "candidate",
            "commit": "a" * 40,
            "supportedHosts": {"ubuntu": ["22.04", "24.04"], "architectures": ["amd64", "arm64"]},
            "minimumSourceVersion": "1.1.0",
            "nativeAcceptance": {
                "profile": "github-actions-native-v1", "minimumFreeBytes": 8589934592,
                "tlsMode": "local-ca", "diskEvidence": "separately-tested",
            },
            "database": {"schema": "932", "backupSchema": 2, "rollback": "restore-only", "compatibleSourceSchemas": ["932"]},
            "images": images,
            "assets": {"bundles": bundles},
        }
        (assets / "release.json").write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        (assets / "release-notes.md").write_text("release notes\n", encoding="utf-8")
        internal_names = sorted([record["name"] for record in bundles.values()] + ["release.json", "release-notes.md"])
        (assets / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((assets / name).read_bytes()).hexdigest()}  {name}\n" for name in internal_names
        ), encoding="ascii")
        # Feed the real readiness producer into the final verifier. Keeping a
        # second hand-written schema here hid the matrix-key contract mismatch.
        helper_path = ROOT / "platform/deploy/production/release/native_acceptance_evidence.py"
        spec = importlib.util.spec_from_file_location("publication_native_evidence", helper_path)
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        manifest_sha = hashlib.sha256((assets / "release.json").read_bytes()).hexdigest()
        image_keys = {"admin": "ADMIN_IMAGE", "platform": "PLATFORM_IMAGE",
                      "publish-gateway": "PUBLISH_GATEWAY_IMAGE", "engine": "ENGINE_IMAGE"}
        mode = "predecessor" if promotable else "bootstrap"
        identity = {"version": version, "commit": "a" * 40, "manifestSha256": manifest_sha,
                    "bundles": bundles, "baseline": {"mode": mode},
                    "images": {name: {"reference": images[key], "digest": image_digest}
                               for name, key in image_keys.items()}}
        summaries = []
        hashes = {}
        for ubuntu, architecture in sorted(native.RUNNERS):
            summary = {
                "schemaVersion": 1, "status": "passed", "exitCode": 0, "lastStep": "uninstall",
                "runner": {"ubuntu": ubuntu, "architecture": architecture},
                "candidate": {"manifestSha256": manifest_sha, "bundleSha256": bundles[architecture]["sha256"],
                              "images": {name: image_digest for name in image_keys}},
                "baselineMode": mode, "upgradeVerified": promotable,
                "tls": {"scope": "ci-local-trusted-ca", "httpsMarkerVerified": True, "publicAcmeVerified": False},
                "preflightDisk": "separately-tested", "completedAt": "2026-11-03T00:00:00+00:00",
            }
            summaries.append(summary)
            hashes[native.MATRIX_EVIDENCE_KEYS[(ubuntu, architecture)]] = hashlib.sha256(
                json.dumps(summary, sort_keys=True).encode()).hexdigest()
        native.write_release_readiness(identity, summaries, hashes, assets / "release-readiness.json")
        for name in ("admin", "platform", "publish-gateway", "engine"):
            (assets / f"sbom-{name}.spdx.json").write_text(
                json.dumps({"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT"}) + "\n", encoding="utf-8"
            )
            (assets / f"cosign-{name}.sigstore.json").write_text("{}\n", encoding="utf-8")
        top_names = sorted(path.name for path in assets.iterdir())
        (assets / "RELEASE_SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((assets / name).read_bytes()).hexdigest()}  {name}\n" for name in top_names
        ), encoding="ascii")
        (assets / "cosign-release.sigstore.json").write_text("{}\n", encoding="utf-8")

        fake_bin = self.root / "bin"
        fake_bin.mkdir(exist_ok=True)
        log = self.root / "commands.log"
        log.write_text("", encoding="utf-8")
        for name, body in {
            "cosign": '''#!/bin/sh
printf "cosign %s\\n" "$*" >> "$VERIFY_LOG"
if [ "$1" = verify ]; then
  for arg in "$@"; do
    [ "$arg" != --bundle ] || { echo 'unknown flag: --bundle' >&2; exit 1; }
  done
fi
case "$*" in
  *cosign-admin.sigstore.json*)
    [ "$1" = verify-blob-attestation ] || exit 1
    [ "${FAIL_IMAGE_BUNDLE:-0}" != 1 ] || exit 1 ;;
esac
''',
            "gh": '#!/bin/sh\nprintf "gh %s\\n" "$*" >> "$VERIFY_LOG"\n',
            "docker": '#!/bin/sh\nprintf "docker %s\\n" "$*" >> "$VERIFY_LOG"\nprintf oci-index\n',
        }.items():
            path = fake_bin / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        return assets, {"PATH": f"{fake_bin}:{os.environ['PATH']}", "VERIFY_LOG": str(log)}

    def run_verifier(
        self, assets: Path, environment: dict[str, str], target: str = "v1.2.3-rc.1", target_commit: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            str(VERIFY_PATH), "--assets", str(assets), "--repository", "example/project",
            "--target-tag", target, "--source-tag", "v1.2.3-rc.1",
        ]
        if target_commit is not None:
            command.extend(["--target-commit", target_commit])
        return subprocess.run(
            command,
            text=True, capture_output=True, check=False, env={**os.environ, **environment},
        )

    def test_script_has_strict_syntax_and_required_verification_commands(self):
        result = subprocess.run(["bash", "-n", str(VERIFY_PATH)], text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        text = VERIFY_PATH.read_text(encoding="utf-8")
        self.assertIn("set -Eeuo pipefail", text)
        for required in (
            "sha256sum --check", "RELEASE_SHA256SUMS", "release.json", "release-readiness.json",
            "cosign verify-blob", "cosign verify", "gh attestation verify", "docker buildx imagetools inspect",
            "sbom-admin.spdx.json", "sbom-platform.spdx.json", "sbom-publish-gateway.spdx.json",
        ):
            self.assertIn(required, text)
        shell = shell_without_heredocs(text)
        self.assertNotRegex(shell, r"\|\|\s*(?:true|exit\s+0)\b")
        self.assertNotRegex(shell, r"\b(?:skip|bypass|disable)_verification\b")

    def test_strict_tag_expression_rejects_noncanonical_versions(self):
        for valid in ("v0.1.0", "v1.2.3", "v1.2.3-rc.0", "v10.20.30-rc.42"):
            self.assertRegex(valid, STRICT_TAG)
        for invalid in ("1.2.3", "v01.2.3", "v1.02.3", "v1.2.03", "v1.2.3-rc.01", "v1.2.3+build"):
            self.assertNotRegex(invalid, STRICT_TAG)

    def test_complete_candidate_and_stable_promotion_are_verified(self):
        assets, environment = self.make_fixture()
        candidate = self.run_verifier(assets, environment)
        self.assertEqual(0, candidate.returncode, candidate.stderr)
        stable = self.run_verifier(assets, environment, target="v1.2.3")
        self.assertEqual(0, stable.returncode, stable.stderr)
        commands = Path(environment["VERIFY_LOG"]).read_text(encoding="utf-8")
        self.assertEqual(18, commands.count("cosign "))
        self.assertNotIn('cosign generate ', commands)
        self.assertEqual(2, commands.count("cosign verify-blob "))
        self.assertEqual(8, commands.count("cosign verify-blob-attestation "))
        self.assertEqual(8, sum(line.startswith('cosign verify ') for line in commands.splitlines()))
        for line in commands.splitlines():
            if line.startswith(('cosign verify ', 'cosign verify-blob ', 'cosign verify-blob-attestation ')):
                self.assertIn('--certificate-identity https://github.com/example/project/.github/workflows/release.yml@refs/tags/v1.2.3-rc.1', line)
                self.assertIn('--certificate-oidc-issuer https://token.actions.githubusercontent.com', line)
        self.assertEqual(36, commands.count("gh attestation verify"))
        self.assertEqual(8, commands.count("docker buildx imagetools inspect"))
        self.assertEqual(2, commands.count("docker buildx imagetools inspect ghcr.io/example/engine@"))
        self.assertEqual(4, commands.count("gh attestation verify oci://ghcr.io/example/engine@"))
        self.assertEqual(2, sum("gh attestation verify " in line and "sbom-engine.spdx.json" in line
                                for line in commands.splitlines()))

    def test_image_bundle_verification_failure_stops_before_registry_and_attestations(self):
        assets, environment = self.make_fixture()
        result = self.run_verifier(assets, {**environment, 'FAIL_IMAGE_BUNDLE': '1'})
        self.assertNotEqual(0, result.returncode)
        commands = Path(environment['VERIFY_LOG']).read_text()
        self.assertIn('cosign verify-blob-attestation --bundle ', commands)
        self.assertIn('cosign-admin.sigstore.json', commands)
        self.assertNotIn('gh attestation verify', commands)

    def test_signed_image_digest_and_predicate_claims_are_explicit(self):
        assets, environment = self.make_fixture()
        result = self.run_verifier(assets, environment)
        self.assertEqual(0, result.returncode, result.stderr)
        commands = Path(environment['VERIFY_LOG']).read_text()
        expected = hashlib.sha256(b'oci-index').hexdigest()
        lines = [line for line in commands.splitlines() if line.startswith('cosign verify-blob-attestation ')]
        self.assertEqual(4, len(lines))
        for line in lines:
            self.assertIn(f'--digest {expected} --digestAlg sha256', line)
            self.assertIn('--type https://sigstore.dev/cosign/sign/v1', line)
            self.assertIn('--check-claims=true', line)

    def test_complete_bootstrap_assets_are_a_baseline_but_not_stable_promotion(self):
        assets, environment = self.make_fixture(promotable=False)
        candidate = self.run_verifier(assets, environment)
        self.assertEqual(0, candidate.returncode, candidate.stderr)
        report = json.loads((assets / "release-readiness.json").read_text())
        self.assertEqual("bootstrap", report["baselineMode"])
        self.assertFalse(report["promotable"])
        self.assertEqual("bootstrap-not-promotable", report["reason"])
        stable = self.run_verifier(assets, environment, target="v1.2.3")
        self.assertNotEqual(0, stable.returncode)
        self.assertIn("stable promotion requires upgrade-verified native readiness", stable.stderr)

    def test_matrix_identity_requires_exact_producer_keys_and_hashes(self):
        for mutation in ("hyphen-alias", "missing", "extra", "invalid-hash"):
            with self.subTest(mutation=mutation):
                assets, environment = self.make_fixture()
                path = assets / "release-readiness.json"
                report = json.loads(path.read_text())
                hashes = report["matrixEvidenceSha256"]
                if mutation == "hyphen-alias":
                    report["matrixEvidenceSha256"] = {key.replace(".", "-"): value for key, value in hashes.items()}
                elif mutation == "missing":
                    del hashes["ubuntu-22.04-amd64"]
                elif mutation == "extra":
                    hashes["ubuntu-20.04-amd64"] = "9" * 64
                else:
                    hashes["ubuntu-22.04-amd64"] = "not-a-sha256"
                path.write_text(json.dumps(report))
                rejected = self.run_verifier(assets, environment)
                self.assertNotEqual(0, rejected.returncode)
                self.assertIn("native readiness evidence identity is incomplete", rejected.stderr)
                shutil.rmtree(assets)

    def test_engine_inventory_and_readiness_are_required(self):
        for mutation in ("missing-sbom", "missing-signature", "missing-readiness-digest", "wrong-readiness-digest"):
            with self.subTest(mutation=mutation):
                assets, environment = self.make_fixture()
                if mutation == "missing-sbom":
                    (assets / "sbom-engine.spdx.json").unlink()
                elif mutation == "missing-signature":
                    (assets / "cosign-engine.sigstore.json").unlink()
                else:
                    path = assets / "release-readiness.json"
                    report = json.loads(path.read_text())
                    if mutation == "missing-readiness-digest":
                        del report["engineManifestDigest"]
                    else:
                        report["engineManifestDigest"] = "sha256:" + "9" * 64
                    path.write_text(json.dumps(report))
                result = self.run_verifier(assets, environment)
                self.assertNotEqual(0, result.returncode)
                shutil.rmtree(assets)

    def test_tampering_unknown_assets_and_unpromotable_stable_fail_closed(self):
        assets, environment = self.make_fixture()
        (assets / "release.json").write_text("{}\n", encoding="utf-8")
        result = self.run_verifier(assets, environment)
        self.assertNotEqual(0, result.returncode)

        shutil.rmtree(assets)
        assets, environment = self.make_fixture()
        (assets / "unexpected.secret").write_text("no\n", encoding="utf-8")
        result = self.run_verifier(assets, environment)
        self.assertNotEqual(0, result.returncode)

        shutil.rmtree(assets)
        assets, environment = self.make_fixture(promotable=False)
        result = self.run_verifier(assets, environment, target="v1.2.3")
        self.assertNotEqual(0, result.returncode)

        shutil.rmtree(assets)
        assets, environment = self.make_fixture()
        result = self.run_verifier(assets, environment, target_commit="b" * 40)
        self.assertNotEqual(0, result.returncode)

    def test_every_untracked_entry_type_and_path_collision_fails_closed(self):
        mutators = {
            "directory": lambda assets: (assets / "extra").mkdir(),
            "nested-file": lambda assets: ((assets / "extra").mkdir(), (assets / "extra" / "run.sh").write_text("x")),
            "symlink": lambda assets: (assets / "extra-link").symlink_to("release.json"),
            "hardlink": lambda assets: os.link(assets / "release.json", assets / "extra-hardlink"),
            "fifo": lambda assets: os.mkfifo(assets / "extra-fifo"),
            "case-collision": lambda assets: (assets / "Release.JSON").write_text("{}\n"),
            "unicode-collision": lambda assets: (
                (assets / unicodedata.normalize("NFC", "releas\N{COMBINING ACUTE ACCENT}.json")).write_text("x"),
                (assets / unicodedata.normalize("NFD", "releas\N{COMBINING ACUTE ACCENT}.json")).write_text("x"),
            ),
        }
        for name, mutate in mutators.items():
            with self.subTest(name=name):
                if (self.root / "assets").exists():
                    shutil.rmtree(self.root / "assets")
                assets, environment = self.make_fixture()
                mutate(assets)
                result = self.run_verifier(assets, environment)
                self.assertNotEqual(0, result.returncode, f"{name} accepted")

    def test_socket_checksum_self_reference_and_duplicate_entry_fail_closed(self):
        assets, environment = self.make_fixture()
        endpoint = socket.socket(socket.AF_UNIX)
        try:
            try:
                endpoint.bind(str(assets / "extra.sock"))
            except PermissionError:
                pass
            else:
                self.assertNotEqual(0, self.run_verifier(assets, environment).returncode)
        finally:
            endpoint.close()

        for mutation in ("self", "duplicate"):
            shutil.rmtree(assets)
            assets, environment = self.make_fixture()
            checksum = assets / "RELEASE_SHA256SUMS"
            if mutation == "self":
                checksum.write_text(checksum.read_text() + f"{'0' * 64}  RELEASE_SHA256SUMS\n")
            else:
                first = checksum.read_text().splitlines()[0]
                checksum.write_text(checksum.read_text() + first + "\n")
            self.assertNotEqual(0, self.run_verifier(assets, environment).returncode)


def real_registry_preflight() -> None:
    """Read existing rc16 image evidence only; never build, push or qualify rc16."""
    version = subprocess.run(['cosign', 'version'], text=True, capture_output=True, timeout=30, check=True)
    assert re.search(r'GitVersion:\s+v3\.1\.4\b', version.stdout + version.stderr), 'expected pinned Cosign v3.1.4'
    gh_version = subprocess.run(['gh', 'version'], text=True, capture_output=True, timeout=30, check=True)
    print(json.dumps({'scope': 'existing rc16 registry evidence only; not candidate acceptance',
                      'cosignVersion': 'v3.1.4', 'ghVersion': gh_version.stdout.splitlines()[0]}), flush=True)
    digests = {
        'admin': 'a39d53bc1d1344b93408faa4230222a1e27612b2be18598f39e953a4085607c5',
        'platform': '6a7558d70d74bb07ca9b1a1a6a2b5f94eae8acac9c911e91baea08cb4b97f926',
        'publish-gateway': 'b42077d4cfea1bf6db3a02d6c635c75dd954176e1ce700bfc0de7ecfce0cf625',
        'engine': '972f686221824c78a5b1d330e156bcd3be78e51aba41309b01009f6cdf94895f',
    }
    identity = 'https://github.com/lsgoodlionel/star-survey/.github/workflows/release.yml@refs/tags/v0.3.16-rc.1'
    for name, digest in digests.items():
        reference = f'ghcr.io/lsgoodlionel/star-survey-{name}@sha256:{digest}'
        bindings = {'$reference': reference, 'oci://$reference': f'oci://{reference}',
                    '$workflow_identity': identity, '$issuer': 'https://token.actions.githubusercontent.com',
                    '$repository': 'lsgoodlionel/star-survey', '$source_ref': 'refs/tags/v0.3.16-rc.1',
                    '$repository/.github/workflows/release.yml': 'lsgoodlionel/star-survey/.github/workflows/release.yml',
                    '$source_commit': 'ba2cd00091690173f2fe6a5f2014665a4a1dc9c8'}
        commands = {}
        for kind, marker, occurrence in [('registry-signature', '  cosign verify ', 0),
                                          ('provenance', '  gh attestation verify ', 0),
                                          ('sbom', '  gh attestation verify ', 1)]:
            body = VERIFY_PATH.read_text().split(marker)[occurrence + 1].split(' >/dev/null', 1)[0]
            tokens = shlex.split(marker.strip() + ' ' + body.replace('\\\n', ' '))
            commands[kind] = [bindings.get(token, token) for token in tokens]
            assert not any('$' in token for token in commands[kind]), 'unresolved verifier command variable'
        for kind, command in commands.items():
            result = subprocess.run(command, text=True, capture_output=True, timeout=120, check=False)
            assert result.returncode == 0, f'{name}/{kind} failed: {result.stderr.splitlines()[-1:]}'
            print(json.dumps({'image': name, 'check': kind, 'exitCode': result.returncode}), flush=True)


def real_cosign_preflight(bundle: Path) -> None:
    """Verify the original rc16 DSSE bundle with the actual pinned CLI, no stubs."""
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == '0206b44a1602d4fd45413266ec3c0df17fb2ce056f9492b20fbcba9f98a9fce9'
    version = subprocess.run(['cosign', 'version'], text=True, capture_output=True, timeout=30, check=True)
    assert re.search(r'GitVersion:\s+v3\.1\.4\b', version.stdout + version.stderr), 'expected pinned Cosign v3.1.4'
    print(json.dumps({'cosignVersion': 'v3.1.4', 'mode': 'DSSE subject-digest and predicate claims; default trusted-root and transparency verification',
                      'fixtureSha256': '0206b44a1602d4fd45413266ec3c0df17fb2ce056f9492b20fbcba9f98a9fce9'}), flush=True)
    identity = 'https://github.com/lsgoodlionel/star-survey/.github/workflows/release.yml@refs/tags/v0.3.16-rc.1'
    digest = 'a39d53bc1d1344b93408faa4230222a1e27612b2be18598f39e953a4085607c5'
    # Exercise the actual verifier invocation rather than a separately copied CLI.
    body = VERIFY_PATH.read_text().split('  cosign verify-blob-attestation ', 1)[1].split('\n  cosign verify ', 1)[0]
    tokens = shlex.split('cosign verify-blob-attestation ' + body.replace('\\\n', ' '))
    bindings = {'$assets/cosign-$name.sigstore.json': str(bundle),
                '${expected_digest#sha256:}': digest,
                '$workflow_identity': identity,
                '$issuer': 'https://token.actions.githubusercontent.com'}
    command = [bindings.get(token, token) for token in tokens]
    assert not any('$' in token for token in command), 'unresolved verifier command variable'
    cases = [('original-dsse-bundle', None, None, None),
             ('wrong-digest', '--digest', '0' * 64, 'digest'),
             ('wrong-identity', '--certificate-identity', identity + '-wrong', 'identity'),
             ('wrong-issuer', '--certificate-oidc-issuer', 'https://invalid.example', 'issuer'),
             ('wrong-predicate', '--type', 'https://invalid.example/predicate', 'predicate')]
    for name, flag, value, reason in cases:
        arguments = command.copy()
        if flag:
            arguments[arguments.index(flag) + 1] = value
        result = subprocess.run(arguments, text=True, capture_output=True, timeout=120, check=False)
        if flag is None:
            assert result.returncode == 0, f'{name} failed: {result.stderr.splitlines()[-1:]}'
        else:
            assert result.returncode != 0, f'{name} incorrectly accepted'
            assert reason in result.stderr.lower(), f'{name} did not fail for the expected claim: {result.stderr.splitlines()[-1:]}'
        print(json.dumps({'case': name, 'expectedResultVerified': True, 'exitCode': result.returncode}), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ['--real-registry']:
        real_registry_preflight()
    elif len(sys.argv) == 3 and sys.argv[1] == '--real-cosign':
        real_cosign_preflight(Path(sys.argv[2]))
    else:
        unittest.main()
