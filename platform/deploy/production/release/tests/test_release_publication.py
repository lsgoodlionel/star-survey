from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import unittest
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

    def test_publication_is_tag_only_fail_closed_and_idempotent_without_clobber(self):
        assert_publication_contract(self, self.workflow)
        writers = {
            name for name, job in self.workflow["jobs"].items()
            if job.get("permissions", {}).get("contents") == "write"
        }
        self.assertEqual({"publication"}, writers)
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
            },
        }
        readiness = {"promotable": True, "reason": "upgrade-verified", "candidateCommit": self.commit}
        (self.assets / "release.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        (self.assets / "release-readiness.json").write_text(json.dumps(readiness) + "\n", encoding="utf-8")
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
        return {
            "PATH": f"{fake_bin}:{os.environ['PATH']}", "GH_STATE": str(state),
            "GH_REMOTE": str(remote), "GH_LOG": str(log),
        }, state, log

    def run_publisher(self, body: Path, metadata: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            str(PUBLISH_RELEASE_PATH), "--assets", str(self.assets), "--metadata", str(metadata),
            "--body", str(body), "--repository", "example/project",
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

    def test_unknown_create_outcome_is_reconciled_without_clobber(self):
        body, metadata, expected = self.render("v1.2.3-rc.4", "v1.2.3-rc.4")
        environment, state, log = self.make_fake_gh(None, create_unknown=True)
        result = self.run_publisher(body, metadata, environment)
        self.assertEqual(0, result.returncode, result.stderr)
        final = json.loads(state.read_text())["release"]
        self.assertFalse(final["isDraft"])
        self.assertEqual(expected["name"], final["name"])
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
            "ENGINE_IMAGE": "example.invalid/engine@sha256:" + "2" * 64,
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
        readiness = {
            "schemaVersion": 1, "candidateVersion": version, "candidateCommit": "a" * 40,
            "manifestSha256": hashlib.sha256((assets / "release.json").read_bytes()).hexdigest(),
            "amd64BundleSha256": bundles["amd64"]["sha256"], "arm64BundleSha256": bundles["arm64"]["sha256"],
            "adminManifestDigest": image_digest, "platformManifestDigest": image_digest,
            "gatewayManifestDigest": image_digest,
            "matrixEvidenceSha256": {
                "ubuntu-22-04-amd64": "5" * 64, "ubuntu-24-04-amd64": "6" * 64,
                "ubuntu-22-04-arm64": "7" * 64, "ubuntu-24-04-arm64": "8" * 64,
            },
            "baselineMode": "predecessor", "nativeMatrixCount": 4,
            "promotable": promotable, "reason": "upgrade-verified" if promotable else "bootstrap-not-promotable",
        }
        (assets / "release-readiness.json").write_text(json.dumps(readiness) + "\n", encoding="utf-8")
        for name in ("admin", "platform", "publish-gateway"):
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
            "cosign": '#!/bin/sh\nprintf "cosign %s\\n" "$*" >> "$VERIFY_LOG"\n',
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
        self.assertEqual(8, commands.count("cosign "))
        self.assertEqual(30, commands.count("gh attestation verify"))
        self.assertEqual(6, commands.count("docker buildx imagetools inspect"))

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


if __name__ == "__main__":
    unittest.main()
