"""Emit only fixed, allowlisted fixture status categories; never read raw logs."""
import os
from pathlib import Path
import stat
import sys

STAGES = frozenset({
    'initialize', 'engine_install', 'engine_setup', 'survey_publish', 'gateway',
    'platform_database', 'platform_ready', 'browser', 'outbox', 'relay_first',
    'relay_replay', 'worker_recovery', 'assertions',
})
CLEANUP = frozenset({'verified', 'unverified'})


def read_category(path, allowed):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64:
                return 'unknown'
            value = os.read(fd, 65).decode('ascii').strip()
        finally:
            os.close(fd)
        return value if value in allowed else 'unknown'
    except (OSError, UnicodeError, ValueError):
        return 'unknown'


def main():
    if len(sys.argv) != 3:
        print('stage=unknown cleanup=unknown')
        return
    stage = read_category(Path(sys.argv[1]), STAGES)
    cleanup = read_category(Path(sys.argv[2]), CLEANUP)
    print(f'stage={stage} cleanup={cleanup}')


if __name__ == '__main__':
    main()
