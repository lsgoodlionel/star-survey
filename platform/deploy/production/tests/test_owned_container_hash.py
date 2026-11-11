"""Hash helper lifetime, without real credentials or a daemon."""
from contextlib import contextmanager
from copy import deepcopy
import importlib
from pathlib import Path
import sys
import unittest

from test_owned_children import Guard, Store

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class OwnedContainerHashTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('owned_container_hash')
        self.store, self.guard, self.calls = Store(), Guard(), []
        self.phase, self.fail, self.removed = 'created', None, False
        self.inherit_image = False
        self.hash = b'$2a$14$' + b'x' * 53 + b'\n'
        owner = self
        class API:
            def identity(self): return 'daemon-fixture'
            def _json(self, method, path, body=None, **kwargs):
                owner.calls.append((method, path, body))
                if '/images/' in path: return {'Config': {'Volumes': {'/data': {}, '/config': {}}}}
                if '/create?' in path:
                    owner.assertEqual('create-pending', owner.store.value['phase'])
                    owner.config = body
                    return {'Id': 'c' * 64, 'Warnings': []}
                if path.endswith('/start'):
                    owner.assertEqual('released', owner.store.value['phase'])
                    if owner.fail == 'start': raise OSError('unknown start')
                    owner.phase = 'exited'; return None
                if '/wait?' in path: return {'StatusCode': 0}
                if method == 'DELETE':
                    if owner.fail == 'remove': raise OSError('unknown removal')
                    owner.removed = True; return None
                if path.endswith('/json'):
                    config = deepcopy(owner.config)
                    if owner.inherit_image:
                        config['Labels']['org.opencontainers.image.title'] = 'fixture Caddy'
                        if not config['Cmd']: config['Cmd'] = ['caddy', 'run']
                    return {'Id': 'c' * 64, 'Name': '/' + owner.helper.name, 'Config': config,
                        'HostConfig': owner.config['HostConfig'], 'Mounts': [],
                        'State': {'Status': owner.phase, 'Running': False, 'ExitCode': 0, 'StartedAt': '2026-11-11T00:00:00Z'}}
                raise AssertionError('unexpected API call')
            @contextmanager
            def attach_input(self, identity):
                class Channel:
                    def finish(self, payload, limit):
                        owner.assertEqual(b'synthetic-test-password-32-characters\n', payload)
                        return owner.hash
                yield Channel()
            def container_absent(self, identity): return owner.removed
        self.helper = self.module.OwnedContainerHash(API(), self.store, self.guard,
            operation_id='00000000-0000-4000-8000-000000000001',
            installation_id='00000000-0000-4000-8000-000000000002',
            image='synthetic/caddy@sha256:' + 'a' * 64, target_sha256='b' * 64)

    def test_capture_id_before_start_then_delete_before_publishing_hash(self):
        value = self.helper.run(b'synthetic-test-password-32-characters')
        self.assertEqual(self.hash, value)
        self.assertTrue(self.removed)
        self.assertEqual('completed', self.store.value['phase'])
        self.assertFalse(any(b'synthetic-test-password' in str(call).encode() for call in self.calls))
        self.assertEqual('none', self.config['HostConfig']['NetworkMode'])

    def test_unknown_start_or_remove_keeps_pending_and_never_returns_hash(self):
        for fail in ('start', 'remove'):
            with self.subTest(fail=fail):
                self.setUp(); self.fail = fail
                with self.assertRaises(self.module.UncertainHashHelper):
                    self.helper.run(b'synthetic-test-password-32-characters')
                self.assertEqual('released', self.store.value['phase'])

    def test_invalid_hash_is_failed_only_after_verified_helper_removal(self):
        self.hash = b'partial'
        with self.assertRaises(self.module.KnownHashFailure):
            self.helper.run(b'synthetic-test-password-32-characters')
        self.assertTrue(self.removed)
        self.assertEqual('failed', self.store.value['phase'])

    def test_image_oci_labels_and_default_command_cannot_change_the_helper_program(self):
        self.inherit_image = True
        self.assertEqual(self.hash, self.helper.run(b'synthetic-test-password-32-characters'))
        self.assertEqual(['--algorithm', 'bcrypt'], self.config['Cmd'])


if __name__ == '__main__':
    unittest.main()
