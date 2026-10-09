import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from collections import namedtuple


PRODUCTION_DIR = Path(__file__).resolve().parents[1]


def load_module(name):
    path = PRODUCTION_DIR / f"{name}.py"
    if str(PRODUCTION_DIR) not in sys.path:
        sys.path.insert(0, str(PRODUCTION_DIR))
    spec = importlib.util.spec_from_file_location(f"production_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self, fail_when=None, line_delimited_health=False):
        self.calls = []
        self.fail_when = fail_when
        self.line_delimited_health = line_delimited_health

    def run(self, command, **kwargs):
        command = tuple(str(value) for value in command)
        self.calls.append((command, kwargs))
        if self.fail_when and self.fail_when(command):
            raise subprocess.CalledProcessError(1, command, stderr="injected failure")
        stdout = kwargs.get("stdout")
        if stdout is not None and hasattr(stdout, "write"):
            if "pg_dump" in command:
                stdout.write(b"PGDMP\0consistent")
            elif any("mariadb-dump" in value for value in command):
                stdout.write(b"-- MariaDB dump\nconsistent\n")
            elif command[:2] == ("docker", "cp"):
                with tarfile.open(fileobj=stdout, mode="w|") as archive:
                    info = tarfile.TarInfo("data")
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                    payload = b"data"
                    info = tarfile.TarInfo("data/value.txt")
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
        if command[:3] == ("docker", "compose", "-f") and "ps" in command:
            payload = [
                {"Service": name, "State": "running", "Health": "healthy"}
                for name in ("platform-db", "engine-db", "platform", "publish-gateway", "engine", "admin-web")
            ]
            stdout = "\n".join(json.dumps(row) for row in payload) if self.line_delimited_health else json.dumps(payload)
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if command[:3] == ("docker", "compose", "-f") and "config" in command:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"services": {}}), stderr="")
        if command[:2] == ("docker", "volume"):
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class FakeCipher:
    def encrypt(self, source, destination, key_file):
        destination.write_bytes(b"ENC1" + source.read_bytes()[::-1])

    def decrypt(self, source, destination, key_file):
        payload = source.read_bytes()
        if not payload.startswith(b"ENC1"):
            raise ValueError("invalid ciphertext")
        destination.write_bytes(payload[4:][::-1])


