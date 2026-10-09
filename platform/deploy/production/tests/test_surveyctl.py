import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


PRODUCTION_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PRODUCTION_DIR / "surveyctl.py"
FIXTURE_MANIFEST = Path(__file__).resolve().parent / "fixtures" / "release.json"


def write_fake_backup(output, version="0.2.0", schema="920"):
    output.mkdir(parents=True, exist_ok=True)
    payload = output / "payload.bin"
    payload.write_bytes(b"verified backup")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    images = json.loads(FIXTURE_MANIFEST.read_text())["images"]
    (output / "manifest.json").write_text(json.dumps({
        "schemaVersion": 2, "version": version, "databaseSchema": schema,
        "releaseManifestSha256": "a" * 64, "images": images, "files": {"payload.bin": digest},
    }))
    (output / "SHA256SUMS").write_text(f"{digest}  payload.bin\n")
    (output / "CONTROL-HMAC").write_text("0" * 64 + "\n")


def write_test_release(root, version="0.2.0", rollback="compatible", **updates):
    bundle = Path(root) / f"survey-{version}-linux-arm64.tar.gz"
    files = {
        "compose.yml": b"name: verified-bundle\nservices: {}\n",
        "Caddyfile": b":443 { respond 204 }\n",
        "surveyctl": b"#!/bin/sh\n",
        "surveyctl.py": b"# verified controller\n",
        "backup.py": b"# verified backup helper\n",
        "restore.py": b"# verified restore helper\n",
        "doctor.py": b"# verified doctor helper\n",
        "release.schema.json": b"{}\n",
        "env.example": b"PUBLIC_HOST=example.invalid\n",
        "engines.example.json": b"{}\n",
        "init/marker.txt": b"verified init\n",
    }
    metadata = {
        "schemaVersion": 1,
        "version": version,
        "architecture": "arm64",
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
    }
    files["bundle-metadata.json"] = json.dumps(metadata, sort_keys=True).encode()
    with tarfile.open(bundle, "w:gz") as archive:
        for name, value in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            info.mode = 0o755 if name in {"surveyctl", "surveyctl.py"} else 0o644
            archive.addfile(info, io.BytesIO(value))
    data = json.loads(FIXTURE_MANIFEST.read_text())
    data["version"] = version
    data["database"]["rollback"] = rollback
    data["supportedHosts"]["architectures"] = ["arm64"]
    data["assets"]["bundles"] = {
        "arm64": {"name": bundle.name, "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest()}
    }
    data.update(updates)
    manifest = Path(root) / f"release-{version}.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest, bundle


def load_module():
    spec = importlib.util.spec_from_file_location("surveyctl_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(
        self,
        fail_when=None,
        unhealthy_service=None,
        empty_health=False,
        create_backup=True,
        stdout="",
        stderr="",
        backup_schema="920",
        engine_init_exit_code=0,
        engine_init_health="",
    ):
        self.commands = []
        self.calls = []
        self.fail_when = fail_when
        self.unhealthy_service = unhealthy_service
        self.empty_health = empty_health
        self.create_backup = create_backup
        self.stdout = stdout
        self.stderr = stderr
        self.backup_schema = backup_schema
        self.engine_init_exit_code = engine_init_exit_code
        self.engine_init_health = engine_init_health

    def run(self, command, **kwargs):
        command = tuple(str(part) for part in command)
        self.commands.append(command)
        self.calls.append((command, kwargs))
        if self.fail_when and self.fail_when(command):
            raise subprocess.CalledProcessError(1, command)
        if "hash-password" in command:
            return subprocess.CompletedProcess(command, 0, stdout="$2a$14$" + "x" * 53 + "\n", stderr="")
        if "backup.py" in " ".join(command) and "backup" in command and self.create_backup:
            output = Path(command[command.index("--output") + 1])
            version = command[command.index("--version") + 1]
            write_fake_backup(output, version, self.backup_schema)
        if "ps" in command and "--format" in command:
            services = ["edge", "admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db"]
            payload = [
                {
                    "Service": service,
                    "State": "running",
                    "Health": "" if self.empty_health else ("unhealthy" if service == self.unhealthy_service else "healthy"),
                }
                for service in services
            ]
            payload.append({
                "Service": "engine-init",
                "State": "exited",
                "Health": self.engine_init_health,
                "ExitCode": self.engine_init_exit_code,
            })
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr=self.stderr)


class FakeHostProbe:
    def __init__(self, **overrides):
        self.values = {
            "os_id": "ubuntu",
            "os_version": "24.04",
            "architecture": "arm64",
            "docker_version": "27.5.1",
            "compose_version": "2.32.4",
            "free_bytes": 40 * 1024**3,
            "memory_bytes": 8 * 1024**3,
            "ports_available": True,
            "dns_addresses": ["203.0.113.10"],
            "local_addresses": ["203.0.113.10"],
            "time_synchronized": True,
            "systemd": True,
            "dns_public": True,
            "tls_valid": True,
        }
        self.values.update(overrides)

    def inspect(self, host, target=None):
        self.target = target
        return dict(self.values)

    def verify_tls(self, host):
        return self.values["tls_valid"]


