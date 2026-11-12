"""HMAC/source-flow model. Target API, seals and owner are explicit models."""
import hashlib
import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import uuid
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_owned_backup_verify as source_fixtures
from test_owned_children import Guard
from test_owned_memory import PortableFDBackend


class AuthenticatedDatabaseCopyTest(unittest.TestCase):
    def setUp(self):
        fixture=source_fixtures.OwnedBackupVerifyTest();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.fixture=fixture
        self.module=importlib.import_module('controlled_database_import')
        import owned_memory,owned_docker_exec
        self.exec_module=owned_docker_exec
        self.workspace_factory=mock.patch.object(self.module,'AnonymousWorkspace',
            side_effect=lambda **kw:owned_memory.AnonymousWorkspace(_backend=PortableFDBackend(),**kw))
        self.workspace_factory.start();self.addCleanup(self.workspace_factory.stop)
        self.seals=mock.patch.object(owned_docker_exec,'_input_seals',return_value=True)
        self.seals.start();self.addCleanup(self.seals.stop)
        self.events=[];self.mode=None;self.ids={};self.codes={};owner=self
        self.target=object.__new__(self.module.NewCopyTarget)  # Deliberate target model, not real ownership proof.
        self.target.root=fixture.fixture.root/'copy-target';self.target.root.mkdir(mode=0o700)
        (self.target.root/'.surveyctl').mkdir(mode=0o700)
        self.target.operations={key:str(uuid.uuid4()) for key in self.module.OPERATIONS}
        self.target.record={'installationId':str(uuid.uuid4()),'daemonId':'fixture-daemon'}
        self.target.services={'platform-db':'a'*64,'engine-db':'b'*64}
        self.target.guard=Guard();self.target.failed=False
        self.target.maintenance=SimpleNamespace(held=[],uncertain=False)
        self.target.check=lambda service,cid,allowed=None: self.mode!='target-drift' and self.target.services.get(service)==cid
        class API:
            def identity(self):return 'fixture-daemon'
            def container_absent(self,cid):return False
            def create(self,cid,command,*,stdin=False):
                eid=format(len(owner.ids)+1,'064x');owner.ids[eid]=(cid,command[-1]);owner.codes[eid]=0
                owner.events.append(('create',cid));return eid
            def stream(self,eid,output,limit,*,profile):
                owner.emit(eid,output,b'0\n');owner.events.append(('read',profile))
            def stream_input(self,eid,output,limit,*,profile,source):
                owner.events.append(('input',profile))
                if profile=='mariadb-import' and owner.mode=='unknown':
                    next(source.chunks());raise OSError('partial send outcome unknown')
                content=b''.join(source.chunks())
                owner.assertTrue(content.startswith(b'PGDMP' if profile=='postgres-import' else b'--'))
                if profile=='mariadb-import' and owner.mode=='nonzero':owner.codes[eid]=1
                owner.emit(eid,output,(hashlib.sha256(content).hexdigest()+'  -\n').encode())
            def inspect(self,eid):return {'ID':eid,'ContainerID':owner.ids[eid][0],'Running':False,'ExitCode':owner.codes[eid]}
        self.target.api=API()

    def emit(self,eid,output,payload):
        nonce=self.ids[eid][1]
        output(payload);output(self.exec_module.PayloadProof.PREFIX+nonce.encode()+b':'+hashlib.sha256(payload).hexdigest().encode()+b'\n')

    def copier(self):
        value=self.module.ControlledDatabaseCopy(self.fixture.manager,self.target)
        self.addCleanup(self.dispose_model,value);return value

    def dispose_model(self,copy):
        # No real process/socket exists in this model; dispose only test FDs
        # after assertions. This is NOT a production unknown-settlement path.
        for context in reversed(copy._exports):context.__exit__(None,None,None)
        copy._exports=[]
        for workspace in copy._outputs:workspace.close()
        copy._outputs=[]

    def test_bad_hmac_starts_no_database_exec(self):
        (self.fixture.output/'CONTROL-HMAC').write_text('0'*64+'\n')
        copy=self.copier()
        with self.assertRaisesRegex(self.fixture.module.RestoreIntegrityError,'authentication'):copy.execute(self.fixture.output)
        self.assertEqual([],self.events)

    def test_verified_source_runs_fixed_profiles_and_records_bound_source_evidence(self):
        copy=self.copier();report=copy.execute(self.fixture.output)
        self.assertTrue(report['backupHmacAuthenticated']);self.assertTrue(report['databaseInputsTransferred'])
        self.assertIs(False,report['restoreAuthorized']);self.assertIs(False,report['databaseStateVerified'])
        self.assertEqual([('input','postgres-import'),('input','mariadb-import')],[x for x in self.events if x[0]=='input'])
        from runtime_ownership import PrivateStore
        for profile in ('postgres-import','mariadb-import'):
            record=PrivateStore(self.target.root,name='runtime-exec-'+self.target.operations[profile].replace('-','')+'.json').load()
            self.assertEqual(4,record['schemaVersion']);self.assertEqual('completed',record['phase'])
            self.assertEqual('backup-hmac-control-v1',record['sourceEvidence']['kind'])
            self.assertTrue(record['inputEvidence']['delivered'])
        before=list(self.events)
        with self.assertRaises(self.exec_module.KnownExecFailure):copy.execute(self.fixture.output)
        self.assertEqual(before,self.events)

    def test_partial_success_nonzero_stops_and_cannot_replay(self):
        self.mode='nonzero';copy=self.copier()
        with self.assertRaises(self.exec_module.KnownExecFailure):copy.execute(self.fixture.output)
        self.assertTrue(copy.failed);self.assertTrue(self.target.failed);self.assertFalse(copy.uncertain)
        before=list(self.events)
        with self.assertRaises(self.exec_module.KnownExecFailure):copy.execute(self.fixture.output)
        self.assertEqual(before,self.events)

    def test_unknown_retains_exports_blocks_release_and_later_writes(self):
        self.mode='unknown';copy=self.copier()
        with self.assertRaises(self.exec_module.UncertainExec):copy.execute(self.fixture.output)
        self.assertTrue(copy.uncertain);self.assertTrue(self.target.maintenance.uncertain)
        self.assertTrue(copy._exports)
        with self.assertRaises(self.exec_module.UncertainExec):copy.release()
        before=list(self.events)
        with self.assertRaises(self.exec_module.KnownExecFailure):copy.execute(self.fixture.output)
        self.assertEqual(before,self.events)

    def test_target_identity_drift_prevents_all_database_exec(self):
        self.mode='target-drift';copy=self.copier()
        with self.assertRaises(self.exec_module.KnownExecFailure):copy.execute(self.fixture.output)
        self.assertEqual([],self.events)


    def test_effective_source_change_after_preflight_cannot_start_import(self):
        original=self.target.preflight
        def changed(copy):
            original(copy)
            path=self.fixture.fixture.target/'releases/0.2.0/.env'
            path.write_text(path.read_text()+'\nSOURCE_FIXTURE_CHANGED=true\n')
        self.target.preflight=changed
        copy=self.copier()
        with self.assertRaises(self.exec_module.KnownExecFailure):copy.execute(self.fixture.output)
        self.assertEqual([], [e for e in self.events if e[0]=='input'])

    def test_ciphertext_byte_change_after_preflight_cannot_start_import(self):
        original=self.target.preflight
        def changed(copy):
            original(copy)
            path=self.fixture.output/'postgres.dump.enc'
            with path.open('r+b') as handle:
                first=handle.read(1);handle.seek(0);handle.write(bytes([first[0]^1]))
        self.target.preflight=changed
        copy=self.copier()
        with self.assertRaises(self.exec_module.KnownExecFailure) as failure:copy.execute(self.fixture.output)
        self.assertIsInstance(failure.exception.__cause__,self.fixture.module.RestoreIntegrityError)
        self.assertEqual([], [e for e in self.events if e[0]=='input'])
        self.assertTrue(copy.failed)

    def test_raw_sealed_lease_and_caller_digest_cannot_enter_database_profile(self):
        import owned_memory
        from runtime_ownership import PrivateStore
        with owned_memory.AnonymousWorkspace(_backend=PortableFDBackend()) as workspace:
            plain=workspace/'unverified'
            with plain.open('xb') as handle:handle.write(b'PGDMP unverified caller bytes')
            with workspace.export_files(inputs=(plain,),output=workspace/'output') as (source,output):
                runner=self.target.executor('postgres-import')
                with self.assertRaises(self.exec_module.KnownExecFailure):
                    runner.execute('postgres-import',self.target.services['platform-db'],output,
                        input_lease=source,input_sha256=hashlib.sha256(b'PGDMP unverified caller bytes').hexdigest())
        self.assertEqual([],self.events)

    def test_ciphertext_change_after_pg_stops_maria_without_replaying_pg(self):
        original=self.target.api.stream_input
        def changed(eid,output,limit,*,profile,source):
            original(eid,output,limit,profile=profile,source=source)
            if profile=='postgres-import':
                path=self.fixture.output/'mariadb.sql.enc'
                with path.open('r+b') as handle:
                    first=handle.read(1);handle.seek(0);handle.write(bytes([first[0]^1]))
        self.target.api.stream_input=changed
        copy=self.copier()
        with self.assertRaises(self.exec_module.KnownExecFailure):copy.execute(self.fixture.output)
        self.assertEqual([('input','postgres-import')],[e for e in self.events if e[0]=='input'])
        before=list(self.events)
        with self.assertRaises(self.exec_module.KnownExecFailure):copy.execute(self.fixture.output)
        self.assertEqual(before,self.events)


    def test_v4_receipt_requires_source_evidence_and_known_terminal_even_after_gc(self):
        import runtime_cohort
        from runtime_ownership import PrivateStore
        from copy import deepcopy
        copy=self.copier();copy.execute(self.fixture.output)
        operation=self.target.operations['postgres-import'];name='runtime-exec-'+operation.replace('-','')+'.json'
        record=PrivateStore(self.target.root,name=name).load()
        probe=object.__new__(runtime_cohort.CohortProbe);probe.api=self.target.api;probe.reader=lambda _:None
        owner=self.target.record
        self.assertTrue(probe._receipt(name,record,owner))
        def missing(_):raise self.exec_module.ExecMetadataGone('known 404')
        self.target.api.inspect=missing
        self.assertTrue(probe._receipt(name,record,owner))
        for patch in ({'controlSha256':True},{'releaseManifestSha256':'bad'},{'kind':'caller-sha'},{'extra':False}):
            row=deepcopy(record);row['sourceEvidence'].update(patch)
            self.assertFalse(probe._receipt(name,row,owner))
        for patch in ({'schemaVersion':True},{'schemaVersion':3},{'phase':'released'},{'completionEvidence':None}):
            row=deepcopy(record);row.update(patch)
            self.assertFalse(probe._receipt(name,row,owner))

    def test_control_symlink_is_rejected_without_reading_referent(self):
        outside=self.fixture.fixture.root/'synthetic-private';outside.write_bytes(b'synthetic-not-a-real-secret')
        path=self.fixture.output/'CONTROL-HMAC';path.unlink();path.symlink_to(outside)
        original=Path.read_bytes;reads=[]
        def observed(value):
            if value==path:reads.append(str(value))
            return original(value)
        copy=self.copier()
        with mock.patch.object(Path,'read_bytes',observed):
            with self.assertRaises(self.fixture.module.RestoreIntegrityError):copy.execute(self.fixture.output)
        self.assertEqual([],reads)
        self.assertEqual([],self.events)


