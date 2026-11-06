import importlib.util
from pathlib import Path
import unittest
import json

path = Path(__file__).with_name('check_prepared_transaction.py')
spec = importlib.util.spec_from_file_location('prepared_test', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreparedDiagnosticsTest(unittest.TestCase):
    def test_wait_requires_initialization_then_successful_query(self):
        now = [0.0]
        initialized = iter((False, True, True))
        queries = iter((False, True))
        calls = []
        def query():
            calls.append('query')
            return next(queries)
        module.wait_for_final_server(lambda: next(initialized), query, clock=lambda: now[0],
                                     sleep=lambda delay: now.__setitem__(0, now[0] + delay), timeout=1)
        self.assertEqual(calls, ['query', 'query'])
        self.assertEqual(now[0], .5)

    def test_wait_fails_closed_at_soft_deadline(self):
        now = [0.0]
        def no_query():
            self.fail('query must not run before initialization')
        with self.assertRaisesRegex(RuntimeError, 'did not start'):
            module.wait_for_final_server(lambda: False, no_query, clock=lambda: now[0],
                                         sleep=lambda delay: now.__setitem__(0, now[0] + delay), timeout=.5)

    def test_container_diagnostics_reports_only_fixed_fields_and_counts(self):
        secret = 'PRIVATE_FIXTURE_SENTINEL'
        inspect = json.dumps([{'State': {'Status': 'running', 'ExitCode': 0, 'OOMKilled': False},
                               'RestartCount': 0, 'secret': secret}])
        result = module.summarize_container_diagnostics(inspect, secret + '\n' +
            'database system is ready to accept connections\ndatabase system is shut down')
        self.assertEqual(result, 'container_state=running restarts=0 exit_code=0 oom=false ready=1 shutdown=1 startup=0 init_complete=0')
        self.assertNotIn(secret, result)
        self.assertEqual(module.summarize_container_diagnostics(inspect.replace('running', secret), secret),
                         'container_state=unavailable')
        self.assertEqual(module.summarize_container_diagnostics('not json ' + secret, secret),
                         'container_state=unavailable')

    def test_failure_classification_never_exposes_raw_data(self):
        secret = 'PRIVATE_FIXTURE_SENTINEL'
        for stderr, category in ((f'connection refused {secret}', 'connection_refused'),
                                 (f'the database system is starting up {secret}', 'starting_up'),
                                 (f'other failure {secret}', 'other')):
            with self.subTest(category=category):
                result = module.summarize_sql_error(stderr)
                self.assertIn('category=' + category, result)
                self.assertRegex(result, r'stderr_sha256=[0-9a-f]{64}$')
                self.assertNotIn(secret, result)
                self.assertNotIn(stderr, result)


if __name__ == '__main__':
    unittest.main()
