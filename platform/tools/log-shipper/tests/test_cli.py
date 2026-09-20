"""命令行与配置测试：配置缺项要在启动时就报错，而不是等故障发生时才失败。"""

import json
import tempfile
import unittest
from pathlib import Path

from logship.cli import run
from logship.config import ConfigError, ShipperConfig


def write_config(directory: Path, **overrides) -> Path:
    payload = {
        "app": "survey",
        "instance": "tenant-a",
        "environment": "prod",
        "version": "7.1.2-mjy.1",
        "repository": "lsgoodlionel/paper",
        "tokenEnv": "MJY_LOGSHIP_TOKEN",
        "stateDir": str(directory / "state"),
        "spoolDir": str(directory / "spool"),
        "sources": [{"name": "app.log", "kind": "file", "location": str(directory / "app.log")}],
    }
    payload.update(overrides)
    path = directory / "logship.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tempdir.name)
        (self.workdir / "app.log").write_text("boom\n", encoding="utf-8")

    def tearDown(self):
        self._tempdir.cleanup()

    def test_loads_a_complete_configuration(self):
        config = ShipperConfig.load(write_config(self.workdir))

        self.assertEqual(config.app, "survey")
        self.assertEqual(config.repository, "lsgoodlionel/paper")
        self.assertEqual(config.sources[0].name, "app.log")
        self.assertEqual(config.branch, "main")

    def test_missing_required_field_is_rejected_with_its_name(self):
        path = write_config(self.workdir)
        payload = json.loads(path.read_text(encoding="utf-8"))
        del payload["repository"]
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(ConfigError) as raised:
            ShipperConfig.load(path)

        self.assertIn("repository", str(raised.exception))

    def test_configuration_without_sources_is_rejected(self):
        with self.assertRaises(ConfigError):
            ShipperConfig.load(write_config(self.workdir, sources=[]))

    def test_token_is_read_from_the_environment_never_from_the_file(self):
        config = ShipperConfig.load(write_config(self.workdir, token="should-not-be-used"))

        self.assertFalse(hasattr(config, "token"))
        self.assertEqual(config.token_env, "MJY_LOGSHIP_TOKEN")


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tempdir.name)
        (self.workdir / "app.log").write_text("CDbException: boom\n", encoding="utf-8")
        self.config_path = write_config(self.workdir)

    def tearDown(self):
        self._tempdir.cleanup()

    def test_dry_run_ship_reports_what_would_be_uploaded_without_a_token(self):
        exit_code = run(
            ["ship", "--config", str(self.config_path), "--summary", "CDbException: boom", "--dry-run"],
            env={},
        )

        self.assertEqual(exit_code, 0)

    def test_ship_without_a_token_fails_clearly(self):
        exit_code = run(["ship", "--config", str(self.config_path), "--summary", "boom"], env={})

        self.assertEqual(exit_code, 2)

    def test_doctor_checks_configuration_sources_and_token(self):
        exit_code = run(["doctor", "--config", str(self.config_path)], env={"MJY_LOGSHIP_TOKEN": "t"})

        self.assertEqual(exit_code, 0)

    def test_doctor_fails_when_a_source_is_unreadable(self):
        (self.workdir / "app.log").unlink()

        exit_code = run(["doctor", "--config", str(self.config_path)], env={"MJY_LOGSHIP_TOKEN": "t"})

        self.assertEqual(exit_code, 1)

    def test_spooled_incident_is_shipped_and_then_archived(self):
        spool = self.workdir / "spool"
        spool.mkdir(parents=True, exist_ok=True)
        incident = spool / "incident-1.json"
        incident.write_text(json.dumps({"errorSummary": "TypeError: null given"}), encoding="utf-8")

        exit_code = run(["drain", "--config", str(self.config_path), "--dry-run"], env={})

        self.assertEqual(exit_code, 0)
        self.assertFalse(incident.exists())
        self.assertTrue(list((spool / "shipped").glob("incident-1.json")))


if __name__ == "__main__":
    unittest.main()
