import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import tempfile
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
    def test_cli_accepts_only_the_four_lifecycle_commands(self):
        demo = load_demo()

        for command in ("start", "stop", "status", "refresh-token"):
            self.assertEqual(command, demo.parse_args([command]).command)
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
            (201, {"id": new_folder_id, "kind": "folder", "parentId": project_id, "name": "2026 Q4"}),
            (201, {"id": new_survey_id, "title": "品牌跟踪调查", "status": "draft"}),
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
            (201, {"id": folder_id, "kind": "folder", "parentId": project_id, "name": "2026 Q4"}),
            (201, {"id": survey_id, "title": "品牌跟踪调查", "status": "draft"}),
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

    def test_production_ssl_policy_requires_all_https_proxy_checks(self):
        demo = load_demo()

        for missing in ("https_redirect", "proxy_headers", "secure_cookie"):
            checks = {"https_redirect": True, "proxy_headers": True, "secure_cookie": True}
            checks[missing] = False
            with self.assertRaises(demo.DemoError):
                demo.ssl_policy("production", public_url="https://survey.example", **checks)
        with self.assertRaises(demo.DemoError):
            demo.ssl_policy(
                "production", public_url="http://survey.example",
                https_redirect=True, proxy_headers=True, secure_cookie=True,
            )
        self.assertEqual(
            {"force_ssl": "on", "ssl_disable_alert": 0},
            demo.ssl_policy(
                "production", public_url="https://survey.example",
                https_redirect=True, proxy_headers=True, secure_cookie=True,
            ),
        )

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
        first_up = next(line for line in text.splitlines() if 'up -d platform-db platform engine-db engine' in line)
        self.assertNotIn("--build", first_up)
        self.assertNotIn('up -d --build gateway admin-web', text)

    def test_runner_installs_the_fixed_advanced_question_theme(self):
        text = RUNNER.read_text(encoding="utf-8")

        self.assertIn("platform/tests/e2e/install_question_themes.php mjy-collapsible", text)

    def test_quiet_lifecycle_commands_keep_output_in_a_0600_private_log(self):
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
            self.assertIn(secret, log.read_text(encoding="utf-8"))
            self.assertEqual(0o600, stat.S_IMODE(log.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
