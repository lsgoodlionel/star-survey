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
import unittest


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


class ReleaseBuilderTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def build(
        self,
        output: Path,
        *extra: str,
        source: Path = PRODUCTION_DIR,
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
        for value in compatible_schemas:
            command.extend(["--compatible-source-schema", value])
        digests = {key: index for index, key in enumerate(IMAGE_KEYS, start=1)}
        for key in image_keys:
            command.extend(["--image", f"{key}=ghcr.io/lsgoodlionel/{key.lower()}@sha256:{digests[key]:064x}"])
        command.extend(extra)
        return subprocess.run(command, text=True, capture_output=True, check=False)

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
        } <= names)
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
        pristine = self.root / "pristine"
        shutil.copytree(PRODUCTION_DIR, pristine)
        sentinel = "EXTERNAL_RELEASE_SENTINEL"

        external_root = self.root / "external-root"
        shutil.copytree(pristine, external_root)
        external_root.joinpath("Caddyfile").write_text(sentinel, encoding="utf-8")
        linked_root = self.root / "linked-root"
        linked_root.symlink_to(external_root, target_is_directory=True)

        intermediate_source = self.root / "intermediate-source"
        shutil.copytree(pristine, intermediate_source)
        external_init = self.root / "external-init"
        shutil.copytree(intermediate_source / "init", external_init)
        external_init.joinpath("config.production.php").write_text(sentinel, encoding="utf-8")
        shutil.rmtree(intermediate_source / "init")
        intermediate_source.joinpath("init").symlink_to(external_init, target_is_directory=True)

        file_source = self.root / "file-source"
        shutil.copytree(pristine, file_source)
        external_file = self.root / "external-Caddyfile"
        external_file.write_text(sentinel, encoding="utf-8")
        file_source.joinpath("Caddyfile").unlink()
        file_source.joinpath("Caddyfile").symlink_to(external_file)

        hardlink_source = self.root / "hardlink-source"
        shutil.copytree(pristine, hardlink_source)
        hardlink_source.joinpath("Caddyfile").unlink()
        os.link(external_file, hardlink_source / "Caddyfile")

        for label, source in (
            ("source-root", linked_root),
            ("intermediate", intermediate_source),
            ("file", file_source),
            ("hardlink", hardlink_source),
        ):
            output = self.root / f"output-{label}"
            result = self.build(output, source=source)
            with self.subTest(label=label):
                self.assertNotEqual(0, result.returncode)
                self.assertIn("runtime", result.stderr.lower())
                self.assertFalse(output.exists())

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
        self.assertNotIn("0123456789abcdef0123456789abcdef01234567", notes)


if __name__ == "__main__":
    unittest.main()
