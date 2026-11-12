import contextlib
import fcntl
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
if str(PRODUCTION_DIR) not in sys.path:
    sys.path.insert(0, str(PRODUCTION_DIR))
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


def write_test_release(root, version="0.2.0", rollback="compatible", restore_source=None, payload_overrides=None,
                       _fixture_architecture='arm64', _fixture_owned_support=False, **updates):
    if _fixture_architecture not in ('amd64', 'arm64'):
        raise ValueError('unsupported fixture architecture')
    bundle = Path(root) / f"survey-{version}-linux-{_fixture_architecture}.tar.gz"
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
    if _fixture_owned_support:
        # Synthetic bundle, real code hashes; not a signed candidate identity.
        files.update({name: (PRODUCTION_DIR / name).read_bytes() for name in load_module().OWNED_RUNTIME_FILES})
    if payload_overrides:
        files.update(payload_overrides)
    metadata = {
        "schemaVersion": 1,
        "version": version,
        "architecture": _fixture_architecture,
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
    data["supportedHosts"]["architectures"] = [_fixture_architecture]
    data["assets"]["bundles"] = {
        _fixture_architecture: {"name": bundle.name, "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest()}
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
        self.down_noop = False
        self.fixture_running = True

    def run(self, command, **kwargs):
        command = tuple(str(part) for part in command)
        self.commands.append(command)
        self.calls.append((command, kwargs))
        if self.fail_when and self.fail_when(command):
            raise subprocess.CalledProcessError(1, command)
        if 'down' in command and not self.down_noop:
            self.fixture_running = False
        if 'up' in command:
            self.fixture_running = True
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
            if not self.fixture_running:
                return subprocess.CompletedProcess(command, 0, stdout='[]', stderr='')
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


class FixtureRuntime:
    """Filesystem/controller unit stub only; never an ownership/cohort proof.

    Real source/daemon checks are in runtime_controller tests and the explicit
    isolated native entry. This model distinguishes no-op down from removal.
    """
    def __init__(self, manager, version):
        self.manager, self.version = manager, version

    def preflight_cleanup(self, purge):
        self.manager._run(['docker', 'info'], 'fixture discovery unavailable')

    def cleanup(self, purge, host_purge=None):
        compose = self.manager._compose(self.version)
        self.manager._run(compose + ['down'], 'fixture stop failed')
        result = self.manager._run(compose + ['ps', '--all', '--format', 'json'], 'fixture postcheck failed')
        if json.loads(result.stdout):
            raise RuntimeError('fixture resources remain after a successful no-op')
        if host_purge is not None:
            host_purge()

    def install(self, effect):
        return effect()

    def ensure_admin_hash(self):
        self.manager._ensure_admin_password_hash(self.manager._release_manifest(self.version).raw['images']['CADDY_IMAGE'])


def fixture_runtime(manager, version, *, create=False):
    return FixtureRuntime(manager, version)


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
        kwargs.setdefault('_runtime_factory', fixture_runtime)
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

    def test_operation_intent_schema_requires_exact_integer_version(self):
        self.target.mkdir(mode=0o700)
        control = self.target / '.surveyctl'
        control.mkdir(mode=0o700)
        intent = control / 'operation.intent'
        manager = self.manager()
        for value in (True, 1.0, False, '1', None, 2):
            with self.subTest(schema=value):
                payload = json.dumps({'schemaVersion': value, 'state': 'complete', 'operation': 'fixture'})
                intent.write_text(payload)
                intent.chmod(0o600)
                with self.assertRaisesRegex(self.module.IntegrityError, 'intent is invalid'):
                    with manager._mutation_lock(operation='fixture'):
                        self.fail('invalid intent must block before any mutation')
                self.assertEqual(payload, intent.read_text())
        intent.write_text(json.dumps({'schemaVersion': 1, 'state': 'complete', 'operation': 'fixture'}))
        intent.chmod(0o600)
        with manager._mutation_lock(operation='fixture'):
            pass

    def test_operation_intent_duplicate_fields_are_rejected_without_mutation(self):
        self.target.mkdir(mode=0o700)
        control = self.target / '.surveyctl'
        control.mkdir(mode=0o700)
        intent = control / 'operation.intent'
        manager = self.manager()
        for payload in (
            '{"schemaVersion":1,"state":"pending","state":"complete","operation":"fixture"}',
            '{"schemaVersion":2,"schemaVersion":1,"state":"complete","operation":"fixture"}',
            '{"schemaVersion":1,"state":"complete","operation":"unsafe","operation":"fixture"}',
        ):
            with self.subTest(payload=payload):
                intent.write_text(payload)
                intent.chmod(0o600)
                with self.assertRaisesRegex(self.module.IntegrityError, 'intent'):
                    with manager._mutation_lock(operation='fixture'):
                        self.fail('ambiguous intent must block before any mutation')
                self.assertEqual(payload, intent.read_text())

    def test_duplicate_installation_state_fields_block_mutation(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        state_path = self.target / '.surveyctl' / 'state.json'
        original = state_path.read_text()
        for injected in ('"status":"recovery_required",', '"version":"9.9.9",'):
            with self.subTest(injected=injected):
                payload = '{' + injected + original.lstrip()[1:]
                state_path.write_text(payload)
                with self.assertRaisesRegex(self.module.IntegrityError, 'installation state is corrupt'):
                    with manager._mutation_lock(operation='fixture'):
                        self.fail('ambiguous installation state must block before mutation')
                self.assertEqual(payload, state_path.read_text())
        state_path.write_text(original)
        with manager._mutation_lock(operation='fixture'):
            pass

    def test_operation_intent_replacement_during_read_blocks_mutation(self):
        self.target.mkdir(mode=0o700)
        control = self.target / '.surveyctl'
        control.mkdir(mode=0o700)
        intent = control / 'operation.intent'
        intent.write_text(json.dumps({'schemaVersion': 1, 'state': 'complete', 'operation': 'fixture'}))
        intent.chmod(0o600)
        replacement = self.root / 'synthetic-pending.intent'
        replacement.write_text(json.dumps({'schemaVersion': 1, 'state': 'pending', 'operation': 'fixture'}))
        replacement.chmod(0o600)
        original_load = self.module.json.load
        def swap_after_read(handle, *args, **kwargs):
            result = original_load(handle, *args, **kwargs)
            os.replace(replacement, intent)
            return result
        with mock.patch.object(self.module.json, 'load', side_effect=swap_after_read):
            with self.assertRaisesRegex(self.module.IntegrityError, 'intent identity changed'):
                with self.manager()._mutation_lock(operation='fixture'):
                    self.fail('replaced intent must block before mutation')
        self.assertEqual('pending', json.loads(intent.read_text())['state'])

    def test_operation_intent_in_place_change_during_read_blocks_mutation(self):
        self.target.mkdir(mode=0o700)
        control = self.target / '.surveyctl'
        control.mkdir(mode=0o700)
        intent = control / 'operation.intent'
        intent.write_text(json.dumps({'schemaVersion': 1, 'state': 'complete', 'operation': 'fixture'}))
        intent.chmod(0o600)
        original_info = intent.stat()
        original_load = self.module.json.load
        def modify_after_read(handle, *args, **kwargs):
            result = original_load(handle, *args, **kwargs)
            replacement = json.dumps({'schemaVersion': 1, 'state': 'pending', 'operation': 'fixture'})
            intent.write_text(replacement.ljust(original_info.st_size))
            os.utime(intent, ns=(original_info.st_atime_ns, original_info.st_mtime_ns + 1_000_000_000))
            return result
        with mock.patch.object(self.module.json, 'load', side_effect=modify_after_read):
            with self.assertRaisesRegex(self.module.IntegrityError, 'intent.*changed'):
                with self.manager()._mutation_lock(operation='fixture'):
                    self.fail('changed intent must block before mutation')
        self.assertEqual(original_info.st_ino, intent.stat().st_ino)
        self.assertEqual(original_info.st_size, intent.stat().st_size)
        self.assertEqual('pending', json.loads(intent.read_text())['state'])

    def test_installation_state_in_place_change_during_read_is_rejected(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        state_path = self.target / '.surveyctl' / 'state.json'
        state = json.loads(state_path.read_text())
        state['status'] = 'recovery_required'
        state['recoveryBackup'] = 'synthetic-fixture-backup'
        state_path.write_text(state_path.read_text() + ' ' * 128)
        original_info = state_path.stat()
        self.assertLessEqual(len(json.dumps(state)), original_info.st_size)
        original_load = self.module.json.load
        def modify_after_read(handle, *args, **kwargs):
            result = original_load(handle, *args, **kwargs)
            state_path.write_text(json.dumps(state).ljust(original_info.st_size))
            os.utime(state_path, ns=(original_info.st_atime_ns, original_info.st_mtime_ns + 1_000_000_000))
            return result
        with mock.patch.object(self.module.json, 'load', side_effect=modify_after_read):
            with self.assertRaisesRegex(self.module.IntegrityError, 'state.*changed'):
                manager._read_state()
        self.assertEqual(original_info.st_ino, state_path.stat().st_ino)
        self.assertEqual(original_info.st_size, state_path.stat().st_size)
        self.assertEqual('recovery_required', json.loads(state_path.read_text())['status'])

    def test_intent_ctime_detects_in_place_change_with_restored_mtime(self):
        self.target.mkdir(mode=0o700)
        control = self.target / '.surveyctl'
        control.mkdir(mode=0o700)
        intent = control / 'operation.intent'
        intent.write_text(json.dumps({'schemaVersion': 1, 'state': 'complete', 'operation': 'fixture'}))
        intent.chmod(0o600)
        original_info = intent.stat()
        original_load = self.module.json.load
        def modify_after_read(handle, *args, **kwargs):
            result = original_load(handle, *args, **kwargs)
            changed = json.dumps({'schemaVersion': 1, 'state': 'pending', 'operation': 'fixture'})
            intent.write_text(changed.ljust(original_info.st_size))
            os.utime(intent, ns=(original_info.st_atime_ns, original_info.st_mtime_ns))
            observed = intent.stat()
            self.assertEqual(original_info.st_ino, observed.st_ino)
            self.assertEqual(original_info.st_size, observed.st_size)
            self.assertEqual(original_info.st_mtime_ns, observed.st_mtime_ns)
            self.assertEqual(self.module._file_read_snapshot(original_info)[:-1],
                             self.module._file_read_snapshot(observed)[:-1])
            if observed.st_ctime_ns == original_info.st_ctime_ns:
                self.skipTest('filesystem did not expose a distinct ctime for the modification')
            return result
        with mock.patch.object(self.module.json, 'load', side_effect=modify_after_read):
            with self.assertRaisesRegex(self.module.IntegrityError, 'intent.*changed'):
                with self.manager()._mutation_lock(operation='fixture'):
                    self.fail('ctime change must block mutation')
        self.assertEqual('pending', json.loads(intent.read_text())['state'])

    def test_intent_permission_change_during_read_blocks_mutation(self):
        self.target.mkdir(mode=0o700)
        control = self.target / '.surveyctl'
        control.mkdir(mode=0o700)
        intent = control / 'operation.intent'
        intent.write_text(json.dumps({'schemaVersion': 1, 'state': 'complete', 'operation': 'fixture'}))
        intent.chmod(0o600)
        original_load = self.module.json.load
        def change_mode_after_read(handle, *args, **kwargs):
            result = original_load(handle, *args, **kwargs)
            intent.chmod(0o644)
            return result
        # Mask ctime to prove the permission field itself triggers rejection.
        snapshot = self.module._file_read_snapshot
        with mock.patch.object(self.module, '_file_read_snapshot', side_effect=lambda info: snapshot(info)[:-1]):
            with mock.patch.object(self.module.json, 'load', side_effect=change_mode_after_read):
                with self.assertRaisesRegex(self.module.IntegrityError, 'intent.*changed'):
                    with self.manager()._mutation_lock(operation='fixture'):
                        self.fail('permission change must block mutation')
        self.assertEqual(0o644, stat.S_IMODE(intent.stat().st_mode))

    def test_operation_lock_control_directory_swap_blocks_before_yield(self):
        self.target.mkdir(mode=0o700)
        control = self.target / '.surveyctl'
        control.mkdir(mode=0o700)
        intent = control / 'operation.intent'
        intent.write_text(json.dumps({'schemaVersion': 1, 'state': 'complete', 'operation': 'fixture'}))
        intent.chmod(0o600)
        moved = self.root / 'synthetic-moved-control'
        original_load = self.module.json.load
        def swap_parent_after_read(handle, *args, **kwargs):
            result = original_load(handle, *args, **kwargs)
            control.rename(moved)
            control.symlink_to(moved, target_is_directory=True)
            return result
        with mock.patch.object(self.module.json, 'load', side_effect=swap_parent_after_read):
            with self.assertRaisesRegex(self.module.IntegrityError, 'control directory identity changed'):
                with self.manager()._mutation_lock(operation='fixture'):
                    self.fail('changed control directory must block before yielding')
        self.assertTrue(control.is_symlink())
        self.assertTrue((moved / 'operation.intent').is_file())

    def test_operation_lock_replacement_blocks_before_yield(self):
        self.target.mkdir(mode=0o700)
        control = self.target / '.surveyctl'
        control.mkdir(mode=0o700)
        intent = control / 'operation.intent'
        intent.write_text(json.dumps({'schemaVersion': 1, 'state': 'complete', 'operation': 'fixture'}))
        intent.chmod(0o600)
        original_load = self.module.json.load
        def replace_lock_after_read(handle, *args, **kwargs):
            result = original_load(handle, *args, **kwargs)
            lock = control / 'operation.lock'
            lock.rename(control / 'original.lock')
            lock.touch(mode=0o600)
            return result
        with mock.patch.object(self.module.json, 'load', side_effect=replace_lock_after_read):
            with self.assertRaisesRegex(self.module.IntegrityError, 'operation lock identity changed'):
                with self.manager()._mutation_lock(operation='fixture'):
                    self.fail('replaced lock must block before yielding')

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

    def test_state_symlink_swap_after_validation_fails_closed(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        state_path = self.target / '.surveyctl' / 'state.json'
        outside = self.root / 'synthetic-foreign-state.json'
        outside.write_bytes(state_path.read_bytes())
        manager._write_state(status='recovery_required', recoveryBackup='fixture-backup')
        original_check = self.module._regular_file
        def swap_after_check(path, label):
            original_check(path, label)
            if label == 'installation state':
                path.unlink()
                path.symlink_to(outside)
        with mock.patch.object(self.module, '_regular_file', side_effect=swap_after_check):
            with self.assertRaises(self.module.IntegrityError):
                manager._read_state()
        self.assertTrue(state_path.is_symlink())
        self.assertTrue(outside.is_file())

    def test_state_replacement_during_read_fails_closed(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        state_path = self.target / '.surveyctl' / 'state.json'
        replacement = self.root / 'synthetic-replacement.json'
        replacement.write_bytes(state_path.read_bytes())
        original_load = self.module.json.load
        def swap_after_read(handle, *args, **kwargs):
            result = original_load(handle, *args, **kwargs)
            os.replace(replacement, state_path)
            return result
        with mock.patch.object(self.module.json, 'load', side_effect=swap_after_read):
            with self.assertRaisesRegex(self.module.IntegrityError, 'state identity changed'):
                manager._read_state()
        self.assertFalse(replacement.exists())
        self.assertTrue(state_path.is_file())

    def test_state_parent_directory_swap_after_validation_fails_closed(self):
        manifest, _ = self.write_bundle_manifest()
        manager = self.manager()
        manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        control = self.target / '.surveyctl'
        moved = self.root / 'synthetic-relocated-control'
        original_check = self.module._regular_file
        def swap_parent_after_check(path, label):
            original_check(path, label)
            if label == 'installation state':
                control.rename(moved)
                control.symlink_to(moved, target_is_directory=True)
        with mock.patch.object(self.module, '_regular_file', side_effect=swap_parent_after_check):
            with self.assertRaises(self.module.IntegrityError):
                manager._read_state()
        self.assertTrue(control.is_symlink())
        self.assertTrue((moved / 'state.json').is_file())

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
    host_probe=tests.FakeHostProbe(), source_dir=Path(sys.argv[3]), _runtime_factory=tests.fixture_runtime)
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
                    host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR, _runtime_factory=fixture_runtime)
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
                    host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR, _runtime_factory=fixture_runtime)
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
    host_probe=tests.FakeHostProbe(), source_dir=Path(sys.argv[3]), _runtime_factory=tests.fixture_runtime)
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
                    host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR, _runtime_factory=fixture_runtime)
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
manager = module.SurveyManager(target=pathlib.Path(sys.argv[2]), runner=tests.FakeRunner(), host_probe=tests.FakeHostProbe(), source_dir=pathlib.Path(sys.argv[3]), _runtime_factory=tests.fixture_runtime)
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

    def test_install_pull_does_not_load_previous_owner_overlay_before_prepare(self):
        manifest, _ = self.write_bundle_manifest()
        admitted = []
        def runtime_factory(manager, version, *, create=False):
            admitted.append((version, create))
            return fixture_runtime(manager, version, create=create)
        manager = self.manager(_runtime_factory=runtime_factory)
        original = manager._compose
        calls = []
        def compose(version, *, owned_overlay=True):
            calls.append((version, owned_overlay, bool(admitted)))
            if not admitted and owned_overlay:
                raise self.module.IntegrityError('target overlay cannot precede source transition')
            return original(version, owned_overlay=owned_overlay)
        with mock.patch.object(manager, '_compose', side_effect=compose):
            manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        self.assertTrue(calls)
        self.assertEqual((False, False), calls[0][1:])
        self.assertEqual([(self.module.load_manifest(manifest).version, True)], admitted)
        self.assertTrue(manager.current_path.exists())

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
        intent = (self.target / '.surveyctl/operation.intent').read_bytes()
        with self.assertRaises(self.module.RuntimeHealthError):
            manager.uninstall(True, f"PURGE {manager.target}")
        self.assertTrue((outside / "keep").exists())
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("healthy", state["status"])  # Read-only refusal precedes intent/deletion.
        self.assertEqual(intent, (self.target / '.surveyctl/operation.intent').read_bytes())

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
            _runtime_factory=fixture_runtime,
        )

    def test_group_writable_empty_target_is_rejected_before_control_creation(self):
        self.target.mkdir(mode=0o775)
        self.target.chmod(0o775)
        with self.assertRaises(self.module.IntegrityError):
            self.install()
        self.assertFalse((self.target / ".surveyctl").exists())
        self.assertEqual([], self.runner.commands)

    def test_unsafe_installed_paths_cannot_launch_helpers(self):
        relatives = (".", "releases", "releases/0.2.0", "releases/0.2.0/backup.py",
                     "releases/0.2.0/restore.py", "releases/0.2.0/doctor.py")
        for index, relative in enumerate(relatives):
            for action in ("backup", "doctor", "verify"):
                self.target = self.root / f"unsafe-{index}-{action}"
                self.install()
                path = self.target / relative
                path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o020)
                self.runner.commands.clear()
                manager = self.manager()
                with self.subTest(path=relative, action=action):
                    with self.assertRaises(self.module.IntegrityError):
                        if action == "backup":
                            manager.backup("unsafe")
                        elif action == "doctor":
                            manager.doctor()
                        else:
                            backup = self.target / "backups/sample"
                            write_fake_backup(backup)
                            with manager._mutation_lock():
                                manager._verify_backup(backup, "0.2.0", "920")
                    self.assertEqual([], self.runner.commands)

    def test_backup_preflight_failure_preserves_complete_intent_and_allows_retry(self):
        for index, failure in enumerate(("helper-mode", "release-mode", "helper-content")):
            self.target = self.root / f"preflight-{index}"
            self.install()
            intent = self.target / ".surveyctl/operation.intent"
            before = intent.read_bytes()
            path = self.target / ("releases/0.2.0" if failure == "release-mode" else "releases/0.2.0/backup.py")
            mode = stat.S_IMODE(path.stat().st_mode)
            content = path.read_bytes() if path.is_file() else None
            if failure == "helper-content":
                path.write_bytes(b"# harmless changed helper\n")
            else:
                path.chmod(mode | 0o020)
            self.runner.commands.clear()
            with self.subTest(failure=failure):
                with self.assertRaises(self.module.IntegrityError):
                    self.manager().backup("preflight-only")
                self.assertEqual(before, intent.read_bytes())
                self.assertFalse((self.target / "backups").exists())
                self.assertEqual([], self.runner.commands)
                if content is not None:
                    path.write_bytes(content)
                path.chmod(mode)
                self.manager().status()
                self.assertEqual(before, intent.read_bytes())
                self.manager().backup("retry-after-preflight")
                self.assertEqual("complete", json.loads(intent.read_text())["state"])

    def test_backup_drift_after_intent_stays_pending_without_helper_execution(self):
        self.install()
        manager = self.manager()
        original_begin = manager._begin_operation
        helper = self.target / "releases/0.2.0/backup.py"
        def drift_after_begin():
            original_begin()
            helper.chmod(0o664)
        self.runner.commands.clear()
        with mock.patch.object(manager, "_begin_operation", side_effect=drift_after_begin):
            with self.assertRaises(self.module.IntegrityError):
                manager.backup("drift")
        self.assertEqual([], self.runner.commands)
        intent = self.target / ".surveyctl/operation.intent"
        self.assertEqual("pending", json.loads(intent.read_text())["state"])
        helper.chmod(0o644)
        with self.assertRaisesRegex(self.module.InputError, "unverified outcome"):
            self.manager().doctor()

    def test_target_replacement_after_lock_is_rejected_before_yield(self):
        self.install()
        original_load = self.module.json.load
        moved = self.root / "original-installation"
        changed = False
        def swap_target(handle, *args, **kwargs):
            nonlocal changed
            value = original_load(handle, *args, **kwargs)
            if not changed:
                changed = True
                self.target.rename(moved)
                self.target.mkdir(mode=0o700)
                # Reusing the exact control inode must not make a new target trusted.
                (moved / ".surveyctl").rename(self.target / ".surveyctl")
                (self.target / "foreign-sentinel").write_text("preserve")
            return value
        with mock.patch.object(self.module.json, "load", side_effect=swap_target):
            with self.assertRaisesRegex(self.module.IntegrityError, "target identity changed"):
                with self.manager()._mutation_lock():
                    self.fail("replacement target must not be used")
        self.assertEqual("preserve", (self.target / "foreign-sentinel").read_text())

    def test_mkdir_refuses_to_chmod_an_unsafe_existing_directory(self):
        self.target.mkdir()
        path = self.target / "releases"
        path.mkdir()
        path.chmod(0o775)
        with self.assertRaises(self.module.IntegrityError):
            self.manager()._mkdir("releases")
        self.assertEqual(0o775, stat.S_IMODE(path.stat().st_mode))

    def test_modified_owned_helper_is_rejected_before_execution(self):
        self.install()
        (self.target / "releases/0.2.0/doctor.py").write_text("# harmless modification\n")
        self.runner.commands.clear()
        with self.assertRaisesRegex(self.module.IntegrityError, "checksum mismatch"):
            self.manager().doctor()
        self.assertEqual([], self.runner.commands)

    def test_foreign_owner_path_and_writable_ancestor_are_rejected(self):
        self.target.mkdir()
        manager = self.manager()
        real_lstat = self.module.os.lstat
        def foreign(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if Path(path) == self.target:
                values = list(info)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return info
        with mock.patch.object(self.module.os, "lstat", side_effect=foreign):
            with self.assertRaises(self.module.IntegrityError):
                manager._assert_target_available()
        self.root.chmod(0o775)
        try:
            with self.assertRaises(self.module.IntegrityError):
                manager._assert_target_available()
        finally:
            self.root.chmod(0o700)

    def test_owned_readonly_helpers_and_directories_remain_usable(self):
        self.install()
        for directory in (self.target, self.target / "releases", self.target / "releases/0.2.0"):
            directory.chmod(0o755)
        for name in ("backup.py", "restore.py", "doctor.py"):
            (self.target / "releases/0.2.0" / name).chmod(0o444)
        self.manager().backup("safe-modes")
        self.assertTrue(any("backup.py" in " ".join(c) for c in self.runner.commands))

    def test_version_install_uses_release_client_and_requires_checksum(self):
        payload = self.base_manifest.read_bytes()
        client = FakeReleaseClient(payload, {self.base_bundle.name: self.base_bundle.read_bytes()})
        manager = self.module.SurveyManager(
            target=self.target,
            runner=self.runner,
            host_probe=FakeHostProbe(),
            release_client=client,
            source_dir=PRODUCTION_DIR,
            _runtime_factory=fixture_runtime,
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
            {"install", "setup-probe", "upgrade", "rollback", "backup", "restore", "recovery-plan", "recovery-reserve", "status", "doctor", "logs", "uninstall"},
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

    def test_helper_exit_codes_in_explicit_cli_fixture_and_default_legacy_denial(self):
        helper_source = ("import pathlib,sys\n"
                         "target=pathlib.Path(sys.argv[sys.argv.index('--target')+1])\n"
                         "sys.exit(0 if 'verify' in sys.argv and (target/'allow-verify').exists() else (5 if 'verify' in sys.argv else 4))\n")
        manifest, _ = write_test_release(self.root, restore_source=helper_source.encode())
        target = self.root / "helper-restore-closed"
        manager = self.module.SurveyManager(target=target, runner=FakeRunner(), host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR, _runtime_factory=fixture_runtime)
        manager.install(manifest, "survey.example.com", "operations", "operations@example.com")
        write_fake_backup(target / "backups" / "sample")
        result = subprocess.run([sys.executable, str(MODULE_PATH), "--target", str(target), "restore", "sample"], capture_output=True, text=True)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("controlled restore is disabled", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual("complete", json.loads((target / ".surveyctl/operation.intent").read_text())["state"])
        other_target = self.root / "helper-backup-target"
        other_manager = self.module.SurveyManager(target=other_target, runner=FakeRunner(), host_probe=FakeHostProbe(), source_dir=PRODUCTION_DIR, _runtime_factory=fixture_runtime)
        backup_manifest, _ = write_test_release(self.root, payload_overrides={"backup.py": b"import sys\nsys.exit(5)\n"})
        other_manager.install(backup_manifest, "survey.example.com", "operations", "operations@example.com")
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--target", str(other_target), "backup", "--output", "helper-integrity"],
            capture_output=True, text=True,
        )
        self.assertEqual(4, result.returncode, result.stderr)
        self.assertIn('controlled runtime ownership is unavailable', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual('complete', json.loads((other_target / '.surveyctl/operation.intent').read_text())['state'])
        # The old helper exit-code contract is retained in an explicit test-only
        # CLI factory. Production has no environment/argument bypass to this.
        script = r'''import importlib.util,sys
spec=importlib.util.spec_from_file_location('survey_fixture',sys.argv[1])
tests=importlib.util.module_from_spec(spec);spec.loader.exec_module(tests)
m=tests.load_module();Base=m.SurveyManager
class FixtureManager(Base):
    def __init__(self,target):
        super().__init__(target,_runtime_factory=tests.fixture_runtime)
m.SurveyManager=FixtureManager
raise SystemExit(m.main(['--target',sys.argv[2],'backup','--output','helper-integrity']))
'''
        result = subprocess.run([sys.executable, '-c', script, str(Path(__file__).resolve()), str(other_target)],
                                capture_output=True, text=True)
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

    def test_uninstall_refuses_when_runtime_discovery_is_unavailable(self):
        self.install()
        self.runner.commands.clear()
        self.runner.fail_when = lambda command: command[0] == "docker" and "down" not in command
        with self.assertRaises(self.module.SurveyctlError):
            self.manager().uninstall(False, None)
        self.assertFalse(any("down" in command for command in self.runner.commands))
        self.assertEqual("healthy", self.manager()._read_state()["status"])

    def test_uninstall_zero_exit_without_resource_verification_is_not_success(self):
        self.install()
        # FakeRunner keeps reporting the original services as running after a
        # successful no-op `down`: inject residual resources, not a real daemon.
        self.runner.commands.clear()
        self.runner.down_noop = True
        with self.assertRaises(self.module.SurveyctlError):
            self.manager().uninstall(False, None)
        self.assertNotEqual("uninstalled", self.manager()._read_state()["status"])

    def test_disabled_restore_refusal_preserves_state_and_complete_intent(self):
        self.install()
        state = self.target / ".surveyctl/state.json"
        intent = self.target / ".surveyctl/operation.intent"
        before = (state.read_bytes(), intent.read_bytes())
        self.runner.commands.clear()
        with self.assertRaisesRegex(self.module.InputError, "controlled restore is disabled"):
            self.manager().restore("nonexistent-backup")
        self.assertEqual(before, (state.read_bytes(), intent.read_bytes()))
        self.assertEqual([], self.runner.commands)

    def test_failed_install_cleanup_never_issues_runtime_mutation(self):
        with self.assertRaises(self.module.RuntimeHealthError):
            self.manager(probe=FakeHostProbe(tls_valid=False)).install(
                self.base_manifest, "survey.example.com", "operations", "operations@example.com")
        state = (self.target / ".surveyctl/state.json").read_bytes()
        marker = self.target / ".surveyctl/operation.intent"
        before = marker.read_bytes()
        self.runner.commands.clear()
        for purge, confirm in ((False, None), (True, f"PURGE {self.target}")):
            with self.subTest(purge=purge):
                with self.assertRaisesRegex(self.module.InputError, "unverified outcome"):
                    self.manager().uninstall(purge, confirm)
                self.assertEqual([], self.runner.commands)
                self.assertEqual(before, marker.read_bytes())
                self.assertEqual(state, (self.target / ".surveyctl/state.json").read_bytes())
                self.assertTrue((self.target / "shared").exists())

    def test_uninstall_preserves_data_and_purge_requires_exact_second_confirmation(self):
        self.install()
        manager = self.manager()
        manager.uninstall(purge_data=False, confirmation=None)
        self.assertTrue((self.target / "shared").exists())
        with self.assertRaises(self.module.InputError):
            manager.uninstall(purge_data=True, confirmation="yes")
        manager.uninstall(purge_data=True, confirmation=f"PURGE {self.target.resolve()}")
        self.assertFalse((self.target / "shared").exists())

    def test_default_uninstall_never_adopts_a_legacy_fixture_without_ownership_record(self):
        self.install()
        state, intent = self.target / '.surveyctl/state.json', self.target / '.surveyctl/operation.intent'
        before = state.read_bytes(), intent.read_bytes()
        self.runner.commands.clear()
        manager = self.module.SurveyManager(self.target, runner=self.runner)
        with self.assertRaises(self.module.RuntimeHealthError): manager.uninstall(False, None)
        self.assertEqual(before, (state.read_bytes(), intent.read_bytes()))
        self.assertEqual([], self.runner.commands)
        self.assertTrue((self.target / 'shared').is_dir())

    def test_default_install_admits_runtime_before_installing_state_and_uses_owned_start(self):
        manifest = self.manifest()
        manager = self.module.SurveyManager(self.target, runner=self.runner, host_probe=FakeHostProbe())
        runtime = mock.Mock()
        runtime.install.side_effect = lambda effect: effect()
        def admitted(version, *, create=False):
            self.assertTrue(create)
            self.assertFalse(manager.state_path.exists())
            return runtime
        with mock.patch.object(manager, '_verify_owned_support') as support, \
             mock.patch.object(manager, '_runtime', side_effect=admitted):
            manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
        support.assert_called_with('0.2.0')
        runtime.ensure_admin_hash.assert_called_once_with()
        runtime.install.assert_called_once()
        self.assertFalse(any('hash-password' in command or '--remove-orphans' in command for command in self.runner.commands))
        self.assertEqual('healthy', manager._read_state()['status'])

    def test_default_install_missing_owned_code_is_rejected_before_runtime_or_pull(self):
        manifest = self.manifest()
        manager = self.module.SurveyManager(self.target, runner=self.runner, host_probe=FakeHostProbe())
        with mock.patch.object(manager, '_runtime') as runtime:
            with self.assertRaises(self.module.IntegrityError):
                manager.install(manifest, 'survey.example.com', 'operations', 'operations@example.com')
            runtime.assert_not_called()
        self.assertFalse(any('pull' in command or 'up' in command or 'hash-password' in command
                             for command in self.runner.commands))

    def test_owned_code_admission_binds_all_executing_modules_to_verified_payload(self):
        manifest, _ = write_test_release(self.root, _fixture_owned_support=True)
        self.install(manifest)
        manager = self.manager()
        manager._verify_owned_support('0.2.0')
        wrong = type('ForeignModule', (), {'__file__': '/tmp/not-the-approved-module.py'})()
        with mock.patch.dict(sys.modules, {'runtime_controller': wrong}):
            with self.assertRaisesRegex(self.module.IntegrityError, 'unbound location'):
                manager._verify_owned_support('0.2.0')

    def test_owned_code_admission_rejects_even_checksum_valid_different_controller_code(self):
        manifest, _ = write_test_release(self.root, _fixture_owned_support=True,
            payload_overrides={'runtime_controller.py': b'# different checksum-valid candidate module\n'})
        self.install(manifest)
        with self.assertRaisesRegex(self.module.IntegrityError, 'differs from the verified release'):
            self.manager()._verify_owned_support('0.2.0')

    def test_owned_code_is_checked_before_any_support_module_import(self):
        self.install()
        control = self.target / '.surveyctl'
        owner = control / 'runtime-owner.json'
        owner.write_text('{}\n'); owner.chmod(0o600)
        before = (control / 'operation.intent').read_bytes()
        manager = self.module.SurveyManager(self.target, runner=self.runner)
        imports, real_import = [], __import__
        def observed(name, *args, **kwargs):
            if name.startswith(('owned_', 'runtime_')): imports.append(name)
            return real_import(name, *args, **kwargs)
        with mock.patch('builtins.__import__', side_effect=observed):
            with self.assertRaises(self.module.SurveyctlError) as error: manager.doctor()
        self.assertEqual([], imports)
        self.assertIsInstance(error.exception, self.module.IntegrityError)
        self.assertEqual(before, (control / 'operation.intent').read_bytes())

    def test_owned_support_loads_captured_bytes_without_executing_pythonpath_shadow(self):
        manifest, _ = write_test_release(self.root, _fixture_owned_support=True)
        self.install(manifest)
        foreign = self.root / 'foreign-pythonpath'
        foreign.mkdir()
        marker = self.root / 'foreign-module-executed'
        (foreign / 'owned_crypto_child.py').write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('unexpected')\n")
        with mock.patch.dict(sys.modules), mock.patch.object(sys, 'path', [str(foreign), *sys.path]):
            sys.modules.pop('owned_crypto_child', None)
            self.manager()._load_owned_support('0.2.0')
            self.assertEqual(PRODUCTION_DIR / 'owned_crypto_child.py',
                             Path(sys.modules['owned_crypto_child'].__file__))
        self.assertFalse(marker.exists())

    def test_default_backup_and_doctor_do_not_launch_legacy_helpers(self):
        self.install()
        manager = self.module.SurveyManager(self.target, runner=self.runner)
        operation = mock.Mock()
        operation.backup.side_effect = lambda output: output
        operation.doctor.return_value = {'schemaVersion': 1, 'status': 'ok', 'checks': []}
        with mock.patch.object(manager, '_runtime') as runtime, \
             mock.patch('owned_maintenance.OwnedMaintenance', return_value=operation), \
             mock.patch.object(manager, '_checked_helper', side_effect=AssertionError('legacy helper')), \
             mock.patch.object(manager, '_run_helper', side_effect=AssertionError('legacy process')):
            self.assertEqual(self.target / 'backups/owned', manager.backup('owned'))
            with contextlib.redirect_stdout(io.StringIO()): manager.doctor()
        self.assertEqual(2, runtime.call_count)
        operation.backup.assert_called_once_with(self.target / 'backups/owned')
        operation.doctor.assert_called_once_with()

    def test_default_backup_known_preflight_failure_does_not_latch_or_leak(self):
        from backup import BackupError
        self.install()
        manager = self.module.SurveyManager(self.target, runner=self.runner)
        intent = (self.target / '.surveyctl/operation.intent').read_bytes()
        operation = mock.Mock()
        operation.preflight_backup.side_effect = BackupError('synthetic-sensitive-marker')
        with mock.patch.object(manager, '_runtime'), \
             mock.patch('owned_maintenance.OwnedMaintenance', return_value=operation):
            with self.assertRaises(self.module.SurveyctlError) as error: manager.backup('preflight')
        self.assertNotIn('synthetic-sensitive-marker', str(error.exception))
        self.assertEqual(intent, (self.target / '.surveyctl/operation.intent').read_bytes())
        operation.backup.assert_not_called()

    def test_default_backup_unknown_failure_keeps_pending_and_returns_controlled_error(self):
        from owned_maintenance import UncertainMaintenance
        self.install()
        manager = self.module.SurveyManager(self.target, runner=self.runner)
        operation = mock.Mock()
        operation.backup.side_effect = UncertainMaintenance('synthetic-sensitive-marker')
        with mock.patch.object(manager, '_runtime'), \
             mock.patch('owned_maintenance.OwnedMaintenance', return_value=operation):
            with self.assertRaises(self.module.RuntimeHealthError) as error: manager.backup('unknown')
        self.assertNotIn('synthetic-sensitive-marker', str(error.exception))
        self.assertEqual('pending', json.loads((self.target / '.surveyctl/operation.intent').read_text())['state'])

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


class RecoveryPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('recovery_plan_ctl', PRODUCTION_DIR / 'surveyctl.py')
        cls.ctl = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.ctl
        if str(PRODUCTION_DIR) not in sys.path:
            sys.path.insert(0, str(PRODUCTION_DIR))
        spec.loader.exec_module(cls.ctl)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'source'
        self.control = self.source / '.surveyctl'
        self.control.mkdir(parents=True, mode=0o700)
        self.write(self.control / 'operation.lock', b'')
        self.write(self.control / 'operation.intent', {'schemaVersion': 1, 'state': 'pending', 'operation': 'install'})
        self.write(self.control / 'state.json', {'status': 'install_failed', 'version': '0.3.20-rc.1',
                   'adminEmail': 'private@example.invalid', 'privateUnknown': 'DO-NOT-OUTPUT'})
        self.destination = self.root / 'new-target'
        self.runner = mock.Mock()
        self.runner.run.side_effect = AssertionError('planning must never run a command')
        self.manager = self.ctl.SurveyManager(self.source, runner=self.runner)

    def write(self, path, value):
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        path.chmod(0o600)

    def snapshot(self):
        return {str(p.relative_to(self.source)): (p.read_bytes(), p.stat().st_mode, p.stat().st_ino)
                for p in self.source.rglob('*') if p.is_file()}

    def plan(self, **kwargs):
        return self.manager.recovery_plan(self.destination, **kwargs)

    def test_pending_pointerless_install_is_reported_without_changing_any_files(self):
        before = self.snapshot()
        plan = self.plan()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.destination.exists())
        self.assertEqual('execution-blocked', plan['status'])
        self.assertIs(False, plan['executable'])
        self.assertEqual('missing', plan['source']['current'])
        self.assertEqual('pending', plan['source']['intent'])
        self.assertEqual(plan, self.plan())
        self.assertNotIn('private@example.invalid', json.dumps(plan))
        self.assertNotIn('DO-NOT-OUTPUT', json.dumps(plan))
        self.assertIn('helper-containment-and-crash-cleanup', plan['requiredChecks'])
        self.runner.run.assert_not_called()

    def test_recovery_required_and_mismatched_current_are_observations_not_reset(self):
        self.write(self.control / 'state.json', {'status': 'recovery_required', 'version': '0.3.19-rc.1'})
        self.write(self.source / 'current', b'0.3.20-rc.1\n')
        before = self.snapshot()
        plan = self.plan()
        self.assertEqual('differs-from-state', plan['source']['current'])
        self.assertEqual('recovery_required', plan['source']['status'])
        self.assertEqual(before, self.snapshot())
        with self.assertRaises(self.ctl.InputError):
            with self.manager._mutation_lock(operation='backup'):
                self.fail('planning must not clear existing mutation gate')

    def test_first_install_without_state_can_be_observed(self):
        (self.control / 'state.json').unlink()
        plan = self.plan()
        self.assertEqual('missing', plan['source']['status'])
        self.assertIsNone(plan['source']['version'])
        self.assertEqual('pending', plan['source']['intent'])

    def test_backup_metadata_is_not_mistaken_for_authentication_or_payload_verification(self):
        backup = self.source / 'backups' / 'before-upgrade'
        backup.mkdir(parents=True, mode=0o700)
        self.write(backup / 'manifest.json', {'schemaVersion': 2, 'version': '0.3.19-rc.1'})
        (self.source / 'shared').mkdir()
        (self.source / 'shared/secret-sentinel').write_text('MUST-NOT-BE-READ')
        plan = self.plan(backup='before-upgrade')
        self.assertIs(False, plan['backup']['authenticated'])
        self.assertIs(False, plan['backup']['payloadsChecked'])
        first = plan['source']['snapshotSha256']
        self.write(backup / 'manifest.json', {'schemaVersion': 2, 'version': '0.3.17-rc.1'})
        self.assertNotEqual(first, self.plan(backup='before-upgrade')['source']['snapshotSha256'])
        self.runner.run.assert_not_called()
        for invalid in ('../backup', '/backup', 'backup/other', 'a\n', ''):
            with self.subTest(backup=invalid), self.assertRaises(self.ctl.InputError):
                self.plan(backup=invalid)

    def test_overlap_existing_destination_symlink_and_relative_paths_fail_closed(self):
        for path in (self.source, self.source / 'nested', self.root, Path('relative')):
            with self.subTest(path=path), self.assertRaises(self.ctl.InputError):
                self.manager.recovery_plan(path)
        self.destination.mkdir()
        with self.assertRaises(self.ctl.InputError): self.plan()
        self.destination.rmdir()
        self.destination.symlink_to(self.source)
        with self.assertRaises(self.ctl.InputError): self.plan()

    def test_unsafe_state_intent_and_lock_are_rejected_without_runner_calls(self):
        for name in ('state.json', 'operation.intent', 'operation.lock'):
            path = self.control / name
            original = path.read_bytes()
            path.chmod(0o644)
            with self.subTest(name=name), self.assertRaises(self.ctl.IntegrityError): self.plan()
            path.chmod(0o600)
            linked = self.root / ('hard-' + name)
            os.link(path, linked)
            with self.assertRaises(self.ctl.IntegrityError): self.plan()
            linked.unlink()
            path.unlink(); path.symlink_to(linked)
            with self.assertRaises(self.ctl.IntegrityError): self.plan()
            path.unlink(); self.write(path, original)
        self.runner.run.assert_not_called()

    def test_duplicate_malformed_or_oversized_metadata_never_produces_a_plan(self):
        for name, bad in [('operation.intent', b'{"schemaVersion":1,"schemaVersion":1,"state":"pending","operation":"install"}'),
                          ('operation.intent', b'{"schemaVersion":true,"state":"pending","operation":"install"}'),
                          ('state.json', b'not-json'), ('state.json', b'x' * (64 * 1024 + 1))]:
            path = self.control / name; original = path.read_bytes()
            self.write(path, bad)
            with self.subTest(name=name, size=len(bad)), self.assertRaises(self.ctl.IntegrityError): self.plan()
            self.write(path, original)

    def test_lock_is_not_created_or_bypassed(self):
        lock = self.control / 'operation.lock'
        with lock.open('rb') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(self.ctl.InputError): self.plan()
        lock.unlink()
        with self.assertRaises(self.ctl.IntegrityError): self.plan()
        self.assertFalse(lock.exists())

    def test_in_place_change_after_read_is_preserved_and_rejected(self):
        original = json.loads
        state = self.control / 'state.json'
        changed = b'{"status":"upgrade_failed","version":"0.3.20-rc.1"}'
        def loads(data, **kwargs):
            value = original(data, **kwargs)
            if isinstance(value, dict) and value.get('status') == 'install_failed':
                self.write(state, changed)
            return value
        with mock.patch.object(self.ctl.json, 'loads', side_effect=loads), self.assertRaises(self.ctl.IntegrityError):
            self.plan()
        self.assertEqual(changed, state.read_bytes())
        self.assertEqual('pending', original((self.control / 'operation.intent').read_text())['state'])

    def test_replaced_lock_or_intent_never_produces_a_plan(self):
        original = json.loads
        for name in ('operation.lock', 'operation.intent'):
            path = self.control / name
            payload = path.read_bytes()
            def loads(data, **kwargs):
                value = original(data, **kwargs)
                if isinstance(value, dict) and value.get('status') == 'install_failed':
                    temporary = self.control / 'replacement'
                    self.write(temporary, payload); temporary.replace(path)
                return value
            with self.subTest(name=name), mock.patch.object(self.ctl.json, 'loads', side_effect=loads), self.assertRaises(self.ctl.IntegrityError):
                self.plan()
            self.assertEqual(payload, path.read_bytes())

    def test_missing_current_appearing_or_destination_created_mid_read_is_rejected(self):
        original = json.loads
        backup = self.source / 'backups' / 'valid'
        backup.mkdir(parents=True, mode=0o700)
        self.write(backup / 'manifest.json', {'schemaVersion': 2, 'version': '0.3.19-rc.1'})
        for path in (self.source / 'current', self.destination):
            def loads(data, **kwargs):
                value = original(data, **kwargs)
                if isinstance(value, dict) and value.get('schemaVersion') == 2:
                    if path == self.destination: path.mkdir()
                    else: self.write(path, b'0.3.20-rc.1')
                return value
            with self.subTest(path=path), mock.patch.object(self.ctl.json, 'loads', side_effect=loads), self.assertRaises(self.ctl.IntegrityError):
                self.plan(backup='valid')
            if path.is_dir(): path.rmdir()
            else: path.unlink()

    def test_non_string_status_and_fifo_fail_without_blocking(self):
        self.write(self.control / 'state.json', {'status': [], 'version': '0.3.20-rc.1'})
        with self.assertRaises(self.ctl.IntegrityError): self.plan()
        (self.control / 'state.json').unlink()
        os.mkfifo(self.control / 'state.json', 0o600)
        with self.assertRaises(self.ctl.IntegrityError): self.plan()

    def test_cli_emits_only_a_non_executable_plan(self):
        before = self.snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.ctl.main(['--target', str(self.source), 'recovery-plan', '--destination', str(self.destination)])
        self.assertEqual(0, code)  # Successful observation, never recovery success.
        self.assertIs(False, json.loads(output.getvalue())['executable'])
        self.assertEqual(before, self.snapshot())

    def test_runtime_collection_contains_the_actual_cli_command(self):
        spec = importlib.util.spec_from_file_location('recovery_plan_builder', PRODUCTION_DIR / 'release/build_release.py')
        builder = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, 'path', [str(PRODUCTION_DIR / 'release'), *sys.path]):
            spec.loader.exec_module(builder)
        files = builder.collect_runtime(PRODUCTION_DIR, PRODUCTION_DIR.parents[2])
        self.assertIn(b'recovery-plan', files['surveyctl.py'])
        metadata = json.loads(builder.bundle_metadata('0.3.21-rc.1', 'amd64', files))
        self.assertEqual(hashlib.sha256(files['surveyctl.py']).hexdigest(), metadata['files']['surveyctl.py'])



class RecoveryReservationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('recovery_reservation_ctl', PRODUCTION_DIR / 'surveyctl.py')
        cls.ctl = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.ctl
        if str(PRODUCTION_DIR) not in sys.path:
            sys.path.insert(0, str(PRODUCTION_DIR))
        spec.loader.exec_module(cls.ctl)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'source'
        control = self.source / '.surveyctl'
        control.mkdir(parents=True, mode=0o700)
        for name, value in [('operation.lock', b''),
                            ('operation.intent', b'{"schemaVersion":1,"state":"pending","operation":"install"}'),
                            ('state.json', b'{"status":"install_failed","version":"0.3.20-rc.1"}')]:
            (control / name).write_bytes(value); (control / name).chmod(0o600)
        self.destination = self.root / 'reserved-target'
        self.runner = mock.Mock(); self.runner.run.side_effect = AssertionError('no runtime commands are allowed')
        self.manager = self.ctl.SurveyManager(self.source, runner=self.runner)
        self.observed = self.manager.recovery_plan(self.destination)['source']['snapshotSha256']

    def reserve(self, destination=None, observed=None):
        return self.manager.recovery_reserve(destination or self.destination, observed or self.observed)

    def source_bytes(self):
        return {str(p.relative_to(self.source)): p.read_bytes() for p in self.source.rglob('*') if p.is_file()}

    def test_records_private_pending_identity_without_recovery_authorization(self):
        before = self.source_bytes()
        result = self.reserve()
        control = self.destination / '.surveyctl'
        record = json.loads((control / 'recovery.reservation.json').read_text())
        self.assertEqual('reserved-not-authorized', result['status'])
        self.assertIs(False, result['executable'])
        self.assertEqual(self.observed, record['sourceObservationSha256'])
        self.assertRegex(record['reservationId'], r'^[0-9a-f]{32}$')
        self.assertEqual('survey-recovery-' + record['reservationId'], record['projectProposal'])
        self.assertIs(False, record['backupAuthenticated'])
        self.assertIs(False, record['runtimeResourcesCreated'])
        self.assertEqual([self.destination.stat().st_dev, self.destination.stat().st_ino], record['targetIdentity'])
        self.assertEqual('pending', json.loads((control / 'operation.intent').read_text())['state'])
        for path in (self.destination, control): self.assertEqual(0o700, stat.S_IMODE(path.stat().st_mode))
        for path in control.iterdir(): self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual(before, self.source_bytes()); self.runner.run.assert_not_called()
        with self.assertRaises(self.ctl.InputError):
            with self.ctl.SurveyManager(self.destination)._mutation_lock(operation='install'):
                self.fail('a recorded reservation must not reopen ordinary mutation')

    def test_repeat_cannot_overwrite_existing_reservation(self):
        self.reserve()
        marker = self.destination / '.surveyctl/recovery.reservation.json'
        before = marker.read_bytes()
        with self.assertRaises(self.ctl.InputError): self.reserve()
        self.assertEqual(before, marker.read_bytes())

    def test_two_distinct_targets_get_distinct_proposals_without_runtime_resources(self):
        first = self.reserve()
        second = self.reserve(self.root / 'another-target')
        self.assertNotEqual(first['reservationId'], second['reservationId'])
        self.assertIs(False, first['executable']); self.assertIs(False, second['executable'])
        self.runner.run.assert_not_called()

    def test_cooperating_concurrent_creation_has_one_winner(self):
        start = threading.Barrier(2)
        def attempt():
            start.wait(timeout=5)
            try:
                return self.reserve()['reservationId']
            except (self.ctl.InputError, self.ctl.IntegrityError):
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            result = [f.result(timeout=10) for f in futures]
        self.assertEqual(1, sum(value is not None for value in result))
        record = json.loads((self.destination / '.surveyctl/recovery.reservation.json').read_text())
        self.assertIn(record['reservationId'], result)

    def test_changed_or_malformed_observation_never_creates_a_target(self):
        path = self.source / '.surveyctl/state.json'
        path.write_text('{"status":"recovery_required","version":"0.3.20-rc.1"}')
        with self.assertRaises(self.ctl.IntegrityError): self.reserve()
        self.assertFalse(self.destination.exists())
        for value in ('bad', 'A'*64, True, None):
            with self.subTest(value=value), self.assertRaises(self.ctl.InputError):
                self.manager.recovery_reserve(self.destination, value)
        self.assertFalse(self.destination.exists())

    def test_unsafe_parent_or_preexisting_target_is_not_adopted(self):
        self.root.chmod(0o777)
        try:
            with self.assertRaises(self.ctl.IntegrityError): self.reserve()
        finally:
            self.root.chmod(0o700)
        self.assertFalse(self.destination.exists())
        self.destination.mkdir()
        with self.assertRaises(self.ctl.InputError): self.reserve()
        self.assertEqual([], list(self.destination.iterdir()))

    def test_unsafe_ancestor_cannot_hide_behind_a_private_destination_parent(self):
        before = self.source_bytes()
        for index, mode in enumerate((0o775, 0o777)):
            ancestor = self.root / f"writable-ancestor-{index}"
            parent = ancestor / "private-parent"
            parent.mkdir(parents=True, mode=0o700)
            ancestor.chmod(mode)
            destination = parent / "new-target"
            with self.subTest(mode=oct(mode)):
                with self.assertRaises(self.ctl.IntegrityError):
                    self.reserve(destination)
                self.assertFalse(destination.exists())
                self.assertEqual(before, self.source_bytes())
        self.runner.run.assert_not_called()

    def test_foreign_owned_ancestor_is_rejected_before_target_creation(self):
        ancestor = self.root / "foreign-ancestor"
        parent = ancestor / "private-parent"
        parent.mkdir(parents=True, mode=0o700)
        destination = parent / "new-target"
        identity = (ancestor.stat().st_dev, ancestor.stat().st_ino)
        fstat, inspect = self.ctl.os.fstat, self.ctl.os.stat
        def foreign(info):
            if (info.st_dev, info.st_ino) == identity:
                fields = list(info)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return info
        before = self.source_bytes()
        with mock.patch.object(self.ctl.os, "fstat", side_effect=lambda fd: foreign(fstat(fd))), \
                mock.patch.object(self.ctl.os, "stat", side_effect=lambda *a, **kw: foreign(inspect(*a, **kw))):
            with self.assertRaises(self.ctl.IntegrityError):
                self.reserve(destination)
        self.assertFalse(destination.exists())
        self.assertEqual(before, self.source_bytes())
        self.runner.run.assert_not_called()

    def test_interrupt_before_first_record_never_allows_ordinary_install_adoption(self):
        before = self.source_bytes()
        mkdir, opened = self.ctl.os.mkdir, self.ctl.os.open
        for phase in ("control-directory", "first-record"):
            self.destination = self.root / f"interrupted-{phase}"
            def fail_mkdir(name, *args, **kwargs):
                if phase == "control-directory" and name == ".surveyctl":
                    raise OSError("synthetic pre-record interruption")
                return mkdir(name, *args, **kwargs)
            def fail_open(name, flags, *args, **kwargs):
                if phase == "first-record" and name == "recovery.reservation.json" and flags & os.O_CREAT:
                    raise OSError("synthetic pre-record interruption")
                return opened(name, flags, *args, **kwargs)
            with self.subTest(phase=phase):
                with mock.patch.object(self.ctl.os, "mkdir", side_effect=fail_mkdir), \
                        mock.patch.object(self.ctl.os, "open", side_effect=fail_open):
                    with self.assertRaises(self.ctl.IntegrityError):
                        self.reserve()
                self.assertEqual(before, self.source_bytes())
                self.runner.run.assert_not_called()
                # Stronger publication invariant: no partially initialized
                # installation target is visible at all; only a private sibling.
                self.assertFalse(self.destination.exists())
                stages = list(self.root.glob(".surveyctl-recovery-*"))
                self.assertTrue(stages)
                for stage in stages:
                    for offered in (stage, stage / "nested-install"):
                        with self.assertRaises(self.ctl.InputError):
                            with self.ctl.SurveyManager(offered)._mutation_lock(create_target=True, operation="install"):
                                pass
                    self.assertFalse((stage / "nested-install").exists())

    def test_fsync_failure_preserves_partial_target_and_original_marker(self):
        before = self.source_bytes()
        with mock.patch.object(self.ctl.os, 'fsync', side_effect=OSError('synthetic failure')):
            with self.assertRaises(self.ctl.IntegrityError): self.reserve()
        self.assertFalse(self.destination.exists())
        stages = list(self.root.glob(".surveyctl-recovery-*"))
        self.assertEqual(1, len(stages))
        marker = stages[0] / '.surveyctl/recovery.reservation.json'
        self.assertTrue(marker.exists())
        partial = marker.read_bytes()
        self.assertEqual(before, self.source_bytes())
        # Retrying is a fresh reservation, not adoption of the private partial.
        self.reserve()
        self.assertEqual(partial, marker.read_bytes())
        self.assertNotEqual(stages[0].stat().st_ino, self.destination.stat().st_ino)
        self.runner.run.assert_not_called()

    def test_replaced_target_is_not_followed_cleaned_or_reported_successful(self):
        opened = os.open
        moved = self.root / 'preserved-original-reservation'
        def intercept(name, flags, *args, **kwargs):
            if name == 'operation.lock' and flags & os.O_CREAT:
                stage = next(self.root.glob(".surveyctl-recovery-*"))
                self.displaced_stage = stage
                stage.rename(moved)
                stage.mkdir(mode=0o700)
                (stage / 'foreign-sentinel').write_text('preserve')
            return opened(name, flags, *args, **kwargs)
        with mock.patch.object(self.ctl.os, 'open', side_effect=intercept):
            with self.assertRaises(self.ctl.IntegrityError): self.reserve()
        self.assertFalse(self.destination.exists())
        self.assertEqual(['foreign-sentinel'], [p.name for p in self.displaced_stage.iterdir()])
        self.assertEqual('preserve', (self.displaced_stage / 'foreign-sentinel').read_text())
        self.assertTrue((moved / '.surveyctl/recovery.reservation.json').exists())

    def test_record_change_during_final_directory_flush_never_returns_success(self):
        flush = os.fsync
        parent_identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        marker = self.destination / '.surveyctl/recovery.reservation.json'
        def intercept(fd):
            flush(fd)
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == parent_identity and self.destination.exists():
                marker.write_text(marker.read_text().replace('"runtimeResourcesCreated": false', '"runtimeResourcesCreated": true'))
        before = self.source_bytes()
        with mock.patch.object(self.ctl.os, 'fsync', side_effect=intercept):
            with self.assertRaises(self.ctl.IntegrityError): self.reserve()
        self.assertEqual(before, self.source_bytes())
        self.assertTrue(json.loads(marker.read_text())['runtimeResourcesCreated'])
        self.runner.run.assert_not_called()

    def test_atomic_publication_never_overwrites_a_racing_target(self):
        publish = self.ctl._publish_directory_noreplace
        before = self.source_bytes()
        for populated in (False, True):
            self.destination = self.root / f"racing-{populated}"
            saved = []
            def create_competitor(fd, source, destination):
                self.destination.mkdir(mode=0o700)
                saved.append(self.destination.stat().st_ino)
                if populated:
                    (self.destination / "foreign-sentinel").write_text("preserve")
                publish(fd, source, destination)
            with self.subTest(populated=populated):
                with mock.patch.object(self.ctl, "_publish_directory_noreplace", side_effect=create_competitor):
                    with self.assertRaises(self.ctl.InputError):
                        self.reserve()
                self.assertEqual(saved[0], self.destination.stat().st_ino)
                self.assertEqual(["foreign-sentinel"] if populated else [], [p.name for p in self.destination.iterdir()])
                if populated:
                    self.assertEqual("preserve", (self.destination / "foreign-sentinel").read_text())
                self.assertEqual(before, self.source_bytes())
        self.assertEqual(2, len(list(self.root.glob(".surveyctl-recovery-*"))))
        self.runner.run.assert_not_called()

    def test_missing_atomic_primitive_refuses_without_rename_fallback(self):
        before = self.source_bytes()
        with mock.patch.object(self.ctl.ctypes, "CDLL", return_value=object()), \
                mock.patch.object(self.ctl.os, "rename", side_effect=AssertionError("unsafe fallback")):
            with self.assertRaises(self.ctl.IntegrityError):
                self.reserve()
        self.assertFalse(self.destination.exists())
        self.assertEqual(1, len(list(self.root.glob(".surveyctl-recovery-*"))))
        self.assertEqual(before, self.source_bytes())
        self.runner.run.assert_not_called()

    def test_cli_success_only_records_a_non_executable_reservation(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = self.ctl.main(['--target', str(self.source), 'recovery-reserve',
                '--destination', str(self.destination), '--observed-source-sha256', self.observed])
        self.assertEqual(0, result)
        self.assertIs(False, json.loads(stdout.getvalue())['executable'])
        self.runner.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