class NewCopyTargetBoundaryTest(unittest.TestCase):
    def test_requires_actual_controller_types_and_canonical_operation_ids(self):
        import controlled_database_import as module
        with self.assertRaises(module.KnownExecFailure):module.NewCopyTarget({}, {}, {}, {})
        for value in (None, True, 'bad', 42):self.assertFalse(module._uuid(value))

    def test_resource_generation_network_ports_and_volume_drift_reject(self):
        from copy import deepcopy
        import controlled_database_import as module
        target=object.__new__(module.NewCopyTarget)
        target.failed=False;target.services={'platform-db':'a'*64,'engine-db':'b'*64}
        target.record={'installationId':'fixture','resources':{'containers':{'a'*64:'pg','b'*64:'maria'},
            'volumes':{'fixture_data':'volume-fingerprint'},'networks':{}}}
        target.handoff=SimpleNamespace(inspect=lambda:None)
        mode={'mode':'none','ports':None,'networks':{'none':{}},'publishAll':False,'published':{'5432/tcp':None},
            'privileged':False,'pid':'','ipc':'private','uts':'','cgroup':'private','userns':'','capAdd':None,
            'devices':None,'deviceRequests':None,'volumesFrom':None,'securityOpt':['no-new-privileges:true'],
            'memory':512*1024*1024,'cpu':1000000000,'pids':128};tamper={}
        driver=SimpleNamespace(_json=lambda _:deepcopy(mode))
        def rows(kind):
            if kind=='containers':return [{'id':cid} for cid in target.services.values()]
            if kind=='volumes':return [{'name':'fixture_data'}]
            return []
        driver._rows=rows;driver._volume=lambda *args:('bad' if tamper.get('volume') else 'volume-fingerprint',[])
        def container(cid,allowed=None):
            name=next(name for name,value in target.services.items() if value==cid)
            driver._last_container_snapshot={'owner':'wrong' if tamper.get('owner') else 'fixture','service':name}
            return True
        target.maintenance=SimpleNamespace(container_check=container)
        target.protocol=SimpleNamespace(store=SimpleNamespace(load=lambda:deepcopy(target.record)),
            _assert_claim=lambda _:None,driver=driver,_record=lambda _:deepcopy(target.record))
        target.probe=lambda:'quiescent'
        self.assertTrue(target.check('platform-db','a'*64))
        for patch in ({'mode':'bridge'},{'ports':{'5432/tcp':[{}]}},{'networks':{'none':{},'external':{}}},
                {'publishAll':True},{'published':{'5432/tcp':[{}]}},{'privileged':True},
                {'pid':'host'},{'ipc':'host'},{'capAdd':['SYS_ADMIN']},{'devices':[{}]},
                {'securityOpt':['seccomp=unconfined']},{'memory':0},{'pids':None}):
            original=deepcopy(mode);mode.update(patch)
            self.assertFalse(target.check('platform-db','a'*64));mode.clear();mode.update(original)
        for key in ('owner','volume'):
            tamper[key]=True;self.assertFalse(target.check('platform-db','a'*64));tamper.clear()
        self.assertFalse(target.check('platform-db','b'*64))


