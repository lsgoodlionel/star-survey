from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest import mock


PRODUCTION_DIR = Path(__file__).resolve().parents[2]
RELEASE_DIR = PRODUCTION_DIR / "release"
BUILDER = RELEASE_DIR / "build_release.py"
VERSION_FILE = PRODUCTION_DIR / "VERSION"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
IMAGE_KEYS = (
    "CADDY_IMAGE",
    "ADMIN_IMAGE",
    "PLATFORM_IMAGE",
    "PUBLISH_GATEWAY_IMAGE",
    "ENGINE_IMAGE",
    "POSTGRES_IMAGE",
    "MARIADB_IMAGE",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_surveyctl():
    spec = importlib.util.spec_from_file_location("production_surveyctl", PRODUCTION_DIR / "surveyctl.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_builder():
    spec = importlib.util.spec_from_file_location("production_release_builder", BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(RELEASE_DIR))
    try:
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class ReleaseBuilderTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_rc_predecessor_floor_crosses_policy_builder_and_bundled_loader(self):
        import yaml
        workflow = yaml.safe_load((PRODUCTION_DIR.parents[2] / '.github/workflows/release.yml').read_text())
        step = next(step for step in workflow['jobs']['candidate-assets']['steps'] if step.get('id') == 'baseline')
        script = step['run'].split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        policy = self.root / 'policy.json'
        rule = {'mode': 'predecessor-artifact', 'version': '0.3.17-rc.1',
                'manifestSha256': '95b113aa6c554c8e918bdf8d7b0e4ed47bfa59d6b129741d131e7ca4c611379b',
                'runId': 41756770743, 'commit': 'd745a5eeb7eca6af59d63986db004249efbd2acc'}
        policy.write_text(json.dumps({'schemaVersion': 1, 'releases': {'0.3.18-rc.1': rule}}))
        resolved = subprocess.run([sys.executable, '-', '0.3.18-rc.1', str(policy)], input=script, text=True,
                                  capture_output=True, check=False)
        self.assertEqual(0, resolved.returncode, resolved.stderr)
        values = dict(line.split('=', 1) for line in resolved.stdout.splitlines())
        self.assertEqual(rule['version'], values['MINIMUM_SOURCE_VERSION'])
        self.assertEqual('predecessor-artifact', values['BASELINE_SOURCE'])
        self.assertEqual(str(rule['runId']), values['BASELINE_RUN_ID'])
        self.assertEqual(rule['commit'], values['BASELINE_COMMIT'])
        version_file = self.root / 'target-version';version_file.write_text('0.3.18-rc.1\n')
        output = self.root / 'rc-minimum'
        result = self.build(output, '--version-file', str(version_file), '--minimum-source-version', values['MINIMUM_SOURCE_VERSION'])
        self.assertEqual(0, result.returncode, result.stderr)
        manifest = load_surveyctl().load_manifest(output / 'release.json')
        self.assertEqual('0.3.17-rc.1', manifest.raw['minimumSourceVersion'])
        for arch in ('amd64', 'arm64'):
            runtime = self.root / ('runtime-' + arch);runtime.mkdir()
            with tarfile.open(output / manifest.raw['assets']['bundles'][arch]['name']) as archive:
                archive.extractall(runtime)
            spec = importlib.util.spec_from_file_location('bundled_floor_' + arch, runtime / 'surveyctl.py')
            module = importlib.util.module_from_spec(spec);sys.modules[spec.name] = module;spec.loader.exec_module(module)
            loaded = module.load_manifest(output / 'release.json')
            # Both distributed CLIs must accept the exact RC floor, not only surveyctl.
            restore_target = (self.root / ('restore-floor-' + arch)).resolve()
            installed = restore_target / 'releases' / loaded.version
            installed.mkdir(parents=True)
            (restore_target / 'current').write_text(loaded.version)
            (installed / 'release.json').write_bytes((output / 'release.json').read_bytes())
            (installed / 'compose.yml').write_text('services: {}\n')
            (installed / '.env').write_text('PLATFORM_ADMIN_ENFORCE_PERSONNEL=false\n')
            restore_check = subprocess.run([sys.executable, '-c',
                "import sys; sys.path.insert(0, sys.argv[1]); from restore import RestoreManager; "
                "print(RestoreManager(sys.argv[2])._load_current()[-1])", str(runtime), str(restore_target)],
                capture_output=True, text=True, check=False)
            self.assertEqual(0, restore_check.returncode, restore_check.stderr)
            self.assertEqual(rule['version'], restore_check.stdout.strip())
            floor = module._version_key(loaded.raw['minimumSourceVersion'])
            self.assertEqual(module._version_key(rule['version']), floor)
            self.assertLess(module._version_key('0.3.17-rc.0'), floor)
            self.assertGreater(module._version_key('0.3.17'), floor)
            self.assertGreater(module._version_key(loaded.version), floor)
            target = (self.root / ('floor-target-' + arch)).resolve()
            (target / '.surveyctl').mkdir(parents=True, mode=0o700)
            manager = module.SurveyManager(target)
            state = {'version': '0.3.17-rc.0', 'publicHost': 'fixture.test'}
            with mock.patch.object(manager, '_read_state', side_effect=lambda **kwargs: dict(state)), \
                 mock.patch.object(manager, '_personnel_guard_enabled', return_value=False), \
                 mock.patch.object(manager, 'preflight', side_effect=RuntimeError('version-floor-passed')) as preflight:
                with self.assertRaisesRegex(module.InputError, 'below the release minimum source version'):
                    manager.upgrade(output / 'release.json')
                preflight.assert_not_called()
                state['version'] = '0.3.17-rc.1'
                with self.assertRaisesRegex(RuntimeError, 'version-floor-passed'):
                    manager.upgrade(output / 'release.json')
                preflight.assert_called_once()

    def test_invalid_minimum_source_versions_remain_rejected(self):
        valid_output = self.root / 'valid-floor-source'
        built = self.build(valid_output)
        self.assertEqual(0, built.returncode, built.stderr)
        original = json.loads((valid_output / 'release.json').read_text())
        runtime = load_surveyctl()
        for index, version in enumerate(('latest', '0.3.17-rc.', '0.3.17-beta.1', '0.3.17+build', '0.3.17-rc.1\nBAD=1')):
            with self.subTest(version=version):
                result = self.build(self.root / f'bad-floor-{index}', '--minimum-source-version', version)
                self.assertNotEqual(0, result.returncode)
                malformed = {**original, 'minimumSourceVersion': version}
                path = self.root / f'bad-floor-{index}.json';path.write_text(json.dumps(malformed))
                with self.assertRaises(runtime.IntegrityError): runtime.load_manifest(path)

    def test_personnel_capability_requires_matching_verified_image_and_source(self):
        builder = load_builder()
        image = "ghcr.io/example/platform@sha256:" + "a" * 64
        ref = "refs/tags/v0.3.0-rc.1"
        with mock.patch.object(builder.subprocess, "run") as run:
            builder.verify_personnel_image(image, COMMIT, ref, self.root)
            self.assertEqual(["git", "merge-base", "--is-ancestor", builder.PERSONNEL_SECURITY_BASE, COMMIT], run.call_args_list[0].args[0])
            verify = run.call_args_list[1].args[0]
            self.assertIn("oci://" + image, verify)
            self.assertEqual(COMMIT, verify[verify.index("--source-digest") + 1])
            self.assertEqual(ref, verify[verify.index("--source-ref") + 1])
        for failure_at in (0, 1):
            calls = [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)]
            calls[failure_at] = subprocess.CalledProcessError(1, "verification")
            with mock.patch.object(builder.subprocess, "run", side_effect=calls):
                with self.assertRaises(builder.ReleaseBuildError):
                    builder.verify_personnel_image(image, COMMIT, ref, self.root)
        with self.assertRaises(builder.ReleaseBuildError):
            builder.verify_personnel_image(image, COMMIT, "refs/heads/main", self.root)

    def build(
        self,
        output: Path,
        *extra: str,
        source: Path = PRODUCTION_DIR,
        trusted_root: Path | None = None,
        compatible_schemas: tuple[str, ...] = ("919", "920"),
        image_keys: tuple[str, ...] = IMAGE_KEYS,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(BUILDER),
            "--source",
            str(source),
            "--output",
            str(output),
            "--version-file",
            str(VERSION_FILE),
            "--commit",
            COMMIT,
            "--channel",
            "candidate",
            "--minimum-source-version",
            "0.1.0",
            "--database-schema",
            "920",
            "--backup-schema",
            "2",
            "--rollback",
            "restore-only",
            "--source-date-epoch",
            "1700000000",
        ]
        if trusted_root is not None:
            command.extend(["--trusted-root", str(trusted_root)])
        for value in compatible_schemas:
            command.extend(["--compatible-source-schema", value])
        digests = {key: index for index, key in enumerate(IMAGE_KEYS, start=1)}
        for key in image_keys:
            command.extend(["--image", f"{key}=ghcr.io/lsgoodlionel/{key.lower()}@sha256:{digests[key]:064x}"])
        command.extend(extra)
        return subprocess.run(command, text=True, capture_output=True, check=False)

    def copy_trusted_source(self, label: str) -> tuple[Path, Path]:
        trusted_root = self.root.resolve() / label
        source = trusted_root / "platform" / "deploy" / "production"
        source.parent.mkdir(parents=True)
        shutil.copytree(PRODUCTION_DIR, source)
        return trusted_root, source

    def test_repeated_builds_are_byte_reproducible_and_tar_metadata_is_normalized(self):
        first, second = self.root / "first", self.root / "second"
        first_result = self.build(first)
        second_result = self.build(second)
        self.assertEqual(0, first_result.returncode, first_result.stderr)
        self.assertEqual(0, second_result.returncode, second_result.stderr)

        expected_assets = {
            "survey-0.3.0-rc.1-linux-amd64.tar.gz",
            "survey-0.3.0-rc.1-linux-arm64.tar.gz",
            "release.json",
            "release-notes.md",
            "SHA256SUMS",
        }
        self.assertEqual(expected_assets, {path.name for path in first.iterdir()})
        for name in expected_assets:
            self.assertEqual(sha256(first / name), sha256(second / name), name)

        archive_path = first / "survey-0.3.0-rc.1-linux-amd64.tar.gz"
        with archive_path.open("rb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as compressed:
                with tarfile.open(fileobj=compressed, mode="r:") as archive:
                    members = archive.getmembers()
                    self.assertEqual(sorted(member.name for member in members), [member.name for member in members])
                    self.assertTrue(members)
                    for member in members:
                        self.assertEqual(1700000000, member.mtime, member.name)
                        self.assertEqual((0, 0, "root", "root"), (member.uid, member.gid, member.uname, member.gname))
                        self.assertEqual(0o755 if member.isdir() or member.name in {"surveyctl", "surveyctl.py"} or member.name.endswith(".sh") else 0o644, member.mode, member.name)

    def test_equivalent_parameter_order_and_duplicate_schemas_produce_identical_assets(self):
        first, second = self.root / "ordered", self.root / "permuted"
        first_result = self.build(first, compatible_schemas=("919", "1.10.0", "920"))
        second_result = self.build(
            second,
            compatible_schemas=("0920", "1.10.0", "919", "919"),
            image_keys=tuple(reversed(IMAGE_KEYS)),
        )
        self.assertEqual(0, first_result.returncode, first_result.stderr)
        self.assertEqual(0, second_result.returncode, second_result.stderr)
        self.assertEqual(["919", "920", "1.10.0"], json.loads(first.joinpath("release.json").read_text())["database"]["compatibleSourceSchemas"])
        for name in sorted(path.name for path in first.iterdir()):
            self.assertEqual(sha256(first / name), sha256(second / name), name)

    def test_manifest_is_consumable_and_records_release_compatibility_and_asset_digests(self):
        output = self.root / "release"
        result = self.build(output)
        self.assertEqual(0, result.returncode, result.stderr)

        manifest_path = output / "release.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("0.3.0-rc.1", manifest["version"])
        self.assertEqual(COMMIT, manifest["commit"])
        self.assertEqual("candidate", manifest["channel"])
        self.assertEqual(["22.04", "24.04"], manifest["supportedHosts"]["ubuntu"])
        self.assertEqual(["amd64", "arm64"], manifest["supportedHosts"]["architectures"])
        self.assertEqual("0.1.0", manifest["minimumSourceVersion"])
        self.assertEqual("restore-only", manifest["database"]["rollback"])
        self.assertEqual(2, manifest["database"]["backupSchema"])
        self.assertEqual(["919", "920"], manifest["database"]["compatibleSourceSchemas"])
        self.assertEqual(set(IMAGE_KEYS), set(manifest["images"]))
        self.assertTrue(all("@sha256:" in image for image in manifest["images"].values()))

        for architecture in ("amd64", "arm64"):
            asset = manifest["assets"]["bundles"][architecture]
            self.assertEqual(sha256(output / asset["name"]), asset["sha256"])

        loaded = load_surveyctl().load_manifest(manifest_path)
        self.assertEqual("0.3.0-rc.1", loaded.version)

        checksum_lines = (output / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        expected_names = sorted(path.name for path in output.iterdir() if path.name != "SHA256SUMS")
        self.assertEqual(expected_names, [line.split("  ", 1)[1] for line in checksum_lines])
        for line in checksum_lines:
            digest, name = line.split("  ", 1)
            self.assertEqual(sha256(output / name), digest)

    def test_native_acceptance_profile_is_explicit_fixed_and_orchestrator_is_bundled(self):
        output = self.root / "native-release"
        result = self.build(
            output,
            "--native-acceptance-profile",
            "github-actions-native-v1",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        manifest = json.loads((output / "release.json").read_text(encoding="utf-8"))
        self.assertEqual({
            "profile": "github-actions-native-v1",
            "minimumFreeBytes": 8 * 1024**3,
            "tlsMode": "local-ca",
            "diskEvidence": "separately-tested",
        }, manifest["nativeAcceptance"])
        with tarfile.open(output / "survey-0.3.0-rc.1-linux-amd64.tar.gz", "r:gz") as archive:
            names = {member.name for member in archive.getmembers() if member.isfile()}
        self.assertIn("release/native_acceptance.sh", names)
        self.assertIn("release/native_acceptance_evidence.py", names)
        self.assertIn("release/verify_release.sh", names)

    def test_manifest_rejects_mutated_native_acceptance_threshold_or_mode(self):
        output = self.root / "native-release"
        result = self.build(output, "--native-acceptance-profile", "github-actions-native-v1")
        self.assertEqual(0, result.returncode, result.stderr)
        base = json.loads((output / "release.json").read_text(encoding="utf-8"))
        surveyctl = load_surveyctl()
        for field, value in (
            ("minimumFreeBytes", 1),
            ("minimumFreeBytes", True),
            ("tlsMode", "insecure"),
            ("diskEvidence", "pretend"),
        ):
            with self.subTest(field=field, value=value):
                mutated = json.loads(json.dumps(base))
                mutated["nativeAcceptance"][field] = value
                path = self.root / f"mutated-{field}-{str(value).lower()}.json"
                path.write_text(json.dumps(mutated), encoding="utf-8")
                with self.assertRaises(surveyctl.IntegrityError):
                    surveyctl.load_manifest(path)

    def test_bundle_contains_only_the_allowlisted_production_runtime(self):
        output = self.root / "release"
        result = self.build(output)
        self.assertEqual(0, result.returncode, result.stderr)

        with tarfile.open(output / "survey-0.3.0-rc.1-linux-arm64.tar.gz", "r:gz") as archive:
            names = {member.name for member in archive.getmembers() if member.isfile()}

        self.assertTrue({
            "compose.yml",
            "Caddyfile",
            "surveyctl",
            "surveyctl.py",
            "release.schema.json",
            "env.example",
            "engines.example.json",
            "README.md",
            "backup.py",
            "restore.py",
            "doctor.py",
            "bundle-metadata.json",
            "init/engine-init.sh",
            "org-login-empty/README",
        } <= names)
        self.assertEqual({"org-login-empty/README"}, {n for n in names if n.startswith("org-login-empty/")})
        forbidden_fragments = ("tests/", "fixtures/", "secrets/", "backups/", ".runtime/", ".env")
        self.assertFalse([name for name in names if any(fragment in name for fragment in forbidden_fragments)])

    def test_manifest_requires_the_supported_backup_schema(self):
        output = self.root / "release"
        result = self.build(output)
        self.assertEqual(0, result.returncode, result.stderr)
        manifest_path = output / "release.json"
        surveyctl = load_surveyctl()
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        for index, mutation in enumerate(("missing", True, 1)):
            manifest = json.loads(json.dumps(original))
            if mutation == "missing":
                del manifest["database"]["backupSchema"]
            else:
                manifest["database"]["backupSchema"] = mutation
            candidate = self.root / f"invalid-backup-schema-{index}.json"
            candidate.write_text(json.dumps(manifest), encoding="utf-8")
            with self.subTest(mutation=mutation), self.assertRaises(surveyctl.IntegrityError):
                surveyctl.load_manifest(candidate)

    def test_source_root_parent_components_and_files_must_be_unlinked_regular_inodes(self):
        sentinel = "EXTERNAL_RELEASE_SENTINEL"

        root_link_root, root_link_source = self.copy_trusted_source("source-root")
        external_root = self.root.resolve() / "external-root"
        shutil.copytree(root_link_source, external_root)
        external_root.joinpath("Caddyfile").write_text(sentinel, encoding="utf-8")
        shutil.rmtree(root_link_source)
        root_link_source.symlink_to(external_root, target_is_directory=True)

        intermediate_root, intermediate_source = self.copy_trusted_source("intermediate")
        external_init = self.root.resolve() / "external-init"
        shutil.copytree(intermediate_source / "init", external_init)
        external_init.joinpath("config.production.php").write_text(sentinel, encoding="utf-8")
        shutil.rmtree(intermediate_source / "init")
        intermediate_source.joinpath("init").symlink_to(external_init, target_is_directory=True)

        file_root, file_source = self.copy_trusted_source("file")
        external_file = self.root.resolve() / "external-Caddyfile"
        external_file.write_text(sentinel, encoding="utf-8")
        file_source.joinpath("Caddyfile").unlink()
        file_source.joinpath("Caddyfile").symlink_to(external_file)

        hardlink_root, hardlink_source = self.copy_trusted_source("hardlink")
        hardlink_source.joinpath("Caddyfile").unlink()
        os.link(external_file, hardlink_source / "Caddyfile")

        for label, trusted_root, source in (
            ("source-root", root_link_root, root_link_source),
            ("intermediate", intermediate_root, intermediate_source),
            ("file", file_root, file_source),
            ("hardlink", hardlink_root, hardlink_source),
        ):
            output = self.root / f"output-{label}"
            result = self.build(output, source=source, trusted_root=trusted_root)
            with self.subTest(label=label):
                self.assertNotEqual(0, result.returncode)
                self.assertIn("runtime", result.stderr.lower())
                self.assertFalse(output.exists())

    def test_source_ancestor_symlink_beneath_trusted_root_is_rejected(self):
        trusted_root = self.root.resolve() / "ancestor-trusted"
        external_root = self.root.resolve() / "ancestor-external"
        external_source = external_root / "platform" / "deploy" / "production"
        external_source.parent.mkdir(parents=True)
        shutil.copytree(PRODUCTION_DIR, external_source)
        external_source.joinpath("Caddyfile").write_text("ANCESTOR_SYMLINK_EXTERNAL_SENTINEL", encoding="utf-8")
        trusted_root.mkdir()
        trusted_root.joinpath("platform").symlink_to(external_root / "platform", target_is_directory=True)
        source = trusted_root / "platform" / "deploy" / "production"
        output = self.root / "ancestor-output"

        result = self.build(output, source=source, trusted_root=trusted_root)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("trusted", result.stderr.lower())
        self.assertNotIn("unrecognized arguments", result.stderr.lower())
        self.assertFalse(output.exists())

    def test_same_inode_mutation_during_read_is_rejected(self):
        builder = load_builder()
        trusted_root, source = self.copy_trusted_source("mutation")
        target = source / "Caddyfile"
        original = target.read_bytes()
        replacement = b"X" * len(original)
        opened = threading.Event()
        mutated = threading.Event()

        def after_open(path: Path, _metadata) -> None:
            if path == target:
                opened.set()
                self.assertTrue(mutated.wait(timeout=5))

        def mutate() -> None:
            self.assertTrue(opened.wait(timeout=5))
            before = target.stat()
            with target.open("r+b", buffering=0) as handle:
                handle.seek(0)
                handle.write(replacement)
                os.fsync(handle.fileno())
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
            mutated.set()

        worker = threading.Thread(target=mutate, daemon=True)
        worker.start()
        with self.assertRaises(builder.ReleaseBuildError):
            builder.collect_runtime(source, trusted_root, after_open=after_open)
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

    def test_mutable_or_incomplete_image_inputs_are_rejected_without_outputs(self):
        output = self.root / "invalid"
        result = self.build(output, "--image", "ADMIN_IMAGE=ghcr.io/example/admin:latest")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("image", result.stderr.lower())
        self.assertFalse(output.exists())

    def test_release_notes_explain_install_upgrade_backup_and_restore_only_rollback(self):
        output = self.root / "release"
        result = self.build(output)
        self.assertEqual(0, result.returncode, result.stderr)
        notes = (output / "release-notes.md").read_text(encoding="utf-8")
        for phrase in ("0.3.0-rc.1", "candidate", "surveyctl install", "surveyctl upgrade", "backup", "restore-only"):
            self.assertIn(phrase, notes)
        self.assertIn("Do not use the installed release's older CLI", notes)
        self.assertIn("recovery is currently disabled", notes)
        self.assertIn("Preserve the installation, operation marker and verified pre-upgrade backup", notes)
        self.assertIn("do not start older images against a migrated database or bypass the block with an older controller or helper", notes)
        self.assertNotIn("recover with the verified pre-upgrade backup", notes)
        self.assertNotIn("use the verified pre-upgrade backup; do not start", notes)
        self.assertIn("./surveyctl upgrade --manifest /path/to/verified/release.json", notes)
        self.assertNotIn("0123456789abcdef0123456789abcdef01234567", notes)


if __name__ == "__main__":
    unittest.main()
