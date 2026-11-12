"""Actual local archive/code admission; synthetic unsigned release fixtures."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest
from unittest import mock
import test_surveyctl as fixtures

PRODUCTION=Path(__file__).resolve().parents[1]
MODULES={'recovery_reservation.py','recovery_handoff.py','controlled_database_import.py','recovery_target_binding.py'}
HELPER='release/verify_release.sh'

class RecoveryRuntimePackagingTest(unittest.TestCase):
    def setUp(self):
        f=fixtures.SurveyctlTest();f.setUp();self.addCleanup(f.doCleanups);self.f=f
    def test_actual_bundle_collection_binds_all_recovery_sources_and_helper(self):
        release=PRODUCTION/'release';sys.path.insert(0,str(release))
        try:
            spec=importlib.util.spec_from_file_location('recovery_package_builder',release/'build_release.py')
            builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
            runtime=builder.collect_runtime(PRODUCTION,PRODUCTION.parents[2])
        finally:sys.path.remove(str(release))
        self.assertTrue(MODULES|{HELPER}<=set(runtime))
        for architecture in ('amd64','arm64'):
            bundle=self.f.root/('runtime-'+architecture+'.tar.gz')
            payload=dict(runtime,**{'bundle-metadata.json':builder.bundle_metadata('0.4.0-rc.1',architecture,runtime)})
            builder.build_tarball(bundle,payload,0)
            with tarfile.open(bundle,'r:gz') as archive:
                metadata=json.loads(archive.extractfile('bundle-metadata.json').read())
                self.assertEqual(architecture,metadata['architecture'])
                for name in MODULES|{HELPER}:
                    self.assertEqual((PRODUCTION/name).read_bytes(),archive.extractfile(name).read())
                    self.assertEqual(hashlib.sha256(runtime[name]).hexdigest(),metadata['files'][name])
    def test_candidate_with_different_recovery_code_is_rejected_before_import(self):
        for name in sorted(MODULES):
            with self.subTest(name=name):
                root=self.f.root/name;root.mkdir()
                manifest,_=fixtures.write_test_release(root,_fixture_owned_support=True,payload_overrides={name:b'# checksum-valid other code\n'})
                f=self.f
                manager=f.module.SurveyManager(root/'installed',runner=fixtures.FakeRunner(),host_probe=fixtures.FakeHostProbe(),
                    source_dir=PRODUCTION,_runtime_factory=fixtures.fixture_runtime)
                # Use the normal fixture helper: no actual Docker process.
                with mock.patch.object(manager,'_verify_owned_support'):
                    manager.install(manifest,'survey.example.com','operations','operations@example.com')
                with self.assertRaises(f.module.IntegrityError):manager._verify_owned_support('0.2.0')
    def test_verifier_helper_is_checksum_bound_before_any_signature_tool(self):
        manifest,_=fixtures.write_test_release(self.f.root,_fixture_owned_support=True,payload_overrides={HELPER:b'#!/bin/sh\nexit 0\n'})
        self.f.install(manifest)
        with self.assertRaises(self.f.module.IntegrityError):self.f.manager()._verify_owned_support('0.2.0')
    def test_capture_loader_ignores_foreign_recovery_modules_and_does_not_enable_restore(self):
        manifest,_=fixtures.write_test_release(self.f.root,_fixture_owned_support=True)
        self.f.install(manifest)
        foreign=self.f.root/'foreign';foreign.mkdir();marker=self.f.root/'foreign-ran'
        for name in MODULES:(foreign/name).write_text('from pathlib import Path\nPath('+repr(str(marker))+').touch()\n')
        with mock.patch.dict(sys.modules),mock.patch.object(sys,'path',[str(foreign),*sys.path]):
            for name in MODULES:sys.modules.pop(Path(name).stem,None)
            manager=self.f.manager();manager._load_owned_support('0.2.0')
            for name in MODULES:self.assertEqual(PRODUCTION/name,Path(sys.modules[Path(name).stem].__file__))
            with mock.patch('controlled_database_import.ControlledDatabaseCopy.execute') as execute:
                with self.assertRaises(self.f.module.SurveyctlError):manager.restore('unused')
                execute.assert_not_called()
        self.assertFalse(marker.exists())

if __name__=='__main__':unittest.main()
