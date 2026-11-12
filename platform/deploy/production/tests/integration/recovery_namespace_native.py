"""Two random, empty recovery namespaces; no restore/start or production scope.

Claims are created but never started. Volumes/bridges contain no data and are
never mounted/attached. A constant cohort probe is an explicit fixture model,
not evidence of arbitrary host writer containment. Uses an already-local image.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from owned_docker_exec import LocalDockerAPI
from runtime_docker import APIOwnershipDriver
from runtime_ownership import OwnershipProtocol, PrivateStore


class Runner:
    def run(self, args, **kwargs): return subprocess.run(args, **kwargs)


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('runtime_ownership.py', 'runtime_docker.py', 'owned_docker_exec.py',
                         'tests/integration/recovery_namespace_native.py')}


def empty_namespace(api, project):
    """Query every class independently; no unknown object is adopted/deleted."""
    valid = True
    for route, filters in (('/containers/json', {'name': [project]}),
                           ('/networks', {'name': [project]}), ('/volumes', {'name': [project]})):
        try:
            raw = api._json('GET', route + '?all=1&filters=' + quote(json.dumps(filters), safe=''))
            if route == '/volumes':
                if (not isinstance(raw, dict) or set(raw) != {'Volumes', 'Warnings'}
                        or raw['Warnings'] not in (None, [])):
                    valid = False
                    continue
                rows = [] if raw['Volumes'] is None else raw['Volumes']
            else:
                rows = raw
            if not isinstance(rows, list) or rows: valid = False
        except Exception:
            valid = False
    return valid


def run(image):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image): raise ValueError('fixed image required')
    api = LocalDockerAPI()
    image_info = api._json('GET', '/images/' + quote(image, safe='') + '/json')
    if image_info.get('Os') != 'linux' or image_info.get('Config', {}).get('Volumes') not in (None, {}):
        raise ValueError('local Linux image without implicit volumes required')
    before = hashes()
    root = Path(tempfile.mkdtemp(prefix='survey-recovery-ns-')).resolve()
    protocols, locks, projects = [], [], []
    report = {'schemaVersion': 1, 'scope': 'isolated-recovery-namespace-empty-resources',
              'sourceBefore': before, 'nativeArchitecture': image_info.get('Architecture'),
              'claimsNeverStarted': True, 'cohortProbeIsModel': True,
              'businessDataCovered': False, 'restoreAuthorized': False,
              'releaseEligible': False, 'fixtureCleanup': 'unknown', 'status': 'failed'}
    step, result = 'initialization', 1
    try:
        for index in range(2):
            project = 'survey-recovery-' + uuid.uuid4().hex
            projects.append(project)
            target = root / str(index); target.mkdir(mode=0o700)
            control = target / '.surveyctl'; control.mkdir(mode=0o700)
            lock = os.open(control / 'operation.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            locks.append(lock); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            contract = {'services': {'sentinel': {'imageIds': [image_info['Id']],
                'workingDirs': [str(target)], 'mounts': []}},
                'networks': {project + '_net': {'logical': 'net', 'driver': 'bridge', 'internal': True}},
                'volumes': {project + '_data': {'logical': 'data', 'driver': 'local'}}}
            driver = APIOwnershipDriver(Runner(), contract, lambda: 'quiescent', api=api, project=project)
            protocol = OwnershipProtocol(target, driver, PrivateStore(target), image, project=project)
            protocols.append(protocol)
            step = 'claim-' + str(index); protocol.acquire()
            def create_resources():
                owner = driver.binding['installationId']
                labels = {'com.docker.compose.project': project, 'io.survey.installation': owner}
                net = api._json('POST', '/networks/create', {'Name': project + '_net', 'Driver': 'bridge',
                    'Internal': True, 'Labels': {**labels, 'com.docker.compose.network': 'net'}})
                if not isinstance(net, dict) or re.fullmatch('[0-9a-f]{64}', net.get('Id', '')) is None:
                    raise ValueError('network creation outcome unknown')
                volume = api._json('POST', '/volumes/create', {'Name': project + '_data', 'Driver': 'local',
                    'Labels': {**labels, 'com.docker.compose.volume': 'data'}})
                if not isinstance(volume, dict) or volume.get('Name') != project + '_data':
                    raise ValueError('volume creation outcome unknown')
            step = 'resources-' + str(index); protocol.mutate('install', create_resources)
        source = protocols[0].store.load()
        step = 'remove-second'; protocols[1].cleanup(True)
        report['secondNamespaceRemoved'] = empty_namespace(api, projects[1])
        step = 'source-preserved'
        report['firstNamespaceUnchanged'] = protocols[0].store.load() == source
        protocols[0].preflight_cleanup(True)
        step = 'remove-first'; protocols[0].cleanup(True)
        report['firstNamespaceRemoved'] = empty_namespace(api, projects[0])
        if all(report[name] is True for name in ('secondNamespaceRemoved', 'firstNamespaceUnchanged', 'firstNamespaceRemoved')):
            report['status'] = 'passed'; result = 0
    except Exception:
        report['failureStage'] = step  # Fixed enum only; no daemon payload/logs.
    finally:
        clean = True
        for protocol in reversed(protocols):
            try:
                record = protocol.store.load()
                if record is not None and record['phase'] in ('ready', 'retained-data', 'settled-failure'):
                    protocol.cleanup(True)
                elif record is None or record['phase'] != 'removed':
                    clean = False  # Never replay an uncertain mutation.
            except Exception:
                clean = False
        for project in projects:
            if not empty_namespace(api, project): clean = False
        for lock in reversed(locks):
            try: os.close(lock)
            except OSError: clean = False
        report['sourceAfter'] = hashes()
        if report['sourceAfter'] != before: result = 1
        if clean:
            try: shutil.rmtree(root)
            except OSError: clean = False
        if not clean: result = 1
        report['fixtureCleanup'] = 'verified' if clean else 'failed-or-unknown'
        report['privateStateRetained'] = not clean
        if result: report['status'] = 'failed'
    return report, result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--image', required=True)
    args = parser.parse_args()
    try:
        report, code = run(args.image)
    except Exception:
        report, code = {'schemaVersion': 1, 'status': 'failed', 'failureStage': 'preflight',
                        'scope': 'isolated-recovery-namespace-empty-resources', 'fixtureCleanup': 'not-started'}, 1
    print(json.dumps(report, sort_keys=True)); sys.exit(code)
