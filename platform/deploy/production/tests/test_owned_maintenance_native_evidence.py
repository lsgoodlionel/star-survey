"""Synthetic native component success cannot qualify installation or recovery."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest
import tempfile

SPEC = importlib.util.spec_from_file_location('maintenance_native',
    Path(__file__).parent / 'integration/owned_maintenance_native.py')


class MaintenanceNativeEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(self.module)

    def test_component_manifest_capability_matches_its_actual_platform_digest(self):
        images = json.loads((Path(__file__).parent / 'fixtures/release.json').read_text())['images']
        images['PLATFORM_IMAGE'] = 'python@sha256:' + 'b' * 64
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, _ = self.module.write_component_release(Path(directory), images, {}, 'amd64')
            controller = self.module.load_module()
            manifest = controller.load_manifest(manifest_path)
            controller.SurveyManager._require_personnel_capability(manifest)
            self.assertEqual(images['PLATFORM_IMAGE'], manifest.raw['securityCapabilities']['platform-admin-session-v1'])
            manifest.raw['securityCapabilities']['platform-admin-session-v1'] = 'python@sha256:' + 'c' * 64
            with self.assertRaises(controller.InputError):
                controller.SurveyManager._require_personnel_capability(manifest)

    def test_only_complete_scoped_evidence_passes(self):
        report = {'schemaVersion': 1, 'scope': 'recorded-maintenance-real-database-components',
            'sourceBefore': {'file': 'a' * 64}, 'sourceAfter': {'file': 'a' * 64},
            'cases': [{'scenario': kind, 'status': 'passed', 'fixtureCleanup': 'verified',
                       'syntheticManifestOnly': True, 'businessServicesCovered': False,
                       'hostAndTlsAreSynthetic': True, 'upgradeBusinessDataVerified': False,
                       'defaultInstallCovered': False, 'restorationEligible': False, 'releaseEligible': False,
                       **{key: True for key in checks}}
                      for kind, checks in self.module.REQUIRED.items()]}
        self.assertTrue(self.module.passed(report))
        for key in ('schemaVersion', 'sourceAfter', 'cases'):
            bad = deepcopy(report); bad[key] = True
            self.assertFalse(self.module.passed(bad))
        for index, checks in enumerate(self.module.REQUIRED.values()):
            for key in checks:
                for value in (False, 1, None):
                    bad = deepcopy(report); bad['cases'][index][key] = value
                    self.assertFalse(self.module.passed(bad))
            for key, value in (('restorationEligible', True), ('releaseEligible', None),
                               ('fixtureCleanup', 'unknown'), ('businessServicesCovered', True),
                               ('hostAndTlsAreSynthetic', False), ('upgradeBusinessDataVerified', True)):
                bad = deepcopy(report); bad['cases'][index][key] = value
                self.assertFalse(self.module.passed(bad))

    def test_empty_or_unbounded_doctor_error_report_cannot_qualify(self):
        report = {'schemaVersion': 1, 'status': 'blocked', 'checks': [{
            'id': 'doctor-runtime', 'status': 'blocked',
            'summary': 'controlled diagnostic operation failed; preserve its receipts and marker'}]}
        self.assertTrue(self.module.doctor_refusal_report(report))
        for updates in ({'checks': []}, {'schemaVersion': True}, {'raw': 'unbounded'}, {'status': 'ok'}):
            bad = deepcopy(report); bad.update(updates)
            self.assertFalse(self.module.doctor_refusal_report(bad))

    def test_missing_image_labels_are_supported_and_env_is_never_projected(self):
        class API:
            def _json(self, method, path):
                return {'Id': 'sha256:' + 'a' * 64, 'Architecture': 'amd64', 'Os': 'linux',
                        'Config': {'Env': ['PASSWORD=synthetic-never-export']}}
        value = self.module.image_metadata(API(), 'python@sha256:' + 'b' * 64)
        self.assertEqual({'id': 'sha256:' + 'a' * 64, 'architecture': 'amd64', 'os': 'linux', 'version': None}, value)
        self.assertNotIn('synthetic-never-export', str(value))


if __name__ == '__main__': unittest.main()
