import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


RUNNER = Path(__file__).resolve().parents[1] / "run_unittest.py"


class UnittestGateTest(unittest.TestCase):
    def run_fixture(self, source, *options):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_fixture.py").write_text(source, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(RUNNER), "--start", str(root), *options],
                capture_output=True, text=True, check=False,
            )

    def test_success_requires_an_executed_test(self):
        result = self.run_fixture("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_failure_and_unexpected_skip_are_rejected(self):
        for body in ("self.fail('fixture')", "self.skipTest('fixture')"):
            with self.subTest(body=body):
                result = self.run_fixture(f"import unittest\nclass T(unittest.TestCase):\n def test_case(self): {body}\n")
                self.assertNotEqual(0, result.returncode)

    def test_explicit_exclusion_cannot_make_empty_suite_pass(self):
        result = self.run_fixture("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n", "--exclude-file", "test_fixture.py")
        self.assertNotEqual(0, result.returncode)

    def test_excludes_only_the_named_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_native.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_case(self): self.skipTest('native')\n")
            (root / "test_unit.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_case(self): pass\n")
            result = subprocess.run(
                [sys.executable, str(RUNNER), "--start", str(root), "--exclude-file", "test_native.py"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Ran 1 test", result.stderr)
