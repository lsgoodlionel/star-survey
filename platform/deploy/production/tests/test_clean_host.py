import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PRODUCTION_DIR = Path(__file__).resolve().parents[1]


def load_doctor():
    path = PRODUCTION_DIR / "doctor.py"
    spec = importlib.util.spec_from_file_location("production_doctor", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DoctorRunner:
    def __init__(self, unhealthy=False, migration="920", secret="", line_delimited_health=False):
        self.unhealthy = unhealthy
        self.migration = migration
        self.secret = secret
        self.line_delimited_health = line_delimited_health
        self.calls = []

    def run(self, command, **kwargs):
        command = tuple(str(value) for value in command)
        self.calls.append(command)
        joined = " ".join(command)
        if " ps " in f" {joined} ":
            services = ["edge", "admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db"]
            payload = [{"Service": name, "State": "running", "Health": "unhealthy" if self.unhealthy and name == "engine" else "healthy"} for name in services]
            payload.append({"Service": "engine-init", "State": "exited", "ExitCode": 0, "Health": ""})
            stdout = "\n".join(json.dumps(row) for row in payload) if self.line_delimited_health else json.dumps(payload)
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if " config " in f" {joined} ":
            payload = {
                "services": {
                    "edge": {"ports": [{"published": "80"}, {"published": "443"}], "networks": ["edge"]},
                    "platform": {"networks": ["edge", "internal"]},
                    "platform-db": {"networks": ["internal"]},
                    "engine-db": {"networks": ["internal"]},
                },
                "networks": {"edge": {}, "internal": {"internal": True}},
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        if "flyway_schema_history" in joined:
            return subprocess.CompletedProcess(command, 0, stdout=self.migration + "\n", stderr="")
        if "productionInit status" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="completed\n", stderr="")
        if "volume inspect" in joined:
            names = [{"Name": value} for value in command[3:]]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(names), stderr="")
        if "probe" in joined:
            return subprocess.CompletedProcess(command, 0, stdout="ok " + self.secret, stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class DoctorAndCleanHostTest(unittest.TestCase):
    def setUp(self):
        self.doctor = load_doctor()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name).resolve() / "survey"
        release = self.target / "releases" / "0.2.0"
        release.mkdir(parents=True)
        (release / "compose.yml").write_text("services: {}\n")
        (release / ".env").write_text("PUBLIC_HOST=survey.example.com\n")
        (release / "release.json").write_text(json.dumps({"version": "0.2.0", "database": {"schema": "920"}}))
        (self.target / "current").write_text("0.2.0\n")
        (self.target / ".surveyctl").mkdir()
        (self.target / ".surveyctl" / "state.json").write_text(json.dumps({
            "version": "0.2.0", "databaseSchema": "920", "publicHost": "survey.example.com"
        }))
        secret_dir = self.target / "shared" / "secrets"
        secret_dir.mkdir(parents=True)
        (secret_dir / "platform_jwt_hmac_secret").write_text("super-secret-doctor-value\n")

    def test_doctor_reports_blockers_warnings_and_redacts_all_secret_values(self):
        runner = DoctorRunner(unhealthy=True, migration="919", secret="super-secret-doctor-value")
        report = self.doctor.Doctor(self.target, runner=runner, tls_probe=lambda host: True).run()
        encoded = json.dumps(report, sort_keys=True)
        self.assertEqual("blocked", report["status"])
        self.assertIn("containers", {item["id"] for item in report["checks"]})
        self.assertIn("migrations", {item["id"] for item in report["checks"]})
        self.assertNotIn("super-secret-doctor-value", encoded)
        self.assertNotIn(str(self.target / "shared" / "secrets"), encoded)

    def test_doctor_accepts_compose_line_delimited_health_json(self):
        report = self.doctor.Doctor(
            self.target,
            runner=DoctorRunner(line_delimited_health=True),
            tls_probe=lambda host: True,
        ).run()
        containers = next(item for item in report["checks"] if item["id"] == "containers")
        self.assertEqual("ok", containers["status"])

    def test_doctor_checks_tls_ports_internal_exposure_volumes_backups_and_probe(self):
        runner = DoctorRunner()
        report = self.doctor.Doctor(self.target, runner=runner, tls_probe=lambda host: True).run()
        ids = {item["id"] for item in report["checks"]}
        self.assertTrue({"ports", "tls", "containers", "internal-exposure", "migrations", "volumes", "backups", "minimal-probe"} <= ids)
        self.assertEqual("warning", report["status"])
        backup = next(item for item in report["checks"] if item["id"] == "backups")
        self.assertEqual("warning", backup["status"])
        health_command = next(command for command in runner.calls if "ps" in command and "--format" in command)
        self.assertIn("--all", health_command)

    def test_clean_host_scenario_stops_on_migration_failure_and_never_claims_success(self):
        scenario = self.doctor.CleanHostScenario()
        events = []

        def step(name):
            events.append(name)
            if name == "upgrade":
                raise self.doctor.DoctorRuntimeError("migration failed")

        with self.assertRaises(self.doctor.DoctorRuntimeError):
            scenario.run(step)
        self.assertEqual(["install", "doctor-before", "minimal-journey", "backup", "upgrade"], events)

    def test_clean_host_scenario_runs_the_required_order_without_skipping_restore(self):
        events = []
        self.doctor.CleanHostScenario().run(events.append)
        self.assertEqual([
            "install", "doctor-before", "minimal-journey", "backup", "upgrade",
            "doctor-after", "restore-second-project", "uninstall",
        ], events)

    def test_doctor_rejects_a_symlinked_target_without_running_diagnostics(self):
        alias = Path(self.tmp.name).resolve() / "survey-alias"
        alias.symlink_to(self.target, target_is_directory=True)
        runner = DoctorRunner()
        with self.assertRaises(self.doctor.DoctorRuntimeError):
            self.doctor.Doctor(alias, runner=runner)
        self.assertEqual([], runner.calls)

    def test_doctor_rejects_traversing_current_version_before_reading_release(self):
        outside = self.target.parent / "outside"
        outside.mkdir()
        (outside / "release.json").write_text(json.dumps({"version": "../../outside", "database": {"schema": "920"}}))
        (outside / "compose.yml").write_text("services: {}\n")
        (outside / ".env").write_text("PUBLIC_HOST=survey.example.com\n")
        (self.target / "current").write_text("../../outside\n")
        state = json.loads((self.target / ".surveyctl" / "state.json").read_text())
        state["version"] = "../../outside"
        (self.target / ".surveyctl" / "state.json").write_text(json.dumps(state))
        runner = DoctorRunner()
        with self.assertRaises(self.doctor.DoctorRuntimeError):
            self.doctor.Doctor(self.target, runner=runner).run()
        self.assertEqual([], runner.calls)


if __name__ == "__main__":
    unittest.main()
