"""Map a private synthetic-fixture Docker error to fixed, non-sensitive labels."""
import os
import stat
import sys

PATTERNS = {
    'no_space': b'no space left on device',
    'permission': b'permission denied',
    'memory': b'cannot allocate memory',
    'missing_image': b'no such image',
    'invalid_mount': b'invalid mount config',
    'missing_network': b'network not found',
}


def category(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 262144:
                return 'unknown'
            content = os.read(fd, 262145).lower()
        finally:
            os.close(fd)
        matches = [name for name, pattern in PATTERNS.items() if pattern in content]
        return matches[0] if len(matches) == 1 else 'multiple' if matches else 'other'
    except (OSError, ValueError):
        return 'unknown'


if __name__ == '__main__':
    print(category(sys.argv[1]) if len(sys.argv) == 2 else 'unknown')
