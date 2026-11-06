import subprocess
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name('run-admin-web-e2e.sh')


class CleanupStatusTest(unittest.TestCase):
    def test_cleanup_failure_is_failure_and_original_failure_is_preserved(self):
        for original, cleanup, expected in [(0, 1, 1), (7, 0, 7), (0, 0, 0)]:
            with self.subTest(original=original, cleanup=cleanup):
                code = ('source "$1"; test_cleanup="$2"; test_original="$3"; '
                        'cleanup_run() { return "$test_cleanup"; }; '
                        'trap cleanup EXIT; exit "$test_original"')
                result = subprocess.run(['bash', '-c', code, 'relay-test', str(SCRIPT),
                                         str(cleanup), str(original)], capture_output=True, text=True)
                self.assertEqual(expected, result.returncode, result.stderr)


if __name__ == '__main__':
    unittest.main()
