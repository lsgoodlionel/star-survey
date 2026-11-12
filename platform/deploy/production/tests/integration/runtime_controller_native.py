"""Real controller cleanup on synthetic, random-namespace Compose fixtures.

Not a signed candidate or business installation. Hash generation and databases
are deliberately not substituted or claimed passed. Only captured fixture IDs
are torn down, separately from product success/refusal. Pending markers remain.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import runtime_ownership as ownership
from owned_docker_exec import LocalDockerAPI
from runtime_controller import RuntimeController
from test_surveyctl import write_test_release, load_module


def resource_absent(api, kind, identity):
    if kind not in ('containers', 'networks', 'volumes') or not re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', identity):
        raise ValueError('fixture identity invalid')
    connection = api._connect()
    try:
        connection.request('GET', api.VERSION + '/' + kind + '/' + quote(identity, safe='')
            + ('/json' if kind == 'containers' else ''))
        response = connection.getresponse()
        if response.status not in (200, 404): raise RuntimeError('fixture presence unknown')
        return response.status == 404
    finally:
        connection.close()


def remove_and_confirm(api, kind, identity):
    path = '/' + kind + '/' + quote(identity, safe='')
    if kind == 'containers': path += '?force=false&v=false'
    elif kind == 'volumes': path += '?force=false'
    elif kind != 'networks': raise ValueError('fixture kind invalid')
    api._json('DELETE', path, accepted=(204,))
    if not resource_absent(api, kind, identity): raise RuntimeError('fixture remains after deletion')


def namespace_empty(namespace):
    if not re.fullmatch(r'survey-control-fixture-[0-9a-f]{32}', namespace): return False
    empty = True
    for kind in ('container', 'network', 'volume'):
        try:
            args = ['docker', '--host', 'unix:///var/run/docker.sock', kind, 'ls']
            if kind == 'container': args.append('--all')
            field = '.Names' if kind == 'container' else '.Name'
            result = subprocess.run(args + ['--filter', 'name=' + namespace, '--format', '{{json ' + field + '}}'],
                check=True, text=True, capture_output=True, timeout=30)
            if len(result.stdout.encode()) > 65536: raise RuntimeError('fixture inventory oversized')
            for line in result.stdout.splitlines():
                name = json.loads(line)
                if not isinstance(name, str): raise RuntimeError('fixture inventory invalid')
                if name.startswith((namespace + '-', namespace + '_')): empty = False
        except Exception:
            empty = False
    return empty


def run_case(image, scenario):
    namespace = 'survey-control-fixture-' + uuid.uuid4().hex
    ownership.PROJECT, ownership.CLAIM_NAME = namespace, namespace + '-ownership-v1'
    if not namespace.startswith('survey-control-fixture-'):
        raise RuntimeError('fixture namespace binding failed')
    work = Path(tempfile.mkdtemp(prefix='survey-controller-proof-')).resolve()
    target, api = work / 'installation', LocalDockerAPI()
    report = {'scenario': scenario, 'status': 'failed', 'fixtureCleanup': 'unknown',
        'evidenceRoot': str(work), 'syntheticBundleOnly': True, 'hashHelperCovered': False,
        'businessServicesCovered': False, 'restoreEligible': False, 'releaseEligible': False}
    known = {'containers': set(), 'networks': set(), 'volumes': set()}
    module = load_module()
    class RealRunner:
        def run(self, command, **kwargs):
            kwargs.setdefault('timeout', 45)
            kwargs.setdefault('capture_output', True)
            kwargs.setdefault('text', True)
            return subprocess.run(command, **kwargs)
    manager = module.SurveyManager(target, runner=RealRunner())  # No runtime/cohort stub.
    compose = {'name': namespace, 'services': {'platform': {'image': image, 'user': '0:0',
        'entrypoint': ['/bin/sh', '-ec'], 'command': ["printf 'retained-proof\\n' > /state/proof.txt; exec sleep 90"],
        'network_mode': 'none', 'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'init': True, 'pids_limit': 32, 'mem_limit': '64m', 'cpus': 1,
        'volumes': [{'type': 'volume', 'source': 'state', 'target': '/state'}]}}, 'volumes': {'state': {}}}
    images = json.loads((ROOT / 'tests/fixtures/release.json').read_text())['images']
    images['ADMIN_IMAGE'] = image
    architecture = 'arm64' if os.uname().machine in ('arm64', 'aarch64') else 'amd64'
    manifest_path, bundle = write_test_release(work, payload_overrides={'compose.yml': json.dumps(compose).encode()},
        images=images, _fixture_architecture=architecture, _fixture_owned_support=True)
    manifest = module.load_manifest(manifest_path)
    stage = 'prepare'
    def remember():
        record = ownership.PrivateStore(target).load()
        if record:
            if record.get('claimId'): known['containers'].add(record['claimId'])
            for kind in known: known[kind].update(record.get('resources', {}).get(kind, {}))
        return record
    def data(cid):
        deadline = time.monotonic() + 15
        while True:
            connection = api._connect()
            try:
                connection.request('GET', api.VERSION + '/containers/' + cid + '/archive?path=/state/proof.txt')
                response = connection.getresponse()
                if response.status == 200:
                    raw = response.read(65537)
                    if len(raw) > 65536: raise RuntimeError('fixture archive oversized')
                    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:*') as archive:
                        members = archive.getmembers()
                        if len(members) != 1 or not members[0].isfile() or members[0].size > 128:
                            raise RuntimeError('fixture archive shape differs')
                        return archive.extractfile(members[0]).read() == b'retained-proof\n'
                if response.status != 404 or time.monotonic() >= deadline:
                    raise RuntimeError('fixture data unavailable')
            finally:
                connection.close()
            time.sleep(.1)
    def reader_container():
        value = api._json('POST', '/containers/create?name=' + namespace + '-reader-1', {
            'Image': image, 'Entrypoint': ['/bin/true'], 'Cmd': [], 'Labels': {'io.survey.fixture': namespace},
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                'Mounts': [{'Type': 'volume', 'Source': namespace + '_state', 'Target': '/state', 'ReadOnly': True}]}})
        cid = value['Id']
        if not re.fullmatch('[0-9a-f]{64}', cid): raise RuntimeError('reader create identity unknown')
        known['containers'].add(cid)
        return cid
    try:
        with manager._mutation_lock(create_target=True, operation='install'):
            manager._begin_operation()
            manager._stage_release(manifest, architecture,
                'fixture.invalid', 'fixture', 'fixture@example.invalid', bundle)
            runtime = RuntimeController.prepare(manager, manifest.version)
            remember()
            manager._write_state(status='installing', version=manifest.version, databaseSchema=manifest.database_schema,
                publicHost='fixture.invalid', adminUser='fixture', adminEmail='fixture@example.invalid')
            if scenario == 'settled-failure':
                def readiness_failed(*args, **kwargs):
                    record = remember()
                    cid = next(iter(record['resources']['containers']), None)
                    # During mutation-pending the new snapshot is intentionally
                    # not recorded yet. Resolve only the exact newly labelled
                    # fixture container; this is an observation, not adoption.
                    if cid is None:
                        rows = runtime.driver._rows('containers')
                        if len(rows) != 1: raise RuntimeError('fixture start inventory differs')
                        cid = rows[0]['id']
                    if not data(cid): raise RuntimeError('synthetic data was not written')
                    raise module.RuntimeHealthError('injected post-success readiness failure')
                manager._wait_healthy = readiness_failed
                try:
                    manager._owned_start_install(runtime, manifest.version, 'fixture.invalid')
                except module.RuntimeHealthError:
                    record = remember()
                    if record['phase'] != 'retained-data' or record['resources']['containers'] or record['resources']['networks']:
                        raise RuntimeError('failed install did not complete exact runtime removal')
                    report['knownFailureCleanupVerified'] = True
                    # Let the real mutation context keep its pending marker.
                    raise
                raise RuntimeError('injected failed install unexpectedly succeeded')
            runtime.install(lambda: manager._run(manager._compose(manifest.version) + ['up', '-d'], 'fixture startup failed'))
            record = remember()
            if len(record['resources']['containers']) != 1 or not data(next(iter(record['resources']['containers']))):
                raise RuntimeError('fixture startup/data verification failed')
            manager._atomic_write(manager.current_path, manifest.version + '\n')
            manager._write_state(status='healthy')
        stage = 'uninstall'
        if scenario == 'noop-delete':
            import runtime_docker
            original = runtime_docker.LocalDockerAPI._json
            def noop(self, method, path, body=None, **kwargs):
                if method == 'DELETE' and path.startswith('/containers/'):
                    return None  # Fault injection only; daemon retains the object.
                return original(self, method, path, body, **kwargs)
            runtime_docker.LocalDockerAPI._json = noop
            try:
                try: manager.uninstall(False, None)
                except module.RuntimeHealthError:
                    record = remember()
                    report['noopRemovalRefused'] = (record['phase'] == 'cleanup-pending'
                        and manager._read_state()['status'] == 'recovery_required'
                        and json.loads((target / '.surveyctl/operation.intent').read_text())['state'] == 'pending')
                    report['noopRetainsData'] = data(next(iter(record['resources']['containers'])))
                else: raise RuntimeError('no-op delete was accepted')
            finally:
                runtime_docker.LocalDockerAPI._json = original
            report['status'] = 'passed' if report.get('noopRemovalRefused') is True and report.get('noopRetainsData') is True else 'failed'
        else:
            manager.uninstall(False, None)
            record = remember()
            report['ordinaryRemovalRetainsSameVolume'] = record['phase'] == 'retained-data' and len(record['resources']['volumes']) == 1
            stage = 'reader'
            reader = reader_container()
            report['retainedDataReadBack'] = data(reader)
            before = manager.state_path.read_bytes(), (target / '.surveyctl/operation.intent').read_bytes()
            try: manager.uninstall(True, 'PURGE ' + str(target))
            except module.RuntimeHealthError:
                report['unknownAttachmentRefusedBeforeIntent'] = before == (manager.state_path.read_bytes(), (target / '.surveyctl/operation.intent').read_bytes())
            else: raise RuntimeError('unknown reader attachment did not block purge')
            api._json('DELETE', '/containers/' + reader + '?force=false&v=false', accepted=(204,))
            stage = 'purge'
            manager.uninstall(True, 'PURGE ' + str(target))
            record = remember()
            report['purgeRemovedResourcesAndClaim'] = (record['phase'] == 'removed'
                and not any(record['resources'][kind] for kind in known)
                and api.container_absent(record['claimId']) and not (target / 'shared').exists())
            report['status'] = 'passed' if all(report.get(key) is True for key in (
                'ordinaryRemovalRetainsSameVolume', 'retainedDataReadBack', 'unknownAttachmentRefusedBeforeIntent', 'purgeRemovedResourcesAndClaim')) else 'failed'
    except module.RuntimeHealthError:
        if scenario == 'settled-failure' and report.get('knownFailureCleanupVerified') is True:
            report['failureMarkerPreserved'] = json.loads((target / '.surveyctl/operation.intent').read_text())['state'] == 'pending'
            report['knownFailureDataReadBack'] = data(reader_container())
            report['status'] = 'passed' if report['failureMarkerPreserved'] and report['knownFailureDataReadBack'] else 'failed'
        else: report.update(failure='controller-runtime-error', stage=stage)
    except Exception:
        report.update(failure='fixture-or-controller-evidence-unavailable', stage=stage)
    finally:
        cleanup = True
        try:
            remember()
        except Exception:
            cleanup = False  # Continue attempting every already captured resource.
        for cid in known['containers']:
            try:
                if api.container_absent(cid): continue
                value = api._json('GET', '/containers/' + cid + '/json')
                labels = value.get('Config', {}).get('Labels', {})
                if (not value.get('Name', '').startswith('/' + namespace + '-') or not (
                        labels.get('com.docker.compose.project') == namespace or labels.get('io.survey.fixture') == namespace
                        or labels.get('io.survey.role') == 'ownership-claim')):
                    raise RuntimeError('fixture identity changed')
                if value['State']['Running']: api._json('POST', '/containers/' + cid + '/stop?t=5', accepted=(204, 304))
                remove_and_confirm(api, 'containers', cid)
            except Exception: cleanup = False
        for kind in ('networks', 'volumes'):
            for identity in known[kind]:
                try:
                    connection = api._connect()
                    try:
                        path = '/' + kind + '/' + identity
                        connection.request('GET', api.VERSION + path)
                        response = connection.getresponse()
                        if response.status == 404: continue
                        if response.status != 200: raise RuntimeError('fixture presence unknown')
                        value = json.loads(response.read(65536))
                        if value.get('Labels', {}).get('com.docker.compose.project') != namespace:
                            raise RuntimeError('fixture ownership changed')
                    finally: connection.close()
                    remove_and_confirm(api, kind, identity)
                except Exception: cleanup = False
        if not namespace_empty(namespace): cleanup = False
        record = ownership.PrivateStore(target).load()
        if record and record.get('phase') in ('claim-pending', 'mutation-pending'): cleanup = False
        report['fixtureCleanup'] = 'verified' if cleanup else 'failed-or-unknown'
    return report


def main(image):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image) or image.startswith('-'):
        raise SystemExit('a local digest-pinned shell image is required')
    paths = [ROOT / name for name in sorted(load_module().OWNED_RUNTIME_FILES)]
    paths += [Path(__file__).resolve(), ROOT / 'tests/test_surveyctl.py']
    hashes = lambda: {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = {'scope': 'synthetic-controller-cleanup-only', 'sourceBefore': hashes()}
    report['cases'] = [run_case(image, value) for value in ('ordinary-purge', 'noop-delete', 'settled-failure')]
    report['sourceAfter'] = hashes()
    print(json.dumps(report, sort_keys=True))
    return 0 if report['sourceBefore'] == report['sourceAfter'] and all(
        value['status'] == 'passed' and value['fixtureCleanup'] == 'verified' for value in report['cases']) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    raise SystemExit(main(parser.parse_args().image))
