"""Protocol fault injection; fake daemon observations are not native evidence."""
import copy
import importlib.util
import json
import os
import stat
import tempfile
from unittest import mock
from pathlib import Path
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

MODULE = Path(__file__).resolve().parents[1] / 'runtime_ownership.py'


class Store:
    def __init__(self):
        self.record = None
        self.history = []
    def load(self):
        return copy.deepcopy(self.record)
    def save(self, value):
        self.record = copy.deepcopy(value)
        self.history.append(copy.deepcopy(value))


class Daemon:
    def __init__(self):
        self.lock = threading.Lock()
        self.claims = {}
        self.snapshot = {'containers': {}, 'networks': {}, 'volumes': {}, 'cohort': 'quiescent', 'foreign': False}
        self.fail_query = False
        self.unknown_create = False
        self.keep_resources = False
        self.remove_calls = []
        self.claim_deletes = []
    def identity(self):
        if self.fail_query: raise OSError('synthetic unavailable')
        return 'daemon-fixture'
    def observe(self):
        if self.fail_query: raise OSError('synthetic unavailable')
        return copy.deepcopy(self.snapshot)
    def create_claim(self, name, binding, image):
        with self.lock:
            if self.claims: raise FileExistsError('claim occupied')
            claim = {'id': 'c' * 64, 'name': name, 'binding': copy.deepcopy(binding),
                     'image': image, 'stopped': True, 'isolated': True}
            self.claims[claim['id']] = claim
            if self.unknown_create: raise TimeoutError('lost response after create')
            return claim['id']
    def inspect_claim(self, identity):
        if self.fail_query: raise OSError('synthetic unavailable')
        return copy.deepcopy(self.claims.get(identity))
    def delete_resources(self, expected, purge):
        self.remove_calls.append((copy.deepcopy(expected), purge))
        if not self.keep_resources:
            self.snapshot['containers'] = {}
            self.snapshot['networks'] = {}
            if purge: self.snapshot['volumes'] = {}
    def delete_claim(self, identity):
        self.claim_deletes.append(identity)
        self.claims.pop(identity)


class OwnershipProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('ownership_protocol', MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
    def setUp(self):
        self.store, self.daemon = Store(), Daemon()
        self.protocol = self.make('/fixture/primary', self.store)
    def make(self, target, store):
        return self.module.OwnershipProtocol(target, self.daemon, store, 'example/image@sha256:' + 'a' * 64)
    def acquire(self):
        self.protocol.acquire()
    def populate(self):
        self.acquire()
        def create():
            self.daemon.snapshot.update(containers={'1' * 64: 'a' * 64},
                networks={'2' * 64: 'b' * 64}, volumes={'survey-production_data': 'd' * 64})
        self.protocol.mutate('install', create)
    def test_claim_intent_is_persisted_before_daemon_creation(self):
        create = self.daemon.create_claim
        def inspected(*args):
            self.assertEqual('claim-pending', self.store.record['phase'])
            self.assertIsNone(self.store.record['claimId'])
            return create(*args)
        self.daemon.create_claim = inspected
        self.acquire()
        self.assertEqual('ready', self.store.record['phase'])
    def test_two_targets_cannot_both_claim_the_same_daemon(self):
        other_store = Store()
        other = self.make('/fixture/other', other_store)
        barrier = threading.Barrier(2)
        def attempt(protocol):
            barrier.wait(timeout=5)
            try: protocol.acquire(); return True
            except self.module.OwnershipError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(attempt, p) for p in (self.protocol, other)]
            self.assertEqual(1, sum(t.result(timeout=10) for t in tasks))
        self.assertEqual(1, len(self.daemon.claims))
        self.assertEqual([], self.daemon.claim_deletes)
    def test_unknown_create_is_never_adopted_or_retried(self):
        self.daemon.unknown_create = True
        with self.assertRaises(self.module.OwnershipError): self.acquire()
        self.assertEqual('claim-pending', self.store.record['phase'])
        self.assertIsNone(self.store.record['claimId'])
        self.assertEqual(1, len(self.daemon.claims))
        with self.assertRaises(self.module.OwnershipError): self.acquire()
        self.assertEqual([], self.daemon.claim_deletes)
    def test_preexisting_resources_block_before_claim_creation(self):
        self.daemon.snapshot['containers'] = {'f' * 64: 'a' * 64}
        with self.assertRaises(self.module.OwnershipError): self.acquire()
        self.assertIsNone(self.store.record)
        self.assertEqual({}, self.daemon.claims)
    def test_volume_identity_must_follow_the_exact_bound_project_namespace(self):
        with mock.patch.object(self.module, 'PROJECT', 'survey-owner-fixture-123'):
            self.protocol._resources({'containers': {}, 'networks': {},
                'volumes': {'survey-owner-fixture-123_data': 'd' * 64}})
            for name in ('survey-production_data', 'survey-owner-fixture-1234_data', '../data'):
                with self.subTest(name=name), self.assertRaises(self.module.OwnershipError):
                    self.protocol._resources({'containers': {}, 'networks': {}, 'volumes': {name: 'd' * 64}})
    def test_discovery_failure_never_calls_delete(self):
        self.populate()
        before = self.store.load()
        self.daemon.fail_query = True
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(False)
        self.assertEqual([], self.daemon.remove_calls)
        self.assertEqual(before, self.store.load())
    def test_unknown_processes_block_mutation_and_cleanup(self):
        self.populate()
        self.daemon.snapshot['cohort'] = 'unknown'
        calls = []
        with self.assertRaises(self.module.OwnershipError): self.protocol.mutate('backup', lambda: calls.append('ran'))
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(False)
        self.assertEqual([], calls)
        self.assertEqual([], self.daemon.remove_calls)
    def test_identity_drift_and_foreign_objects_block_before_delete(self):
        self.populate()
        saved = copy.deepcopy(self.daemon.snapshot)
        for field, value in (('containers', {'f' * 64: 'a' * 64}), ('foreign', True)):
            self.daemon.snapshot = copy.deepcopy(saved)
            self.daemon.snapshot[field] = value
            with self.subTest(field=field), self.assertRaises(self.module.OwnershipError):
                self.protocol.cleanup(True)
        self.assertEqual([], self.daemon.remove_calls)
    def test_zero_exit_with_residuals_never_releases_claim(self):
        self.populate()
        self.daemon.keep_resources = True
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(True)
        self.assertEqual('cleanup-pending', self.store.record['phase'])
        self.assertEqual([], self.daemon.claim_deletes)
    def test_non_purge_mutation_cannot_lose_or_replace_a_recorded_volume(self):
        for operation in ('backup', 'upgrade', 'install', 'doctor'):
            for replacement in ({}, {'survey-production_data': 'e' * 64}):
                with self.subTest(operation=operation, replacement=replacement):
                    self.setUp()
                    self.populate()
                    def effect():
                        self.daemon.snapshot['volumes'] = replacement.copy()
                    with self.assertRaises(self.module.OwnershipError):
                        self.protocol.mutate(operation, effect)
                    self.assertEqual('mutation-pending', self.store.record['phase'])
                    self.assertEqual({'survey-production_data': 'd' * 64}, self.store.record['resources']['volumes'])
                    self.assertEqual([], self.daemon.remove_calls)

    def test_non_purge_mutation_can_add_a_declared_volume_while_preserving_old_identity(self):
        self.populate()
        def effect():
            self.daemon.snapshot['volumes']['survey-production_added'] = 'e' * 64
        self.protocol.mutate('upgrade', effect)
        self.assertEqual('ready', self.store.record['phase'])
        self.assertEqual({'survey-production_data': 'd' * 64, 'survey-production_added': 'e' * 64},
                         self.store.record['resources']['volumes'])
    def test_ordinary_uninstall_retains_volume_identity_and_claim(self):
        self.populate()
        self.protocol.cleanup(False)
        self.assertEqual('retained-data', self.store.record['phase'])
        self.assertEqual({'survey-production_data': 'd' * 64}, self.store.record['resources']['volumes'])
        self.assertEqual([], self.daemon.claim_deletes)
        other = self.make('/fixture/other', Store())
        with self.assertRaises(self.module.OwnershipError): other.acquire()
    def test_purge_releases_claim_only_after_postcondition(self):
        self.populate()
        remove = self.daemon.delete_claim
        def checked(identity):
            self.assertEqual({}, self.daemon.snapshot['containers'])
            self.assertEqual({}, self.daemon.snapshot['networks'])
            self.assertEqual({}, self.daemon.snapshot['volumes'])
            remove(identity)
        self.daemon.delete_claim = checked
        self.protocol.cleanup(True)
        self.assertEqual('removed', self.store.record['phase'])
        self.assertEqual(['c' * 64], self.daemon.claim_deletes)
    def test_mutation_unknown_result_retains_pending_and_prevents_cleanup(self):
        self.populate()
        def uncertain():
            self.daemon.snapshot['containers']['3' * 64] = 'e' * 64
            raise TimeoutError('unknown up outcome')
        with self.assertRaises(self.module.OwnershipError): self.protocol.mutate('upgrade', uncertain)
        self.assertEqual('mutation-pending', self.store.record['phase'])
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(True)
        self.assertEqual([], self.daemon.remove_calls)
    def test_successful_callback_with_unverified_cohort_is_not_success(self):
        self.populate()
        def lingering(): self.daemon.snapshot['cohort'] = 'unknown'
        with self.assertRaises(self.module.OwnershipError): self.protocol.mutate('backup', lingering)
        self.assertEqual('mutation-pending', self.store.record['phase'])
    def test_strict_schema_rejects_boolean_version_and_wrong_binding(self):
        self.populate()
        original = self.store.load()
        for field, value in (('schemaVersion', True), ('targetSha256', 'f' * 64), ('daemonId', 'different')):
            self.store.record = copy.deepcopy(original)
            self.store.record[field] = value
            with self.subTest(field=field), self.assertRaises(self.module.OwnershipError):
                self.protocol.cleanup(True)
        self.assertEqual([], self.daemon.remove_calls)

    def test_malformed_phase_or_operation_fails_before_mutation(self):
        self.populate()
        saved = self.store.load()
        self.store.record['phase'] = []
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(True)
        self.store.record = saved
        with self.assertRaises(self.module.OwnershipError): self.protocol.mutate([], lambda: None)
        self.assertEqual([], self.daemon.remove_calls)
    def test_post_delete_query_failure_retains_pending_and_claim(self):
        self.populate()
        delete = self.daemon.delete_resources
        def lose_reply(*args):
            delete(*args)
            self.daemon.fail_query = True
        self.daemon.delete_resources = lose_reply
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(True)
        self.assertEqual('cleanup-pending', self.store.record['phase'])
        self.assertEqual([], self.daemon.claim_deletes)
    def test_lost_claim_delete_reply_is_not_recorded_as_removed(self):
        self.populate()
        delete = self.daemon.delete_claim
        def uncertain(identity):
            delete(identity)
            raise TimeoutError('lost delete reply')
        self.daemon.delete_claim = uncertain
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(True)
        self.assertEqual('cleanup-pending', self.store.record['phase'])
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(True)
    def test_claim_replacement_is_not_followed_or_deleted(self):
        self.populate()
        self.daemon.claims['c' * 64]['binding']['installationId'] = '00000000-0000-4000-8000-000000000000'
        with self.assertRaises(self.module.OwnershipError): self.protocol.cleanup(True)
        self.assertEqual([], self.daemon.remove_calls)
        self.assertEqual([], self.daemon.claim_deletes)
    def test_incomplete_observation_is_not_an_empty_daemon(self):
        for snapshot in ({}, {'containers': {}, 'networks': {}, 'volumes': {}}):
            self.daemon.snapshot = snapshot
            with self.subTest(snapshot=snapshot), self.assertRaises(self.module.OwnershipError): self.acquire()
        self.assertIsNone(self.store.record)
        self.assertEqual({}, self.daemon.claims)

class PrivateStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('ownership_store', MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.target = self.root / 'installation'
        self.control = self.target / '.surveyctl'
        self.control.mkdir(parents=True, mode=0o700)
        self.store = self.module.PrivateStore(self.target)
        self.path = self.control / 'runtime-owner.json'
    def test_private_record_roundtrip_and_permissions(self):
        self.assertIsNone(self.store.load())
        self.store.save({'phase': 'claim-pending'})
        self.assertEqual({'phase': 'claim-pending'}, self.store.load())
        self.assertEqual(0o600, stat.S_IMODE(self.path.stat().st_mode))
    def test_child_receipts_cannot_overwrite_the_owner_or_operation_gate(self):
        self.store.save({'phase': 'ready'})
        gate = self.control / 'operation.intent'
        gate.write_bytes(b'preserve-pending')
        gate.chmod(0o600)
        for role in ('child', 'exec', 'launch'):
            name = 'runtime-' + role + '-' + 'a' * 32 + '.json'
            child = self.module.PrivateStore(self.target, name=name)
            child.save({'phase': 'pending'})
            self.assertEqual({'phase': 'pending'}, child.load())
        for name in ('', 'runtime-owner.json', 'operation.intent', '../foreign', '/tmp/foreign',
                     'runtime-child-' + 'a' * 31 + '.json', True):
            with self.subTest(name=name), self.assertRaises(self.module.OwnershipError):
                self.module.PrivateStore(self.target, name=name)
        self.assertEqual({'phase': 'ready'}, self.store.load())
        self.assertEqual(b'preserve-pending', gate.read_bytes())
    def test_shared_write_or_read_record_is_not_repaired_or_replaced(self):
        self.path.write_text('{"x":1}')
        self.path.chmod(0o644)
        for action in (self.store.load, lambda: self.store.save({'x': 2})):
            with self.assertRaises(self.module.OwnershipError): action()
        self.assertEqual('{"x":1}', self.path.read_text())
        self.assertEqual(0o644, stat.S_IMODE(self.path.stat().st_mode))
    def test_symlink_hardlink_and_fifo_records_are_rejected(self):
        outside = self.root / 'outside'
        outside.write_text('{"keep":true}'); outside.chmod(0o600)
        for kind in ('symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'symlink': self.path.symlink_to(outside)
                elif kind == 'hardlink': os.link(outside, self.path)
                else: os.mkfifo(self.path, 0o600)
                with self.assertRaises(self.module.OwnershipError): self.store.load()
                with self.assertRaises(self.module.OwnershipError): self.store.save({'replace': True})
                self.path.unlink()
        self.assertEqual('{"keep":true}', outside.read_text())
    def test_duplicate_json_and_oversized_record_are_rejected(self):
        for value in ('{"x":1,"x":2}', 'x' * 65537):
            self.path.write_text(value); self.path.chmod(0o600)
            with self.assertRaises(self.module.OwnershipError): self.store.load()
    def test_parent_replacement_preserves_foreign_directory(self):
        flush = self.module.os.fsync
        moved = self.root / 'retained-original'
        changed = []
        def replace(fd):
            flush(fd)
            if not changed:
                changed.append(True)
                self.target.rename(moved)
                self.control.mkdir(parents=True, mode=0o700)
                (self.control / 'foreign').write_text('preserve')
        with mock.patch.object(self.module.os, 'fsync', side_effect=replace):
            with self.assertRaises(self.module.OwnershipError): self.store.save({'phase': 'claim-pending'})
        self.assertEqual('preserve', (self.control / 'foreign').read_text())
        self.assertFalse(self.path.exists())
        self.assertTrue(list((moved / '.surveyctl').iterdir()))
    def test_persist_failure_happens_before_any_claim(self):
        daemon = Daemon()
        protocol = self.module.OwnershipProtocol(self.target, daemon, self.store, 'example/image@sha256:' + 'a' * 64)
        with mock.patch.object(self.module.os, 'fsync', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(self.module.OwnershipError): protocol.acquire()
        self.assertEqual({}, daemon.claims)
    def test_record_change_during_decode_is_rejected(self):
        self.store.save({'x': 1})
        decode = self.module.json.loads
        def change(*args, **kwargs):
            value = decode(*args, **kwargs)
            with self.path.open('a') as output: output.write(' ')
            return value
        with mock.patch.object(self.module.json, 'loads', side_effect=change):
            with self.assertRaises(self.module.OwnershipError): self.store.load()
    def test_existing_record_drift_during_temp_flush_is_not_overwritten(self):
        self.store.save({'x': 1})
        flush = self.module.os.fsync
        changed = []
        def change(fd):
            flush(fd)
            if not changed:
                changed.append(True)
                self.path.write_text('{"foreign":"preserve"}')
                self.path.chmod(0o600)
        with mock.patch.object(self.module.os, 'fsync', side_effect=change):
            with self.assertRaises(self.module.OwnershipError): self.store.save({'x': 2})
        self.assertEqual('{"foreign":"preserve"}', self.path.read_text())

class DockerAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('ownership_adapter', MODULE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
    def setUp(self):
        self.calls = []
        self.reply = ''
        self.runner = mock.Mock()
        def run(command, **kwargs):
            self.calls.append((command, kwargs))
            return type('Result', (), {'stdout': self.reply, 'returncode': 0})()
        self.runner.run.side_effect = run
        self.contract = {'services': {'platform': {'imageIds': ['sha256:' + 'a' * 64],
            'workingDirs': ['/fixture/releases/1.0.0'], 'mounts': []}},
            'networks': {}, 'volumes': {}}
    def driver(self, probe=lambda: 'quiescent'):
        return self.module.DockerOwnershipDriver(self.runner, self.contract, probe)
    def test_no_implicit_quiescence_callback(self):
        with self.assertRaises(self.module.OwnershipError): self.driver(None)
        self.assertEqual([], self.calls)
    def test_claim_create_is_stopped_isolated_and_never_pulls(self):
        driver = self.driver()
        def image_then_create(command, **kwargs):
            self.calls.append((command, kwargs))
            stdout = 'null' if 'image' in command and 'inspect' in command else 'c' * 64 + '\n'
            return type('Result', (), {'stdout': stdout, 'returncode': 0})()
        self.runner.run.side_effect = image_then_create
        binding = {'daemonId': 'fixture', 'installationId': '00000000-0000-4000-8000-000000000000',
                   'targetSha256': 'b' * 64}
        self.assertEqual('c' * 64, driver.create_claim(self.module.CLAIM_NAME, binding, 'example/image@sha256:' + 'a' * 64))
        command, options = self.calls[-1]
        self.assertIn('create', command)
        self.assertIn('--pull=never', command)
        self.assertEqual('none', command[command.index('--network') + 1])
        self.assertNotIn('--rm', command)
        self.assertNotIn('--privileged', command)
        self.assertNotIn('--mount', command)
        self.assertNotIn('-v', command)
        self.assertEqual(30, options['timeout'])
        self.assertTrue(options['check'])
    def test_image_declared_volumes_cannot_create_an_implicit_claim_volume(self):
        driver = self.driver()
        def response(command, **kwargs):
            self.calls.append((command, kwargs))
            stdout = '{"/data":{}}' if 'image' in command and 'inspect' in command else 'c' * 64 + '\n'
            return type('Result', (), {'stdout': stdout, 'returncode': 0})()
        self.runner.run.side_effect = response
        binding = {'daemonId': 'fixture', 'installationId': '00000000-0000-4000-8000-000000000000',
                   'targetSha256': 'b' * 64}
        with self.assertRaises(self.module.OwnershipError):
            driver.create_claim(self.module.CLAIM_NAME, binding, 'example/image@sha256:' + 'a' * 64)
        self.assertFalse(any('create' in command for command, _ in self.calls))

    def test_query_failure_is_not_an_empty_inventory(self):
        driver = self.driver()
        self.runner.run.side_effect = OSError('synthetic query failure')
        with self.assertRaises(self.module.OwnershipError): driver.observe()
    def test_malformed_or_oversized_query_fails_closed(self):
        driver = self.driver()
        for value in ('not-json', 'x' * (1024 * 1024 + 1)):
            self.reply = value
            with self.subTest(value_size=len(value)), self.assertRaises(self.module.OwnershipError): driver.observe()
    def test_unknown_host_cohort_cannot_be_promoted_by_empty_lists(self):
        driver = self.driver(lambda: 'unknown')
        result = driver.observe()
        self.assertEqual('unknown', result['cohort'])
        self.assertEqual({}, result['containers'])

class FixtureCleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = MODULE.parent / 'tests/integration/runtime_ownership_docker.py'
        spec = importlib.util.spec_from_file_location('ownership_daemon_fixture', path)
        cls.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture)

    def test_unknown_create_reply_cannot_claim_cleanup_verified(self):
        namespace = 'survey-owner-fixture-' + 'a' * 32
        calls = []
        def docker(arguments):
            calls.append(arguments)
            if arguments[:2] == ['network', 'ls']:
                return json.dumps({'name': namespace + '_internal'})
            return ''
        self.assertFalse(self.fixture.cleanup_fixture(docker, [], namespace))
        self.assertFalse(any('rm' in command for command in calls))

    def test_unknown_inventory_result_is_not_clean_and_other_kinds_are_checked(self):
        namespace = 'survey-owner-fixture-' + 'a' * 32
        calls = []
        def docker(arguments):
            calls.append(arguments)
            if arguments[0] == 'container':
                raise TimeoutError('synthetic unavailable')
            return ''
        self.assertFalse(self.fixture.cleanup_fixture(docker, [], namespace))
        self.assertEqual({'container', 'network', 'volume'}, {command[0] for command in calls})

if __name__ == '__main__': unittest.main()
