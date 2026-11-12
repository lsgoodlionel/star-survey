"""Filesystem transition only; never authorizes resource creation or import."""
import json
import fcntl
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_recovery_reservation as fixtures


class RecoveryHandoffTest(unittest.TestCase):
    def setUp(self):
        fixtures.RecoveryReservationTest.setUp(self)
        self.receipt = 'runtime-exec-' + 'c' * 32 + '.json'

    guard = fixtures.RecoveryReservationTest.guard
    write = fixtures.RecoveryReservationTest.write

    def test_transfers_original_lock_without_releasing_it_or_authorizing_execution(self):
        original = {p.name: p.read_bytes() for p in self.control.iterdir()}
        with self.guard() as guard:
            handoff = guard.handoff(receipts=(self.receipt,))
        try:
            contender = os.open(self.control/'operation.lock', os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally: os.close(contender)
            result = handoff.inspect()
            self.assertIs(False, result['executable'])
            self.assertIs(False, result['runtimeRecordsQualified'])
            with self.assertRaises(self.module.ReservationError), self.guard(): pass
            self.assertTrue(all(not os.get_inheritable(fd) for fd in handoff._fds))
            self.write('runtime-owner.json', {'schemaVersion': 1, 'project': 'survey-recovery-' + self.nonce})
            self.write(self.receipt, {'schemaVersion': 3, 'phase': 'create-pending'})
            self.assertIs(False, handoff.inspect()['executable'])
            self.assertEqual(original, {name: (self.control/name).read_bytes() for name in original})
        finally: handoff.close()
        contender = os.open(self.control/'operation.lock', os.O_RDWR)
        try: fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally: os.close(contender)
        self.assertTrue((self.control/'recovery.handoff.json').exists())
        with self.assertRaises(self.module.ReservationError), self.guard(): pass

    def test_unknown_entry_poison_is_permanent_and_never_deleted(self):
        with self.guard() as guard:
            handoff = guard.handoff(receipts=(self.receipt,))
        try:
            extra = self.control/'unknown'; extra.write_bytes(b'keep')
            with self.assertRaises(self.module.ReservationError): handoff.inspect()
            self.assertEqual(b'keep', extra.read_bytes()); extra.unlink()
            with self.assertRaises(self.module.ReservationError): handoff.inspect()
        finally: handoff.close()

    def test_replaced_marker_and_modified_original_intent_fail_closed(self):
        for name in ('recovery.handoff.json','operation.intent'):
            with self.subTest(name=name):
                # Each case uses a fresh reservation; never reset a pending handoff.
                if name == 'operation.intent': self.setUp()
                with self.guard() as guard: handoff = guard.handoff(receipts=(self.receipt,))
                try:
                    path = self.control/name
                    replacement = self.control/'replacement'; replacement.write_bytes(path.read_bytes())
                    replacement.chmod(0o600); replacement.replace(path)
                    with self.assertRaises(self.module.ReservationError): handoff.inspect()
                finally: handoff.close()

    def test_short_marker_write_keeps_pending_and_does_not_transfer_lock(self):
        with self.assertRaises(self.module.ReservationError):
            with self.guard() as guard:
                write = os.write
                def partial(fd, data): return write(fd, data[:1])
                with mock.patch('os.write', side_effect=partial): guard.handoff(receipts=(self.receipt,))
        self.assertEqual(b'{', (self.control/'recovery.handoff.json').read_bytes())
        with self.assertRaises(self.module.ReservationError), self.guard(): pass

    def test_bad_receipt_plan_rejects_before_writing_or_changing_guard(self):
        for receipts in (True, (), ([],), (None,), (True,), ('runtime-exec-bad.json',), (self.receipt,self.receipt),
                tuple('runtime-exec-'+format(i,'032x')+'.json' for i in range(17))):
            with self.subTest(receipts=receipts), self.guard() as guard:
                with self.assertRaises(self.module.ReservationError): guard.handoff(receipts=receipts)
                self.assertIs(False, guard.inspect()['executable'])
                self.assertFalse((self.control/'recovery.handoff.json').exists())


    def test_planned_record_appearing_before_transfer_is_not_adopted(self):
        original = os.fsync
        inserted = False
        def unexpected(fd):
            nonlocal inserted
            if not inserted:
                inserted = True
                self.write('runtime-owner.json', {'schemaVersion': 1})
            return original(fd)
        with self.assertRaises(self.module.ReservationError):
            with self.guard() as guard:
                with mock.patch('os.fsync', side_effect=unexpected): guard.handoff(receipts=(self.receipt,))
        self.assertFalse(guard._transferred)
        self.assertTrue((self.control/'runtime-owner.json').exists())
        self.assertTrue((self.control/'recovery.handoff.json').exists())

    def test_unsafe_planned_record_is_never_treated_as_runtime_qualification(self):
        for mode in ('symlink', 'hardlink', 'public', 'fifo'):
            with self.subTest(mode=mode):
                self.setUp()
                with self.guard() as guard: handoff = guard.handoff(receipts=(self.receipt,))
                try:
                    path = self.control/self.receipt
                    outside = self.parent/'outside'; outside.write_bytes(b'{"phase":"keep"}'); outside.chmod(0o600)
                    if mode == 'symlink': path.symlink_to(outside)
                    elif mode == 'hardlink': os.link(outside, path)
                    elif mode == 'fifo': os.mkfifo(path, 0o600)
                    else: path.write_bytes(b'{}'); path.chmod(0o644)
                    with self.assertRaises(self.module.ReservationError): handoff.inspect()
                    self.assertEqual(b'{"phase":"keep"}', outside.read_bytes())
                finally: handoff.close()

    def test_close_failure_is_unknown_and_descriptor_is_never_closed_again(self):
        from owned_docker_exec import UncertainExec
        with self.guard() as guard: handoff = guard.handoff(receipts=(self.receipt,))
        actual, closed = os.close, []
        victim = handoff._fds[-1]
        def ambiguous(fd):
            closed.append(fd); actual(fd)
            if fd == victim: raise OSError('ambiguous release')
        with mock.patch('os.close', side_effect=ambiguous):
            with self.assertRaises(UncertainExec): handoff.close()
            handoff.close()
        self.assertEqual(1, closed.count(victim))
        self.assertTrue((self.control/'recovery.handoff.json').exists())
        with self.assertRaises(UncertainExec): handoff.inspect()


    def test_failed_context_entry_releases_private_descriptors_without_clearing_pending(self):
        with self.guard() as guard: handoff = guard.handoff(receipts=(self.receipt,))
        descriptors = tuple(handoff._fds)
        (self.control/'unexpected').write_bytes(b'keep')
        try:
            with self.assertRaises(self.module.ReservationError):
                with handoff: self.fail('unsafe handoff entered')
            for fd in descriptors:
                with self.assertRaises(OSError): os.fstat(fd)
            self.assertTrue((self.control/'recovery.handoff.json').exists())
            self.assertEqual(b'keep', (self.control/'unexpected').read_bytes())
        finally: handoff.close()


    def test_failed_publication_poisons_original_guard_even_if_external_actor_removes_marker(self):
        with self.assertRaises(self.module.ReservationError):
            with self.guard() as guard:
                with mock.patch('os.fsync', side_effect=OSError('interrupted publication')):
                    with self.assertRaises(self.module.ReservationError): guard.handoff(receipts=(self.receipt,))
                (self.control/'recovery.handoff.json').unlink()  # Fault injection only, not implementation repair.
                with self.assertRaises(self.module.ReservationError): guard.inspect()


    def test_runtime_record_read_cannot_hide_late_anchor_or_inventory_replacement(self):
        from runtime_ownership import PrivateStore
        for change in ('marker', 'inventory'):
            with self.subTest(change=change):
                self.setUp()
                with self.guard() as guard: handoff = guard.handoff(receipts=(self.receipt,))
                self.write('runtime-owner.json', {'schemaVersion': 1})
                original, changed = PrivateStore.load, False
                def replacing(store):
                    nonlocal changed
                    value = original(store)
                    if not changed:
                        changed = True
                        if change == 'marker':
                            path = self.control/'recovery.handoff.json'
                            staged = self.control/'swap'; staged.write_bytes(path.read_bytes())
                            staged.chmod(0o600); staged.replace(path)
                        else: (self.control/'late-unknown').write_bytes(b'keep')
                    return value
                try:
                    with mock.patch.object(PrivateStore, 'load', replacing):
                        with self.assertRaises(self.module.ReservationError): handoff.inspect()
                finally: handoff.close()


    def test_nested_preparation_and_outer_guard_close_failure_keep_original_unknown(self):
        from owned_docker_exec import UncertainExec
        actual_close, actual_open = os.close, os.open
        victim, foreign, primary, closed, publications = [], [], [], [], []
        def observed_open(name, *args, **kwargs):
            if name == 'recovery.handoff.json': publications.append(name)
            return actual_open(name, *args, **kwargs)
        def ambiguous_close(fd):
            closed.append(fd); actual_close(fd)
            if victim and fd == victim[0]:
                replacement = actual_open(self.parent/'foreign-fd', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                foreign.append(replacement)
                os.write(replacement, b'foreign must remain open')
                raise OSError('outer guard close result unknown')
        try:
            with mock.patch('os.open', side_effect=observed_open), mock.patch('os.close', side_effect=ambiguous_close):
                with self.assertRaises(UncertainExec) as result:
                    with self.guard() as guard:
                        victim.append(guard._fds[-1])
                        with mock.patch('os.fsync', side_effect=OSError('handoff fsync interrupted')):
                            try: guard.handoff(receipts=(self.receipt,))
                            except UncertainExec as error:
                                primary.append(error); raise
                self.assertIs(result.exception, primary[0])
                self.assertEqual('handoff fsync interrupted', str(result.exception.__cause__))
                with self.assertRaises(self.module.ReservationError): guard.handoff(receipts=(self.receipt,))
                guard._close()  # No retry of any old or reused descriptor.
            self.assertEqual(victim, foreign)
            self.assertEqual(1, closed.count(victim[0]))
            self.assertEqual(1, len(publications))
            self.assertEqual(b'foreign must remain open', os.pread(foreign[0], 64, 0))
            marker = json.loads((self.control/'recovery.handoff.json').read_text())
            self.assertEqual('pending-not-authorized', marker['state'])
            self.assertIs(False, marker['executable'])
        finally:
            for fd in foreign: actual_close(fd)

if __name__ == '__main__': unittest.main()
