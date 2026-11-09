"""Emit only fixed, allowlisted fixture status categories; never read raw logs."""
import os
from pathlib import Path
import stat
import sys

STAGES = frozenset({
    'initialize', 'engine_install', 'engine_setup', 'survey_publish', 'gateway',
    'platform_database', 'platform_db_create', 'platform_db_wait', 'platform_ready', 'browser', 'outbox', 'relay_first',
    'relay_replay', 'worker_recovery', 'assertions',
})
CLEANUP = frozenset({'verified', 'unverified'})
PLATFORM = frozenset({'created', 'running', 'exited', 'dead', 'restarting', 'paused', 'removing'})
ERRORS = frozenset({'no_space', 'permission', 'memory', 'missing_image', 'invalid_mount', 'missing_network', 'multiple', 'other'})


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


def read_probe(path):
    import re
    fallback = 'probe_phase=unknown first_ready=unknown attempt_count=unknown pg_isready_rc=unknown elapsed=unknown'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 160:
                return fallback
            value = os.read(fd, 161).decode('ascii').strip()
        finally:
            os.close(fd)
        pattern = (r'probe_phase=(start|loop|confirm) first_ready=(yes|no) '
                   r'attempt_count=([0-9]{1,2}) pg_isready_rc=(0|1|2|3|other|unknown) elapsed=([0-9]{1,4})')
        match = re.fullmatch(pattern, value)
        if not match or int(match.group(3)) > 60 or int(match.group(5)) > 3600:
            return fallback
        return value
    except (OSError, UnicodeError, ValueError):
        return fallback


def main():
    if len(sys.argv) not in (5, 6):
        print('stage=unknown cleanup=unknown platform=unknown docker_error=unknown')
        return
    stage = read_category(Path(sys.argv[1]), STAGES)
    cleanup = read_category(Path(sys.argv[2]), CLEANUP)
    platform = read_category(Path(sys.argv[3]), PLATFORM)
    error = read_category(Path(sys.argv[4]), ERRORS)
    output = f'stage={stage} cleanup={cleanup} platform={platform} docker_error={error}'
    if len(sys.argv) == 6:
        output += ' ' + read_probe(Path(sys.argv[5]))
    print(output)


if __name__ == '__main__':
    main()
