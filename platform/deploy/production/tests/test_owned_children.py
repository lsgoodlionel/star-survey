"""Durable child transition tests; fake guards are not kernel evidence."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import os

SOURCE = Path(__file__).resolve().parents[1] / 'owned_children.py'


class Store:
    def __init__(self):
        self.value, self.writes, self.fail = None, [], False
    def load(self):
        return copy.deepcopy(self.value)
    def save(self, value):
        if self.fail:
            raise OSError('synthetic persistence failure')
        self.value = copy.deepcopy(value)
        self.writes.append(copy.deepcopy(value))


class Guard:
    def __init__(self):
        self.actor = {'pid': 44, 'start': '444', 'parentPid': 3, 'parentStart': '33'}
    def check(self):
        return self.actor.copy()


class OwnedChildrenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('owned_children_test', SOURCE)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.store, self.guard = Store(), Guard()
        self.children = {}
        self.journal = self.module.ChildJournal(self.store, self.guard,
            operation_id='00000000-0000-4000-8000-000000000001',
            installation_id='00000000-0000-4000-8000-000000000002',
            _child_reader=lambda pid: self.children.get(pid))

    def created(self):
        token = self.journal.begin('openssl-encrypt')
        self.children[55] = {'pid': 55, 'start': '555', 'parentPid': 44, 'state': 'S'}
        self.journal.created(token, 55, '555')
        return token

    def test_intent_precedes_pid_and_release_is_a_distinct_durable_transition(self):
        token = self.created()
        self.assertEqual('pending', self.store.writes[0]['children'][0]['phase'])
        self.assertIsNone(self.store.writes[0]['children'][0]['pid'])
        self.assertEqual('created', self.store.value['children'][0]['phase'])
        self.journal.released(token)
        self.assertEqual('released', self.store.value['children'][0]['phase'])
        del self.children[55]
        self.journal.finished(token, 0, succeeded=True, reason='completed')
        self.assertEqual('completed', self.store.value['children'][0]['phase'])

    def test_persistence_failure_cannot_advance_local_phase_or_release(self):
        token = self.created()
        before = self.store.load()
        self.store.fail = True
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.released(token)
        self.assertEqual(before, self.store.load())

    def test_foreign_reused_dead_and_boolean_child_identities_are_rejected(self):
        for pid, start, entry in ((55, '555', None), (55, '555', {'pid': 55, 'start': '999', 'parentPid': 44, 'state': 'S'}),
                (55, '555', {'pid': 55, 'start': '555', 'parentPid': 99, 'state': 'S'}),
                (55, '555', {'pid': 55, 'start': '555', 'parentPid': 44, 'state': 'Z'}), (True, '555', None)):
            with self.subTest(pid=pid, entry=entry):
                self.setUp()
                token = self.journal.begin('openssl-encrypt')
                if entry is not None:
                    self.children[pid] = entry
                with self.assertRaises(self.module.ChildBoundaryError):
                    self.journal.created(token, pid, start)
                self.assertEqual('pending', self.store.value['children'][0]['phase'])

    def test_child_drift_before_release_preserves_created_receipt(self):
        token = self.created()
        self.children[55]['start'] = '556'
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.released(token)
        self.assertEqual('created', self.store.value['children'][0]['phase'])

    def test_a_running_child_cannot_be_declared_completed(self):
        token = self.created()
        self.journal.released(token)
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.finished(token, 0, succeeded=True, reason='completed')
        self.assertEqual('released', self.store.value['children'][0]['phase'])

    def test_timeout_remains_failed_even_if_child_eventually_exits_zero(self):
        token = self.created()
        self.journal.released(token)
        del self.children[55]
        self.journal.finished(token, 0, succeeded=False, reason='deadline')
        self.assertEqual('failed', self.store.value['children'][0]['phase'])
        self.assertEqual('deadline', self.store.value['children'][0]['reason'])

    def test_owner_identity_change_and_out_of_band_writes_are_not_adopted(self):
        token = self.created()
        self.guard.actor['start'] = '445'
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.released(token)
        self.guard.actor['start'] = '444'
        self.store.value['children'][0]['phase'] = 'released'
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.released(token)

    def test_prior_journal_is_not_resumed_or_silently_reset(self):
        self.journal.begin('openssl-encrypt')
        with self.assertRaises(self.module.ChildBoundaryError):
            self.module.ChildJournal(self.store, self.guard,
                operation_id='00000000-0000-4000-8000-000000000001',
                installation_id='00000000-0000-4000-8000-000000000002')

    def test_invalid_profile_phase_exit_and_token_do_not_publish_success(self):
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.begin('arbitrary-command')
        token = self.created()
        del self.children[55]
        for value in (True, 256, -999):
            with self.assertRaises(self.module.ChildBoundaryError):
                self.journal.finished(token, value, succeeded=False, reason='exit')
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.finished(token, 0, succeeded=True, reason='completed')
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.released(True)

    def test_malformed_observation_and_reason_have_fixed_boundary_failures(self):
        token = self.journal.begin('openssl-decrypt')
        self.children[55] = {'pid': 55, 'start': '555', 'parentPid': 44, 'state': []}
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.created(token, 55, '555')
        self.children[55]['state'] = 'S'
        self.journal.created(token, 55, '555')
        self.journal.released(token)
        del self.children[55]
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.finished(token, 0, succeeded=True, reason=[])
        self.assertEqual('released', self.store.value['children'][0]['phase'])

    def test_known_no_spawn_is_terminal_but_does_not_hide_an_existing_child(self):
        token = self.journal.begin('openssl-decrypt')
        self.journal.not_started(token)
        self.assertEqual('not-started', self.store.value['children'][0]['phase'])
        token = self.created()
        with self.assertRaises(self.module.ChildBoundaryError):
            self.journal.not_started(token)

    def test_controller_actor_comes_from_kernel_observations_and_rejects_identity_switch(self):
        pid, parent = os.getpid(), os.getppid()
        values = {pid: {'pid': pid, 'start': '100', 'parentPid': parent, 'state': 'S'},
            parent: {'pid': parent, 'start': '90', 'parentPid': 1, 'state': 'S'}}
        with mock.patch.object(self.module, 'read_process_identity', side_effect=lambda number: values.get(number)):
            guard = self.module.ControllerProcessGuard()
            self.assertEqual(pid, guard.check()['pid'])
            values[pid]['start'] = '101'
            with self.assertRaises(self.module.ChildBoundaryError): guard.check()


if __name__ == '__main__':
    unittest.main()
