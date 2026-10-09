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
    def test_local_engine_configs_disable_ssl_enforcement_and_alerts(self):
        config_dir = Path(__file__).parents[2] / "deploy/test"
        for name in ("config.mysql.php", "config.pgsql.php"):
            text = (config_dir / name).read_text(encoding="utf-8")
            self.assertIn("'force_ssl' => 'off'", text)
            self.assertIn("'ssl_disable_alert' => 1", text)

    def test_runner_clears_settings_cache_after_preparing_the_engine(self):
        runner = Path(__file__).parents[2] / "deploy/test/run-admin-web-e2e.sh"
        text = runner.read_text(encoding="utf-8")
        prepare = text.index("prepare_test_stack")
        clear = text.index("tmp/runtime/cache", prepare)
        enable = text.index("enable_remote_control", prepare)
        self.assertLess(prepare, clear)
        self.assertLess(clear, enable)

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
            "ADMIN_WEB_TEST_RESULTS_DIR",
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

    def test_artifact_scan_fails_when_a_non_media_file_cannot_be_read(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            blocked = root / "blocked.txt"
            gate.write_private(secret, "header.payload.signature")
            blocked.write_text("network metadata", encoding="utf-8")
            original_read_bytes = Path.read_bytes

            def read_bytes(path):
                if path == blocked:
                    raise PermissionError("simulated unreadable artifact")
                return original_read_bytes(path)

            with mock.patch.object(Path, "read_bytes", read_bytes):
                with self.assertRaises(gate.StepFailed):
                    gate.scrub_sensitive_artifacts(secret, [blocked], remove_media=False)

            self.assertTrue(blocked.exists())

    def test_artifact_scan_fails_when_a_nested_directory_cannot_be_opened(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            artifacts = root / "test-results"
            blocked = artifacts / "blocked"
            gate.write_private(secret, "header.payload.signature")
            blocked.mkdir(parents=True)
            real_scandir = os.scandir

            def scandir(path):
                if Path(path) == blocked:
                    raise PermissionError("simulated unreadable directory")
                return real_scandir(path)

            with mock.patch.object(gate.os, "scandir", side_effect=scandir):
                with self.assertRaises(gate.StepFailed):
                    gate.scrub_sensitive_artifacts(secret, [artifacts], remove_media=False)

    def test_artifact_scan_fails_when_directory_iteration_is_interrupted(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            artifacts = root / "test-results"
            gate.write_private(secret, "header.payload.signature")
            artifacts.mkdir()
            real_scandir = os.scandir

            class InterruptedScandir:
                def __enter__(self):
                    def entries():
                        if False:
                            yield None
                        raise PermissionError("simulated traversal interruption")

                    return entries()

                def __exit__(self, *_args):
                    return False

            def scandir(path):
                if Path(path) == artifacts:
                    return InterruptedScandir()
                return real_scandir(path)

            with mock.patch.object(gate.os, "scandir", side_effect=scandir):
                with self.assertRaises(gate.StepFailed):
                    gate.scrub_sensitive_artifacts(secret, [artifacts], remove_media=False)

    def test_artifact_scan_fails_when_an_entry_cannot_be_statted(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            artifacts = root / "test-results"
            artifact = artifacts / "network.txt"
            gate.write_private(secret, "header.payload.signature")
            artifacts.mkdir()
            artifact.write_text("redacted network metadata", encoding="utf-8")
            real_scandir = os.scandir

            class EntryWithBrokenStat:
                def __init__(self, entry):
                    self._entry = entry
                    self.name = entry.name
                    self.path = entry.path

                def is_dir(self, *, follow_symlinks=True):
                    return self._entry.is_dir(follow_symlinks=follow_symlinks)

                def stat(self, *, follow_symlinks=True):
                    raise PermissionError("simulated entry stat failure")

            class WrappedScandir:
                def __init__(self, scanner):
                    self._scanner = scanner

                def __enter__(self):
                    return (EntryWithBrokenStat(entry) for entry in self._scanner)

                def __exit__(self, *args):
                    return self._scanner.__exit__(*args)

            def scandir(path):
                scanner = real_scandir(path)
                if Path(path) == artifacts:
                    return WrappedScandir(scanner)
                return scanner

            with mock.patch.object(gate.os, "scandir", side_effect=scandir):
                with self.assertRaises(gate.StepFailed):
                    gate.scrub_sensitive_artifacts(secret, [artifacts], remove_media=False)

    def test_artifact_scan_finds_a_secret_in_a_nested_directory(self):
        gate = load_gate()
        token = "header.payload.nested-signature"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            artifacts = root / "test-results"
            leak = artifacts / "desktop" / "trace" / "network.txt"
            safe = artifacts / "mobile" / "summary.txt"
            gate.write_private(secret, token)
            leak.parent.mkdir(parents=True)
            safe.parent.mkdir(parents=True)
            leak.write_text("Authorization: Bearer " + token, encoding="utf-8")
            safe.write_text("redacted", encoding="utf-8")

            with self.assertRaises(gate.StepFailed):
                gate.scrub_sensitive_artifacts(secret, [artifacts], remove_media=False)

            self.assertFalse(leak.exists())
            self.assertTrue(safe.exists())

    def test_failure_scrub_fails_when_media_cannot_be_deleted(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            screenshot = root / "failure.png"
            gate.write_private(secret, "header.payload.signature")
            screenshot.write_bytes(b"opaque screenshot pixels")
            original_unlink = Path.unlink

            def unlink(path, *args, **kwargs):
                if path == screenshot:
                    raise PermissionError("simulated deletion failure")
                return original_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", unlink):
                with self.assertRaises(gate.StepFailed):
                    gate.scrub_sensitive_artifacts(secret, [screenshot], remove_media=True)

            self.assertTrue(screenshot.exists())

    def test_failure_scrub_deletes_media_before_attempting_to_read_it(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            screenshot = root / "failure.png"
            gate.write_private(secret, "header.payload.signature")
            screenshot.write_bytes(b"opaque screenshot pixels")
            original_read_bytes = Path.read_bytes

            def read_bytes(path):
                if path == screenshot:
                    raise PermissionError("media must be deleted before reading")
                return original_read_bytes(path)

            with mock.patch.object(Path, "read_bytes", read_bytes):
                gate.scrub_sensitive_artifacts(secret, [screenshot], remove_media=True)

            self.assertFalse(screenshot.exists())

    def test_export_copies_only_allowlisted_sanitized_failure_evidence(self):
        gate = load_gate()
        token = "header.payload.never-export-this-signature"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            source = root / "private-results"
            case = source / "authoring-failed"
            allowed_root = root / "app" / "test-results"
            destination = allowed_root / "ci-artifacts"
            gate.write_private(secret, token)
            case.mkdir(parents=True)
            destination.mkdir(parents=True)
            (destination / "stale.txt").write_text("old run", encoding="utf-8")
            (case / "sanitized-failure.png").write_bytes(b"\x89PNG\r\n\x1a\nsafe-pixels")
            (case / "sanitized-trace-summary.json").write_text(json.dumps({
                "schemaVersion": 1,
                "kind": "sanitized-playwright-trace-summary",
                "project": "chromium-desktop",
                "testId": "desktop-authoring",
                "status": "failed",
                "durationMs": 123,
                "lastPath": "/surveys/00000000-0000-4000-8000-000000000000/edit",
                "network": [],
            }), encoding="utf-8")
            (case / "trace.zip").write_bytes(b"raw playwright trace")
            (case / "raw-failure.png").write_bytes(b"\x89PNG\r\n\x1a\nraw")
            (case / "network.txt").write_text("Bearer " + token, encoding="utf-8")

            exported = gate.export_sanitized_failure_evidence(
                secret, source, destination, allowed_root
            )

            self.assertEqual(2, exported)
            self.assertEqual({
                "chromium-desktop-desktop-authoring-sanitized-failure.png",
                "chromium-desktop-desktop-authoring-sanitized-trace-summary.json",
            }, {path.name for path in destination.iterdir()})
            for artifact in destination.iterdir():
                self.assertNotIn(token.encode("ascii"), artifact.read_bytes())

    def test_export_preserves_a_bounded_sanitized_network_sequence(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            source = root / "private-results"
            allowed_root = root / "app" / "test-results"
            destination = allowed_root / "ci-artifacts"
            gate.write_private(secret, "header.payload.signature")
            source.mkdir()
            network = [
                {"method": "GET", "status": 200, "url": "http://127.0.0.1:4173/v1/me"},
                {"method": "POST", "status": 503, "url": "http://127.0.0.1:4173/v1/surveys"},
            ]
            (source / "sanitized-trace-summary.json").write_text(json.dumps({
                "schemaVersion": 1,
                "kind": "sanitized-playwright-trace-summary",
                "project": "chromium-desktop",
                "testId": "desktop-authoring",
                "status": "failed",
                "durationMs": 123,
                "lastPath": "/workspace",
                "network": network,
            }), encoding="utf-8")

            exported = gate.export_sanitized_failure_evidence(
                secret, source, destination, allowed_root
            )

            self.assertEqual(1, exported)
            summary = json.loads(next(destination.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(network, summary["network"])

    def test_export_rejects_unsafe_network_sequences_and_clears_old_output(self):
        gate = load_gate()
        token = "Header.Payload.Signature"
        safe_event = {"method": "GET", "status": 200, "url": "http://127.0.0.1:4173/v1/me"}
        unsafe_sequences = {
            "unknown field": [{**safe_event, "headers": {"authorization": "withheld"}}],
            "query": [{**safe_event, "url": "http://127.0.0.1:4173/v1/me?debug=true"}],
            "encoded secret": [{
                **safe_event,
                "url": "http://127.0.0.1:4173/v1/%48%65%61%64%65%72%2e%50%61%79%6c%6f%61%64%2e%53%69%67%6e%61%74%75%72%65",
            }],
            "too many": [safe_event] * 26,
            "url too long": [{**safe_event, "url": "http://127.0.0.1:4173/v1/" + "a" * 800}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            source = root / "private-results"
            allowed_root = root / "app" / "test-results"
            destination = allowed_root / "ci-artifacts"
            gate.write_private(secret, token)
            source.mkdir()

            for label, network in unsafe_sequences.items():
                with self.subTest(label=label):
                    destination.mkdir(parents=True, exist_ok=True)
                    (destination / "stale.txt").write_text("old run", encoding="utf-8")
                    (source / "sanitized-trace-summary.json").write_text(json.dumps({
                        "schemaVersion": 1,
                        "kind": "sanitized-playwright-trace-summary",
                        "project": "chromium-desktop",
                        "testId": "desktop-authoring",
                        "status": "failed",
                        "durationMs": 123,
                        "lastPath": "/workspace",
                        "network": network,
                    }), encoding="utf-8")

                    with self.assertRaises(gate.StepFailed):
                        gate.export_sanitized_failure_evidence(
                            secret, source, destination, allowed_root
                        )

                    self.assertFalse(destination.exists())

    def test_export_rejects_unknown_trace_fields_and_leaves_no_stale_output(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            source = root / "private-results"
            allowed_root = root / "app" / "test-results"
            destination = allowed_root / "ci-artifacts"
            gate.write_private(secret, "header.payload.signature")
            source.mkdir()
            destination.mkdir(parents=True)
            (destination / "stale.txt").write_text("old run", encoding="utf-8")
            (source / "sanitized-trace-summary.json").write_text(json.dumps({
                "schemaVersion": 1,
                "kind": "sanitized-playwright-trace-summary",
                "project": "chromium-desktop",
                "testId": "desktop-authoring",
                "status": "failed",
                "durationMs": 123,
                "lastPath": "/workspace",
                "network": [],
                "jwt": "must be rejected",
            }), encoding="utf-8")

            with self.assertRaises(gate.StepFailed):
                gate.export_sanitized_failure_evidence(
                    secret, source, destination, allowed_root
                )

            self.assertFalse(destination.exists())

    def test_export_rejects_encoded_or_normalized_credentials_and_clears_output(self):
        gate = load_gate()
        token = "Header.Payload.Signature"
        escaped = "".join("\\u{:04x}".format(ord(character)) for character in token)
        encoded = "%48%65%61%64%65%72%2e%50%61%79%6c%6f%61%64%2e%53%69%67%6e%61%74%75%72%65"
        fullwidth = "Ｈｅａｄｅｒ．Ｐａｙｌｏａｄ．Ｓｉｇｎａｔｕｒｅ"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            source = root / "private-results"
            allowed_root = root / "app" / "test-results"
            destination = allowed_root / "ci-artifacts"
            gate.write_private(secret, token)
            source.mkdir()

            payloads = (
                '{"schemaVersion":1,"kind":"sanitized-playwright-trace-summary",'
                '"project":"chromium-desktop","testId":"desktop-authoring",'
                '"status":"failed","durationMs":1,"lastPath":"/workspace/' + escaped + '","network":[]}',
                json.dumps({
                    "schemaVersion": 1,
                    "kind": "sanitized-playwright-trace-summary",
                    "project": "chromium-desktop",
                    "testId": "desktop-authoring",
                    "status": "failed",
                    "durationMs": 1,
                    "lastPath": "/workspace/" + encoded,
                    "network": [],
                }),
                json.dumps({
                    "schemaVersion": 1,
                    "kind": "sanitized-playwright-trace-summary",
                    "project": "chromium-desktop",
                    "testId": "desktop-authoring",
                    "status": "failed",
                    "durationMs": 1,
                    "lastPath": "/workspace/" + fullwidth,
                    "network": [],
                }, ensure_ascii=False),
            )
            for serialized in payloads:
                with self.subTest(serialized=serialized[-80:]):
                    destination.mkdir(parents=True, exist_ok=True)
                    (destination / "stale.txt").write_text("old run", encoding="utf-8")
                    (source / "sanitized-trace-summary.json").write_text(
                        serialized, encoding="utf-8"
                    )

                    with self.assertRaises(gate.StepFailed):
                        gate.export_sanitized_failure_evidence(
                            secret, source, destination, allowed_root
                        )

                    self.assertFalse(destination.exists())

    def test_export_accepts_only_real_admin_web_routes(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            source = root / "private-results"
            allowed_root = root / "app" / "test-results"
            destination = allowed_root / "ci-artifacts"
            gate.write_private(secret, "header.payload.signature")
            source.mkdir()
            invalid_paths = (
                "/workspace?token=hidden",
                "/workspace#hidden",
                "/workspace/%2e%2e/dev/token",
                "/workspace/../dev/token",
                "/not-an-admin-route",
            )
            for path in invalid_paths:
                with self.subTest(path=path):
                    destination.mkdir(parents=True, exist_ok=True)
                    (destination / "stale.txt").write_text("old run", encoding="utf-8")
                    (source / "sanitized-trace-summary.json").write_text(json.dumps({
                        "schemaVersion": 1,
                        "kind": "sanitized-playwright-trace-summary",
                        "project": "chromium-desktop",
                        "testId": "desktop-authoring",
                        "status": "failed",
                        "durationMs": 1,
                        "lastPath": path,
                        "network": [],
                    }), encoding="utf-8")

                    with self.assertRaises(gate.StepFailed):
                        gate.export_sanitized_failure_evidence(
                            secret, source, destination, allowed_root
                        )

                    self.assertFalse(destination.exists())

    def test_export_rejects_a_destination_outside_the_ignored_results_root(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "owner.jwt"
            source = root / "private-results"
            allowed_root = root / "app" / "test-results"
            gate.write_private(secret, "header.payload.signature")
            source.mkdir()

            with self.assertRaises(gate.StepFailed):
                gate.export_sanitized_failure_evidence(
                    secret, source, root / "outside", allowed_root
                )

    def test_runner_scans_only_its_private_results_directory(self):
        runner = Path(__file__).parents[2] / "deploy/test/run-admin-web-e2e.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            shared = app / "test-results"
            private = root / "private-results"
            shared.mkdir(parents=True)
            private.mkdir()
            jwt = root / "owner.jwt"
            result = root / "result.json"
            token = "header.payload.parallel-isolation-signature"
            jwt.write_text(token, encoding="utf-8")
            shared_leak = shared / "other-run.txt"
            private_leak = private / "this-run.txt"
            shared_leak.write_text(token, encoding="utf-8")
            private_leak.write_text(token, encoding="utf-8")
            script = r'''
source "$RUNNER"
ADMIN_WEB_DIR="$APP"
ADMIN_WEB_JWT_FILE="$JWT"
ADMIN_WEB_RESULT_FILE="$RESULT"
ADMIN_WEB_TEST_RESULTS_DIR="$PRIVATE_RESULTS"
sanitize_test_artifacts false
'''
            completed = run_bash(script, {
                "RUNNER": str(runner),
                "APP": str(app),
                "JWT": str(jwt),
                "RESULT": str(result),
                "PRIVATE_RESULTS": str(private),
            })

            self.assertNotEqual(0, completed.returncode)
            self.assertFalse(private_leak.exists())
            self.assertTrue(shared_leak.exists())
            self.assertNotIn(token, completed.stdout + completed.stderr)

    def test_playwright_failure_exports_only_sanitized_ci_evidence(self):
        runner = Path(__file__).parents[2] / "deploy/test/run-admin-web-e2e.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "app"
            private_results = root / "private-results"
            artifact_dir = app / "test-results" / "ci-artifacts"
            jwt = root / "owner.jwt"
            result = root / "result.json"
            token = "header.payload.never-upload-this-signature"
            app.mkdir()
            script = r'''
source "$RUNNER"
ADMIN_WEB_DIR="$APP"
ADMIN_WEB_JWT_FILE="$JWT"
ADMIN_WEB_RESULT_FILE="$RESULT"
ADMIN_WEB_TEST_RESULTS_DIR="$PRIVATE_RESULTS"
ADMIN_WEB_CI_ARTIFACT_DIR="$ARTIFACT_DIR"
issue_browser_token() { printf '%s' "$TOKEN" >"$JWT"; chmod 600 "$JWT"; }
run_playwright() {
  mkdir -p "$PRIVATE_RESULTS/case"
  printf '\211PNG\r\n\032\nsafe-pixels' >"$PRIVATE_RESULTS/case/sanitized-failure.png"
  printf '%s\n' '{"schemaVersion":1,"kind":"sanitized-playwright-trace-summary","project":"chromium-desktop","testId":"desktop-authoring","status":"failed","durationMs":25,"lastPath":"/workspace","network":[]}' >"$PRIVATE_RESULTS/case/sanitized-trace-summary.json"
  printf 'raw trace' >"$PRIVATE_RESULTS/case/trace.zip"
  printf '%s' "$TOKEN" >"$PRIVATE_RESULTS/case/leak.txt"
  return 1
}
run_browser_tests
'''
            completed = run_bash(script, {
                "RUNNER": str(runner),
                "APP": str(app),
                "JWT": str(jwt),
                "RESULT": str(result),
                "PRIVATE_RESULTS": str(private_results),
                "ARTIFACT_DIR": str(artifact_dir),
                "TOKEN": token,
            })

            self.assertNotEqual(0, completed.returncode)
            self.assertNotIn(token, completed.stdout + completed.stderr)
            self.assertEqual({
                "chromium-desktop-desktop-authoring-sanitized-failure.png",
                "chromium-desktop-desktop-authoring-sanitized-trace-summary.json",
            }, {path.name for path in artifact_dir.iterdir()})
            self.assertFalse((private_results / "case" / "trace.zip").exists())
            self.assertFalse((private_results / "case" / "leak.txt").exists())

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
            test_results = Path(directory, "test-results")
            app = Path(directory, "app")
            app.mkdir()
            script = """
source "$RUNNER"
ADMIN_WEB_JWT_FILE="$JWT"
ADMIN_WEB_RESULT_FILE="$RESULT"
ADMIN_WEB_TEST_RESULTS_DIR="$TEST_RESULTS"
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
                "TEST_RESULTS": str(test_results),
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
            results = root / "private-results"
            results.mkdir(parents=True)
            jwt = root / "owner.jwt"
            result = root / "result.json"
            token = "header.payload.never-print-this-signature"
            script = r'''
source "$RUNNER"
ADMIN_WEB_DIR="$APP"
ADMIN_WEB_JWT_FILE="$JWT"
ADMIN_WEB_RESULT_FILE="$RESULT"
ADMIN_WEB_TEST_RESULTS_DIR="$PRIVATE_RESULTS"
issue_browser_token() { printf '%s' "$TOKEN" >"$JWT"; chmod 600 "$JWT"; }
run_playwright() {
  printf '%s' "$TOKEN" >"$RESULT"
  printf '%s' "$TOKEN" >"$PRIVATE_RESULTS/leak.txt"
  printf 'opaque screenshot pixels' >"$PRIVATE_RESULTS/failure.png"
  return 1
}
run_browser_tests
'''
            completed = run_bash(script, {
                "RUNNER": str(runner),
                "APP": str(app),
                "JWT": str(jwt),
                "RESULT": str(result),
                "PRIVATE_RESULTS": str(results),
                "TOKEN": token,
            })

            self.assertNotEqual(0, completed.returncode)
            self.assertNotIn(token, completed.stdout + completed.stderr)
            self.assertFalse(result.exists())
            self.assertFalse((results / "leak.txt").exists())
            self.assertFalse((results / "failure.png").exists())


if __name__ == "__main__":
    unittest.main()
