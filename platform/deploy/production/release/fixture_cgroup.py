"""Unprivileged fixture subprocess containment for a disposable Linux runner.

This scope does not contain work delegated to Docker, a database server, or any
other external daemon. A caller must independently establish an isolated host,
ensure the child cannot access privileged APIs, and verify server-side state.
"""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


class CgroupError(RuntimeError):
    pass


class CgroupScope:
    def __init__(self, uid, gid):
        if sys.platform != 'linux' or os.geteuid() != 0 or uid <= 0 or gid <= 0:
            raise CgroupError('cgroup requires root supervisor and unprivileged child')
        self.uid, self.gid = uid, gid
        self.path = Path(tempfile.mkdtemp(prefix='survey-fixture-', dir='/sys/fs/cgroup'))
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        metadata = os.fstat(self.fd)
        self.identity = (metadata.st_dev, metadata.st_ino)

    def _check(self):
        metadata = os.stat(self.path, follow_symlinks=False)
        if (metadata.st_dev, metadata.st_ino) != self.identity:
            raise CgroupError('cgroup identity changed')

    def spawn(self, command):
        self._check()
        procs = os.open('cgroup.procs', os.O_WRONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        uid, gid = self.uid, self.gid
        def enter():
            os.write(procs, str(os.getpid()).encode('ascii'))
            os.close(procs)
            os.setgroups([])
            os.setresgid(gid, gid, gid)
            os.setresuid(uid, uid, uid)
            # Prevent regaining privilege through setuid binaries or file caps.
            if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
                os._exit(126)
        try:
            return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True,
                                    preexec_fn=enter, pass_fds=(procs,))
        finally:
            os.close(procs)

    def empty(self):
        self._check()
        fd = os.open('cgroup.events', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, 'r', encoding='ascii') as stream:
            lines = stream.read(4096).splitlines()
        values = [line.split() for line in lines]
        matches = [value[1] for value in values if len(value) == 2 and value[0] == 'populated']
        if len(matches) != 1 or matches[0] not in ('0', '1'):
            raise CgroupError('cgroup status ambiguous')
        return matches[0] == '0'

    def kill_and_wait(self, timeout=5):
        self._check()
        fd = os.open('cgroup.kill', os.O_WRONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write('1')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.empty():
                return
            time.sleep(.05)
        raise CgroupError('cgroup did not become empty')

    def close(self):
        self._check()
        if not self.empty():
            raise CgroupError('cgroup remains populated')
        self.path.rmdir()
        os.close(self.fd)
