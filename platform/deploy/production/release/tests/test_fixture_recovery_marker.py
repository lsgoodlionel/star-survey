import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'fixture_recovery_marker.py'
spec = importlib.util.spec_from_file_location('fixture_recovery_marker', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RecoveryMarkerTest(unittest.TestCase):
    def test_marker_is_private_durable_and_requires_matching_identity_to_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            marker = module.RecoveryMarker(directory, 'a' * 64)
            marker.create()
            path = directory / 'fixture-recovery.pending'
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual('a' * 64 + '\n', path.read_text())
            with self.assertRaises(ValueError):
                module.RecoveryMarker(directory, 'b' * 64).clear()
            self.assertTrue(path.exists())
            marker.clear()
            self.assertFalse(path.exists())

    def test_existing_or_unsafe_marker_is_never_overwritten_or_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / 'fixture-recovery.pending'
            path.symlink_to(directory / 'elsewhere')
            marker = module.RecoveryMarker(directory, 'a' * 64)
            with self.assertRaises(ValueError): marker.create()
            with self.assertRaises(ValueError): marker.clear()
            self.assertTrue(path.is_symlink())
            path.unlink()
            os.chmod(directory, 0o755)
            with self.assertRaises(ValueError): marker.create()

    def test_fsync_failure_is_never_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = module.RecoveryMarker(Path(tmp), 'a' * 64)
            with mock.patch.object(module.os, 'fsync', side_effect=OSError('failure')):
                with self.assertRaises(ValueError): marker.create()
            self.assertTrue((Path(tmp) / 'fixture-recovery.pending').exists())

    def test_fifo_does_not_block_or_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixture-recovery.pending'
            os.mkfifo(path)
            with self.assertRaises(ValueError): module.RecoveryMarker(Path(tmp), 'a' * 64).clear()
            self.assertTrue(path.exists())
