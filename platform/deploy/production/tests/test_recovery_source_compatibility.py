import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import test_owned_backup_verify as source_fixtures
from test_surveyctl import write_test_release


class RecoverySourceCompatibilityTest(source_fixtures.OwnedBackupVerifyTest):
    def target_manifest(self, **updates):
        path, _ = write_test_release(self.fixture.root, version='0.3.0',
            **{'minimumSourceVersion': '0.1.0', **updates})
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def verify(self, **updates):
        path, digest = self.target_manifest(**updates)
        return self.manager.verify_target_compatibility(self.output, path, digest)

    def test_verified_payloads_and_target_metadata_do_not_authorize_restore(self):
        report = self.verify()
        self.assertIs(True, report['sourcePayloadsVerified'])
        self.assertIs(True, report['targetMetadataCompatible'])
        for field in ('targetSignatureVerified', 'targetConfigurationVerified', 'restoreAuthorized'):
            self.assertIs(False, report[field])
        self.assertEqual('0.2.0', report['sourceVersion'])
        self.assertEqual('0.3.0', report['targetVersion'])
        with self.assertRaises(self.module.RestoreIntegrityError): self.manager.restore(self.output)

    def test_wrong_manifest_hash_rejects_before_payload_decryption(self):
        path, _ = self.target_manifest()
        with mock.patch.object(self.cipher, 'decrypt', wraps=self.cipher.decrypt) as decrypt:
            with self.assertRaises(self.module.RestoreIntegrityError):
                self.manager.verify_target_compatibility(self.output, path, '0' * 64)
            decrypt.assert_not_called()

    def test_backup_below_target_minimum_is_rejected(self):
        path, digest = self.target_manifest()
        value = json.loads(path.read_text()); value['minimumSourceVersion'] = '0.2.1'
        path.write_text(json.dumps(value)); digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(self.module.RestoreIntegrityError, 'version'):
            self.manager.verify_target_compatibility(self.output, path, digest)

    def test_incompatible_target_schema_is_rejected(self):
        with self.assertRaisesRegex(self.module.RestoreIntegrityError, 'schema'):
            self.verify(database={'schema': '921', 'backupSchema': 2,
                'compatibleSourceSchemas': ['921'], 'rollback': 'compatible'})

    def test_rc_minimum_uses_existing_strict_version_order(self):
        self.assertIs(True, self.verify(minimumSourceVersion='0.2.0-rc.1')['targetMetadataCompatible'])

    def test_source_newer_than_target_is_rejected(self):
        path, _ = self.target_manifest()
        value = json.loads(path.read_text()); value['version'] = '0.1.9'
        for arch, bundle in value['assets']['bundles'].items():
            bundle['name'] = 'survey-0.1.9-linux-' + arch + '.tar.gz'
        path.write_text(json.dumps(value)); digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(self.module.RestoreIntegrityError, 'version'):
            self.manager.verify_target_compatibility(self.output, path, digest)

    def test_required_hash_is_not_optional_or_a_bool(self):
        path, _ = self.target_manifest()
        for digest in ('', None, True, 'A' * 64):
            with self.subTest(digest=digest), mock.patch.object(self.cipher, 'decrypt') as decrypt:
                with self.assertRaises(self.module.RestoreIntegrityError):
                    self.manager.verify_target_compatibility(self.output, path, digest)
                decrypt.assert_not_called()

    def test_bad_hmac_never_reaches_decryption(self):
        (self.output / 'CONTROL-HMAC').write_text('0' * 64 + '\n')
        with mock.patch.object(self.cipher, 'decrypt', wraps=self.cipher.decrypt) as decrypt:
            with self.assertRaisesRegex(self.module.RestoreIntegrityError, 'authentication'):
                self.verify()
            decrypt.assert_not_called()

    def test_source_personnel_floor_cannot_be_downgraded_by_target(self):
        source = self.fixture.target / 'releases/0.2.0/release.json'
        value = json.loads(source.read_text())
        value['securityCapabilities'] = {'platform-admin-session-v1': value['images']['PLATFORM_IMAGE']}
        source.write_text(json.dumps(value))
        (source.parent / '.env').write_text('PLATFORM_ADMIN_ENFORCE_PERSONNEL=true\n')
        control = self.fixture.target / '.surveyctl'; control.mkdir(exist_ok=True)
        (control / 'auth-security-floor').write_text('platform-admin-session-v1\n')
        metadata_path = self.output / 'manifest.json'
        metadata = json.loads(metadata_path.read_text())
        metadata['releaseManifestSha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        metadata_path.write_bytes(self.fixture.backup.canonical_manifest(metadata))
        self.fixture.backup.rewrite_authenticated_control(self.output, self.fixture.key_file)
        with self.assertRaisesRegex(self.module.RestoreIntegrityError, 'personnel'):
            self.verify(securityCapabilities={})

    def test_target_replaced_during_verification_is_not_accepted(self):
        path, digest = self.target_manifest()
        original = self.manager.verify_only
        def mutate(backup):
            result = original(backup)
            path.write_text('{}')
            return result
        with mock.patch.object(self.manager, 'verify_only', side_effect=mutate):
            with self.assertRaises(self.module.RestoreIntegrityError):
                self.manager.verify_target_compatibility(self.output, path, digest)


if __name__ == '__main__': unittest.main()
