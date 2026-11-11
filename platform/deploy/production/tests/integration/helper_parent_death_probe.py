"""Unprivileged Linux feasibility experiment, NOT a production launcher.

Use --docker-fixture REPO@sha256:DIGEST with an already present Python image.
The host launcher creates only its own non-root container, without host mounts,
network, ports or extra capabilities. Never a product containment acceptance.
"""
import ctypes
import json
import os
import re
import uuid
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def identity(pid):
    try:
        data = Path('/proc', str(pid), 'stat').read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = data.rpartition(') ')[2].split()
    return {'pid': pid, 'start': fields[19], 'state': fields[0]}


def live(record):
    actual = identity(record['pid'])
    return actual is not None and actual['start'] == record['start'] and actual['state'] not in ('Z', 'X')


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise RuntimeError('bounded fixture observation failed')


def bind_parent(pid, start):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'parent-death setting rejected')
    parent = identity(pid)
    return os.getppid() == pid and parent is not None and parent['start'] == start


def spawn(role, directory):
    parent = identity(os.getpid())
    return subprocess.Popen([sys.executable, '/tmp/parent-probe.py', role, str(parent['pid']), parent['start'], str(directory)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write(directory, name, value):
    destination = directory / (name + '.json')
    temporary = directory / (name + '.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(destination)


def worker(role, parent, start, directory):
    if role == 'delayed':
        wait_for(lambda: (directory / 'allow-bind').exists())
    if not bind_parent(parent, start):
        write(directory, 'refused', {'refused': True})
        return 10
    write(directory, role, identity(os.getpid()))
    if role == 'leaf':
        binary = Path('/usr/bin/openssl')
        info = binary.stat()
        if info.st_uid != 0 or info.st_mode & 0o6022 or 'security.capability' in os.listxattr(binary):
            raise RuntimeError('fixture binary has unsafe credentials')
        os.execv(str(binary), [str(binary), 'enc', '-aes-256-ctr', '-nosalt', '-K', '00' * 32,
            '-iv', '00' * 16, '-in', '/dev/zero', '-out', '/dev/null'])
    if role == 'controller':
        spawn('helper', directory)
    elif role == 'helper':
        spawn('leaf', directory)
    elif role == 'early-controller':
        child = spawn('delayed', directory)
        write(directory, 'early-child', identity(child.pid))
    elif role == 'escape-controller':
        spawn('escape-helper', directory)
    elif role == 'escape-helper':
        # Negative control: fork/exec clears PDEATHSIG, even when its parent
        # had installed it. A bootstrap that only covers the helper is unsafe.
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        write(directory, 'raw-child', identity(child.pid))
    time.sleep(60)
    return 0


def main():
    summary = {'schemaVersion': 1, 'scope': 'unprivileged-parent-death-feasibility-only',
               'protectedChainEnded': False, 'opensslExecObserved': False, 'earlyParentRefused': False,
               'unwrappedChildEscapeObserved': False, 'cleanup': 'unknown'}
    records, parents = [], []
    def read(directory, name):
        path = directory / (name + '.json')
        wait_for(path.exists)
        record = json.loads(path.read_text())
        records.append(record)
        return record
    def terminate(record):
        if record['pid'] <= 1 or record['pid'] == os.getpid():
            raise RuntimeError('fixture identity is invalid')
        if live(record):
            os.kill(record['pid'], signal.SIGKILL)
    try:
        Path('/tmp/parent-probe.py').write_text(SOURCE)
        with tempfile.TemporaryDirectory(prefix='parent-probe-') as root:
            root = Path(root)
            for case, role in (('chain', 'controller'), ('early', 'early-controller'), ('escape', 'escape-controller')):
                directory = root / case
                directory.mkdir(mode=0o700)
                process = spawn(role, directory)
                parents.append(process)
                controller = read(directory, role)
                if case == 'chain':
                    helper, leaf = read(directory, 'helper'), read(directory, 'leaf')
                    wait_for(lambda: os.readlink('/proc/' + str(leaf['pid']) + '/exe') == '/usr/bin/openssl')
                    summary['opensslExecObserved'] = True
                    terminate(controller)
                    wait_for(lambda: not live(helper) and not live(leaf))
                    summary['protectedChainEnded'] = True
                elif case == 'early':
                    child = read(directory, 'early-child')
                    terminate(controller)
                    process.wait(timeout=5)
                    (directory / 'allow-bind').touch()
                    wait_for(lambda: (directory / 'refused.json').exists() and not live(child))
                    summary['earlyParentRefused'] = True
                else:
                    helper, raw = read(directory, 'escape-helper'), read(directory, 'raw-child')
                    terminate(controller)
                    wait_for(lambda: not live(helper))
                    summary['unwrappedChildEscapeObserved'] = live(raw)
                    terminate(raw)
                    wait_for(lambda: not live(raw))
                process.wait(timeout=5)
    except Exception as exc:
        summary['failure'] = type(exc).__name__
    finally:
        try:
            for record in reversed(records):
                terminate(record)
            for process in parents:
                process.wait(timeout=5)
            wait_for(lambda: all(not live(record) for record in records))
            for entry in Path('/proc').iterdir():
                if entry.name.isdecimal() and int(entry.name) not in (1, os.getpid()):
                    record = identity(int(entry.name))
                    if record and live(record):
                        raise RuntimeError('untracked live fixture process')
            summary['cleanup'] = 'verified-processes-ended'
        except Exception:
            summary['cleanup'] = 'failed-or-unknown'
    print(json.dumps(summary, sort_keys=True))
    return 0 if (all(summary[key] is True for key in ('protectedChainEnded', 'earlyParentRefused',
        'unwrappedChildEscapeObserved', 'opensslExecObserved')) and summary['cleanup'] == 'verified-processes-ended' and 'failure' not in summary) else 1


def docker_fixture(image):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image) or image.startswith('-'):
        raise SystemExit('fixture requires a local digest-pinned Python image')
    namespace = 'survey-parent-fixture-' + uuid.uuid4().hex
    command = ['docker', '--host', 'unix:///var/run/docker.sock']
    report = {'schemaVersion': 1, 'scope': 'isolated-parent-death-experiment', 'containerCleanup': 'unknown'}
    cid = None
    def docker(arguments):
        return subprocess.run(command + arguments, check=True, text=True, capture_output=True, timeout=30).stdout.strip()
    def inspect():
        template = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},'
            '"fixture":{{json (index .Config.Labels "io.survey.fixture")}},"running":{{json .State.Running}},'
            '"pidMode":{{json .HostConfig.PidMode}},"readOnly":{{json .HostConfig.ReadonlyRootfs}},'
            '"network":{{json .HostConfig.NetworkMode}},"privileged":{{json .HostConfig.Privileged}},'
            '"binds":{{json .HostConfig.Binds}},"user":{{json .Config.User}}}')
        item = json.loads(docker(['container', 'inspect', '--format', template, cid]))
        if (item['id'] != cid or item['name'] != '/' + namespace or item['image'] != image
                or item['fixture'] != namespace or item['pidMode'] not in ('', 'private')
                or item['readOnly'] is not True or item['network'] != 'none'
                or item['privileged'] is not False or item['binds'] not in ([], None) or item['user'] != '65534:65534'):
            raise RuntimeError('fixture identity or isolation changed')
        return item
    try:
        if json.loads(docker(['image', 'inspect', '--format', '{{json .Config.Volumes}}', image])) not in ({}, None):
            raise RuntimeError('fixture image declares implicit volumes')
        cid = docker(['container', 'create', '--pull=never', '--name', namespace,
            '--label', 'io.survey.fixture=' + namespace, '--init', '--network', 'none', '--read-only',
            '--user', '65534:65534', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--pids-limit', '32', '--memory', '128m', '--cpus', '1',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m', '-i', '--entrypoint', 'python3', image, '-B', '-'])
        if not re.fullmatch(r'[0-9a-f]{64}', cid):
            raise RuntimeError('fixture creation outcome is unknown')
        inspect()
        source = Path(__file__).read_text()
        result = subprocess.run(command + ['container', 'start', '--attach', '--interactive', cid],
            input='SOURCE=' + repr(source) + '\nexec(compile(SOURCE,"<isolated-parent-probe>","exec"))\n',
            check=False, text=True, capture_output=True, timeout=90)
        evidence = json.loads(result.stdout)
        keys = {'schemaVersion', 'scope', 'protectedChainEnded', 'opensslExecObserved', 'earlyParentRefused',
            'unwrappedChildEscapeObserved', 'cleanup'}
        if (not isinstance(evidence, dict) or set(evidence) != keys or type(evidence['schemaVersion']) is not int
                or evidence['schemaVersion'] != 1 or evidence['scope'] != 'unprivileged-parent-death-feasibility-only'
                or any(type(evidence[key]) is not bool for key in keys - {'schemaVersion', 'scope', 'cleanup'})
                or evidence['cleanup'] not in ('unknown', 'verified-processes-ended', 'failed-or-unknown')):
            raise RuntimeError('fixture evidence is unavailable or malformed')
        report.update(exitCode=result.returncode, evidence=evidence)
    except Exception:
        report['failure'] = 'fixture-execution-or-evidence-unverified'
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
    print(json.dumps(report, sort_keys=True))
    evidence = report.get('evidence', {})
    return 0 if (report.get('exitCode') == 0 and report['containerCleanup'] == 'verified'
        and evidence.get('cleanup') == 'verified-processes-ended'
        and all(evidence.get(key) is True for key in ('protectedChainEnded', 'opensslExecObserved',
            'earlyParentRefused', 'unwrappedChildEscapeObserved'))) else 1


if len(sys.argv) == 3 and sys.argv[1] == '--docker-fixture':
    raise SystemExit(docker_fixture(sys.argv[2]))
if sys.platform != 'linux' or os.geteuid() != 65534 or not Path('/.dockerenv').is_file():
    raise SystemExit('fixture requires an isolated non-root Docker PID namespace')
os.umask(0o077)
if len(sys.argv) > 1:
    raise SystemExit(worker(sys.argv[1], int(sys.argv[2]), sys.argv[3], Path(sys.argv[4])))
raise SystemExit(main())
