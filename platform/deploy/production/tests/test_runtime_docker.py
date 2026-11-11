"""Mutation dispatch cannot fall back to an untracked host CLI."""
import importlib
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RuntimeDockerTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('runtime_docker')
        self.calls, self.cli = [], []
        owner = self
        class API:
            _id = staticmethod(self.module.LocalDockerAPI._id)
            def _json(self, method, path, body=None, **kwargs):
                owner.calls.append((method, path, body, kwargs))
                if owner.api_failure: raise OSError('synthetic lost reply')
                return {'Id': 'c' * 64, 'Warnings': []} if method == 'POST' and '/create?' in path else None
        class Runner:
            def run(self, command, **kwargs):
                owner.cli.append(command)
                return subprocess.CompletedProcess(command, 0, 'null', '')
        self.api_failure = False
        self.driver = self.module.APIOwnershipDriver(Runner(), {'services': {'platform': {}},
            'networks': {}, 'volumes': {}}, lambda: 'quiescent', api=API())

    def test_stop_and_exact_removal_use_api_and_lost_reply_has_no_cli_fallback(self):
        for command in (['container', 'stop', '--time', '20', 'a' * 64], ['container', 'rm', 'a' * 64],
                ['network', 'rm', 'b' * 64], ['volume', 'rm', 'survey-production_data']):
            self.driver._query(command)
        self.assertEqual([], self.cli)
        self.assertTrue(self.calls[1][1].endswith('?force=false&v=false'))
        self.assertTrue(self.calls[-1][1].endswith('?force=false'))
        self.api_failure = True
        with self.assertRaises(OSError): self.driver._query(['container', 'rm', 'a' * 64])
        self.assertEqual([], self.cli)

    def test_unsupported_force_or_foreign_volume_is_never_sent(self):
        for command in (['container', 'rm', '--force', 'a' * 64], ['volume', 'rm', 'other_data'],
                ['container', 'create', 'anything']):
            with self.assertRaises(self.module.ownership.OwnershipError): self.driver._query(command)
        self.assertEqual([], self.calls)
        self.assertEqual([], self.cli)

    def test_claim_uses_no_implicit_volume_or_running_workload(self):
        binding = {'daemonId': 'fixture', 'installationId': '00000000-0000-4000-8000-000000000001',
            'targetSha256': 'a' * 64}
        result = self.driver.create_claim(self.module.ownership.CLAIM_NAME, binding,
            'synthetic/image@sha256:' + 'b' * 64)
        self.assertEqual('c' * 64, result)
        self.assertEqual(1, len(self.cli))  # Read-only image VOLUME projection only.
        body = self.calls[0][2]
        self.assertEqual('none', body['HostConfig']['NetworkMode'])
        self.assertNotIn('Mounts', body['HostConfig'])
        self.assertNotIn('Volumes', body)
        self.assertEqual(['/bin/true'], body['Entrypoint'])

    def test_idle_snapshot_can_follow_a_finished_transient_exec_but_never_adopts_it(self):
        cid, eid = 'c' * 64, 'e' * 64
        snapshots = [[eid], []]
        def sample(identity):
            execs = snapshots.pop(0)
            self.driver._last_container_snapshot = {'id': cid, 'execs': execs, 'paused': False, 'status': 'running'}
            return 'f' * 64, bool(execs), 'running'
        self.driver.idle_sleep = lambda seconds: None
        with patch.object(self.module.ownership.DockerOwnershipDriver, '_container', side_effect=sample) as query:
            self.assertEqual(('f' * 64, False, 'running'), self.driver._container(cid))
            self.assertEqual(2, query.call_count)
        self.assertEqual([], self.calls)

    def test_active_or_paused_unknown_exec_never_becomes_an_idle_snapshot(self):
        cid, eid = 'c' * 64, 'e' * 64
        for paused in (False, True):
            def sample(identity):
                self.driver._last_container_snapshot = {'id': cid, 'execs': [eid], 'paused': paused, 'status': 'running'}
                return 'f' * 64, True, 'running'
            self.driver.idle_sleep = lambda seconds: None
            with patch.object(self.module.ownership.DockerOwnershipDriver, '_container', side_effect=sample) as query:
                self.assertTrue(self.driver._container(cid)[1])
                self.assertLessEqual(query.call_count, 50)
                if paused: self.assertEqual(1, query.call_count)
        self.assertEqual([], self.calls)

    def test_expected_created_exec_does_not_deadlock_waiting_for_its_own_start(self):
        cid, eid = 'c' * 64, 'e' * 64
        def sample(identity):
            self.driver._last_container_snapshot = {'id': cid, 'execs': [eid], 'paused': False, 'status': 'running'}
            return 'f' * 64, True, 'running'
        with patch.object(self.module.ownership.DockerOwnershipDriver, '_container', side_effect=sample) as query:
            self.assertTrue(self.driver._container(cid, allowed_exec=eid)[1])
            self.assertEqual(1, query.call_count)

    def test_pending_private_cohort_is_not_cleared_by_idle_container_snapshots(self):
        self.driver.probe = lambda: 'unknown'
        self.driver._rows = lambda kind: []
        self.assertEqual('unknown', self.driver.observe()['cohort'])
        self.assertEqual([], self.calls)


if __name__ == '__main__':
    unittest.main()
