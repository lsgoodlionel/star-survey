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
    if str(PRODUCTION_DIR) not in sys.path:
        sys.path.insert(0, str(PRODUCTION_DIR))
    spec = importlib.util.spec_from_file_location("production_doctor", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DoctorRunner:
    def __init__(self, unhealthy=False, migration="920", secret="", line_delimited_health=False,
                 runtime_db_port=False, missing_https_listener=False):
        self.unhealthy = unhealthy
        self.migration = migration
        self.secret = secret
        self.line_delimited_health = line_delimited_health
        self.runtime_db_port = runtime_db_port
        self.missing_https_listener = missing_https_listener
        self.calls = []

    def run(self, command, **kwargs):
        command = tuple(str(value) for value in command)
        self.calls.append(command)
        joined = " ".join(command)
        if " ps " in f" {joined} ":
            services = ["edge", "admin-web", "platform", "publish-gateway", "engine", "platform-db", "engine-db"]
            payload = [{"Service": name, "ID": "id-" + name, "State": "running", "Health": "unhealthy" if self.unhealthy and name == "engine" else "healthy"} for name in services]
            payload.append({"Service": "engine-init", "ID": "id-engine-init", "State": "exited", "ExitCode": 0, "Health": ""})
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
        if command[:2] == ("docker", "inspect"):
            services = [value.removeprefix("id-") for value in command[2:]]
            networks = {
                "edge": ["edge"], "admin-web": ["edge"], "platform": ["edge", "internal"],
                "publish-gateway": ["internal"], "engine": ["edge", "internal"],
                "platform-db": ["internal"], "engine-db": ["internal"], "engine-init": ["internal"],
            }
            rows = []
            for service in services:
                bindings = None
                if service == "edge":
                    bindings = {
                        "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}],
                        "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}],
                    }
                elif self.runtime_db_port and service == "platform-db":
                    bindings = {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5432"}]}
                rows.append({"Id": "id-" + service, "Name": "/survey-production-" + service,
                             "Config": {"Labels": {"com.docker.compose.service": service}},
                             "HostConfig": {"PortBindings": bindings},
                             "NetworkSettings": {"Networks": {
                                 "survey-production_" + name: {} for name in networks[service]
                             }}})
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(rows), stderr="")
        if command and command[0] == "ss":
            lines = ["LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:((\"docker-proxy\",pid=1,fd=4))"]
            if not self.missing_https_listener:
                lines.append("LISTEN 0 4096 0.0.0.0:443 0.0.0.0:* users:((\"docker-proxy\",pid=2,fd=4))")
            if self.runtime_db_port:
                lines.append("LISTEN 0 4096 0.0.0.0:5432 0.0.0.0:* users:((\"docker-proxy\",pid=3,fd=4))")
            return subprocess.CompletedProcess(command, 0, stdout="\n".join(lines), stderr="")
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
        report = self.doctor.Doctor(self.target, runner=runner, tls_probe=lambda host: True,
                                    product_probe=lambda *args: None).run()
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
            product_probe=lambda *args: None,
        ).run()
        containers = next(item for item in report["checks"] if item["id"] == "containers")
        self.assertEqual("ok", containers["status"])

    def test_doctor_checks_tls_ports_internal_exposure_volumes_backups_and_probe(self):
        runner = DoctorRunner()
        report = self.doctor.Doctor(self.target, runner=runner, tls_probe=lambda host: True,
                                    product_probe=lambda *args: None).run()
        ids = {item["id"] for item in report["checks"]}
        self.assertTrue({"ports", "tls", "containers", "internal-exposure", "migrations", "volumes", "backups", "minimal-probe"} <= ids)
        self.assertEqual("warning", report["status"])
        backup = next(item for item in report["checks"] if item["id"] == "backups")
        self.assertEqual("warning", backup["status"])
        health_command = next(command for command in runner.calls if "ps" in command and "--format" in command)
        self.assertIn("--all", health_command)
        self.assertTrue(any(command[:2] == ("docker", "inspect") for command in runner.calls))
        self.assertTrue(any(command and command[0] == "ss" for command in runner.calls))

    def test_doctor_blocks_runtime_port_drift_and_missing_host_listener(self):
        for runner in (DoctorRunner(runtime_db_port=True), DoctorRunner(missing_https_listener=True)):
            with self.subTest(runner=runner):
                report = self.doctor.Doctor(
                    self.target, runner=runner, tls_probe=lambda host: True, product_probe=lambda *args: None
                ).run()
                self.assertEqual("blocked", report["status"])
                runtime = next(item for item in report["checks"] if item["id"] == "runtime-exposure")
                self.assertEqual("blocked", runtime["status"])

    def test_minimal_product_probe_is_blocking_and_never_reports_partial_success(self):
        stages = []

        def probe(*args):
            stages.extend(["login", "create", "publish", "public-answer", "query", "export", "cleanup"])
            raise self.doctor.DoctorRuntimeError("export failed")

        report = self.doctor.Doctor(
            self.target, runner=DoctorRunner(), tls_probe=lambda host: True, product_probe=probe
        ).run()
        check = next(item for item in report["checks"] if item["id"] == "minimal-probe")
        self.assertEqual("blocked", check["status"])
        self.assertEqual(["login", "create", "publish", "public-answer", "query", "export", "cleanup"], stages)

    def test_doctor_backup_check_calls_the_restore_grade_verifier(self):
        backup = self.target / "backups" / "candidate"
        backup.mkdir(parents=True)
        calls = []

        class RejectingVerifier:
            def verify_backup(self, directory, temporary):
                calls.append(directory)
                raise self_outer.doctor.RestoreIntegrityError("unsafe tar")

        self_outer = self
        report = self.doctor.Doctor(
            self.target, runner=DoctorRunner(), tls_probe=lambda host: True,
            product_probe=lambda *args: None, backup_verifier=RejectingVerifier(),
        ).run()
        check = next(item for item in report["checks"] if item["id"] == "backups")
        self.assertEqual("warning", check["status"])
        self.assertEqual([backup], calls)

    def test_product_probe_runs_complete_journey_and_cleans_up(self):
        (self.target / "shared" / "secrets" / "platform_jwt_hmac_secret").write_text("x" * 48 + "\n")
        (self.target / "shared" / "product-probe.json").write_text(json.dumps({
            "tenantId": "11111111-1111-4111-8111-111111111111", "actorId": "production-probe-owner",
        }))
        runner = DoctorRunner()
        events = []

        class Probe(self.doctor.ProductProbe):
            def _api(probe, token, method, path, body=None, raw=False):
                events.append((method, path))
                if path.startswith("/v1/resources?limit"):
                    return 200, {"items": []}
                if path == "/v1/projects":
                    return 201, {"id": "project-id"}
                if path == "/v1/surveys":
                    return 201, {"id": "survey-id"}
                if path.endswith("/publish"):
                    return 200, {"version": {"engineSid": 101, "engineInstanceId": "production-engine-01"}}
                if "/responses?" in path:
                    return 200, {"items": [{"responseId": 1}]}
                if path.endswith("/exports"):
                    return 202, {"jobId": "export-id"}
                if path == "/v1/exports/export-id":
                    return 200, {"status": "completed"}
                if path.endswith("/download"):
                    return 200, b"csv"
                if path.endswith("/archive"):
                    return 200, {"archived": True}
                raise AssertionError(path)

            def _answer(probe, sid):
                events.append(("ANSWER", str(sid)))

            def _close_engine_survey(probe, instance, sid):
                events.append(("CLOSE", f"{instance}:{sid}"))

        Probe(self.target, "survey.example.com", ["docker", "compose"], runner, now=lambda: 1, sleep=lambda _: None).run()
        self.assertIn(("ANSWER", "101"), events)
        self.assertIn(("GET", "/v1/surveys/survey-id/responses?state=engine_completed&limit=10"), events)
        self.assertIn(("GET", "/v1/exports/export-id/download"), events)
        self.assertIn(("CLOSE", "production-engine-01:101"), events)
        self.assertEqual(("POST", "/v1/resources/project-id/archive"), events[-1])

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
