"""Internal verify-only path; no restore or standalone CLI authorization.

Candidate code/source release and operation binding remain controller duties.
The caller retains the cipher/workspace, especially after an unknown child.
"""
from pathlib import Path
import re

from backup import safe_absolute
from owned_cipher_adapter import ControlledCipher
from restore import RestoreManager, RestoreIntegrityError


class ControlledVerifier(RestoreManager):
    def __init__(self, target, cipher):
        if not isinstance(cipher, ControlledCipher):
            raise RestoreIntegrityError('controlled verifier requires its bound FD cipher')
        super().__init__(target, cipher=cipher)
        self.workspace = cipher.workspace

    def _key_file(self):
        return self.cipher.key

    def verify_backup(self, backup, temporary):
        if not isinstance(backup, Path) or backup != self.cipher.root or temporary is not self.workspace:
            raise RestoreIntegrityError('controlled verification operation binding differs')
        self.cipher.directory.check()
        return super().verify_backup(backup, temporary)

    def verify_only(self, backup):
        backup = safe_absolute(backup, 'controlled backup path')
        if backup.parent != self.target / 'backups' or backup != self.cipher.root:
            raise RestoreIntegrityError('controlled backup path differs')
        metadata, _ = self.verify_backup(backup, self.workspace)
        return metadata

    def restore(self, backup):
        raise RestoreIntegrityError('controlled verification does not authorize restore')

    def verify_target_compatibility(self, backup, target_manifest, target_manifest_sha256):
        """Authenticate source payloads, then assess a hash-bound target's metadata.

        Internal and unconnected to recovery/CLI. The enclosing controller must
        bind source ownership, verified code, operation, target signatures and
        effective configuration. This report is neither a durable capability
        nor permission to import data or start applications. The workspace and
        cipher lifetime, including unknown child outcomes, stay with the caller.
        """
        from surveyctl import load_manifest, SurveyctlError
        if (not isinstance(target_manifest_sha256, str)
                or re.fullmatch('[0-9a-f]{64}', target_manifest_sha256) is None):
            raise RestoreIntegrityError('recovery target manifest SHA256 is required')
        def load_target():
            try:
                return load_manifest(target_manifest, target_manifest_sha256)
            except SurveyctlError as exc:
                raise RestoreIntegrityError('recovery target manifest binding is invalid') from exc

        target = load_target()  # Reject unknown/mismatched target before decrypting.
        floor = self.target / '.surveyctl/auth-security-floor'
        personnel_required = floor.exists() or floor.is_symlink()
        metadata = self.verify_only(backup)  # Existing HMAC, hashes, all anonymous payload validators.
        if personnel_required != (floor.exists() or floor.is_symlink()):
            raise RestoreIntegrityError('source personnel security floor changed')
        self._load_current()  # Recheck the effective source floor; never infer it from an old backup.
        compatible = {*target.compatible_source_schemas, target.database_schema}
        if metadata['databaseSchema'] not in compatible:
            raise RestoreIntegrityError('backup schema is incompatible with the recovery target')
        if not (self._version_key(target.raw['minimumSourceVersion'])
                <= self._version_key(metadata['version']) <= self._version_key(target.version)):
            raise RestoreIntegrityError('backup version is incompatible with the recovery target')
        if personnel_required and target.raw.get('securityCapabilities', {}).get('platform-admin-session-v1') != target.raw['images']['PLATFORM_IMAGE']:
            raise RestoreIntegrityError('recovery target would downgrade the personnel security floor')
        load_target()  # Expensive payload checks must not hide a changed target file.
        return {'schemaVersion': 1, 'sourcePayloadsVerified': True, 'targetMetadataCompatible': True,
                'sourceVersion': metadata['version'], 'sourceDatabaseSchema': metadata['databaseSchema'],
                'sourceManifestSha256': metadata['releaseManifestSha256'],
                'targetVersion': target.version, 'targetManifestSha256': target_manifest_sha256,
                'targetSignatureVerified': False, 'targetConfigurationVerified': False,
                'restoreAuthorized': False}
