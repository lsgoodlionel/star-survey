import importlib.util
from pathlib import Path
import unittest
import tempfile
import subprocess
import sys

spec = importlib.util.spec_from_file_location("scope", Path(__file__).resolve().parents[1] / "scope.py")
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class ScopeTests(unittest.TestCase):
    def test_only_diagnostic_paths(self):
        paths = ["platform/a.py", "platform/b.py", "vendor/c.py"]
        log = "Run python3 -m unittest\nplatform/a.py:42: AssertionError\nplatform/b.py mentioned\n"
        self.assertEqual(scope.failed_paths(log, paths), ["platform/a.py"])
        self.assertEqual(scope.commands(log), ["python3 -m unittest"])

    def test_unique_basename_and_ambiguous_name(self):
        log = "Thing.java:17\nShared.java:4"
        paths = ["src/Thing.java", "a/Shared.java", "b/Shared.java"]
        self.assertEqual(scope.failed_paths(log, paths), ["src/Thing.java"])

    def test_bundle_contains_only_diagnostic_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("x = 1\n")
            (root / "src" / "b.py").write_text("y = 2\n")
            (root / "failed.log").write_text("Run python3 -m unittest discover\nsrc/a.py:2: failed\n")
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "add", "src/a.py", "src/b.py"], cwd=root, check=True)
            subprocess.run([sys.executable, str(Path(scope.__file__)), "failed.log", "bundle"], cwd=root, check=True, capture_output=True)
            self.assertTrue((root / "bundle" / "src" / "a.py").is_file())
            self.assertFalse((root / "bundle" / "src" / "b.py").exists())

    def test_excess_file_count_is_detectable(self):
        paths = [f"src/f{i}.py" for i in range(6)]
        log = "\n".join(f"src/f{i}.py:{i+1}: error" for i in range(6))
        self.assertGreater(len(scope.failed_paths(log, paths)), scope.MAX_FILES)

    def test_workflow_and_vendor_paths_excluded(self):
        paths = [".github/workflows/a.py", "vendor/x.py", "platform/ok.ts"]
        log = ".github/workflows/a.py:1\nvendor/x.py:2\nplatform/ok.ts(4,1)"
        self.assertEqual(scope.failed_paths(log, paths), ["platform/ok.ts"])
