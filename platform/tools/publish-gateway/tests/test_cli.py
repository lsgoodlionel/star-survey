"""命令行入口：退出码是调用方唯一可靠的信号。"""

import contextlib
import io
import json
import os
import tempfile
import unittest

from pubgw import cli

from .fakes import FakeEngine
from .fixtures import sample_definition, sample_payload


def write(payload, suffix=".json"):
    handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    handle.write(json.dumps(payload) if not isinstance(payload, str) else payload)
    handle.close()
    return handle.name


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.paths = []
        self.engine = FakeEngine(sample_definition())
        self.env = {"LIMESURVEY_RPC_PASSWORD": "password"}

    def tearDown(self):
        for path in self.paths:
            os.unlink(path)

    def temp(self, payload, suffix=".json"):
        path = write(payload, suffix)
        self.paths.append(path)
        return path

    def run_cli(self, argv):
        """吞掉命令的输出：测试关心的是退出码与落盘结果。"""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.run(argv, env=self.env, transport_factory=lambda url: self.engine.transport)

    # ------------------------------------------------------------ validate

    def test_validate_accepts_a_good_definition(self):
        path = self.temp(sample_payload())

        self.assertEqual(0, self.run_cli(["validate", "--definition", path]))

    def test_validate_rejects_a_bad_definition(self):
        payload = sample_payload()
        payload["groups"][0]["questions"][0]["answers"] = []

        self.assertEqual(1, self.run_cli(["validate", "--definition", self.temp(payload)]))

    def test_a_malformed_definition_is_a_config_error(self):
        path = self.temp("{not json", suffix=".json")

        self.assertEqual(2, self.run_cli(["validate", "--definition", path]))

    def test_a_missing_definition_file_is_a_config_error(self):
        self.assertEqual(2, self.run_cli(["validate", "--definition", "/nonexistent/x.json"]))

    # ------------------------------------------------------------- compile

    def test_compile_writes_the_lss_document(self):
        out = self.temp({}, suffix=".lss")
        path = self.temp(sample_payload())

        self.assertEqual(0, self.run_cli(["compile", "--definition", path, "--out", out]))
        with open(out, encoding="utf-8") as handle:
            self.assertIn("<LimeSurveyDocType><![CDATA[Survey]]>", handle.read())

    # ------------------------------------------------------------- publish

    def test_publish_succeeds_and_writes_the_binding_record(self):
        binding = self.temp({}, suffix=".json")
        path = self.temp(sample_payload())

        code = self.run_cli(
            [
                "publish",
                "--definition", path,
                "--engine-url", "http://localhost",
                "--engine-instance", "survey-test-web",
                "--binding-out", binding,
            ]
        )

        self.assertEqual(0, code)
        with open(binding, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual("survey-test-web", record["engineInstance"])
        self.assertEqual(4, len(record["questions"]))

    def test_publish_fails_and_reports_the_rollback(self):
        self.engine.rename = {"QSINGLE": "r7q0"}
        path = self.temp(sample_payload())

        code = self.run_cli(["publish", "--definition", path, "--engine-url", "http://localhost"])

        self.assertEqual(1, code)
        self.assertEqual(1, len(self.engine.deleted))

    def test_publish_without_a_password_in_the_environment_is_a_config_error(self):
        self.env = {}
        path = self.temp(sample_payload())

        code = self.run_cli(["publish", "--definition", path, "--engine-url", "http://localhost"])

        self.assertEqual(2, code)
        self.assertEqual([], self.engine.methods())

    # --------------------------------------------------------- drift-check

    def test_drift_check_passes_on_an_untouched_survey(self):
        binding = self.temp({}, suffix=".json")
        path = self.temp(sample_payload())
        self.run_cli(
            ["publish", "--definition", path, "--engine-url", "http://x", "--binding-out", binding]
        )

        code = self.run_cli(["drift-check", "--binding", binding, "--engine-url", "http://x"])

        self.assertEqual(0, code)

    def test_drift_check_reports_a_post_publication_rename(self):
        binding = self.temp({}, suffix=".json")
        path = self.temp(sample_payload())
        self.run_cli(
            ["publish", "--definition", path, "--engine-url", "http://x", "--binding-out", binding]
        )
        self.engine.rename = {"QSINGLE": "HACKED"}

        code = self.run_cli(["drift-check", "--binding", binding, "--engine-url", "http://x"])

        self.assertEqual(1, code)


if __name__ == "__main__":
    unittest.main()
