"""Test-only subprocess supervision; no target ownership or recovery proof.

Only use from a separately verified isolated fixture supervisor. SIGKILL and
host loss cannot be caught and must remain failed/unverified externally.
"""
import os
import signal
import subprocess
import time


def supervise(worker, recovery, *, stop_timeout=5, recovery_timeout=120):
    """Run recovery after a stopped worker, including a SIGTERM to this process.

    Return nonzero for interruption, any worker/recovery failure, or an uncertain
    worker stop. Commands are argument vectors and never run through a shell.
    Signal handling must run on the main Python thread. Output is discarded to
    avoid leaking test credentials; evidence must be emitted separately.
    """
    interrupted = False
    child = None
    stopped = True
    worker_ok = False
    recovery_ok = False
    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)

    def interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True

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
                        try:
                            child.wait(timeout=stop_timeout)
                        except subprocess.TimeoutExpired:
                            stopped = False
                worker_ok = child.poll() == 0 and not interrupted
        except (OSError, subprocess.SubprocessError):
            stopped = child is None or child.poll() is not None
        if stopped:
            try:
                result = subprocess.run(recovery, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=recovery_timeout, check=False)
                recovery_ok = result.returncode == 0
            except (OSError, subprocess.SubprocessError):
                recovery_ok = False
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    return 0 if worker_ok and recovery_ok and not interrupted else 1
