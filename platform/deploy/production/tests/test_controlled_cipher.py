"""Runner handoff and existing HMAC envelope, with synthetic data only."""
import hashlib
import hmac
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_owned_memory import PortableFDBackend

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ControlledCipherHandoffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backup = load('controlled_cipher_backup', ROOT / 'backup.py')
        cls.memory = load('controlled_cipher_memory', ROOT / 'owned_memory.py')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.key = self.root / 'fixture-key'
        self.key.write_bytes(b'isolated-fixture-key-material-32-bytes')
        self.key.chmod(0o600)
        self.workspace = self.memory.AnonymousWorkspace(_backend=PortableFDBackend())
        self.addCleanup(self.workspace.close)
        self.calls = []

    def runner(self, *, fail=False, decrypt_partial=False, uncertain=False):
        owner = self
        class Runner:
            def run(self, command, **kwargs):
                owner.calls.append((command, kwargs))
                if fail:
                    raise subprocess.CalledProcessError(1, ['openssl'])
                source = command[command.index('-in') + 1]
                target = command[command.index('-out') + 1]
                if '-d' in command:
                    owner.assertIsInstance(source, owner.memory.ChildArgument)
                    owner.assertEqual(b'fixture-ciphertext', source.file.read_bytes())
                    owner.assertIsInstance(target, owner.memory.ChildArgument)
                    if uncertain:
                        owner.uncertain_export = target.file._workspace.export_files(
                            inputs=(source.file,), output=target.file)
                        leases = owner.uncertain_export.__enter__()
                        os.pwrite(leases[-1].fd, b'partial', 0)
                        raise RuntimeError('synthetic execution outcome is unknown')
                    with target.file.open('wb') as output:
                        output.write(b'fixture-plaintext')
                    if decrypt_partial:
                        raise subprocess.CalledProcessError(1, ['openssl'])
                else:
                    owner.assertIsInstance(source, owner.memory.ChildArgument)
                    owner.assertEqual(b'fixture-plaintext', source.file.read_bytes())
                    Path(target).write_bytes(b'fixture-ciphertext')
                return subprocess.CompletedProcess(['openssl'], 0, stdout=b'', stderr=b'')
        return Runner()

    def test_encrypt_hands_off_an_opaque_input_and_preserves_the_existing_envelope(self):
        plain = self.workspace / 'payload'
        with plain.open('xb') as output:
            output.write(b'fixture-plaintext')
        encrypted = self.root / 'encrypted.enc'
        cipher = self.backup.AuthenticatedOpenSSLCipher(runner=self.runner())
        cipher.encrypt(plain, encrypted, self.key)
        mac_key = hashlib.sha256(self.key.read_bytes() + b'\x00survey-backup-hmac').digest()
        payload = b'SURVEYBK1' + b'fixture-ciphertext'
        self.assertEqual(payload + hmac.new(mac_key, payload, hashlib.sha256).digest(), encrypted.read_bytes())
        self.assertEqual(0o600, encrypted.stat().st_mode & 0o777)
        self.assertFalse(encrypted.with_suffix('.enc.cipher').exists())
        self.assertTrue(self.calls[0][1]['check'])
        self.assertTrue(self.calls[0][1]['capture_output'])

    def test_decrypt_verifies_hmac_before_runner_and_keeps_plaintext_anonymous(self):
        data = b'SURVEYBK1' + b'fixture-ciphertext'
        mac_key = hashlib.sha256(self.key.read_bytes() + b'\x00survey-backup-hmac').digest()
        source = self.root / 'encrypted.enc'
        source.write_bytes(data + hmac.new(mac_key, data, hashlib.sha256).digest())
        target = self.workspace / 'payload'
        cipher = self.backup.AuthenticatedOpenSSLCipher(runner=self.runner())
        cipher.decrypt(source, target, self.key)
        self.assertEqual(b'fixture-plaintext', target.read_bytes())
        self.assertEqual({'encrypted.enc', 'fixture-key'}, {path.name for path in self.root.iterdir()})
        damaged = bytearray(source.read_bytes())
        damaged[-1] ^= 1
        source.write_bytes(damaged)
        self.calls.clear()
        with self.assertRaises(self.backup.BackupError):
            cipher.decrypt(source, self.workspace / 'rejected', self.key)
        self.assertEqual([], self.calls)

    def test_runner_failure_keeps_failure_and_removes_its_partial_ciphertext(self):
        plain = self.workspace / 'payload'
        with plain.open('xb') as output:
            output.write(b'fixture-plaintext')
        cipher = self.backup.AuthenticatedOpenSSLCipher(runner=self.runner(fail=True))
        with self.assertRaises(self.backup.BackupError):
            cipher.encrypt(plain, self.root / 'failed.enc', self.key)
        self.assertEqual({'fixture-key'}, {path.name for path in self.root.iterdir()})

    def fixture_encrypted(self):
        data = b'SURVEYBK1' + b'fixture-ciphertext'
        mac_key = hashlib.sha256(self.key.read_bytes() + b'\x00survey-backup-hmac').digest()
        source = self.root / 'encrypted.enc'
        source.write_bytes(data + hmac.new(mac_key, data, hashlib.sha256).digest())
        return source

    def test_confirmed_failed_decrypt_removes_its_partial_anonymous_output(self):
        source, target = self.fixture_encrypted(), self.workspace / 'payload'
        cipher = self.backup.AuthenticatedOpenSSLCipher(runner=self.runner(decrypt_partial=True))
        with self.assertRaises(self.backup.BackupError):
            cipher.decrypt(source, target, self.key)
        with self.assertRaises(FileNotFoundError):
            target.stat()
        with self.assertRaises(FileNotFoundError):
            target.with_suffix('.cipher').stat()

    def test_unknown_decrypt_retains_the_export_and_cannot_claim_cleanup(self):
        source, target = self.fixture_encrypted(), self.workspace / 'payload'
        cipher = self.backup.AuthenticatedOpenSSLCipher(runner=self.runner(uncertain=True))
        try:
            with self.assertRaises(self.memory.WorkspaceError):
                cipher.decrypt(source, target, self.key)
            with self.assertRaises(self.memory.WorkspaceError):
                self.workspace.close()
            with self.assertRaises(self.memory.WorkspaceError):
                target.read_bytes()
        finally:
            # Only now simulate the separate executor confirming its child ended.
            self.uncertain_export.__exit__(None, None, None)
        target.unlink()
        target.with_suffix('.cipher').unlink()


if __name__ == '__main__':
    unittest.main()
