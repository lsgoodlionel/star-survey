"""Internal verify-only path; no restore or standalone CLI authorization.

Candidate code/source release and operation binding remain controller duties.
The caller retains the cipher/workspace, especially after an unknown child.
"""
from pathlib import Path

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
