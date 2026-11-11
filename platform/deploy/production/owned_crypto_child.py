"""Internal FD-only OpenSSL bootstrap. Not a general command launcher."""
import fcntl
import json
import math
import os
from pathlib import Path
import resource
import signal
import stat
import sys


def describe_fd(fd, role, profile):
    if type(fd) is not int or fd <= 2 or role not in ('binary', 'input', 'output', 'key'):
        raise ValueError('invalid descriptor')
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('descriptor is not a regular file')
    if role == 'binary':
        expected = os.stat('/usr/bin/openssl', follow_symlinks=False)
        if ((info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino) or info.st_uid != 0
                or info.st_mode & 0o6022 or os.pread(fd, 4, 0) != b'\x7fELF'
                or 'security.capability' in os.listxattr(fd)):
            raise ValueError('OpenSSL binary binding is invalid')
        kind = 'binary'
    else:
        if info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink not in (0, 1):
            raise ValueError('data descriptor is not private')
        kind = 'memory' if info.st_nlink == 0 else 'ciphertext'
        plaintext = (role == 'input' and profile == 'encrypt') or (role == 'output' and profile == 'decrypt')
        if (plaintext or role == 'key') and kind != 'memory':
            raise ValueError('plaintext and keys cannot be named files')
        flags = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
        if role == 'output':
            if flags not in (os.O_WRONLY, os.O_RDWR):
                raise ValueError('output descriptor is not writable')
        elif kind == 'memory':
            required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
            if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required != required:
                raise ValueError('input memory is not sealed')
        elif flags != os.O_RDONLY:
            raise ValueError('ciphertext input must be read only')
        if role == 'key' and not 32 <= info.st_size <= 1024:
            raise ValueError('key descriptor size is invalid')
    return {'fd': fd, 'dev': info.st_dev, 'ino': info.st_ino, 'uid': info.st_uid,
        'mode': stat.S_IMODE(info.st_mode), 'nlink': info.st_nlink,
        'size': info.st_size, 'mtime': info.st_mtime_ns, 'kind': kind}


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate envelope field')
        result[key] = value
    return result


def main():
    # -I removes the ambient module path. Only this trusted package directory is
    # added; the controller must verify the package before starting this child.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from owned_children import establish_parent_guard
    try:
        if len(sys.argv) != 3:
            return 121
        guard = establish_parent_guard(int(sys.argv[1]), sys.argv[2])
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(5)  # No payload release may leave an idle bootstrap forever.
        data = sys.stdin.buffer.read(16385)
        if len(data) > 16384:
            return 122
        request = json.loads(data.decode('utf-8'), object_pairs_hook=unique)
        if (not isinstance(request, dict)
                or set(request) != {'schemaVersion', 'profile', 'fds', 'maxOutputBytes', 'timeoutSeconds'}
                or type(request['schemaVersion']) is not int or request['schemaVersion'] != 1
                or not isinstance(request['profile'], str) or request['profile'] not in ('encrypt', 'decrypt')
                or type(request['maxOutputBytes']) is not int or not 1 <= request['maxOutputBytes'] <= 2**30 + 64
                or type(request['timeoutSeconds']) is not int or not 1 <= request['timeoutSeconds'] <= 300
                or not isinstance(request['fds'], dict) or set(request['fds']) != {'binary', 'input', 'output', 'key'}):
            return 122
        observed = {}
        for role, expected in request['fds'].items():
            if not isinstance(expected, dict) or type(expected.get('fd')) is not int:
                return 123
            actual = describe_fd(expected['fd'], role, request['profile'])
            if actual != expected:
                return 123
            observed[role] = actual
        if (len({item['fd'] for item in observed.values()}) != 4
                or len({(item['dev'], item['ino']) for item in observed.values()}) != 4):
            return 123
        resource.setrlimit(resource.RLIMIT_FSIZE, (request['maxOutputBytes'], request['maxOutputBytes']))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        guard.check()
        signal.alarm(request['timeoutSeconds'])
        fds = {role: item['fd'] for role, item in observed.items()}
        # fd: reads the inherited open-file description, unlike reopening the
        # input through /proc/self/fd. A previous child may have consumed it.
        os.lseek(fds['key'], 0, os.SEEK_SET)
        os.set_inheritable(fds['binary'], False)
        arguments = ['/usr/bin/openssl', 'enc']
        if request['profile'] == 'decrypt':
            arguments.append('-d')
        arguments += ['-aes-256-cbc', '-pbkdf2', '-iter', '200000']
        if request['profile'] == 'encrypt':
            arguments.append('-salt')
        arguments += ['-in', '/proc/self/fd/' + str(fds['input']), '-out', '/proc/self/fd/' + str(fds['output']),
                      '-pass', 'fd:' + str(fds['key'])]
        os.execve(fds['binary'], arguments, {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    except Exception:
        return 126  # No raw exception, command, key or OpenSSL stderr output.
    return 125


if __name__ == '__main__':
    raise SystemExit(main())
