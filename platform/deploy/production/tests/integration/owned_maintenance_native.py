"""Linux-only real DB/crypto/controller components on synthetic isolated stacks.

No business application, public TLS, real identity, restore or release proof.
Only random namespaces and synthetic credentials/data are used. The failed
doctor case retains its pending marker; independent fixture teardown is not
product recovery authorization. Never upload this fixture's private directory.
"""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import tarfile
import uuid
from urllib.parse import quote
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests'), str(Path(__file__).parent)]
import runtime_ownership as ownership
from backup import VOLUME_SOURCES
from owned_children import read_process_identity
from owned_docker_exec import LocalDockerAPI, DockerRequestTimeout
from native_cleanup_proof import prove_uninstall
from runtime_controller_native import namespace_empty, remove_and_confirm
from test_surveyctl import FakeHostProbe, load_module, write_test_release

REQUIRED = {
    'backup-purge': ('realHashReceipt', 'backupVerified', 'archiveContentsVerified', 'restoreRefusedWithoutEffects',
                     'ownedUpgradeCompleted', 'persistentIDsKeptAcrossUpgrade', 'oldContainerIDsAbsentAfterUpgrade',
                     'newContainerIDsBound', 'retainedNineVolumes', 'ordinaryUninstallProofVerified',
                     'productPurgeVerified', 'allHelpersEnded'),
    'doctor-refusal': ('realHashReceipt', 'doctorBlockedWithoutLeak', 'pendingPreserved',
                      'unsafeUninstallRefused', 'allHelpersEnded'),
}
PENDING_FIXTURES = []  # Keep unknown leases/managers alive until this process exits.


def doctor_refusal_report(value):
    return (isinstance(value, dict) and type(value.get('schemaVersion')) is int and value == {
        'schemaVersion': 1, 'status': 'blocked', 'checks': [{
            'id': 'doctor-runtime', 'status': 'blocked',
            'summary': 'controlled diagnostic operation failed; preserve its receipts and marker'}]})


def passed(report):
    if (not isinstance(report, dict) or type(report.get('schemaVersion')) is not int or report['schemaVersion'] != 1
            or report.get('scope') != 'recorded-maintenance-real-database-components'
            or not isinstance(report.get('sourceBefore'), dict) or not report['sourceBefore']
            or report.get('sourceAfter') != report['sourceBefore']
            or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in report['sourceBefore'].values())
            or not isinstance(report.get('cases'), list) or len(report['cases']) != len(REQUIRED)):
        return False
    found = {}
    for case in report['cases']:
        if not isinstance(case, dict) or case.get('scenario') not in REQUIRED or case['scenario'] in found: return False
        found[case['scenario']] = case
        if case['scenario'] == 'backup-purge' and (type(case.get('retainedNetworkCount')) is not int
                                                  or case['retainedNetworkCount'] != 1):
            return False
        if (case.get('status') != 'passed' or case.get('fixtureCleanup') != 'verified'
                or case.get('syntheticManifestOnly') is not True or case.get('businessServicesCovered') is not False
                or case.get('defaultInstallCovered') is not False or case.get('restorationEligible') is not False
                or case.get('hostAndTlsAreSynthetic') is not True or case.get('upgradeBusinessDataVerified') is not False
                or case.get('releaseEligible') is not False or any(case.get(k) is not True for k in REQUIRED[case['scenario']])):
            return False
    return set(found) == set(REQUIRED)


def command(args):
    if args[0] == 'docker' and args[:3] != ['docker', '--host', 'unix:///var/run/docker.sock']:
        args = ['docker', '--host', 'unix:///var/run/docker.sock', *args[1:]]
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)


def image_metadata(api, reference):
    raw = api._json('GET', '/images/' + quote(reference, safe='') + '/json')
    label = ((raw.get('Config') or {}).get('Labels') or {}).get('org.opencontainers.image.version')
    version = label if isinstance(label, str) and re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]{1,32})?', label) else None
    return {'id': raw['Id'], 'architecture': raw['Architecture'], 'os': raw['Os'], 'version': version}


