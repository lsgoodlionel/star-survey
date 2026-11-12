"""Isolated ledger fixtures, not production/controller acceptance."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from test_runtime_cohort import RuntimeCohortTest as CohortFixture


class ReceiptRetirementTest(unittest.TestCase):
    def setUp(self):
        self.f = CohortFixture(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.owner(); self.f.receipt()
        import runtime_cohort
        self.module = runtime_cohort
        self.calls = []
        self.intent = self.f.target / '.surveyctl/operation.intent'
        def begin():
            self.calls.append('begin')
            self.intent.write_text(json.dumps({'schemaVersion': 1, 'state': 'pending', 'operation': 'backup'}))
            self.intent.chmod(0o600)
        self.begin = begin
        self.check = lambda: self.f.store_type(self.f.target).load()

    def primitive(self):
        return self.module.ReceiptRetirement(self.f.probe, self.check, self.begin, 'backup')

    def receipts(self): return list((self.f.target / '.surveyctl').glob('runtime-exec-*.json'))

    def test_read_only_plan_then_verified_retirement_keeps_pending_marker(self):
        plan = self.primitive().plan()
        self.assertFalse(plan['executable']); self.assertEqual(1, plan['count'])
        self.assertEqual(1, len(self.receipts())); self.assertFalse(self.intent.exists())
        self.assertEqual(1, self.primitive().run())
        self.assertFalse(self.receipts()); self.assertEqual(['begin'], self.calls)
        self.assertEqual('pending', json.loads(self.intent.read_text())['state'])

    def test_v1_gc_and_pending_never_write_or_delete(self):
        from owned_docker_exec import ExecMetadataGone
        for pending in (False, True):
            self.f.receipt(**({'phase':'released','exitCode':None} if pending else {}))
            self.f.api.inspect = lambda identity: (_ for _ in ()).throw(ExecMetadataGone('fixture'))
            before = self.receipts()[0].read_bytes()
            with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
            self.assertEqual(before, self.receipts()[0].read_bytes()); self.assertFalse(self.calls)

    def test_intent_failure_never_deletes_and_partial_unlink_keeps_pending(self):
        self.begin = lambda: (_ for _ in ()).throw(OSError('fixture intent failure'))
        with self.assertRaises(OSError): self.primitive().run()
        self.assertEqual(1, len(self.receipts()))
        self.setUp()
        with patch.object(self.module.os, 'unlink', side_effect=OSError('fixture unlink failure')):
            with self.assertRaises(OSError): self.primitive().run()
        self.assertEqual(1, len(self.receipts())); self.assertEqual('pending', json.loads(self.intent.read_text())['state'])

    def test_capacity_and_resource_drift_fail_before_intent(self):
        control=self.f.target/'.surveyctl'
        for i in range(256): (control/('filler-'+str(i))).touch(mode=0o600)
        with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
        self.assertFalse(self.calls); self.assertEqual(1,len(self.receipts()))
        for path in control.glob('filler-*'): path.unlink()
        self.check=lambda: dict(self.f.store_type(self.f.target).load(), daemonId='wrong')
        with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
        self.assertFalse(self.calls)

    def test_v2_gc_can_retire_but_transport_error_cannot(self):
        from owned_docker_exec import ExecMetadataGone
        self.f.durable_receipt()
        self.f.api.inspect=lambda identity: (_ for _ in ()).throw(ExecMetadataGone('fixture'))
        self.assertEqual(1,self.primitive().run())
        self.f.durable_receipt()
        self.f.api.inspect=lambda identity: (_ for _ in ()).throw(OSError('fixture unavailable'))
        before=self.receipts()[0].read_bytes()
        with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
        self.assertEqual(before,self.receipts()[0].read_bytes())

    def test_post_unlink_fsync_failure_never_clears_pending(self):
        with patch.object(self.module.os,'fsync',side_effect=OSError('fixture durability failure')):
            with self.assertRaises(OSError): self.primitive().run()
        self.assertFalse(self.receipts())
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])

    def test_receipt_becoming_pending_after_begin_is_not_retired(self):
        original=self.begin
        def begin():
            original()
            self.f.receipt(phase='released',exitCode=None)
        self.begin=begin
        with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
        self.assertEqual(1,len(self.receipts()))
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])

    def test_repeated_terminal_cycles_do_not_accumulate_more_than_capacity(self):
        import uuid
        total=0
        for cycle in range(30):
            for i in range(9): self.f.receipt(operationId=str(uuid.uuid4()))
            total+=self.primitive().run()
            self.assertFalse(self.receipts())
        self.assertGreater(total,256)

    def test_missing_pending_intent_and_symlink_receipt_never_delete(self):
        self.begin=lambda: None
        with self.assertRaises(FileNotFoundError): self.primitive().run()
        self.assertEqual(1,len(self.receipts()))
        path=self.receipts()[0]; content=path.read_bytes(); path.unlink()
        foreign=self.f.target/'foreign'; foreign.write_bytes(content); foreign.chmod(0o600)
        path.symlink_to(foreign)
        with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
        self.assertTrue(path.is_symlink()); self.assertEqual(content,foreign.read_bytes())

    def test_each_batch_is_bounded_and_does_not_clear_outer_pending(self):
        import uuid
        for i in range(20): self.f.receipt(operationId=str(uuid.uuid4()))
        self.assertEqual(16,self.primitive().plan()['batchCount'])
        self.assertEqual(16,self.primitive().run())
        self.assertEqual(5,len(self.receipts()))
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])

if __name__ == '__main__': unittest.main()
