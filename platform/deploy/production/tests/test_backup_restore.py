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
from unittest import mock
import unicodedata
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
                for name in ("platform-db", "engine-db", "platform", "publish-gateway", "engine", "admin-web", "edge")
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

    def replace_mariadb_dump(self, output, payload):
        plain = self.root / 'vendor-mariadb.sql'
        plain.write_bytes(payload)
        FakeCipher().encrypt(plain, output / 'mariadb.sql.enc', self.key_file)
        self.backup.rewrite_authenticated_control(output, self.key_file)

    def test_official_mariadb_sandbox_preamble_is_verified_without_rewriting(self):
        output = self.create_backup()
        payload = b'/*M!999999\\- enable the sandbox mode */ \n-- MariaDB dump fixture\n'
        self.replace_mariadb_dump(output, payload)
        with tempfile.TemporaryDirectory() as temporary:
            _, decrypted = self.restore.RestoreManager(self.target, cipher=FakeCipher()).verify_backup(output, Path(temporary))
            self.assertEqual(payload, decrypted['mariadb.sql'].read_bytes())
        legacy = b'-- MariaDB dump legacy fixture\n'
        self.replace_mariadb_dump(output, legacy)
        with tempfile.TemporaryDirectory() as temporary:
            _, decrypted = self.restore.RestoreManager(self.target, cipher=FakeCipher()).verify_backup(output, Path(temporary))
            self.assertEqual(legacy, decrypted['mariadb.sql'].read_bytes())

    def test_mariadb_sandbox_preamble_rejects_malformed_and_injected_variants(self):
        output = self.create_backup()
        header = b'/*M!999999\\- enable the sandbox mode */'
        invalid = [header, header + b' SELECT 1;\n-- fixture\n', header + b'\nSELECT 1;\n',
                   header + b'\n' + header + b'\n-- fixture\n',
                   b'/*M!999998\\- enable the sandbox mode */\n-- fixture\n',
                   b'/*M!999999- enable the sandbox mode */\n-- fixture\n',
                   b'/*M!999999\\! system command */\n-- fixture\n',
                   b'/* arbitrary comment */\n-- fixture\n']
        for payload in invalid:
            with self.subTest(payload=payload):
                self.replace_mariadb_dump(output, payload)
                with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
                        self.restore.RestoreIntegrityError, 'MariaDB dump format is invalid'):
                    self.restore.RestoreManager(self.target, cipher=FakeCipher()).verify_backup(output, Path(temporary))

    def test_mariadb_vendor_header_never_bypasses_authentication_or_floor(self):
        output = self.create_backup()
        self.replace_mariadb_dump(output, b'/*M!999999\\- enable the sandbox mode */ \n-- MariaDB dump fixture\n')
        (output / 'CONTROL-HMAC').write_text('0' * 64)
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(self.restore.RestoreIntegrityError):
            self.restore.RestoreManager(self.target, cipher=FakeCipher()).verify_backup(output, Path(temporary))
        self.backup.rewrite_authenticated_control(output, self.key_file)
        (self.target / 'releases/0.2.0/.env').write_text('PLATFORM_ADMIN_ENFORCE_PERSONNEL=true\n')
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
                self.restore.RestoreIntegrityError, 'personnel security floor is missing'):
            self.restore.RestoreManager(self.target, cipher=FakeCipher()).verify_backup(output, Path(temporary))

    def test_direct_restore_rejects_legacy_backup_before_starting_services(self):
        output = self.create_backup()
        source = self.target / "releases" / "0.2.0"
        release = self.target / "releases" / "0.3.0"
        release.mkdir()
        (release / "compose.yml").write_text((source / "compose.yml").read_text())
        (release / ".env").write_text("PLATFORM_ADMIN_ENFORCE_PERSONNEL=true\n")
        manifest = json.loads((source / "release.json").read_text())
        manifest["version"] = "0.3.0"
        manifest["securityCapabilities"] = {"platform-admin-session-v1": manifest["images"]["PLATFORM_IMAGE"]}
        (release / "release.json").write_text(json.dumps(manifest))
        (self.target / "current").write_text("0.3.0\n")
        floor = self.target / ".surveyctl" / "auth-security-floor"
        floor.parent.mkdir()
        floor.write_text("platform-admin-session-v1\n")
        runner = FakeRunner()
        with self.assertRaisesRegex(self.restore.RestoreIntegrityError, "predates.*security"):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        self.assertEqual([], runner.calls)

    def test_restore_invalidates_personnel_before_any_application_start(self):
        release = self.target / "releases" / "0.2.0"
        manifest = json.loads((release / "release.json").read_text())
        manifest["securityCapabilities"] = {"platform-admin-session-v1": manifest["images"]["PLATFORM_IMAGE"]}
        (release / "release.json").write_text(json.dumps(manifest))
        (release / ".env").write_text("PLATFORM_ADMIN_ENFORCE_PERSONNEL=true\n")
        floor = self.target / ".surveyctl" / "auth-security-floor"
        floor.parent.mkdir()
        floor.write_text("platform-admin-session-v1\n")
        output = self.create_backup()
        runner = FakeRunner()
        self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        commands = [call[0] for call in runner.calls]
        restores = [i for i, command in enumerate(commands) if "pg_restore" in command]
        scrubs = [i for i, command in enumerate(commands) if "psql" in command]
        starts = [i for i, command in enumerate(commands) if i > restores[0] and "up" in command and ("platform" in command or "--remove-orphans" in command)]
        self.assertEqual(2, len(restores))
        self.assertEqual(2, len(scrubs))
        for restore, scrub in zip(restores, scrubs):
            self.assertLess(restore, scrub)
            self.assertFalse(any(restore < start < scrub for start in starts))
            self.assertTrue(any(start > scrub for start in starts))
            sql = runner.calls[scrub][1]["input"]
            self.assertIn("UPDATE public.platform_admin_grant", sql)
            self.assertIn("UPDATE public.org_login_session", sql)
            self.assertIn("platform_security_restore_audit", sql)

    def test_failed_personnel_invalidation_never_starts_candidate_apps(self):
        release = self.target / "releases" / "0.2.0"
        manifest = json.loads((release / "release.json").read_text())
        manifest["securityCapabilities"] = {"platform-admin-session-v1": manifest["images"]["PLATFORM_IMAGE"]}
        (release / "release.json").write_text(json.dumps(manifest))
        (release / ".env").write_text("PLATFORM_ADMIN_ENFORCE_PERSONNEL=true\n")
        floor = self.target / ".surveyctl" / "auth-security-floor"
        floor.parent.mkdir()
        floor.write_text("platform-admin-session-v1\n")
        output = self.create_backup()
        runner = FakeRunner(fail_when=lambda command: "psql" in command)
        with self.assertRaisesRegex(self.restore.RestoreRuntimeError, "invalidation failed"):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        self.assertFalse(any("up" in command and "platform" in command for command, _ in runner.calls))

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
            elif attack in {"file-before-child", "child-before-file"}:
                entries = (("parent", b"file"), ("parent/child", b"child"))
                if attack == "child-before-file":
                    entries = reversed(entries)
                for name, payload in entries:
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
            elif attack == "casefold-parent-child":
                info = tarfile.TarInfo("Parent/child")
                info.size = 1
                archive.addfile(info, io.BytesIO(b"x"))
                info = tarfile.TarInfo("parent")
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif attack == "unicode-normalization":
                for name in ("caf\u00e9.txt", unicodedata.normalize("NFD", "caf\u00e9.txt")):
                    info = tarfile.TarInfo(name)
                    info.size = 1
                    archive.addfile(info, io.BytesIO(b"x"))
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
        attacks += ("file-before-child", "child-before-file", "casefold-parent-child", "unicode-normalization")
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

    def _tar_entries(self, entries):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, kind in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.size = 1 if kind == tarfile.REGTYPE else 0
                archive.addfile(member, io.BytesIO(b"x") if member.size else None)
        return stream.getvalue()

    def test_docker_dot_prefix_archives_verify_without_rewriting(self):
        # docker cp container:/volume/. - emits a '.' directory and './child'.
        payload = self._tar_entries([
            (".", tarfile.DIRTYPE), ("./nested", tarfile.DIRTYPE),
            ("./nested/value", tarfile.REGTYPE), ("./empty", tarfile.DIRTYPE),
        ])
        output = self.create_backup(name="docker-dot-paths")
        for volume in self.backup.VOLUME_SOURCES:
            plain = self.root / volume
            plain.write_bytes(payload)
            FakeCipher().encrypt(plain, output / f"{volume}.enc", self.key_file)
        self.backup.rewrite_authenticated_control(output, self.key_file)
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as temporary:
            _, decrypted = self.restore.RestoreManager(
                self.target, runner=runner, cipher=FakeCipher()
            ).verify_backup(output, Path(temporary))
            for volume in self.backup.VOLUME_SOURCES:
                self.assertEqual(payload, decrypted[volume].read_bytes())
        self.assertEqual([], runner.calls)

    def test_dot_prefix_does_not_hide_unsafe_paths_or_aliases(self):
        bad = [
            [(name, tarfile.REGTYPE)] for name in (
                "./../escape", "./dir/../escape", ".//absolute", "././repeat",
                "./dir//file", "./dir/./file", "./dir\\file", "./", ".",
            )
        ]
        bad += [
            [("././", tarfile.DIRTYPE)], [("./.", tarfile.DIRTYPE)],
            [("./same", tarfile.REGTYPE), ("same", tarfile.REGTYPE)],
            [(".", tarfile.DIRTYPE), ("./", tarfile.DIRTYPE)],
            [("./Parent/child", tarfile.REGTYPE), ("parent", tarfile.DIRTYPE)],
            [("./parent", tarfile.REGTYPE), ("parent/child", tarfile.REGTYPE)],
            [("parent/child", tarfile.REGTYPE), ("./parent", tarfile.REGTYPE)],
            [("./caf\u00e9", tarfile.REGTYPE), ("cafe\u0301", tarfile.REGTYPE)],
        ]
        for index, entries in enumerate(bad):
            with self.subTest(entries=entries):
                archive = self.root / f"bad-dot-{index}.tar"
                archive.write_bytes(self._tar_entries(entries))
                with self.assertRaises(self.restore.RestoreIntegrityError):
                    self.restore.RestoreManager(self.target)._validate_archive(archive)

    def test_dot_prefix_keeps_entry_type_and_resource_limits(self):
        for attack in ("symlink", "hardlink", "fifo", "device", "socket", "duplicate",
                       "count-limit", "size-limit", "decompression-bomb"):
            with self.subTest(attack=attack):
                original = self._malicious_tar(attack)
                stream = io.BytesIO()
                with tarfile.open(fileobj=io.BytesIO(original)) as source, tarfile.open(
                    fileobj=stream, mode="w:gz" if attack == "decompression-bomb" else "w"
                ) as destination:
                    for member in source:
                        member.name = "./" + member.name
                        destination.addfile(member, source.extractfile(member) if member.isfile() else None)
                archive = self.root / f"dot-{attack}.tar"
                archive.write_bytes(stream.getvalue())
                limits = {"max_members": 3, "max_file_bytes": 8, "max_total_bytes": 64}
                if attack == "decompression-bomb":
                    limits = {"max_file_bytes": 8192, "max_total_bytes": 8192,
                              "max_expansion_ratio": 2, "expansion_slack_bytes": 0}
                with self.assertRaises(self.restore.RestoreIntegrityError):
                    self.restore.RestoreManager(self.target, archive_limits=limits)._validate_archive(archive)

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

    def test_backup_waits_for_real_health_after_unpause_before_compose_up(self):
        class StaleHealthRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.resumed = False
                self.health_checks = 0
                self.ready = False
            def run(self, command, **kwargs):
                if 'unpause' in command:
                    self.resumed = True
                if self.resumed and 'ps' in command and '--format' in command:
                    self.health_checks += 1
                    self.ready = self.health_checks >= 2
                    self.calls.append((tuple(command), kwargs))
                    rows = [{'Service': name, 'State': 'running',
                             'Health': 'healthy' if self.ready else 'unhealthy'}
                            for name in self_writer_services]
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps(rows), stderr='')
                if self.resumed and 'up' in command and not self.ready:
                    raise subprocess.CalledProcessError(1, command, stderr='dependency is unhealthy')
                return super().run(command, **kwargs)
        self_writer_services = self.backup.WRITER_SERVICES
        runner = StaleHealthRunner()
        with mock.patch('time.sleep'):
            output = self.create_backup(runner)
        self.assertTrue((output / 'CONTROL-HMAC').is_file())
        self.assertGreaterEqual(runner.health_checks, 2)
        commands = [call[0] for call in runner.calls]
        unpause = next(i for i, command in enumerate(commands) if 'unpause' in command)
        checks = [i for i, command in enumerate(commands) if '--format' in command and i > unpause]
        up = next(i for i, command in enumerate(commands) if 'up' in command)
        self.assertTrue(all(unpause < check < up for check in checks))

    def test_backup_permanent_unhealthy_resume_removes_output_and_does_not_reconcile(self):
        class UnhealthyRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.resumed = False
                self.health_checks = 0
            def run(self, command, **kwargs):
                if 'unpause' in command:
                    self.resumed = True
                if self.resumed and 'ps' in command and '--format' in command:
                    self.health_checks += 1
                    self.calls.append((tuple(command), kwargs))
                    rows = [{'Service': name, 'State': 'running', 'Health': 'unhealthy'}
                            for name in self_writer_services]
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps(rows), stderr='')
                return super().run(command, **kwargs)
        self_writer_services = self.backup.WRITER_SERVICES
        runner = UnhealthyRunner()
        output = self.target / 'backups' / 'unhealthy'
        with mock.patch('time.sleep'):
            with self.assertRaises(self.backup.BackupError):
                self.backup.BackupManager(self.target, runner=runner, cipher=FakeCipher()).create(output, '0.2.0')
        self.assertFalse(output.exists())
        self.assertGreater(runner.health_checks, 0)
        self.assertLess(runner.health_checks, 100)
        self.assertFalse(any('up' in command for command, _ in runner.calls))

    def test_backup_query_failures_delete_complete_output(self):
        for mode in ('timeout', 'nonzero', 'malformed-json'):
            with self.subTest(mode=mode):
                output = self.target / 'backups' / mode
                class Runner(FakeRunner):
                    def __init__(self):
                        super().__init__()
                        self.resumed = False
                        self.complete_output_existed = False
                    def run(self, command, **kwargs):
                        if 'unpause' in command:
                            self.resumed = True
                        if self.resumed and 'ps' in command and '--format' in command:
                            self.complete_output_existed = output.exists()
                            if mode == 'timeout': raise subprocess.TimeoutExpired(command, 10)
                            if mode == 'nonzero': raise subprocess.CalledProcessError(1, command)
                            return subprocess.CompletedProcess(command, 0, stdout='{invalid', stderr='')
                        return super().run(command, **kwargs)
                runner = Runner()
                with mock.patch('time.sleep'), self.assertRaises(self.backup.BackupError):
                    self.backup.BackupManager(self.target, runner=runner, cipher=FakeCipher()).create(output, '0.2.0')
                self.assertTrue(runner.complete_output_existed)
                self.assertFalse(output.exists())
                self.assertFalse(any('up' in command for command, _ in runner.calls))

    def test_backup_resume_accepts_line_delimited_health(self):
        runner = FakeRunner(line_delimited_health=True)
        self.backup.BackupManager(self.target, runner=runner)._wait_writers_healthy(['docker', 'compose', '-f', 'fixture'])

    def test_backup_resume_rejects_incomplete_duplicate_and_paused_health(self):
        rows = [{'Service': name, 'State': 'running', 'Health': 'healthy'}
                for name in self.backup.WRITER_SERVICES]
        paused = [dict(row, State='paused') for row in rows]
        for payload in (rows[:-1], rows + [rows[0]], paused):
            with self.subTest(payload=payload):
                class Runner:
                    def run(self, command, **kwargs):
                        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr='')
                with mock.patch('time.sleep'), self.assertRaises(self.backup.BackupError):
                    self.backup.BackupManager(self.target, runner=Runner())._wait_writers_healthy([])

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

    def test_restore_waits_for_databases_before_import_in_both_projects(self):
        output = self.create_backup(name="database-readiness")

        class StartingDatabases(FakeRunner):
            def __init__(self):
                super().__init__()
                self.ready = set()
            def run(self, command, **kwargs):
                if "up" in command and "platform-db" in command and "engine-db" in command:
                    project = command[command.index("-p") + 1]
                    if "--wait" in command and "--wait-timeout" in command:
                        self.ready.add(project)
                if "pg_restore" in command:
                    project = command[command.index("-p") + 1]
                    if project not in self.ready:
                        raise subprocess.CalledProcessError(1, command, stderr="fixture database starting")
                return super().run(command, **kwargs)

        runner = StartingDatabases()
        self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        self.assertEqual(2, len(runner.ready))
        self.assertIn("survey-production", runner.ready)
        waits = [command for command, _ in runner.calls
                 if "up" in command and "platform-db" in command and "engine-db" in command]
        self.assertEqual(2, len(waits))
        for command in waits:
            self.assertEqual("120", command[command.index("--wait-timeout") + 1])

    def test_restore_database_readiness_failure_never_imports_or_switches(self):
        output = self.create_backup(name="database-never-ready")

        class FailedReadiness(FakeRunner):
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                if "up" in command and "--wait" in command and "platform-db" in command:
                    raise subprocess.CalledProcessError(1, command, stderr="fixture health timeout")
                return result

        runner = FailedReadiness()
        with self.assertRaisesRegex(self.restore.RestoreRuntimeError, "restore database startup failed"):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        commands = [command for command, _ in runner.calls]
        self.assertFalse(any("pg_restore" in command or any("mariadb" in str(part) for part in command)
                             for command in commands))
        self.assertFalse(any("survey-production" in command for command in commands))
        self.assertTrue(any("down" in command and "--volumes" in command for command in commands))

    def test_restore_waits_for_apps_before_checking_both_restored_projects(self):
        output = self.create_backup(name="application-readiness")

        class StartingApps(FakeRunner):
            def __init__(self):
                super().__init__()
                self.ready = {"survey-production"}
            def run(self, command, **kwargs):
                project = command[command.index("-p") + 1] if "-p" in command else None
                if "down" in command:
                    self.ready.discard(project)
                if "up" in command and ("engine-init" in command or "--remove-orphans" in command):
                    if "--wait" in command and "--wait-timeout" in command:
                        self.ready.add(project)
                result = super().run(command, **kwargs)
                if "ps" in command and "--format" in command and project not in self.ready:
                    rows = json.loads(result.stdout)
                    for row in rows:
                        if row["Service"] == "publish-gateway":
                            row["Health"] = "starting"
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps(rows), stderr="")
                return result

        runner = StartingApps()
        self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        commands = [command for command, _ in runner.calls]
        candidate_up = next(command for command in commands if "up" in command and "engine-init" in command)
        production_down = next(index for index, command in enumerate(commands)
                               if "survey-production" in command and "down" in command)
        production_up = next(command for command in commands[production_down + 1:]
                             if "up" in command and "--remove-orphans" in command)
        for command in (candidate_up, production_up):
            self.assertIn("--wait", command)
            self.assertEqual("120", command[command.index("--wait-timeout") + 1])

    def test_restore_application_readiness_failure_never_switches_production(self):
        output = self.create_backup(name="applications-never-ready")
        runner = FakeRunner(fail_when=lambda command: "up" in command and "--wait" in command
                            and "engine-init" in command)
        with self.assertRaisesRegex(self.restore.RestoreRuntimeError, "isolated restored stack startup failed"):
            self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        commands = [command for command, _ in runner.calls]
        self.assertFalse(any("survey-production" in command for command in commands))
        self.assertTrue(any("down" in command and "--volumes" in command for command in commands))

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
        recovery_starts = [command for command, _ in runner.calls
                           if "survey-production" in command and "up" in command
                           and "--wait" in command and "--remove-orphans" in command]
        self.assertEqual(1, len(recovery_starts))
        command = recovery_starts[0]
        self.assertEqual(("up", "-d", "--wait", "--wait-timeout", "120", "--remove-orphans"),
                         command[command.index("up"):])
        safety = list((self.target / "backups").glob("pre-restore-safety-*"))
        self.assertEqual(1, len(safety))

    def test_restore_preserves_recorded_postgres_owners_in_both_projects(self):
        output = self.create_backup(name="preserve-postgres-owners")
        runner = FakeRunner()
        self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher()).restore(output)
        imports = [command for command, _ in runner.calls if "pg_restore" in command]
        self.assertEqual(2, len(imports))
        for command in imports:
            self.assertNotIn("--no-owner", command)
            self.assertNotIn("--no-acl", command)
            self.assertEqual(("pg_restore", "-U", "postgres", "-d", "platform", "--clean", "--if-exists"),
                             command[command.index("pg_restore"):])

    def test_production_application_wait_failure_recovers_safety_and_reports_failure(self):
        output = self.create_backup(name="switch-app-wait-fails")

        class FailFirstSwitchedApplicationWait(FakeRunner):
            def __init__(self):
                super().__init__()
                self.production_waits = []
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                if ("survey-production" in command and "up" in command
                        and "--wait" in command and "--remove-orphans" in command):
                    self.production_waits.append(tuple(command))
                    if len(self.production_waits) == 1:
                        raise subprocess.CalledProcessError(1, command, stderr="fixture application wait failed")
                return result

        runner = FailFirstSwitchedApplicationWait()
        manager = self.restore.RestoreManager(self.target, runner=runner, cipher=FakeCipher())
        with mock.patch.object(manager, "verify_backup", wraps=manager.verify_backup) as verified:
            with self.assertRaisesRegex(
                self.restore.RestoreRuntimeError,
                "^production restore failed; previous stack recovered from safety backup$",
            ) as failure:
                manager.restore(output)
        self.assertIsInstance(failure.exception.__cause__, self.restore.RestoreRuntimeError)
        self.assertEqual("restored production startup failed", str(failure.exception.__cause__))
        self.assertIsInstance(failure.exception.__cause__.__cause__, subprocess.CalledProcessError)
        safety = list((self.target / "backups").glob("pre-restore-safety-*"))
        self.assertEqual(1, len(safety))
        self.assertEqual([output, output, safety[0]], [call.args[0] for call in verified.call_args_list])
        self.assertEqual(2, len(runner.production_waits))
        for command in runner.production_waits:
            self.assertEqual(("up", "-d", "--wait", "--wait-timeout", "120", "--remove-orphans"),
                             command[command.index("up"):])
        commands = [command for command, _ in runner.calls]
        production_downs = [index for index, command in enumerate(commands)
                            if "survey-production" in command and "down" in command]
        production_restores = [index for index, command in enumerate(commands)
                               if "survey-production" in command and "pg_restore" in command]
        production_waits = [index for index, command in enumerate(commands)
                            if command in runner.production_waits]
        self.assertEqual(3, len(production_downs))
        self.assertEqual(2, len(production_restores))
        self.assertLess(production_restores[0], production_waits[0])
        self.assertLess(production_waits[0], production_downs[1])
        self.assertLess(production_downs[1], production_downs[2])
        self.assertLess(production_downs[2], production_restores[1])
        self.assertLess(production_restores[1], production_waits[1])
        self.assertTrue(any("survey-production" in command and "ps" in command and "--format" in command
                            for command in commands[production_waits[1] + 1:]))

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
