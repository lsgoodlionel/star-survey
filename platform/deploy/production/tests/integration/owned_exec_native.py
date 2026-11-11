"""Real local Engine exec identity test on an isolated synthetic container.

No image pull, host mount, port or network. Injected host guard is NOT process
cohort evidence. Only this fixture's captured container ID may be stopped/rm'd.
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
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from owned_docker_exec import LocalDockerAPI, OwnedDockerExec, KnownExecFailure
from runtime_ownership import PrivateStore


def main(image):
    if not isinstance(image, str) or image.startswith('-') or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image):
        raise SystemExit('a local digest-pinned Python image is required')
    name, cid = 'survey-exec-fixture-' + uuid.uuid4().hex, None
    command = ['docker', '--host', 'unix:///var/run/docker.sock']
    paths = [ROOT / value for value in ('owned_docker_exec.py', 'owned_children.py', 'runtime_ownership.py')]
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
            '-B', '-c', 'import time; time.sleep(90)'])
        if not re.fullmatch(r'[0-9a-f]{64}', cid):
            raise RuntimeError('create identity unknown')
        inspect(running=False)
        docker(['start', cid])
        inspect(running=True)
        api = LocalDockerAPI()
        def check(identity, allowed):
            if identity != cid:
                return False
            inspect(allowed, running=True)
            return True
        with tempfile.TemporaryDirectory(prefix='survey-exec-receipts-') as temporary:
            target = Path(temporary).resolve()
            (target / '.surveyctl').mkdir(mode=0o700)
            def executor():
                store = PrivateStore(target, name='runtime-exec-' + uuid.uuid4().hex + '.json')
                return OwnedDockerExec(api, store, FixtureGuard(), operation_id=str(uuid.uuid4()),
                    installation_id=str(uuid.uuid4()), container_check=check), store
            stage = 'positive'
            with tempfile.TemporaryFile() as output:
                runner, store = executor()
                runner.execute('exec-preflight', cid, SimpleNamespace(fd=output.fileno(), max_file_bytes=256))
                receipt = store.load()
                report['actualExecCompleted'] = (receipt['phase'] == 'completed' and receipt['exitCode'] == 0
                    and os.pread(output.fileno(), 256, 0) == b'survey-owned-exec-v1\n')
                report['execId'] = receipt['execId']
            stage = 'nonzero'
            with tempfile.TemporaryFile() as output:
                runner, store = executor()
                try:
                    runner.execute('postgres-dump', cid, SimpleNamespace(fd=output.fileno(), max_file_bytes=256))
                except KnownExecFailure:
                    receipt = store.load()
                    report['missingProgramRejected'] = receipt['phase'] == 'failed' and receipt['exitCode'] == 127
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
    return 0 if ('failure' not in report and report['containerCleanup'] == 'verified'
        and report['sourceBefore'] == report['sourceAfter'] and all(report.get(key) is True for key in
            ('actualExecCompleted', 'missingProgramRejected', 'foreignContainerNeverStarted'))) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    raise SystemExit(main(parser.parse_args().image))
