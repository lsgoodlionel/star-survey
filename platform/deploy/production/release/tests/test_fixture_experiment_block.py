"""Safety gates for unproven fixture experiments; no real daemon or database."""
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = Path(__file__).resolve().parent
RELEASE = HERE.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixtureExperimentBlockTest(unittest.TestCase):
    def test_untrusted_ci_flags_never_authorize_database_gate(self):
        gate = load('blocked_database_gate', HERE / 'check_database_gate.py')
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                                          'GITHUB_REPOSITORY': 'lsgoodlionel/star-survey',
                                          'FIXTURE_PG_PORT': '5432'}, clear=True):
            with mock.patch('subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'independent target isolation'):
                    gate.main()
                run.assert_not_called()

    def test_unverified_compensation_does_not_reach_database(self):
        compensation = load('blocked_compensation', HERE / 'check_fixture_compensation.py')
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'FIXTURE_PG_PORT': '5432'}, clear=True):
            with mock.patch('subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'independent target isolation'):
                    compensation.main()
                run.assert_not_called()

    def test_direct_experiment_entrypoints_fail_closed(self):
        for filename in ('check_database_gate.py', 'check_fixture_compensation.py'):
            with self.subTest(filename=filename):
                result = subprocess.run([sys.executable, str(HERE / filename)],
                                        capture_output=True, text=True, timeout=5)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('blocked pending independent target isolation', result.stderr)
                self.assertEqual('', result.stdout)

    def test_database_failure_never_discloses_raw_details(self):
        compensation = load('blocked_compensation_log', HERE / 'check_fixture_compensation.py')
        secret = 'SYNTHETIC_SECRET_SENTINEL'
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            with self.assertRaises(RuntimeError) as error:
                compensation.raise_database_failure(secret)
        self.assertFalse(secret in str(error.exception))
        self.assertFalse(secret in captured.getvalue())
        self.assertIsNone(error.exception.__cause__)

    def test_marker_clear_is_blocked_without_inspecting_or_deleting_any_path(self):
        marker = load('blocked_marker', RELEASE / 'fixture_recovery_marker.py')
        with mock.patch('os.unlink') as unlink, mock.patch('os.open') as open_file:
            with self.assertRaisesRegex(RuntimeError, 'independent exclusion'):
                marker.clear_pending_marker(Path('/synthetic/untrusted'))
            unlink.assert_not_called()
            open_file.assert_not_called()


if __name__ == '__main__':
    unittest.main()
