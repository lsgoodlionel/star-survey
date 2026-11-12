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

    def test_exact_capacity_reserves_intent_temporary_before_writing(self):
        control=self.f.target/'.surveyctl'
        while len(list(control.iterdir()))<256:
            (control/('filler-'+str(len(list(control.iterdir()))))).touch(mode=0o600)
        with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
        self.assertFalse(self.calls); self.assertFalse(self.intent.exists())
        self.assertEqual(256,len(list(control.iterdir())))

    def test_twenty_one_receipts_use_bounded_full_verification_passes(self):
        import uuid
        for i in range(20): self.f.receipt(operationId=str(uuid.uuid4()))
        counts={'inspect':0,'absent':0,'resources':0}
        inspect,absent=self.f.api.inspect,self.f.api.container_absent
        def query(identity): counts['inspect']+=1; return inspect(identity)
        def presence(identity): counts['absent']+=1; return absent(identity)
        self.f.api.inspect=query; self.f.api.container_absent=presence
        def resources():
            counts['resources']+=1
            if self.f.probe()!='quiescent': raise self.module.ownership.OwnershipError('unverified')
            return self.f.store_type(self.f.target).load()
        self.check=resources
        self.assertEqual(16,self.primitive().run())
        self.observed_queries=dict(counts)
        self.assertLessEqual(counts['resources'],3)
        self.assertLessEqual(counts['inspect'],192)
        self.assertLessEqual(counts['absent'],192)

    def test_one_free_slot_is_enough_for_intent_atomic_publication(self):
        control=self.f.target/'.surveyctl'
        while len(list(control.iterdir()))<255:
            (control/('filler-'+str(len(list(control.iterdir()))))).touch(mode=0o600)
        begin=self.begin
        def atomic_begin():
            temporary=control/'intent-temporary'; temporary.touch(mode=0o600)
            self.assertEqual(256,len(list(control.iterdir())))
            temporary.unlink(); begin()
        self.begin=atomic_begin
        self.assertEqual(1,self.primitive().run())
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])

    def test_new_pending_receipt_during_batch_stops_without_clearing_intent(self):
        import uuid
        self.f.receipt(operationId=str(uuid.uuid4()))
        unlink=self.module.os.unlink
        def changed(name,**kwargs):
            unlink(name,**kwargs)
            self.f.receipt(operationId=str(uuid.uuid4()),phase='released',exitCode=None)
        with patch.object(self.module.os,'unlink',side_effect=changed):
            with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])
        self.assertTrue(any(json.loads(path.read_text())['phase']=='released' for path in self.receipts()))

    def test_post_batch_resource_drift_keeps_pending(self):
        count=[0]
        def resources():
            count[0]+=1
            owner=self.f.store_type(self.f.target).load()
            return dict(owner,daemonId='changed') if count[0]>=3 else owner
        self.check=resources
        with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().run()
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])

    def test_same_bytes_replacement_during_local_load_is_rejected_before_unlink(self):
        primitive=self.primitive(); local=primitive._local_check
        load=self.f.store_type.load; state={'armed':False,'replaced':False}
        path=self.receipts()[0]; original=path.stat().st_ino
        def checked(*args):
            state['armed']=True
            try: return local(*args)
            finally: state['armed']=False
        def swapped(store):
            if state['armed'] and not state['replaced'] and store.NAME.startswith('runtime-exec-'):
                replacement=self.f.target/'replacement'
                replacement.write_bytes(path.read_bytes()); replacement.chmod(0o600)
                os.replace(replacement,path); state['replaced']=True
            return load(store)
        primitive._local_check=checked
        with patch.object(self.f.store_type,'load',swapped), patch.object(self.module.os,'unlink',wraps=os.unlink) as unlink:
            with self.assertRaises(self.module.ownership.OwnershipError): primitive.run()
            self.assertEqual(0,unlink.call_count)
        self.assertTrue(state['replaced']); self.assertNotEqual(original,path.stat().st_ino)
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])

    def test_replacement_after_local_check_is_rejected_by_final_target_check(self):
        primitive=self.primitive(); local=primitive._local_check
        path=self.receipts()[0]; replaced=[]
        def checked(*args):
            local(*args)
            if not replaced:
                replacement=self.f.target/'replacement'
                replacement.write_bytes(path.read_bytes()); replacement.chmod(0o600)
                os.replace(replacement,path); replaced.append(True)
        primitive._local_check=checked
        with patch.object(self.module.os,'unlink',wraps=os.unlink) as unlink:
            with self.assertRaises(self.module.ownership.OwnershipError): primitive.run()
            self.assertEqual(0,unlink.call_count)
        self.assertTrue(path.exists()); self.assertEqual([True],replaced)
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])

    def test_capacity_reservation_reclaims_only_when_needed_before_helpers(self):
        control=self.f.target/'.surveyctl'
        while len(list(control.iterdir()))<252:
            (control/('filler-'+str(len(list(control.iterdir()))))).touch(mode=0o600)
        # 252 + three receipts + missing intent + one temporary is 257.
        self.primitive().reserve(3)
        self.assertFalse(self.receipts())
        self.assertLessEqual(len(list(control.iterdir()))+3+1,256)
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])

    def test_impossible_reservation_is_read_only_and_invalid_slots_rejected(self):
        control=self.f.target/'.surveyctl'
        while len(list(control.iterdir()))<255:
            (control/('filler-'+str(len(list(control.iterdir()))))).touch(mode=0o600)
        for slots in (3,True,-1,257):
            with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().reserve(slots)
        self.assertFalse(self.calls); self.assertFalse(self.intent.exists())
        self.assertEqual(1,len(self.receipts()))

    def test_reserve_leaves_terminal_history_when_capacity_already_sufficient(self):
        before=self.receipts()[0].read_bytes()
        self.primitive().reserve(3)
        self.assertEqual(before,self.receipts()[0].read_bytes())
        self.assertFalse(self.calls); self.assertFalse(self.intent.exists())

    def test_late_resource_result_cannot_admit_backup_after_soft_deadline(self):
        clock=[100]
        def resources():
            clock[0]=131
            return self.f.store_type(self.f.target).load()
        self.check=resources
        with patch('time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().reserve(3)
        self.assertFalse(self.calls); self.assertEqual(1,len(self.receipts()))

    def test_reservation_deadline_after_pending_never_deletes_or_admits(self):
        control=self.f.target/'.surveyctl'
        while len(list(control.iterdir()))<252:
            (control/('filler-'+str(len(list(control.iterdir()))))).touch(mode=0o600)
        clock=[100]; calls=[0]
        def resources():
            calls[0]+=1
            if calls[0]==3: clock[0]=131
            return self.f.store_type(self.f.target).load()
        self.check=resources
        with patch('time.monotonic',side_effect=lambda:clock[0]), patch.object(self.module.os,'unlink',wraps=os.unlink) as unlink:
            with self.assertRaises(self.module.ownership.OwnershipError): self.primitive().reserve(3)
            unlink.assert_not_called()
        self.assertEqual('pending',json.loads(self.intent.read_text())['state'])
        self.assertEqual(1,len(self.receipts()))

if __name__ == '__main__': unittest.main()
