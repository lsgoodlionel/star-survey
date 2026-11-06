"""Exercise diagnostic hook through the real EXIT trap with disposable stubs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'platform/deploy/production/release'
sys.path.insert(0, str(ROOT))
from fixture_checkpoint import bound_checkpoint_identity

class DiagnosticChainTest(unittest.TestCase):
    def test_success_and_failure_never_set_author_latch_or_uninstall(self):
        source = (ROOT / 'native_acceptance.sh').read_text()
        hook = source[source.index('# Hosted-only diagnostic:'):source.index('last_step="uninstall"')]
        trap = source[source.index('collect_evidence() {'):source.index('trap collect_evidence EXIT')]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for scenario in ('success', 'failure'):
                with self.subTest(scenario=scenario):
                    root = base / scenario; root.mkdir()
                    work=root/'work'; evidence=root/'evidence'; workspace=root/'workspace'; bin_dir=root/'bin'
                    for item in (work,evidence,workspace,bin_dir): item.mkdir(mode=0o700)
                    identity=bound_checkpoint_identity(work,'12','1','b'*64)
                    (workspace/'scratch_candidate_stop.py').write_text('''import os, sys
from pathlib import Path
from fixture_checkpoint import run_checkpointed
work, evidence = map(Path, sys.argv[1:])
os.mkdir(evidence, 0o700)
def action():
    if os.environ['SCENARIO'] == 'failure': raise RuntimeError('injected failure')
run_checkpointed(evidence, work, os.environ['NATIVE_AUTHOR_CHECKPOINT_IDENTITY'], action, lambda: True, run_id='12', attempt='1', manifest_sha='b'*64)
''')
                    docker=bin_dir/'docker'; docker.write_text('#!/bin/sh\ntouch "$SENTINEL"\nexit 1\n'); docker.chmod(0o755)
                    helper=root/'helper.py'; helper.write_text('')
                    script=f'''set -Eeuo pipefail
work_root={str(work)!r}
evidence_root={str(evidence)!r}
script_dir={str(ROOT)!r}
evidence_helper={str(helper)!r}
target="$work_root/primary"
ctl_bin="unused"
hosts_marker=test
last_step=test
upgrade_verified=false
acceptance_status=failed
author_mode=1
author_checkpoint_verified=false
NATIVE_AUTHOR_CHECKPOINT_IDENTITY={identity!r}
export NATIVE_AUTHOR_CHECKPOINT_IDENTITY
GITHUB_RUN_ID=12
GITHUB_RUN_ATTEMPT=1
CANDIDATE_MANIFEST_SHA256={'b'*64!r}
evidence_secret_args() {{ :; }}
write_summary() {{ printf '%s:%s' "$acceptance_status" "$1" > "$evidence_root/result.txt"; }}
run_ctl() {{ touch "$SENTINEL"; }}
{trap}
trap collect_evidence EXIT
{hook}
'''
                    env=dict(os.environ, GITHUB_WORKSPACE=str(workspace), RUNNER_ENVIRONMENT='github-hosted',
                             GITHUB_REPOSITORY='lsgoodlionel/star-survey', NATIVE_CANDIDATE_STOP_DIAGNOSTIC='1',
                             SCENARIO=scenario, SENTINEL=str(root/'called'), PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
                    result=subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=10)
                    self.assertEqual(result.returncode,5,result.stderr)
                    self.assertTrue(work.exists())
                    self.assertFalse((root/'called').exists())
                    self.assertEqual(json.loads((evidence/'fixture-checkpoint-status.json').read_text())['status'],'pending_or_unverified')
                    checkpoint=root/'evidence.fixture-checkpoint'
                    self.assertTrue((checkpoint/('fixture-recovery.verified' if scenario=='success' else 'fixture-recovery.pending')).is_file())
                    self.assertEqual((evidence/'result.txt').read_text(),'failed:5')

if __name__ == '__main__': unittest.main()
