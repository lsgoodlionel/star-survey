"""Real ownership protocol with model daemon; not native lifecycle evidence."""
import copy
from contextlib import contextmanager
import importlib
import importlib.util
import json
import io
import contextlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_runtime_ownership import Store, Daemon
import runtime_ownership as ownership


class NativeCleanupProofTest(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module('native_cleanup_proof')
        self.store, self.daemon = Store(), Daemon()
        self.target = Path('/fixture/primary')
        self.protocol = ownership.OwnershipProtocol(self.target, self.daemon, self.store,
            'example/image@sha256:' + 'a' * 64)
        self.protocol.acquire()
        def create():
            self.daemon.snapshot.update(containers={'1'*64: 'a'*64},
                networks={'2'*64: 'b'*64}, volumes={'survey-production_data': 'd'*64})
        self.protocol.mutate('install', create)
        self.binding = dict(schemaVersion=1, version='0.3.21-rc.1', commit='a'*40,
            manifestSha256='b'*64, bundleSha256='c'*64, runId='123', attempt='1',
            ubuntu='24.04', architecture='amd64')
        self.state = dict(status='healthy', version=self.binding['version'])
        self.locked = False
        self.calls = 0
        self.runtime = SimpleNamespace(protocol=self.protocol,
            data=dict(version=self.binding['version'], manifestSha256='b'*64))
        self.manifest = SimpleNamespace(version=self.binding['version'], sha256='b'*64,
            raw=dict(commit='a'*40, assets=dict(bundles=dict(amd64=dict(sha256='c'*64)))))
        self.manager = SimpleNamespace(target=self.target, _mutation_lock=self.lock,
            _read_state=self.read_state, _runtime=lambda v:self.runtime,
            _release_manifest=lambda v:self.manifest, uninstall=self.uninstall)
        self.after = lambda:None

    @contextmanager
    def lock(self, **kwargs):
        self.assertFalse(self.locked)
        self.assertEqual('uninstall', kwargs['operation'])
        self.locked = True
        try: yield
        finally: self.locked = False

    def read_state(self, **kwargs):
        self.assertTrue(self.locked)
        return copy.deepcopy(self.state)

    def uninstall(self, purge_data, confirmation):
        self.assertTrue(self.locked)
        self.assertIs(False, purge_data)
        self.assertIsNone(confirmation)
        self.calls += 1
        self.protocol.cleanup(False)
        self.state.update(status='uninstalled', dataPurged=False)
        self.after()

    def test_same_lock_independent_after_check_preserves_volumes_and_claim(self):
        claim = copy.deepcopy(self.daemon.claims)
        result = self.m.prove_uninstall(self.manager, self.binding)
        self.assertEqual(1, self.calls)
        self.assertEqual(claim, self.daemon.claims)
        self.assertEqual({'survey-production_data': 'd'*64}, self.daemon.snapshot['volumes'])
        self.assertEqual(self.binding, result['candidate'])
        self.assertIs(True, result['ordinaryUninstallVerified'])
        self.assertIs(False, result['hostCleanupVerified'])
        self.assertIs(False, result['candidateQualified'])
        self.assertNotIn('resources', result)

    def test_binding_mismatch_rejects_before_any_deletion(self):
        for key, value in [('commit','d'*40), ('manifestSha256','d'*64),
                ('bundleSha256','d'*64), ('version','0.3.20-rc.1')]:
            with self.subTest(key=key):
                binding = dict(self.binding, **{key:value})
                with self.assertRaises(self.m.CleanupProofError):
                    self.m.prove_uninstall(self.manager, binding)
        self.assertEqual(0, self.calls)

    def test_unknown_foreign_and_pending_before_cleanup_never_delete(self):
        for field, value in [('cohort','unknown'), ('foreign',True)]:
            with self.subTest(field=field):
                self.daemon.snapshot[field] = value
                with self.assertRaises(self.m.CleanupProofError):
                    self.m.prove_uninstall(self.manager,self.binding)
                self.daemon.snapshot[field] = 'quiescent' if field=='cohort' else False
        self.store.record['phase'] = 'mutation-pending'
        with self.assertRaises(self.m.CleanupProofError):
            self.m.prove_uninstall(self.manager,self.binding)
        self.assertEqual(0,self.calls)

    def test_postcheck_failure_does_not_retry_or_claim_success(self):
        for fault in ('query','container','network','volume','claim','state','purge'):
            with self.subTest(fault=fault):
                self.setUp()
                def corrupt():
                    if fault=='query': self.daemon.fail_query=True
                    elif fault in ('container','network'):
                        self.daemon.snapshot[fault+'s']['9'*64]='e'*64
                    elif fault=='volume': self.daemon.snapshot['volumes'].clear()
                    elif fault=='claim': self.daemon.claims.clear()
                    elif fault=='state': self.state['status']='recovery_required'
                    elif fault=='purge': self.state['dataPurged']=True
                self.after=corrupt
                with self.assertRaises(self.m.CleanupProofError):
                    self.m.prove_uninstall(self.manager,self.binding)
                self.assertEqual(1,self.calls)

    def test_binding_shape_is_strict_and_does_not_accept_boolean_schema(self):
        for binding in (dict(self.binding,schemaVersion=True),dict(self.binding,runId='0'),
                dict(self.binding,ubuntu='26.04'),dict(self.binding,architecture='x86_64'),
                dict(self.binding,extra='untrusted')):
            with self.subTest(binding=binding), self.assertRaises(self.m.CleanupProofError):
                self.m.prove_uninstall(self.manager,binding)
        self.assertEqual(0,self.calls)

    def test_collected_candidate_binds_the_cleanup_proof_module(self):
        production=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('native_cleanup_builder', production/'release/build_release.py')
        builder=importlib.util.module_from_spec(spec)
        sys.path.insert(0,str(production/'release'))
        try: spec.loader.exec_module(builder)
        finally: sys.path.remove(str(production/'release'))
        payload=builder.collect_runtime(production,production.parents[2])
        self.assertIn('native_cleanup_proof.py',set(payload))
        self.assertEqual((production/'native_cleanup_proof.py').read_bytes(),payload['native_cleanup_proof.py'])

    def test_actual_controller_postcheck_failure_keeps_pending_and_blocks_retry(self):
        self.actual_controller_case(unknown=True)

    def test_actual_controller_success_finishes_intent_after_independent_postcheck(self):
        self.actual_controller_case(unknown=False)

    def actual_controller_case(self, *, unknown):
        import test_surveyctl as fixtures
        f=fixtures.SurveyctlTest(); f.setUp(); self.addCleanup(f.doCleanups)
        manifest_path,_=fixtures.write_test_release(f.root,version=self.binding['version'])
        manager=f.manager()
        manager.install(manifest_path,'survey.example.com','operations','operations@example.com')
        manifest=manager._release_manifest(self.binding['version'])
        architecture=next(iter(manifest.raw['assets']['bundles']))
        binding=dict(self.binding,manifestSha256=manifest.sha256,commit=manifest.raw['commit'],
            architecture=architecture,bundleSha256=manifest.raw['assets']['bundles'][architecture]['sha256'])
        self.daemon=Daemon()
        protocol=ownership.OwnershipProtocol(manager.target,self.daemon,Store(),
            'example/image@sha256:'+'a'*64)
        # A separate modeled claim for this actual filesystem installation.
        self.daemon.claims.clear(); protocol.acquire()
        def populate():
            self.daemon.snapshot.update(containers={'1'*64:'a'*64},networks={'2'*64:'b'*64},
                volumes={'survey-production_data':'d'*64})
        protocol.mutate('install',populate)
        def cleanup(purge,host_purge=None):
            self.assertEqual(2,manager._mutation_depth)  # Outer proof + normal public uninstall.
            protocol.cleanup(purge,host_purge)
            if unknown: self.daemon.snapshot['cohort']='unknown'
        runtime=SimpleNamespace(protocol=protocol,data=dict(version=manifest.version,manifestSha256=manifest.sha256),
            preflight_cleanup=protocol.preflight_cleanup,cleanup=cleanup)
        manager._runtime_factory=lambda *a,**kw:runtime
        if unknown:
            with self.assertRaises(self.m.CleanupProofError): self.m.prove_uninstall(manager,binding)
        else:
            result=self.m.prove_uninstall(manager,binding)
            self.assertIs(True,result['ordinaryUninstallVerified'])
        control=manager.control_dir
        self.assertEqual('pending' if unknown else 'complete',json.loads((control/'operation.intent').read_text())['state'])
        self.assertEqual('uninstalled',json.loads(manager.state_path.read_text())['status'])
        self.assertTrue((manager.target/'shared').is_dir())
        if unknown:
            count=len(self.daemon.remove_calls)
            with self.assertRaises(self.m.CleanupProofError): self.m.prove_uninstall(manager,binding)
            self.assertEqual(count,len(self.daemon.remove_calls))

    def test_lock_finalization_error_never_returns_success(self):
        @contextmanager
        def broken(**kwargs):
            with self.lock(**kwargs): yield
            raise OSError('synthetic lock close failure')
        self.manager._mutation_lock=broken
        with self.assertRaises(self.m.CleanupProofError): self.m.prove_uninstall(self.manager,self.binding)
        self.assertEqual(1,self.calls)

    def test_cli_calls_the_same_proof_and_only_prints_bounded_result(self):
        import surveyctl
        args=['--target',str(self.target)]
        for key,value in self.binding.items():
            if key!='schemaVersion':
                option={'manifestSha256':'manifest-sha256','bundleSha256':'bundle-sha256',
                    'runId':'run-id'}.get(key,key)
                args += ['--'+option,value]
        output=io.StringIO()
        with mock.patch.object(surveyctl,'SurveyManager',return_value=self.manager), contextlib.redirect_stdout(output):
            code=self.m.main(args)
        self.assertEqual(0,code)
        self.assertIs(True,json.loads(output.getvalue())['ordinaryUninstallVerified'])
        self.assertEqual(1,self.calls)

    def test_cli_unknown_postcheck_is_exit_four_without_result_or_exception_leak(self):
        import surveyctl
        args=['--target',str(self.target)]
        for key,value in self.binding.items():
            if key!='schemaVersion':
                option={'manifestSha256':'manifest-sha256','bundleSha256':'bundle-sha256',
                    'runId':'run-id'}.get(key,key)
                args += ['--'+option,value]
        def fail(): raise OSError('synthetic-sensitive-body-never-print')
        self.after=fail
        output,errors=io.StringIO(),io.StringIO()
        with mock.patch.object(surveyctl,'SurveyManager',return_value=self.manager), \
                contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
            code=self.m.main(args)
        self.assertEqual(4,code)
        self.assertEqual('',output.getvalue())
        self.assertNotIn('synthetic-sensitive-body',errors.getvalue())
        self.assertEqual(1,self.calls)

    def test_settled_failed_install_uses_the_same_ordinary_cleanup_contract(self):
        self.state['status']='install_failed'
        self.store.record['phase']='settled-failure'
        result=self.m.prove_uninstall(self.manager,self.binding)
        self.assertIs(True,result['ordinaryUninstallVerified'])
        self.assertEqual(1,result['retainedVolumeCount'])


if __name__ == '__main__': unittest.main()