def helper_receipts_ended(manager, api):
    """Independent fixture end check only; does not authorize a product action."""
    try:
        if any(op.uncertain or op.held for op in getattr(manager, '_owned_contexts', [])): return False
        for path in manager.control_dir.iterdir():
            if not path.name.lstrip('.').startswith('runtime-'): continue
            if path.name in ('runtime-owner.json', 'runtime-contract.json', 'runtime-owner.compose.json'): continue
            if path.name.startswith('.'): return False
            value = ownership.PrivateStore(manager.target, name=path.name).load()
            if value.get('kind') == 'controlled-helper-children':
                for row in value['children']:
                    if row['phase'] == 'not-started' and row['pid'] is None and row['start'] is None: continue
                    if row['phase'] not in ('completed', 'failed') or type(row.get('exitCode')) is not int: return False
                    observed = read_process_identity(row['pid'])
                    if observed is not None and observed['start'] == row['start'] and observed['state'] not in ('Z', 'X'): return False
            elif value.get('kind') in ('controlled-docker-exec', 'controlled-container-helper'):
                if value.get('phase') not in ('completed', 'failed') or type(value.get('exitCode')) is not int: return False
                if api.container_absent(value['containerId']): continue
                if value['kind'] == 'controlled-container-helper': return False
                ended = api.inspect(value['execId'])
                if (ended.get('ID') != value['execId'] or ended.get('ContainerID') != value['containerId']
                        or ended.get('Running') is not False or type(ended.get('ExitCode')) is not int
                        or ended['ExitCode'] != value['exitCode']): return False
            else: return False
        return True
    except Exception:
        return False


def fixture_cleanup(manager, namespace, api):
    """Passed-case fixture teardown only; never exceptional finalization."""
    try:
        record = ownership.PrivateStore(manager.target).load()
        if record is None: return namespace_empty(namespace)
        if record['phase'] not in ('ready', 'retained-data', 'settled-failure', 'removed'): return False
        if not helper_receipts_ended(manager, api): return False
        if record['phase'] == 'removed': return namespace_empty(namespace)
        valid = True
        for cid in record['resources']['containers']:
            try:
                if api.container_absent(cid): continue
                value = api._json('GET', '/containers/' + cid + '/json')
                labels = value.get('Config', {}).get('Labels', {})
                if (value.get('Id') != cid or labels.get('com.docker.compose.project') != namespace
                        or labels.get('io.survey.installation') != record['installationId']): raise ValueError('fixture identity')
                if value['State']['Running']: api._json('POST', '/containers/' + cid + '/stop?t=20', accepted=(204, 304))
                remove_and_confirm(api, 'containers', cid)
            except Exception: valid = False
        for kind in ('networks', 'volumes'):
            for identity in record['resources'][kind]:
                try:
                    value = api._json('GET', '/' + kind + '/' + identity)
                    if value.get('Labels', {}).get('io.survey.installation') != record['installationId']:
                        raise ValueError('fixture identity')
                    remove_and_confirm(api, kind, identity)
                except Exception: valid = False
        if not valid: return False
        claim = record['claimId']
        ids = command(['docker', '--host', 'unix:///var/run/docker.sock', 'container', 'ls', '--all',
                       '--quiet', '--no-trunc', '--filter', 'name=' + namespace]).stdout.splitlines()
        if set(ids) != {claim}: return False  # Unknown objects keep the reservation.
        for kind in ('network', 'volume'):
            if command(['docker', '--host', 'unix:///var/run/docker.sock', kind, 'ls', '--quiet',
                        '--filter', 'name=' + namespace]).stdout.strip(): return False
        value = api._json('GET', '/containers/' + claim + '/json')
        if (value['State']['Running'] is not False or value['Config']['Labels'].get('io.survey.role') != 'ownership-claim'
                or value['Config']['Labels'].get('io.survey.installation') != record['installationId']): return False
        remove_and_confirm(api, 'containers', claim)
        return namespace_empty(namespace)
    except Exception:
        return False


