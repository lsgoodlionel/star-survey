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
import tempfile
import subprocess
import py_compile
import os
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
        self.installed={key:self.binding[key] for key in ('version','manifestSha256','architecture','bundleSha256')}
        self.admissions=[]
        self.manager = SimpleNamespace(target=self.target, _mutation_lock=self.lock,
            _read_state=self.read_state, _runtime=lambda v:self.runtime,
            _release_dir=lambda v:self.target/'releases'/v,
            _verify_staged_release=lambda *a:copy.deepcopy(self.installed),
            _load_owned_support=lambda version:self.admissions.append(version),
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

    def test_actual_controller_rejects_other_valid_manifest_architecture_before_uninstall(self):
        self.actual_controller_case(unknown=False, wrong_bundle=True)

    def actual_controller_case(self, *, unknown, wrong_bundle=False):
        import test_surveyctl as fixtures
        f=fixtures.SurveyctlTest(); f.setUp(); self.addCleanup(f.doCleanups)
        manifest_path,_=fixtures.write_test_release(f.root,version=self.binding['version'])
        if wrong_bundle:
            manifest_data=json.loads(manifest_path.read_text())
            installed_arch=next(iter(manifest_data['assets']['bundles']))
            other_arch='amd64' if installed_arch=='arm64' else 'arm64'
            manifest_data['supportedHosts']['architectures'].append(other_arch)
            manifest_data['assets']['bundles'][other_arch]=dict(
                name='survey-'+self.binding['version']+'-linux-'+other_arch+'.tar.gz',sha256='f'*64)
            manifest_path.write_text(json.dumps(manifest_data))
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
        if wrong_bundle:
            binding.update(architecture=other_arch,bundleSha256='f'*64)
            with self.assertRaises(self.m.CleanupProofError): self.m.prove_uninstall(manager,binding)
            self.assertEqual([],self.daemon.remove_calls)
            self.assertEqual('healthy',json.loads(manager.state_path.read_text())['status'])
            return
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
            spec=importlib.util.spec_from_file_location('failed_native_fixture',
                Path(__file__).parent/'integration/owned_maintenance_native.py')
            native=importlib.util.module_from_spec(spec); spec.loader.exec_module(native)
            report={'status':'failed'}
            with mock.patch.object(native,'fixture_cleanup') as cleanup, \
                    mock.patch.object(native,'PENDING_FIXTURES',[]):
                native.finalize_fixture(manager,'synthetic-namespace',None,report)
                cleanup.assert_not_called()
                self.assertEqual([manager],native.PENDING_FIXTURES)
            self.assertEqual('preserved-failed-or-unknown',report['fixtureCleanup'])
            self.assertEqual('pending',json.loads((control/'operation.intent').read_text())['state'])
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
        args=['--target',str(self.target)]
        for key,value in self.binding.items():
            if key!='schemaVersion':
                option={'manifestSha256':'manifest-sha256','bundleSha256':'bundle-sha256',
                    'runId':'run-id'}.get(key,key)
                args += ['--'+option,value]
        output=io.StringIO()
        controller=SimpleNamespace(SurveyManager=lambda target:self.manager,OWNED_IMPORT_ORDER=())
        with mock.patch.object(self.m,'_bootstrap_controller',return_value=controller), contextlib.redirect_stdout(output):
            code=self.m.main(args)
        self.assertEqual(0,code)
        self.assertIs(True,json.loads(output.getvalue())['ordinaryUninstallVerified'])
        self.assertEqual(1,self.calls)
        self.assertEqual([self.binding['version']],self.admissions)

    def test_cli_unknown_postcheck_is_exit_four_without_result_or_exception_leak(self):
        args=['--target',str(self.target)]
        for key,value in self.binding.items():
            if key!='schemaVersion':
                option={'manifestSha256':'manifest-sha256','bundleSha256':'bundle-sha256',
                    'runId':'run-id'}.get(key,key)
                args += ['--'+option,value]
        def fail(): raise OSError('synthetic-sensitive-body-never-print')
        self.after=fail
        output,errors=io.StringIO(),io.StringIO()
        controller=SimpleNamespace(SurveyManager=lambda target:self.manager,OWNED_IMPORT_ORDER=())
        with mock.patch.object(self.m,'_bootstrap_controller',return_value=controller), \
                contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
            code=self.m.main(args)
        self.assertEqual(4,code)
        self.assertEqual('',output.getvalue())
        self.assertNotIn('synthetic-sensitive-body',errors.getvalue())
        self.assertEqual(1,self.calls)

    def test_cold_cli_rejects_preloaded_owned_module_before_admission_or_uninstall(self):
        args=['--target',str(self.target)]
        for key,value in self.binding.items():
            if key!='schemaVersion':
                option={'manifestSha256':'manifest-sha256','bundleSha256':'bundle-sha256',
                    'runId':'run-id'}.get(key,key)
                args+=['--'+option,value]
        controller=SimpleNamespace(SurveyManager=lambda target:self.manager,
            OWNED_IMPORT_ORDER=('owned_docker_exec',))
        with mock.patch.object(self.m,'_bootstrap_controller',return_value=controller), \
                contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(4,self.m.main(args))
        self.assertEqual([],self.admissions)
        self.assertEqual(0,self.calls)

    def test_settled_failed_install_uses_the_same_ordinary_cleanup_contract(self):
        self.state['status']='install_failed'
        self.store.record['phase']='settled-failure'
        result=self.m.prove_uninstall(self.manager,self.binding)
        self.assertIs(True,result['ordinaryUninstallVerified'])
        self.assertEqual(1,result['retainedVolumeCount'])

    def test_installed_bundle_drift_after_uninstall_is_not_a_success(self):
        def drift(): self.installed['bundleSha256']='f'*64
        self.after=drift
        with self.assertRaises(self.m.CleanupProofError): self.m.prove_uninstall(self.manager,self.binding)
        self.assertEqual(1,self.calls)

    def test_real_script_rejects_unchecked_pyc_and_only_executes_admitted_source(self):
        import test_surveyctl as fixtures
        production=Path(__file__).resolve().parents[1]
        cases=[(name,False) for name in ('owned_docker_exec','surveyctl','native_cleanup_proof')]
        for poisoned,drift in cases+[('owned_docker_exec',True)]:
            with self.subTest(poisoned=poisoned,drift=drift),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve(); runtime=root/'runtime';runtime.mkdir(mode=0o700)
                marker=root/'unchecked-cache-ran'; admitted=root/'verified-source-ran'
                owned=(production/'owned_docker_exec.py').read_bytes()
                owned+=('\nfrom pathlib import Path\nPath('+repr(str(admitted))+').touch()\n').encode()
                f=fixtures.SurveyctlTest();f.setUp();self.addCleanup(f.doCleanups)
                manifest,_=fixtures.write_test_release(f.root,version=self.binding['version'],
                    _fixture_owned_support=True,payload_overrides={'owned_docker_exec.py':owned})
                f.manager().install(manifest,'survey.example.com','operations','operations@example.com')
                controller=f.module
                for name in controller.OWNED_RUNTIME_FILES|controller.OWNED_HELPER_FILES:
                    path=runtime/name;path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(owned if name=='owned_docker_exec.py' else (production/name).read_bytes())
                (runtime/'release.schema.json').write_bytes((production/'release.schema.json').read_bytes())
                poison=root/'poison.py'
                poison.write_text('from pathlib import Path\nPath('+repr(str(marker))+').touch()\nraise RuntimeError("unchecked cache")\n')
                cache_root=str(root/'cache')
                with mock.patch.object(sys,'pycache_prefix',cache_root):
                    cache=importlib.util.cache_from_source(str(runtime/(poisoned+'.py')))
                Path(cache).parent.mkdir(parents=True,exist_ok=True)
                py_compile.compile(str(poison),cfile=cache,doraise=True,
                    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
                selected=controller.load_manifest(manifest)
                arch=next(iter(selected.raw['assets']['bundles']))
                # Prove this cached payload is consumable by a plain import;
                # Mac temporary-path aliases must not make the attack vacuous.
                plain=subprocess.run([sys.executable,'-B','-X','pycache_prefix='+cache_root,'-c',
                    'import sys;sys.path.insert(0,'+repr(str(runtime))+');__import__('+repr(poisoned)+')'],
                    capture_output=True,text=True,timeout=20)
                self.assertNotEqual(0,plain.returncode)
                self.assertTrue(marker.exists(),'poisoned cache control did not execute')
                marker.unlink()
                if drift:
                    (runtime/'owned_docker_exec.py').write_bytes(owned+b'\n# unadmitted source drift\n')
                args=[sys.executable,'-B','-X','pycache_prefix='+cache_root,str(runtime/'native_cleanup_proof.py'),'--target',str(f.target),
                    '--version',selected.version,'--commit',selected.raw['commit'],
                    '--manifest-sha256',selected.sha256,'--bundle-sha256',selected.bundle_for(arch)['sha256'],
                    '--run-id','1','--attempt','1','--ubuntu','24.04','--architecture',arch]
                run=subprocess.run(args,capture_output=True,text=True,timeout=20,
                    env=dict(os.environ,PYTHONPYCACHEPREFIX=cache_root))
                # Fixture has no owned runtime: refusal after admission is the
                # legitimate control, with no Docker mutation or fake success.
                self.assertEqual(4,run.returncode,run.stderr)
                self.assertFalse(marker.exists(),'unchecked pyc executed before source admission')
                self.assertEqual(not drift,admitted.exists(),'owned code must run only after full source admission')
                self.assertEqual('',run.stdout)


if __name__ == '__main__': unittest.main()
