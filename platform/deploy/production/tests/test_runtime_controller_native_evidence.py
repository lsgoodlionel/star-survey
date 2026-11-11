"""Fixture deletion 204 is not enough; independently check exact absence."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('controller_native_evidence',
    Path(__file__).parent / 'integration/runtime_controller_native.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControllerNativeEvidenceTest(unittest.TestCase):
    def test_correct_inspect_routes_and_noop_delete_cannot_verify_cleanup(self):
        calls = []
        class Connection:
            def request(self, method, path): calls.append((method, path))
            def getresponse(self): return type('Response', (), {'status': 200})()
            def close(self): pass
        class API:
            VERSION = '/v1.47'
            def _connect(self): return Connection()
            def _json(self, method, path, **kwargs): calls.append((method, path))
        api = API()
        for kind in ('networks', 'volumes'):
            identity = 'a' * 64 if kind == 'networks' else 'fixture_state'
            self.assertFalse(MODULE.resource_absent(api, kind, identity))
            self.assertEqual(('GET', '/v1.47/' + kind + '/' + identity), calls[-1])
            with self.assertRaises(RuntimeError): MODULE.remove_and_confirm(api, kind, identity)


if __name__ == '__main__':
    unittest.main()
