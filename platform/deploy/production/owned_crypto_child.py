"""Internal FD-only OpenSSL bootstrap. Not a general command launcher."""
import fcntl
import array
import json
import math
import os
from pathlib import Path
import resource
import signal
import socket
import struct
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


def load_parent_guard():
    # The controller admits this complete package before starting the child.
    # A normal import could nevertheless select a timestamp-valid altered pyc.
    # Read only the admitted companion path, never import/search/cache it.
    path = Path(__file__).absolute().with_name('owned_children.py')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        def snapshot(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid not in (0, os.geteuid()) or before.st_mode & 0o022
                or not 1 <= before.st_size <= 131072):
            raise ValueError('guard companion source is unsafe')
        source = os.read(fd, 131073)
        if (len(source) != before.st_size or snapshot(os.fstat(fd)) != snapshot(before)
                or snapshot(os.lstat(path)) != snapshot(before)):
            raise ValueError('guard companion source changed')
    finally:
        os.close(fd)
    namespace = {'__name__': '_survey_admitted_child_boundary', '__file__': str(path)}
    exec(compile(source, str(path), 'exec', dont_inherit=True), namespace)
    factory = namespace.get('establish_parent_guard')
    if not callable(factory): raise ValueError('guard companion has no fixed entry')
    return factory


def main():
    try:
        if len(sys.argv) != 4:
            return 121
        establish_parent_guard = load_parent_guard()
        guard = establish_parent_guard(int(sys.argv[1]), sys.argv[2])
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(5)  # No payload release may leave an idle bootstrap forever.
        control_fd = int(sys.argv[3])
        if control_fd <= 2: return 121
        with socket.socket(fileno=control_fd) as channel:
            os.set_inheritable(control_fd, False)
            if channel.family != socket.AF_UNIX or channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET:
                return 121
            peer_pid, peer_uid, _ = struct.unpack('3i', channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer_pid != int(sys.argv[1]) or peer_uid != os.geteuid(): return 121
            channel.settimeout(5)
            guard.check()
            channel.send(b'SURVEY-CRYPTO-GUARDED-1')  # No data/key FD exists in this process yet.
            data, messages, flags, _ = channel.recvmsg(16385, socket.CMSG_SPACE(4 * array.array('i').itemsize),
                socket.MSG_CMSG_CLOEXEC)
            received = []
            for level, kind, content in messages:
                if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS: return 122
                values = array.array('i'); values.frombytes(content[:len(content) - len(content) % values.itemsize])
                received.extend(values)
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(received) != 4 or len(set(received)) != 4:
                return 122
            guard.check()
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
        for role, descriptor in zip(('input', 'output', 'key', 'binary'), received):
            expected = request['fds'][role]
            if not isinstance(expected, dict) or type(expected.get('fd')) is not int:
                return 123
            actual = describe_fd(descriptor, role, request['profile'])
            if {k: v for k, v in actual.items() if k != 'fd'} != {k: v for k, v in expected.items() if k != 'fd'}:
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
        for role in ('input', 'output', 'key'):
            os.set_inheritable(fds[role], True)
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
