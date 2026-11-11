"""Private receipts are necessary, terminal flags alone are not sufficient."""
import importlib
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RuntimeCohortTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('runtime_cohort')
        import runtime_ownership
        self.store_type = runtime_ownership.PrivateStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name).resolve()
        (self.target / '.surveyctl').mkdir(mode=0o700)
        self.lock = os.open(self.target / '.surveyctl/operation.lock', os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, self.lock)
        self.active, self.live, self.absent = True, {}, False
        owner = self
        class API:
            def identity(self): return 'daemon-fixture'
            def container_absent(self, identity): return owner.absent
            def inspect(self, identity):
                return {'ID': 'e' * 64, 'ContainerID': 'c' * 64, 'Running': False, 'ExitCode': 0}
        self.api = API()
        self.probe = self.module.CohortProbe(self.target, self.lock, lambda: self.active,
            api=self.api, fresh=True, _process_reader=lambda pid: self.live.get(pid))
        self.installation = '00000000-0000-4000-8000-000000000002'

    def owner(self):
        self.store_type(self.target).save({'schemaVersion': 1, 'project': 'survey-production',
            'installationId': self.installation, 'daemonId': 'daemon-fixture', 'phase': 'ready',
            'targetSha256': hashlib.sha256(str(self.target).encode()).hexdigest(), 'claimId': 'a' * 64,
            'image': 'synthetic/claim@sha256:' + 'b' * 64,
            'resources': {'containers': {}, 'networks': {}, 'volumes': {}}})

    def receipt(self, **updates):
        value = {'schemaVersion': 1, 'kind': 'controlled-docker-exec',
            'operationId': '00000000-0000-4000-8000-000000000001', 'installationId': self.installation,
            'owner': {'pid': 44, 'start': '440', 'parentPid': 33, 'parentStart': '330'},
            'daemonId': 'daemon-fixture', 'containerId': 'c' * 64, 'profile': 'exec-preflight',
            'phase': 'completed', 'execId': 'e' * 64, 'exitCode': 0}
        value.update(updates)
        self.store_type(self.target, name='runtime-exec-' + value['operationId'].replace('-', '') + '.json').save(value)

    def test_fresh_only_while_locked_and_without_legacy_state_or_receipts(self):
        self.assertEqual('quiescent', self.probe())
        (self.target / '.surveyctl/state.json').write_text('{}')
        self.assertEqual('unknown', self.probe())
        (self.target / '.surveyctl/state.json').unlink()
        self.active = False
        self.assertEqual('unknown', self.probe())

    def test_terminal_exec_needs_owner_ended_and_real_exec_end(self):
        self.owner(); self.receipt()
        self.assertEqual('quiescent', self.probe())
        self.live[44] = {'pid': 44, 'start': '440', 'parentPid': 33, 'state': 'S'}
        self.assertEqual('unknown', self.probe())
        self.live.clear()
        self.api.inspect = lambda identity: {'ID': 'e' * 64, 'ContainerID': 'c' * 64, 'Running': True, 'ExitCode': 0}
        self.assertEqual('unknown', self.probe())
        self.absent = True
        self.assertEqual('quiescent', self.probe())  # Exact original container removed; cannot run again.

    def test_pending_receipt_remains_unknown_even_after_container_absence(self):
        self.owner(); self.receipt(phase='released', exitCode=None)
        self.absent = True
        self.assertEqual('unknown', self.probe())

    def test_hash_helper_requires_terminal_receipt_and_original_container_absence(self):
        self.owner()
        value = {'schemaVersion': 1, 'kind': 'controlled-container-helper',
            'operationId': '00000000-0000-4000-8000-000000000001', 'installationId': self.installation,
            'owner': {'pid': 44, 'start': '440', 'parentPid': 33, 'parentStart': '330'},
            'daemonId': 'daemon-fixture', 'profile': 'caddy-hash',
            'image': 'caddy@sha256:' + 'd' * 64, 'phase': 'completed',
            'containerId': 'c' * 64, 'exitCode': 0}
        store = self.store_type(self.target, name='runtime-launch-' + value['operationId'].replace('-', '') + '.json')
        store.save(value)
        self.assertEqual('unknown', self.probe())  # Even a stopped helper could restart.
        self.absent = True
        self.assertEqual('quiescent', self.probe())
        for updates in ({'phase': 'released'}, {'exitCode': False}, {'exitCode': 1},
                        {'daemonId': 'other'}, {'profile': 'arbitrary'}, {'image': 'caddy:latest'},
                        {'containerId': 'not-an-id'}, {'unexpected': True}):
            store.save(dict(value, **updates))
            self.assertEqual('unknown', self.probe(), updates)
        store.save(dict(value, phase='failed', exitCode=126))
        self.assertEqual('quiescent', self.probe())  # Known failed/removed is ended, not a success.
        self.live[44] = {'pid': 44, 'start': '440', 'parentPid': 33, 'state': 'S'}
        self.assertEqual('unknown', self.probe())

    def test_wrong_installation_bool_status_unknown_files_and_replaced_lock_are_rejected(self):
        self.owner(); self.receipt(installationId='00000000-0000-4000-8000-000000000003')
        self.assertEqual('unknown', self.probe())
        self.receipt(exitCode=False)
        self.assertEqual('unknown', self.probe())
        self.receipt()
        (self.target / '.surveyctl/runtime-untracked.json').write_text('{}')
        self.assertEqual('unknown', self.probe())
        (self.target / '.surveyctl/runtime-untracked.json').unlink()
        path = self.target / '.surveyctl/operation.lock'
        path.unlink(); path.write_bytes(b''); path.chmod(0o600)
        self.assertEqual('unknown', self.probe())


if __name__ == '__main__':
    unittest.main()
