"""Recovery ordering for a future isolated candidate fixture supervisor.

This does not establish instance ownership or execute any operation by itself.
A caller must supply a fail-closed binding check and real, independently verified
operations. Success here alone is not evidence of revocation or restoration.
"""


class RecoveryError(RuntimeError):
    """A recovery step failed; details intentionally omit sensitive output."""


def recover_fixture(check_binding, revoke, restore, verify, doctor):
    """Attempt independent revocation and restore; verify before health checking.

    All callbacks are required to raise on an unsuccessful or ambiguous result.
    A binding failure stops immediately. A revoke failure does not prevent a
    separately bound restore attempt. Exceptions and interrupts never count as
    success, even if a later restore recovers the state.
    """
    failures = []

    def run(name, callback):
        try:
            check_binding()
        except BaseException:
            raise RecoveryError('fixture recovery binding failed') from None
        try:
            callback()
            return True
        except BaseException:
            failures.append(name)
            return False

    run('revoke', revoke)
    if run('restore', restore) and run('verify', verify):
        run('doctor', doctor)
    if failures:
        raise RecoveryError('fixture recovery failed: ' + ', '.join(failures)) from None


class FixtureExecutionError(RuntimeError):
    """A fixture attempt or its recovery did not complete."""


def execute_fixture(provision, journey, recovery):
    """Recover even when provisioning may have committed before reporting failure.

    A caller must durably mark the attempt before calling this function and
    arrange recovery on process termination. This helper cannot handle SIGKILL,
    machine loss, or guarantee that callback implementations are safe.
    """
    attempted = False
    failed = False
    try:
        attempted = True
        provision()
        journey()
    except BaseException:
        failed = True
    finally:
        if attempted:
            try:
                recovery()
            except BaseException:
                failed = True
    if failed:
        raise FixtureExecutionError('fixture execution or recovery failed') from None


def execute_marked_fixture(mark_pending, provision, journey, recovery, clear_pending):
    """Record intent before mutation and retain it whenever recovery is uncertain.

    Process-level signal supervision and durable storage across VM loss remain the
    caller's responsibility; this synchronous helper cannot provide either.
    """
    try:
        mark_pending()
    except BaseException:
        raise FixtureExecutionError('fixture recovery marker failed') from None
    failed = False
    try:
        provision()
        journey()
    except BaseException:
        failed = True
    try:
        recovery()
    except BaseException:
        raise FixtureExecutionError('fixture recovery failed; marker retained') from None
    try:
        clear_pending()
    except BaseException:
        raise FixtureExecutionError('fixture marker clearance failed') from None
    if failed:
        raise FixtureExecutionError('fixture execution failed after recovery') from None
