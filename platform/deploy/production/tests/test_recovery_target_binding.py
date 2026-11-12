"""Target binding model: signature tool and Compose are explicit test seams."""
import hashlib
import importlib
from pathlib import Path
import sys
import tempfile
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
        self.signature=mock.patch.object(self.m.subprocess,'run',return_value=SimpleNamespace(returncode=0))
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
        self.assertEqual(self.m.subprocess.DEVNULL,self.run.call_args.kwargs['stdout'])
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

if __name__=='__main__':unittest.main()