class BackupRestoreTest(unittest.TestCase):
    def setUp(self):
        self.backup = load_module("backup")
        self.restore = load_module("restore")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.target = self.root / "survey"
        release = self.target / "releases" / "0.2.0"
        release.mkdir(parents=True)
        (release / "compose.yml").write_text("name: survey-production\nservices: {}\n")
        (release / ".env").write_text("PUBLIC_HOST=survey.example.com\n")
        (release / "release.json").write_text(json.dumps({
            "schemaVersion": 1,
            "version": "0.2.0",
            "minimumSourceVersion": "0.1.0",
            "database": {"schema": "920", "compatibleSourceSchemas": ["920"]},
            "images": {
                name: "example/{}@sha256:{}".format(name.lower(), digit * 64)
                for name, digit in zip(
                    ("CADDY_IMAGE", "ADMIN_IMAGE", "PLATFORM_IMAGE", "PUBLISH_GATEWAY_IMAGE",
                     "ENGINE_IMAGE", "POSTGRES_IMAGE", "MARIADB_IMAGE"),
                    "abcdef1",
                )
            },
        }))
        (self.target / "current").write_text("0.2.0\n")
        secret_dir = self.target / "shared" / "secrets"
        secret_dir.mkdir(parents=True)
        self.key_file = secret_dir / "backup_encryption_key"
        self.key_file.write_text("test-only-key-material-with-enough-entropy\n")
        self.key_file.chmod(0o600)

    def create_backup(self, runner=None, name="nightly"):
        output = self.target / "backups" / name
        manager = self.backup.BackupManager(self.target, runner=runner or FakeRunner(), cipher=FakeCipher())
        manager.create(output, "0.2.0")
        return output

    def test_backup_contains_encrypted_dumps_volumes_and_exact_checksums(self):
        output = self.create_backup()
        manifest = json.loads((output / "manifest.json").read_text())
        expected = {
            "postgres.dump.enc", "mariadb.sql.enc", "caddy-data.tar.enc", "caddy-config.tar.enc",
            "platform-assets.tar.enc", "platform-exports.tar.enc", "publish-gateway-state.tar.enc",
            "engine-upload.tar.enc", "engine-runtime.tar.enc",
        }
        self.assertEqual(expected, set(manifest["files"]))
        self.assertEqual(2, manifest["schemaVersion"])
        self.assertEqual("920", manifest["databaseSchema"])
        self.assertEqual(7, len(manifest["images"]))
        self.assertRegex(manifest["releaseManifestSha256"], r"^[0-9a-f]{64}$")
        self.assertTrue((output / "CONTROL-HMAC").is_file())
        self.assertFalse(any(path.suffix in {".dump", ".sql", ".tar"} for path in output.iterdir()))
        sums = (output / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(9, len(sums))
        for name, digest in manifest["files"].items():
            self.assertEqual(digest, hashlib.sha256((output / name).read_bytes()).hexdigest())

    def test_control_metadata_and_checksums_are_authenticated(self):
        for name, mutation in (
            ("manifest.json", lambda value: value.replace(b'"version":"0.2.0"', b'"version":"9.9.9"')),
            ("SHA256SUMS", lambda value: value.replace(value[:1], b"0", 1)),
            ("CONTROL-HMAC", lambda value: b"0" * len(value)),
        ):
            with self.subTest(name=name):
                output = self.create_backup(name="tampered-" + name.lower().replace(".", "-"))
                path = output / name
                path.write_bytes(mutation(path.read_bytes()))
                runner = FakeRunner()
                with self.assertRaises(self.restore.RestoreIntegrityError):
                    self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
                self.assertEqual([], runner.calls)

    def test_restore_enforces_authenticated_version_and_image_identity_gates(self):
        for field in ("version", "images"):
            with self.subTest(field=field):
                output = self.create_backup(name="identity-" + field)
                manifest = json.loads((output / "manifest.json").read_text())
                if field == "version":
                    manifest["version"] = "9.9.9"
                else:
                    manifest["images"]["ENGINE_IMAGE"] = "example/engine@sha256:" + "9" * 64
                self.backup.rewrite_authenticated_control(output, self.key_file, manifest)
                runner = FakeRunner()
                with self.assertRaises(self.restore.RestoreIntegrityError):
                    self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
                self.assertEqual([], runner.calls)

    def _malicious_tar(self, attack):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz" if attack == "decompression-bomb" else "w") as archive:
            if attack == "duplicate":
                for payload in (b"first", b"second"):
                    info = tarfile.TarInfo("same.txt")
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
            elif attack == "case-collision":
                for name in ("Data.txt", "data.txt"):
                    info = tarfile.TarInfo(name)
                    info.size = 1
                    archive.addfile(info, io.BytesIO(b"x"))
            elif attack == "count-limit":
                for index in range(4):
                    info = tarfile.TarInfo(f"{index}.txt")
                    info.size = 1
                    archive.addfile(info, io.BytesIO(b"x"))
            elif attack == "size-limit":
                info = tarfile.TarInfo("large.bin")
                info.size = 9
                archive.addfile(info, io.BytesIO(b"123456789"))
            elif attack == "decompression-bomb":
                payload = b"0" * 4096
                info = tarfile.TarInfo("compressed.bin")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            else:
                info = tarfile.TarInfo({"absolute": "/escape", "traversal": "../escape"}.get(attack, "unsafe"))
                info.type = {
                    "symlink": tarfile.SYMTYPE,
                    "hardlink": tarfile.LNKTYPE,
                    "fifo": tarfile.FIFOTYPE,
                    "device": tarfile.CHRTYPE,
                }.get(attack, tarfile.REGTYPE)
                if attack == "socket":
                    info.type = b"s"
                info.linkname = "/run/secrets/platform_jwt_hmac_secret"
                archive.addfile(info)
        return stream.getvalue()

    def test_all_seven_volume_archives_reject_unsafe_members_before_docker(self):
        attacks = ("absolute", "traversal", "symlink", "hardlink", "fifo", "device", "socket",
                   "duplicate", "case-collision", "count-limit", "size-limit", "decompression-bomb")
        for index, volume in enumerate(self.backup.VOLUME_SOURCES):
            for attack in attacks:
                with self.subTest(volume=volume, attack=attack):
                    output = self.create_backup(name=f"attack-{index}-{attack}")
                    plain = self.root / f"{index}-{attack}.tar"
                    plain.write_bytes(self._malicious_tar(attack))
                    encrypted = output / f"{volume}.enc"
                    encrypted.unlink()
                    FakeCipher().encrypt(plain, encrypted, self.key_file)
                    self.backup.rewrite_authenticated_control(output, self.key_file)
                    runner = FakeRunner()
                    limits = {"max_members": 3, "max_file_bytes": 8, "max_total_bytes": 64}
                    if attack == "decompression-bomb":
                        limits = {"max_members": 3, "max_file_bytes": 8192, "max_total_bytes": 8192,
                                  "max_expansion_ratio": 2, "expansion_slack_bytes": 0}
                    with self.assertRaises(self.restore.RestoreIntegrityError):
                        self.restore.RestoreManager(
                            self.target, runner=runner, cipher=FakeCipher(), archive_limits=limits
                        ).restore(output)
                    self.assertEqual([], runner.calls)

    def test_real_cipher_round_trip_authenticates_before_decryption(self):
        source = self.root / "plain.bin"
        encrypted = self.root / "payload.enc"
        restored = self.root / "restored.bin"
        source.write_bytes(b"sensitive backup payload")
        cipher = self.backup.AuthenticatedOpenSSLCipher()
        cipher.encrypt(source, encrypted, self.key_file)
        self.assertNotIn(source.read_bytes(), encrypted.read_bytes())
        cipher.decrypt(encrypted, restored, self.key_file)
        self.assertEqual(source.read_bytes(), restored.read_bytes())
        encrypted.write_bytes(encrypted.read_bytes()[:-1] + bytes([encrypted.read_bytes()[-1] ^ 1]))
        with self.assertRaises(self.backup.BackupError):
            cipher.decrypt(encrypted, restored, self.key_file)

    def test_backup_failure_removes_partial_artifact_and_restarts_services(self):
        runner = FakeRunner(fail_when=lambda command: any("mariadb-dump" in part for part in command))
        output = self.target / "backups" / "failed"
        with self.assertRaises(self.backup.BackupError):
            self.backup.BackupManager(self.target, runner=runner, cipher=FakeCipher()).create(output, "0.2.0")
        self.assertFalse(output.exists())
        commands = [call[0] for call in runner.calls]
        self.assertTrue(any("pause" in command for command in commands))
        self.assertTrue(any("up" in command for command in commands))

    def test_backup_rejects_insufficient_disk_before_pausing_services(self):
        Usage = namedtuple("Usage", "total used free")
        runner = FakeRunner()
        manager = self.backup.BackupManager(
            self.target,
            runner=runner,
            cipher=FakeCipher(),
            disk_usage=lambda path: Usage(1024, 1000, 24),
        )
        with self.assertRaises(self.backup.BackupError):
            manager.create(self.target / "backups" / "no-space", "0.2.0")
        self.assertEqual([], runner.calls)

    def test_helpers_reject_paths_outside_managed_target_and_target_symlinks(self):
        outside = self.root / "outside"
        outside.mkdir()
        runner = FakeRunner()
        with self.assertRaises(self.backup.BackupError):
            self.backup.BackupManager(self.target, runner=runner, cipher=FakeCipher()).create(outside / "escaped", "0.2.0")
        with self.assertRaises(self.restore.RestoreIntegrityError):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(outside)
        alias = self.root / "target-alias"
        alias.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(self.backup.BackupError):
            self.backup.BackupManager(alias, runner=runner, cipher=FakeCipher())
        with self.assertRaises(self.restore.RestoreIntegrityError):
            self.restore.RestoreManager(alias, runner=runner, cipher=FakeCipher())
        self.assertEqual([], runner.calls)

    def test_restore_rejects_truncated_or_checksum_mismatched_payload_before_docker(self):
        for mutation in ("truncate", "checksum"):
            with self.subTest(mutation=mutation):
                output = self.create_backup(name=f"nightly-{mutation}")
                payload = output / "postgres.dump.enc"
                if mutation == "truncate":
                    payload.write_bytes(payload.read_bytes()[:3])
                else:
                    manifest = json.loads((output / "manifest.json").read_text())
                    manifest["files"]["postgres.dump.enc"] = "0" * 64
                    (output / "manifest.json").write_text(json.dumps(manifest))
                runner = FakeRunner()
                with self.assertRaises(self.restore.RestoreIntegrityError):
                    self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
                self.assertEqual([], runner.calls)
                if mutation == "truncate":
                    payload.write_bytes(b"repaired-for-cleanup")

    def test_restore_rejects_unlisted_empty_directory_before_docker(self):
        output = self.create_backup(name="unexpected-directory")
        (output / "unlisted").mkdir()
        runner = FakeRunner()
        with self.assertRaises(self.restore.RestoreIntegrityError):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        self.assertEqual([], runner.calls)

    def test_restore_validates_isolated_project_before_touching_production(self):
        output = self.create_backup()
        runner = FakeRunner(fail_when=lambda command: "survey-restore-" in " ".join(command) and "up" in command)
        with self.assertRaises(self.restore.RestoreRuntimeError):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        commands = [" ".join(call[0]) for call in runner.calls]
        self.assertTrue(any("survey-restore-" in command for command in commands))
        self.assertFalse(any("survey-production" in command and " down" in command for command in commands))

    def test_restore_unhealthy_candidate_never_stops_production(self):
        output = self.create_backup(name="unhealthy")

        class UnhealthyRunner(FakeRunner):
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                command = tuple(str(value) for value in command)
                if "survey-restore-" in " ".join(command) and "ps" in command and "--format" in command:
                    rows = json.loads(result.stdout)
                    rows[0]["Health"] = "unhealthy"
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps(rows), stderr="")
                return result

        runner = UnhealthyRunner()
        with self.assertRaises(self.restore.RestoreRuntimeError):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        commands = [" ".join(call[0]) for call in runner.calls]
        self.assertFalse(any("survey-production" in command and " down" in command for command in commands))

    def test_successful_restore_clears_volume_contents_before_extracting_archives(self):
        output = self.create_backup(name="successful")
        runner = FakeRunner()
        self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        commands = [" ".join(call[0]) for call in runner.calls]
        self.assertTrue(any("survey-restore-" in command and " find " in f" {command} " for command in commands))
        self.assertTrue(any("survey-production" in command and " down --remove-orphans" in command for command in commands))
        production_down = next(index for index, command in enumerate(commands) if "survey-production" in command and " down --remove-orphans" in command)
        production_find = next(index for index, command in enumerate(commands) if index > production_down and "survey-production" in command and " find " in f" {command} ")
        production_copy = next(index for index, command in enumerate(commands) if index > production_find and command.startswith("docker cp -"))
        self.assertLess(production_find, production_copy)

    def test_restore_accepts_compose_line_delimited_health_json(self):
        output = self.create_backup(name="line-health")
        self.restore.RestoreManager(
            self.target,
            runner=FakeRunner(line_delimited_health=True),
            cipher=FakeCipher(),
        ).restore(output)

    def test_production_apply_failure_restores_automatic_safety_backup(self):
        output = self.create_backup(name="requested")

        class FailFirstProductionRestore(FakeRunner):
            def __init__(self):
                super().__init__()
                self.failed = False

            def run(self, command, **kwargs):
                joined = " ".join(str(value) for value in command)
                if "survey-production" in joined and "pg_restore" in joined and not self.failed:
                    self.failed = True
                    command = tuple(str(value) for value in command)
                    self.calls.append((command, kwargs))
                    raise subprocess.CalledProcessError(1, command, stderr="injected production restore failure")
                return super().run(command, **kwargs)

        runner = FailFirstProductionRestore()
        with self.assertRaises(self.restore.RestoreRuntimeError):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        commands = [" ".join(call[0]) for call in runner.calls]
        production_pg_restore = [command for command in commands if "survey-production" in command and "pg_restore" in command]
        self.assertEqual(2, len(production_pg_restore))
        self.assertTrue(any("survey-production" in command and "up -d --remove-orphans" in command for command in commands))
        safety = list((self.target / "backups").glob("pre-restore-safety-*"))
        self.assertEqual(1, len(safety))

    def test_restore_rejects_database_schema_incompatible_with_release(self):
        output = self.create_backup()
        manifest = json.loads((output / "manifest.json").read_text())
        manifest["databaseSchema"] = "919"
        (output / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(self.restore.RestoreIntegrityError):
            self.restore.RestoreManager(self.target, runner=FakeRunner(), cipher=FakeCipher()).restore(output)

    def test_restore_rejects_boolean_schema_version_and_invalid_backup_version(self):
        for field, value in (("schemaVersion", True), ("version", "../../escape")):
            with self.subTest(field=field):
                output = self.create_backup(name=f"bad-{field}")
                manifest = json.loads((output / "manifest.json").read_text())
                manifest[field] = value
                (output / "manifest.json").write_text(json.dumps(manifest))
                runner = FakeRunner()
                with self.assertRaises(self.restore.RestoreIntegrityError):
                    self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
                self.assertEqual([], runner.calls)


if __name__ == "__main__":
    unittest.main()
