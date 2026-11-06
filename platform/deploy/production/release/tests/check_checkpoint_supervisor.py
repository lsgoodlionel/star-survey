#!/usr/bin/env python3
"""CI-only cgroup/checkpoint integration; no candidate or daemon access."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_checkpoint import bound_checkpoint_identity, require_completed_checkpoint
from fixture_supervisor import supervise_author_preview, supervise_checkpointed

if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true' or
        os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
    raise RuntimeError('dedicated hosted runner required')
uid = gid = 65534
with tempfile.TemporaryDirectory(prefix='checkpoint-scope-') as root_name, tempfile.TemporaryDirectory(prefix='checkpoint-worker-') as worker_name:
    root, worker = Path(root_name), Path(worker_name)
    os.chown(worker, uid, gid); os.chmod(worker, 0o700)
    work, evidence = root / 'work', root / 'evidence'
    work.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
    identity = bound_checkpoint_identity(work, '12', '1', 'b' * 64)
    env = {'PATH': '/usr/bin:/bin'}
    child = '/usr/bin/python3'
    verify_env = "import os; assert set(os.environ) <= {'PATH','LC_CTYPE'}; assert os.geteuid()!=0"
    supervise_checkpointed(evidence, work, identity, lambda: None, [child, '-c', verify_env],
                           [child, '-c', verify_env], uid, gid, lambda: True,
                           run_id='12', attempt='1', manifest_sha='b' * 64, child_env=env)
    require_completed_checkpoint(evidence, work, identity, run_id='12', attempt='1', manifest_sha='b' * 64)
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
                               [child, '-c', recovery_code], uid, gid, lambda: True,
                               run_id='12', attempt='1', manifest_sha='b' * 64, child_env=env)
    except RuntimeError:
        pass
    else:
        raise RuntimeError('detached worker was incorrectly accepted')
    if not recovered.exists() or not (evidence / 'fixture-recovery.pending').exists():
        raise RuntimeError('recovery order or pending checkpoint not verified')
    if (evidence / 'fixture-recovery.verified').exists(): raise RuntimeError('failed execution was marked verified')
    (evidence / 'fixture-recovery.pending').unlink(); descendant.unlink(); recovered.unlink()
    def trusted_recover():
        process = Path('/proc') / descendant.read_text() / 'stat'
        state = process.read_text().rsplit(')', 1)[1].strip().split()[0] if process.exists() else 'gone'
        if state not in ('Z', 'gone'): raise RuntimeError('descendant survived into recovery')
        recovered.touch()
    try:
        supervise_author_preview(evidence, work, identity, lambda: None, [child, '-c', worker_code],
                                 trusted_recover, lambda: False, uid, gid,
                                 run_id='12', attempt='1', manifest_sha='b' * 64, child_env=env)
    except RuntimeError:
        pass
    else:
        raise RuntimeError('author worker with descendant was accepted')
    if not recovered.exists() or not (evidence / 'fixture-recovery.pending').exists():
        raise RuntimeError('author trusted recovery order or checkpoint not verified')
    # Deliver real SIGTERM during verification and during receipt fsync. The
    # supervisor must return failure and leave no trusted completion receipt.
    launcher = r'''import os,sys,time
from pathlib import Path
from unittest import mock
sys.path.insert(0,sys.argv[1])
from fixture_checkpoint import bound_checkpoint_identity
from fixture_supervisor import supervise_author_preview
work,evidence,ready,phase=Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]),sys.argv[5]
identity=bound_checkpoint_identity(work,'12','1','b'*64)
original=os.fsync; count=[0]
def delayed_fsync(fd):
    count[0]+=1
    if phase=='receipt' and count[0]==3:
        ready.touch(); time.sleep(1)
    return original(fd)
def verify():
    if phase=='verify': ready.touch(); time.sleep(1)
    return True
with mock.patch('fixture_checkpoint.os.fsync', side_effect=delayed_fsync):
    supervise_author_preview(evidence,work,identity,lambda:None,['/usr/bin/python3','-c','pass'],
        lambda:None,verify,65534,65534,run_id='12',attempt='1',manifest_sha='b'*64,
        child_env={'PATH':'/usr/bin:/bin'})
'''
    module_dir = str(Path(__file__).resolve().parents[1])
    for phase in ('verify', 'receipt'):
        phase_work = root / ('work-' + phase); phase_work.mkdir(mode=0o700)
        phase_evidence = root / ('evidence-' + phase); phase_evidence.mkdir(mode=0o700)
        ready = root / ('ready-' + phase)
        proc = subprocess.Popen([sys.executable, '-c', launcher, module_dir, str(phase_work),
                                 str(phase_evidence), str(ready), phase],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.05)
            if not ready.exists(): raise RuntimeError('signal test did not reach target phase')
            proc.send_signal(signal.SIGTERM)
            if proc.wait(timeout=10) == 0: raise RuntimeError('interrupted finalization was accepted')
            if not (phase_evidence / 'fixture-recovery.pending').exists(): raise RuntimeError('interrupted checkpoint missing')
            if (phase_evidence / 'fixture-recovery.verified').exists(): raise RuntimeError('interrupted receipt retained')
        finally:
            if proc.poll() is None: proc.kill(); proc.wait(timeout=5)
    print('checkpoint wraps real cgroup supervisor; detached descendant stopped before recovery; failure remains pending')