class FakeReleaseClient:
    def __init__(self, payload=None, assets=None):
        self.requests = []
        self.payload = payload or FIXTURE_MANIFEST.read_bytes()
        self.assets = assets or {}

    def fetch_manifest(self, version):
        self.requests.append(version)
        return self.payload

    def fetch_asset(self, version, name):
        self.requests.append((version, name))
        return self.assets[name]


class SurveyctlAdversarialTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.target = self.root / "survey"

    def manager(self, runner=None, probe=None, client=None, **kwargs):
        return self.module.SurveyManager(
            target=self.target,
            runner=runner or FakeRunner(),
            host_probe=probe or FakeHostProbe(),
            release_client=client or FakeReleaseClient(),
            source_dir=PRODUCTION_DIR,
            **kwargs,
        )

    def write_bundle_manifest(self, version="0.2.0", rollback="compatible"):
        return write_test_release(self.root, version, rollback)

    def test_verified_bundle_is_the_only_source_of_deployed_files(self):
        manifest, _ = self.write_bundle_manifest()
        self.manager().install(manifest, "survey.example.com", "operations", "operations@example.com")
        deployed = self.target / "releases" / "0.2.0" / "compose.yml"
        self.assertEqual("name: verified-bundle\nservices: {}\n", deployed.read_text())
        self.assertTrue((self.target / "releases" / "0.2.0" / "backup.py").is_file())
        key = self.target / "shared" / "secrets" / "backup_encryption_key"
        self.assertTrue(key.is_file())
        self.assertEqual(0, key.stat().st_mode & 0o077)

    def test_bundle_without_recovery_helpers_is_rejected(self):
        manifest, bundle = self.write_bundle_manifest()
        with tarfile.open(bundle, "r:gz") as source:
            retained = [(member, source.extractfile(member).read()) for member in source.getmembers() if member.isfile() and member.name not in {"backup.py", "restore.py", "doctor.py", "bundle-metadata.json"}]
        files = {member.name: value for member, value in retained}
        metadata = {"schemaVersion": 1, "version": "0.2.0", "architecture": "arm64", "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
        files["bundle-metadata.json"] = json.dumps(metadata, sort_keys=True).encode()
        with tarfile.open(bundle, "w:gz") as archive:
            for name, value in files.items():
                info = tarfile.TarInfo(name); info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
        data = json.loads(manifest.read_text())
        data["assets"]["bundles"]["arm64"]["sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(data))
        with self.assertRaises(self.module.IntegrityError):
            self.manager().install(manifest, "survey.example.com", "operations", "operations@example.com")

    def test_staged_payload_and_generated_config_are_reverified_before_compose(self):
        for name in ("compose.yml", "Caddyfile", ".env"):
            with self.subTest(name=name):
                self.target = self.root / f"survey-drift-{name.replace('.', 'dot')}"
                manifest, _ = self.write_bundle_manifest()
                manager = self.manager()
                manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
                path = self.target / "releases" / "0.2.0" / name
                path.write_text(path.read_text() + "\n# tampered\n")
                with self.assertRaises(self.module.IntegrityError):
                    manager.status()

    def test_bundle_digest_mismatch_fails_before_state_is_written(self):
        manifest, bundle = self.write_bundle_manifest()
        bundle.write_bytes(bundle.read_bytes() + b"tamper")
        with self.assertRaises(self.module.IntegrityError):
            self.manager().install(manifest, "survey.example.com", "operations", "operations@example.com")
        self.assertFalse((self.target / ".surveyctl" / "state.json").exists())

    def test_bundle_rejects_traversal_and_symlink_entries(self):
        for kind in ("traversal", "symlink"):
            with self.subTest(kind=kind):
                self.target = self.root / f"survey-{kind}"
                manifest, bundle = self.write_bundle_manifest(version="0.2.1" if kind == "traversal" else "0.2.2")
                with tarfile.open(bundle, "w:gz") as archive:
                    info = tarfile.TarInfo("../escape" if kind == "traversal" else "unsafe-link")
                    if kind == "symlink":
                        info.type = tarfile.SYMTYPE
                        info.linkname = "/etc/passwd"
                        archive.addfile(info)
                    else:
                        payload = b"escape"
                        info.size = len(payload)
                        archive.addfile(info, io.BytesIO(payload))
                data = json.loads(manifest.read_text())
                data["assets"]["bundles"]["arm64"]["sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
                manifest.write_text(json.dumps(data))
                with self.assertRaises(self.module.IntegrityError):
                    self.manager().install(manifest, "survey.example.com", "operations", "operations@example.com")

    def test_download_symlink_cannot_escape_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / ".surveyctl").mkdir(parents=True)
        (self.target / ".surveyctl" / "downloads").symlink_to(outside, target_is_directory=True)
        digest = hashlib.sha256(FIXTURE_MANIFEST.read_bytes()).hexdigest()
        with self.assertRaises(self.module.InputError):
            self.manager().install_version("0.2.0", digest, "survey.example.com", "operations", "operations@example.com")
        self.assertEqual([], list(outside.iterdir()))

    def test_requested_remote_version_must_equal_manifest_version(self):
        manifest, bundle = self.write_bundle_manifest()
        client = FakeReleaseClient(manifest.read_bytes(), {bundle.name: bundle.read_bytes()})
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        with self.assertRaises(self.module.IntegrityError):
            self.manager(client=client).install_version(
                "0.3.0", digest, "survey.example.com", "operations", "operations@example.com"
            )

    def test_rollback_rejects_traversal_and_manifest_version_mismatch(self):
        manifest, _ = self.write_bundle_manifest()
        self.manager().install(manifest, "survey.example.com", "operations", "operations@example.com")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "release.json").write_bytes(manifest.read_bytes())
        (outside / ".env").write_text("")
        (outside / "compose.yml").write_text("services: {}\n")
        with self.assertRaises(self.module.InputError):
            self.manager().rollback("../../outside")
        mismatched, _ = self.write_bundle_manifest("0.1.4")
        mismatch_dir = self.target / "releases" / "0.1.5"
        mismatch_dir.mkdir()
        (mismatch_dir / "release.json").write_bytes(mismatched.read_bytes())
        with self.assertRaises(self.module.IntegrityError):
            self.manager().rollback("0.1.5")

    def test_state_and_current_pointer_symlinks_are_rejected(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        outside = self.root / "outside-version"
        outside.write_text("0.2.0\n")
        current = self.target / "current"
        current.unlink()
        current.symlink_to(outside)
        with self.assertRaises(self.module.InputError):
            manager.status()

    def test_upgrade_requires_a_real_verified_backup_before_pull(self):
        manifest, _ = self.write_bundle_manifest()
        runner = FakeRunner(create_backup=False)
        manager = self.manager(runner=runner)
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        next_manifest, _ = self.write_bundle_manifest("0.3.0")
        runner.commands.clear()
        with self.assertRaises(self.module.IntegrityError):
            manager.upgrade(next_manifest)
        self.assertFalse(any("pull" in command for command in runner.commands))

    def test_upgrade_rejects_corrupt_backup_checksum_before_pull(self):
        manifest, _ = self.write_bundle_manifest()
        runner = FakeRunner()
        manager = self.manager(runner=runner)
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        next_manifest, _ = self.write_bundle_manifest("0.3.0")
        original_run = runner.run

        def corrupting_run(command, **kwargs):
            result = original_run(command, **kwargs)
            if "backup.py" in " ".join(str(item) for item in command):
                output = Path(command[command.index("--output") + 1])
                (output / "payload.bin").write_bytes(b"tampered")
            return result

        runner.run = corrupting_run
        runner.commands.clear()
        with self.assertRaises(self.module.IntegrityError):
            manager.upgrade(next_manifest)
        self.assertFalse(any("pull" in command for command in runner.commands))

    def test_upgrade_rejects_backup_with_wrong_database_schema_before_pull(self):
        manifest, _ = self.write_bundle_manifest()
        runner = FakeRunner(backup_schema="919")
        manager = self.manager(runner=runner)
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        next_manifest, _ = self.write_bundle_manifest("0.3.0")
        runner.commands.clear()

        with self.assertRaises(self.module.IntegrityError) as raised:
            manager.upgrade(next_manifest)

        self.assertEqual(5, raised.exception.exit_code)
        self.assertFalse(any("pull" in command for command in runner.commands))

    def test_backup_rejects_recursive_unlisted_symlinks_special_files_and_content(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")

        attacks = ("directory-symlink", "fifo", "unlisted-file")
        for index, attack in enumerate(attacks):
            with self.subTest(attack=attack):
                backup = manager.backup(f"recursive-{index}")
                if attack == "directory-symlink":
                    outside = self.root / f"outside-{index}"
                    outside.mkdir()
                    (outside / "escaped").write_text("outside")
                    (backup / "unlisted-link").symlink_to(outside, target_is_directory=True)
                elif attack == "fifo":
                    nested = backup / "unlisted-dir"
                    nested.mkdir()
                    os.mkfifo(nested / "special")
                else:
                    nested = backup / "unlisted-dir"
                    nested.mkdir()
                    (nested / "extra").write_text("not in manifest")
                with self.assertRaises(self.module.IntegrityError):
                    manager._verify_backup(backup, "0.2.0", "920")

    def test_restore_only_upgrade_failure_never_starts_old_images_without_restore(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        next_manifest, _ = self.write_bundle_manifest("0.3.0", rollback="restore-only")
        runner = FakeRunner(fail_when=lambda command: ("up" in command and "0.3.0" in " ".join(command))
                            or ("restore.py" in " ".join(command) and "restore" in command))
        with self.assertRaises(self.module.RuntimeHealthError):
            self.manager(runner=runner).upgrade(next_manifest)
        old_starts = [command for command in runner.commands if "up" in command and "0.2.0" in " ".join(command)]
        self.assertEqual([], old_starts)
        self.assertTrue(any("stop" in command for command in runner.commands))
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("recovery_required", state["status"])

    def test_health_requires_nonempty_healthy_status_for_exact_service_set(self):
        manifest, _ = self.write_bundle_manifest()
        with self.assertRaises(self.module.RuntimeHealthError):
            self.manager(runner=FakeRunner(empty_health=True)).install(
                manifest, "survey.example.com", "operations", "operations@example.com"
            )

    def test_malformed_nested_manifest_is_integrity_error_not_attribute_error(self):
        data = json.loads(FIXTURE_MANIFEST.read_text())
        data["database"] = []
        path = self.root / "malformed.json"
        path.write_text(json.dumps(data))
        with self.assertRaises(self.module.IntegrityError):
            self.module.load_manifest(path)

    def test_manifest_schema_rejects_nested_types_extra_keys_and_empty_compatibility(self):
        cases = []
        base = json.loads(FIXTURE_MANIFEST.read_text())
        value = json.loads(json.dumps(base)); value["supportedHosts"] = "ubuntu"; cases.append(value)
        value = json.loads(json.dumps(base)); value["images"] = []; cases.append(value)
        value = json.loads(json.dumps(base)); value["database"]["compatibleSourceSchemas"] = []; cases.append(value)
        value = json.loads(json.dumps(base)); del value["database"]["backupSchema"]; cases.append(value)
        value = json.loads(json.dumps(base)); value["database"]["backupSchema"] = True; cases.append(value)
        value = json.loads(json.dumps(base)); value["database"]["backupSchema"] = 1; cases.append(value)
        value = json.loads(json.dumps(base)); value["database"]["unexpected"] = True; cases.append(value)
        value = json.loads(json.dumps(base)); value["assets"]["bundles"]["amd64"]["unexpected"] = True; cases.append(value)
        for index, value in enumerate(cases):
            with self.subTest(index=index):
                path = self.root / f"schema-fuzz-{index}.json"
                path.write_text(json.dumps(value))
                with self.assertRaises(self.module.IntegrityError):
                    self.module.load_manifest(path)

    def test_schema_rejects_bool_for_integer_const_and_all_wrong_json_types(self):
        value = json.loads(FIXTURE_MANIFEST.read_text())
        value["schemaVersion"] = True
        path = self.root / "bool-schema-version.json"
        path.write_text(json.dumps(value))
        with self.assertRaises(self.module.IntegrityError):
            self.module.load_manifest(path)

        cases = (
            ({"type": "integer"}, True),
            ({"type": "integer"}, 1.0),
            ({"type": "number"}, True),
            ({"type": "number"}, "1.5"),
            ({"type": "string"}, 1),
            ({"type": "array"}, {}),
            ({"type": "object"}, []),
        )
        for schema, instance in cases:
            with self.subTest(schema=schema, instance=instance):
                with self.assertRaises(self.module.IntegrityError):
                    self.module._validate_json_schema(instance, schema, "$")

    def test_preflight_requires_systemd_minimum_versions_target_space_and_tls(self):
        manifest, _ = self.write_bundle_manifest()
        release = self.module.load_manifest(manifest)
        for overrides in (
            {"systemd": False},
            {"docker_version": "23.0.0"},
            {"compose_version": "2.19.9"},
            {"free_bytes": 1024},
            {"dns_public": False},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(self.module.EnvironmentError):
                    self.manager(probe=FakeHostProbe(**overrides)).preflight(release, "survey.example.com")
        probe = FakeHostProbe()
        self.manager(probe=probe).preflight(release, "survey.example.com")
        self.assertEqual(self.target, probe.target)

    def test_preflight_requires_dns_to_match_local_or_explicit_expected_address(self):
        manifest, _ = self.write_bundle_manifest()
        release = self.module.load_manifest(manifest)
        probe = FakeHostProbe(dns_addresses=["8.8.8.8"], local_addresses=["203.0.113.10"])
        with self.assertRaises(self.module.EnvironmentError):
            self.manager(probe=probe).preflight(release, "survey.example.com")

        manager = self.manager(probe=probe, expected_public_addresses=["8.8.8.8"])
        values = manager.preflight(release, "survey.example.com")
        self.assertEqual(["8.8.8.8"], values["dns_addresses"])

    def test_post_start_tls_failure_is_runtime_error(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager(probe=FakeHostProbe(tls_valid=False))
        with self.assertRaises(self.module.RuntimeHealthError):
            manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("install_failed", state["status"])

    def test_tls_probe_requires_the_deployment_specific_marker(self):
        class Response:
            status = 200

            def __init__(self, body, marker):
                self.body = body
                self.headers = {"X-Survey-Deployment": marker}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return self.body

        probe = self.module.HostProbe()
        responses = (
            (Response(b"unrelated", "survey-production-v1"), False),
            (Response(b"survey-production-v1\n", "wrong"), False),
            (Response(b"survey-production-v1\n", "survey-production-v1"), True),
        )
        for response, expected in responses:
            with self.subTest(expected=expected):
                with mock.patch.object(self.module.urllib.request, "urlopen", return_value=response):
                    self.assertEqual(expected, probe.verify_tls("survey.example.com"))

    def test_purge_refuses_symlink_and_reports_remaining_data(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        outside = self.root / "sensitive"
        outside.mkdir()
        (outside / "keep").write_text("data")
        shared = self.target / "shared"
        for path in sorted(shared.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        shared.rmdir()
        shared.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(self.module.RuntimeHealthError):
            manager.uninstall(True, f"PURGE {manager.target}")
        self.assertTrue((outside / "keep").exists())
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("purge_failed", state["status"])

    def test_purge_deletion_error_is_nonzero_and_diagnostic(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        with mock.patch.object(self.module.shutil, "rmtree", side_effect=PermissionError("denied")):
            with self.assertRaises(self.module.RuntimeHealthError) as raised:
                manager.uninstall(True, f"PURGE {manager.target}")
        self.assertIn("managed data remains", str(raised.exception))
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("purge_failed", state["status"])

    def test_doctor_redacts_secret_values_and_sensitive_assignments(self):
        manifest, _ = self.write_bundle_manifest()
        runner = FakeRunner()
        manager = self.manager(runner=runner)
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        secret = (self.target / "shared" / "secrets" / "platform_jwt_hmac_secret").read_text().strip()
        runner.stdout = f"ok secret={secret} token=visible\n"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            manager.doctor()
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("visible", output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())

    def test_backup_restore_paths_reject_absolute_parent_and_symlink(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        for value in (self.root / "absolute", "../escape", "nested/name"):
            with self.subTest(value=value):
                with self.assertRaises(self.module.InputError):
                    manager.backup(value)

    def test_cli_malformed_manifest_returns_integrity_code_without_traceback(self):
        malformed = self.root / "malformed.json"
        malformed.write_text('{"schemaVersion":1,"database":[]}')
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--target", str(self.target), "install", "--manifest", str(malformed),
             "--public-host", "survey.example.com", "--admin-user", "operations", "--admin-email", "operations@example.com"],
            capture_output=True, text=True,
        )
        self.assertEqual(5, result.returncode)
        self.assertNotIn("Traceback", result.stderr)

    def test_offline_install_interface_is_parseable(self):
        args = self.module.build_parser().parse_args([
            "--target", str(self.target), "install", "--offline", "release.tar.gz",
            "--public-host", "survey.example.com", "--admin-user", "operations",
            "--admin-email", "operations@example.com",
        ])
        self.assertEqual("release.tar.gz", args.offline)

    def test_offline_package_installs_only_its_verified_inner_bundle(self):
        manifest, bundle = self.write_bundle_manifest()
        package = self.root / "offline-package.tar.gz"
        with tarfile.open(package, "w:gz") as archive:
            archive.add(manifest, arcname="release.json")
            archive.add(bundle, arcname=bundle.name)
        self.manager().install_offline(package, "survey.example.com", "operations", "operations@example.com")
        deployed = self.target / "releases" / "0.2.0" / "compose.yml"
        self.assertEqual("name: verified-bundle\nservices: {}\n", deployed.read_text())

    def test_main_maps_all_operational_errors_without_tracebacks(self):
        for class_name, expected in (("InputError", 2), ("EnvironmentError", 3), ("RuntimeHealthError", 4), ("IntegrityError", 5)):
            with self.subTest(class_name=class_name):
                code = f'''\nimport importlib.util, sys\nspec=importlib.util.spec_from_file_location("surveyctl_cli", {str(MODULE_PATH)!r})\nm=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)\nclass Manager:\n    def __init__(self, target): pass\n    def status(self): raise getattr(m, {class_name!r})("bounded failure")\nm.SurveyManager=Manager\nraise SystemExit(m.main(["--target", {str(self.target)!r}, "status"]))\n'''
                result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
                self.assertEqual(expected, result.returncode, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_malformed_health_runtime_payload_returns_exit_four_without_traceback(self):
        code = f'''\nimport importlib.util, json, subprocess, sys\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location("surveyctl_cli", {str(MODULE_PATH)!r})\nm=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)\nclass Runner:\n    def run(self, command, **kwargs):\n        payload=[{{"Service": name, "State": "running", "Health": "healthy"}} for name in m.LONG_RUNNING_SERVICES]\n        payload.append({{"Service": "engine-init", "State": "exited", "Health": "", "ExitCode": "not-an-integer"}})\n        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")\nreal=m.SurveyManager(Path({str(self.target)!r}), runner=Runner())\nclass Manager:\n    def __init__(self, target): pass\n    def status(self): real._assert_healthy(["docker", "compose"], "malformed health")\nm.SurveyManager=Manager\nraise SystemExit(m.main(["--target", {str(self.target)!r}, "status"]))\n'''
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(4, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        with self.assertRaises(self.module.RuntimeHealthError):
            manager = self.manager(runner=FakeRunner(engine_init_exit_code=False))
            manager._assert_healthy(["docker", "compose"], "malformed health")


class SurveyctlTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.target = self.root / "survey"
        self.runner = FakeRunner()
        self.base_manifest, self.base_bundle = write_test_release(self.root)

    def manifest(self, **updates):
        version = updates.pop("version", "0.2.0")
        return write_test_release(self.root, version=version, **updates)[0]

    def manager(self, probe=None, runner=None):
        return self.module.SurveyManager(
            target=self.target,
            runner=runner or self.runner,
            host_probe=probe or FakeHostProbe(),
            source_dir=PRODUCTION_DIR,
        )

    def test_version_install_uses_release_client_and_requires_checksum(self):
        payload = self.base_manifest.read_bytes()
        client = FakeReleaseClient(payload, {self.base_bundle.name: self.base_bundle.read_bytes()})
        manager = self.module.SurveyManager(
            target=self.target,
            runner=self.runner,
            host_probe=FakeHostProbe(),
            release_client=client,
            source_dir=PRODUCTION_DIR,
        )
        with self.assertRaises(self.module.InputError):
            manager.install_version("0.2.0", None, "survey.example.com", "operations", "operations@example.com")
        digest = hashlib.sha256(payload).hexdigest()
        manager.install_version("0.2.0", digest, "survey.example.com", "operations", "operations@example.com")
        self.assertEqual("0.2.0", client.requests[0])
        self.assertEqual(("0.2.0", self.base_bundle.name), client.requests[1])
        self.assertEqual("0.2.0", (self.target / "current").read_text().strip())

    def test_remote_manifest_must_be_utf8(self):
        manager = self.module.SurveyManager(
            target=self.target,
            runner=self.runner,
            host_probe=FakeHostProbe(),
            release_client=FakeReleaseClient(b"\xff\xfe"),
            source_dir=PRODUCTION_DIR,
        )
        with self.assertRaises(self.module.IntegrityError):
            manager.install_version("0.2.0", hashlib.sha256(b"\xff\xfe").hexdigest(), "survey.example.com", "operations", "operations@example.com")

    def install(self, manifest=None):
        self.manager().install(
            manifest or self.base_manifest,
            public_host="survey.example.com",
            admin_user="operations",
            admin_email="operations@example.com",
        )

    def test_cli_exposes_all_lifecycle_commands_and_fixed_exit_codes(self):
        parser = self.module.build_parser()
        self.assertEqual(
            {"install", "setup-probe", "upgrade", "rollback", "backup", "restore", "status", "doctor", "logs", "uninstall"},
            set(parser._subparsers._group_actions[0].choices),
        )
        self.assertEqual(0, self.module.EXIT_SUCCESS)
        self.assertEqual(2, self.module.EXIT_INPUT)
        self.assertEqual(3, self.module.EXIT_ENVIRONMENT)
        self.assertEqual(4, self.module.EXIT_RUNTIME)
        self.assertEqual(5, self.module.EXIT_INTEGRITY)

    def test_manifest_requires_schema_and_digest_locked_images(self):
        schema = json.loads((PRODUCTION_DIR / "release.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(1, schema["properties"]["schemaVersion"]["const"])
        self.assertEqual("integer", schema["properties"]["schemaVersion"]["type"])
        manifest = self.module.load_manifest(FIXTURE_MANIFEST)
        self.assertEqual("0.2.0", manifest.version)
        self.assertEqual("920", manifest.database_schema)

        invalid = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        invalid["images"]["ADMIN_IMAGE"] = "ghcr.io/example/admin:0.2.0"
        path = self.manifest(**invalid)
        with self.assertRaises(self.module.IntegrityError):
            self.module.load_manifest(path)

    def test_manifest_file_checksum_mismatch_is_integrity_error(self):
        digest = hashlib.sha256(FIXTURE_MANIFEST.read_bytes()).hexdigest()
        self.module.load_manifest(FIXTURE_MANIFEST, expected_sha256=digest)
        with self.assertRaises(self.module.IntegrityError):
            self.module.load_manifest(FIXTURE_MANIFEST, expected_sha256="0" * 64)

    def test_preflight_rejects_unsupported_host_and_capacity(self):
        manager = self.manager(probe=FakeHostProbe(os_id="debian"))
        with self.assertRaises(self.module.EnvironmentError):
            manager.preflight(self.module.load_manifest(FIXTURE_MANIFEST), "survey.example.com")

    def test_install_rejects_environment_injection_and_unhealthy_services(self):
        manager = self.manager()
        with self.assertRaises(self.module.InputError):
            manager.install(self.base_manifest, "survey.example.com\nADMIN_IMAGE=evil", "operations", "operations@example.com")
        unhealthy = self.manager(runner=FakeRunner(unhealthy_service="engine"))
        with self.assertRaises(self.module.RuntimeHealthError):
            unhealthy.install(self.base_manifest, "survey.example.com", "operations", "operations@example.com")
        manager = self.manager(probe=FakeHostProbe(free_bytes=1024, memory_bytes=1024))
        with self.assertRaises(self.module.EnvironmentError):
            manager.preflight(self.module.load_manifest(FIXTURE_MANIFEST), "survey.example.com")

    def test_install_writes_only_target_state_secures_secrets_and_starts_in_order(self):
        self.install()
        self.assertEqual("0.2.0", (self.target / "current").read_text().strip())
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("healthy", state["status"])
        self.assertEqual("920", state["databaseSchema"])
        secret_dir = self.target / "shared" / "secrets"
        self.assertEqual(0o700, stat.S_IMODE(secret_dir.stat().st_mode))
        for secret in secret_dir.iterdir():
            self.assertEqual(0o600, stat.S_IMODE(secret.stat().st_mode))
        joined = [" ".join(command) for command in self.runner.commands]
        self.assertIn("pull", joined[0])
        self.assertIn("hash-password", joined[1])
        self.assertIn("up -d", joined[2])
        self.assertIn("ps", joined[3])
        self.assertTrue((secret_dir / "engine_admin_password_hash").read_text().startswith("$2a$14$"))
        admin_password = (secret_dir / "engine_admin_password").read_text().strip()
        self.assertTrue(all(admin_password not in value for value in joined))
        self.assertTrue(all(str(self.target) in value for value in state["managedPaths"]))
        master = (secret_dir / "platform_engine_events_secret").read_text().strip().encode()
        expected = hmac.new(master, b"mjy-engine-events/v1/production-engine-01", hashlib.sha256).hexdigest()
        self.assertEqual(expected, (secret_dir / "engine_instance_events_secret").read_text().strip())
        self.assertIn(
            f"ENGINE_INSTANCE_EVENTS_SECRET_FILE={secret_dir / 'engine_instance_events_secret'}",
            (self.target / "releases" / "0.2.0" / ".env").read_text(),
        )

    def test_setup_probe_creates_least_privilege_identity_without_logging_secrets(self):
        self.install()
        calls = []

        def api_call(token, method, path, body=None):
            calls.append((method, path, body, token))
            if path == "/v1/platform/tenants":
                return 201, {"id": "11111111-1111-4111-8111-111111111111", "status": "provisioning"}
            if path == "/v1/platform/plans":
                return 201, {"id": "22222222-2222-4222-8222-222222222222", "version": 1}
            if path.endswith("/onboarding"):
                return 200, {"ownerActorId": "production-probe-owner"}
            if path.endswith("/status"):
                return 200, {"status": "active"}
            if path.endswith("/engine-instances"):
                return 201, {"id": "production-engine-01"}
            if path.endswith("/event-secret"):
                return 200, {
                    "engineInstanceId": "production-engine-01",
                    "secret": (self.target / "shared" / "secrets" / "engine_instance_events_secret").read_text().strip(),
                }
            raise AssertionError(path)

        self.manager().setup_probe(api_call=api_call)
        probe = self.target / "shared" / "product-probe.json"
        self.assertEqual(0o600, stat.S_IMODE(probe.stat().st_mode))
        self.assertEqual(
            {"tenantId": "11111111-1111-4111-8111-111111111111", "actorId": "production-probe-owner"},
            json.loads(probe.read_text()),
        )
        plan = next(body for method, path, body, _ in calls if path == "/v1/platform/plans")
        self.assertEqual(
            {"survey.read", "survey.write", "response.collect", "response.export"},
            set(plan["capabilities"]),
        )
        secrets_on_disk = [path.read_text().strip() for path in (self.target / "shared" / "secrets").iterdir()]
        self.assertTrue(all(secret not in "\n".join(" ".join(map(str, call[:3])) for call in calls) for secret in secrets_on_disk))

    def test_helper_exit_codes_are_preserved_by_real_cli_without_traceback(self):
        self.install()
        backup = self.target / "backups" / "sample"
        write_fake_backup(backup)
        helper = self.target / "releases" / "0.2.0" / "restore.py"
        for operation, helper_source, expected in (
            ("verify", "import sys\nsys.exit(5)\n", 5),
            ("restore", "import sys\nsys.exit(0 if 'verify' in sys.argv else 4)\n", 4),
        ):
            with self.subTest(operation=operation):
                helper.write_text(helper_source)
                result = subprocess.run(
                    [sys.executable, str(MODULE_PATH), "--target", str(self.target), "restore", "sample"],
                    capture_output=True, text=True,
                )
                self.assertEqual(expected, result.returncode, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
        backup_helper = self.target / "releases" / "0.2.0" / "backup.py"
        backup_helper.write_text("import sys\nsys.exit(5)\n")
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--target", str(self.target), "backup", "--output", "helper-integrity"],
            capture_output=True, text=True,
        )
        self.assertEqual(5, result.returncode, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_install_refuses_to_overwrite_an_existing_managed_installation(self):
        self.install()
        with self.assertRaises(self.module.InputError):
            self.install()

    def test_upgrade_backs_up_before_pull_and_switches_pointer_after_health(self):
        self.install()
        self.runner.commands.clear()
        next_manifest = self.manifest(version="0.3.0", minimumSourceVersion="0.2.0")
        self.manager().upgrade(next_manifest)
        joined = [" ".join(command) for command in self.runner.commands]
        self.assertIn("backup", joined[0])
        self.assertLess(next(i for i, value in enumerate(joined) if "backup" in value), next(i for i, value in enumerate(joined) if "pull" in value))
        self.assertLess(next(i for i, value in enumerate(joined) if "ps" in value), len(joined))
        self.assertEqual("0.3.0", (self.target / "current").read_text().strip())

    def test_upgrade_refuses_a_lower_version(self):
        self.install()
        lower = self.manifest(version="0.1.9", minimumSourceVersion="0.1.0")
        with self.assertRaises(self.module.InputError):
            self.manager().upgrade(lower)

    def test_failed_upgrade_restores_previous_pointer_and_records_failure(self):
        self.install()
        failing = FakeRunner(fail_when=lambda command: "up" in command and "0.3.0" in " ".join(command))
        next_manifest = self.manifest(version="0.3.0", minimumSourceVersion="0.2.0")
        with self.assertRaises(self.module.RuntimeHealthError):
            self.manager(runner=failing).upgrade(next_manifest)
        self.assertEqual("0.2.0", (self.target / "current").read_text().strip())
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("upgrade_failed", state["status"])
        self.assertEqual("0.2.0", state["version"])
        joined = [" ".join(command) for command in failing.commands]
        recovery_starts = [value for value in joined if "releases/0.2.0/compose.yml" in value and "up -d" in value]
        self.assertEqual(1, len(recovery_starts))

    def test_rollback_enforces_database_compatibility_gate(self):
        self.install()
        compatible = self.manifest(version="0.1.5")
        manager = self.manager()
        manager._stage_release(
            self.module.load_manifest(compatible), "arm64", "survey.example.com", "operations", "operations@example.com"
        )
        manager.rollback("0.1.5")
        self.assertEqual("0.1.5", (self.target / "current").read_text().strip())

        incompatible = self.manifest(version="0.1.0")
        incompatible_data = json.loads(incompatible.read_text())
        incompatible_data["database"]["compatibleSourceSchemas"] = ["918"]
        incompatible.write_text(json.dumps(incompatible_data))
        manager._stage_release(
            self.module.load_manifest(incompatible), "arm64", "survey.example.com", "operations", "operations@example.com"
        )
        with self.assertRaises(self.module.InputError):
            manager.rollback("0.1.0")

        current = self.target / "releases" / "0.1.5" / "release.json"
        current_data = json.loads(current.read_text())
        current_data["database"]["rollback"] = "restore-only"
        current.write_text(json.dumps(current_data))
        with self.assertRaises(self.module.InputError):
            manager.rollback("0.2.0")

    def test_restore_status_doctor_and_logs_delegate_without_exposing_secrets(self):
        self.install()
        self.runner.commands.clear()
        manager = self.manager()
        manager.backup("sample")
        manager.restore("sample")
        manager.status()
        manager.doctor()
        manager.logs("platform")
        output = "\n".join(" ".join(command) for command in self.runner.commands)
        self.assertIn("backup", output)
        self.assertIn("restore", output)
        self.assertIn("ps", output)
        self.assertIn("logs --no-color platform", output)
        for secret in (self.target / "shared" / "secrets").iterdir():
            self.assertNotIn(secret.read_text().strip(), output)

    def test_restore_accepts_only_release_declared_source_schemas(self):
        self.install()
        backup = self.target / "backups" / "older-compatible"
        write_fake_backup(backup, "0.1.9", "919")
        self.runner.commands.clear()
        self.manager().restore("older-compatible")
        self.assertTrue(any("restore.py" in " ".join(command) for command in self.runner.commands))

        manifest = json.loads((backup / "manifest.json").read_text())
        manifest["databaseSchema"] = "918"
        (backup / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaises(self.module.IntegrityError):
            self.manager().restore("older-compatible")

    def test_uninstall_preserves_data_and_purge_requires_exact_second_confirmation(self):
        self.install()
        manager = self.manager()
        manager.uninstall(purge_data=False, confirmation=None)
        self.assertTrue((self.target / "shared").exists())
        with self.assertRaises(self.module.InputError):
            manager.uninstall(purge_data=True, confirmation="yes")
        manager.uninstall(purge_data=True, confirmation=f"PURGE {self.target.resolve()}")
        self.assertFalse((self.target / "shared").exists())

    def test_refuses_target_outside_owned_state_boundary(self):
        self.target.mkdir(parents=True)
        (self.target / "unmanaged.txt").write_text("keep")
        with self.assertRaises(self.module.InputError):
            self.manager().install(
                self.base_manifest,
                public_host="survey.example.com",
                admin_user="operations",
                admin_email="operations@example.com",
            )
        self.assertEqual("keep", (self.target / "unmanaged.txt").read_text())


if __name__ == "__main__":
    unittest.main()
