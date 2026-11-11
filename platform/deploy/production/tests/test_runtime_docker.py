"""Mutation dispatch cannot fall back to an untracked host CLI."""
import importlib
from pathlib import Path
import subprocess
import sys
import unittest

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
                if owner.fail: raise OSError('synthetic lost reply')
                return {'Id': 'c' * 64, 'Warnings': []} if method == 'POST' and '/create?' in path else None
        class Runner:
            def run(self, command, **kwargs):
                owner.cli.append(command)
                return subprocess.CompletedProcess(command, 0, 'null', '')
        self.fail = False
        self.driver = self.module.APIOwnershipDriver(Runner(), {'services': {'platform': {}},
            'networks': {}, 'volumes': {}}, lambda: 'quiescent', api=API())

    def test_stop_and_exact_removal_use_api_and_lost_reply_has_no_cli_fallback(self):
        for command in (['container', 'stop', '--time', '20', 'a' * 64], ['container', 'rm', 'a' * 64],
                ['network', 'rm', 'b' * 64], ['volume', 'rm', 'survey-production_data']):
            self.driver._query(command)
        self.assertEqual([], self.cli)
        self.assertTrue(self.calls[1][1].endswith('?force=false&v=false'))
        self.assertTrue(self.calls[-1][1].endswith('?force=false'))
        self.fail = True
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


if __name__ == '__main__':
    unittest.main()
