"""Adapter contract tests; fake executor is not Linux process evidence."""
import hashlib
import hmac
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from test_owned_memory import PortableFDBackend

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class OwnedCipherAdapterTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('owned_cipher_adapter')
        self.memory = importlib.import_module('owned_memory')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.memory.AnonymousWorkspace(_backend=PortableFDBackend())
        self.calls, self.fail = [], None
        owner = self
        class Executor:
            def execute(self, profile, source, target, key):
                owner.calls.append(profile)
                owner.assertEqual(b'k' * 64, os.pread(key.fd, 64, 0))
                if owner.fail:
                    raise owner.fail
                os.pwrite(target.fd, b'cipher-body' if profile == 'encrypt' else b'synthetic-answer', 0)
        self.cipher = self.module.ControlledCipher(self.workspace, Executor(), self.root, b'k' * 64)

    def tearDown(self):
        if not self.cipher.uncertain:
            self.cipher.close()
            self.workspace.close()

    def plain(self, name='plain'):
        result = self.workspace / name
        with result.open('xb') as output:
            output.write(b'synthetic-answer')
        return result

    def test_existing_hmac_envelope_round_trip_and_no_plaintext_disk_file(self):
        path = self.root / 'postgres.dump.enc'
        self.cipher.encrypt(self.plain(), path, self.cipher.key)
        payload = b'SURVEYBK1cipher-body'
        mac = hmac.new(hashlib.sha256(b'k' * 64 + b'\x00survey-backup-hmac').digest(), payload, hashlib.sha256).digest()
        self.assertEqual(payload + mac, path.read_bytes())
        target = self.workspace / 'restored'
        self.cipher.decrypt(path, target, self.cipher.key)
        self.assertEqual(b'synthetic-answer', target.read_bytes())
        self.assertEqual({'postgres.dump.enc'}, {item.name for item in self.root.iterdir()})
        damaged = bytearray(path.read_bytes()); damaged[-1] ^= 1
        path.write_bytes(damaged)
        before = list(self.calls)
        with self.assertRaises(self.module.BackupError):
            self.cipher.decrypt(path, self.workspace / 'rejected', self.cipher.key)
        self.assertEqual(before, self.calls)

    def test_unknown_child_retains_workspace_export_and_blocks_cleanup_and_reuse(self):
        from owned_crypto import UncertainCryptoChild
        self.fail = UncertainCryptoChild('synthetic unknown')
        with self.assertRaises(UncertainCryptoChild):
            self.cipher.encrypt(self.plain(), self.root / 'postgres.dump.enc', self.cipher.key)
        self.assertTrue(self.cipher.uncertain)
        with self.assertRaises(self.memory.WorkspaceError):
            self.workspace.close()
        with self.assertRaises(UncertainCryptoChild):
            self.cipher.encrypt(self.workspace / 'plain', self.root / 'next.enc', self.cipher.key)
        # Only a fake child exists in this test: explicitly resolve it here.
        self.cipher._runner.held.__exit__(None, None, None)
        self.cipher._runner.held = None
        self.workspace.close()

    def test_named_plaintext_foreign_key_and_outside_root_are_refused_before_executor(self):
        plain = self.plain()
        for source, target, key in ((self.root / 'named', self.root / 'postgres.dump.enc', self.cipher.key),
                (plain, self.root.parent / 'outside.enc', self.cipher.key),
                (plain, self.root / 'postgres.dump.enc', object())):
            with self.assertRaises(self.module.BackupError):
                self.cipher.encrypt(source, target, key)
        self.assertEqual([], self.calls)

    def test_captured_key_cannot_be_rewritten_before_first_child(self):
        with self.assertRaises(self.memory.WorkspaceError):
            self.cipher.key.open('wb')

    def test_unexpected_executor_exception_cannot_release_child_descriptors(self):
        from owned_crypto import UncertainCryptoChild
        self.fail = RuntimeError('synthetic interrupted boundary')
        with self.assertRaises(UncertainCryptoChild):
            self.cipher.encrypt(self.plain(), self.root / 'postgres.dump.enc', self.cipher.key)
        self.assertTrue(self.cipher.uncertain)
        with self.assertRaises(self.memory.WorkspaceError):
            self.workspace.close()
        self.cipher._runner.held.__exit__(None, None, None)
        self.cipher._runner.held = None
        self.workspace.close()

    def test_writable_ancestor_is_rejected_and_mid_child_root_replacement_writes_nowhere(self):
        ancestor = self.root / 'unsafe'
        ancestor.mkdir(mode=0o777); ancestor.chmod(0o777)
        child = ancestor / 'private'; child.mkdir(mode=0o700)
        with self.assertRaises(self.module.BackupError):
            self.module.ControlledCipher(self.workspace, self.cipher._runner.executor, child, b'q' * 64)
        execute = self.cipher._runner.executor.execute
        moved = self.root.with_name(self.root.name + '-moved')
        self.addCleanup(lambda: moved.rmdir() if moved.exists() and not list(moved.iterdir()) else None)
        def replace(*args):
            result = execute(*args)
            self.root.rename(moved)
            self.root.mkdir(mode=0o700)
            return result
        self.cipher._runner.executor.execute = replace
        try:
            with self.assertRaises(self.module.BackupError):
                self.cipher.encrypt(self.plain(), self.root / 'postgres.dump.enc', self.cipher.key)
            self.assertEqual([], list(self.root.iterdir()))
            self.assertFalse((moved / 'postgres.dump.enc').exists())
        finally:
            self.root.rmdir()
            moved.rename(self.root)

    def test_fifo_input_is_nonblocking_and_rejected_before_child(self):
        path = self.root / 'postgres.dump.enc'
        os.mkfifo(path, 0o600)
        original = os.open
        def checked(name, flags, *args, **kwargs):
            if name == path.name:
                self.assertTrue(flags & os.O_NONBLOCK, 'must not block before checking file type')
            return original(name, flags, *args, **kwargs)
        with mock.patch.object(self.module.os, 'open', side_effect=checked):
            with self.assertRaises(self.module.BackupError):
                self.cipher.decrypt(path, self.workspace / 'rejected', self.cipher.key)
        self.assertEqual([], self.calls)


if __name__ == '__main__':
    unittest.main()
