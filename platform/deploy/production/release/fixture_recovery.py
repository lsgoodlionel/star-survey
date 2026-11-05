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