def topology(namespace, target, images, version='0.2.0'):
    volumes = {name: {} for name in ('caddy-data', 'caddy-config', 'platform-assets', 'platform-exports',
        'publish-gateway-state', 'engine-upload', 'engine-runtime', 'platform-db-data', 'engine-db-data')}
    services = {}
    for name in ('edge', 'admin-web', 'platform', 'publish-gateway', 'engine', 'engine-relay'):
        mounts = []
        paths = []
        for archive, (service, destination) in VOLUME_SOURCES.items():
            if service != name: continue
            mounts.append({'type': 'volume', 'source': archive.removesuffix('.tar'), 'target': destination})
            paths.append(destination)
        # Model ordinary graceful service termination; the separate stop-budget
        # fixture covers a real PID1 that ignores TERM for the full 20s grace.
        sleeper = 'import signal,sys,time; signal.signal(signal.SIGTERM,lambda *a:sys.exit(0)); time.sleep(600)'
        script = '; '.join(["printf '%s\\n' 'fixture-data' > " + shlex.quote(p + '/proof.txt') for p in paths]
                           + ['exec python -c ' + shlex.quote(sleeper)])
        services[name] = {'image': images['ADMIN_IMAGE'], 'entrypoint': ['/bin/sh', '-ec'], 'command': [script],
            'network_mode': 'none', 'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'volumes': mounts, 'pids_limit': 32, 'mem_limit': '64m',
            'healthcheck': {'test': ['CMD', '/bin/true'], 'interval': '5s', 'timeout': '3s', 'retries': 10}}
    services['engine-init'] = dict(services['admin-web'], entrypoint=['/bin/true'], command=[], healthcheck={'test': ['NONE']})
    secrets = {name: {'file': str(target / 'shared/runtime-secrets' / name)}
               for name in ('platform_db_superuser_password', 'engine_db_root_password')}
    release = target / 'releases' / version
    services['platform-db'] = {'image': images['POSTGRES_IMAGE'], 'network_mode': 'none',
        'security_opt': ['no-new-privileges:true'], 'mem_limit': '512m',
        'environment': {'POSTGRES_USER': 'postgres', 'POSTGRES_DB': 'platform',
                        'POSTGRES_PASSWORD_FILE': '/run/secrets/platform_db_superuser_password'},
        'secrets': ['platform_db_superuser_password'], 'volumes': [
            {'type': 'volume', 'source': 'platform-db-data', 'target': '/var/lib/postgresql/data'},
            {'type': 'bind', 'source': str(release / 'init/fixture-pg.sql'), 'target': '/docker-entrypoint-initdb.d/fixture.sql', 'read_only': True}],
        'healthcheck': {'test': ['CMD-SHELL', "pg_isready -h 127.0.0.1 -U postgres -d platform && psql -U postgres -d platform -Atqc 'SELECT 1' | grep -qx 1"],
                        'interval': '2s', 'timeout': '3s', 'retries': 40}}
    services['engine-db'] = {'image': images['MARIADB_IMAGE'], 'network_mode': 'none',
        'security_opt': ['no-new-privileges:true'], 'mem_limit': '512m',
        'environment': {'MARIADB_DATABASE': 'limesurvey', 'MARIADB_ROOT_PASSWORD_FILE': '/run/secrets/engine_db_root_password'},
        'secrets': ['engine_db_root_password'], 'volumes': [
            {'type': 'volume', 'source': 'engine-db-data', 'target': '/var/lib/mysql'},
            {'type': 'bind', 'source': str(release / 'init/fixture-mdb.sql'), 'target': '/docker-entrypoint-initdb.d/fixture.sql', 'read_only': True}],
        'healthcheck': {'test': ['CMD', 'healthcheck.sh', '--connect', '--innodb_initialized'],
                        'interval': '2s', 'timeout': '3s', 'retries': 40}}
    # One idle, non-listening fixture process exercises an actual retained
    # bridge. No published port or production/network-policy change is involved.
    services['admin-web'].pop('network_mode')
    services['admin-web']['networks'] = ['internal']
    return {'name': namespace, 'services': services, 'volumes': volumes, 'secrets': secrets,
            'networks': {'internal': {'name': namespace + '_internal', 'internal': True}}}


