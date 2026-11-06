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


def _checked_child_environment(child_env):
    allowed = {'PATH', 'HOME', 'CI', 'GITHUB_ACTIONS', 'RUNNER_ENVIRONMENT', 'GITHUB_REPOSITORY',
               'SURVEY_NATIVE_ACCEPTANCE', 'NATIVE_AUTHOR_FIXTURE_DIR', 'PLAYWRIGHT_BROWSERS_PATH',
               'NATIVE_AUTHOR_EXPECTED_TENANT', 'NATIVE_AUTHOR_EXPECTED_PRINCIPAL',
               'SSL_CERT_FILE', 'NODE_EXTRA_CA_CERTS'}
    if (not isinstance(child_env, dict) or not child_env or
            any(not isinstance(k, str) or k not in allowed or not isinstance(v, str) or '\x00' in v
                for k, v in child_env.items())):
        raise ValueError('fixture child environment is invalid')
    return dict(child_env)


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


def supervise_cgroup(worker, recovery, uid, gid, *, worker_timeout=120, recovery_timeout=120, child_env=None):
    """Supervise explicitly unprivileged local processes in distinct cgroups.

    This does not authorize a target, contain daemon-created processes, prevent
    privileged API access, or prove server-side rollback. Never use it for an
    operation until those properties are independently established.
    """
    from fixture_cgroup import CgroupScope
    interrupted = False
    worker_ok = False
    recovery_ok = False
    old_term, old_int = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)

    def interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    def run(scope, command, timeout, cancel_on_signal):
        process = scope.spawn(command, env=child_env)
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            if cancel_on_signal and interrupted:
                break
            time.sleep(.05)
        # A child or detached descendant still present makes this step fail,
        # but recovery can proceed only after the kernel scope is empty.
        success = process.poll() == 0 and scope.empty()
        if not scope.empty():
            scope.kill_and_wait()
        process.wait(timeout=5)
        if not scope.empty():
            raise RuntimeError('scope did not become empty')
        return success

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    worker_scope = recovery_scope = None
    try:
        worker_scope = CgroupScope(uid, gid)
        try:
            worker_ok = run(worker_scope, worker, worker_timeout, True)
        except BaseException:
            # An ambiguous worker state must not cause recovery to begin.
            if not worker_scope.empty():
                return 1
        if not worker_scope.empty():
            return 1
        worker_scope.close()
        worker_scope = None
        recovery_scope = CgroupScope(uid, gid)
        try:
            recovery_ok = run(recovery_scope, recovery, recovery_timeout, False)
        except BaseException:
            recovery_ok = False
        if not recovery_scope.empty():
            return 1
        recovery_scope.close()
        recovery_scope = None
    except BaseException:
        return 1
    finally:
        # Never delete a group whose emptiness cannot be proved.
        for scope in (worker_scope, recovery_scope):
            if scope is not None:
                try:
                    if scope.empty():
                        scope.close()
                except BaseException:
                    pass
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    return 0 if worker_ok and recovery_ok and not interrupted else 1


def supervise_checkpointed(evidence, work, identity, provision, worker, recovery, uid, gid, verify,
                           *, run_id, attempt, manifest_sha, child_env, worker_timeout=120, recovery_timeout=120):
    """Record pending before provisioning and clear only after containment and verification.

    This is test orchestration, not target authorization: trusted callers must
    establish instance ownership, deny privileged APIs to workers, bind the
    database and verify the real recovery. Failure to provision or supervise
    retains the checkpoint. SIGKILL or host loss remains externally unverified.
    """
    from fixture_checkpoint import bound_checkpoint_identity, run_checkpointed
    if not callable(provision) or not callable(verify):
        raise ValueError('fixture callbacks are invalid')
    if identity != bound_checkpoint_identity(work, run_id, attempt, manifest_sha):
        raise ValueError('fixture checkpoint binding differs')
    child_env = _checked_child_environment(child_env)

    def action():
        provision()
        if supervise_cgroup(worker, recovery, uid, gid, worker_timeout=worker_timeout,
                            recovery_timeout=recovery_timeout, child_env=dict(child_env)) != 0:
            raise RuntimeError('fixture supervision failed')

    try:
        run_checkpointed(evidence, work, identity, action, verify,
                         run_id=run_id, attempt=attempt, manifest_sha=manifest_sha)
    except BaseException:
        raise RuntimeError('fixture execution or recovery unverified') from None


def supervise_author_preview(evidence, work, identity, provision, worker, recover, verify, uid, gid,
                             *, run_id, attempt, manifest_sha, child_env, worker_timeout=600):
    """Run an unprivileged author worker and trusted recovery after its scope is empty.

    The caller must supply real target binding, server-side writer exclusion and
    independently verified recovery; cgroups do not contain daemon work. The
    root supervisor does not expose its ambient environment to the child.
    Failed journeys and cancellation before the checkpoint commit point remain
    pending even if recovery ran. A later signal does not undo completed recovery.
    """
    from fixture_cgroup import CgroupScope
    from fixture_checkpoint import bound_checkpoint_identity, run_checkpointed
    if any(not callable(fn) for fn in (provision, recover, verify)):
        raise ValueError('fixture callbacks are invalid')
    if identity != bound_checkpoint_identity(work, run_id, attempt, manifest_sha):
        raise ValueError('fixture checkpoint binding differs')
    child_env = _checked_child_environment(child_env)
    interrupted = False
    old_term, old_int = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)

    def interrupt(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    def action():
        provision()
        if interrupted:
            raise RuntimeError('fixture interrupted before worker')
        scope = CgroupScope(uid, gid)
        process = None
        worker_ok = False
        try:
            process = scope.spawn(worker, env=child_env)
            deadline = time.monotonic() + worker_timeout
            while process.poll() is None and not interrupted and time.monotonic() < deadline:
                time.sleep(.05)
            worker_ok = process.poll() == 0 and scope.empty() and not interrupted
            if not scope.empty():
                scope.kill_and_wait()
            process.wait(timeout=5)
            if not scope.empty():
                raise RuntimeError('fixture worker scope remains populated')
            scope.close()
        except BaseException:
            # No recovery callback can begin if worker containment is uncertain.
            try:
                if not scope.empty():
                    scope.kill_and_wait()
                if process is not None:
                    process.wait(timeout=5)
                if scope.empty():
                    scope.close()
            except BaseException:
                pass
            raise
        recover()
        if not worker_ok or interrupted:
            raise RuntimeError('fixture author worker failed')

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    try:
        run_checkpointed(evidence, work, identity, action, verify,
                         run_id=run_id, attempt=attempt, manifest_sha=manifest_sha,
                         continue_finalization=lambda: not interrupted)
    except BaseException:
        raise RuntimeError('fixture author execution or recovery unverified') from None
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
