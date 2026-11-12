"""Scoped protocol/adapter models; not a recovery executor or native proof."""
from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import test_runtime_ownership as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RecoveryOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('runtime_ownership')
        self.project = 'survey-recovery-' + 'a' * 32
        self.other = 'survey-recovery-' + 'b' * 32
        self.contract = {'services': {'sentinel': {'imageIds': ['sha256:' + '1' * 64],
            'workingDirs': ['/fixture/new'], 'mounts': []}},
            'networks': {self.project + '_net': {'logical': 'net', 'driver': 'bridge', 'internal': True}},
            'volumes': {self.project + '_data': {'logical': 'data', 'driver': 'local'}}}

    def protocol(self, project=None, store=None, daemon=None):
        daemon = daemon or fixtures.Daemon(); daemon.project = project or self.project
        return self.module.OwnershipProtocol('/fixture/new', daemon, store or fixtures.Store(),
            'example/claim@sha256:' + '1' * 64, project=project or self.project)

    def test_claim_and_record_are_bound_to_explicit_project_without_changing_globals(self):
        protocol = self.protocol(); protocol.acquire()
        self.assertEqual(self.project, protocol.store.record['project'])
        self.assertEqual(self.project + '-ownership-v1', next(iter(protocol.driver.claims.values()))['name'])
        self.assertEqual('survey-production', self.module.PROJECT)
        self.assertEqual('survey-production-ownership-v1', self.module.CLAIM_NAME)

    def test_cross_project_record_or_volume_is_never_adopted_or_deleted(self):
        protocol = self.protocol(); protocol.acquire()
        protocol.store.record['project'] = self.other
        with self.assertRaises(self.module.OwnershipError): protocol.preflight_cleanup(True)
        self.assertFalse(protocol.driver.remove_calls)
        with self.assertRaises(self.module.OwnershipError):
            protocol._resources({'containers': {}, 'networks': {}, 'volumes': {'survey-production_data': '1' * 64}})

    def test_later_global_change_does_not_redirect_an_existing_instance(self):
        protocol = self.protocol()
        with mock.patch.object(self.module, 'PROJECT', self.other), mock.patch.object(self.module, 'CLAIM_NAME', self.other + '-ownership-v1'):
            protocol.acquire()
            self.assertEqual(self.project, protocol.store.record['project'])
            self.assertEqual(self.project + '-ownership-v1', next(iter(protocol.driver.claims.values()))['name'])

    def test_bad_project_and_mismatched_adapter_fail_before_claim_creation(self):
        for name in ('survey-recovery-', 'survey-recovery-' + 'A' * 32, 'survey-production-old',
                     '../survey-recovery-' + 'a' * 32, True):
            with self.subTest(name=name), self.assertRaises(self.module.OwnershipError): self.protocol(project=name)
        daemon = fixtures.Daemon(); daemon.project = self.other
        with self.assertRaises(self.module.OwnershipError):
            self.module.OwnershipProtocol('/fixture/new', daemon, fixtures.Store(),
                'example/claim@sha256:' + '1' * 64, project=self.project)
        self.assertEqual(0, daemon.created_claims)
        unbound = fixtures.Daemon()
        with self.assertRaises(self.module.OwnershipError):
            self.module.OwnershipProtocol('/fixture/new', unbound, fixtures.Store(),
                'example/claim@sha256:' + '1' * 64, project=self.project)

    def test_unknown_create_remains_pending_without_replay_or_cleanup(self):
        protocol = self.protocol(); protocol.driver.unknown_create = True
        with self.assertRaises(self.module.OwnershipError): protocol.acquire()
        self.assertEqual('claim-pending', protocol.store.record['phase'])
        with self.assertRaises(self.module.OwnershipError): protocol.acquire()
        self.assertEqual(1, protocol.driver.created_claims)
        self.assertFalse(protocol.driver.remove_calls)

    def driver(self, contract=None, project=None):
        owner = self
        self.calls = []
        class Runner:
            def run(self, command, **kwargs):
                owner.calls.append(command)
                return subprocess.CompletedProcess(command, 0, '', '')
        return self.module.DockerOwnershipDriver(Runner(), contract or self.contract,
            lambda: 'quiescent', project=project or self.project)

    def test_recovery_contract_rejects_external_volumes_bind_paths_and_public_network(self):
        cases = []
        value = deepcopy(self.contract); value['volumes']['survey-production_data'] = value['volumes'].pop(self.project + '_data'); cases.append(value)
        value = deepcopy(self.contract); value['services']['sentinel']['mounts'] = [{'type': 'volume', 'name': 'survey-production_data', 'target': '/data', 'readOnly': False}]; cases.append(value)
        value = deepcopy(self.contract); value['services']['sentinel']['mounts'] = [{'type': 'bind', 'source': '/source', 'target': '/data', 'readOnly': True}]; cases.append(value)
        value = deepcopy(self.contract); value['networks'][self.project + '_net']['internal'] = False; cases.append(value)
        for contract in cases:
            with self.subTest(contract=contract), self.assertRaises(self.module.OwnershipError): self.driver(contract)

    def test_inventory_does_not_absorb_neighbour_project_or_hide_stopped_containers(self):
        driver = self.driver()
        rows = [{'id': '1' * 64, 'name': self.project + '-sentinel-1', 'project': self.project},
                {'id': '2' * 64, 'name': self.other + '-sentinel-1', 'project': self.other},
                {'id': '3' * 64, 'name': 'survey-production-platform-1', 'project': 'survey-production'}]
        seen = []
        def query(args): seen.append(args); return '\n'.join(json.dumps(row) for row in rows)
        driver._query = query
        self.assertEqual([rows[0]], driver._rows('containers'))
        self.assertIn('--all', seen[0])

    def test_api_claim_and_volume_mutations_are_scoped_and_reject_source_names(self):
        module = importlib.import_module('runtime_docker')
        calls = []
        class API:
            def _json(self, method, path, body=None, **kwargs):
                calls.append((method, path, body)); return {'Id': 'c' * 64, 'Warnings': []}
        driver = module.APIOwnershipDriver(object(), self.contract, lambda: 'quiescent',
            project=self.project, api=API())
        driver._json = lambda args: None
        binding = {'daemonId': 'fixture', 'installationId': 'a', 'targetSha256': 'b'}
        with self.assertRaises(self.module.OwnershipError):
            driver.create_claim('survey-production-ownership-v1', binding, 'example/claim@sha256:' + '1' * 64)
        with self.assertRaises(self.module.OwnershipError): driver._query(['volume', 'rm', 'survey-production_data'])
        self.assertEqual([], calls)
        driver.create_claim(self.project + '-ownership-v1', binding, 'example/claim@sha256:' + '1' * 64)
        self.assertEqual('/containers/create?name=' + self.project + '-ownership-v1', calls[0][1])
        driver._query(['volume', 'rm', self.project + '_data'])
        self.assertEqual(('DELETE', '/volumes/' + self.project + '_data' + '?force=false'), calls[-1][:2])

    def test_native_cleanup_inventory_rejects_malformed_volume_result_and_checks_all_kinds(self):
        path = Path(__file__).parent / 'integration/recovery_namespace_native.py'
        spec = importlib.util.spec_from_file_location('recovery_namespace_probe', path)
        probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
        for volume in ({}, {'Volumes': [], 'Warnings': ['unknown']}, {'Volumes': 'invalid', 'Warnings': None}):
            calls = []
            class API:
                def _json(self, method, route):
                    calls.append(route)
                    if route.startswith('/containers'): raise OSError('unavailable')
                    return volume if route.startswith('/volumes') else []
            with self.subTest(volume=volume):
                self.assertFalse(probe.empty_namespace(API(), self.project))
                self.assertEqual(3, len(calls))

    def test_recovery_namespace_still_does_not_enable_restore_operation(self):
        protocol = self.protocol(); protocol.acquire()
        effects = []
        with self.assertRaises(self.module.OwnershipError): protocol.mutate('restore', lambda: effects.append(True))
        self.assertEqual([], effects)
        self.assertEqual('ready', protocol.store.record['phase'])


if __name__ == '__main__': unittest.main()