def write_component_release(work, images, payload, architecture, *, version='0.2.0'):
    # Synthetic parser fixture, not a signed capability assertion. Keep it bound
    # to this fixture's image so the real safety check is exercised unchanged.
    return write_test_release(work, version=version, images=images, payload_overrides=payload,
                              securityCapabilities={'platform-admin-session-v1': images['PLATFORM_IMAGE']},
                              _fixture_owned_support=True, _fixture_architecture=architecture)


def finalize_fixture(manager, namespace, api, result):
    if result.get('status') != 'passed':
        # A failed proof can leave pending intent + retained-data. Recognizable
        # resources/claim do not authorize destructive exceptional teardown.
        result['fixtureCleanup'] = 'preserved-failed-or-unknown'
    else:
        try:
            verified = fixture_cleanup(manager, namespace, api)
        except Exception:
            verified = False
        result['fixtureCleanup'] = 'verified' if verified is True else 'unknown-or-refused'
    if result['fixtureCleanup'] != 'verified': PENDING_FIXTURES.append(manager)
    result['privateStateRetained'] = True


def run_case(images, scenario):
    namespace = 'survey-control-fixture-' + uuid.uuid4().hex
    ownership.PROJECT, ownership.CLAIM_NAME = namespace, namespace + '-ownership-v1'
    work = Path(tempfile.mkdtemp(prefix='survey-maintenance-component-')).resolve()
    work.chmod(0o700)
    target, api = work / 'installation', LocalDockerAPI()
    module = load_module()
    architecture = 'arm64' if os.uname().machine == 'aarch64' else 'amd64'
    # The upgrade host/DNS/TLS preflight is modeled: this component stack has
    # no edge listener or signed candidate. Runtime/process guards remain real.
    manager = module.SurveyManager(target, host_probe=FakeHostProbe(architecture=architecture))
    result = {'scenario': scenario, 'status': 'failed', 'fixtureCleanup': 'unknown', 'syntheticManifestOnly': True,
              'businessServicesCovered': False, 'defaultInstallCovered': False,
              'hostAndTlsAreSynthetic': True, 'upgradeBusinessDataVerified': False,
              'restorationEligible': False, 'releaseEligible': False, 'fixtureCleanupScope': 'docker-objects-only'}
    stage = 'preflight'
    try:
        if not namespace_empty(namespace): raise ValueError('fixture collision')
        compose = topology(namespace, target, images)
        payload = {'compose.yml': json.dumps(compose).encode(),
            'init/fixture-pg.sql': b"CREATE TABLE owned_fixture(id int primary key, answer text); INSERT INTO owned_fixture VALUES(1,'fixture-answer'); CREATE TABLE flyway_schema_history(installed_rank int,version text,success boolean); INSERT INTO flyway_schema_history VALUES(1,'920',true);\n",
            'init/fixture-mdb.sql': b"USE limesurvey; CREATE TABLE owned_fixture(id int primary key, answer text); INSERT INTO owned_fixture VALUES(1,'fixture-answer');\n"}
        manifest_path, bundle = write_component_release(work, images, payload,
            'arm64' if os.uname().machine == 'aarch64' else 'amd64')
        manifest = module.load_manifest(manifest_path)
        stage = 'initialize'
        with manager._mutation_lock(create_target=True, operation='install'):
            manager._begin_operation()
            manager._stage_release(manifest, os.uname().machine.replace('x86_64', 'amd64').replace('aarch64', 'arm64'),
                                   'fixture.invalid', 'fixture', 'fixture@example.invalid', bundle)
            manager._activate_personnel_floor(manifest.version)  # Synthetic parser fixture only, never signed evidence.
            runtime = manager._runtime(manifest.version, create=True)
            runtime.ensure_admin_hash()
            launch = [ownership.PrivateStore(target, name=p.name).load() for p in manager.control_dir.glob('runtime-launch-*.json')]
            result['realHashReceipt'] = (len(launch) == 1 and launch[0]['phase'] == 'completed'
                                        and api.container_absent(launch[0]['containerId']))
            manager._write_state(status='installing', version=manifest.version, databaseSchema=manifest.database_schema,
                                 publicHost='fixture.invalid', adminUser='fixture', adminEmail='fixture@example.invalid')
            def start():
                manager._run(manager._compose(manifest.version) + ['up', '-d'], 'fixture start failed')
                manager._wait_healthy(manager._compose(manifest.version), 'fixture DB readiness failed')
            runtime.install(start)
            manager._atomic_write(manager.current_path, manifest.version + '\n')
            manager._write_state(status='healthy')
        if scenario == 'backup-purge':
            stage = 'backup'
            backup = manager.backup('native-component')
            stage = 'verify'
            with manager._mutation_lock(operation='verify'):
                metadata = manager._verify_backup(backup, manifest.version, manifest.database_schema)
            result['backupVerified'] = set(metadata['files']) == {'postgres.dump.enc', 'mariadb.sql.enc',
                                                                *(name + '.enc' for name in VOLUME_SOURCES)}
            stage = 'verify-archive-content'
            with manager._mutation_lock(operation='verify-data'):
                from owned_maintenance import OwnedMaintenance
                from owned_backup_verify import ControlledVerifier
                operation = OwnedMaintenance(manager._runtime(manifest.version))
                manager._begin_operation()
                def verify_contents():
                    with operation.cipher_session(backup) as (workspace, cipher):
                        _, decrypted = ControlledVerifier(target, cipher).verify_backup(backup, workspace)
                        for name in VOLUME_SOURCES:
                            with decrypted[name].open('rb') as stream, tarfile.open(fileobj=stream, mode='r:*') as archive:
                                files = [member for member in archive if member.isfile()]
                                if (len(files) != 1 or files[0].name not in ('proof.txt', './proof.txt')
                                        or archive.extractfile(files[0]).read() != b'fixture-data\n'):
                                    raise ValueError('synthetic archive root content differs')
                    return True
                result['archiveContentsVerified'] = operation._perform('doctor', verify_contents)
            stage = 'restore-denial'
            before = [(p, p.read_bytes()) for p in (manager.state_path, manager.control_dir / 'operation.intent',
                                                   manager.control_dir / 'runtime-owner.json')]
            try: manager.restore('native-component')
            except module.InputError:
                result['restoreRefusedWithoutEffects'] = all(p.read_bytes() == data for p, data in before)
            stage = 'owned-upgrade'
            old = ownership.PrivateStore(target).load()
            candidate_version = '0.3.21-rc.1'  # Synthetic component source, not a release candidate/tag.
            candidate_payload = dict(payload, **{'compose.yml': json.dumps(topology(namespace, target, images, candidate_version)).encode()})
            candidate_path, _ = write_component_release(work, images, candidate_payload, architecture, version=candidate_version)
            manager.upgrade(candidate_path)
            upgraded = ownership.PrivateStore(target).load()
            result['ownedUpgradeCompleted'] = (manager._read_state(require_current=True)['version'] == candidate_version
                                                and upgraded['phase'] == 'ready')
            result['retainedNetworkCount'] = len(old['resources']['networks'])
            result['persistentIDsKeptAcrossUpgrade'] = (bool(old['resources']['networks'])
                and old['claimId'] == upgraded['claimId']
                and all(old['resources'][kind] == upgraded['resources'][kind] for kind in ('networks', 'volumes')))
            result['oldContainerIDsAbsentAfterUpgrade'] = all(api.container_absent(cid) for cid in old['resources']['containers'])
            result['newContainerIDsBound'] = (len(upgraded['resources']['containers']) == len(old['resources']['containers'])
                and not set(upgraded['resources']['containers']).intersection(old['resources']['containers']))
            stage = 'uninstall'
            candidate = manager._release_manifest(candidate_version)
            proof = prove_uninstall(manager, dict(schemaVersion=1, version=candidate_version,
                commit=candidate.raw['commit'], manifestSha256=candidate.sha256,
                bundleSha256=candidate.raw['assets']['bundles'][architecture]['sha256'],
                runId='1', attempt='1', ubuntu='24.04', architecture=architecture))
            result['ordinaryUninstallProofVerified'] = (proof['ordinaryUninstallVerified'] is True
                and proof['retainedVolumeCount'] == 9 and proof['claimRetained'] is True
                and proof['cohortQuiescent'] is True and proof['hostCleanupVerified'] is False
                and proof['candidateQualified'] is False)
            record = ownership.PrivateStore(target).load()
            result['retainedNineVolumes'] = (record['phase'] == 'retained-data' and len(record['resources']['volumes']) == 9
                                            and not record['resources']['containers'])
            stage = 'purge'
            manager.uninstall(True, 'PURGE ' + str(target))
            record = ownership.PrivateStore(target).load()
            result['productPurgeVerified'] = (record['phase'] == 'removed' and not any(record['resources'].values())
                and api.container_absent(record['claimId']) and not (target / 'shared').exists() and not (target / 'backups').exists())
        else:
            import owned_maintenance
            stage = 'doctor-denial'
            output = io.StringIO()
            # No external network/TLS target is contacted. This is deliberately
            # the synthetic refusal path, never a positive business doctor.
            with patch.object(owned_maintenance.OwnedDoctor, '_tls_probe', return_value=False), contextlib.redirect_stdout(output):
                try: manager.doctor()
                except module.RuntimeHealthError:
                    report = json.loads(output.getvalue())
                    result['doctorBlockedWithoutLeak'] = doctor_refusal_report(report)
            result['pendingPreserved'] = json.loads((manager.control_dir / 'operation.intent').read_text())['state'] == 'pending'
            before = [(p, p.read_bytes()) for p in (manager.state_path, manager.control_dir / 'operation.intent',
                                                   manager.control_dir / 'runtime-owner.json')]
            try: manager.uninstall(False, None)
            except module.InputError:
                result['unsafeUninstallRefused'] = all(p.read_bytes() == data for p, data in before)
        result['allHelpersEnded'] = helper_receipts_ended(manager, api)
        if all(result.get(k) is True for k in REQUIRED[scenario]): result['status'] = 'passed'
    except Exception as exc:
        result.update(failureStage=stage, failureClass=type(exc).__name__)
        chain, locations, operations = [], [], []
        allowed = {Path(__file__).name, 'runtime_controller_native.py', *module.OWNED_RUNTIME_FILES}
        while exc is not None and len(chain) < 6:
            if isinstance(exc, DockerRequestTimeout) and exc.operation in ('container-stop', 'docker-json'):
                operations.append(exc.operation)
            chain.append(type(exc).__name__)
            trace, frames = exc.__traceback__, []
            while trace:
                filename = Path(trace.tb_frame.f_code.co_filename).name
                if filename in allowed: frames.append({'file': filename, 'line': trace.tb_lineno})
                trace = trace.tb_next
            locations.append(frames[-1] if frames else None)
            exc = exc.__cause__
        result['failureClasses'] = chain  # No messages, logs, SQL, HTTP bodies or credentials.
        result['failureLocations'] = locations
        result['failureOperations'] = operations  # Fixed enums only; never paths or request bodies.
        try:
            codes, phases = [], []
            for path in manager.control_dir.glob('runtime-*.json'):
                if not re.fullmatch(r'runtime-(?:child|exec|launch)-[0-9a-f]{32}\.json', path.name): continue
                receipt = ownership.PrivateStore(target, name=path.name).load()
                rows = receipt.get('children', []) if receipt.get('kind') == 'controlled-helper-children' else [receipt]
                for row in rows:
                    code, phase = row.get('exitCode'), row.get('phase')
                    if type(code) is int and -64 <= code <= 255 and code != 0: codes.append(code)
                    if phase in ('pending', 'create-pending', 'created', 'released'): phases.append(phase)
            result['knownFailureExitCodes'] = sorted(set(codes))[:16]
            result['unsettledReceiptPhases'] = sorted(set(phases))
        except Exception:
            result['receiptDiagnosticUnavailable'] = True
    finally:
        finalize_fixture(manager, namespace, api, result)
    return result


