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


if __name__ == '__main__':
    unittest.main()
