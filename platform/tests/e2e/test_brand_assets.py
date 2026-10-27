"""Unit tests for the real-engine acceptance oracle; no Docker required."""

import base64
import importlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from urllib.request import Request


class BrandAssetsAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(Path(__file__).with_name("brand_assets.py").is_file(),
                        "real brandAssets acceptance driver is missing")
        self.driver = importlib.import_module("brand_assets")

    def test_versions_keep_distinct_logo_favicon_and_earlier_bytes(self):
        first, assets = self.driver.snapshot(1)
        saved = json.dumps([first, assets], sort_keys=True)
        second, later = self.driver.snapshot(2)
        self.assertEqual(saved, json.dumps([first, assets], sort_keys=True))
        self.assertEqual(first["uuid"], second["uuid"])
        self.assertEqual(1, first["branding"]["logoAsset"]["assetVersion"])
        self.assertEqual(2, second["branding"]["logoAsset"]["assetVersion"])
        self.assertNotEqual(assets[0]["sha256"], assets[1]["sha256"])
        for old, new in zip(assets, later):
            self.assertNotEqual(old["contentBase64"], new["contentBase64"])
            self.assertTrue(base64.b64decode(old["contentBase64"]).startswith(b"\x89PNG\r\n\x1a\n"))
        request = self.driver.parse_request(json.dumps({
            "requestId": "123e4567-e89b-42d3-a456-426614174000",
            "engineInstanceId": "brand-assets-unit",
            "definition": first, "brandAssets": assets,
        }).encode())
        self.assertEqual(2, len(request.brand_assets))

    def test_snapshot_is_a_publishable_materialized_definition(self):
        from pubgw.validate import validate_definition

        definition, _ = self.driver.snapshot(1)

        report = validate_definition(
            self.driver.SurveyDefinition.from_dict(definition)
        )

        self.assertEqual([], [(issue.code, issue.path) for issue in report.issues])

    def page(self, logo="/upload/surveys/42/images/mjy-brand-logo.png",
             favicon="/upload/surveys/42/images/mjy-brand-favicon.png"):
        return '<img class="logo img-fluid" src="{}"><link rel="shortcut icon" href="{}">'.format(
            logo, favicon)

    def test_extracts_only_real_logo_and_favicon_elements(self):
        urls = self.driver.asset_urls(self.page(), 42)
        self.assertEqual("http://localhost/upload/surveys/42/images/mjy-brand-logo.png", urls["logoAsset"])
        self.assertEqual("http://localhost/upload/surveys/42/images/mjy-brand-favicon.png", urls["faviconAsset"])

    def test_accepts_same_origin_published_theme_asset_url(self):
        for digest in ("fcb4bb6f", "e56beba"):
            published = "/tmp/assets/{}/mjy-brand-favicon.png".format(digest)
            urls = self.driver.asset_urls(self.page(favicon=published), 42)
            self.assertEqual("http://localhost" + published, urls["faviconAsset"])

    def test_rejects_foreign_origin_or_another_survey(self):
        for bad in ("https://foreign.invalid/upload/surveys/42/images/mjy-brand-logo.png",
                    "/upload/surveys/99/images/mjy-brand-logo.png",
                    "http://localhost:99/upload/surveys/42/images/mjy-brand-logo.png"):
            with self.subTest(bad=bad), self.assertRaises(self.driver.AcceptanceFailure):
                self.driver.asset_urls(self.page(logo=bad), 42)

    def test_filename_in_plain_text_is_not_rendered_asset_evidence(self):
        with self.assertRaises(self.driver.AcceptanceFailure):
            self.driver.asset_urls("mjy-brand-logo.png mjy-brand-favicon.png", 42)

    def test_missing_favicon_fails_even_when_logo_is_present(self):
        with self.assertRaises(self.driver.AcceptanceFailure):
            self.driver.asset_urls('<img class="logo" src="/upload/surveys/42/images/mjy-brand-logo.png">', 42)

    def test_http_byte_comparison_rejects_html_errors_and_later_version(self):
        _, first = self.driver.snapshot(1)
        _, second = self.driver.snapshot(2)
        self.driver.assert_asset_bytes(base64.b64decode(first[0]["contentBase64"]), first[0])
        for wrong in (b"<html>404</html>", base64.b64decode(second[0]["contentBase64"])):
            with self.subTest(wrong=wrong[:10]), self.assertRaises(self.driver.AcceptanceFailure):
                self.driver.assert_asset_bytes(wrong, first[0])

    def test_signed_post_reuses_docker_curl_transport_without_disclosing_url(self):
        request = Request("http://localhost/index.php/private?sig=do-not-log", data=b"payload")
        with patch.object(self.driver.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, b'{"status":"ok"}', b"")) as run:
            response = self.driver.docker_post("unit-web")(request, 30)
        self.assertEqual(b'{"status":"ok"}', response)
        self.assertEqual(b"payload", run.call_args.kwargs["input"])
        self.assertIn("unit-web", run.call_args.args[0])
        self.assertIn(request.full_url, run.call_args.args[0])

    def test_transport_failure_hides_signed_url_and_raw_stderr(self):
        request = Request("http://localhost/index.php/private?sig=do-not-log", data=b"payload")
        with patch.object(self.driver.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 7, b"", b"secret=do-not-log")):
            with self.assertRaises(self.driver.BrandAssetError) as error:
                self.driver.docker_post("unit-web")(request, 30)
        self.assertNotIn("do-not-log", str(error.exception))

    def test_stage_failure_summary_cannot_echo_engine_details(self):
        result = {"ok": False, "failedStage": "brand_assets", "errors": ["sig=private"],
                  "steps": [{"stage": "import", "ok": True, "detail": "private"}]}
        with self.assertRaises(self.driver.AcceptanceFailure) as error:
            self.driver.require_publish(result)
        self.assertIn("brand_assets", str(error.exception))
        self.assertNotIn("private", str(error.exception))

    def test_stack_names_and_secrets_are_fresh_even_with_shared_env_defaults(self):
        with patch.dict(self.driver.os.environ, {"SURVEY_TEST_PREFIX": "survey", "COMPOSE_PROJECT_NAME": "survey"}):
            first = self.driver.IsolatedStack("mysql")
            second = self.driver.IsolatedStack("pgsql")
        self.assertNotEqual(first.project, second.project)
        self.assertNotEqual("survey", first.project)
        self.assertNotEqual(first.env["MJY_PLATFORM_EVENTS_SECRET"], second.env["MJY_PLATFORM_EVENTS_SECRET"])
        self.assertEqual(first.project, first.env["MJY_ENGINE_INSTANCE_ID"])
        self.assertIn(first.project, first.compose)
        self.assertNotIn("survey-test-web", first.compose)

    def test_rollback_oracle_ignores_theme_defaults_but_rejects_author_values(self):
        class Db:
            def __init__(self, count):
                self.count = count
                self.sql = ""

            def value(self, sql):
                self.sql = sql
                return str(self.count)

        defaults = Db(0)
        self.driver.assert_no_author_brand_options(defaults, 42)
        self.assertIn("sid=42", defaults.sql)
        self.assertNotIn("LIKE '%mjybrand%'", defaults.sql)
        for marker in ("#176B48", "Pinned brand assets", "Acceptance logo",
                       "image::survey::", "mjy-brand-logo.png", "mjy-brand-favicon.png"):
            self.assertIn(marker, defaults.sql)
        with self.assertRaisesRegex(self.driver.AcceptanceFailure, "brand theme options"):
            self.driver.assert_no_author_brand_options(Db(1), 42)


if __name__ == "__main__":
    unittest.main()
