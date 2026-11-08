import subprocess
import os
import tempfile
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


    def test_cleanup_removes_private_files_even_if_relay_stop_fails(self):
        with tempfile.TemporaryDirectory() as parent:
            private = Path(parent) / 'private'
            private.mkdir()
            (private / 'fixture').write_text('private')
            code = r'''source "$1"
ADMIN_WEB_RELAY_DIAGNOSTIC=1
COMPOSE_PROJECT_NAME=relay-ci-test
COMPOSE=(fake_compose)
WORK_DIR="$2"
fake_compose() { return 1; }
docker() { return 0; }
cleanup_run 0'''
            result = subprocess.run(['bash', '-c', code, 'relay-test', str(SCRIPT), str(private)],
                                    capture_output=True, text=True)
            self.assertEqual(1, result.returncode)
            self.assertFalse(private.exists())

    def test_conflicting_diagnostic_modes_fail_before_start(self):
        env = os.environ.copy()
        env.update(ADMIN_WEB_RELAY_DIAGNOSTIC='1', ADMIN_WEB_ATTACHMENT_DIAGNOSTIC='1')
        result = subprocess.run(['bash', str(SCRIPT), '--fresh'], env=env, capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('diagnostics must run separately', result.stderr)

    def test_relay_preflight_rejection_does_not_call_compose_cleanup(self):
        with tempfile.TemporaryDirectory() as parent:
            marker = Path(parent) / 'compose-called'
            code = r'''source "$1"
ADMIN_WEB_RELAY_DIAGNOSTIC=1
unset GITHUB_ACTIONS RUNNER_ENVIRONMENT
COMPOSE=(fake_compose)
TEST_MARKER="$2"
ensure_node22() { :; }
docker() { return 0; }
fake_compose() { : > "$TEST_MARKER"; }
main --fresh'''
            result = subprocess.run(['bash', '-c', code, 'relay-test', str(SCRIPT), str(marker)],
                                    capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('requires a GitHub-hosted runner', result.stderr)
            self.assertFalse(marker.exists())

    def test_relay_preflight_refuses_uninspectable_project(self):
        for failing, label in [('ps', 'containers'), ('volume', 'volumes'), ('network', 'networks')]:
            with self.subTest(failing=failing):
                code = r'''source "$1"
ADMIN_WEB_RELAY_DIAGNOSTIC=1
GITHUB_ACTIONS=true
RUNNER_ENVIRONMENT=github-hosted
SURVEY_TEST_PREFIX=relay-ci-fixture
COMPOSE_PROJECT_NAME=relay-ci-fixture
ensure_node22() { :; }
docker() { if [[ "$1" == "$test_kind" ]]; then return 7; fi; return 0; }
test_kind="$2"
main --fresh'''
                result = subprocess.run(['bash', '-c', code, 'relay-test', str(SCRIPT), failing],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(f'unable to inspect relay diagnostic {label}', result.stderr)
                self.assertNotIn('build jar', result.stderr)

    def test_mysql_worker_uses_same_test_database_credential_as_test_web(self):
        import re
        root = SCRIPT.parents[3]
        compose = (SCRIPT.parent / 'admin-web-e2e.compose.yml').read_text()
        development = (root / 'docker-compose.dev.yml').read_text()
        relay = re.search(r'(?ms)^  test-relay:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)', compose)
        web = re.search(r'(?ms)^  test-web:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)', development)
        self.assertIsNotNone(relay)
        self.assertIsNotNone(web)
        pattern = r'(?m)^      ENGINE_DB_ROOT_PASSWORD: (\S+)$'
        relay_password = re.search(pattern, relay.group(1))
        web_password = re.search(pattern, web.group(1))
        self.assertIsNotNone(relay_password)
        self.assertIsNotNone(web_password)
        self.assertEqual(relay_password.group(1), web_password.group(1))


if __name__ == '__main__':
    unittest.main()
