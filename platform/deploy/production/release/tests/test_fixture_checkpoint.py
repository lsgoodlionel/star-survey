import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_checkpoint import bound_checkpoint_identity, require_completed_checkpoint, run_checkpointed


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.work, self.evidence = self.base / 'work', self.base / 'evidence'
        self.work.mkdir(mode=0o700); self.evidence.mkdir(mode=0o700)
        self.marker = self.evidence / 'fixture-recovery.pending'
        self.identity = bound_checkpoint_identity(self.work, '12', '1', 'b' * 64)

    def run_guard(self, action, verify):
        return run_checkpointed(self.evidence, self.work, self.identity, action, verify, run_id='12', attempt='1', manifest_sha='b' * 64)

    def test_identity_binds_run_candidate_and_work_directory(self):
        other = self.base / 'other'; other.mkdir(mode=0o700)
        identity = bound_checkpoint_identity(self.work, '12', '1', 'b' * 64)
        self.assertNotEqual(identity, bound_checkpoint_identity(other, '12', '1', 'b' * 64))
        self.assertNotEqual(identity, bound_checkpoint_identity(self.work, '13', '1', 'b' * 64))
        self.assertNotEqual(identity, bound_checkpoint_identity(self.work, '12', '2', 'b' * 64))
        self.assertNotEqual(identity, bound_checkpoint_identity(self.work, '12', '1', 'c' * 64))
        with self.assertRaises(ValueError): bound_checkpoint_identity(self.work, '0', '1', 'b' * 64)
        run_checkpointed(self.evidence, self.work, identity, lambda: None, lambda: True, run_id='12', attempt='1', manifest_sha='b' * 64)
        with self.assertRaises(ValueError):
            require_completed_checkpoint(self.evidence, other, identity, run_id='12', attempt='1', manifest_sha='b' * 64)
        cli = str(Path(__file__).resolve().parents[1] / 'fixture_checkpoint.py')
        for work, run, attempt, manifest, expected in (
            (self.work, '12', '1', 'b' * 64, 0), (other, '12', '1', 'b' * 64, 1),
            (self.work, '13', '1', 'b' * 64, 1), (self.work, '12', '2', 'b' * 64, 1),
            (self.work, '12', '1', 'c' * 64, 1),
        ):
            result = subprocess.run([sys.executable, cli, 'verify', str(self.evidence), str(work),
                                     identity, run, attempt, manifest], capture_output=True, timeout=5)
            self.assertEqual(result.returncode == 0, expected == 0)

    def test_verified_completion_clears_marker_only_after_verify(self):
        def verify():
            self.assertTrue(self.marker.is_file())
            return True
        self.run_guard(lambda: self.assertTrue(self.marker.is_file()), verify)
        self.assertFalse(self.marker.exists())
        require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)

    def test_receipt_required_and_pending_overrides_receipt(self):
        with self.assertRaises(OSError):
            require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)
        self.run_guard(lambda: None, lambda: True)
        self.marker.write_text(self.identity + '\n')
        with self.assertRaises(RuntimeError):
            require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)
        self.marker.unlink()
        (self.evidence / 'fixture-recovery.verified').write_text('b' * 64 + '\n')
        with self.assertRaises(ValueError):
            require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)

    def test_failed_marker_clear_keeps_pending_even_with_receipt(self):
        with mock.patch('fixture_checkpoint.RecoveryMarker.clear', side_effect=ValueError('clear failed')):
            with self.assertRaises(ValueError):
                self.run_guard(lambda: None, lambda: True)
        self.assertTrue(self.marker.exists())
        self.assertFalse((self.evidence / 'fixture-recovery.verified').exists())
        with self.assertRaises(RuntimeError):
            require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)

    def test_unlink_then_fsync_failure_does_not_verify(self):
        def fail_after_unlink(_marker):
            self.marker.unlink()
            raise ValueError('directory fsync failed')
        with mock.patch('fixture_checkpoint.RecoveryMarker.clear', autospec=True, side_effect=fail_after_unlink):
            with self.assertRaises(ValueError): self.run_guard(lambda: None, lambda: True)
        self.assertTrue(self.marker.exists())
        self.assertFalse((self.evidence / 'fixture-recovery.verified').exists())
        with self.assertRaises(RuntimeError):
            require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)

    def test_fifo_or_symlink_receipt_is_rejected_without_blocking(self):
        receipt = self.evidence / 'fixture-recovery.verified'
        os.mkfifo(receipt, 0o600)
        with self.assertRaises(ValueError):
            require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)
        receipt.unlink()
        receipt.symlink_to(self.work / 'not-a-receipt')
        with self.assertRaises(OSError):
            require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)

    def test_action_and_verification_failure_retain_marker(self):
        for failure in ('action', 'false', 'verify'):
            with self.subTest(failure=failure):
                def fail(): raise RuntimeError('failed')
                with self.assertRaises(RuntimeError):
                    self.run_guard(fail if failure == 'action' else lambda: None,
                                   fail if failure == 'verify' else lambda: False)
                self.assertTrue(self.marker.is_file())
                self.marker.unlink()

    def test_nested_or_unsafe_evidence_rejected_before_action(self):
        nested = self.work / 'nested'; nested.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            run_checkpointed(nested, self.work, self.identity, lambda: self.fail('action ran'), lambda: True, run_id='12', attempt='1', manifest_sha='b' * 64)
        self.evidence.chmod(0o755)
        with self.assertRaises(ValueError): self.run_guard(lambda: self.fail('action ran'), lambda: True)

    def test_killed_supervisor_leaves_pending_marker_on_surviving_filesystem(self):
        module_dir = str(Path(__file__).resolve().parents[1])
        code = '''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from fixture_checkpoint import run_checkpointed
run_checkpointed(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],lambda: time.sleep(60),lambda: True,run_id='12',attempt='1',manifest_sha='b'*64)
'''
        proc = subprocess.Popen([sys.executable, '-c', code, module_dir, str(self.evidence), str(self.work), self.identity])
        try:
            import time
            deadline = time.monotonic() + 5
            while not self.marker.exists() and time.monotonic() < deadline: time.sleep(.02)
            self.assertTrue(self.marker.exists())
            proc.send_signal(signal.SIGKILL); proc.wait(timeout=5)
            self.assertTrue(self.marker.exists())
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=5)


if __name__ == '__main__': unittest.main()