def run(arguments):
    if sys.platform != 'linux': raise SystemExit('this fixture requires Linux; no portable fallback')
    refs = (arguments.python_image, arguments.caddy_image, arguments.postgres_image, arguments.mariadb_image)
    if any(not re.fullmatch(r'[^\s-][^\s]*@sha256:[0-9a-f]{64}', ref) for ref in refs):
        raise SystemExit('only locally available digest-pinned fixture images are allowed')
    api = LocalDockerAPI()
    api.identity()  # Reject remote/context overrides before any CLI query.
    metadata = {}
    architecture = 'arm64' if os.uname().machine == 'aarch64' else 'amd64'
    for image in refs:
        value = image_metadata(api, image)
        if (not re.fullmatch('sha256:[0-9a-f]{64}', value['id']) or value['architecture'] != architecture
                or value['os'] != 'linux'): raise SystemExit('fixture image native identity unavailable')
        metadata[image] = value
    images = {key: arguments.python_image for key in ('ADMIN_IMAGE', 'PLATFORM_IMAGE', 'PUBLISH_GATEWAY_IMAGE', 'ENGINE_IMAGE')}
    images.update(CADDY_IMAGE=arguments.caddy_image, POSTGRES_IMAGE=arguments.postgres_image, MARIADB_IMAGE=arguments.mariadb_image)
    paths = [ROOT / name for name in sorted(load_module().OWNED_RUNTIME_FILES)]
    paths += [Path(__file__).resolve(), ROOT / 'tests/test_surveyctl.py', ROOT / 'tests/integration/runtime_controller_native.py']
    hashes = lambda: {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result = {'schemaVersion': 1, 'scope': 'recorded-maintenance-real-database-components',
              'images': images, 'imageMetadata': metadata, 'nativeArchitecture': architecture, 'sourceBefore': hashes()}
    result['cases'] = []
    for name in REQUIRED:
        case = run_case(images, name)
        result['cases'].append(case)
        if case['fixtureCleanup'] != 'verified': break  # No second fixture after an unknown mutation/helper.
    result['sourceAfter'] = hashes()
    evidence = Path(arguments.evidence)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(result, sort_keys=True) + '\n')
    print(json.dumps({'scope': result['scope'], 'passed': passed(result),
                      'cases': [{k: case.get(k) for k in ('scenario', 'status', 'fixtureCleanup', 'failureStage', 'failureClasses')}
                                for case in result['cases']]}))
    return 0 if passed(result) else 1


def main(arguments):
    try:
        return run(arguments)
    except Exception as exc:
        # Preflight/driver failures must not dump raw commands or API responses.
        result = {'schemaVersion': 1, 'scope': 'recorded-maintenance-real-database-components',
                  'cases': [], 'failureStage': 'fixture-driver', 'failureClass': type(exc).__name__,
                  'restorationEligible': False, 'releaseEligible': False, 'cleanupVerified': False}
        evidence = Path(arguments.evidence)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(result) + '\n')
        print(json.dumps(result))
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-image', required=True)
    parser.add_argument('--caddy-image', required=True)
    parser.add_argument('--postgres-image', required=True)
    parser.add_argument('--mariadb-image', required=True)
    parser.add_argument('--evidence', required=True)
    raise SystemExit(main(parser.parse_args()))
