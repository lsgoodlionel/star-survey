import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_checkpoint import bound_checkpoint_identity, require_completed_checkpoint
from fixture_supervisor import supervise_author_preview, supervise_checkpointed


class CheckpointSupervisorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.evidence, self.work = root / 'evidence', root / 'work'
        self.evidence.mkdir(mode=0o700); self.work.mkdir(mode=0o700)
        self.pending = self.evidence / 'fixture-recovery.pending'
        self.identity = bound_checkpoint_identity(self.work, '12', '1', 'b' * 64)
        self.events = []

    def invoke(self, provision, verify, env=None):
        return supervise_checkpointed(self.evidence, self.work, self.identity, provision,
            ['worker'], ['recovery'], 1001, 1001, verify,
            run_id='12', attempt='1', manifest_sha='b' * 64,
            child_env={'PATH': '/usr/bin:/bin'} if env is None else env)

    def test_pending_precedes_provision_and_supervision_and_verification(self):
        def provision():
            self.assertTrue(self.pending.exists()); self.events.append('provision')
        def supervise(*args, **kwargs):
            self.assertTrue(self.pending.exists()); self.events.append('supervise')
            self.assertEqual(kwargs['child_env'], {'PATH': '/usr/bin:/bin'})
            return 0
        def verify():
            self.assertTrue(self.pending.exists()); self.events.append('verify'); return True
        with mock.patch('fixture_supervisor.supervise_cgroup', side_effect=supervise):
            self.invoke(provision, verify)
        self.assertEqual(self.events, ['provision', 'supervise', 'verify'])
        require_completed_checkpoint(self.evidence, self.work, self.identity, run_id='12', attempt='1', manifest_sha='b' * 64)

    def test_provision_supervisor_and_verifier_failures_retain_pending(self):
        for failure in ('provision', 'supervisor', 'verify'):
            with self.subTest(failure=failure):
                def provision():
                    if failure == 'provision': raise RuntimeError('secret details')
                def verify(): return failure != 'verify'
                with mock.patch('fixture_supervisor.supervise_cgroup', return_value=1 if failure == 'supervisor' else 0):
                    with self.assertRaisesRegex(RuntimeError, '^fixture execution or recovery unverified$'):
                        self.invoke(provision, verify)
                self.assertTrue(self.pending.is_file()); self.pending.unlink()

    def test_rejects_ambient_secret_environment_before_provision(self):
        with self.assertRaises(ValueError):
            self.invoke(lambda: self.fail('provision ran'), lambda: True,
                        {'PATH': '/usr/bin', 'GITHUB_TOKEN': 'secret'})
        self.assertFalse(self.pending.exists())

    def test_author_recovery_runs_only_after_worker_scope_is_empty(self):
        events = self.events
        class Process:
            def poll(self): return 0
            def wait(self, timeout): events.append('wait'); return 0
        class Scope:
            def __init__(self, uid, gid): self.populated = True
            def spawn(self, command, *, env):
                self_env = {'PATH': '/usr/bin:/bin'}
                if env != self_env: raise AssertionError('unexpected child environment')
                events.append('spawn'); return Process()
            def empty(self): return not self.populated
            def kill_and_wait(self): events.append('kill'); self.populated = False
            def close(self): events.append('close')
        def provision():
            self.assertTrue(self.pending.exists()); events.append('provision')
        def recover():
            self.assertEqual(events[-1], 'close'); events.append('recover')
        with mock.patch('fixture_cgroup.CgroupScope', Scope):
            with self.assertRaisesRegex(RuntimeError, 'unverified'):
                supervise_author_preview(self.evidence, self.work, self.identity, provision,
                    ['worker'], recover, lambda: self.fail('verification ran'), 1001, 1001,
                    run_id='12', attempt='1', manifest_sha='b' * 64,
                    child_env={'PATH': '/usr/bin:/bin'})
        self.assertEqual(events, ['provision', 'spawn', 'kill', 'wait', 'close', 'recover'])
        self.assertTrue(self.pending.exists())


if __name__ == '__main__': unittest.main()
