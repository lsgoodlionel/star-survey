#!/usr/bin/env python3
"""CI-only cgroup/checkpoint integration; no candidate or daemon access."""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_checkpoint import require_completed_checkpoint
from fixture_supervisor import supervise_checkpointed

if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true' or
        os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
    raise RuntimeError('dedicated hosted runner required')
uid = gid = 65534
identity = 'a' * 64
with tempfile.TemporaryDirectory(prefix='checkpoint-scope-') as root_name, tempfile.TemporaryDirectory(prefix='checkpoint-worker-') as worker_name:
    root, worker = Path(root_name), Path(worker_name)
    os.chown(worker, uid, gid); os.chmod(worker, 0o700)
    work, evidence = root / 'work', root / 'evidence'
    work.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
    env = {'PATH': '/usr/bin:/bin'}
    child = '/usr/bin/python3'
    verify_env = "import os; assert set(os.environ) <= {'PATH','LC_CTYPE'}; assert os.geteuid()!=0"
    supervise_checkpointed(evidence, work, identity, lambda: None, [child, '-c', verify_env],
                           [child, '-c', verify_env], uid, gid, lambda: True, child_env=env)
    require_completed_checkpoint(evidence, work, identity)
    (evidence / 'fixture-recovery.verified').unlink()
    descendant = worker / 'descendant.pid'; recovered = worker / 'recovered'
    child_code = f"import os,time; from pathlib import Path; Path({str(descendant)!r}).write_text(str(os.getpid())); time.sleep(60)"
    worker_code = ("import subprocess,sys,time; from pathlib import Path; "
                   "subprocess.Popen([sys.executable,'-c'," + repr(child_code) + "],start_new_session=True); "
                   f"p=Path({str(descendant)!r}); deadline=time.monotonic()+5; "
                   "exec(\"while not p.exists() and time.monotonic()<deadline:\\n time.sleep(.02)\"); assert p.exists()")
    recovery_code = (f"from pathlib import Path; p=Path('/proc')/Path({str(descendant)!r}).read_text()/'stat'; "
                     "state=p.read_text().rsplit(')',1)[1].strip().split()[0] if p.exists() else 'gone'; "
                     "assert state in ('Z','gone'); " + f"Path({str(recovered)!r}).touch()")
    try:
        supervise_checkpointed(evidence, work, identity, lambda: None, [child, '-c', worker_code],
                               [child, '-c', recovery_code], uid, gid, lambda: True, child_env=env)
    except RuntimeError:
        pass
    else:
        raise RuntimeError('detached worker was incorrectly accepted')
    if not recovered.exists() or not (evidence / 'fixture-recovery.pending').exists():
        raise RuntimeError('recovery order or pending checkpoint not verified')
    if (evidence / 'fixture-recovery.verified').exists(): raise RuntimeError('failed execution was marked verified')
    print('checkpoint wraps real cgroup supervisor; detached descendant stopped before recovery; failure remains pending')