class NativeCopyAcceptanceTest(unittest.TestCase):
    def test_cleanup_checks_every_scope_before_delete_and_stops_on_unknown(self):
        import importlib.util
        path=Path(__file__).parent/'integration/authenticated_database_copy_native.py'
        sys.path.insert(0,str(path.parent))
        try:
            spec=importlib.util.spec_from_file_location('auth_copy_cleanup',path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        finally:sys.path.remove(str(path.parent))
        events=[]
        def protocol(name,fail=None):
            def call(stage):
                events.append((name,stage))
                if stage==fail:raise OSError('unknown fixture outcome')
            return SimpleNamespace(preflight_cleanup=lambda _:call('check'),cleanup=lambda _:call('delete'))
        self.assertFalse(module.cleanup_protocols([protocol('source'),protocol('target','check')],True))
        self.assertEqual([('source','check'),('target','check')],events)
        events.clear()
        self.assertFalse(module.cleanup_protocols([protocol('source'),protocol('target','delete')],True))
        self.assertEqual([('source','check'),('target','check'),('target','delete')],events)
        events.clear()
        self.assertFalse(module.cleanup_protocols([protocol('source')],False));self.assertEqual([],events)

    def test_known_partial_is_distinct_from_full_copy_and_no_qualification_upgrade(self):
        import importlib.util
        path=Path(__file__).parent/'integration/authenticated_database_copy_native.py'
        sys.path.insert(0,str(path.parent))
        try:
            spec=importlib.util.spec_from_file_location('auth_copy_native_acceptance',path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        finally:sys.path.remove(str(path.parent))
        hashes={'fixture':'a'*64}
        report={'schemaVersion':1,'status':'passed','fixtureCleanup':'verified','sourceBefore':hashes,'sourceAfter':hashes,'failMaria':False}
        for key in ('targetWasFresh','resourceIsolationVerified','postgresDataVerified','postSnapshotWritesPreserved',
                'sourcePersonnelUnchanged','applicationsNeverStarted','pendingIntentsRetainedDuringCopy',
                'backupHmacAuthenticated','controlledDatabaseInputReceipts','sourceCryptoChildrenEnded',
                'targetCohortUsesKernelReader','retryPrevented','badHmacStartsNoExec','badHmacStartsNoChild',
                'mariadbDataVerified','targetPersonnelInvalidated','targetRestoreAuditRecorded'):report[key]=True
        for key in module.UNQUALIFIED:report[key]=False
        self.assertTrue(module.passed(report,hashes))
        report.update(failMaria=True,knownPartialCopy=True,mariadbUntouched=True,mariadbDataVerified=False,
            targetPersonnelInvalidated=False,targetRestoreAuditRecorded=False)
        self.assertTrue(module.passed(report,hashes))
        for patch in ({'schemaVersion':True},{'failMaria':1},{'candidateQualified':True},{'mariadbUntouched':False}):
            self.assertFalse(module.passed({**report,**patch},hashes))

if __name__=='__main__':unittest.main()
