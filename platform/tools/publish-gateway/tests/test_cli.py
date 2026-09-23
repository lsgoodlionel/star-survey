"""命令行入口：退出码是调用方唯一可靠的信号。"""

import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from pubgw import cli
from pubgw.store import RESULTS_FILE

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


class PruneResultsTest(unittest.TestCase):
    """回收磁盘是运维显式触发的维护动作（自动路径不做 VACUUM，见 store.reclaim）。"""

    DAY = 24 * 3600

    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.state_dir, RESULTS_FILE)

    def tearDown(self):
        shutil.rmtree(self.state_dir)

    def run_cli(self, env=None):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
            code = cli.run(["prune-results"], env=dict({"PUBGW_STATE_DIR": self.state_dir}, **(env or {})))
        return code, buffer.getvalue()

    def store_row(self, request_id, age_seconds):
        connection = sqlite3.connect(self.path)
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS publish_results (request_id TEXT PRIMARY KEY, "
                "fingerprint TEXT NOT NULL, status INTEGER NOT NULL, body BLOB NOT NULL, "
                "created_at INTEGER NOT NULL, survey_id INTEGER, pruned_at INTEGER)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO publish_results "
                "(request_id, fingerprint, status, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (request_id, "fp", 200, sqlite3.Binary(b"x" * 4096), int(time.time()) - age_seconds),
            )
        connection.close()

    def test_reports_what_it_dropped_and_vacuums(self):
        self.store_row("old", 8 * self.DAY)
        self.store_row("ancient", 100 * self.DAY)
        self.store_row("fresh", 60)

        code, out = self.run_cli()

        self.assertEqual(0, code)
        report = json.loads(out)
        self.assertEqual(1, report["bodiesDropped"])
        self.assertEqual(1, report["tombstonesDeleted"])
        self.assertTrue(report["vacuumed"])
        self.assertEqual(7 * self.DAY, report["resultTtlSeconds"])

    def test_honours_a_configured_window(self):
        self.store_row("old", 2 * self.DAY)

        code, out = self.run_cli({"PUBGW_RESULT_TTL_SECONDS": "86400"})

        self.assertEqual(0, code)
        self.assertEqual(1, json.loads(out)["bodiesDropped"])

    def test_refuses_without_a_state_dir(self):
        code, _ = self.run_cli()
        self.assertEqual(0, code)  # 上面那次已经建过库；这里只验证缺配置的分支
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, cli.run(["prune-results"], env={}))

    def test_refuses_a_bad_retention_window(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli.run(["prune-results"],
                           env={"PUBGW_STATE_DIR": self.state_dir, "PUBGW_RESULT_TTL_SECONDS": "60"})

        self.assertEqual(2, code)
