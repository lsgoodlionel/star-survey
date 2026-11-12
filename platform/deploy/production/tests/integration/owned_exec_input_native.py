"""Linux sealed memfd -> fixed sha256sum ExecID. NOT a database restore.

One private synthetic fixture; injected guard is not host cohort proof. Unknown
outcomes retain the receipt and container; do not retry or guess cleanup safe.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from owned_memory import AnonymousWorkspace
from owned_docker_exec import LocalDockerAPI, OwnedDockerExec, KnownExecFailure
from runtime_ownership import PrivateStore


def main(image):
    if sys.platform != 'linux' or not re.fullmatch(r'[^\s-][^\s]*@sha256:[0-9a-f]{64}', image):
        raise SystemExit('Linux and a local immutable image are required')
    paths = [Path(__file__), *(ROOT / name for name in
        ('owned_memory.py', 'owned_docker_exec.py', 'runtime_cohort.py', 'owned_children.py', 'runtime_ownership.py'))]
    hashes = lambda: {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = dict(scope='sealed-input-exec-component', sourceBefore=hashes(),
        ownerGuard='injected-not-process-proof', authenticatedBackup=False,
        databaseImport=False, restoreAuthorized=False, candidateQualified=False,
        cleanup='unknown', retainedFixtureRoot=None)
    project, cid, unknown = 'survey-stdin-fixture-' + uuid.uuid4().hex, None, True
    root = Path(tempfile.mkdtemp(prefix='survey-stdin-receipts-')).resolve()
    os.chmod(root, 0o700); (root / '.surveyctl').mkdir(mode=0o700)
    intent = root / '.surveyctl/operation.intent'
    fd = os.open(intent, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, json.dumps(dict(state='pending', fixture=project)).encode()); os.fsync(fd)
    finally: os.close(fd)
    marker = intent.read_bytes()
    def docker(args):
        return subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', *args],
            check=True, capture_output=True, timeout=35).stdout.strip()
    def check(identity, allowed=None):
        if identity != cid: return False
        # Only bounded fields, never inspect Config.Env or credentials.
        raw = docker(['inspect', '--format',
            '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},'
            '"fixture":{{json (index .Config.Labels "io.survey.fixture")}},'
            '"running":{{json .State.Running}},"network":{{json .HostConfig.NetworkMode}},'
            '"binds":{{json .HostConfig.Binds}},"readonly":{{json .HostConfig.ReadonlyRootfs}},'
            '"user":{{json .Config.User}},"privileged":{{json .HostConfig.Privileged}},'
            '"capDrop":{{json .HostConfig.CapDrop}},"capAdd":{{json .HostConfig.CapAdd}},'
            '"execs":{{json .ExecIDs}}}', cid])
        item = json.loads(raw)
        return (item['id'] == cid and item['name'] == '/' + project and item['image'] == image
            and item['fixture'] == project and item['running'] is True
            and item['network'] == 'none' and item['binds'] in (None, []) and item['readonly'] is True
            and item['user'] == '65534:65534' and item['privileged'] is False
            and item['capDrop'] == ['ALL'] and not item['capAdd']
            and item['execs'] in (None, [], [] if allowed is None else [allowed]))
    class Guard:
        def check(self):
            return dict(pid=os.getpid(), start='1', parentPid=os.getppid(), parentStart='1')
    api = LocalDockerAPI()
    stage = 'create'
    try:
        if json.loads(docker(['image', 'inspect', '--format', '{{json .Config.Volumes}}', image])) not in (None, {}):
            raise RuntimeError('implicit-volume')
        raw = docker(['create', '--pull=never', '--name', project, '--label', 'io.survey.fixture=' + project,
            '--network', 'none', '--read-only', '--user', '65534:65534', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--pids-limit', '32', '--memory', '128m',
            '--cpus', '1', '--entrypoint', 'python3', image, '-B', '-c', 'import time;time.sleep(150)'])
        cid = raw.decode('ascii')
        if not re.fullmatch(r'[0-9a-f]{64}', cid): raise RuntimeError('create-identity')
        docker(['start', cid])
        if not check(cid): raise RuntimeError('fixture-binding')
        stage = 'sealed-input'
        with AnonymousWorkspace(max_bytes=2**22) as workspace:
            source, output = workspace / 'input', workspace / 'proof'
            data = b'non-secret-stdin-fixture\n' * 65536  # Larger than socket send buffers.
            with source.open('xb') as handle: handle.write(data)
            digest = hashlib.sha256(data).hexdigest()
            operation = str(uuid.uuid4())
            store = PrivateStore(root, name='runtime-exec-' + operation.replace('-', '') + '.json')
            runner = OwnedDockerExec(api, store, Guard(), operation_id=operation,
                installation_id=str(uuid.uuid4()), container_check=check)
            stage = 'exec-input'
            with workspace.export_files(inputs=(source,), output=output) as (input_lease, output_lease):
                runner.execute('stdin-proof', cid, output_lease, input_lease=input_lease, input_sha256=digest)
            receipt = store.load()
            report['sealedMemfdAndActualExec'] = (receipt['schemaVersion'] == 3
                and receipt['phase'] == 'completed' and receipt['exitCode'] == 0
                and receipt['inputEvidence'] == dict(size=len(data), sha256=digest, delivered=True)
                and output.read_bytes() == (digest + '  -\n').encode()
                and receipt['completionEvidence'] == dict(kind='exec-inspect-terminal-v1',
                    ID=receipt['execId'], ContainerID=cid, Running=False, ExitCode=0))
            if report['sealedMemfdAndActualExec'] is not True: raise RuntimeError('proof')
            # Fault injection: an explicitly different synthetic consumer
            # fills stdout/stderr before reading stdin. It deliberately omits
            # the nonce/footer and must end FAILED, never become stdin-proof.
            # This is not a registered database or production command profile.
            stage = 'backpressure-negative-consumer'
            class FaultAPI(LocalDockerAPI):
                def create(self, container, command, *, stdin=False):
                    code = ("import hashlib,sys;"
                        "sys.stderr.buffer.write(b'e'*49152);sys.stderr.buffer.flush();"
                        "sys.stdout.buffer.write(b'x'*524288);sys.stdout.buffer.flush();"
                        "d=hashlib.sha256(sys.stdin.buffer.read()).hexdigest();"
                        "sys.stdout.buffer.write((d+'  -\\n').encode());sys.stdout.buffer.flush()")
                    return super().create(container, ['/usr/bin/timeout', '--signal=KILL', '30',
                        'python3', '-B', '-c', code], stdin=stdin)
            fault_operation = str(uuid.uuid4())
            fault_store = PrivateStore(root, name='runtime-exec-' + fault_operation.replace('-', '') + '.json')
            fault = OwnedDockerExec(FaultAPI(), fault_store, Guard(), operation_id=fault_operation,
                installation_id=receipt['installationId'], container_check=check)
            rejected = False
            with workspace.export_files(inputs=(source,), output=workspace / 'negative-proof') as (input_lease, output_lease):
                try:
                    fault.execute('stdin-proof', cid, output_lease, input_lease=input_lease, input_sha256=digest)
                except KnownExecFailure:
                    rejected = True
            negative = fault_store.load()
            report['backpressureConsumerEndedRejected'] = (rejected and negative['phase'] == 'failed'
                and negative['exitCode'] == 0 and negative['completionEvidence']['ExitCode'] == 0
                and negative['inputEvidence']['delivered'] is False)
            if not report['backpressureConsumerEndedRejected']: raise RuntimeError('backpressure-negative-proof')
            receipt = negative  # Cleanup permits only this last exact ended ExecID.
            unknown = False
        stage = 'cleanup'
    except BaseException as exc:
        report['failure'] = dict(stage=stage, category=type(exc).__name__)
    finally:
        if cid is not None and not unknown:
            try:
                if intent.read_bytes() != marker or not check(cid, receipt['execId']): raise RuntimeError('cleanup-binding')
                docker(['stop', '--time', '20', cid]); docker(['rm', cid])
                if api.container_absent(cid) is not True: raise RuntimeError('cleanup-presence')
                report['cleanup'] = 'verified'
                import shutil
                shutil.rmtree(root)  # Only this fresh private synthetic fixture after proven cleanup.
            except BaseException as exc:
                report['failure'] = dict(stage='cleanup', category=type(exc).__name__)
        if root.exists(): report['retainedFixtureRoot'] = str(root)
        report['sourceAfter'] = hashes()
        report['sourceUnchanged'] = report['sourceBefore'] == report['sourceAfter']
        print(json.dumps(report, sort_keys=True))
    return 0 if (report.get('sealedMemfdAndActualExec') is True
        and report.get('backpressureConsumerEndedRejected') is True and report['cleanup'] == 'verified'
        and report['sourceUnchanged'] is True and 'failure' not in report) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--image', required=True)
    raise SystemExit(main(parser.parse_args().image))
