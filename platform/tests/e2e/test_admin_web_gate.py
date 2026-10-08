import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("admin_web_gate.py")


def load_gate():
    spec = importlib.util.spec_from_file_location("admin_web_gate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AdminWebGateTest(unittest.TestCase):
    def test_private_files_remain_0600_when_overwriting_an_existing_file(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "jwt")
            path.write_text("old", encoding="utf-8")
            path.chmod(0o644)

            gate.write_private(path, "header.payload.signature")

            self.assertEqual("header.payload.signature", path.read_text(encoding="utf-8"))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_prepare_artifacts_never_print_the_jwt(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            jwt_path = Path(directory, "owner.jwt")
            metadata_path = Path(directory, "metadata.json")
            token = "header.payload.sensitive-signature"
            output = io.StringIO()

            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                gate.write_prepare_artifacts(
                    jwt_path,
                    metadata_path,
                    token,
                    {
                        "schemaVersion": 1,
                        "tenantId": "11111111-1111-4111-8111-111111111111",
                        "ownerActor": "admin-web-e2e-owner",
                        "engineInstanceId": "admin-web-engine-01",
                    },
                )

            self.assertNotIn(token, output.getvalue())
            self.assertNotIn(token, metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(0o600, stat.S_IMODE(jwt_path.stat().st_mode))

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


if __name__ == "__main__":
    unittest.main()
