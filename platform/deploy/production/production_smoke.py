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


def main():
    require_disposable_runner(os.environ)
    root = Path(__file__).resolve().parents[3]
    production = Path(__file__).resolve().parent
    sys.path.insert(0, str(production))
    sys.path.insert(0, str(production / 'release'))
    from surveyctl import SurveyManager, load_manifest
    from doctor import Doctor, ProductProbe, CommandRunner
    from native_acceptance_evidence import write_doctor_evidence, write_engine_health_evidence, write_gateway_diagnostic_evidence

    evidence = Path(os.environ['SMOKE_EVIDENCE_DIR']).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    # This driver owns neither pre-existing containers nor network/volume state.
    assert_resources_absent(('survey-production', 'survey-smoke-registry'))
    work = Path(tempfile.mkdtemp(prefix='survey-production-smoke-'))
    os.chmod(work, 0o700)
    target, assets = work / 'target', work / 'assets'
    compose = None
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
        for key, tag in [('CADDY_IMAGE', 'caddy:2.11.6-alpine'), ('POSTGRES_IMAGE', 'postgres:16.10-alpine'), ('MARIADB_IMAGE', 'mariadb:10.11.14')]:
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
                  'releaseAcceptance': False}
        (evidence / 'smoke-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0 if journey and cleanup else 1


if __name__ == '__main__':
    sys.exit(main())
