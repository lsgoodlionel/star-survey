import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
import signal
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
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


def write_test_release(root, version="0.2.0", rollback="compatible", restore_source=None, payload_overrides=None, **updates):
    bundle = Path(root) / f"survey-{version}-linux-arm64.tar.gz"
    files = {
        "compose.yml": b"name: verified-bundle\nservices: {}\n",
        "Caddyfile": b":443 { respond 204 }\n",
        "surveyctl": b"#!/bin/sh\n",
        "surveyctl.py": b"# verified controller\n",
        "backup.py": b"# verified backup helper\n",
        "restore.py": restore_source or b"# verified restore helper\n",
        "doctor.py": b"# verified doctor helper\n",
        "release.schema.json": b"{}\n",
        "env.example": b"PUBLIC_HOST=example.invalid\n",
        "engines.example.json": (PRODUCTION_DIR / 'engines.example.json').read_bytes(),
        "init/marker.txt": b"verified init\n",
    }
    if payload_overrides:
        files.update(payload_overrides)
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
    data["securityCapabilities"] = {"platform-admin-session-v1": data["images"]["PLATFORM_IMAGE"]}
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
        configured_relay=True,
        running_relay=True,
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
        self.configured_relay = configured_relay
        self.running_relay = running_relay

    def run(self, command, **kwargs):
        command = tuple(str(part) for part in command)
        self.commands.append(command)
        self.calls.append((command, kwargs))
        if self.fail_when and self.fail_when(command):
            raise subprocess.CalledProcessError(1, command)
        if "hash-password" in command:
            return subprocess.CompletedProcess(command, 0, stdout="$2a$14$" + "x" * 53 + "\n", stderr="")
        if "cp" in command and "root.crt" in " ".join(command):
            destination = Path(command[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("test native acceptance CA", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "backup.py" in " ".join(command) and "backup" in command and self.create_backup:
            output = Path(command[command.index("--output") + 1])
            version = command[command.index("--version") + 1]
            write_fake_backup(output, version, self.backup_schema)
        if "config" in command and "--services" in command:
            services = {"edge", "admin-web", "platform", "publish-gateway", "engine",
                        "platform-db", "engine-db", "engine-init"}
            if self.configured_relay:
                services.add("engine-relay")
            return subprocess.CompletedProcess(command, 0, stdout="\n".join(
                sorted(services)) + "\n", stderr="")
        if "ps" in command and "--format" in command:
            services = ["edge", "admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db"]
            if self.running_relay:
                services.append("engine-relay")
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

    def verify_tls(self, host, ca_file=None):
        self.ca_file = ca_file
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

    def test_compose_resolves_bundle_mounts_from_the_selected_release(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        command = manager._compose("0.2.0")
        self.assertEqual(
            str(self.target / "releases" / "0.2.0"),
            command[command.index("--project-directory") + 1],
        )

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

    def test_recovery_stops_target_relay_and_rejects_unverified_inventory(self):
        module = self.module
        class RecoveryRunner(FakeRunner):
            def __init__(runner, *, mode='normal'):
                super().__init__()
                runner.mode = mode
                runner.stopped = False
            def run(runner, command, **kwargs):
                if 'stop' in command and 'engine-relay' in command:
                    if runner.mode == 'stop-error':
                        raise subprocess.CalledProcessError(1, command)
                    runner.stopped = True
                if 'ps' in command and '--format' in command:
                    rows = [{'Service': 'platform', 'State': 'running'},
                            {'Service': 'engine-relay', 'ID': 'a' * 64 if not runner.stopped or runner.mode != 'replaced' else 'b' * 64, 'State': 'exited' if runner.stopped and runner.mode != 'restart' else 'running'}]
                    if runner.mode == 'absent':
                        rows = [rows[0]]
                    if runner.mode == 'unknown':
                        rows.append({'Service': 'unknown-writer', 'State': 'running'})
                    if runner.mode == 'duplicate':
                        rows.append({'Service': 'engine-relay', 'State': 'running'})
                    return subprocess.CompletedProcess(command, 0, stdout=json.dumps(rows), stderr='')
                return super().run(command, **kwargs)
        for mode in ('normal', 'absent', 'stop-error', 'restart', 'replaced', 'unknown', 'duplicate'):
            with self.subTest(mode=mode):
                runner = RecoveryRunner(mode=mode)
                manager = self.manager(runner=runner)
                if mode in ('normal', 'absent'):
                    self.assertTrue(manager._stop_new_writers_before_legacy_restore(['docker', 'compose']))
                    self.assertEqual(mode == 'normal', runner.stopped)
                else:
                    with self.assertRaises(module.RuntimeHealthError):
                        manager._stop_new_writers_before_legacy_restore(['docker', 'compose'])

    def test_restore_only_never_calls_legacy_restore_when_writer_quiescence_fails(self):
        manager = self.manager()
        manifest = mock.Mock(version='0.3.0', rollback='restore-only')
        failure = self.module.RuntimeHealthError('upgrade failed')
        with mock.patch.object(manager, '_compose', return_value=['docker', 'compose']), \
             mock.patch.object(manager, '_personnel_guard_enabled', return_value=False), \
             mock.patch.object(manager, '_stop_new_writers_before_legacy_restore', side_effect=self.module.RuntimeHealthError('writer')) as stop, \
             mock.patch.object(manager, '_run_helper') as restore, \
             mock.patch.object(manager, '_stop_after_failed_recovery', side_effect=self.module.RuntimeHealthError('recovery required')):
            with self.assertRaises(self.module.RuntimeHealthError):
                manager._recover_failed_upgrade(manifest, '0.2.0', 'backup', failure)
            stop.assert_called_once()
            restore.assert_not_called()

    def test_restore_only_does_not_invoke_old_helper_after_relay_was_stopped(self):
        manager = self.manager()
        manifest = mock.Mock(version='0.3.0', rollback='restore-only')
        failure = self.module.RuntimeHealthError('upgrade failed')
        with mock.patch.object(manager, '_compose', return_value=['docker', 'compose']), \
             mock.patch.object(manager, '_personnel_guard_enabled', return_value=False), \
             mock.patch.object(manager, '_stop_new_writers_before_legacy_restore', return_value=True), \
             mock.patch.object(manager, '_run_helper') as restore, \
             mock.patch.object(manager, '_stop_after_failed_recovery', side_effect=self.module.RuntimeHealthError('recovery required')):
            with self.assertRaises(self.module.RuntimeHealthError):
                manager._recover_failed_upgrade(manifest, '0.2.0', 'backup', failure)
            restore.assert_not_called()

    def test_recovery_required_blocks_mutating_commands_after_process_restart(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        manager._write_state(status="recovery_required", recoveryBackup="preserved-backup")
        state_path = self.target / ".surveyctl" / "state.json"
        original_state = state_path.read_bytes()
        original_current = (self.target / "current").read_bytes()
        commands = (
            ["upgrade", "--manifest", str(self.root / "nonexistent.json")],
            ["upgrade", "--version", "0.3.0", "--manifest-sha256", "a" * 64],
            ["rollback"],
            ["backup", "--output", "unsafe-labeled-backup"],
            ["restore", "preserved-backup"],
            ["uninstall"],
            ["uninstall", "--purge-data", "--confirm", f"PURGE {self.target}"],
            ["setup-probe"],
            ["doctor"],
            ["install", "--manifest", str(manifest), "--public-host", "survey.example.com", "--admin-user", "operations", "--admin-email", "operations@example.com"],
            ["install", "--version", "0.3.0", "--manifest-sha256", "a" * 64, "--public-host", "survey.example.com", "--admin-user", "operations", "--admin-email", "operations@example.com"],
            ["install", "--offline", str(self.root / "nonexistent.tar"), "--public-host", "survey.example.com", "--admin-user", "operations", "--admin-email", "operations@example.com"],
        )
        for command in commands:
            with self.subTest(command=command[0]):
                result = subprocess.run([sys.executable, str(MODULE_PATH), "--target", str(self.target), *command],
                                        capture_output=True, text=True, timeout=10,
                                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                self.assertNotEqual(0, result.returncode)
                self.assertIn('recovery_required', result.stderr)
                self.assertIn('post-backup writes may exist', result.stderr)
                self.assertEqual(original_state, state_path.read_bytes())
                self.assertEqual(original_current, (self.target / "current").read_bytes())
        with self.assertRaises(self.module.InputError):
            manager._write_state(status="uninstalled")
        self.assertEqual(original_state, state_path.read_bytes())

    def test_interruption_during_upgrade_leaves_persistent_latch(self):
        initial, _ = self.write_bundle_manifest()
        target_manifest, _ = self.write_bundle_manifest('0.3.0')
        script = r'''import importlib.util, os, signal, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('survey_test_fixture', sys.argv[1])
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
module = tests.load_module()
manager = module.SurveyManager(target=Path(sys.argv[2]), runner=tests.FakeRunner(),
    host_probe=tests.FakeHostProbe(), source_dir=Path(sys.argv[3]))
def interrupted(*args, **kwargs):
    if sys.argv[6] in ('sigterm', 'sigkill'):
        os.kill(os.getpid(), signal.SIGTERM if sys.argv[6] == 'sigterm' else signal.SIGKILL)
    raise KeyboardInterrupt()
if sys.argv[5] == 'stage':
    manager._stage_release = interrupted
else:
    manager._wait_healthy = interrupted
manager.upgrade(Path(sys.argv[4]))'''
        for phase, interruption in ((p, i) for p in ('stage', 'after_start') for i in ('sigterm', 'sigkill', 'interrupt')):
            with self.subTest(phase=phase, interruption=interruption):
                target = self.root / ('survey-' + phase + '-' + interruption)
                manager = self.module.SurveyManager(target=target, runner=FakeRunner(),
                    host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR)
                manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
                result = subprocess.run([sys.executable, '-c', script, str(Path(__file__).resolve()),
                                         str(target), str(PRODUCTION_DIR), str(target_manifest), phase, interruption],
                                        capture_output=True, text=True, timeout=15,
                                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                if interruption in ('sigterm', 'sigkill'):
                    self.assertEqual(-getattr(signal, 'SIGTERM' if interruption == 'sigterm' else 'SIGKILL'), result.returncode, result.stderr)
                else:
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn('KeyboardInterrupt', result.stderr)
                state = json.loads((target / '.surveyctl' / 'state.json').read_text())
                self.assertEqual('upgrading', state['status'])
                if phase == 'stage':
                    self.assertFalse((target / 'releases' / '0.3.0').exists())
                    self.assertIsNone(state.get('recoveryBackup'))
                else:
                    self.assertTrue((target / 'releases' / '0.3.0').exists())
                    self.assertTrue((target / 'backups' / state['recoveryBackup']).is_dir())
                commands = (["uninstall"], ["uninstall", "--purge-data", "--confirm", f"PURGE {target}"],
                            ["rollback"], ["restore", "preserved-backup"],
                            ["upgrade", "--manifest", str(target_manifest)],
                            ["upgrade", "--version", "0.3.0", "--manifest-sha256", "a" * 64],
                            ["backup", "--output", "unsafe-labeled-backup"], ["doctor"], ["setup-probe"],
                            ["install", "--manifest", str(target_manifest), "--public-host", "survey.example.com", "--admin-user", "operations", "--admin-email", "operations@example.com"],
                            ["install", "--version", "0.3.0", "--manifest-sha256", "a" * 64, "--public-host", "survey.example.com", "--admin-user", "operations", "--admin-email", "operations@example.com"],
                            ["install", "--offline", str(self.root / "nonexistent.tar"), "--public-host", "survey.example.com", "--admin-user", "operations", "--admin-email", "operations@example.com"])
                for command in commands:
                    blocked = subprocess.run([sys.executable, str(MODULE_PATH), '--target', str(target), *command],
                                             capture_output=True, text=True, timeout=10,
                                             env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                    self.assertNotEqual(0, blocked.returncode)
                    self.assertTrue('unverified outcome' in blocked.stderr or 'upgrading' in blocked.stderr, blocked.stderr)
                self.assertEqual(state, json.loads((target / '.surveyctl' / 'state.json').read_text()))

    def test_intent_directory_fsync_failure_never_starts_staging(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        candidate, _ = self.write_bundle_manifest('0.3.0')
        original_fsync = os.fsync
        def fsync_or_fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('injected directory fsync failure')
            return original_fsync(fd)
        with mock.patch.object(self.module.os, 'fsync', side_effect=fsync_or_fail_directory), \
             mock.patch.object(manager, '_stage_release') as stage:
            with self.assertRaises(OSError):
                manager.upgrade(candidate)
        stage.assert_not_called()
        # The rename may already be visible; it must never be treated as a
        # successful upgrade or authorization to continue without rechecking.
        self.assertEqual('healthy', manager._read_state()['status'])
        self.assertEqual('pending', json.loads((self.target / '.surveyctl/operation.intent').read_text())['state'])

    def test_remote_upgrade_records_intent_before_staging_managed_downloads(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        candidate, _ = self.write_bundle_manifest('0.3.0')
        payload = candidate.read_bytes()
        manager.release_client = FakeReleaseClient(payload=payload)
        def interrupted_stage(*args, **kwargs):
            self.assertEqual('upgrading', manager._read_state()['status'])
            self.assertFalse((self.target / '.surveyctl' / 'downloads').exists())
            raise self.module.RuntimeHealthError('fixture interrupted')
        with mock.patch.object(manager, '_stage_release', side_effect=interrupted_stage):
            with self.assertRaises(self.module.RuntimeHealthError):
                manager.upgrade_version('0.3.0', hashlib.sha256(payload).hexdigest())
        self.assertEqual('upgrading', manager._read_state()['status'])

    def test_unsafe_operation_lock_is_rejected_before_uninstall(self):
        initial, _ = self.write_bundle_manifest()
        for kind in ('symlink', 'fifo', 'hardlink', 'permissions'):
            with self.subTest(kind=kind):
                target = self.root / ('unsafe-lock-' + kind)
                runner = FakeRunner()
                manager = self.module.SurveyManager(target=target, runner=runner,
                    host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR)
                manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
                lock = target / '.surveyctl' / 'operation.lock'
                state = (target / '.surveyctl' / 'state.json').read_bytes()
                if kind == 'permissions':
                    lock.chmod(0o666)
                else:
                    lock.unlink()
                    if kind == 'symlink':
                        lock.symlink_to(self.root / 'outside')
                    elif kind == 'fifo':
                        os.mkfifo(lock, 0o600)
                    else:
                        outside = self.root / 'lock-hardlink-source'
                        outside.write_bytes(b'')
                        outside.chmod(0o600)
                        os.link(outside, lock)
                runner.commands.clear()
                with self.assertRaises(self.module.IntegrityError):
                    manager.uninstall(False, None)
                self.assertEqual([], runner.commands)
                self.assertEqual(state, (target / '.surveyctl' / 'state.json').read_bytes())

    def test_real_process_lock_prevents_stale_state_mutations_and_releases_on_kill(self):
        initial, _ = self.write_bundle_manifest()
        candidate, _ = self.write_bundle_manifest('0.3.0')
        script = r'''import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('survey_test_fixture', sys.argv[1])
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
module = tests.load_module()
manager = module.SurveyManager(target=Path(sys.argv[2]), runner=tests.FakeRunner(),
    host_probe=tests.FakeHostProbe(), source_dir=Path(sys.argv[3]))
ready = Path(sys.argv[5])
def wait_here():
    ready.write_text('ready')
    while True: time.sleep(1)
if sys.argv[6] == 'upgrade':
    manager._stage_release = lambda *args, **kwargs: wait_here()
    manager.upgrade(Path(sys.argv[4]))
else:
    original = manager._read_state
    def read_then_wait(*args, **kwargs):
        value = original(*args, **kwargs)
        wait_here()
        return value
    manager._read_state = read_then_wait
    if sys.argv[6] == 'uninstall': manager.uninstall(False, None)
    else: manager.rollback()
'''
        for phase in ('uninstall', 'rollback', 'upgrade'):
            with self.subTest(holder=phase):
                target = self.root / ('lock-' + phase)
                manager = self.module.SurveyManager(target=target, runner=FakeRunner(),
                    host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR)
                manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
                ready = self.root / ('ready-' + phase)
                process = subprocess.Popen([sys.executable, '-c', script, str(Path(__file__).resolve()),
                    str(target), str(PRODUCTION_DIR), str(candidate), str(ready), phase],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                try:
                    deadline = time.monotonic() + 8
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(ready.exists(), 'holder did not reach the locked phase')
                    before = (target / '.surveyctl' / 'state.json').read_bytes()
                    commands = (["upgrade", "--manifest", str(candidate)], ["rollback"], ["uninstall"])
                    for command in commands:
                        blocked = subprocess.run([sys.executable, str(MODULE_PATH), '--target', str(target), *command],
                            capture_output=True, text=True, timeout=5,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                        self.assertNotEqual(0, blocked.returncode)
                        if phase == 'upgrade' and command[0] == 'uninstall':
                            self.assertIn('upgrading', blocked.stderr)
                        else:
                            self.assertIn('operation is in progress', blocked.stderr)
                        self.assertEqual(before, (target / '.surveyctl' / 'state.json').read_bytes())
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
                self.assertEqual(-signal.SIGKILL, process.returncode)
                # OS lock is released on process death. The durable upgrade
                # latch, if set, must still block a new mutating command.
                if phase == 'upgrade':
                    with self.assertRaisesRegex(self.module.InputError, 'unverified outcome'):
                        with manager._mutation_lock():
                            pass
                else:
                    with manager._mutation_lock():
                        pass

    def test_killed_controller_leaves_intent_while_child_survives(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        ready = self.root / 'intent-child-ready'
        child_pid = self.root / 'intent-child-pid'
        script = r'''import importlib.util, pathlib, subprocess, sys, time
spec = importlib.util.spec_from_file_location('survey_test_fixture', sys.argv[1])
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
module = tests.load_module(); manager = module.SurveyManager(sys.argv[2])
with manager._mutation_lock(operation='uninstall'):
    manager._begin_operation()
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pathlib.Path(sys.argv[3]).write_text(str(child.pid))
    pathlib.Path(sys.argv[4]).write_text('ready')
    while True: time.sleep(1)
'''
        holder = subprocess.Popen([sys.executable, '-c', script, str(Path(__file__).resolve()), str(self.target), str(child_pid), str(ready)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        pid = None
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), 'controller did not reach the started phase')
            pid = int(child_pid.read_text())
            holder.kill(); holder.communicate(timeout=5)
            os.kill(pid, 0)
            for _ in range(2):
                result = subprocess.run([sys.executable, str(MODULE_PATH), '--target', str(self.target), 'uninstall'], capture_output=True, text=True, timeout=5)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('unverified outcome', result.stderr)
                if pid is not None:
                    os.kill(pid, signal.SIGTERM); pid = None
            self.assertEqual('pending', json.loads((self.target / '.surveyctl/operation.intent').read_text())['state'])
        finally:
            if holder.poll() is None:
                holder.kill(); holder.communicate(timeout=5)
            if pid is not None:
                try: os.kill(pid, signal.SIGTERM)
                except ProcessLookupError: pass

    def test_backup_verification_rejects_corruption_before_helper(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        backup = self.target / 'backups' / 'corrupt'
        write_fake_backup(backup)
        (backup / 'payload.bin').write_bytes(b'tampered')
        with mock.patch.object(manager, '_run_helper') as helper:
            with manager._mutation_lock(operation='verify-test'):
                with self.assertRaises(self.module.IntegrityError):
                    manager._verify_backup(backup)
            helper.assert_not_called()

    def test_backup_verify_helper_interruption_keeps_pending(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        backup = self.target / 'backups' / 'valid'
        write_fake_backup(backup)
        def interrupted(*args, **kwargs):
            self.assertEqual('pending', json.loads((self.target / '.surveyctl/operation.intent').read_text())['state'])
            raise KeyboardInterrupt()
        with mock.patch.object(manager, '_run_helper', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                with manager._mutation_lock(operation='verify-test'):
                    manager._verify_backup(backup)
        with self.assertRaisesRegex(self.module.InputError, 'unverified outcome'):
            manager.uninstall(False, None)

    def test_killed_backup_verification_controller_blocks_while_child_survives(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        write_fake_backup(self.target / 'backups' / 'valid')
        ready, child_file = self.root / 'verify-ready', self.root / 'verify-child'
        script = r'''import importlib.util, pathlib, subprocess, sys, time
spec = importlib.util.spec_from_file_location('survey_test_fixture', sys.argv[1])
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
module = tests.load_module()
manager = module.SurveyManager(target=pathlib.Path(sys.argv[2]), runner=tests.FakeRunner(), host_probe=tests.FakeHostProbe(), source_dir=pathlib.Path(sys.argv[3]))
def helper(command, *args, **kwargs):
    assert 'verify' in command
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pathlib.Path(sys.argv[4]).write_text(str(child.pid)); pathlib.Path(sys.argv[5]).write_text('ready')
    while True: time.sleep(1)
manager._run_helper = helper
with manager._mutation_lock(operation='verify-test'):
    manager._verify_backup(manager.target / 'backups' / 'valid')
'''
        holder = subprocess.Popen([sys.executable, '-c', script, str(Path(__file__).resolve()), str(self.target), str(PRODUCTION_DIR), str(child_file), str(ready)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid = None
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), 'verification helper was not reached')
            pid = int(child_file.read_text())
            holder.kill(); holder.wait(timeout=5); os.kill(pid, 0)
            blocked = subprocess.run([sys.executable, str(MODULE_PATH), '--target', str(self.target), 'uninstall'], capture_output=True, text=True, timeout=5)
            self.assertNotEqual(0, blocked.returncode)
            self.assertIn('unverified outcome', blocked.stderr)
            self.assertEqual('pending', json.loads((self.target / '.surveyctl/operation.intent').read_text())['state'])
        finally:
            if holder.poll() is None: holder.kill(); holder.wait(timeout=5)
            if pid is not None:
                try: os.kill(pid, signal.SIGTERM)
                except ProcessLookupError: pass

    def test_restore_only_upgrade_failure_never_starts_old_images_without_restore(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        next_manifest, _ = self.write_bundle_manifest("0.3.0", rollback="restore-only")
        runner = FakeRunner(fail_when=lambda command: ("up" in command and "0.3.0" in " ".join(command))
                            or ("restore.py" in " ".join(command) and "restore" in command))
        with self.assertRaises(self.module.RuntimeHealthError) as error:
            self.manager(runner=runner).upgrade(next_manifest)
        self.assertIn("post-backup writes may exist", str(error.exception))
        self.assertIn("do not overwrite data", str(error.exception))
        self.assertFalse(any("restore.py" in " ".join(command) and "restore" in command for command in runner.commands))
        old_starts = [command for command in runner.commands if "up" in command and "0.2.0" in " ".join(command)]
        self.assertEqual([], old_starts)
        self.assertTrue(any("stop" in command for command in runner.commands))
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("recovery_required", state["status"])
        self.assertTrue((self.target / "backups" / state["recoveryBackup"]).is_dir())

    def test_health_requires_nonempty_healthy_status_for_exact_service_set(self):
        manifest, _ = self.write_bundle_manifest()
        with self.assertRaises(self.module.RuntimeHealthError):
            self.manager(runner=FakeRunner(empty_health=True)).install(
                manifest, "survey.example.com", "operations", "operations@example.com"
            )

    def test_health_uses_target_release_contract_for_legacy_and_relay_stacks(self):
        old = self.manager(runner=FakeRunner(configured_relay=False, running_relay=False))
        old._assert_healthy(["docker", "compose"], "legacy rollback")
        new = self.manager(runner=FakeRunner(configured_relay=True, running_relay=True))
        new._assert_healthy(["docker", "compose"], "new release")
        missing = self.manager(runner=FakeRunner(configured_relay=True, running_relay=False))
        with self.assertRaises(self.module.RuntimeHealthError):
            missing._assert_healthy(["docker", "compose"], "new release missing relay")
        unexpected = self.manager(runner=FakeRunner(configured_relay=False, running_relay=True))
        with self.assertRaises(self.module.RuntimeHealthError):
            unexpected._assert_healthy(["docker", "compose"], "legacy release extra relay")

        class UnknownContractRunner(FakeRunner):
            def run(runner, command, **kwargs):
                if "config" in command and "--services" in command:
                    return subprocess.CompletedProcess(command, 0, stdout="edge\nengine\n", stderr="")
                return super().run(command, **kwargs)

        with self.assertRaises(self.module.RuntimeHealthError):
            self.manager(runner=UnknownContractRunner())._assert_healthy(["docker", "compose"], "unknown target")

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

    def test_native_ci_disk_profile_requires_actions_flag_switch_and_manifest_declaration(self):
        profile = {
            "profile": "github-actions-native-v1",
            "minimumFreeBytes": 8 * 1024**3,
            "tlsMode": "local-ca",
            "diskEvidence": "separately-tested",
        }
        manifest, _ = write_test_release(self.root, version="0.3.0-rc.1", nativeAcceptance=profile)
        release = self.module.load_manifest(manifest)
        probe = FakeHostProbe(
            free_bytes=9 * 1024**3,
            dns_addresses=["127.0.0.1"],
            local_addresses=["127.0.0.1"],
            dns_public=False,
        )
        combinations = (
            {},
            {"GITHUB_ACTIONS": "true"},
            {"SURVEY_NATIVE_ACCEPTANCE": "1"},
            {"GITHUB_ACTIONS": "false", "SURVEY_NATIVE_ACCEPTANCE": "1"},
            {"SURVEY_NATIVE_ACCEPTANCE_MIN_DISK_BYTES": "1"},
        )
        for environment in combinations:
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(self.module.EnvironmentError):
                    self.manager(probe=probe, expected_public_addresses=["127.0.0.1"]).preflight(release, "survey-native.test")

        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "SURVEY_NATIVE_ACCEPTANCE": "1"}, clear=True):
            values = self.manager(probe=probe, expected_public_addresses=["127.0.0.1"]).preflight(release, "survey-native.test")
        self.assertEqual(8 * 1024**3, values["required_disk_bytes"])
        self.assertEqual(20 * 1024**3, values["production_required_disk_bytes"])
        self.assertTrue(values["native_acceptance_profile"])

        ordinary_root = self.root / "ordinary"
        ordinary_root.mkdir()
        ordinary_manifest, _ = write_test_release(ordinary_root, version="0.3.0-rc.1")
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "SURVEY_NATIVE_ACCEPTANCE": "1"}, clear=True):
            with self.assertRaises(self.module.EnvironmentError):
                self.manager(probe=probe, expected_public_addresses=["127.0.0.1"]).preflight(
                    self.module.load_manifest(ordinary_manifest), "survey-native.test"
                )

    def test_native_ci_local_ca_is_copied_and_used_without_weakening_production_tls(self):
        profile = {
            "profile": "github-actions-native-v1",
            "minimumFreeBytes": 8 * 1024**3,
            "tlsMode": "local-ca",
            "diskEvidence": "separately-tested",
        }
        manifest, _ = write_test_release(self.root, version="0.3.0-rc.1", nativeAcceptance=profile)
        probe = FakeHostProbe(
            free_bytes=9 * 1024**3,
            dns_addresses=["127.0.0.1"],
            local_addresses=["127.0.0.1"],
            dns_public=False,
        )
        runner = FakeRunner()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "SURVEY_NATIVE_ACCEPTANCE": "1"}, clear=True):
            self.manager(runner=runner, probe=probe, expected_public_addresses=["127.0.0.1"]).install(
                manifest, "survey-native.test", "operations", "operations@example.com"
            )
        ca_file = self.target / ".surveyctl" / "native-acceptance-ca.crt"
        self.assertEqual(ca_file, probe.ca_file)
        self.assertTrue(ca_file.is_file())
        env = (self.target / "releases" / "0.3.0-rc.1" / ".env").read_text(encoding="utf-8")
        self.assertIn("CADDY_TLS_DIRECTIVE=tls internal", env)

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

    def test_staged_gateway_rpc_identity_matches_initialized_engine(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        release = manager._stage_release(self.module.load_manifest(manifest), 'arm64',
                                         'survey.example.com', 'chosen-operator', 'operations@example.com')
        config = json.loads((release / 'engines.json').read_text())
        with self.subTest(binding='username'):
            self.assertEqual('chosen-operator', config['production-engine-01']['user'])
        private = self.target / 'shared/secrets'
        with self.subTest(binding='password'):
            self.assertTrue((private / 'engine_admin_password').read_bytes() ==
                            (private / 'pubgw_engine_admin_password').read_bytes(), 'RPC credential must match engine identity')

    def test_existing_rpc_mismatch_refuses_upgrade_without_shared_changes(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        (self.target / 'shared/secrets/pubgw_engine_admin_password').write_text('legacy-different-test-value\n')
        def snapshot():
            paths = [*self.target.joinpath('shared').rglob('*'), manager.state_path, manager.current_path]
            return {str(p.relative_to(self.target)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
        before = snapshot()
        next_manifest, _ = self.write_bundle_manifest('0.3.0')
        with self.assertRaisesRegex(self.module.IntegrityError, 'controlled.*RPC'):
            manager.upgrade(next_manifest)
        after = snapshot()
        state_key = str(manager.state_path.relative_to(self.target))
        self.assertNotEqual(before.pop(state_key), after.pop(state_key))
        self.assertEqual(before, after, 'existing credentials and runtime must remain unchanged')
        self.assertEqual('upgrading', manager._read_state()['status'])

    def test_backup_failure_preserves_matching_rpc_credentials_and_runtime(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        paths = [p for p in (self.target / 'shared').rglob('*') if p.is_file()]
        paths += [manager.state_path, manager.current_path]
        before = {p: p.read_bytes() for p in paths}
        next_manifest, _ = self.write_bundle_manifest('0.3.0')
        with mock.patch.object(manager, '_backup_for_upgrade_or_manual', side_effect=self.module.RuntimeHealthError('fixture backup failure')):
            with self.assertRaises(self.module.RuntimeHealthError):
                manager.upgrade(next_manifest)
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items() if p != manager.state_path), 'backup failure must preserve existing runtime')
        self.assertEqual('upgrading', manager._read_state()['status'])

    def test_legacy_gateway_username_requires_controlled_reconciliation(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        current = self.target / 'releases/0.2.0'
        engines = json.loads((current / 'engines.json').read_text())
        engines['production-engine-01']['user'] = 'legacy-rpc-user'
        (current / 'engines.json').write_text(json.dumps(engines))
        integrity = json.loads((current / 'deployment-integrity.json').read_text())
        integrity['generatedFiles']['engines.json'] = hashlib.sha256((current / 'engines.json').read_bytes()).hexdigest()
        (current / 'deployment-integrity.json').write_text(json.dumps(integrity))
        before = manager.state_path.read_bytes(), manager.current_path.read_bytes(), (current / 'engines.json').read_bytes()
        next_manifest, _ = self.write_bundle_manifest('0.3.0')
        with self.assertRaisesRegex(self.module.IntegrityError, 'controlled RPC identity'):
            manager.upgrade(next_manifest)
        self.assertNotEqual(before[0], manager.state_path.read_bytes())
        self.assertEqual(before[1:], (manager.current_path.read_bytes(), (current / 'engines.json').read_bytes()))
        self.assertEqual('upgrading', manager._read_state()['status'])

    def test_staging_failure_preserves_rpc_secrets_and_runtime(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        paths = [p for p in (self.target / 'shared').rglob('*') if p.is_file()]
        paths += [manager.state_path, manager.current_path]
        before = {p: p.read_bytes() for p in paths}
        next_manifest, _ = self.write_bundle_manifest('0.3.0')
        with mock.patch.object(manager, '_sync_runtime_secrets', side_effect=self.module.RuntimeHealthError('fixture stage failure')):
            with self.assertRaises(self.module.RuntimeHealthError):
                manager.upgrade(next_manifest)
        self.assertTrue(all(p.read_bytes() == content for p, content in before.items() if p != manager.state_path), 'staging failure must preserve existing runtime')
        self.assertEqual('upgrading', manager._read_state()['status'])

    def test_failed_install_blocks_retries_and_uninstall_when_outcome_uncertain(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager(probe=FakeHostProbe(tls_valid=False))
        with self.assertRaises(self.module.RuntimeHealthError):
            manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        self.assertFalse(manager.current_path.exists())
        state_before = manager.state_path.read_bytes()
        intent = self.target / '.surveyctl/operation.intent'
        self.assertEqual('pending', json.loads(intent.read_text())['state'])
        next_manifest, _ = self.write_bundle_manifest('0.3.0')
        with self.assertRaisesRegex(self.module.InputError, 'do not remove it'):
            manager.uninstall(False, None)
        with self.assertRaisesRegex(self.module.InputError, 'controlled recovery is not yet available'):
            self.manager().install(next_manifest, 'survey.example.com', 'operations', 'operations@example.com')
        self.assertEqual(state_before, manager.state_path.read_bytes())
        self.assertFalse(manager.current_path.exists())

    def test_ordinary_uninstall_with_removed_current_is_not_failed_install_lineage(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        manager.uninstall(False, None)
        manager.current_path.unlink()
        next_manifest, _ = self.write_bundle_manifest('0.3.0')
        with self.assertRaises(self.module.InputError):
            manager.install(next_manifest, 'survey.example.com', 'operations', 'operations@example.com')

    def test_successful_purge_allows_fresh_new_version_install(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        manager.uninstall(True, f'PURGE {self.target.resolve()}')
        self.assertFalse((self.target / 'shared').exists())
        next_manifest, _ = self.write_bundle_manifest('0.3.0')
        manager.install(next_manifest, 'survey.example.com', 'new-operations', 'operations@example.com')
        self.assertEqual('healthy', json.loads(manager.state_path.read_text())['status'])
        self.assertEqual('new-operations', json.loads((self.target / 'releases/0.3.0/engines.json').read_text())['production-engine-01']['user'])

    def test_uninstall_without_purge_does_not_allow_missing_credentials_to_be_recreated(self):
        initial, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(initial, 'survey.example.com', 'operations', 'operations@example.com')
        manager.uninstall(False, None)
        private = self.target / 'shared/secrets'
        (private / 'engine_admin_password').unlink()
        (private / 'pubgw_engine_admin_password').unlink()
        next_manifest, _ = self.write_bundle_manifest('0.3.0')
        with self.assertRaisesRegex(self.module.IntegrityError, 'controlled RPC credential'):
            manager.install(next_manifest, 'survey.example.com', 'operations', 'operations@example.com')
        self.assertFalse((private / 'engine_admin_password').exists())
        self.assertFalse((private / 'pubgw_engine_admin_password').exists())

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

    def test_failed_doctor_preserves_redacted_report_and_remains_failure(self):
        manifest, _ = self.write_bundle_manifest()
        runner = FakeRunner()
        manager = self.manager(runner=runner)
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        secret = (self.target / "shared" / "secrets" / "platform_jwt_hmac_secret").read_text().strip()
        report = json.dumps({"status": "error", "checks": [{"id": "tls", "status": "error", "summary": "token=" + secret}]})
        runner.run = mock.Mock(side_effect=subprocess.CalledProcessError(1, ["doctor"], output=report, stderr=""))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaisesRegex(self.module.RuntimeHealthError, "doctor checks failed"):
            manager.doctor()
        self.assertEqual("error", json.loads(output.getvalue())["checks"][0]["status"])
        self.assertNotIn(secret, output.getvalue())

    def test_failed_doctor_runtime_error_is_reported_safely(self):
        manifest, _ = self.write_bundle_manifest()
        runner = FakeRunner()
        manager = self.manager(runner=runner)
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        report = json.dumps({"schemaVersion": 1, "status": "blocked", "error": "docker inspect failed token=unsafe-value"})
        runner.run = mock.Mock(side_effect=subprocess.CalledProcessError(4, ["doctor"], output=report, stderr=""))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(self.module.RuntimeHealthError):
            manager.doctor()
        check = json.loads(output.getvalue())["checks"][0]
        self.assertEqual("doctor-runtime", check["id"])
        self.assertIn("docker inspect failed", check["summary"])
        self.assertNotIn("unsafe-value", output.getvalue())

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

    def test_org_secret_directory_is_empty_private_and_survives_sync(self):
        manager = self.manager()
        manager.install(self.base_manifest, "survey.example.com", "operations", "operations@example.com")
        parent = self.target / "shared/runtime-secrets"
        directory = parent / "org-login"
        self.assertEqual(0o700, stat.S_IMODE(parent.stat().st_mode))
        self.assertEqual(0o755, stat.S_IMODE(directory.stat().st_mode))
        self.assertEqual([], list(directory.iterdir()))
        fixture = directory / "PLATFORM_ORG_SECRET_3F2A0C1E7B4D4E5F9A8B0C1D2E3F4A5B_WECOM"
        fixture.write_text("synthetic-secret\n")
        fixture.chmod(0o444)
        manager._sync_runtime_secrets()
        self.assertEqual("synthetic-secret\n", fixture.read_text())
        env = (self.target / "releases/0.2.0/.env").read_text()
        self.assertIn(f"PLATFORM_ORG_SECRET_DIR_HOST={directory}", env)
        self.assertNotIn("synthetic-secret", env)

    def test_bind_mounts_are_readable_without_exposing_canonical_secrets(self):
        self.install()
        canonical = self.target / "shared/secrets"
        projected = self.target / "shared/runtime-secrets"
        self.assertEqual(0o700, stat.S_IMODE(projected.stat().st_mode))
        self.assertFalse((projected / "backup_encryption_key").exists())
        for name in self.module.SECRET_NAMES:
            self.assertEqual(0o600, stat.S_IMODE((canonical / name).stat().st_mode))
            if name == "backup_encryption_key":
                continue
            path = projected / name
            self.assertEqual((canonical / name).read_bytes(), path.read_bytes())
            self.assertEqual(0o444, stat.S_IMODE(path.stat().st_mode))
        release = self.target / "releases/0.2.0"
        self.assertEqual(0o644, stat.S_IMODE((release / "engines.json").stat().st_mode))
        env = (release / ".env").read_text()
        self.assertIn(f"PLATFORM_DB_OWNER_PASSWORD_FILE={projected / 'platform_db_owner_password'}", env)
        self.assertNotIn(str(canonical), env)

    def test_runtime_projections_refresh_from_canonical_files(self):
        self.install()
        canonical = self.target / "shared/secrets/platform_pubgw_secret"
        canonical.write_text("fixture-rotation\n")
        self.manager()._sync_runtime_secrets()
        projected = self.target / "shared/runtime-secrets/platform_pubgw_secret"
        self.assertEqual(canonical.read_bytes(), projected.read_bytes())
        self.assertEqual(0o444, stat.S_IMODE(projected.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(canonical.stat().st_mode))

    def test_runtime_secret_projection_rejects_symlinks(self):
        self.install()
        projected = self.target / "shared/runtime-secrets/platform_pubgw_secret"
        projected.unlink()
        victim = self.target / "victim"
        victim.write_text("preserve")
        projected.symlink_to(victim)
        with self.assertRaises(self.module.InputError):
            self.manager()._sync_runtime_secrets()
        self.assertEqual("preserve", victim.read_text())

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
        self.assertIn("config --services", joined[3])
        self.assertIn("ps", joined[4])
        self.assertTrue((secret_dir / "engine_admin_password_hash").read_text().startswith("$2a$14$"))
        admin_password = (secret_dir / "engine_admin_password").read_text().strip()
        self.assertTrue(all(admin_password not in value for value in joined))
        self.assertTrue(all(str(self.target) in value for value in state["managedPaths"]))
        master = (secret_dir / "platform_engine_events_secret").read_text().strip().encode()
        expected = hmac.new(master, b"mjy-engine-events/v1/production-engine-01", hashlib.sha256).hexdigest()
        self.assertEqual(expected, (secret_dir / "engine_instance_events_secret").read_text().strip())
        self.assertIn(
            f"ENGINE_INSTANCE_EVENTS_SECRET_FILE={self.target / 'shared/runtime-secrets/engine_instance_events_secret'}",
            (self.target / "releases" / "0.2.0" / ".env").read_text(),
        )

    def test_starting_health_deadline_fails_closed(self):
        manager = self.manager()
        with unittest.mock.patch.object(manager, "_assert_healthy", side_effect=self.module.StartupPending("starting")):
            with self.assertRaisesRegex(self.module.RuntimeHealthError, "deadline exceeded"):
                manager._wait_healthy(["docker", "compose"], "installation", timeout=0)

    def test_startup_does_not_retry_unhealthy_or_invalid_status(self):
        manager = self.manager()
        with unittest.mock.patch.object(manager, "_assert_healthy", side_effect=self.module.RuntimeHealthError("unhealthy")) as check:
            with self.assertRaisesRegex(self.module.RuntimeHealthError, "unhealthy"):
                manager._wait_healthy(["docker", "compose"], "installation")
            check.assert_called_once()

    def test_install_waits_for_edge_health_before_recording_success(self):
        class StartingEdgeRunner(FakeRunner):
            def run(self, command, **kwargs):
                result = super().run(command, **kwargs)
                if "ps" in command and "--format" in command:
                    records = json.loads(result.stdout)
                    if sum("ps" in call for call in self.commands) == 1:
                        next(record for record in records if record["Service"] == "edge")["Health"] = "starting"
                    result.stdout = json.dumps(records)
                return result

        runner = StartingEdgeRunner()
        self.manager(runner=runner).install(
            self.base_manifest, "survey.example.com", "operations", "operations@example.com"
        )
        self.assertGreaterEqual(sum("ps" in call for call in runner.commands), 2)
        self.assertEqual("0.2.0", (self.target / "current").read_text().strip())

    def test_channel_secret_derivation_matches_plugin_contract_vector(self):
        self.assertEqual(
            "204745a72bcb796d18bf2df097af7efea324f61077971e41b50189196036ff31",
            self.module.derive_engine_channel_secret(
                "0123456789abcdef0123456789abcdef-instance",
                "hd-engine-01",
            ),
        )
        with self.assertRaises(ValueError):
            self.module.derive_engine_channel_secret("too-short", "hd-engine-01")

    def test_install_generates_a_separate_derived_gateway_channel_secret(self):
        self.install()
        secret_dir = self.target / "shared" / "secrets"
        instance_secret = (secret_dir / "engine_instance_events_secret").read_bytes().strip()
        channel_path = secret_dir / "pubgw_engine_channel_secret"
        expected = hmac.new(
            instance_secret,
            b"mjy-plugin-channel/v1/production-engine-01",
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(expected, channel_path.read_text(encoding="ascii").strip())
        self.assertNotEqual(instance_secret.decode("ascii"), channel_path.read_text(encoding="ascii").strip())
        self.assertEqual(0o600, stat.S_IMODE(channel_path.stat().st_mode))
        env = (self.target / "releases" / "0.2.0" / ".env").read_text(encoding="utf-8")
        self.assertIn(f"PUBGW_ENGINE_CHANNEL_SECRET_FILE={self.target / 'shared/runtime-secrets/pubgw_engine_channel_secret'}", env)

    def test_upgrade_refreshes_tampered_channel_secret_idempotently(self):
        self.install()
        channel_path = self.target / "shared" / "secrets" / "pubgw_engine_channel_secret"
        expected = channel_path.read_text(encoding="ascii")
        channel_path.write_text("tampered\n", encoding="ascii")

        next_manifest = self.manifest(version="0.3.0", minimumSourceVersion="0.2.0")
        self.manager().upgrade(next_manifest)

        self.assertEqual(expected, channel_path.read_text(encoding="ascii"))
        self.manager()._configure_release(
            self.target / "releases" / "0.3.0",
            self.module.load_manifest(next_manifest),
            "arm64",
            "survey.example.com",
            "operations",
            "operations@example.com",
        )
        self.assertEqual(expected, channel_path.read_text(encoding="ascii"))

    def test_setup_probe_creates_least_privilege_identity_without_logging_secrets(self):
        self.install()
        # Simulate an older installation that predates the separate probe credential.
        (self.target / "shared" / "secrets" / "platform_probe_hmac_secret").unlink()
        (self.target / "shared" / "probe-installation.json").unlink()
        release = self.target / "releases" / "0.2.0"
        env = release / ".env"
        env.write_text("\n".join(line for line in env.read_text().splitlines() if not line.startswith("PLATFORM_ADMIN_ENFORCE_PERSONNEL=")) + "\n")
        integrity = release / "deployment-integrity.json"
        metadata = json.loads(integrity.read_text())
        metadata["generatedFiles"][".env"] = hashlib.sha256(env.read_bytes()).hexdigest()
        integrity.write_text(json.dumps(metadata))
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
        tenant_id = "11111111-1111-4111-8111-111111111111"
        self.assertEqual([
            ("POST", "/v1/platform/tenants"),
            ("POST", "/v1/platform/plans"),
            ("POST", f"/v1/platform/tenants/{tenant_id}/onboarding"),
            ("POST", f"/v1/platform/tenants/{tenant_id}/status"),
            ("POST", f"/v1/platform/tenants/{tenant_id}/engine-instances"),
            ("POST", "/v1/platform/engine-instances/production-engine-01/event-secret"),
        ], [(method, path) for method, path, _, _ in calls])
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
        self.assertEqual({"member.seats": 1, "response.valid_completed": 100}, plan["quotas"])
        secrets_on_disk = [path.read_text().strip() for path in (self.target / "shared" / "secrets").iterdir()]
        self.assertTrue(all(secret not in "\n".join(" ".join(map(str, call[:3])) for call in calls) for secret in secrets_on_disk))

    def test_install_stages_distinct_machine_identity_and_personnel_guard_configuration(self):
        self.install()
        root = self.target / "shared"
        probe = (root / "secrets" / "platform_probe_hmac_secret").read_bytes().strip()
        personnel = (root / "secrets" / "platform_jwt_hmac_secret").read_bytes().strip()
        self.assertGreaterEqual(len(probe), 32)
        self.assertNotEqual(probe, personnel)
        config = json.loads((root / "probe-installation.json").read_text())
        self.assertEqual(set(config), {"installationId"})
        self.assertEqual(str(__import__('uuid').UUID(config['installationId'])), config['installationId'])
        env = (self.target / "releases" / "0.2.0" / ".env").read_text()
        self.assertIn("PLATFORM_PROBE_INSTALLATION_ID=" + config["installationId"], env)
        self.assertIn("PLATFORM_PROBE_HMAC_SECRET_FILE=", env)
        self.assertNotIn(probe.decode(), env)

    def test_upgrade_and_rollback_preserve_legacy_probe_without_auto_adoption(self):
        self.install()
        root = self.target / "shared"
        legacy = {"tenantId": "11111111-1111-4111-8111-111111111111", "actorId": "production-probe-owner"}
        product = root / "product-probe.json"
        product.write_text(json.dumps(legacy) + "\n")
        before = product.read_bytes()
        (root / "secrets" / "platform_probe_hmac_secret").unlink()
        (root / "probe-installation.json").unlink()
        self.manager().upgrade(self.manifest(version="0.3.0", minimumSourceVersion="0.2.0"))
        self.assertEqual(product.read_bytes(), before)
        with self.assertRaisesRegex(self.module.RuntimeHealthError, "legacy probe retained; run doctor"):
            self.manager().setup_probe(api_call=lambda *_args: self.fail("legacy probe must not be adopted"))
        self.assertFalse((root / "probe-run.json").exists())
        self.manager().rollback("0.2.0")
        self.assertEqual(product.read_bytes(), before)

    def test_setup_probe_uses_independent_machine_identity_and_retries_the_same_run(self):
        self.install()
        probe_key = self.target / "shared" / "secrets" / "platform_probe_hmac_secret"
        probe_key.write_text("isolated-probe-key-for-tests-only-000000000000")
        probe_key.chmod(0o600)
        installation = "10000000-0000-4000-8000-000000000001"
        (self.target / "shared" / "probe-installation.json").write_text(json.dumps({"installationId": installation}))
        seen = []

        def api_call(token, method, path, body=None):
            import base64
            self.assertEqual((method, path, body), ("POST", "/v1/setup-probe", None))
            payload = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            signed, signature = token.rsplit(".", 1)
            expected = hmac.new(probe_key.read_bytes().strip(), signed.encode("ascii"), hashlib.sha256).digest()
            self.assertTrue(hmac.compare_digest(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)), expected))
            self.assertEqual(claims["aud"], ["survey-setup-probe"])
            self.assertEqual(claims["iss"], "survey-probe")
            self.assertEqual(claims["scope"], ["probe.setup"])
            self.assertEqual(claims["installation_id"], installation)
            seen.append(claims["run_id"])
            return 200, {"tenantId": "11111111-1111-4111-8111-111111111111", "actorId": "production-probe-owner",
                         "engineSecret": (self.target / "shared" / "secrets" / "engine_instance_events_secret").read_text().strip()}

        self.manager().setup_probe(api_call=api_call)
        self.manager().setup_probe(api_call=api_call)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(0o600, stat.S_IMODE((self.target / "shared" / "probe-run.json").stat().st_mode))

    def test_setup_probe_rejects_partial_or_shared_machine_credentials_without_fallback(self):
        self.install()
        probe_key = self.target / "shared" / "secrets" / "platform_probe_hmac_secret"
        probe_key.write_text((self.target / "shared" / "secrets" / "platform_jwt_hmac_secret").read_text())
        probe_key.chmod(0o600)
        (self.target / "shared" / "probe-installation.json").unlink()
        calls = []
        with self.assertRaises(self.module.IntegrityError):
            self.manager().setup_probe(api_call=lambda *args: calls.append(args))
        (self.target / "shared" / "probe-installation.json").write_text(json.dumps({"installationId": "10000000-0000-4000-8000-000000000001"}))
        with self.assertRaises(self.module.IntegrityError):
            self.manager().setup_probe(api_call=lambda *args: calls.append(args))
        probe_key.write_text((self.target / "shared" / "secrets" / "platform_engine_events_secret").read_text())
        with self.assertRaises(self.module.IntegrityError):
            self.manager().setup_probe(api_call=lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_personnel_mode_never_falls_back_to_legacy_when_machine_config_is_missing(self):
        self.install()
        (self.target / "shared" / "secrets" / "platform_probe_hmac_secret").unlink()
        (self.target / "shared" / "probe-installation.json").unlink()
        calls = []
        with self.assertRaisesRegex(self.module.IntegrityError, "machine configuration"):
            self.manager().setup_probe(api_call=lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_setup_probe_does_not_commit_state_when_machine_handler_secret_disagrees(self):
        self.install()
        key = self.target / "shared" / "secrets" / "platform_probe_hmac_secret"
        key.write_text("isolated-probe-key-for-tests-only-000000000000")
        key.chmod(0o600)
        (self.target / "shared" / "probe-installation.json").write_text(json.dumps({"installationId": "10000000-0000-4000-8000-000000000001"}))
        def rejected(*_args):
            return 200, {"tenantId": "11111111-1111-4111-8111-111111111111", "actorId": "production-probe-owner", "engineSecret": "incorrect"}
        with self.assertRaises(self.module.IntegrityError):
            self.manager().setup_probe(api_call=rejected)
        self.assertFalse((self.target / "shared" / "product-probe.json").exists())
        self.assertTrue((self.target / "shared" / "probe-run.json").exists())

    def test_concurrent_machine_setup_rejects_second_operation(self):
        import base64
        self.install()
        key = self.target / "shared" / "secrets" / "platform_probe_hmac_secret"
        key.write_text("isolated-probe-key-for-tests-only-000000000000")
        key.chmod(0o600)
        (self.target / "shared" / "probe-installation.json").write_text(json.dumps({"installationId": "10000000-0000-4000-8000-000000000001"}))
        entered, release = threading.Event(), threading.Event()
        seen = []
        def api_call(token, method, path, body=None):
            payload = token.split(".")[1]
            seen.append(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["run_id"])
            entered.set()
            if not release.wait(timeout=10):
                raise RuntimeError('test synchronization failed')
            return 200, {"tenantId": "11111111-1111-4111-8111-111111111111", "actorId": "production-probe-owner",
                         "engineSecret": (self.target / "shared" / "secrets" / "engine_instance_events_secret").read_text().strip()}
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.manager().setup_probe, api_call)
            try:
                self.assertTrue(entered.wait(timeout=5))
                with self.assertRaisesRegex(self.module.InputError, 'operation is in progress'):
                    self.manager().setup_probe(api_call)
            finally:
                release.set()
            first.result(timeout=10)
        persisted = json.loads((self.target / "shared" / "probe-run.json").read_text())["runId"]
        self.assertEqual(seen, [persisted])

    def test_helper_exit_codes_are_preserved_by_real_cli_without_traceback(self):
        helper_source = ("import pathlib,sys\n"
                         "target=pathlib.Path(sys.argv[sys.argv.index('--target')+1])\n"
                         "sys.exit(0 if 'verify' in sys.argv and (target/'allow-verify').exists() else (5 if 'verify' in sys.argv else 4))\n")
        manifest, _ = write_test_release(self.root, restore_source=helper_source.encode())
        target = self.root / "helper-restore-closed"
        manager = self.module.SurveyManager(target=target, runner=FakeRunner(), host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR)
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        write_fake_backup(target / "backups" / "sample")
        result = subprocess.run([sys.executable, str(MODULE_PATH), "--target", str(target), "restore", "sample"], capture_output=True, text=True)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("controlled restore is disabled", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual("complete", json.loads((target / ".surveyctl/operation.intent").read_text())["state"])
        other_target = self.root / "helper-backup-target"
        other_manager = self.module.SurveyManager(target=other_target, runner=FakeRunner(), host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR)
        other_manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        backup_helper = other_target / "releases" / "0.2.0" / "backup.py"
        backup_helper.write_text("import sys\nsys.exit(5)\n")
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--target", str(other_target), "backup", "--output", "helper-integrity"],
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

    def test_upgrade_from_legacy_and_failed_upgrade_recovers_legacy_topology(self):
        self.runner.configured_relay = self.runner.running_relay = False
        self.install()

        class VersionRunner(FakeRunner):
            def run(runner, command, **kwargs):
                old = any("/releases/0.2.0/" in str(part) for part in command)
                runner.configured_relay = runner.running_relay = not old
                return super().run(command, **kwargs)

        next_manifest = self.manifest(version="0.3.0", minimumSourceVersion="0.2.0")
        failing = VersionRunner(fail_when=lambda command: "up" in command and "0.3.0" in " ".join(command))
        with self.assertRaises(self.module.RuntimeHealthError):
            self.manager(runner=failing).upgrade(next_manifest)
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("upgrade_failed", state["status"])
        self.assertEqual("0.2.0", state["version"])
        self.assertTrue(any("config" in c and "--services" in c and "0.2.0" in " ".join(c)
                            for c in failing.commands))

    def test_successful_upgrade_from_legacy_checks_new_relay_topology(self):
        self.runner.configured_relay = self.runner.running_relay = False
        self.install()

        class VersionRunner(FakeRunner):
            def run(runner, command, **kwargs):
                old = any("/releases/0.2.0/" in str(part) for part in command)
                runner.configured_relay = runner.running_relay = not old
                return super().run(command, **kwargs)

        runner = VersionRunner()
        next_manifest = self.manifest(version="0.3.0", minimumSourceVersion="0.2.0")
        self.manager(runner=runner).upgrade(next_manifest)
        self.assertEqual("0.3.0", (self.target / "current").read_text().strip())
        self.assertTrue(any("config" in c and "--services" in c and "0.3.0" in " ".join(c)
                            for c in runner.commands))

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
        integrity = current.parent / "deployment-integrity.json"
        integrity_data = json.loads(integrity.read_text())
        integrity_data["manifestSha256"] = hashlib.sha256(current.read_bytes()).hexdigest()
        integrity.write_text(json.dumps(integrity_data))
        with self.assertRaises(self.module.InputError):
            manager.rollback("0.2.0")

    def test_personnel_guard_blocks_rollback_to_legacy_before_changing_running_stack(self):
        self.install()
        manager = self.manager()
        legacy = self.manifest(version="0.1.5", securityCapabilities={})
        manager._stage_release(self.module.load_manifest(legacy), "arm64", "survey.example.com", "operations", "operations@example.com")
        before = (self.target / ".surveyctl" / "state.json").read_bytes()
        self.runner.commands.clear()
        with self.assertRaisesRegex(self.module.InputError, "personnel.*security"):
            manager.rollback("0.1.5")
        self.assertEqual([], self.runner.commands)
        self.assertEqual(before, (self.target / ".surveyctl" / "state.json").read_bytes())
        self.assertEqual("0.2.0", (self.target / "current").read_text().strip())

    def test_personnel_guard_allows_capable_rollback(self):
        self.install()
        manager = self.manager()
        capable = self.manifest(version="0.1.5")
        manager._stage_release(self.module.load_manifest(capable), "arm64", "survey.example.com", "operations", "operations@example.com")
        manager.rollback("0.1.5")
        self.assertEqual("0.1.5", (self.target / "current").read_text().strip())

    def test_historical_unenforced_install_can_rollback_without_security_capability(self):
        self.install()
        (self.target / ".surveyctl" / "auth-security-floor").unlink()
        release = self.target / "releases" / "0.2.0"
        env = release / ".env"
        env.write_text(env.read_text().replace("PLATFORM_ADMIN_ENFORCE_PERSONNEL=true", "PLATFORM_ADMIN_ENFORCE_PERSONNEL=false"))
        integrity = release / "deployment-integrity.json"
        data = json.loads(integrity.read_text())
        data["generatedFiles"][".env"] = hashlib.sha256(env.read_bytes()).hexdigest()
        integrity.write_text(json.dumps(data))
        legacy = self.manifest(version="0.1.5", securityCapabilities={})
        manager = self.manager()
        manager._stage_release(self.module.load_manifest(legacy), "arm64", "survey.example.com", "operations", "operations@example.com")
        manager.rollback("0.1.5")
        self.assertEqual("0.1.5", (self.target / "current").read_text().strip())

    def test_personnel_guard_rejects_capability_bound_to_another_image(self):
        self.install()
        manager = self.manager()
        other = "ghcr.io/example/platform@sha256:" + "f" * 64
        candidate = self.manifest(version="0.1.5", securityCapabilities={"platform-admin-session-v1": other})
        manager._stage_release(self.module.load_manifest(candidate), "arm64", "survey.example.com", "operations", "operations@example.com")
        self.runner.commands.clear()
        with self.assertRaisesRegex(self.module.InputError, "personnel.*security"):
            manager.rollback("0.1.5")
        self.assertEqual([], self.runner.commands)

    def test_failed_security_upgrade_does_not_restart_unenforced_predecessor(self):
        self.install()
        (self.target / ".surveyctl" / "auth-security-floor").unlink()
        release = self.target / "releases" / "0.2.0"
        env = release / ".env"
        env.write_text(env.read_text().replace("PLATFORM_ADMIN_ENFORCE_PERSONNEL=true", "PLATFORM_ADMIN_ENFORCE_PERSONNEL=false"))
        old_manifest = release / "release.json"
        data = json.loads(old_manifest.read_text())
        data["securityCapabilities"] = {}
        old_manifest.write_text(json.dumps(data))
        integrity = release / "deployment-integrity.json"
        data = json.loads(integrity.read_text())
        data["generatedFiles"][".env"] = hashlib.sha256(env.read_bytes()).hexdigest()
        data["manifestSha256"] = hashlib.sha256(old_manifest.read_bytes()).hexdigest()
        integrity.write_text(json.dumps(data))
        failing = FakeRunner(fail_when=lambda command: "up" in command and "0.3.0" in " ".join(command))
        candidate = self.manifest(version="0.3.0", minimumSourceVersion="0.2.0")
        with self.assertRaisesRegex(self.module.RuntimeHealthError, "service stop requested"):
            self.manager(runner=failing).upgrade(candidate)
        self.assertEqual("recovery_required", json.loads((self.target / ".surveyctl" / "state.json").read_text())["status"])
        self.assertFalse(any("up" in command and "releases/0.2.0/compose.yml" in " ".join(command) for command in failing.commands))
        self.assertTrue((self.target / ".surveyctl" / "auth-security-floor").is_file())

    def test_manual_restore_is_closed_before_any_backup_or_helper_access(self):
        self.install()
        manager = self.manager()
        before = (self.target / '.surveyctl' / 'state.json').read_bytes()
        intent = (self.target / '.surveyctl' / 'operation.intent').read_bytes()
        with mock.patch.object(manager, '_verify_backup') as verify, \
             mock.patch.object(manager, '_run_helper') as helper:
            with self.assertRaisesRegex(self.module.InputError, 'controlled restore is disabled'):
                manager.restore('synthetic-backup')
            verify.assert_not_called()
            helper.assert_not_called()
        self.assertEqual(before, (self.target / '.surveyctl' / 'state.json').read_bytes())
        self.assertEqual(intent, (self.target / '.surveyctl' / 'operation.intent').read_bytes())

    def test_restore_status_doctor_and_logs_delegate_without_exposing_secrets(self):
        self.install()
        self.runner.commands.clear()
        manager = self.manager()
        manager.backup("sample")
        with self.assertRaisesRegex(self.module.InputError, "controlled restore is disabled"):
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

    def test_restore_stays_closed_for_compatible_and_incompatible_backups(self):
        self.install()
        source = self.manifest(version="0.1.9")
        self.manager()._stage_release(self.module.load_manifest(source), "arm64", "survey.example.com", "operations", "operations@example.com")
        backup = self.target / "backups" / "older-compatible"
        write_fake_backup(backup, "0.1.9", "919")
        for schema in ("919", "918"):
            data = json.loads((backup / "manifest.json").read_text())
            data["databaseSchema"] = schema
            (backup / "manifest.json").write_text(json.dumps(data))
            self.runner.commands.clear()
            with self.assertRaisesRegex(self.module.InputError, "controlled restore is disabled"):
                self.manager().restore("older-compatible")
            self.assertEqual([], self.runner.commands)

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
