#!/usr/bin/env python3
"""Run only on a disposable GitHub-hosted Linux runner, never an installation.

This experiment exercises kernel cgroup accounting, not database operations or
proof that Docker-daemon-created processes belong to the same cgroup.
"""
import os
import selectors
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise RuntimeError('cgroup state was not observed')


def populated(group):
    values = dict(line.split() for line in (group / 'cgroup.events').read_text().splitlines())
    if values.get('populated') not in ('0', '1'):
        raise RuntimeError('invalid kernel cgroup state')
    return values['populated'] == '1'


def main():
    root = Path('/sys/fs/cgroup')
    if sys.platform != 'linux' or os.geteuid() != 0 or not (root / 'cgroup.controllers').is_file():
        raise RuntimeError('requires root on a cgroup v2 host')
    uid, gid = int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID'])
    if uid == 0 or gid == 0:
        raise RuntimeError('fixture child must be unprivileged')
    group = Path(tempfile.mkdtemp(prefix='survey-fixture-probe-', dir=str(root)))
    child = None
    try:
        def enter_and_drop():
            # This is a single-threaded test launcher: join before exec, then
            # discard supplementary groups and all root user/group privileges.
            (group / 'cgroup.procs').write_text(str(os.getpid()))
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)

        # Verify that setsid does not leave the kernel scope and that the
        # unprivileged process cannot move itself into the host root cgroup.
        grandchild = """import os, sys, time
try:
    with open('/sys/fs/cgroup/cgroup.procs', 'w') as f:
        f.write(str(os.getpid()))
except PermissionError:
    print('escape-denied', flush=True)
else:
    print('escape-allowed', flush=True)
    sys.exit(2)
time.sleep(60)
"""
        # The direct process exits after starting a new-session descendant.
        # The scope must remain populated after the direct process has exited.
        program = ("import subprocess, sys; "
                   "subprocess.Popen([sys.executable, '-c', " + repr(grandchild) +
                   "], start_new_session=True)")
        child = subprocess.Popen([sys.executable, '-c', program], preexec_fn=enter_and_drop,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10) or child.stdout.readline().strip() != 'escape-denied':
                raise RuntimeError('scope escape was not denied')
        if child.wait(timeout=10) != 0:
            raise RuntimeError('direct process did not exit cleanly')
        wait_for(lambda: populated(group))
        (group / 'cgroup.kill').write_text('1')
        wait_for(lambda: not populated(group))
        print('isolated cgroup retained setsid descendant after parent exit, denied migration, and became empty')
    finally:
        # Only this newly created, randomly named cgroup is touched. Never
        # recursively kill or remove any other group.
        if populated(group):
            (group / 'cgroup.kill').write_text('1')
        if child is not None:
            child.wait(timeout=10)
        wait_for(lambda: not populated(group))
        group.rmdir()

    # Exercise the actual supervisor integration with only synthetic programs.
    # The worker leaves a detached child; the supervisor must kill the scope,
    # invoke the independently scoped recovery, and still return failure.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fixture_supervisor import supervise_cgroup
    with tempfile.TemporaryDirectory(prefix='survey-fixture-check-') as tmp:
        os.chown(tmp, uid, gid)
        recovered = Path(tmp) / 'recovered'
        worker = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)"
        recovery = f"from pathlib import Path; Path({str(recovered)!r}).touch()"
        result = supervise_cgroup([sys.executable, '-c', worker], [sys.executable, '-c', recovery], uid, gid)
        if result == 0 or not recovered.exists():
            raise RuntimeError('supervisor did not contain worker before recovery')
        print('supervisor contained detached descendant before isolated recovery')


if __name__ == '__main__':
    main()
