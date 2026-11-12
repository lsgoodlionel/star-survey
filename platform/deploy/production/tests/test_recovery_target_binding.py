"""Target binding model: signature tool and Compose are explicit test seams."""
import hashlib
import importlib
from pathlib import Path
import sys
import tempfile
import os
import time
from types import SimpleNamespace
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class RecoveryTargetBindingTest(unittest.TestCase):
    def setUp(self):
        self.m=importlib.import_module('recovery_target_binding')
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.assets=self.root/'assets';self.assets.mkdir(mode=0o700)
        (self.assets/'release.json').write_bytes(b'fixed synthetic manifest')
        self.sha=hashlib.sha256((self.assets/'release.json').read_bytes()).hexdigest()
        self.manager=SimpleNamespace(target=self.root/'staged')
        self.data={'version':'0.4.0-rc.1','manifestSha256':self.sha,'contract':{'services':{
            'platform-db':{'imageIds':['sha256:'+'a'*64]},'engine-db':{'imageIds':['sha256:'+'b'*64]}}}}
        self.manifest=SimpleNamespace(sha256=self.sha,raw={'commit':'c'*40})
        self.manager._release_manifest=lambda _:self.manifest
        # Explicit code-admission model; packaging tests exercise real hashes.
        self.manager._verify_owned_support=lambda _:{'verify_release':(self.m.VERIFIER,self.m.VERIFIER.read_bytes())}
        self.signature=mock.patch.object(self.m,'_run_verifier',return_value=SimpleNamespace(returncode=0))
        self.run=self.signature.start();self.addCleanup(self.signature.stop)
        self.compose=mock.patch.object(self.m,'source_contract',side_effect=lambda *_:self.data)
        self.compose.start();self.addCleanup(self.compose.stop)
    def bind(self):
        return self.m.RecoveryTargetBinding(self.manager,'0.4.0-rc.1',self.assets,self.sha,'c'*40)
    def test_fixed_verifier_and_explicit_non_qualification(self):
        bound=self.bind();report=bound.recheck()
        command=self.run.call_args.args[0]
        self.assertEqual('bash',command[0]);self.assertTrue(command[1].endswith('/release/verify_release.sh'))
        self.assertEqual('lsgoodlionel/star-survey',command[command.index('--repository')+1])
        self.assertEqual('v0.4.0-rc.1',command[command.index('--source-tag')+1])
        self.assertEqual('c'*40,command[command.index('--target-commit')+1])
        self.assertTrue(report['releaseSignaturesVerified'])
        self.assertIs(False,report['targetConfigurationVerified']);self.assertIs(False,report['restoreAuthorized'])
        self.assertEqual(180,self.run.call_args.kwargs['timeout'])
    def test_signature_nonzero_and_timeout_reject(self):
        for outcome in (SimpleNamespace(returncode=1),self.m.subprocess.TimeoutExpired('fixed verifier',180)):
            with self.subTest(outcome=type(outcome).__name__):
                if isinstance(outcome,Exception):self.run.side_effect=outcome
                else:self.run.return_value=outcome;self.run.side_effect=None
                with self.assertRaises(self.m.TargetBindingError):self.bind()
    def test_manifest_commit_and_configuration_drift_reject(self):
        bound=self.bind();self.data['version']='0.4.1-rc.1'
        with self.assertRaises(self.m.TargetBindingError):bound.recheck()
        self.data['version']='0.4.0-rc.1';self.manifest.raw['commit']='d'*40
        with self.assertRaises(self.m.TargetBindingError):bound.recheck()
    def test_asset_mutation_during_or_after_verification_rejects(self):
        def changed(*_,**__):
            (self.assets/'release.json').write_bytes(b'changed');return SimpleNamespace(returncode=0)
        self.run.side_effect=changed
        with self.assertRaises(self.m.TargetBindingError):self.bind()
        self.run.side_effect=None;(self.assets/'release.json').write_bytes(b'fixed synthetic manifest')
        bound=self.bind();(self.assets/'extra').write_text('untracked')
        with self.assertRaises(self.m.TargetBindingError):bound.recheck()
    def test_symlink_and_wrong_manifest_hash_reject_before_verifier(self):
        (self.assets/'extra').symlink_to(self.assets/'release.json')
        with self.assertRaises(self.m.TargetBindingError):self.bind()
        self.run.assert_not_called()
    def test_image_mapping_requires_current_target_check(self):
        bound=self.bind()
        target=SimpleNamespace(failed=False,services={'platform-db':'x','engine-db':'y'},
            protocol=SimpleNamespace(driver=SimpleNamespace(contract=self.data['contract'])),check=lambda *_:True)
        with mock.patch.object(self.m,'NewCopyTarget',SimpleNamespace):
            self.assertTrue(bound.check_database_target(target)['databaseImagesBound'])
            target.check=lambda *_:False
            with self.assertRaises(self.m.TargetBindingError):bound.check_database_target(target)

    def test_unadmitted_or_different_verifier_cannot_start_signature_tool(self):
        for captured in ({},{'verify_release':(self.m.VERIFIER,b'changed helper')},
                {'verify_release':(self.root/'foreign.sh',self.m.VERIFIER.read_bytes())}):
            self.manager._verify_owned_support=lambda _,value=captured:value
            with self.assertRaises(self.m.TargetBindingError):self.bind()
            self.run.assert_not_called()

    def test_binding_is_rechecked_before_authentication_and_each_import(self):
        from test_authenticated_database_copy import AuthenticatedDatabaseCopyTest
        fixture=AuthenticatedDatabaseCopyTest();fixture.setUp();self.addCleanup(fixture.doCleanups)
        calls=[]
        class Bound:
            def check_database_target(_,target):
                calls.append(len(fixture.events))
                if len(calls)==3:raise self.m.TargetBindingError('target drift after PG')
        copy=fixture.copier()
        with mock.patch.object(self.m,'RecoveryTargetBinding',Bound):
            with self.assertRaises(fixture.exec_module.KnownExecFailure):copy.execute_bound(fixture.fixture.output,Bound())
        self.assertEqual(3,len(calls))
        self.assertEqual(0,calls[0])
        self.assertEqual([('input','postgres-import')],[e for e in fixture.events if e[0]=='input'])
        self.assertTrue(copy.failed)
        with self.assertRaises(fixture.exec_module.KnownExecFailure):copy.execute(fixture.fixture.output)

