"""Real local Engine exec identity test on an isolated synthetic container.

No image pull, host mount, port or network. Injected host guard is NOT process
cohort evidence. Only this fixture's captured container ID may be stopped/rm'd.
"""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from owned_docker_exec import ExecMetadataGone, LocalDockerAPI, OwnedDockerExec, KnownExecFailure
from runtime_cohort import CohortProbe
from runtime_ownership import PrivateStore


def main(image, wait_for_gc=False, read_timeout=False):
    if not isinstance(image, str) or image.startswith('-') or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image):
        raise SystemExit('a local digest-pinned Python image is required')
    name, cid = 'survey-exec-fixture-' + uuid.uuid4().hex, None
    command = ['docker', '--host', 'unix:///var/run/docker.sock']
    paths = [Path(__file__), *(ROOT / value for value in
             ('owned_docker_exec.py', 'owned_children.py', 'runtime_ownership.py', 'runtime_cohort.py'))]
    hashes = lambda: {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = {'scope': 'synthetic-local-exec-id-only', 'ownerGuard': 'fixture-injected-not-process-proof',
        'containerCleanup': 'unknown', 'sourceBefore': hashes()}
    def docker(args):
        return subprocess.run(command + args, check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    def inspect(allowed=None, *, running=None):
        fields = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},'
            '"fixture":{{json (index .Config.Labels "io.survey.fixture")}},"running":{{json .State.Running}},'
            '"network":{{json .HostConfig.NetworkMode}},"binds":{{json .HostConfig.Binds}},'
            '"readOnly":{{json .HostConfig.ReadonlyRootfs}},"user":{{json .Config.User}},'
            '"privileged":{{json .HostConfig.Privileged}},"capDrop":{{json .HostConfig.CapDrop}},'
            '"capAdd":{{json .HostConfig.CapAdd}},"execs":{{json .ExecIDs}}}')
        item = json.loads(docker(['inspect', '--format', fields, cid]))
        if (item['id'] != cid or item['name'] != '/' + name or item['image'] != image or item['fixture'] != name
                or item['network'] != 'none' or item['binds'] not in ([], None) or not item['readOnly']
                or item['user'] != '65534:65534' or item['privileged'] or item['capDrop'] != ['ALL'] or item['capAdd']
                or (running is not None and item['running'] is not running)
                or (running is True and item['execs'] not in ([], None, [] if allowed is None else [allowed]))):
            raise RuntimeError('fixture isolation or exec cohort differs')
        return item['running']
    class FixtureGuard:
        def check(self):
            return {'pid': os.getpid(), 'start': '1', 'parentPid': os.getppid(), 'parentStart': '1'}
    stage = 'image'
    try:
        if json.loads(docker(['image', 'inspect', '--format', '{{json .Config.Volumes}}', image])) not in (None, {}):
            raise RuntimeError('implicit volumes are forbidden')
        stage = 'create'
        cid = docker(['create', '--pull=never', '--name', name, '--label', 'io.survey.fixture=' + name,
            '--init', '--network', 'none', '--read-only', '--user', '65534:65534', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--pids-limit', '32', '--memory', '128m', '--cpus', '1',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m', '--entrypoint', 'python3', image,
            '-B', '-c', 'import time; time.sleep(' + ('900' if wait_for_gc else '90') + ')'])
        if not re.fullmatch(r'[0-9a-f]{64}', cid):
            raise RuntimeError('create identity unknown')
        inspect(running=False)
        docker(['start', cid])
        inspect(running=True)
        class ObservedAPI(LocalDockerAPI):
            def _connect(self):
                connection = super()._connect()
                request, getresponse = connection.request, connection.getresponse
                state = {'execStart': False}
                def observed_request(method, path, *args, **kwargs):
                    state['execStart'] = method == 'POST' and bool(re.fullmatch(r'/v[0-9.]+/exec/[0-9a-f]{64}/start', path))
                    return request(method, path, *args, **kwargs)
                def observed_response():
                    response = getresponse()
                    if state['execStart']:
                        report.setdefault('execStartResponses', []).append({
                            'status': response.status, 'chunked': response.chunked,
                            'transferEncoding': ('none' if response.getheader('Transfer-Encoding') is None else
                                'chunked' if response.getheader('Transfer-Encoding').lower() == 'chunked' else 'other')})
                    return response
                connection.request, connection.getresponse = observed_request, observed_response
                return connection
        api = ObservedAPI()
        def check(identity, allowed):
            if identity != cid:
                return False
            inspect(allowed, running=True)
            return True
        with tempfile.TemporaryDirectory(prefix='survey-exec-receipts-') as temporary:
            target = Path(temporary).resolve()
            (target / '.surveyctl').mkdir(mode=0o700)
            def executor():
                operation = str(uuid.uuid4())
                store = PrivateStore(target, name='runtime-exec-' + operation.replace('-', '') + '.json')
                return OwnedDockerExec(api, store, FixtureGuard(), operation_id=operation,
                    installation_id=str(uuid.uuid4()), container_check=check), store
            stage = 'positive'
            with tempfile.TemporaryFile() as output:
                runner, store = executor()
                runner.execute('exec-preflight', cid, SimpleNamespace(fd=output.fileno(), max_file_bytes=256))
                receipt = store.load()
                report['actualExecCompleted'] = (receipt['phase'] == 'completed' and receipt['exitCode'] == 0
                    and os.pread(output.fileno(), 256, 0) == b'survey-owned-exec-v1\n')
                report['execId'] = receipt['execId']
                report['terminalObservationBound'] = (receipt['schemaVersion'] == 2 and receipt['completionEvidence'] == {
                    'kind': 'exec-inspect-terminal-v1', 'ID': receipt['execId'], 'ContainerID': cid,
                    'Running': False, 'ExitCode': 0})
            if wait_for_gc:
                stage = 'wait-real-daemon-gc'
                version = api._json('GET', '/version').get('Version')
                report['dockerVersion'] = version if isinstance(version, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) else 'unknown'
                lock = os.open(target / '.surveyctl/operation.lock', os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    # Historical receipt evaluation only; this is not an actual
                    # installation claim or host-process containment proof.
                    probe = CohortProbe(target, lock, lambda: True, api=api, _process_reader=lambda pid: None)
                    owner = {'installationId': receipt['installationId'], 'daemonId': receipt['daemonId']}
                    filename = 'runtime-exec-' + receipt['operationId'].replace('-', '') + '.json'
                    report['durableBeforeGcAccepted'] = probe._receipt(filename, receipt, owner)
                    # Attached start sends HTTP 200 before backend rejection.
                    # Detached start exposes the actual single-shot conflict.
                    connection = api._connect()
                    try:
                        connection.request('POST', api.VERSION + '/exec/' + receipt['execId'] + '/start',
                            b'{"Detach":true,"Tty":false}', {'Content-Type': 'application/json'})
                        report['finishedExecRestartStatus'] = connection.getresponse().status
                        report['finishedExecRestartRejected'] = report['finishedExecRestartStatus'] == 409
                    finally:
                        connection.close()
                    if not report['finishedExecRestartRejected']: raise RuntimeError('single-shot property unverified')
                    start, last = time.monotonic(), -60
                    while time.monotonic() - start < 660:
                        elapsed = time.monotonic() - start
                        if elapsed - last >= 60:
                            print(json.dumps({'stage': 'waiting-real-gc', 'elapsedSeconds': int(elapsed)}), file=sys.stderr, flush=True)
                            last = elapsed
                        try:
                            api.inspect(receipt['execId'])
                        except ExecMetadataGone:
                            report.update(realGcObserved=True, gcWaitSeconds=round(time.monotonic() - start, 2),
                                originalContainerStillRunning=inspect(running=True),
                                durableAfterGcAccepted=probe._receipt(filename, receipt, owner))
                            legacy = copy.deepcopy(receipt); legacy['schemaVersion'] = 1; legacy.pop('completionEvidence')
                            report['legacyAfterGcRejected'] = probe._receipt(filename, legacy, owner) is False
                            pending = dict(receipt, phase='released', exitCode=None)
                            report['pendingAfterGcRejected'] = probe._receipt(filename, pending, owner) is False
                            break
                        time.sleep(5)
                    if report.get('realGcObserved') is not True: raise RuntimeError('real GC was not observed')
                finally:
                    os.close(lock)
            stage = 'nonzero'
            with tempfile.TemporaryFile() as output:
                runner, store = executor()
                try:
                    runner.execute('postgres-dump', cid, SimpleNamespace(fd=output.fileno(), max_file_bytes=256))
                except KnownExecFailure:
                    receipt = store.load()
                    report['missingProgramRejected'] = receipt['phase'] == 'failed' and receipt['exitCode'] == 127
            if read_timeout:
                stage = 'quiet-read-deadline'
                report['quietTimeoutScope'] = 'direct-api-fixture-not-controller-or-lease-proof'
                # Deliberately outlive the read budget in this fixture only;
                # production command profiles/command timeout remain unchanged.
                quiet_id = api.create(cid, ['python3', '-B', '-c', 'import time; time.sleep(15)'])
                inspect(quiet_id, running=True)
                started, blocks = time.monotonic(), []
                try:
                    api.stream(quiet_id, blocks.append, 256, profile='exec-preflight')
                except (TimeoutError, socket.timeout):
                    elapsed = time.monotonic() - started
                    report['quietReadDeadlineSeconds'] = round(elapsed, 3)
                    report['quietReadTimedOut'] = 9 <= elapsed <= 15 and blocks == []
                except Exception as exc:
                    categories = {'OSError', 'ValueError', 'TypeError', 'AttributeError',
                                  'IncompleteRead', 'RemoteDisconnected', 'ExecProtocolError'}
                    report['quietReadExceptionCategory'] = type(exc).__name__ if type(exc).__name__ in categories else 'other'
                    report['quietReadDeadlineSeconds'] = round(time.monotonic() - started, 3)
                    raise
                else:
                    raise RuntimeError('quiet read was not timed out')
                ended = api.inspect(quiet_id)
                def bound(value):
                    return value.get('ID') == quiet_id and value.get('ContainerID') == cid
                report['execStillRunningAfterReadTimeout'] = bound(ended) and ended.get('Running') is True
                inspect(quiet_id, running=True)
                # Observe, never restart. Wait for this known fixture command to
                # finish before cleanup; this is not product unknown recovery.
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    ended = api.inspect(quiet_id)
                    if not bound(ended): raise RuntimeError('fixture exec identity changed')
                    if ended.get('Running') is False and type(ended.get('ExitCode')) is int:
                        report['quietExecEndedWithoutReplay'] = ended['ExitCode'] == 0
                        break
                    time.sleep(.2)
                if report.get('quietExecEndedWithoutReplay') is not True:
                    raise RuntimeError('fixture exec end is unverified')
            stage = 'foreign'
            with tempfile.TemporaryFile() as output:
                runner, store = executor()
                try:
                    runner.execute('exec-preflight', '0' * 64, SimpleNamespace(fd=output.fileno(), max_file_bytes=256))
                except KnownExecFailure:
                    report['foreignContainerNeverStarted'] = store.load() is None
        stage = 'postcheck'
        inspect(running=True)
    except Exception:
        report.update(failure='exec-evidence-unavailable', failureStage=stage)
    finally:
        if cid and re.fullmatch(r'[0-9a-f]{64}', cid):
            try:
                if inspect():
                    docker(['stop', '--time', '5', cid])
                inspect(running=False)
                docker(['rm', cid])
                if docker(['ps', '-aq', '--no-trunc', '--filter', 'id=' + cid]):
                    raise RuntimeError('fixture remains')
                report['containerCleanup'] = 'verified'
            except Exception:
                report['containerCleanup'] = 'failed-or-unknown'
    report['sourceAfter'] = hashes()
    print(json.dumps(report, sort_keys=True))
    gc_checks = ('durableBeforeGcAccepted', 'finishedExecRestartRejected', 'realGcObserved',
                 'originalContainerStillRunning', 'durableAfterGcAccepted', 'legacyAfterGcRejected', 'pendingAfterGcRejected')
    return 0 if ('failure' not in report and report['containerCleanup'] == 'verified'
        and report['sourceBefore'] == report['sourceAfter'] and all(report.get(key) is True for key in
            ('actualExecCompleted', 'terminalObservationBound', 'missingProgramRejected', 'foreignContainerNeverStarted'))
        and (not wait_for_gc or all(report.get(key) is True for key in gc_checks))
        and (not read_timeout or all(report.get(key) is True for key in
            ('quietReadTimedOut', 'execStillRunningAfterReadTimeout', 'quietExecEndedWithoutReplay')))) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--wait-for-gc', action='store_true', help='one isolated real daemon GC observation, at most 11 minutes')
    parser.add_argument('--read-timeout', action='store_true', help='isolated direct API quiet exec deadline, not controller proof')
    args = parser.parse_args()
    raise SystemExit(main(args.image, args.wait_for_gc, args.read_timeout))
