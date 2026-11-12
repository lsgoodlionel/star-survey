import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RecoveryReservationTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('recovery_reservation')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name).resolve()
        self.target = self.parent / 'reserved'
        self.target.mkdir(mode=0o700)
        self.control = self.target / '.surveyctl'
        self.control.mkdir(mode=0o700)
        self.nonce, self.sha = 'a' * 32, 'b' * 64
        self.record = dict(schemaVersion=1, state='reserved-not-authorized', executable=False,
            reservationId=self.nonce, projectProposal='survey-recovery-' + self.nonce,
            sourceObservationSha256=self.sha,
            targetIdentity=[self.target.stat().st_dev, self.target.stat().st_ino],
            controlIdentity=[self.control.stat().st_dev, self.control.stat().st_ino],
            backupAuthenticated=False, runtimeResourcesCreated=False)
        self.write('recovery.reservation.json', self.record)
        self.write('operation.intent', dict(schemaVersion=1, state='pending', operation='recovery_reserve'))
        self.write('operation.lock', b'')

    def write(self, name, value):
        path = self.control / name
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        path.chmod(0o600)

    def guard(self, **kwargs):
        return self.module.RecoveryReservation(self.target, self.nonce, self.sha, **kwargs)

    def test_valid_reservation_is_read_only_and_never_authorizes_execution(self):
        before = {p.name: (p.read_bytes(), p.stat().st_ino) for p in self.control.iterdir()}
        with self.guard() as guard:
            result = guard.inspect()
            self.assertIs(False, result['executable'])
            self.assertIs(False, result['backupAuthenticated'])
            self.assertIs(False, result['runtimeResourcesCreated'])
            self.assertEqual('reservation-verified-not-authorized', result['status'])
            self.assertEqual('survey-recovery-' + self.nonce, result['projectProposal'])
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_ino) for p in self.control.iterdir()})
        with self.assertRaises(self.module.ReservationError): guard.inspect()

    def test_identity_copy_or_wrong_expected_binding_is_rejected(self):
        for patch in ({'targetIdentity': [1, 2]}, {'controlIdentity': [1, 2]},
                      {'reservationId': 'c' * 32}, {'sourceObservationSha256': 'c' * 64}):
            with self.subTest(patch=patch):
                self.write('recovery.reservation.json', {**self.record, **patch})
                with self.assertRaises(self.module.ReservationError), self.guard(): pass

    def test_strict_schema_rejects_bool_integer_unsafe_project_and_authorization(self):
        for patch in ({'schemaVersion': True}, {'targetIdentity': [True, 2]},
                      {'projectProposal': 'survey-production'}, {'executable': True},
                      {'backupAuthenticated': True}, {'runtimeResourcesCreated': True}, {'extra': 1}):
            with self.subTest(patch=patch):
                self.write('recovery.reservation.json', {**self.record, **patch})
                with self.assertRaises(self.module.ReservationError), self.guard(): pass

    def test_duplicates_oversize_and_non_json_are_rejected(self):
        for value in (b'{"schemaVersion":1,"schemaVersion":1}', b'x' * 8193, b'not-json'):
            with self.subTest(size=len(value)):
                self.write('recovery.reservation.json', value)
                with self.assertRaises(self.module.ReservationError), self.guard(): pass

    def test_symlink_hardlink_and_permissions_fail_without_changes(self):
        path = self.control / 'recovery.reservation.json'
        original = path.read_bytes()
        path.chmod(0o644)
        with self.assertRaises(self.module.ReservationError), self.guard(): pass
        path.chmod(0o600)
        alias = self.parent / 'hardlink'; os.link(path, alias)
        with self.assertRaises(self.module.ReservationError), self.guard(): pass
        alias.unlink()
        path.rename(alias); path.symlink_to(alias)
        with self.assertRaises(self.module.ReservationError), self.guard(): pass
        self.assertTrue(path.is_symlink()); self.assertEqual(original, alias.read_bytes())

    def test_ancestor_permissions_and_symlink_are_not_hidden_by_private_target(self):
        self.parent.chmod(0o777)
        with self.assertRaises(self.module.ReservationError), self.guard(): pass
        self.parent.chmod(0o700)
        alias = self.parent / 'alias'; alias.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(self.module.ReservationError):
            with self.module.RecoveryReservation(alias, self.nonce, self.sha): pass

    def test_unexpected_entry_or_changed_intent_is_rejected(self):
        extra = self.control / 'unknown'; extra.write_text('retain')
        with self.assertRaises(self.module.ReservationError), self.guard(): pass
        self.assertEqual('retain', extra.read_text()); extra.unlink()
        self.write('operation.intent', dict(schemaVersion=True, state='pending', operation='recovery_reserve'))
        with self.assertRaises(self.module.ReservationError), self.guard(): pass

    def test_lock_conflict_refuses_and_releases_after_exit(self):
        with self.guard() as guard:
            self.assertTrue(all(not os.get_inheritable(fd) for fd in guard._fds))
            with self.assertRaises(self.module.ReservationError), self.guard(): pass
        with self.guard(): pass

    def test_failed_check_permanently_blocks_context_even_after_entry_is_removed(self):
        with self.assertRaises(self.module.ReservationError):
            with self.guard() as guard:
                extra = self.control / 'unknown'; extra.write_bytes(b'')
                with self.assertRaises(self.module.ReservationError): guard.inspect()
                extra.unlink()
                with self.assertRaises(self.module.ReservationError): guard.inspect()

    def test_directory_replaced_during_file_read_is_rejected_before_inspect_returns(self):
        with self.assertRaises(self.module.ReservationError):
            with self.guard() as guard:
                original = self.module.os.read
                moved = False
                def replace(fd, count):
                    nonlocal moved
                    result = original(fd, count)
                    if not moved:
                        moved = True
                        self.target.rename(self.parent / 'pinned-old')
                        self.target.mkdir(mode=0o700)
                    return result
                with mock.patch.object(self.module.os, 'read', side_effect=replace):
                    with self.assertRaises(self.module.ReservationError): guard.inspect()

    def test_special_file_does_not_block_or_get_adopted(self):
        path = self.control / 'operation.lock'; path.unlink(); os.mkfifo(path, 0o600)
        with self.assertRaises(self.module.ReservationError), self.guard(): pass
        self.assertTrue(path.exists())

    def test_same_bytes_replacement_and_in_place_mutation_are_detected(self):
        for replacement in (True, False):
            with self.subTest(replacement=replacement):
                self.write('recovery.reservation.json', self.record)
                with self.assertRaises(self.module.ReservationError):
                    with self.guard() as guard:
                        path = self.control / 'recovery.reservation.json'
                        if replacement:
                            other = self.control / 'staging'; other.write_bytes(path.read_bytes())
                            other.chmod(0o600); other.replace(path)
                        else:
                            self.write('recovery.reservation.json', {**self.record, 'executable': True})
                        guard.inspect()

    def test_target_replacement_is_detected_and_external_target_is_preserved(self):
        old = self.parent / 'original'
        with self.assertRaises(self.module.ReservationError):
            with self.guard():
                self.target.rename(old); self.target.mkdir(mode=0o700)
                (self.target / 'external').write_text('do not delete')
        self.assertEqual('do not delete', (self.target / 'external').read_text())
        self.assertTrue((old / '.surveyctl/operation.intent').exists())

    def test_existing_reserve_output_is_accepted_without_runtime_mutation(self):
        import surveyctl
        source = self.parent / 'source'; source.mkdir(mode=0o700)
        (source / '.surveyctl').mkdir(mode=0o700)
        lock = source / '.surveyctl/operation.lock'; lock.touch(mode=0o600)
        manager = surveyctl.SurveyManager(source)
        destination = self.parent / 'real-reservation'
        observation = manager.recovery_plan(destination)
        result = manager.recovery_reserve(destination, observation['source']['snapshotSha256'])
        with self.module.RecoveryReservation(destination, result['reservationId'],
                result['sourceObservationSha256']) as guard:
            self.assertIs(False, guard.inspect()['executable'])


if __name__ == '__main__': unittest.main()
