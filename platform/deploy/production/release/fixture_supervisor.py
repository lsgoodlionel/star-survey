"""Unintegrated test-only supervisor; it does not establish ownership.

A process group cannot contain a descendant that calls setsid. A caller must
supply an independently anchored containment verifier (e.g. a dedicated cgroup)
that proves no descendants remain, including after reparenting. Until one exists,
the default is fail-closed. SIGKILL and host loss remain externally unverified.
"""
import os
import signal
import subprocess
import time


def _group_gone(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def supervise(worker, recovery, *, verify_containment=None, stop_timeout=5, recovery_timeout=120):
    """Return failure unless each isolated process group and containment is empty.

    The verifier receives (phase, group_leader_pid), must raise or return false
    unless it proves the entire independently controlled containment is empty,
    and must not infer that proof merely from the marker or process group.
    """
    if not callable(verify_containment):
        return 1
    interrupted = False
    child = None
    worker_ok = False
    recovery_ok = False
    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)

    def interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    def clean(phase, process):
        if process is None or process.poll() is None or not _group_gone(process.pid):
            return False
        try:
            return verify_containment(phase, process.pid) is True
        except BaseException:
            return False

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        try:
            if not interrupted:
                child = subprocess.Popen(worker, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
                while child.poll() is None and not interrupted:
                    time.sleep(.05)
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=stop_timeout)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=stop_timeout)
                worker_ok = child.poll() == 0 and not interrupted
        except (OSError, subprocess.SubprocessError):
            pass
        if clean('worker', child):
            recovery_process = None
            try:
                recovery_process = subprocess.Popen(recovery, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL, start_new_session=True)
                recovery_process.wait(timeout=recovery_timeout)
                recovery_ok = recovery_process.returncode == 0 and clean('recovery', recovery_process)
            except (OSError, subprocess.SubprocessError):
                # A timed-out recovery must not keep running unattended.
                if recovery_process is not None and recovery_process.poll() is None:
                    try:
                        os.killpg(recovery_process.pid, signal.SIGKILL)
                        recovery_process.wait(timeout=stop_timeout)
                    except (OSError, subprocess.SubprocessError):
                        pass
                recovery_ok = False
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    return 0 if worker_ok and recovery_ok and not interrupted else 1
