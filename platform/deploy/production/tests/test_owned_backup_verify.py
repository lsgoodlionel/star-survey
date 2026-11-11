"""Synthetic payloads through real metadata/archive validation, no Docker."""
import importlib
import os
from pathlib import Path
import sys
import unittest

import test_backup_restore as fixtures
from test_owned_memory import PortableFDBackend

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class OwnedBackupVerifyTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('owned_backup_verify')
        import owned_memory
        from owned_cipher_adapter import ControlledCipher
        fixture = fixtures.BackupRestoreTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.output = fixture.create_backup()
        self.workspace = owned_memory.AnonymousWorkspace(_backend=PortableFDBackend())
        self.addCleanup(self.workspace.close)
        class CopyExecutor:
            def execute(self, profile, source, target, key):
                os.pwrite(target.fd, os.pread(source.fd, 1024 * 1024, 0), 0)
        self.cipher = ControlledCipher(self.workspace, CopyExecutor(), self.output, fixture.key_file.read_bytes().strip())
        self.addCleanup(self.cipher.close)
        for path in sorted(self.output.glob('*.enc')):
            content = path.read_bytes()[4:][::-1]  # Explicit fixture-only ENC1 cipher.
            path.unlink()
            plain = self.workspace / ('input-' + path.stem)
            with plain.open('xb') as handle:
                handle.write(content)
            self.cipher.encrypt(plain, path, self.cipher.key)
            plain.unlink()
        fixture.backup.rewrite_authenticated_control(self.output, fixture.key_file)
        self.manager = self.module.ControlledVerifier(fixture.target, self.cipher)

    def test_verify_only_checks_all_payloads_anonymously_and_restore_stays_disabled(self):
        before = {str(path.relative_to(self.fixture.target)) for path in self.fixture.target.rglob('*')}
        metadata = self.manager.verify_only(self.output)
        self.assertEqual('0.2.0', metadata['version'])
        self.assertEqual(before, {str(path.relative_to(self.fixture.target)) for path in self.fixture.target.rglob('*')})
        with self.assertRaises(self.module.RestoreIntegrityError):
            self.manager.restore(self.output)

    def test_wrong_backup_target_and_authenticated_path_traversal_archive_are_rejected(self):
        with self.assertRaises(self.module.RestoreIntegrityError):
            self.manager.verify_only(self.output.with_name('other'))
        import io, tarfile
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            item = tarfile.TarInfo('../escape'); item.size = 1
            archive.addfile(item, io.BytesIO(b'x'))
        plain = self.workspace / 'malicious'
        with plain.open('xb') as handle:
            handle.write(stream.getvalue())
        destination = self.output / 'engine-upload.tar.enc'
        destination.unlink()
        self.cipher.encrypt(plain, destination, self.cipher.key)
        self.fixture.backup.rewrite_authenticated_control(self.output, self.fixture.key_file)
        with self.assertRaises(self.module.RestoreIntegrityError):
            self.manager.verify_only(self.output)
        self.assertFalse((self.fixture.target / 'escape').exists())


if __name__ == '__main__':
    unittest.main()
