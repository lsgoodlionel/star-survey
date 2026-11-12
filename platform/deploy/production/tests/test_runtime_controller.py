"""Source contract + protocol integration with an explicitly fake daemon."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_runtime_ownership import Daemon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMAGE = 'synthetic/admin@sha256:' + 'a' * 64


class RuntimeControllerTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('runtime_controller')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name).resolve()
        self.control = self.target / '.surveyctl'; self.control.mkdir(mode=0o700)
        self.release = self.target / 'releases/0.2.0'; self.release.mkdir(parents=True)
        for name in ('compose.yml', '.env'): (self.release / name).write_text('synthetic')
        self.lock = os.open(self.control / 'operation.lock', os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, self.lock)
        self.config = {'services': {'platform': {'image': IMAGE, 'volumes': [
            {'type': 'volume', 'source': 'data', 'target': '/state'}]}},
            'networks': {'internal': {'name': 'survey-production_internal', 'internal': True}},
            'volumes': {'data': {'name': 'survey-production_data'}}}
        owner = self
        class Runner:
            def run(self, command, **kwargs):
                stdout = (json.dumps(owner.config) if 'config' in command else
                    'null' if '{{json .Config.Volumes}}' in command else json.dumps('sha256:' + 'b' * 64))
                return subprocess.CompletedProcess(command, 0, stdout, '')
        self.manager = SimpleNamespace(target=self.target, control_dir=self.control,
            state_path=self.control / 'state.json', current_path=self.target / 'current',
            runner=Runner(), _runtime_lock_fd=self.lock, _mutation_depth=1, _operation_name='install',
            _release_dir=lambda version: self.target / 'releases' / version,
            _verify_staged_release=lambda path, version: None,
            _release_manifest=lambda version: SimpleNamespace(raw={'images': {'ADMIN_IMAGE': IMAGE}}, sha256='c' * 64),
            _compose=lambda version, **kwargs: ['docker', 'compose', 'config-source'])
        self.daemon = Daemon()
        class API:
            def identity(self): return 'daemon-fixture'
        self.api = API()
        def driver(runner, contract, probe):
            self.daemon.contract, self.daemon.probe = contract, probe
            return self.daemon
        self.driver = driver
        self.original_observe = self.daemon.observe
        def observe():
            result = self.original_observe(); result['cohort'] = self.daemon.probe(); return result
        self.daemon.observe = observe

    def prepare(self):
        return self.module.RuntimeController.prepare(self.manager, '0.2.0',
            _driver_factory=self.driver, _api=self.api)

    def test_new_source_contract_labels_and_reload_bind_actual_installation(self):
        runtime = self.prepare()
        def created():
            self.daemon.snapshot.update(containers={'1' * 64: 'a' * 64},
                networks={'2' * 64: 'b' * 64}, volumes={'survey-production_data': 'd' * 64})
        runtime.install(created)
        contract = json.loads((self.control / 'runtime-contract.json').read_text())
        overlay = json.loads((self.control / 'runtime-owner.compose.json').read_text())
        self.assertEqual(contract['installationId'], overlay['services']['platform']['labels']['io.survey.installation'])
        loaded = self.module.RuntimeController.open(self.manager, '0.2.0', _driver_factory=self.driver, _api=self.api)
        loaded.preflight_cleanup(False)
        loaded.cleanup(False)
        self.assertEqual('retained-data', loaded.store.load()['phase'])
        self.assertTrue(self.daemon.snapshot['volumes'])
        self.assertFalse(self.daemon.snapshot['containers'])

    def test_reinstall_binds_target_contract_and_generation_before_hash_admission(self):
        for purge in (False, True):
            with self.subTest(purge=purge):
                if purge: self.setUp()
                runtime = self.prepare()
                def created():
                    self.daemon.snapshot.update(containers={'1' * 64: 'a' * 64},
                        networks={'2' * 64: 'b' * 64}, volumes={'survey-production_data': 'd' * 64})
                runtime.install(created)
                old = runtime.store.load()
                runtime.cleanup(purge)
                release = self.target / 'releases/0.3.0'; release.mkdir()
                for name in ('compose.yml', '.env'): (release / name).write_text('new-source')
                prepared = self.module.RuntimeController.prepare(self.manager, '0.3.0',
                    _driver_factory=self.driver, _api=self.api)
                # ensure_admin_hash calls this exact binding check before a helper.
                bound = self.module.RuntimeController._load(self.manager, '0.3.0')
                self.assertEqual(prepared.store.load()['installationId'], bound['installationId'])
                self.assertEqual('0.3.0', prepared.data['version'])
                if purge:
                    self.assertNotEqual(old['installationId'], bound['installationId'])
                    self.assertIn(old['installationId'], prepared.store.load()['retiredInstallations'])
                else:
                    self.assertEqual(old['installationId'], bound['installationId'])
                    self.assertEqual(old['resources']['volumes'], prepared.store.load()['resources']['volumes'])
                self.assertFalse(self.daemon.snapshot['containers'])
                def restarted():
                    self.daemon.snapshot.update(containers={'3' * 64: 'b' * 64},
                        networks={'4' * 64: 'c' * 64}, volumes={'survey-production_data': 'd' * 64})
                prepared.install(restarted)
                self.assertEqual('ready', prepared.store.load()['phase'])
                self.assertEqual({'3' * 64: 'b' * 64}, prepared.store.load()['resources']['containers'])
                self.assertEqual(bound, self.module.RuntimeController._load(self.manager, '0.3.0'))

    def test_reinstall_prechecks_preserve_existing_ledger_claim_and_resources_on_rejection(self):
        for failure in ('active', 'candidate', 'network', 'volume', 'query'):
            with self.subTest(failure=failure):
                if failure != 'active': self.setUp()
                runtime = self.prepare()
                runtime.install(lambda: self.daemon.snapshot.update(volumes={'survey-production_data': 'd' * 64}))
                if failure != 'active': runtime.cleanup(False)
                release = self.target / 'releases/0.3.0'; release.mkdir()
                for name in ('compose.yml', '.env'): (release / name).write_text('new-source')
                paths = [self.control / name for name in
                    ('runtime-owner.json', 'runtime-contract.json', 'runtime-owner.compose.json')]
                before = {path: path.read_bytes() for path in paths}
                claims, snapshot = deepcopy(self.daemon.claims), deepcopy(self.daemon.snapshot)
                if failure == 'candidate':
                    original = self.manager._verify_staged_release
                    def verify(path, version):
                        if version == '0.3.0': raise self.module.ownership.OwnershipError('candidate verification failed')
                        return original(path, version)
                    self.manager._verify_staged_release = verify
                elif failure == 'network': self.config['networks']['internal']['internal'] = False
                elif failure == 'volume': self.config['volumes']['other'] = {'name': 'survey-production_other'}
                elif failure == 'query': self.daemon.fail_query = True
                with self.assertRaises(self.module.ownership.OwnershipError):
                    self.module.RuntimeController.prepare(self.manager, '0.3.0',
                        _driver_factory=self.driver, _api=self.api)
                self.assertEqual(before, {path: path.read_bytes() for path in paths})
                self.assertEqual(claims, self.daemon.claims)
                self.assertEqual(snapshot, self.daemon.snapshot)

    def test_invalid_purge_reinstall_candidate_cannot_acquire_a_new_generation(self):
        runtime = self.prepare(); runtime.install(lambda: None); runtime.cleanup(True)
        before = runtime.store.load(); count = self.daemon.created_claims
        with patch.object(self.module, 'source_contract', side_effect=self.module.ownership.OwnershipError('bad target')):
            with self.assertRaises(self.module.ownership.OwnershipError):
                self.module.RuntimeController.prepare(self.manager, '0.3.0',
                    _driver_factory=self.driver, _api=self.api)
        self.assertEqual(before, runtime.store.load())
        self.assertEqual(count, self.daemon.created_claims)
        self.assertFalse(self.daemon.claims)

    def test_partial_target_binding_failure_cannot_admit_a_helper_or_runtime(self):
        runtime = self.prepare(); runtime.install(lambda: None); runtime.cleanup(False)
        release = self.target / 'releases/0.3.0'; release.mkdir()
        for name in ('compose.yml', '.env'): (release / name).write_text('new-source')
        self.config['services']['new-platform'] = self.config['services'].pop('platform')
        original = self.module.ownership.PrivateStore.save
        def save(store, value):
            if store.NAME == 'runtime-owner.compose.json': raise OSError('fixture persistence failure')
            return original(store, value)
        with patch.object(self.module.ownership.PrivateStore, 'save', save):
            with self.assertRaises(OSError):
                self.module.RuntimeController.prepare(self.manager, '0.3.0',
                    _driver_factory=self.driver, _api=self.api)
        with self.assertRaises(self.module.ownership.OwnershipError):
            self.module.RuntimeController._load(self.manager, '0.3.0')
        self.assertFalse(self.daemon.snapshot['containers'])
        self.assertEqual('retained-data', runtime.store.load()['phase'])

    def test_source_drift_and_legacy_state_never_authorize_cleanup_or_adoption(self):
        (self.control / 'state.json').write_text('{"status":"install_failed"}')
        with self.assertRaises(self.module.ownership.OwnershipError): self.prepare()
        self.assertEqual({}, self.daemon.claims)
        (self.control / 'state.json').unlink()
        runtime = self.prepare()
        (self.release / '.env').write_text('changed')
        with self.assertRaises(self.module.ownership.OwnershipError):
            self.module.RuntimeController.open(self.manager, '0.2.0', _driver_factory=self.driver, _api=self.api)
        self.assertEqual([], self.daemon.remove_calls)

    def test_external_volume_or_outside_bind_is_refused_before_claim(self):
        for change in ('external', 'bind'):
            with self.subTest(change=change):
                if change == 'external': self.config['volumes']['data']['external'] = True
                else:
                    self.config['volumes']['data'].pop('external', None)
                    self.config['services']['platform']['volumes'] = [{'type': 'bind', 'source': '/outside', 'target': '/state'}]
                with self.assertRaises(self.module.ownership.OwnershipError): self.prepare()
                self.assertEqual({}, self.daemon.claims)

    def test_hash_bridge_uses_bound_image_and_retains_unknown_helper_failure(self):
        runtime = self.prepare()
        secret = self.target / 'shared/secrets'
        secret.mkdir(parents=True, mode=0o700)
        password = secret / 'engine_admin_password'
        password.write_bytes(b'x' * 64 + b'\n'); password.chmod(0o600)
        self.manager._managed = lambda name, **kwargs: self.target / name
        def atomic_fixture(path, value):
            path.write_text(value)
            path.chmod(0o600)
        self.manager._atomic_write = atomic_fixture
        sync = []
        self.manager._sync_runtime_secrets = lambda: sync.append(True)
        self.manager._release_manifest = lambda version: SimpleNamespace(raw={'images': {
            'ADMIN_IMAGE': IMAGE, 'CADDY_IMAGE': 'caddy@sha256:' + 'd' * 64}}, sha256='c' * 64)
        calls = []
        class Helper:
            def __init__(self, api, store, guard, **kwargs):
                calls.append((api, store, kwargs))
            def run(self, value):
                if value != b'x' * 64: raise AssertionError('wrong synthetic input')
                raise RuntimeError('synthetic unknown result')
        # Bridge-only unit test; a real Linux guard/helper is separately needed.
        with patch('owned_children.ControllerProcessGuard'), patch('owned_container_hash.OwnedContainerHash', Helper):
            with self.assertRaisesRegex(RuntimeError, 'synthetic unknown result'): runtime.ensure_admin_hash()
        self.assertEqual('caddy@sha256:' + 'd' * 64, calls[0][2]['image'])
        self.assertEqual(runtime.store.load()['installationId'], calls[0][2]['installation_id'])
        self.assertEqual([], sync)
        self.assertFalse((secret / 'engine_admin_password_hash').exists())
        self.assertEqual('ready', runtime.store.load()['phase'])
        class CompletedHelper(Helper):
            def run(self, value): return b'$2a$14$' + b'x' * 53 + b'\n'
        with patch('owned_children.ControllerProcessGuard'), patch('owned_container_hash.OwnedContainerHash', CompletedHelper):
            runtime.ensure_admin_hash()
        self.assertEqual(b'$2a$14$' + b'x' * 53 + b'\n', (secret / 'engine_admin_password_hash').read_bytes())
        self.assertEqual([True], sync)
        with patch('owned_container_hash.OwnedContainerHash') as helper:
            runtime.ensure_admin_hash()
            helper.assert_not_called()
        self.assertEqual([True, True], sync)

    def test_hash_bridge_rejects_fifo_and_invalid_existing_hash_without_launch(self):
        runtime = self.prepare()
        secret = self.target / 'shared/secrets'
        secret.mkdir(parents=True, mode=0o700)
        path = secret / 'engine_admin_password_hash'
        self.manager._managed = lambda name, **kwargs: self.target / name
        for special in (False, True):
            if special: os.mkfifo(path, 0o600)
            else: path.write_bytes(b'invalid\n'); path.chmod(0o600)
            with patch('owned_container_hash.OwnedContainerHash') as helper:
                with self.assertRaises(self.module.ownership.OwnershipError): runtime.ensure_admin_hash()
                helper.assert_not_called()
            path.unlink()


if __name__ == '__main__':
    unittest.main()