class RealVerifierLifecycleTest(unittest.TestCase):
    def test_timeout_stops_real_group_child_and_preserves_foreign_parent_fd(self):
        import recovery_target_binding as m
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);pidfile=root/'child.pid';sentinel=root/'late-write';foreign=root/'foreign';fdstate=root/'fd-state'
            fd=os.open(foreign,os.O_CREAT|os.O_RDWR,0o600);os.set_inheritable(fd,True)
            script="""import os,sys,time
try:
 os.fstat(int(sys.argv[3])); state='inherited'
except OSError: state='closed'
open(sys.argv[4],'w').write(state)
pid=os.fork()
if pid==0:
 time.sleep(1.2)
 open(sys.argv[2],'w').write('late child survived')
 os._exit(0)
open(sys.argv[1],'w').write(str(pid))
time.sleep(30)
"""
            command=[sys.executable,'-c',script,str(pidfile),str(sentinel),str(fd),str(fdstate)]
            try:
                with mock.patch.object(m.subprocess,'Popen',wraps=m.subprocess.Popen) as launched:
                    with self.assertRaises(m.TargetBindingError) as failure:
                        m._run_verifier(command,timeout=0.5,cleanup_timeout=2)
                self.assertTrue(launched.call_args.kwargs['start_new_session'])
                self.assertTrue(launched.call_args.kwargs['close_fds'])
                for key in ('stdin','stdout','stderr'):
                    self.assertEqual(m.subprocess.DEVNULL,launched.call_args.kwargs[key])
                os.fstat(fd)
                time.sleep(1.3)
                self.assertTrue(pidfile.exists());self.assertFalse(sentinel.exists())
                self.assertEqual('closed',fdstate.read_text())
                pid=int(pidfile.read_text())
                state=m.subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True,check=False)
                self.assertTrue(not state.stdout.strip() or state.stdout.strip().startswith('Z'),state.stdout)
            finally:os.close(fd)

    def test_unknown_group_is_uncertain_and_never_resignaled_after_leader_reap(self):
        import recovery_target_binding as m
        with mock.patch.object(m,'_group_absent',return_value=False),mock.patch.object(m.os,'killpg') as signal_group:
            with self.assertRaises(m.TargetVerificationUncertain) as failure:
                m._run_verifier([sys.executable,'-c','pass'],timeout=3)
        signal_group.assert_not_called()
        self.assertIsInstance(failure.exception,m.UncertainExec)
        self.assertEqual(0,failure.exception.verifier_process.returncode)

    def test_failed_group_termination_is_uncertain_and_not_retried(self):
        import recovery_target_binding as m
        process=mock.Mock(pid=999999999,returncode=None)
        process.wait.side_effect=m.subprocess.TimeoutExpired('fixture',1)
        with mock.patch.object(m.subprocess,'Popen',return_value=process),mock.patch.object(m.os,'killpg',side_effect=PermissionError) as signal_group:
            with self.assertRaises(m.TargetVerificationUncertain) as failure:m._run_verifier(['fixed-fixture'],timeout=1)
        self.assertIs(process,failure.exception.verifier_process)
        self.assertEqual(1,signal_group.call_count)
        self.assertEqual(1,process.wait.call_count)

if __name__=='__main__':unittest.main()
