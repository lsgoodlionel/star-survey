"""打包测试：体积可控、内容已脱敏、清单足以让开发定位问题。"""

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from logship.bundle import BundleBuilder, BundleLimits, IncidentContext
from logship.redact import Redactor, RedactionRules


class BundleBuilderTest(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tempdir.name)
        self.builder = BundleBuilder(
            redactor=Redactor(RedactionRules.default()),
            limits=BundleLimits(max_lines_per_source=100, max_bundle_bytes=5_000_000),
        )

    def tearDown(self):
        self._tempdir.cleanup()

    def context(self, **overrides):
        defaults = dict(
            app="survey",
            instance="tenant-a",
            environment="prod",
            version="7.1.2-mjy.1",
            error_summary="CDbException: SQLSTATE[42S02] table lime_foo missing",
            occurred_at="2026-09-20T08:15:03Z",
            suppressed_count=0,
        )
        defaults.update(overrides)
        return IncidentContext(**defaults)

    def write_log(self, name, lines):
        path = self.workdir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_bundle_contains_manifest_and_redacted_sources(self):
        log = self.write_log("app.log", ["boom password=\"hunter2\"", "at Response.php:242"])

        bundle = self.builder.build(self.context(), {"app.log": log.read_text(encoding="utf-8")}, self.workdir / "b.tar.gz")

        with tarfile.open(bundle.path, "r:gz") as archive:
            names = archive.getnames()
            self.assertIn("manifest.json", names)
            self.assertIn("logs/app.log", names)
            content = archive.extractfile("logs/app.log").read().decode("utf-8")
        self.assertNotIn("hunter2", content)
        self.assertIn("Response.php:242", content)

    def test_manifest_carries_what_a_developer_needs_to_triage(self):
        bundle = self.builder.build(
            self.context(suppressed_count=7), {"app.log": "boom\n"}, self.workdir / "b.tar.gz"
        )

        with tarfile.open(bundle.path, "r:gz") as archive:
            manifest = json.loads(archive.extractfile("manifest.json").read().decode("utf-8"))

        self.assertEqual(manifest["app"], "survey")
        self.assertEqual(manifest["instance"], "tenant-a")
        self.assertEqual(manifest["environment"], "prod")
        self.assertEqual(manifest["version"], "7.1.2-mjy.1")
        self.assertEqual(manifest["suppressedCount"], 7)
        self.assertEqual(manifest["fingerprint"], bundle.fingerprint)
        self.assertIn("CDbException", manifest["errorSummary"])
        self.assertIn("app.log", manifest["sources"])
        self.assertIn("redaction", manifest)

    def test_fingerprint_ignores_volatile_parts_of_the_message(self):
        first = self.builder.build(
            self.context(error_summary="CDbException at 2026-09-20 08:15:03 for response 4711"),
            {"app.log": "x\n"},
            self.workdir / "a.tar.gz",
        )
        second = self.builder.build(
            self.context(error_summary="CDbException at 2026-09-21 23:02:11 for response 9152"),
            {"app.log": "y\n"},
            self.workdir / "b.tar.gz",
        )

        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_different_errors_get_different_fingerprints(self):
        first = self.builder.build(self.context(error_summary="CDbException: table missing"), {"a": "x"}, self.workdir / "a.tgz")
        second = self.builder.build(self.context(error_summary="TypeError: null given"), {"a": "x"}, self.workdir / "b.tgz")

        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_long_sources_are_truncated_from_the_front_keeping_the_tail(self):
        lines = [f"line {index}" for index in range(500)]

        bundle = self.builder.build(self.context(), {"app.log": "\n".join(lines)}, self.workdir / "b.tar.gz")

        with tarfile.open(bundle.path, "r:gz") as archive:
            content = archive.extractfile("logs/app.log").read().decode("utf-8")
        self.assertIn("line 499", content)
        self.assertNotIn("line 0\n", content)
        self.assertIn("truncated", content.splitlines()[0])

    def test_oversized_bundle_is_rejected_rather_than_silently_shipped(self):
        builder = BundleBuilder(
            redactor=Redactor(RedactionRules.default()),
            limits=BundleLimits(max_lines_per_source=100000, max_bundle_bytes=256),
        )
        huge = "\n".join(f"line {index} with padding" for index in range(50000))

        with self.assertRaises(ValueError):
            builder.build(self.context(), {"app.log": huge}, self.workdir / "b.tar.gz")


if __name__ == "__main__":
    unittest.main()
