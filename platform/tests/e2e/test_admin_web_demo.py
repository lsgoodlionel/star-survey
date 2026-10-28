import contextlib
import http.server
import importlib.util
import io
import json
import os
import pty
import stat
import subprocess
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "platform/deploy/demo/admin_web_demo.py"
RUNNER = ROOT / "platform/deploy/demo/run-admin-web-demo.sh"
COMPOSE = ROOT / "platform/deploy/demo/admin-web-demo.compose.yml"


def load_demo():
    spec = importlib.util.spec_from_file_location("admin_web_demo", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeApi:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if not self.replies:
            raise AssertionError("unexpected API call: {} {}".format(method, path))
        return self.replies.pop(0)


class AdminWebDemoTest(unittest.TestCase):
    def test_cli_accepts_lifecycle_and_ssl_gate_commands(self):
        demo = load_demo()

        for command in ("start", "stop", "status", "refresh-token"):
            self.assertEqual(command, demo.parse_args([command]).command)
        checked = demo.parse_args([
            "check-production-ssl", "--public-url", "https://survey.example",
            "--proxy-probe-url", "http://proxy.internal/health",
        ])
        self.assertEqual("check-production-ssl", checked.command)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                demo.parse_args(["destroy"])

    def test_compose_is_persistent_isolated_and_loopback_only(self):
        completed = subprocess.run(
            [
                "docker", "compose", "-p", "adminweb-demo", "-f", str(COMPOSE),
                "config", "--format", "json",
            ],
            text=True,
            capture_output=True,
            check=False,
            env=dict(
                os.environ,
                ADMIN_WEB_DEMO_PLATFORM_JAR="/tmp/business.jar",
                ADMIN_WEB_DEMO_ENGINES_FILE="/tmp/engines.json",
                PLATFORM_JWT_HMAC_SECRET="x" * 48,
                PLATFORM_ENGINE_EVENTS_SECRET="y" * 48,
                PUBGW_SHARED_SECRET="z" * 48,
                PUBGW_ENGINE_ADMIN_WEB_PASSWORD="p" * 48,
                DEMO_ENGINE_ADMIN_USER="admin",
                DEMO_ENGINE_ADMIN_PASSWORD="a" * 48,
                ENGINE_DB_ROOT_PASSWORD="b" * 48,
                PLATFORM_DB_SUPERUSER_PASSWORD="c" * 48,
                PLATFORM_DB_APP_PASSWORD="d" * 48,
                PLATFORM_DB_OWNER_PASSWORD="e" * 48,
            ),
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        config = json.loads(completed.stdout)

        self.assertEqual(
            {"engine", "engine-db", "platform", "platform-db", "gateway", "admin-web"},
            set(config["services"]),
        )
        for name in ("engine", "platform", "gateway", "admin-web"):
            published = config["services"][name]["ports"][0].get("published")
            self.assertIn(published, (None, ""))
            self.assertEqual("127.0.0.1", config["services"][name]["ports"][0]["host_ip"])
        self.assertEqual(
            {"engine-data", "engine-runtime", "engine-tmp", "platform-data"},
            set(config["volumes"]),
        )
        self.assertNotIn("tmpfs", config["services"]["engine-db"])
        self.assertNotIn("tmpfs", config["services"]["platform-db"])
        engine_sources = {
            mount["source"] for mount in config["services"]["engine"]["volumes"]
            if mount["type"] == "bind"
        }
        self.assertIn(str(ROOT), engine_sources)
        engine_targets = {mount["target"] for mount in config["services"]["engine"]["volumes"]}
        self.assertIn("/var/www/html/application/runtime", engine_targets)
        rendered = completed.stdout
        for hardcoded in ('"MARIADB_ROOT_PASSWORD":"root"', '"POSTGRES_PASSWORD":"postgres"',
                          '"PLATFORM_DB_APP_PASSWORD":"platform_app"',
                          '"PLATFORM_DB_OWNER_PASSWORD":"platform_owner"'):
            self.assertNotIn(hardcoded, rendered)

    def test_fixed_definition_has_basic_questions_advanced_theme_and_branding(self):
        demo = load_demo()

        definition = demo.demo_definition()

        self.assertEqual("品牌跟踪调查", definition["title"])
        self.assertEqual("zh-business", definition["theme"])
        self.assertEqual("zh-Hans", definition["language"])
        questions = [question for group in definition["groups"] for question in group["questions"]]
        self.assertTrue({"L", "M", "S", "T"}.issubset({question["type"] for question in questions}))
        self.assertEqual(
            ["mjy-collapsible"],
            [question["theme"] for question in questions if question.get("theme")],
        )
        self.assertEqual(1, definition["branding"]["brandingVersion"])
        for item in definition["groups"] + questions:
            self.assertEqual(str(uuid.UUID(item["uuid"])), item["uuid"])

    def test_seed_reuses_valid_fixed_resources_without_creating_duplicates(self):
        demo = load_demo()
        tenant_id = "11111111-1111-4111-8111-111111111111"
        project_id = "22222222-2222-4222-8222-222222222222"
        folder_id = "33333333-3333-4333-8333-333333333333"
        survey_id = "44444444-4444-4444-8444-444444444444"
        metadata = {
            "schemaVersion": 1,
            "tenantId": tenant_id,
            "projectId": project_id,
            "folderId": folder_id,
            "surveyId": survey_id,
            "engineInstanceId": "admin-web-demo-engine-01",
        }
        operator = FakeApi([(200, {"id": tenant_id, "code": "admin-web-demo", "status": "active"})])
        owner = FakeApi([
            (200, {"id": project_id, "kind": "project", "parentId": None, "name": "客户体验研究"}),
            (200, {"id": folder_id, "kind": "folder", "parentId": project_id, "name": "2026 Q4"}),
            (200, {"id": survey_id, "kind": "survey", "parentId": folder_id, "name": "品牌跟踪调查"}),
            (200, {"id": survey_id, "title": "品牌跟踪调查", "status": "published", "publishedVersion": 1}),
            (200, [{"version": 1, "engineSid": 45678, "live": True}]),
        ])

        result = demo.ensure_demo_seed(operator, owner, metadata, "http://127.0.0.1:49152")

        self.assertEqual(metadata, result["metadata"])
        self.assertEqual("http://127.0.0.1:49152/index.php/45678?newtest=Y&lang=zh-Hans", result["respondentUrl"])
        self.assertFalse(any(method == "POST" for method, _, _ in operator.calls + owner.calls))

    def test_seed_recovers_from_the_first_missing_resource(self):
        demo = load_demo()
        tenant_id = "11111111-1111-4111-8111-111111111111"
        project_id = "22222222-2222-4222-8222-222222222222"
        new_folder_id = "55555555-5555-4555-8555-555555555555"
        new_survey_id = "66666666-6666-4666-8666-666666666666"
        metadata = {
            "schemaVersion": 1,
            "tenantId": tenant_id,
            "projectId": project_id,
            "folderId": "33333333-3333-4333-8333-333333333333",
            "surveyId": "44444444-4444-4444-8444-444444444444",
            "engineInstanceId": "admin-web-demo-engine-01",
        }
        operator = FakeApi([(200, {"id": tenant_id, "code": "admin-web-demo", "status": "active"})])
        owner = FakeApi([
            (200, {"id": project_id, "kind": "project", "parentId": None, "name": "客户体验研究"}),
            (404, {"error": "not_found"}),
            (200, {"items": []}),
            (201, {"id": new_folder_id, "kind": "folder", "parentId": project_id, "name": "2026 Q4"}),
            (200, {"items": []}),
            (201, {"id": new_survey_id, "title": "品牌跟踪调查", "status": "draft"}),
            (200, {"id": new_survey_id, "title": "品牌跟踪调查", "status": "draft"}),
            (200, {"survey": {"id": new_survey_id, "status": "published", "publishedVersion": 1},
                   "version": {"version": 1, "engineSid": 56789, "live": True}}),
        ])

        result = demo.ensure_demo_seed(operator, owner, metadata, "http://127.0.0.1:49152")

        self.assertEqual(new_folder_id, result["metadata"]["folderId"])
        self.assertEqual(new_survey_id, result["metadata"]["surveyId"])
        self.assertEqual(
            [("POST", "/v1/folders", {"parentId": project_id, "name": "2026 Q4"})],
            [call for call in owner.calls if call[1] == "/v1/folders"],
        )
        self.assertEqual(1, sum(path == "/v1/surveys" for _, path, _ in owner.calls))
        self.assertEqual(1, sum(path.endswith("/publish") for _, path, _ in owner.calls))

    def test_seed_persists_created_ids_before_publish_can_fail(self):
        demo = load_demo()
        tenant_id = "11111111-1111-4111-8111-111111111111"
        project_id = "22222222-2222-4222-8222-222222222222"
        folder_id = "55555555-5555-4555-8555-555555555555"
        survey_id = "66666666-6666-4666-8666-666666666666"
        metadata = {
            "schemaVersion": 1,
            "tenantId": tenant_id,
            "projectId": project_id,
            "folderId": None,
            "surveyId": None,
            "engineInstanceId": "admin-web-demo-engine-01",
        }
        operator = FakeApi([(200, {"id": tenant_id, "code": "admin-web-demo", "status": "active"})])
        owner = FakeApi([
            (200, {"id": project_id, "kind": "project", "parentId": None, "name": "客户体验研究"}),
            (200, {"items": []}),
            (201, {"id": folder_id, "kind": "folder", "parentId": project_id, "name": "2026 Q4"}),
            (200, {"items": []}),
            (201, {"id": survey_id, "title": "品牌跟踪调查", "status": "draft"}),
            (200, {"id": survey_id, "title": "品牌跟踪调查", "status": "draft"}),
            (502, {"error": "publish_failed"}),
        ])

        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory, "metadata.json")
            with self.assertRaises(demo.DemoError):
                demo.ensure_demo_seed(
                    operator, owner, metadata, "http://127.0.0.1:49152", metadata_path=metadata_path,
                )
            persisted = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(folder_id, persisted["folderId"])
        self.assertEqual(survey_id, persisted["surveyId"])

    def test_seed_only_treats_404_as_missing_and_rejects_bad_gets(self):
        demo = load_demo()
        tenant_id = "11111111-1111-4111-8111-111111111111"
        base = {
            "schemaVersion": 2, "tenantId": tenant_id,
            "projectId": "22222222-2222-4222-8222-222222222222",
            "folderId": None, "surveyId": None, "engineInstanceId": "admin-web-demo-engine-01",
        }
        operator_reply = (200, {"id": tenant_id, "code": "admin-web-demo", "status": "active"})
        bad_replies = [
            (401, {"error": "unauthorized"}),
            (503, {"error": "unavailable"}),
            (200, "not-an-object"),
        ]
        for reply in bad_replies:
            owner = FakeApi([reply])
            with self.subTest(reply=reply), self.assertRaises(demo.DemoError):
                demo.ensure_demo_seed(FakeApi([operator_reply]), owner, dict(base), "http://engine")
            self.assertFalse(any(method == "POST" for method, _, _ in owner.calls))

        class TimeoutApi:
            calls = []

            def call(self, method, path, body=None):
                self.calls.append((method, path, body))
                raise TimeoutError("simulated")

        timeout = TimeoutApi()
        with self.assertRaises(demo.DemoError):
            demo.ensure_demo_seed(FakeApi([operator_reply]), timeout, dict(base), "http://engine")
        self.assertFalse(any(method == "POST" for method, _, _ in timeout.calls))

    def test_seed_reuses_one_list_match_and_rejects_ambiguous_matches(self):
        demo = load_demo()
        match = {"id": "22222222-2222-4222-8222-222222222222", "kind": "project",
                 "parentId": None, "name": "客户体验研究"}
        found = FakeApi([(200, {"items": [match]})])
        self.assertEqual(match, demo._find_resource(found, "project", "客户体验研究", None))
        self.assertIn("query=%E5%AE%A2%E6%88%B7%E4%BD%93%E9%AA%8C%E7%A0%94%E7%A9%B6", found.calls[0][1])
        with self.assertRaises(demo.DemoError):
            demo._find_resource(FakeApi([(200, {"items": [match, dict(match)]})]),
                                "project", "客户体验研究", None)

    def test_atomic_private_writes_replace_and_fsync_file_and_directory(self):
        demo = load_demo()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "metadata.json")
            path.write_text("old", encoding="utf-8")
            calls = []
            original_fsync = demo.os.fsync
            with mock.patch.object(demo.os, "fsync", side_effect=lambda fd: (calls.append(fd), original_fsync(fd))[1]):
                demo._write_metadata(path, {"schemaVersion": 2, "tenantId": "tenant"})
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual("tenant", json.loads(path.read_text(encoding="utf-8"))["tenantId"])
            self.assertEqual([], [item for item in Path(directory).iterdir() if item.name.startswith(".metadata")])

    def test_atomic_write_failure_preserves_the_previous_file(self):
        demo = load_demo()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "access.json")
            path.write_text("previous", encoding="utf-8")
            with mock.patch.object(demo.os, "replace", side_effect=OSError("simulated crash")):
                with self.assertRaises(OSError):
                    demo.write_private(path, "replacement")
            self.assertEqual("previous", path.read_text(encoding="utf-8"))
            self.assertEqual([], [item for item in Path(directory).iterdir() if item.name.startswith(".access")])

    def test_bootstrap_recovers_tenant_and_engine_after_commit_before_local_write(self):
        demo = load_demo()
        tenant_id = "11111111-1111-4111-8111-111111111111"
        secret = "s" * 64
        operator = FakeApi([
            (404, {"error": "not_found"}),
            (409, {"error": "conflict"}),
            (200, {"id": tenant_id, "code": "admin-web-demo", "status": "active"}),
            (200, [{"id": "admin-web-demo-engine-01", "tenantId": tenant_id,
                    "baseUrl": "http://engine", "status": "active"}]),
            (200, {"secret": secret}),
        ])
        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory, "metadata.json")
            secret_path = Path(directory, "event-secret")
            metadata = demo._bootstrap(operator, "http://engine", metadata_path, secret_path)
            persisted = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(tenant_id, metadata["tenantId"])
        self.assertEqual("admin-web-demo-engine-01", persisted["engineInstanceId"])
        self.assertEqual(1, sum(method == "POST" and path == "/v1/platform/tenants"
                                for method, path, _ in operator.calls))

    def test_bootstrap_recovers_plan_onboarding_and_engine_conflicts(self):
        demo = load_demo()
        tenant_id = "11111111-1111-4111-8111-111111111111"
        plan_id = "22222222-2222-4222-8222-222222222222"
        engine = {"id": "admin-web-demo-engine-01", "tenantId": tenant_id,
                  "baseUrl": "http://engine", "status": "active"}
        operator = FakeApi([
            (200, {"id": tenant_id, "code": "admin-web-demo", "status": "provisioning"}),
            (404, {"error": "not_found"}),
            (409, {"error": "conflict"}),
            (200, {"id": plan_id, "planCode": "admin-web-demo", "version": 1}),
            (409, {"error": "already_onboarded"}),
            (200, {"id": tenant_id, "status": "active"}),
            (200, []),
            (409, {"error": "conflict"}),
            (200, [engine]),
            (200, {"secret": "s" * 64}),
        ])
        with tempfile.TemporaryDirectory() as directory:
            metadata_path = Path(directory, "metadata.json")
            metadata = demo._bootstrap(operator, "http://engine", metadata_path, Path(directory, "secret"))
            persisted = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertTrue(metadata["onboarded"])
        self.assertTrue(persisted["activated"])
        self.assertEqual(plan_id, persisted["planVersionId"])

    def test_private_access_file_is_0600_and_no_credential_is_printed(self):
        demo = load_demo()
        credential = "local-secret-that-must-not-leak"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "access.json")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                demo.write_access(path, {
                    "schemaVersion": 1,
                    "adminWebUrl": "http://127.0.0.1:49151",
                    "engineOperationsUrl": "http://127.0.0.1:49152",
                    "respondentUrl": "http://127.0.0.1:49152/index.php/12345",
                    "platformUsername": "admin-web-demo-owner",
                    "platformAccessToken": credential,
                    "engineUsername": "admin",
                    "enginePassword": credential,
                })

            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertNotIn(credential, output.getvalue())
            self.assertEqual(str(path), output.getvalue().strip())

    def test_local_and_ci_ssl_policy_is_explicitly_non_production(self):
        demo = load_demo()

        for environment in ("local", "demo", "ci"):
            self.assertEqual(
                {"force_ssl": "off", "ssl_disable_alert": 1},
                demo.ssl_policy(environment),
            )

    def test_production_ssl_policy_uses_probe_evidence(self):
        demo = load_demo()
        calls = []

        def fetch(url, headers):
            calls.append((url, headers))
            if url == "http://survey.example/":
                return 308, "https://survey.example/", ()
            if url == "https://survey.example":
                return 200, "", ()
            if headers.get("X-Forwarded-Proto") == "https":
                return 200, "", ("session=opaque; Path=/; Secure; HttpOnly",)
            return 302, "https://survey.example/", ()

        self.assertEqual(
            {"force_ssl": "on", "ssl_disable_alert": 0},
            demo.ssl_policy("production", "https://survey.example", "http://proxy.internal/health", fetch),
        )
        self.assertEqual(4, len(calls))

        broken = lambda url, headers: (200, "", ())
        with self.assertRaises(demo.DemoError):
            demo.ssl_policy("production", "https://survey.example", "http://proxy.internal/health", broken)
        with self.assertRaises(demo.DemoError):
            demo.ssl_policy("production", "http://survey.example", "http://proxy.internal/health", fetch)

    def test_production_ssl_probe_with_simulated_http_server(self):
        demo = load_demo()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/public-http", "/proxy") and self.headers.get("X-Forwarded-Proto") != "https":
                    self.send_response(308)
                    self.send_header("Location", "https://survey.example/")
                    self.end_headers()
                    return
                self.send_response(200)
                if self.path == "/proxy":
                    self.send_header("Set-Cookie", "session=opaque; Path=/; Secure; HttpOnly")
                self.end_headers()

            def log_message(self, format_string, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        local = "http://127.0.0.1:{}".format(server.server_port)

        def fetch(url, headers):
            if url == "http://survey.example/":
                url = local + "/public-http"
            elif url == "https://survey.example":
                url = local + "/public-https"
            return demo._http_probe(url, headers)

        try:
            self.assertEqual(
                {"force_ssl": "on", "ssl_disable_alert": 0},
                demo.ssl_policy("production", "https://survey.example", local + "/proxy", fetch),
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_ssl_cli_never_prints_ready_without_evidence(self):
        completed = subprocess.run(
            ["python3", str(MODULE_PATH), "check-production-ssl",
             "--public-url", "https://127.0.0.1:1", "--proxy-probe-url", "http://127.0.0.1:1"],
            text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertNotIn("ready", completed.stdout)

    @staticmethod
    def runtime_env_text(extra=""):
        secret = "x" * 48
        return "".join([
            "PLATFORM_JWT_HMAC_SECRET={}\n".format(secret),
            "PLATFORM_ENGINE_EVENTS_SECRET={}\n".format(secret),
            "PUBGW_SHARED_SECRET={}\n".format(secret),
            "DEMO_ENGINE_ADMIN_USER=admin\n",
            "DEMO_ENGINE_ADMIN_PASSWORD={}\n".format(secret),
            "ENGINE_DB_ROOT_PASSWORD={}\n".format(secret),
            "PLATFORM_DB_SUPERUSER_PASSWORD={}\n".format(secret),
            "PLATFORM_DB_APP_PASSWORD={}\n".format(secret),
            "PLATFORM_DB_OWNER_PASSWORD={}\n".format(secret),
            extra,
        ])

    def test_runtime_env_parser_accepts_only_allowlisted_literal_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            env_file = runtime / "runtime.env"
            env_file.write_text(self.runtime_env_text(), encoding="utf-8")
            env_file.chmod(0o600)
            script = 'source "{}"; RUNTIME_DIR="{}"; RUNTIME_ENV="{}"; load_runtime_env'.format(
                RUNNER, runtime, env_file,
            )
            valid = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
            self.assertEqual(0, valid.returncode, valid.stderr)

            for malicious in (
                    "UNKNOWN_KEY=value\n", "PLATFORM_JWT_HMAC_SECRET=duplicate\n",
                    "PLATFORM_DB_APP_PASSWORD=$(id)\n"):
                env_file.write_text(self.runtime_env_text(malicious), encoding="utf-8")
                env_file.chmod(0o600)
                completed = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
                self.assertNotEqual(0, completed.returncode, malicious)

    def test_invalid_runtime_env_stops_before_generated_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            env_file = runtime / "runtime.env"
            env_file.write_text(self.runtime_env_text("UNKNOWN_KEY=value\n"), encoding="utf-8")
            env_file.chmod(0o600)
            script = 'source "{}"; RUNTIME_DIR="{}"; RUNTIME_ENV="{}"; ENGINES_FILE="$RUNTIME_DIR/engines.json"; ensure_runtime'.format(
                RUNNER, runtime, env_file,
            )
            completed = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
            self.assertNotEqual(0, completed.returncode)
            self.assertFalse((runtime / "engines.json").exists())

    def test_invalid_command_arguments_stop_before_runtime_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory, "runtime")
            script = (
                'source "{}"; RUNTIME_DIR="{}"; RUNTIME_ENV="$RUNTIME_DIR/runtime.env"; '
                'ENGINES_FILE="$RUNTIME_DIR/engines.json"; main start unexpected'
            ).format(RUNNER, runtime)
            completed = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=False)
            self.assertNotEqual(0, completed.returncode)
            self.assertFalse(runtime.exists())

    def test_runner_syntax_and_purge_confirmation_are_non_destructive_in_tests(self):
        syntax = subprocess.run(["bash", "-n", str(RUNNER)], text=True, capture_output=True, check=False)
        self.assertEqual(0, syntax.returncode, syntax.stderr)

        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            docker = bin_dir / "docker"
            docker.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            docker.chmod(0o755)
            completed = subprocess.run(
                [str(RUNNER), "stop", "--purge"],
                input="no\n",
                text=True,
                capture_output=True,
                check=False,
                env=dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"]),
            )

        self.assertNotEqual(0, completed.returncode)
        self.assertNotIn("credential", completed.stdout + completed.stderr)
        self.assertNotEqual(99, completed.returncode, "docker must not run after purge is refused")

    def test_purge_pty_requires_the_exact_phrase_before_invoking_docker(self):
        def attempt(phrase):
            with tempfile.TemporaryDirectory() as directory:
                bin_dir = Path(directory)
                marker = bin_dir / "docker-called"
                docker = bin_dir / "docker"
                docker.write_text('#!/bin/sh\nprintf called >"{}"\nexit 99\n'.format(marker), encoding="utf-8")
                docker.chmod(0o755)
                master, slave = pty.openpty()
                process = subprocess.Popen(
                    [str(RUNNER), "stop", "--purge"], stdin=slave,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    env=dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"]),
                )
                os.close(slave)
                os.write(master, (phrase + "\n").encode("utf-8"))
                stdout, stderr = process.communicate(timeout=10)
                os.close(master)
                return process.returncode, marker.exists(), stdout + stderr

        wrong = attempt("PURGE adminweb_demo")
        self.assertNotEqual(0, wrong[0])
        self.assertFalse(wrong[1])
        correct = attempt("PURGE adminweb-demo")
        self.assertNotEqual(0, correct[0], "fake docker deliberately refuses the destructive command")
        self.assertTrue(correct[1], correct[2])

    def test_runner_re_resolves_the_random_engine_port_after_recreate(self):
        text = RUNNER.read_text(encoding="utf-8")
        recreated = text.index('up -d --force-recreate engine')
        refreshed = text.index('engine_url="$(service_url engine 80)"', recreated)
        final_seed = text.index('python3 "$SUPPORT" start', recreated)

        self.assertLess(recreated, refreshed)
        self.assertLess(refreshed, final_seed)

    def test_runner_builds_the_engine_image_only_when_it_is_missing(self):
        text = RUNNER.read_text(encoding="utf-8")

        self.assertIn("docker image inspect survey-web", text)
        self.assertIn('"${COMPOSE[@]}" build engine', text)
        self.assertIn('"${COMPOSE[@]}" build gateway admin-web', text)
        self.assertIn('"${COMPOSE[@]}" up -d gateway admin-web', text)
        self.assertIn('"${COMPOSE[@]}" up -d platform-db engine-db', text)
        self.assertIn('"${COMPOSE[@]}" up -d platform engine', text)
        self.assertNotIn('up -d --build gateway admin-web', text)

    def test_runner_never_places_database_or_engine_passwords_in_cli_arguments(self):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"-p(?:root|\$\{?ENGINE_DB_ROOT_PASSWORD)")
        self.assertNotIn('console.php install \\', text)
        self.assertIn('console.php installDemo', text)
        self.assertIn('docker exec -e MYSQL_PWD', text)

    def test_runner_installs_the_fixed_advanced_question_theme(self):
        text = RUNNER.read_text(encoding="utf-8")

        self.assertIn("platform/tests/e2e/install_question_themes.php mjy-collapsible", text)

    def test_quiet_lifecycle_commands_never_persist_secret_output(self):
        secret = "must-not-reach-terminal"
        with tempfile.TemporaryDirectory() as directory:
            script = """
                source {runner}
                RUNTIME_DIR={runtime}
                mkdir -p "$RUNTIME_DIR"
                run_quiet sh -c 'echo {secret}; echo {secret} >&2'
            """.format(runner=RUNNER, runtime=directory, secret=secret)
            completed = subprocess.run(
                ["bash", "-c", script], text=True, capture_output=True, check=False,
            )
            log = Path(directory, "lifecycle.log")

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertNotIn(secret, completed.stdout + completed.stderr)
            self.assertNotIn(secret, log.read_text(encoding="utf-8"))
            self.assertEqual(0o600, stat.S_IMODE(log.stat().st_mode))

            failed = subprocess.run(
                ["bash", "-c", script.replace("echo {0}; echo {0} >&2".format(secret),
                                               "echo {0}; echo {0} >&2; exit 7".format(secret))],
                text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertNotIn(secret, failed.stdout + failed.stderr)
            self.assertNotIn(secret, log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
