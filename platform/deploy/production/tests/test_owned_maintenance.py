"""Maintenance fault models; not native Docker or database evidence."""
from contextlib import contextmanager
from copy import deepcopy
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('owned_maintenance')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name).resolve()
        self.installation = '00000000-0000-4000-8000-000000000002'
        self.ids = {'edge': 'e' * 64, 'platform': 'a' * 64, 'platform-db': 'b' * 64}
        self.paused, self.events = set(), []
        self.pause_failure, self.resume_noop = None, False
        owner = self
        class Guard:
            def check(self): return {'pid': 44, 'start': '440', 'parentPid': 33, 'parentStart': '330'}
        class API:
            VERSION = '/v1.47'
            def identity(self): return 'fixture-daemon'
            def _json(self, method, path, **kwargs):
                cid = path.split('/')[2]
                if method == 'GET':
                    return {'Id': cid, 'State': {'Running': True, 'Paused': cid in owner.paused,
                        'Health': {'Status': 'healthy'}}}
                owner.events.append(path)
                if path.endswith('/pause'):
                    owner.paused.add(cid)
                    if cid == owner.pause_failure: raise OSError('lost pause reply')
                elif path.endswith('/unpause') and not owner.resume_noop: owner.paused.discard(cid)
                return None
        class Driver:
            CONTAINER = 'fixed-fixture-projection'
            def _container(self, cid, **kwargs):
                self._last_container_snapshot = self._json(['fixture-projection', cid])
                return cid, cid in owner.paused, 'running'
            def _json(self, args):
                cid = args[-1]
                return {'id': cid, 'service': next(k for k, v in owner.ids.items() if v == cid),
                    'paused': cid in owner.paused, 'execs': []}
        record = {'installationId': self.installation, 'daemonId': 'fixture-daemon',
                  'resources': {'containers': {v: v for v in self.ids.values()}}}
        self.manager = SimpleNamespace(target=self.target, _managed=lambda name, **kwargs: self.target / name)
        self.runtime = SimpleNamespace(manager=self.manager, data={'version': '0.2.0',
            'contract': {'services': {k: {} for k in self.ids}}}, driver=Driver(),
            probe=SimpleNamespace(api=API()), preflight_cleanup=lambda purge: deepcopy(record))
        self.runtime._private_bytes = lambda path, maximum: b'x' * 64
        self.operation = self.module.OwnedMaintenance(self.runtime, _guard=Guard())
        self.operation.sleep = lambda seconds: None

    def test_partial_pause_lost_reply_still_resumes_every_planned_writer(self):
        self.pause_failure = self.ids['platform']
        with self.assertRaises(OSError):
            with self.operation.frozen_writers(): pass
        self.assertEqual(set(), self.paused)
        self.assertIn('/containers/' + self.ids['edge'] + '/unpause', self.events)
        self.assertIn('/containers/' + self.ids['platform'] + '/unpause', self.events)
        self.assertTrue(self.operation.uncertain)  # A lost RPC reply is not proved ended by a later snapshot.

    def test_resume_zero_reply_without_state_change_is_not_success(self):
        self.resume_noop = True
        with self.assertRaises(self.module.UncertainMaintenance):
            with self.operation.frozen_writers(): pass
        self.assertTrue(self.paused)
        self.assertTrue(self.operation.uncertain)

    def test_recorded_pause_uses_docker_paused_status_and_still_resumes(self):
        observe = self.runtime.driver._container
        def actual_status(cid, **kwargs):
            fingerprint, unknown, _ = observe(cid, **kwargs)
            return fingerprint, unknown, 'paused' if cid in self.paused else 'running'
        self.runtime.driver._container = actual_status
        with self.operation.frozen_writers():
            self.assertTrue(self.paused)
            for cid in self.paused:
                self.assertFalse(self.operation.container_check(cid))
                self.assertTrue(self.operation.container_check(cid, allow_paused=True))
        self.assertEqual(set(), self.paused)
        self.assertFalse(self.operation.uncertain)
        self.paused.add(self.ids['platform'])  # External pause was never registered by this operation.
        self.assertFalse(self.operation.container_check(self.ids['platform'], allow_paused=True))

    def test_foreign_exec_or_replaced_fingerprint_is_refused(self):
        self.assertTrue(self.operation.container_check(self.ids['platform-db']))
        self.runtime.driver._container = lambda cid, **kwargs: ('different', False, 'running')
        self.assertFalse(self.operation.container_check(self.ids['platform-db']))

    def test_only_the_one_recorded_exec_may_be_present(self):
        raw = self.runtime.driver._json
        self.runtime.driver._json = lambda args: dict(raw(args), execs=['c' * 64])
        self.assertFalse(self.operation.container_check(self.ids['platform-db']))
        self.assertTrue(self.operation.container_check(self.ids['platform-db'], 'c' * 64))
        self.assertFalse(self.operation.container_check(self.ids['platform-db'], 'd' * 64))

    def test_archive_requires_http_complete_length_and_is_bounded(self):
        class Response(io.BytesIO):
            status = 200
            def getheader(self, name): return {'Content-Length': '8'}.get(name)
        class Connection:
            def request(self, method, path): pass
            def getresponse(self): return Response(b'short')
            def close(self): pass
        self.runtime.probe.api._connect = lambda: Connection()
        output = io.BytesIO()
        with self.assertRaises(self.module.UncertainMaintenance):
            self.operation.archive(self.ids['platform'], '/var/lib/survey/assets', output, 20)
        self.assertEqual(b'short', output.getvalue())

    def test_probe_close_failure_does_not_skip_project_archive(self):
        import doctor
        events = []
        class Probe(self.module.OwnedProductProbe):
            def _configuration(self): return {}
            def _token(self, config): return 'synthetic'
            def _answer(self, sid): pass
            def _api(self, token, method, path, body=None, raw=False):
                events.append(path)
                if path == '/v1/resources?limit=1': return 200, {}
                if path == '/v1/projects': return 201, {'id': 'project'}
                if path == '/v1/surveys': return 201, {'id': 'survey'}
                if path.endswith('/publish'): return 200, {'version': {'engineSid': 123, 'engineInstanceId': 'engine-01'}}
                if path.endswith('/archive'): return 200, {}
                raise AssertionError('unexpected synthetic route')
        class Runner:
            def run(self, *args, **kwargs): raise OSError('synthetic event failure')
        def fail(*args, **kwargs): raise OSError('synthetic close failure')
        self.operation.exec_bytes = fail
        probe = Probe(self.operation, 'fixture.invalid', ['compose'], Runner())
        with self.assertRaises(doctor.ProbeStageError): probe.run()
        self.assertIn('/v1/resources/project/archive', events)

    def test_first_install_backup_warning_retains_native_acceptance_contract(self):
        doctor = object.__new__(self.module.OwnedDoctor)
        doctor.target = self.target
        self.assertEqual({'id': 'backups', 'status': 'warning', 'summary': 'no completed backup exists yet'},
                         doctor._backup_check())

    def test_first_backup_creates_private_parent_inside_the_operation(self):
        events = []
        def mkdir(name):
            self.assertEqual(['operation'], events)
            events.append('mkdir')
            (self.target / name).mkdir(mode=0o700)
        self.manager._mkdir = mkdir
        def perform(kind, effect):
            events.append('operation')
            return effect()
        self.operation._perform = perform
        with patch.object(self.module.BackupManager, '_state', side_effect=BackupErrorForTest()):
            with self.assertRaises(BackupErrorForTest): self.operation.backup(self.target / 'backups/first')
        self.assertEqual(['operation', 'mkdir'], events)
        self.assertEqual(0o700, (self.target / 'backups').stat().st_mode & 0o777)

    def test_known_low_disk_preflight_starts_no_mutation_or_directory(self):
        with patch.object(self.module.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)), \
             patch.object(self.operation, '_perform') as effect:
            with self.assertRaises(self.module.BackupError):
                self.operation.preflight_backup(self.target / 'backups/first')
            effect.assert_not_called()
        self.assertFalse((self.target / 'backups').exists())

    def test_unknown_operation_cannot_launch_another_exec_or_plaintext_session(self):
        self.operation.uncertain = True
        with patch.object(self.module, 'AnonymousWorkspace') as workspace, \
             patch.object(self.module, 'OwnedDockerExec') as executor:
            with self.assertRaises(self.module.UncertainMaintenance):
                self.operation.exec_bytes('postgres-schema', 'platform-db')
            with self.assertRaises(self.module.UncertainMaintenance):
                with self.operation.cipher_session(self.target): pass
            with self.assertRaises(self.module.UncertainMaintenance):
                self.operation.exec_file('postgres-dump', 'platform-db', None)
            workspace.assert_not_called()
            executor.assert_not_called()


class BackupErrorForTest(Exception):
    pass


if __name__ == '__main__': unittest.main()
