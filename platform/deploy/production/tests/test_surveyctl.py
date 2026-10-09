import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


PRODUCTION_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PRODUCTION_DIR / "surveyctl.py"
FIXTURE_MANIFEST = Path(__file__).resolve().parent / "fixtures" / "release.json"


def load_module():
    spec = importlib.util.spec_from_file_location("surveyctl_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self, fail_when=None, unhealthy_service=None):
        self.commands = []
        self.fail_when = fail_when
        self.unhealthy_service = unhealthy_service

    def run(self, command, **kwargs):
        command = tuple(str(part) for part in command)
        self.commands.append(command)
        if self.fail_when and self.fail_when(command):
            raise subprocess.CalledProcessError(1, command)
        if "hash-password" in command:
            return subprocess.CompletedProcess(command, 0, stdout="$2a$14$" + "x" * 53 + "\n", stderr="")
        if "ps" in command and "--format" in command:
            services = ["edge", "admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db"]
            payload = [
                {
                    "Service": service,
                    "State": "running",
                    "Health": "unhealthy" if service == self.unhealthy_service else "healthy",
                }
                for service in services
            ]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


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
            "time_synchronized": True,
        }
        self.values.update(overrides)

    def inspect(self, host):
        return dict(self.values)


class FakeReleaseClient:
    def __init__(self, payload=None):
        self.requests = []
        self.payload = payload or FIXTURE_MANIFEST.read_bytes()

    def fetch_manifest(self, version):
        self.requests.append(version)
        return self.payload


class SurveyctlTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "survey"
        self.runner = FakeRunner()

    def manifest(self, **updates):
        data = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        data.update(updates)
        path = Path(self.tmp.name) / f"release-{data.get('version', 'bad')}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def manager(self, probe=None, runner=None):
        return self.module.SurveyManager(
            target=self.target,
            runner=runner or self.runner,
            host_probe=probe or FakeHostProbe(),
            source_dir=PRODUCTION_DIR,
        )

    def test_version_install_uses_release_client_and_requires_checksum(self):
        client = FakeReleaseClient()
        manager = self.module.SurveyManager(
            target=self.target,
            runner=self.runner,
            host_probe=FakeHostProbe(),
            release_client=client,
            source_dir=PRODUCTION_DIR,
        )
        with self.assertRaises(self.module.InputError):
            manager.install_version("0.2.0", None, "survey.example.com", "operations", "operations@example.com")
        digest = hashlib.sha256(FIXTURE_MANIFEST.read_bytes()).hexdigest()
        manager.install_version("0.2.0", digest, "survey.example.com", "operations", "operations@example.com")
        self.assertEqual(["0.2.0"], client.requests)
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
            manifest or FIXTURE_MANIFEST,
            public_host="survey.example.com",
            admin_user="operations",
            admin_email="operations@example.com",
        )

    def test_cli_exposes_all_lifecycle_commands_and_fixed_exit_codes(self):
        parser = self.module.build_parser()
        self.assertEqual(
            {"install", "upgrade", "rollback", "backup", "restore", "status", "doctor", "logs", "uninstall"},
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
            manager.install(FIXTURE_MANIFEST, "survey.example.com\nADMIN_IMAGE=evil", "operations", "operations@example.com")
        unhealthy = self.manager(runner=FakeRunner(unhealthy_service="engine"))
        with self.assertRaises(self.module.RuntimeHealthError):
            unhealthy.install(FIXTURE_MANIFEST, "survey.example.com", "operations", "operations@example.com")
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
        failing = FakeRunner(fail_when=lambda command: "up" in command)
        next_manifest = self.manifest(version="0.3.0", minimumSourceVersion="0.2.0")
        with self.assertRaises(self.module.RuntimeHealthError):
            self.manager(runner=failing).upgrade(next_manifest)
        self.assertEqual("0.2.0", (self.target / "current").read_text().strip())
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        self.assertEqual("upgrade_failed", state["status"])
        self.assertEqual("0.2.0", state["version"])
        joined = [" ".join(command) for command in failing.commands]
        self.assertIn("releases/0.2.0/compose.yml", joined[-1])
        self.assertIn("up -d", joined[-1])

    def test_rollback_enforces_database_compatibility_gate(self):
        self.install()
        compatible = self.manifest(version="0.1.5")
        release_dir = self.target / "releases" / "0.1.5"
        release_dir.mkdir(parents=True)
        (release_dir / "release.json").write_bytes(compatible.read_bytes())
        self.manager().rollback("0.1.5")
        self.assertEqual("0.1.5", (self.target / "current").read_text().strip())

        incompatible_data = json.loads(FIXTURE_MANIFEST.read_text())
        incompatible_data["version"] = "0.1.0"
        incompatible_data["database"]["compatibleSourceSchemas"] = ["918"]
        incompatible = self.manifest(**incompatible_data)
        release_dir = self.target / "releases" / "0.1.0"
        release_dir.mkdir(parents=True)
        (release_dir / "release.json").write_bytes(incompatible.read_bytes())
        with self.assertRaises(self.module.InputError):
            self.manager().rollback("0.1.0")

        current = self.target / "releases" / "0.1.5" / "release.json"
        current_data = json.loads(current.read_text())
        current_data["database"]["rollback"] = "restore-only"
        current.write_text(json.dumps(current_data))
        with self.assertRaises(self.module.InputError):
            self.manager().rollback("0.2.0")

    def test_restore_status_doctor_and_logs_delegate_without_exposing_secrets(self):
        self.install()
        self.runner.commands.clear()
        backup = self.target / "backups" / "sample"
        backup.mkdir(parents=True)
        (backup / "manifest.json").write_text('{"version":"0.2.0"}')
        manager = self.manager()
        manager.backup(self.target / "backups" / "manual")
        manager.restore(backup)
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
                FIXTURE_MANIFEST,
                public_host="survey.example.com",
                admin_user="operations",
                admin_email="operations@example.com",
            )
        self.assertEqual("keep", (self.target / "unmanaged.txt").read_text())


if __name__ == "__main__":
    unittest.main()
