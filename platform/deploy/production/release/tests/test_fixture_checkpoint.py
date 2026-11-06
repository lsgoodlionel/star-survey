import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_checkpoint import require_completed_checkpoint, run_checkpointed


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.work, self.evidence = self.base / 'work', self.base / 'evidence'
        self.work.mkdir(mode=0o700); self.evidence.mkdir(mode=0o700)
        self.marker = self.evidence / 'fixture-recovery.pending'
        self.identity = 'a' * 64

    def run_guard(self, action, verify):
        return run_checkpointed(self.evidence, self.work, self.identity, action, verify)

    def test_verified_completion_clears_marker_only_after_verify(self):
        def verify():
            self.assertTrue(self.marker.is_file())
            return True
        self.run_guard(lambda: self.assertTrue(self.marker.is_file()), verify)
        self.assertFalse(self.marker.exists())
        require_completed_checkpoint(self.evidence, self.work, self.identity)

    def test_receipt_required_and_pending_overrides_receipt(self):
        with self.assertRaises(OSError):
            require_completed_checkpoint(self.evidence, self.work, self.identity)
        self.run_guard(lambda: None, lambda: True)
        self.marker.write_text(self.identity + '\n')
        with self.assertRaises(RuntimeError):
            require_completed_checkpoint(self.evidence, self.work, self.identity)
        self.marker.unlink()
        (self.evidence / 'fixture-recovery.verified').write_text('b' * 64 + '\n')
        with self.assertRaises(ValueError):
            require_completed_checkpoint(self.evidence, self.work, self.identity)

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
            run_checkpointed(nested, self.work, self.identity, lambda: self.fail('action ran'), lambda: True)
        self.evidence.chmod(0o755)
        with self.assertRaises(ValueError): self.run_guard(lambda: self.fail('action ran'), lambda: True)

    def test_killed_supervisor_leaves_pending_marker_on_surviving_filesystem(self):
        module_dir = str(Path(__file__).resolve().parents[1])
        code = '''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from fixture_checkpoint import run_checkpointed
run_checkpointed(Path(sys.argv[2]),Path(sys.argv[3]),'a'*64,lambda: time.sleep(60),lambda: True)
'''
        proc = subprocess.Popen([sys.executable, '-c', code, module_dir, str(self.evidence), str(self.work)])
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
