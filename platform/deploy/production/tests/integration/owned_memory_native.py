"""Explicit kernel checks in a new non-root container; no product/helper gate.

Usage: python3 -B owned_memory_native.py --image PYTHON_REPO@sha256:DIGEST
Only uses a local image. It never pulls, mounts the host, or executes a backup.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types
import uuid


CHECKS = ('privateAnonymousNonInheritable', 'kernelWriteSealEnforced',
          'childBudgetEnforced', 'originalDescriptorGone', 'noNamedWorkspaceFiles',
          'privateReferenceNonInheritable', 'identityPinnedAcrossReplacement',
          'foreignReplacementRejected', 'noReferenceDescriptorLeaks')
CHILD_BUDGET = '''
import errno, os, resource, signal, sys
fd, limit = int(sys.argv[1]), int(sys.argv[2])
resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
try:
    os.write(fd, b'x' * (limit + 1))
    os.write(fd, b'y')
except OSError as exc:
    sys.exit(23 if exc.errno == errno.EFBIG else 24)
sys.exit(25)
'''


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def native_check():
    summary = {key: False for key in CHECKS}
    summary.update(schemaVersion=1, scope='anonymous-workspace-kernel-only',
                   sourceSha256=digest(SOURCE), ownedSourceSha256=digest(OWNED_SOURCE))
    workspace = None
    try:
        module = types.ModuleType('owned_memory')
        exec(compile(OWNED_SOURCE, '<owned-memory>', 'exec'), module.__dict__)
        workspace = module.AnonymousWorkspace(max_bytes=32)
        plain = workspace / 'payload'
        with plain.open('xb') as output:
            output.write(b'fixture')
        with plain.export_for_child(write=False) as lease:
            original = lease.fd
            anchor = plain._entry['anchor']
            summary['privateReferenceNonInheritable'] = (anchor != original and not os.get_inheritable(anchor))
            info = os.fstat(original)
            identity = info.st_dev, info.st_ino
            summary['privateAnonymousNonInheritable'] = (info.st_nlink == 0
                and info.st_mode & 0o777 == 0o600 and not os.get_inheritable(original))
            try:
                os.pwrite(original, b'x', 0)
            except PermissionError as exc:
                summary['kernelWriteSealEnforced'] = exc.errno == errno.EPERM
        output = workspace / 'child-output'
        compile(CHILD_BUDGET, '<budget-child>', 'exec')
        with output.export_for_child(write=True) as lease:
            result = subprocess.run([sys.executable, '-B', '-c', CHILD_BUDGET,
                str(lease.fd), str(lease.max_file_bytes)], pass_fds=(lease.fd,),
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            summary['childBudgetEnforced'] = (result.returncode == 23
                and os.fstat(lease.fd).st_size <= lease.max_file_bytes)
        references = (anchor, output._entry['anchor'])
        workspace.close()
        released = []
        for fd in references:
            try: os.fstat(fd); released.append(False)
            except OSError as exc: released.append(exc.errno == errno.EBADF)
        try:
            current = os.fstat(original)
            summary['originalDescriptorGone'] = (current.st_dev, current.st_ino) != identity
        except OSError as exc:
            summary['originalDescriptorGone'] = exc.errno == errno.EBADF
        before = set(os.listdir('/proc/self/fd'))
        replacement = module.AnonymousWorkspace(max_bytes=32)
        path = replacement / 'identity'
        with path.open('xb') as handle: handle.write(b'owned')
        public, reference = path._entry['fd'], path._entry['anchor']
        identity = os.fstat(reference).st_dev, os.fstat(reference).st_ino
        os.close(public)
        foreign = os.memfd_create('foreign-fixture', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        os.fchmod(foreign, 0o600)
        if foreign != public: os.dup2(foreign, public, inheritable=False)
        try:
            os.pwrite(public, b'foreign', 0)
            summary['identityPinnedAcrossReplacement'] = (os.pread(reference, 5, 0) == b'owned'
                and identity != (os.fstat(public).st_dev, os.fstat(public).st_ino))
            rejected = []
            for action in (path.read_bytes, path.unlink, replacement.close, replacement.close):
                try: action(); rejected.append(False)
                except module.WorkspaceError: rejected.append(True)
            summary['foreignReplacementRejected'] = all(rejected) and os.pread(public, 7, 0) == b'foreign'
            try: os.fstat(reference); released.append(False)
            except OSError as exc: released.append(exc.errno == errno.EBADF)
        finally:
            if foreign != public: os.close(public)
            os.close(foreign)
        summary['noReferenceDescriptorLeaks'] = all(released) and set(os.listdir('/proc/self/fd')) == before
        summary['noNamedWorkspaceFiles'] = not list(Path('/tmp').iterdir())
    except Exception:
        summary['failure'] = 'native-check-failed'
    finally:
        if workspace is not None:
            try:
                workspace.close()
            except Exception:
                summary['failure'] = 'workspace-close-unverified'
    print(json.dumps(summary, sort_keys=True))
    return 0 if all(summary[key] is True for key in CHECKS) and 'failure' not in summary else 1


def host_check(image):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image) or image.startswith('-'):
        raise SystemExit('a local digest-pinned Python image is required')
    name = 'survey-memfd-fixture-' + uuid.uuid4().hex
    command = ['docker', '--host', 'unix:///var/run/docker.sock']
    paths = (Path(__file__), Path(__file__).resolve().parents[2] / 'owned_memory.py')
    sources = [path.read_text() for path in paths]
    hashes = list(map(digest, sources))
    report = {'schemaVersion': 1, 'scope': 'isolated-memfd-kernel-experiment',
              'containerCleanup': 'unknown', 'sourceBefore': hashes}
    cid = None
    def docker(arguments):
        return subprocess.run(command + arguments, check=True, text=True,
            capture_output=True, timeout=30).stdout.strip()
    def inspect():
        fields = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},'
            '"fixture":{{json (index .Config.Labels "io.survey.fixture")}},"running":{{json .State.Running}},'
            '"pid":{{json .HostConfig.PidMode}},"network":{{json .HostConfig.NetworkMode}},'
            '"binds":{{json .HostConfig.Binds}},"readOnly":{{json .HostConfig.ReadonlyRootfs}},'
            '"privileged":{{json .HostConfig.Privileged}},"user":{{json .Config.User}},'
            '"capDrop":{{json .HostConfig.CapDrop}},"capAdd":{{json .HostConfig.CapAdd}}}')
        item = json.loads(docker(['container', 'inspect', '--format', fields, cid]))
        if (item['id'] != cid or item['name'] != '/' + name or item['image'] != image or item['fixture'] != name
                or item['pid'] not in ('', 'private') or item['network'] != 'none' or item['binds'] not in (None, [])
                or item['readOnly'] is not True or item['privileged'] is not False or item['user'] != '65534:65534'
                or item['capDrop'] != ['ALL'] or item['capAdd'] not in (None, [])):
            raise RuntimeError('fixture ownership or isolation changed')
        return item
    try:
        if json.loads(docker(['image', 'inspect', '--format', '{{json .Config.Volumes}}', image])) not in (None, {}):
            raise RuntimeError('image declares implicit volumes')
        cid = docker(['container', 'create', '--pull=never', '--name', name,
            '--label', 'io.survey.fixture=' + name, '--init', '--network', 'none', '--read-only',
            '--user', '65534:65534', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--pids-limit', '32', '--memory', '128m', '--cpus', '1',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m', '-i', '--entrypoint', 'python3', image, '-B', '-', '--inside'])
        if not re.fullmatch(r'[0-9a-f]{64}', cid):
            raise RuntimeError('fixture creation is unknown')
        inspect()
        payload = 'SOURCE=' + repr(sources[0]) + '\nOWNED_SOURCE=' + repr(sources[1]) + '\nexec(compile(SOURCE,"<memfd-probe>","exec"))\n'
        compile(payload, '<fixture-stdin>', 'exec')
        result = subprocess.run(command + ['container', 'start', '--attach', '--interactive', cid],
            input=payload, check=False, text=True, capture_output=True, timeout=90)
        evidence = json.loads(result.stdout)
        keys = {*CHECKS, 'schemaVersion', 'scope', 'sourceSha256', 'ownedSourceSha256'}
        if (not isinstance(evidence, dict) or set(evidence) != keys or type(evidence['schemaVersion']) is not int
                or evidence['schemaVersion'] != 1 or evidence['scope'] != 'anonymous-workspace-kernel-only'
                or [evidence['sourceSha256'], evidence['ownedSourceSha256']] != hashes
                or any(type(evidence[key]) is not bool for key in CHECKS)):
            raise RuntimeError('native evidence is unverified')
        report.update(exitCode=result.returncode, evidence=evidence)
    except Exception:
        report['failure'] = 'fixture-or-kernel-evidence-unverified'
    finally:
        if cid and re.fullmatch(r'[0-9a-f]{64}', cid):
            try:
                if inspect()['running']:
                    docker(['container', 'stop', '--time', '5', cid])
                docker(['container', 'rm', cid])
                if docker(['container', 'ls', '--all', '--no-trunc', '--quiet', '--filter', 'id=' + cid]):
                    raise RuntimeError('fixture remains')
                report['containerCleanup'] = 'verified'
            except Exception:
                report['containerCleanup'] = 'failed-or-unknown'
    report['sourceAfter'] = [digest(path.read_text()) for path in paths]
    print(json.dumps(report, sort_keys=True))
    return 0 if (report.get('exitCode') == 0 and report['sourceAfter'] == hashes
        and report['containerCleanup'] == 'verified' and 'failure' not in report
        and all(report['evidence'][key] is True for key in CHECKS)) else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['--inside']:
        if (sys.platform != 'linux' or os.geteuid() != 65534 or not Path('/.dockerenv').is_file()
                or 'SOURCE' not in globals() or 'OWNED_SOURCE' not in globals()):
            raise SystemExit('internal probe requires its own controlled container')
        raise SystemExit(native_check())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    raise SystemExit(host_check(parser.parse_args().image))
