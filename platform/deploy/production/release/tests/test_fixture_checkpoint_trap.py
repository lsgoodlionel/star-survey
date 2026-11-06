"""Exercise native EXIT handling with only disposable paths and stub commands."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fixture_checkpoint import run_checkpointed
from fixture_recovery_marker import RecoveryMarker


class CheckpointTrapTest(unittest.TestCase):
    def test_pending_retains_work_and_failed_summary(self):
        self.exercise(pending=True)

    def test_verified_receipt_allows_normal_cleanup(self):
        self.exercise(pending=False)

    def test_uninstall_failure_keeps_work_and_failure_summary(self):
        self.exercise(pending=False, uninstall_failure=True)

    def exercise(self, pending, uninstall_failure=False):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve(); work = base / 'work'; evidence = base / 'evidence'
            checkpoint = base / 'evidence.fixture-checkpoint'; bindir = base / 'bin'
            for path in (work, evidence, checkpoint, bindir): path.mkdir(mode=0o700)
            identity = 'a' * 64
            if pending: RecoveryMarker(checkpoint, identity).create()
            else: run_checkpointed(checkpoint, work, identity, lambda: None, lambda: True)
            if uninstall_failure:
                (work / 'primary/.surveyctl').mkdir(parents=True)
                (work / 'primary/.surveyctl/state.json').write_text('{}')
            helper = base / 'helper.py'
            helper.write_text('import sys\nfrom pathlib import Path\nargs=sys.argv\n'
                              'p=Path(args[args.index("--output")+1]) if "--output" in args else None\n'
                              'p.write_text("{}") if p else None\n')
            docker = bindir / 'docker'
            docker.write_text('#!/bin/sh\nif [ "$1" = ps ]; then exit 0; fi\nexit 1\n'); docker.chmod(0o755)
            source = (ROOT / 'native_acceptance.sh').read_text()
            function = source[source.index('collect_evidence() {'):source.index('trap collect_evidence EXIT')]
            # The production trap also edits /etc/hosts. Exclude that unrelated
            # host mutation; every remaining path here is under this temp root.
            function = '\n'.join('  :' if '/etc/hosts' in line else line for line in function.splitlines())
            shell = f'''set +e
work_root={str(work)!r}
evidence_root={str(evidence)!r}
script_dir={str(ROOT)!r}
evidence_helper={str(helper)!r}
target="$work_root/primary"
ctl_bin={'present' if uninstall_failure else ''!r}
restore_clean=true
hosts_marker=test
last_step=test
upgrade_verified=false
acceptance_status=passed
NATIVE_AUTHOR_CHECKPOINT_IDENTITY={identity!r}
evidence_secret_args() {{ :; }}
write_summary() {{ printf '%s:%s' "$acceptance_status" "$1" > "$evidence_root/result.txt"; }}
run_ctl() {{ return 1; }}
{function}
{'false' if uninstall_failure else ':'}
collect_evidence
'''
            env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['bash', '-c', shell], env=env, capture_output=True, text=True, timeout=10)
            status = json.loads((evidence / 'fixture-checkpoint-status.json').read_text())
            if pending:
                self.assertEqual(result.returncode, 5, result.stderr)
                self.assertEqual(status['status'], 'pending_or_unverified')
                self.assertEqual((evidence / 'result.txt').read_text(), 'failed:5')
                self.assertTrue(work.exists())
                self.assertTrue((checkpoint / 'fixture-recovery.pending').exists())
            elif uninstall_failure:
                self.assertEqual(result.returncode, 5, result.stderr)
                self.assertEqual(status['status'], 'verified')
                self.assertEqual((evidence / 'result.txt').read_text(), 'failed:5')
                self.assertTrue(work.exists())
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(status['status'], 'verified')
                self.assertEqual((evidence / 'result.txt').read_text(), 'passed:0')
                self.assertFalse(work.exists())
                self.assertEqual(checkpoint.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__': unittest.main()
