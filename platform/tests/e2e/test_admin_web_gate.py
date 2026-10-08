import contextlib
import base64
import importlib.util
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("admin_web_gate.py")


def load_gate():
    spec = importlib.util.spec_from_file_location("admin_web_gate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def decode_claims(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def run_bash(script, env=None):
    return subprocess.run(
        ["bash", "-c", script],
        text=True,
        capture_output=True,
        check=False,
        env=dict(os.environ, **(env or {})),
    )


class AdminWebGateTest(unittest.TestCase):
    def test_browser_token_is_valid_for_exactly_ten_minutes(self):
        gate = load_gate()
        claims = decode_claims(gate.mint_token(
            "x" * 48,
            "admin-web-e2e-owner",
            "11111111-1111-4111-8111-111111111111",
            [],
        ))

        self.assertEqual(600, claims["exp"] - claims["iat"])

    def test_cli_splits_seed_from_late_browser_token_issuance(self):
        gate = load_gate()
        seed = gate.parse_args([
            "--base-url", "http://127.0.0.1:18080",
            "prepare",
            "--instance", "admin-web-engine-01",
            "--engine-base-url", "http://test-web",
            "--metadata-file", "/tmp/metadata.json",
            "--event-secret-file", "/tmp/event-secret",
        ])
        issue = gate.parse_args([
            "--base-url", "http://127.0.0.1:18080",
            "issue-browser-token",
            "--metadata-file", "/tmp/metadata.json",
            "--jwt-file", "/tmp/owner.jwt",
        ])

        self.assertEqual("prepare", seed.command)
        self.assertEqual("issue-browser-token", issue.command)
        self.assertFalse(hasattr(seed, "jwt_file"))

    def test_private_files_remain_0600_when_overwriting_an_existing_file(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "jwt")
            path.write_text("old", encoding="utf-8")
            path.chmod(0o644)

            gate.write_private(path, "header.payload.signature")

            self.assertEqual("header.payload.signature", path.read_text(encoding="utf-8"))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_issue_browser_token_never_prints_the_jwt(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            jwt_path = Path(directory, "owner.jwt")
            metadata_path = Path(directory, "metadata.json")
            metadata_path.write_text(json.dumps({
                "schemaVersion": 1,
                "tenantId": "11111111-1111-4111-8111-111111111111",
                "actorId": "admin-web-e2e-owner",
                "engineInstanceId": "admin-web-engine-01",
            }), encoding="utf-8")
            output = io.StringIO()

            with mock.patch.dict(os.environ, {"PLATFORM_JWT_HMAC_SECRET": "x" * 48}), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                gate.cmd_issue_browser_token(Namespace(
                    metadata_file=str(metadata_path),
                    jwt_file=str(jwt_path),
                ))

            token = jwt_path.read_text(encoding="utf-8")
            self.assertNotIn(token, output.getvalue())
            self.assertNotIn(token, metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(0o600, stat.S_IMODE(jwt_path.stat().st_mode))
            claims = decode_claims(token)
            self.assertEqual(600, claims["exp"] - claims["iat"])

    def test_result_schema_rejects_missing_or_unknown_fields(self):
        gate = load_gate()
        valid = {
            "surveyId": "22222222-2222-4222-8222-222222222222",
            "tenantId": "11111111-1111-4111-8111-111111111111",
            "version": 1,
            "network": [{"method": "GET", "status": 200, "url": "http://127.0.0.1/v1/me"}],
        }

        self.assertEqual(valid, gate.validate_result(valid))
        with self.assertRaises(gate.StepFailed):
            gate.validate_result({key: value for key, value in valid.items() if key != "network"})
        with self.assertRaises(gate.StepFailed):
            gate.validate_result(dict(valid, jwt="must-not-be-accepted"))

    def test_verify_evidence_rejects_a_platform_or_engine_mismatch(self):
        gate = load_gate()
        result = gate.validate_result({
            "surveyId": "22222222-2222-4222-8222-222222222222",
            "tenantId": "11111111-1111-4111-8111-111111111111",
            "version": 1,
            "network": [{"method": "POST", "status": 200, "url": "http://127.0.0.1/v1/publish"}],
        })
        metadata = {
            "schemaVersion": 1,
            "tenantId": "11111111-1111-4111-8111-111111111111",
            "actorId": "admin-web-e2e-owner",
            "engineInstanceId": "admin-web-engine-01",
        }
        api = {
            "survey": {"id": result["surveyId"], "status": "published", "publishedVersion": 1},
            "versions": [{
                "version": 1,
                "engineInstanceId": metadata["engineInstanceId"],
                "engineSid": 12345,
                "live": True,
                "fingerprint": "fm1:abc",
            }],
            "route": {
                "publicId": result["surveyId"],
                "tenantId": metadata["tenantId"],
                "engineInstanceId": metadata["engineInstanceId"],
                "engineSid": 12345,
            },
        }
        platform_db = {
            "status": "published",
            "publishedVersion": 1,
            "engineInstanceId": metadata["engineInstanceId"],
            "engineSid": 12345,
            "liveVersionCount": 1,
        }
        engine_db = {"active": "Y", "surveyCount": 1}

        gate.validate_evidence(metadata, result, api, platform_db, engine_db, gateway_publish_calls=1)

        with self.assertRaises(gate.StepFailed):
            gate.validate_evidence(
                metadata,
                result,
                api,
                dict(platform_db, publishedVersion=2),
                engine_db,
                gateway_publish_calls=1,
            )
        with self.assertRaises(gate.StepFailed):
            gate.validate_evidence(
                metadata,
                result,
                api,
                platform_db,
                dict(engine_db, active="N"),
                gateway_publish_calls=1,
            )

    def test_compose_uses_an_isolated_database_and_loopback_random_ports(self):
        compose = Path(__file__).parents[2] / "deploy/test/admin-web-e2e.compose.yml"
        text = compose.read_text(encoding="utf-8")

        self.assertIn("admin-web-platform-db:", text)
        self.assertIn("postgres:16-alpine", text)
        self.assertIn("tmpfs:", text)
        self.assertNotIn("platform-db_default", text)
        self.assertGreaterEqual(text.count('127.0.0.1::8080'), 2)
        self.assertIn('      - "127.0.0.1::80"\n', text)
        for service in ("platform:", "gateway:", "admin-web:"):
            self.assertIn(service, text)

    def test_runner_exports_the_browser_contract_and_cleans_its_compose_project(self):
        runner = Path(__file__).parents[2] / "deploy/test/run-admin-web-e2e.sh"
        text = runner.read_text(encoding="utf-8")

        for name in (
            "ADMIN_WEB_BASE_URL",
            "ADMIN_WEB_JWT_FILE",
            "ADMIN_WEB_METADATA_FILE",
            "ADMIN_WEB_RESULT_FILE",
        ):
            self.assertIn("export " + name, text)
        self.assertIn("--project=chromium-desktop", text)
        self.assertIn("--project=chromium-mobile", text)
        self.assertIn('"$GATE"', text)
        self.assertIn("verify", text)
        self.assertIn('down -v --remove-orphans', text)
        self.assertNotIn("source \"$REPO_ROOT/platform/deploy/test/run-p1-e2e.sh\"", text)
        self.assertIn('NODE22_BIN', text)
        self.assertIn('v22*/bin', text)

        syntax = subprocess.run(
            ["bash", "-n", str(runner)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, syntax.returncode, syntax.stderr)

    def test_gateway_publish_counter_only_counts_publish_requests(self):
        gate = load_gate()
        logs = "\n".join((
            'INFO: 127.0.0.1 - "GET /healthz HTTP/1.1" 200 OK',
            'INFO: 172.0.0.2 - "POST /v1/publish HTTP/1.1" 200 OK',
            'INFO: 172.0.0.2 - "POST /v1/close HTTP/1.1" 200 OK',
        ))

        self.assertEqual(1, gate.count_gateway_publish_calls(logs))

    def test_sensitive_artifact_scan_deletes_matches_without_printing_token(self):
        gate = load_gate()
        token = "header.payload.sensitive-signature"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            result = root / "result.json"
            test_results = root / "test-results"
            screenshot = test_results / "failure.png"
            harmless = test_results / "notes.txt"
            gate.write_private(secret, token)
            test_results.mkdir()
            result.write_text('{"token":"' + token + '"}', encoding="utf-8")
            screenshot.write_bytes(b"png-prefix" + token.encode("ascii"))
            harmless.write_text("no credential here", encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(gate.StepFailed):
                    gate.scrub_sensitive_artifacts(secret, [result, test_results], remove_media=False)

            self.assertNotIn(token, output.getvalue())
            self.assertFalse(result.exists())
            self.assertFalse(screenshot.exists())
            self.assertTrue(harmless.exists())

    def test_failure_scrub_removes_media_even_when_token_is_only_visual(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            test_results = root / "test-results"
            screenshot = test_results / "failure.png"
            text_log = test_results / "network.txt"
            gate.write_private(secret, "header.payload.signature")
            test_results.mkdir()
            screenshot.write_bytes(b"opaque-png-with-rendered-pixels")
            text_log.write_text("GET /v1/me 401", encoding="utf-8")

            gate.scrub_sensitive_artifacts(secret, [test_results], remove_media=True)

            self.assertFalse(screenshot.exists())
            self.assertTrue(text_log.exists())

    def test_keep_mode_still_removes_private_work_directory(self):
        runner = Path(__file__).parents[2] / "deploy/test/run-admin-web-e2e.sh"
        with tempfile.TemporaryDirectory() as directory:
            private_dir = Path(directory, "private")
            private_dir.mkdir()
            (private_dir / "owner.jwt").write_text("secret", encoding="utf-8")
            script = """
source "$RUNNER"
WORK_DIR="$PRIVATE_DIR"
ADMIN_WEB_E2E_KEEP=1
COMPOSE=(true)
docker() { return 0; }
cleanup_run 1
test ! -e "$PRIVATE_DIR"
"""
            completed = run_bash(script, {
                "RUNNER": str(runner),
                "PRIVATE_DIR": str(private_dir),
            })

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertNotIn("secret", completed.stdout + completed.stderr)

    def test_browser_token_is_issued_immediately_before_playwright(self):
        runner = Path(__file__).parents[2] / "deploy/test/run-admin-web-e2e.sh"
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory, "trace")
            jwt = Path(directory, "owner.jwt")
            result = Path(directory, "result.json")
            app = Path(directory, "app")
            app.mkdir()
            script = """
source "$RUNNER"
ADMIN_WEB_JWT_FILE="$JWT"
ADMIN_WEB_RESULT_FILE="$RESULT"
ADMIN_WEB_DIR="$APP"
issue_browser_token() { echo issue >>"$TRACE"; printf token >"$JWT"; chmod 600 "$JWT"; }
run_playwright() { echo playwright >>"$TRACE"; printf '{}' >"$RESULT"; }
sanitize_test_artifacts() { return 0; }
run_browser_tests
"""
            completed = run_bash(script, {
                "RUNNER": str(runner),
                "TRACE": str(trace),
                "JWT": str(jwt),
                "RESULT": str(result),
                "APP": str(app),
            })

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(["issue", "playwright"], trace.read_text(encoding="utf-8").splitlines())

    def test_main_issues_browser_token_after_cold_stack_is_ready(self):
        runner = Path(__file__).parents[2] / "deploy/test/run-admin-web-e2e.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "trace"
            jar = root / "business.jar"
            jar.touch()
            script = r'''
source "$RUNNER"
TRACE="$TRACE_FILE"
PLATFORM_JAR="$JAR"
COMPOSE=(docker)
ensure_node22() { :; }
command() { return 0; }
docker() { echo "docker $*" >>"$TRACE"; return 0; }
wait_healthy() { echo "healthy $1" >>"$TRACE"; }
service_url() { printf 'http://127.0.0.1:12345\n'; }
random_secret() { printf 'forty-eight-byte-test-secret-material-xxxxxxxxxxxx\n'; }
prepare_test_stack() { echo engine-installed >>"$TRACE"; }
enable_remote_control() { :; }
db_query() { :; }
python3() {
  case " $* " in
    *" prepare "*)
      echo seed >>"$TRACE"
      printf event-secret >"$EVENT_SECRET_FILE"
      ;;
    *" verify "*) echo verify >>"$TRACE" ;;
  esac
}
issue_browser_token() {
  echo issue-token >>"$TRACE"
  printf browser-token >"$ADMIN_WEB_JWT_FILE"
  chmod 600 "$ADMIN_WEB_JWT_FILE"
}
run_playwright() {
  echo playwright >>"$TRACE"
  printf '{}' >"$ADMIN_WEB_RESULT_FILE"
}
sanitize_test_artifacts() { return 0; }
main --fresh
'''
            completed = run_bash(script, {
                "RUNNER": str(runner),
                "TRACE_FILE": str(trace),
                "JAR": str(jar),
            })

            self.assertEqual(0, completed.returncode, completed.stderr)
            events = trace.read_text(encoding="utf-8").splitlines()
            engine_index = events.index("engine-installed")
            build_index = next(i for i, event in enumerate(events) if "up -d --build gateway admin-web" in event)
            issue_index = events.index("issue-token")
            playwright_index = events.index("playwright")
            self.assertLess(engine_index, issue_index)
            self.assertLess(build_index, issue_index)
            self.assertEqual(issue_index + 1, playwright_index)

    def test_playwright_failure_scrubs_sensitive_artifacts_without_printing_token(self):
        runner = Path(__file__).parents[2] / "deploy/test/run-admin-web-e2e.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            results = app / "test-results"
            results.mkdir(parents=True)
            jwt = root / "owner.jwt"
            result = root / "result.json"
            token = "header.payload.never-print-this-signature"
            script = r'''
source "$RUNNER"
ADMIN_WEB_DIR="$APP"
ADMIN_WEB_JWT_FILE="$JWT"
ADMIN_WEB_RESULT_FILE="$RESULT"
issue_browser_token() { printf '%s' "$TOKEN" >"$JWT"; chmod 600 "$JWT"; }
run_playwright() {
  printf '%s' "$TOKEN" >"$RESULT"
  printf '%s' "$TOKEN" >"$APP/test-results/leak.txt"
  printf 'opaque screenshot pixels' >"$APP/test-results/failure.png"
  return 1
}
run_browser_tests
'''
            completed = run_bash(script, {
                "RUNNER": str(runner),
                "APP": str(app),
                "JWT": str(jwt),
                "RESULT": str(result),
                "TOKEN": token,
            })

            self.assertNotEqual(0, completed.returncode)
            self.assertNotIn(token, completed.stdout + completed.stderr)
            self.assertFalse(result.exists())
            self.assertFalse((results / "leak.txt").exists())
            self.assertFalse((results / "failure.png").exists())


if __name__ == "__main__":
    unittest.main()
