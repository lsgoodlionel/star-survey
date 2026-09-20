"""日志采集测试：只取尾部、缺失来源不能让整次上报失败。"""

import tempfile
import unittest
from pathlib import Path

from logship.sources import CommandRunner, SourceSpec, collect


class FakeRunner(CommandRunner):
    def __init__(self, outputs=None):
        self.calls = []
        self._outputs = outputs or {}

    def run(self, argv):
        self.calls.append(argv)
        key = argv[1] if len(argv) > 1 else ""
        result = self._outputs.get(key, "")
        if isinstance(result, Exception):
            raise result
        return result


class CollectTest(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tempdir.name)

    def tearDown(self):
        self._tempdir.cleanup()

    def write(self, name, text):
        path = self.workdir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_file_source_keeps_only_the_tail(self):
        path = self.write("app.log", "\n".join(f"line {index}" for index in range(100)) + "\n")
        spec = SourceSpec(name="app.log", kind="file", location=str(path), lines=10)

        collected = collect([spec], runner=FakeRunner())

        self.assertIn("line 99", collected["app.log"])
        self.assertNotIn("line 5\n", collected["app.log"])

    def test_missing_file_is_reported_instead_of_raising(self):
        spec = SourceSpec(name="gone.log", kind="file", location=str(self.workdir / "gone.log"), lines=10)

        collected = collect([spec], runner=FakeRunner())

        self.assertIn("unavailable", collected["gone.log"])

    def test_docker_source_uses_the_container_logs(self):
        spec = SourceSpec(name="web.log", kind="docker", location="survey-web", lines=50)
        runner = FakeRunner({"logs": "container output\n"})

        collected = collect([spec], runner=runner)

        self.assertEqual(collected["web.log"], "container output\n")
        self.assertIn("--tail", runner.calls[0])
        self.assertIn("survey-web", runner.calls[0])

    def test_failing_command_does_not_break_other_sources(self):
        good = self.write("good.log", "ok\n")
        specs = [
            SourceSpec(name="web.log", kind="docker", location="survey-web", lines=10),
            SourceSpec(name="good.log", kind="file", location=str(good), lines=10),
        ]
        runner = FakeRunner({"logs": RuntimeError("docker not running")})

        collected = collect(specs, runner=runner)

        self.assertIn("unavailable", collected["web.log"])
        self.assertEqual(collected["good.log"], "ok\n")

    def test_unknown_source_kind_is_rejected_at_collection_time(self):
        spec = SourceSpec(name="x", kind="carrier-pigeon", location="?", lines=1)

        collected = collect([spec], runner=FakeRunner())

        self.assertIn("unsupported", collected["x"])

    def test_binary_noise_does_not_crash_the_collector(self):
        path = self.workdir / "bin.log"
        path.write_bytes(b"\xff\xfe ok \x00 line\n")
        spec = SourceSpec(name="bin.log", kind="file", location=str(path), lines=10)

        collected = collect([spec], runner=FakeRunner())

        self.assertIn("ok", collected["bin.log"])


if __name__ == "__main__":
    unittest.main()
