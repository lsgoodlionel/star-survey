import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).with_name('check_prepared_transaction.py')
spec = importlib.util.spec_from_file_location('prepared_test', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PreparedDiagnosticsTest(unittest.TestCase):
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
