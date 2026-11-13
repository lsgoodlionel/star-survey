"""Disposable source-built production-topology smoke; never release evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

MARIADB_SOURCE_VERSION = "10.11.14"


def require_disposable_runner(env):
    if any(env.get(k) != v for k, v in {'GITHUB_ACTIONS': 'true',
           'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_ARCH': 'X64'}.items()):
        raise ValueError('production smoke requires a disposable GitHub-hosted X64 runner')


def overall_status(journey, cleanup):
    return 'passed' if journey and cleanup else 'failed'


def run(command, **kwargs):
    # Never forward raw subprocess output: Compose and HTTP failures may contain
    # generated test credentials. Only fixed phase and exception classes escape.
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def assert_resources_absent(prefixes, runner=run):
    for kind in ('container', 'network', 'volume'):
        args = ['docker', kind, 'ls']
        if kind == 'container':
            args.append('--all')
        args += ['--format', '{{.Names}}' if kind == 'container' else '{{.Name}}']
        if any(name.startswith(prefixes) for name in runner(args).stdout.splitlines()):
            raise ValueError('production smoke resource collision or cleanup incomplete')


def attempt_cleanups(actions):
    succeeded = True
    for action in actions:
        try:
            action()
        except Exception:
            succeeded = False
    return succeeded


def engine_subprobes(engine, runner=run):
    # Execute real checks but retain no response body, CLI output or addresses.
    code = '''$c=stream_context_create(["http"=>["timeout"=>2,"follow_location"=>0]]);
    $b=@file_get_contents("http://127.0.0.1/production-health.php",false,$c);
    $line=$http_response_header[0]??"";preg_match("#^HTTP/\\\\S+ ([0-9]{3})#",$line,$m);
    echo json_encode(["httpStatus"=>isset($m[1])?(int)$m[1]:0,"requestCompleted"=>$b!==false,
    "bodyHasError"=>is_string($b)&&preg_match("/CException|CDbException|Fatal error|Parse error/i",$b)===1,
    "edgeResolved"=>@gethostbynamel("edge")!==false,"gatewayResolved"=>@gethostbynamel("publish-gateway")!==false]);'''
    value = json.loads(runner(['docker', 'exec', engine, 'php', '-r', code]).stdout)
    if set(value) != {'httpStatus', 'requestCompleted', 'bodyHasError', 'edgeResolved', 'gatewayResolved'} or type(value['httpStatus']) is not int or not 0 <= value['httpStatus'] <= 599 or any(type(value[k]) is not bool for k in ('requestCompleted', 'bodyHasError', 'edgeResolved', 'gatewayResolved')):
        raise ValueError('engine subprobe output is invalid')
    try:
        runner(['docker', 'exec', engine, 'php', 'application/commands/console.php', 'productionInit', 'status'])
        value['initStatusExitCode'] = 0
    except subprocess.CalledProcessError as exc:
        value['initStatusExitCode'] = exc.returncode if 0 <= exc.returncode <= 255 else -1
    return {'schemaVersion': 1, **value}


def check_gateway_rpc(compose, runner=run):
    script = '''import os,sys
from pubgw.engines import load_engines
from pubgw.rpc import HttpTransport,RemoteControlClient
e=load_engines(os.environ['PUBGW_ENGINES_CONFIG'],os.environ)['production-engine-01']
c=RemoteControlClient(HttpTransport(e.rpc_url,rpc_path=''))
try:
 c.login(e.user,e.password)
 c.logout()
except Exception:
 sys.exit(1)
'''
    runner(compose + ['exec', '-T', 'publish-gateway', '/bin/sh', '/opt/survey/run-with-secrets.sh',
           'PUBGW_SHARED_SECRET=/run/secrets/platform_pubgw_secret',
           'PUBGW_ENGINE_ADMIN_PASSWORD=/run/secrets/pubgw_engine_admin_password',
           'PUBGW_ENGINE_CHANNEL_SECRET=/run/secrets/pubgw_engine_channel_secret', '--', 'python', '-c', script])


def bridge_diagnostic(compose, sid, runner=run):
    if type(sid) is not int or not 0 < sid <= 2147483647:
        raise ValueError('bridge diagnostic scope is invalid')
    result = runner(compose + ['exec', '-T', '-e',
        'YII_CONSOLE_COMMANDS=/var/www/html/platform/deploy/production/smoke-commands',
        '-e', 'SMOKE_SURVEY_ID=' + str(sid), 'engine', 'php', 'application/commands/console.php', 'smokeBridge'], timeout=30)
    lines = [line[len('SMOKE_BRIDGE_JSON:'):] for line in result.stdout.splitlines() if line.startswith('SMOKE_BRIDGE_JSON:')]
    if len(lines) != 1:
        raise ValueError('bridge diagnostic marker is missing or ambiguous')
    value = json.loads(lines[0])
    counts = {'completedResponses', 'recordedEvents', 'pendingEvents', 'deliveredEvents'}
    if not isinstance(value, dict) or set(value) != counts | {'phase', 'errorCategory', 'httpStatus'}:
        raise ValueError('bridge diagnostic fields are invalid')
    if any(type(value[k]) is not int or not 0 <= value[k] <= 1000000 for k in counts):
        raise ValueError('bridge diagnostic counts are invalid')
    if value['phase'] not in {'inspect', 'completed'} or value['errorCategory'] not in {None, 'RuntimeException', 'CDbException', 'CException', 'other'}:
        raise ValueError('bridge diagnostic classes are invalid')
    if value['httpStatus'] is not None:
        raise ValueError('bridge diagnostic status is invalid')
    return {'schemaVersion': 1, 'scope': 'current-probe-before-cleanup', 'diagnosticStatus': 'captured', **value}


def observed_probe_class(base, evidence, collector=bridge_diagnostic):
    class ObservedProbe(base):
        def _close_engine_survey(self, instance, sid):
            try:
                report = collector(self.compose, sid)
            except Exception as exc:
                category = 'command-failed' if isinstance(exc, subprocess.CalledProcessError) else 'invalid-report' if isinstance(exc, (ValueError, TypeError)) else 'other'
                report = {'schemaVersion': 1, 'scope': 'current-probe-before-cleanup',
                          'diagnosticStatus': 'unavailable', 'errorCategory': category}
            try:
                (evidence / 'bridge-diagnostic.json').write_text(json.dumps(report, indent=2) + '\n')
            except OSError:
                # Missing evidence still fails require_bridge_evidence; diagnostics
                # must never interrupt the caller's engine/project cleanup.
                pass
            finally:
                super()._close_engine_survey(instance, sid)
    return ObservedProbe


def require_bridge_evidence(evidence):
    value = json.loads((evidence / 'bridge-diagnostic.json').read_text())
    if (value.get('diagnosticStatus') != 'captured' or value.get('phase') != 'completed'
            or value.get('errorCategory') is not None or value.get('completedResponses', 0) < 1
            or value.get('deliveredEvents', 0) < 1):
        raise ValueError('current probe bridge evidence did not verify completion and delivery')


def verify_source_backup(target, directory, version):
    """Authenticate source-test bytes; never confer restoration eligibility."""
    from backup import (AuthenticatedOpenSSLCipher, IMAGE_KEYS, VOLUME_SOURCES,
                        canonical_manifest, control_authenticator, safe_absolute, sha256)
    from restore import RestoreIntegrityError, RestoreManager, mariadb_dump_format
    import secrets

    target = safe_absolute(target, 'source target')
    directory = safe_absolute(directory, 'source backup')
    if not isinstance(version, str) or re.fullmatch(r'\d+\.\d+\.\d+(?:-rc\.\d+)?', version) is None:
        raise ValueError('source backup version is invalid')
    release = safe_absolute(target / 'releases' / version, 'source release')
    manifest_path = safe_absolute(release / 'release.json', 'source manifest')
    source = json.loads(manifest_path.read_text())
    guard = [line for line in (release / '.env').read_text().splitlines()
             if line.startswith('PLATFORM_ADMIN_ENFORCE_PERSONNEL=')]
    floor = safe_absolute(target / '.surveyctl/auth-security-floor', 'source floor')
    if guard != ['PLATFORM_ADMIN_ENFORCE_PERSONNEL=true'] or floor.exists() or source.get('securityCapabilities'):
        raise ValueError('source fixture must retain its enforced, unqualified boundary')
    key = safe_absolute(target / 'shared/secrets/backup_encryption_key', 'source backup key')
    if not key.is_file() or key.stat().st_mode & 0o077:
        raise ValueError('source backup key is unsafe')
    payloads = {name + '.enc' for name in ('postgres.dump', 'mariadb.sql', *VOLUME_SOURCES)}
    expected = payloads | {'manifest.json', 'SHA256SUMS', 'CONTROL-HMAC'}
    if (not directory.is_dir() or {path.name for path in directory.iterdir()} != expected
            or any(path.is_symlink() or not path.is_file() for path in directory.iterdir())):
        raise ValueError('source backup inventory is invalid')
    raw = (directory / 'manifest.json').read_bytes()
    metadata = json.loads(raw)
    if (not isinstance(metadata, dict) or metadata.get('schemaVersion') != 2
            or metadata.get('version') != version or metadata.get('databaseSchema') != str(source['database']['schema'])
            or metadata.get('releaseManifestSha256') != sha256(manifest_path)
            or not isinstance(source.get('images'), dict) or set(source['images']) != IMAGE_KEYS
            or metadata.get('images') != source['images'] or canonical_manifest(metadata) != raw
            or not isinstance(metadata.get('files'), dict) or set(metadata['files']) != payloads):
        raise ValueError('source backup identity is invalid')
    files = metadata['files']
    if any(not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None for digest in files.values()):
        raise ValueError('source backup checksums are invalid')
    sums = (directory / 'SHA256SUMS').read_bytes()
    expected_sums = ''.join(f'{digest}  {name}\n' for name, digest in sorted(files.items())).encode('ascii')
    if sums != expected_sums or not secrets.compare_digest(
            (directory / 'CONTROL-HMAC').read_text(encoding='ascii').strip(), control_authenticator(raw, sums, key)):
        raise ValueError('source backup control authentication failed')
    cipher = AuthenticatedOpenSSLCipher()
    dump_format = None
    volumes_validated = 0
    with tempfile.TemporaryDirectory() as temporary:
        for name, digest in files.items():
            encrypted = directory / name
            if not secrets.compare_digest(sha256(encrypted), digest):
                raise ValueError('source backup payload checksum failed')
            plain = Path(temporary) / name.removesuffix('.enc')
            cipher.decrypt(encrypted, plain, key)
            if not plain.stat().st_size:
                raise ValueError('source backup payload is empty')
            if name.removesuffix('.enc') in VOLUME_SOURCES:
                try:
                    RestoreManager(target)._validate_archive(plain)
                except RestoreIntegrityError as error:
                    raise ValueError('source volume archive format is invalid') from error
                volumes_validated += 1
            if name == 'mariadb.sql.enc':
                dump_format = mariadb_dump_format(plain.read_bytes())
                if dump_format is None:
                    raise ValueError('source MariaDB dump format is invalid')
        try:
            RestoreManager(target).verify_backup(directory, Path(temporary))
        except RestoreIntegrityError as error:
            if str(error) != 'personnel security floor is missing':
                raise ValueError('source backup restoration rejection is unexpected') from error
        else:
            raise ValueError('unsigned source backup unexpectedly qualified for restoration')
    return {'schemaVersion': 1, 'scope': 'source-backup-integrity-only', 'integrityVerified': True,
            'encryptedPayloads': len(payloads), 'authenticatedPayloads': len(payloads),
            'mariadbDumpFormat': dump_format, 'volumeArchivesValidated': volumes_validated,
            'restoreEligibility': 'rejected-missing-personnel-floor', 'restorationEligible': False,
            'serviceRecoveryVerified': False, 'releaseAcceptance': False}



def mariadb_dump_client_version(compose, runner=run):
    result = runner(compose + ['exec', '-T', 'engine-db', 'mariadb-dump', '--version'])
    versions = re.findall(r'\bDistrib ([0-9]+\.[0-9]+\.[0-9]+)(?:[-\s]|$)', result.stdout)
    if versions != [MARIADB_SOURCE_VERSION]:
        raise ValueError('source MariaDB dump client version is unexpected or ambiguous')
    return versions[0]


def require_source_doctor(report, proof):
    if (proof.get('scope') != 'source-backup-integrity-only' or proof.get('integrityVerified') is not True
            or proof.get('restorationEligible') is not False or proof.get('releaseAcceptance') is not False
            or proof.get('restoreEligibility') != 'rejected-missing-personnel-floor'
            or proof.get('encryptedPayloads') != 9 or proof.get('authenticatedPayloads') != 9
            or proof.get('volumeArchivesValidated') != 7
            or proof.get('mariadbDumpFormat') not in {'legacy-comment', 'mariadb-sandbox'}
            or proof.get('mariadbDumpClientVersion') != MARIADB_SOURCE_VERSION):
        raise ValueError('source backup integrity evidence is missing or qualified')
    required = {'ports', 'internal-exposure', 'runtime-exposure', 'tls', 'containers',
                'migrations', 'volumes', 'backups', 'minimal-probe'}
    rows = report.get('checks', [])
    if (not isinstance(rows, list) or len(rows) != len(required)
            or any(not isinstance(row, dict) or row.get('id') not in required for row in rows)):
        raise ValueError('source doctor checks are invalid')
    checks = {row['id']: row for row in rows}
    if (set(checks) != required or report.get('status') != 'warning'
            or checks['backups'].get('status') != 'warning'
            or checks['backups'].get('summary') != 'no readable verified backup exists'
            or any(checks[name].get('status') != 'ok' for name in required - {'backups'})):
        raise ValueError('source doctor must retain only the unqualified-backup warning')


def main():
    require_disposable_runner(os.environ)
    root = Path(__file__).resolve().parents[3]
    production = Path(__file__).resolve().parent
    sys.path.insert(0, str(production))
    sys.path.insert(0, str(production / 'release'))
    from surveyctl import SurveyManager, load_manifest
    from doctor import Doctor, ProductProbe, CommandRunner
    from backup import BackupManager
    from native_acceptance_evidence import write_doctor_evidence, write_engine_health_evidence, write_gateway_diagnostic_evidence

    evidence = Path(os.environ['SMOKE_EVIDENCE_DIR']).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    # This driver owns neither pre-existing containers nor network/volume state.
    assert_resources_absent(('survey-production', 'survey-smoke-registry'))
    work = Path(tempfile.mkdtemp(prefix='survey-production-smoke-'))
    os.chmod(work, 0o700)
    target, assets = work / 'target', work / 'assets'
    compose = None
    backup_evidence = None
    registry_started = False
    stage, error, journey, cleanup = 'build', None, False, False
    host, marker = 'survey-smoke.test', '# survey-production-smoke'
    hosts_changed = False
    try:
        run(['docker', 'run', '-d', '--name', 'survey-smoke-registry', '-p', '127.0.0.1:5000:5000', 'registry:2.8.3'])
        registry_started = True
        images = {}
        for name, context, dockerfile in (
            ('ADMIN_IMAGE', 'platform/apps/admin-web', 'platform/apps/admin-web/Dockerfile'),
            ('PLATFORM_IMAGE', 'platform/services/business', 'platform/services/business/Dockerfile'),
            ('PUBLISH_GATEWAY_IMAGE', 'platform/tools/publish-gateway', 'platform/tools/publish-gateway/Dockerfile'),
            ('ENGINE_IMAGE', '.', 'platform/deploy/production/Dockerfile.engine')):
            stage = 'build-' + name.lower().replace('_image', '').replace('_', '-')
            tag = 'localhost:5000/smoke/' + name.lower() + ':test'
            run(['docker', 'build', '--platform', 'linux/amd64', '-f', str(root / dockerfile), '-t', tag, str(root / context)])
            run(['docker', 'push', tag])
            images[name] = json.loads(run(['docker', 'image', 'inspect', tag]).stdout)[0]['RepoDigests'][0]
        for key, tag in [('CADDY_IMAGE', 'caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee'), ('POSTGRES_IMAGE', 'postgres:16.10-alpine'), ('MARIADB_IMAGE', f'mariadb:{MARIADB_SOURCE_VERSION}')]:
            stage = 'pull-' + key.lower().replace('_image', '')
            run(['docker', 'pull', '--platform', 'linux/amd64', tag])
            images[key] = json.loads(run(['docker', 'image', 'inspect', tag]).stdout)[0]['RepoDigests'][0]
        stage = 'stage-source-bundle'
        version_file = work / 'version'
        version_file.write_text('0.0.0-rc.1\n')
        commit = run(['git', '-C', str(root), 'rev-parse', 'HEAD']).stdout.strip()
        schema = max(int(m.group(1)) for p in (root / 'platform/services/business/src/main/resources/db/migration').glob('V*.sql')
                     if (m := re.match(r'V(\d+)__', p.name)))
        args = [sys.executable, str(production / 'release/build_release.py'), '--source', str(production),
                '--trusted-root', str(root), '--output', str(assets), '--version-file', str(version_file), '--commit', commit,
                '--channel', 'candidate', '--minimum-source-version', '0.0.0', '--database-schema', str(schema),
                '--backup-schema', '2', '--rollback', 'restore-only', '--compatible-source-schema', str(schema),
                '--source-date-epoch', run(['git', '-C', str(root), 'show', '-s', '--format=%ct', 'HEAD']).stdout.strip(),
                '--native-acceptance-profile', 'github-actions-native-v1']
        for key, ref in images.items():
            args += ['--image', key + '=' + ref]
        run(args)
        # Source-built unsigned fixtures deliberately have no securityCapability
        # attestation. We stage them directly for smoke, not install acceptance.
        os.environ['SURVEY_NATIVE_ACCEPTANCE'] = '1'
        manifest = load_manifest(assets / 'release.json')
        manager = SurveyManager(target)
        release = manager._stage_release(manifest, 'amd64', host, 'smoke-operator', 'smoke@example.invalid')
        manager._write_state(status='installing', version=manifest.version, databaseSchema=str(schema),
                             publicHost=host, adminUser='smoke-operator', adminEmail='smoke@example.invalid')
        compose = manager._compose(manifest.version)
        manager._ensure_admin_password_hash(images['CADDY_IMAGE'])
        stage = 'initialize-production-topology'
        run(compose + ['up', '-d', '--remove-orphans'])
        manager._wait_healthy(compose, 'production smoke health failed')
        with open('/etc/hosts', 'a') as hosts:
            hosts.write('127.0.0.1 ' + host + ' ' + marker + '\n')
        hosts_changed = True
        os.environ['SSL_CERT_FILE'] = str(target / '.surveyctl/native-acceptance-ca.crt')
        stage = 'tls'
        manager._verify_public_tls(host)
        manager._atomic_write(manager.current_path, manifest.version + '\n')
        manager._write_state(status='healthy')
        stage = 'machine-setup'
        manager.setup_probe()
        stage = 'recovery-relay-stop-guard'
        # Real disposable Compose topology: verify the new recovery guard
        # can stop and identify the live relay before it could invoke an old
        # restore helper, then restart it and require full service health.
        manager._stop_new_writers_before_legacy_restore(compose)
        run(compose + ['up', '-d', '--no-deps', 'engine-relay'])
        manager._wait_healthy(compose, 'relay did not recover after stop guard')
        (evidence / 'recovery-relay-stop.json').write_text(json.dumps({
            'scope': 'source-built-compose-relay-stop-restart', 'status': 'passed',
            'crossVersionRestoreVerified': False, 'databaseQuiescenceVerified': False,
        }, indent=2) + '\n')
        stage = 'gateway-rpc-authentication'
        check_gateway_rpc(compose)
        stage = 'doctor'
        observed = observed_probe_class(ProductProbe, evidence)
        report = Doctor(target, product_probe=lambda host, commands: observed(target, host, commands, CommandRunner()).run()).run()
        secrets = [(target / 'shared/secrets' / p).read_text().strip() for p in os.listdir(target / 'shared/secrets')]
        write_doctor_evidence(report, evidence / 'doctor-installed.json', secrets)
        if report['status'] == 'blocked':
            raise ValueError('production doctor blocked')
        stage = 'bridge-evidence'
        require_bridge_evidence(evidence)
        stage = 'backup'
        backup_directory = BackupManager(target).create(target / 'backups/source-smoke', manifest.version)
        backup_evidence = verify_source_backup(target, backup_directory, manifest.version)
        backup_evidence['mariadbDumpClientVersion'] = mariadb_dump_client_version(compose)
        backup_evidence['mariadbDumpImage'] = images['MARIADB_IMAGE']
        (evidence / 'source-backup.json').write_text(json.dumps(backup_evidence, indent=2) + '\n')
        stage = 'doctor-backed-up'
        report = Doctor(target, product_probe=lambda host, commands: observed(target, host, commands, CommandRunner()).run()).run()
        write_doctor_evidence(report, evidence / 'doctor-backed-up.json', secrets)
        require_source_doctor(report, backup_evidence)
        backup_evidence['serviceRecoveryVerified'] = True
        (evidence / 'source-backup.json').write_text(json.dumps(backup_evidence, indent=2) + '\n')
        require_bridge_evidence(evidence)
        stage = 'gateway-negative-requests'
        gateway = run(compose + ['ps', '-q', 'publish-gateway']).stdout.strip()
        env = dict(os.environ, GATEWAY_CONTAINER=gateway, SCHEME_ENGINE_HOST='engine',
                   SCHEME_NETWORK='survey-production_internal', SCHEME_CLIENT_IMAGE=images['PUBLISH_GATEWAY_IMAGE'])
        run(['bash', str(root / 'platform/deploy/test/assert-gateway-scheme.sh')], env=env)
        journey = True
    except Exception as exc:
        error = type(exc).__name__
        if compose:
            try:
                engine = run(compose + ['ps', '-aq', 'engine']).stdout.strip()
                health = json.loads(run(['docker', 'inspect', '--format', '{{json .State.Health}}', engine]).stdout)
                write_engine_health_evidence(health, evidence / 'engine-health.json')
                (evidence / 'engine-subprobes.json').write_text(json.dumps(engine_subprobes(engine), indent=2) + '\n')
            except Exception:
                pass  # Explicitly absent evidence; never marks a check passed.
            try:
                logs = run(compose + ['logs', '--no-color', '--tail', '200', 'publish-gateway'])
                write_gateway_diagnostic_evidence(logs.stdout + logs.stderr, evidence / 'gateway-diagnostic.json')
            except Exception:
                pass
    finally:
        def cleanup_compose():
            if compose is not None:
                run(compose + ['down', '--volumes', '--remove-orphans'])
                # Verify cleanup independently of the down command's exit status.
                assert_resources_absent(('survey-production',))
        def cleanup_registry():
            if registry_started:
                run(['docker', 'rm', '-fv', 'survey-smoke-registry'])
                assert_resources_absent(('survey-smoke-registry',))
        cleanup = attempt_cleanups([cleanup_compose, cleanup_registry])
        if hosts_changed:
            try:
                hosts_path = Path('/etc/hosts')
                hosts_path.write_text(''.join(line for line in hosts_path.read_text().splitlines(keepends=True) if marker not in line))
            except OSError:
                cleanup = False
        try:
            shutil.rmtree(work)
        except OSError:
            cleanup = False
        result = {'schemaVersion': 1, 'scope': 'source-built-amd64-production-smoke', 'status': overall_status(journey, cleanup),
                  'lastStep': stage, 'errorCategory': error, 'journeyPassed': journey, 'cleanupPassed': cleanup,
                  'backupIntegrityVerified': bool(backup_evidence and backup_evidence.get('integrityVerified')),
                  'restorationEligible': False, 'releaseAcceptance': False}
        (evidence / 'smoke-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0 if journey and cleanup else 1


if __name__ == '__main__':
    sys.exit(main())
