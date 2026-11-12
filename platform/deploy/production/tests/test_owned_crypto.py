"""Child executor transition tests; fake backend never executes OpenSSL."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

from test_owned_children import Guard, Store

ROOT = Path(__file__).resolve().parents[1]


class OwnedCryptoExecutorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        spec = importlib.util.spec_from_file_location('owned_crypto_test', ROOT / 'owned_crypto.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        import owned_children
        cls.children_module = owned_children

    def setUp(self):
        self.store, self.guard = Store(), Guard()
        self.events, self.identities = [], {}
        self.mode, self.payload = 'success', None
        owner = self
        class Process:
            pid = 55
            returncode = None
            def communicate(self, *, input, timeout):
                owner.assertEqual('released', owner.store.value['children'][0]['phase'])
                owner.payload = json.loads(input)
                owner.events.append('release')
                if owner.mode in ('timeout', 'unknown'):
                    raise subprocess.TimeoutExpired(['fixture'], timeout)
                self.returncode = 1 if owner.mode == 'exit' else 0
                if owner.mode != 'lying-exit':
                    owner.identities.pop(55, None)
                return None, None
            def poll(self):
                return self.returncode
            def kill(self):
                owner.events.append('kill')
                if owner.mode == 'unknown':
                    raise OSError('synthetic cannot kill')
                self.returncode = -9
            def wait(self, *, timeout):
                owner.events.append('wait')
                if owner.mode == 'unknown':
                    raise subprocess.TimeoutExpired(['fixture'], timeout)
                owner.identities.pop(55, None)
                return self.returncode
        class Backend:
            @contextmanager
            def binary(self):
                owner.events.append('binary-open')
                try:
                    yield 66
                finally:
                    owner.events.append('binary-close')
            def describe(self, fd, role, profile):
                return {'fd': fd, 'dev': 1, 'ino': fd, 'uid': 1000, 'mode': 384,
                    'nlink': 0, 'size': 0, 'mtime': 1, 'kind': 'memory'}
            def spawn(self, actor, fds):
                owner.assertEqual('pending', owner.store.value['children'][0]['phase'])
                owner.events.append('spawn')
                owner.identities[55] = {'pid': 55, 'start': '555', 'parentPid': 44, 'state': 'S'}
                if owner.mode == 'journal-fail-after-spawn':
                    owner.store.fail = True
                owner.process = Process()
                return owner.process
            def identity(self, pid):
                if owner.mode == 'end-query-fails' and getattr(owner, 'process', None) is not None and owner.process.returncode is not None:
                    raise OSError('synthetic observation failure')
                return owner.identities.get(pid)
        self.journal = self.children_module.ChildJournal(self.store, self.guard,
            operation_id='00000000-0000-4000-8000-000000000001',
            installation_id='00000000-0000-4000-8000-000000000002',
            _child_reader=lambda pid: self.identities.get(pid))
        self.runner = self.module.OpenSSLProcess(self.journal, self.guard, _backend=Backend())
        self.leases = [SimpleNamespace(fd=number, max_file_bytes=32) for number in (11, 12, 13)]

    def execute(self, **kwargs):
        return self.runner.execute('encrypt', *self.leases, timeout=1, **kwargs)

    def test_pid_is_persisted_before_payload_and_completion_follows_observed_end(self):
        result = self.execute()
        self.assertEqual(0, result.returncode)
        self.assertEqual('completed', self.store.value['children'][0]['phase'])
        self.assertLess(self.events.index('spawn'), self.events.index('release'))
        self.assertEqual({'binary', 'input', 'output', 'key'}, set(self.payload['fds']))
        self.assertNotIn('argv', self.payload)

    def test_begin_persistence_failure_never_spawns_a_child(self):
        self.store.fail = True
        with self.assertRaises(self.module.KnownCryptoFailure):
            self.execute()
        self.assertNotIn('spawn', self.events)

    def test_created_receipt_failure_kills_and_waits_without_releasing_payload(self):
        self.mode = 'journal-fail-after-spawn'
        with self.assertRaises(self.module.KnownCryptoFailure):
            self.execute()
        self.assertNotIn('release', self.events)
        self.assertIn('kill', self.events)
        self.assertIn('wait', self.events)

    def test_timeout_and_nonzero_exit_cannot_become_success(self):
        for mode in ('timeout', 'exit'):
            with self.subTest(mode=mode):
                self.setUp()
                self.mode = mode
                with self.assertRaises(self.module.KnownCryptoFailure):
                    self.execute()
                self.assertEqual('failed', self.store.value['children'][0]['phase'])
                self.assertEqual('deadline' if mode == 'timeout' else 'exit', self.store.value['children'][0]['reason'])

    def test_unconfirmed_termination_stays_released_and_is_not_a_known_failure(self):
        self.mode = 'unknown'
        with self.assertRaises(self.module.UncertainCryptoChild):
            self.execute()
        self.assertEqual('released', self.store.value['children'][0]['phase'])
        self.assertIn('wait', self.events)  # kill failure cannot suppress the wait attempt.

    def test_zero_status_is_not_enough_when_end_observation_is_live_or_unknown(self):
        for mode in ('lying-exit', 'end-query-fails'):
            with self.subTest(mode=mode):
                self.setUp()
                self.mode = mode
                with self.assertRaises(self.module.UncertainCryptoChild):
                    self.execute()
                self.assertEqual('released', self.store.value['children'][0]['phase'])

    def test_invalid_or_aliased_descriptors_and_bad_timeouts_do_not_spawn(self):
        for timeout in (True, 0, float('inf'), 301):
            with self.assertRaises(self.module.KnownCryptoFailure):
                self.runner.execute('encrypt', *self.leases, timeout=timeout)
        self.leases[1].fd = self.leases[0].fd
        with self.assertRaises(self.module.KnownCryptoFailure):
            self.execute()
        self.assertNotIn('spawn', self.events)


if __name__ == '__main__':
    unittest.main()
