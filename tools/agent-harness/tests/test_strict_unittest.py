"""GitHub Python gates fail closed when unittest reports any skip."""

from pathlib import Path
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

from agent_harness.strict_unittest import main  # noqa: E402


class StrictUnittestTests(unittest.TestCase):
    def test_skip_is_failure_and_all_pass_is_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            test_file = root / "test_sample.py"
            test_file.write_text(
                "import unittest\n"
                "class Sample(unittest.TestCase):\n"
                "    @unittest.skip('fixture')\n"
                "    def test_case(self): pass\n",
                encoding="utf-8")
            self.assertEqual(main(["--start", str(root)]), 1)
            test_file.unlink()
            (root / "test_pass.py").write_text(
                "import unittest\n"
                "class Sample(unittest.TestCase):\n"
                "    def test_case(self): self.assertTrue(True)\n",
                encoding="utf-8")
            self.assertEqual(main(["--start", str(root)]), 0)

    def test_explicitly_partitioned_native_file_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_native.py").write_text(
                "import unittest\n"
                "class Native(unittest.TestCase):\n"
                "    @unittest.skip('release-only')\n"
                "    def test_case(self): pass\n", encoding="utf-8")
            (root / "test_unit.py").write_text(
                "import unittest\n"
                "class Unit(unittest.TestCase):\n"
                "    def test_case(self): self.assertTrue(True)\n", encoding="utf-8")
            self.assertEqual(main([
                "--start", str(root), "--exclude-file", "test_native.py",
            ]), 0)


if __name__ == "__main__":
    unittest.main()
