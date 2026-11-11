"""Explicit, isolated daemon checks; not controller/cohort/native acceptance.

Usage: python3 .../runtime_ownership_docker.py --image REPO@sha256:DIGEST
Uses an already present image, never pulls/starts a container. A random fixture
namespace replaces module constants in this process only. No production object
or shared fixed claim name is queried for deletion. Cleanup uses captured IDs
and a fresh fixture label check; failures are reported separately. Creates one
empty local volume and one private bridge; neither is attached to a container.
"""
import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor


class Runner:
    def run(self, command, **kwargs):
        return subprocess.run(command, **kwargs)


def cleanup_fixture(docker, owned, namespace):
    if not re.fullmatch(r'survey-owner-fixture-[0-9a-f]{32}', namespace):
        return False
    cleanup_ok = True
    for kind, identity in reversed(owned):
        try:
            resource = 'container' if kind == 'claim' else kind
            query = ([resource, 'ls', '--all', '--no-trunc', '--quiet', '--filter', 'id=' + identity]
                if resource == 'container' else [resource, 'ls', '--quiet',
                    *(['--no-trunc'] if resource == 'network' else []), '--filter',
                    ('name=' if resource == 'volume' else 'id=') + identity])
            present = docker(query)
            if not present:
                continue
            if present != identity:
                raise RuntimeError('ambiguous fixture identity')
            template = '{{json (index ' + ('.Config.Labels' if resource == 'container' else '.Labels') + ' "' + (
                'io.survey.role' if kind == 'claim' else 'io.survey.fixture') + '")}}'
            expected = 'ownership-claim' if kind == 'claim' else namespace
            if json.loads(docker([resource, 'inspect', '--format', template, identity])) != expected:
                raise RuntimeError('fixture ownership changed')
            # No force: an unexpectedly running fixture is retained.
            docker([resource, 'rm', identity])
            if docker(query):
                raise RuntimeError('fixture still present')
        except Exception:
            cleanup_ok = False
    # A successful create reply can be lost before `owned` is updated. Do not
    # adopt or delete such objects. Independently query all kinds even if one
    # fails; any residual/malformed/unknown observation withholds verification.
    for resource in ('container', 'network', 'volume'):
        try:
            query = [resource, 'ls']
            if resource == 'container':
                query.append('--all')
            name = '.Names' if resource == 'container' else '.Name'
            query.extend(['--filter', 'name=' + namespace, '--format', '{"name":{{json ' + name + '}}}'])
            raw = docker(query)
            if not isinstance(raw, str) or len(raw.encode('utf-8')) > 65536:
                raise RuntimeError('fixture inventory is missing or oversized')
            rows = raw.splitlines()
            if len(rows) > 128:
                raise RuntimeError('fixture inventory is not bounded')
            for row in rows:
                item = json.loads(row)
                if not isinstance(item, dict) or set(item) != {'name'} or not isinstance(item['name'], str):
                    raise RuntimeError('fixture inventory is malformed')
                if item['name'].startswith((namespace + '-', namespace + '_')):
                    cleanup_ok = False
        except Exception:
            cleanup_ok = False
    return cleanup_ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--fail-after-create', action='store_true',
        help='inject a fixture failure and independently test harness cleanup (must exit 1)')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('fixture_ownership',
        Path(__file__).resolve().parents[2] / 'runtime_ownership.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Deliberately never exercise the shared survey-production namespace on a
    # developer daemon. A candidate must separately test its real namespace.
    namespace = 'survey-owner-fixture-' + uuid.uuid4().hex
    module.PROJECT = namespace
    module.CLAIM_NAME = namespace + '-ownership-v1'
    label = 'io.survey.fixture=' + namespace
    environment = dict(os.environ, DOCKER_HOST=module.DockerOwnershipDriver.HOST)
    if os.environ.get('DOCKER_CONTEXT'):
        raise RuntimeError('explicit Docker context is not supported')
    owned = []
    def docker(arguments):
        return subprocess.run(['docker', '--host', module.DockerOwnershipDriver.HOST, *arguments],
            env=environment, check=True, text=True, capture_output=True, timeout=30).stdout.strip()
    def own(kind, identity):
        valid = (identity == namespace + '_data' if kind == 'volume' else bool(module.HEX64.fullmatch(identity)))
        if not valid:
            raise RuntimeError('fixture create result is unknown')
        owned.append((kind, identity))
        return identity
    summary = {'schemaVersion': 1, 'scope': 'isolated-daemon-protocol-only',
               'mutualExclusion': False, 'unknownCohortRefused': False,
               'retainedVolumeVerified': False, 'resourceRemovalVerified': False, 'cleanup': 'not-attempted'}
    stage = 'image-inspect'
    success = False
    try:
        # A caller-selected local image is fixture material, not signed release
        # provenance. Driver itself enforces digest pinning and no image VOLUME.
        image_id = json.loads(docker(['image', 'inspect', '--format', '{{json .Id}}', args.image]))
        with tempfile.TemporaryDirectory(prefix='survey-owner-fixture-') as root:
            # macOS tempfile commonly uses /var -> /private/var. Resolve only
            # our newly created fixture root; production paths remain no-follow.
            targets = [Path(root).resolve() / name for name in ('one', 'two')]
            for target in targets:
                target.mkdir(mode=0o700)
                (target / '.surveyctl').mkdir(mode=0o700)
            cohort = ['quiescent']
            contract = {'services': {'fixture': {'imageIds': [image_id],
                'workingDirs': [str(target) for target in targets], 'mounts': []}},
                'networks': {namespace + '_internal': {'logical': 'internal', 'driver': 'bridge', 'internal': True}},
                'volumes': {namespace + '_data': {'logical': 'data', 'driver': 'local'}}}
            drivers = [module.DockerOwnershipDriver(Runner(), contract, lambda: cohort[0]) for _ in targets]
            stores = [module.PrivateStore(target) for target in targets]
            protocols = [module.OwnershipProtocol(target, driver, store, args.image)
                for target, driver, store in zip(targets, drivers, stores)]
            barrier = threading.Barrier(2)
            errors = []
            def acquire(index):
                barrier.wait(timeout=5)
                try:
                    protocols[index].acquire()
                    return index
                except module.OwnershipError as exc:
                    chain = []
                    while exc is not None and len(chain) < 8:
                        entry = {'class': type(exc).__name__}
                        if isinstance(exc, OSError):
                            entry['errno'] = exc.errno
                        traceback = exc.__traceback__
                        while traceback and traceback.tb_next:
                            traceback = traceback.tb_next
                        if traceback:
                            entry['line'] = traceback.tb_lineno
                            entry['file'] = Path(traceback.tb_frame.f_code.co_filename).name
                        chain.append(entry)
                        exc = exc.__cause__
                    errors.append(chain)
                    return None
            stage = 'concurrent-acquire'
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(acquire, range(2)))
            winners = [index for index in results if index is not None]
            # Discover only the exact randomly named claim for fixture teardown,
            # including a create that succeeded before a later check failed.
            claim = docker(['container', 'ls', '--all', '--no-trunc', '--quiet',
                '--filter', 'name=^/' + module.CLAIM_NAME + '$'])
            if claim:
                own('claim', claim)
            if len(winners) != 1:
                summary['acquireFailures'] = errors
                raise RuntimeError('daemon claim did not produce exactly one winner')
            summary['mutualExclusion'] = True
            index = winners[0]
            protocol, driver, store = protocols[index], drivers[index], stores[index]
            owner = store.load()['installationId']
            def create():
                own('network', docker(['network', 'create', '--internal', '--driver', 'bridge',
                    '--label', label, '--label', module.DockerOwnershipDriver.LABEL + '=' + namespace,
                    '--label', 'com.docker.compose.network=internal',
                    '--label', module.DockerOwnershipDriver.OWNER + '=' + owner, namespace + '_internal']))
                own('volume', docker(['volume', 'create', '--driver', 'local', '--label', label,
                    '--label', module.DockerOwnershipDriver.LABEL + '=' + namespace,
                    '--label', 'com.docker.compose.volume=data',
                    '--label', module.DockerOwnershipDriver.OWNER + '=' + owner, namespace + '_data']))
                own('container', docker(['container', 'create', '--pull=never', '--network', 'none',
                    '--name', namespace + '-fixture-1', '--label', label,
                    '--label', module.DockerOwnershipDriver.LABEL + '=' + namespace,
                    '--label', 'com.docker.compose.service=fixture',
                    '--label', 'com.docker.compose.project.working_dir=' + str(targets[index]),
                    '--label', module.DockerOwnershipDriver.OWNER + '=' + owner,
                    '--entrypoint', '/bin/true', args.image]))
                if args.fail_after_create:
                    raise RuntimeError('fixture mutation failure')
            stage = 'resource-create-and-record'
            protocol.mutate('install', create)
            before = store.load()
            cohort[0] = 'unknown'
            try:
                protocol.cleanup(True)
                raise RuntimeError('unknown cohort allowed deletion')
            except module.OwnershipError:
                if store.load() != before:
                    raise RuntimeError('unknown cohort changed ownership state')
            cohort[0] = 'quiescent'
            summary['unknownCohortRefused'] = True
            stage = 'ordinary-removal-retains-volume-and-claim'
            protocol.cleanup(False)
            retained = store.load()
            if (retained['phase'] != 'retained-data' or retained['resources']['containers']
                    or retained['resources']['networks']
                    or retained['resources']['volumes'] != before['resources']['volumes']
                    or driver.inspect_claim(before['claimId']) is None):
                raise RuntimeError('ordinary removal lost data ownership')
            summary['retainedVolumeVerified'] = True
            stage = 'verified-resource-removal'
            protocol.cleanup(True)
            if store.load()['phase'] != 'removed' or driver.inspect_claim(before['claimId']) is not None:
                raise RuntimeError('deletion postcondition was not met')
            summary['resourceRemovalVerified'] = True
            success = True
    except Exception:
        # Never print Docker stderr/inspect bodies or arbitrary exception text.
        summary['failure'] = 'protocol-or-daemon-check-failed'
        summary['stage'] = stage
    finally:
        cleanup_ok = cleanup_fixture(docker, owned, namespace)
        summary['cleanup'] = 'verified' if cleanup_ok else 'failed-or-unknown'
    print(json.dumps(summary, sort_keys=True))
    return 0 if success and cleanup_ok else 1


if __name__ == '__main__':
    sys.exit(main())
