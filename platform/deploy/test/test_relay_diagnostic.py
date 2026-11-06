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


    def test_diagnostic_cleanup_rejects_remaining_or_uninspectable_resources(self):
        for kind in ('none', 'containers', 'volumes', 'networks', 'inspect_error'):
            with self.subTest(kind=kind):
                code = r'''source "$1"
ADMIN_WEB_RELAY_DIAGNOSTIC=1
COMPOSE_PROJECT_NAME=relay-ci-test
COMPOSE=(fake_compose)
WORK_DIR=""
fake_compose() { return 0; }
docker() {
  case "$1:$2" in
    ps:-aq) [[ "$test_kind" != inspect_error ]] || return 2; [[ "$test_kind" != containers ]] || echo found ;;
    volume:ls) [[ "$test_kind" != volumes ]] || echo found ;;
    network:ls) [[ "$test_kind" != networks ]] || echo found ;;
  esac
  return 0
}
test_kind="$2"
cleanup_run 0'''
                result = subprocess.run(['bash', '-c', code, 'relay-test', str(SCRIPT), kind],
                                        capture_output=True, text=True)
                self.assertEqual(0 if kind == 'none' else 1, result.returncode, result.stderr)


if __name__ == '__main__':
    unittest.main()
