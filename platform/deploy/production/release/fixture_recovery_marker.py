"""Fail-closed marker API for a future isolated fixture supervisor.

No marker deletion is authorized until independent same-user exclusion and
crash-safe ownership are proven. This does not implement or enable recovery.
"""


def clear_pending_marker(_untrusted_path):
    raise RuntimeError('marker clearance blocked pending independent exclusion')
