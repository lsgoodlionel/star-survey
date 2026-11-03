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


def main():
    require_disposable_runner(os.environ)
    root = Path(__file__).resolve().parents[3]
    production = Path(__file__).resolve().parent
    sys.path.insert(0, str(production))
    sys.path.insert(0, str(production / 'release'))
    from surveyctl import SurveyManager, load_manifest
    from doctor import Doctor
    from native_acceptance_evidence import write_doctor_evidence, write_engine_health_evidence, write_gateway_diagnostic_evidence

    evidence = Path(os.environ['SMOKE_EVIDENCE_DIR']).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    # This driver owns neither pre-existing containers nor network/volume state.
    for kind in ('container', 'network', 'volume'):
        found = run(['docker', kind, 'ls', '--format', '{{.Name}}' if kind != 'container' else '{{.Names}}']).stdout
        if any(n.startswith(('survey-production', 'survey-smoke-registry')) for n in found.splitlines()):
            raise ValueError('production smoke resource collision')
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
        stage = 'doctor'
        report = Doctor(target).run()
        secrets = [(target / 'shared/secrets' / p).read_text().strip() for p in os.listdir(target / 'shared/secrets')]
        write_doctor_evidence(report, evidence / 'doctor-installed.json', secrets)
        if report['status'] == 'blocked':
            raise ValueError('production doctor blocked')
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
            except Exception:
                pass  # Explicitly absent evidence; never marks a check passed.
            try:
                logs = run(compose + ['logs', '--no-color', '--tail', '200', 'publish-gateway'])
                write_gateway_diagnostic_evidence(logs.stdout + logs.stderr, evidence / 'gateway-diagnostic.json')
            except Exception:
                pass
    finally:
        try:
            if compose:
                run(compose + ['down', '--volumes', '--remove-orphans'])
                # Verify cleanup independently of the down command's exit status.
                for kind in ('container', 'network', 'volume'):
                    names = run(['docker', kind, 'ls', '--format', '{{.Name}}' if kind != 'container' else '{{.Names}}']).stdout
                    if any(n.startswith('survey-production') for n in names.splitlines()):
                        raise ValueError('production smoke cleanup incomplete')
            if registry_started:
                run(['docker', 'rm', '-fv', 'survey-smoke-registry'])
            cleanup = True
        except Exception:
            cleanup = False
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
