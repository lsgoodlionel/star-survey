"""Manual, single-case evidence entry; never a candidate acceptance result."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types

PRODUCTION = Path(__file__).resolve().parents[2]
REPO = PRODUCTION.parents[2]
BASE = 'e869516b72ffa8d669392a3c7c20a250479617e1'
DIAGNOSTIC = '2d61cdd5d147d78fb18748c84db8382963e24d6d'
BRANCH = 'diagnostic/ownership-health-e869'
DIAGNOSTIC_FILES = {
    'platform/deploy/production/surveyctl.py': '586d64c898cec3c5209cbaa85dc65c9f9954ea73de4704f8ea64ded611e31410',
    'platform/deploy/production/tests/integration/owned_maintenance_native.py': '2c76cf0667e99df61cf641f51565016270cf1d6f25fe3da541e88a5d9f36a457',
}
CHANGED_FILES = set(DIAGNOSTIC_FILES) | {
    '.github/workflows/production-smoke.yml',
    'platform/deploy/production/tests/integration/ownership_health_sampling.py',
    'platform/deploy/production/tests/test_ownership_health_sampling.py',
}
IMAGES = {
    'python@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e':
        'sha256:274d131132dbdc6058ed77088525d77adaf0749a6d99f2cf7d42feb9a10737c8',
    'caddy@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee':
        'sha256:5813f7eb9b94fac4970f9f576695fe1ed1d53d41586fd3eaa350caf7c4627d9c',
    'postgres@sha256:a3b7f434b2dc57ce85a67e171163eb8ab1a1ebcb39d27484661f26b1dfbe30d6':
        'sha256:1b3c642526f8d274b12bdcd93b90aeb7e68a1f59eb20613adb96ed561c01d98c',
    'mariadb@sha256:7f22313fc130a377a44999965bcb0a08dd5b21e8502824c1b864f792f9bc66ab':
        'sha256:70867812f39c1e386104450bd6cf3bf9f6fb31ee4d771a10f7ea900950d53e71',
}


class SamplingError(Exception):
    pass


def execution_context(expected_sha):
    env = os.environ
    fixed = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
             'GITHUB_REPOSITORY': 'lsgoodlionel/star-survey', 'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64',
             'GITHUB_REF': 'refs/heads/' + BRANCH, 'GITHUB_JOB': 'ownership-health-sample',
             'GITHUB_RUN_ATTEMPT': '1'}
    if (sys.platform != 'linux' or os.uname().machine != 'x86_64'
            or any(env.get(k) != v for k, v in fixed.items())
            or re.fullmatch('[0-9a-f]{40}', expected_sha) is None or env.get('GITHUB_SHA') != expected_sha
            or re.fullmatch('[1-9][0-9]{0,19}', env.get('GITHUB_RUN_ID', '')) is None
            or re.fullmatch('[A-Za-z0-9 _.\u002d]{1,100}', env.get('RUNNER_NAME', '')) is None):
        raise SamplingError('context')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
        raise SamplingError('context')
    return {'repository': fixed['GITHUB_REPOSITORY'], 'runId': env['GITHUB_RUN_ID'], 'attempt': 1,
            'job': fixed['GITHUB_JOB'], 'runner': env['RUNNER_NAME'], 'executionSha': expected_sha,
            'ubuntu': '24.04', 'architecture': 'amd64'}


def git(*args):
    return subprocess.run(['git', *args], cwd=REPO, check=True, capture_output=True,
                          text=True, timeout=20).stdout.strip()


def verify_source(expected_sha):
    try:
        if (git('rev-parse', 'HEAD') != expected_sha or git('diff', '--name-only', 'HEAD')
                or git('ls-files', '--others', '--exclude-standard')
                or set(git('diff', '--name-only', BASE, 'HEAD').splitlines()) != CHANGED_FILES
                or any(hashlib.sha256((REPO / name).read_bytes()).hexdigest() != digest
                       for name, digest in DIAGNOSTIC_FILES.items())):
            raise SamplingError('source-identity')
    except (OSError, subprocess.SubprocessError):
        raise SamplingError('source-identity') from None


def load_component():
    path = Path(__file__).with_name('owned_maintenance_native.py')
    module = types.ModuleType('ownership_health_component')
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return module


def image_preflight(native):
    try:
        api = native.LocalDockerAPI()
        api.identity()  # Original local-daemon/override rejection, before even pulling.
        metadata = {}
        for reference, image_id in IMAGES.items():
            subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', 'pull', reference],
                           check=True, capture_output=True, timeout=90)
            value = native.image_metadata(api, reference)
            if value.get('id') != image_id or value.get('architecture') != 'amd64' or value.get('os') != 'linux':
                raise SamplingError('image-identity')
            metadata[reference] = value
        return metadata
    except Exception:
        raise SamplingError('image-identity') from None


def inventory(native):
    names = getattr(native, '_sampling_inventory_names', None)
    if names is None:
        names = tuple(sorted(set(native.load_module().OWNED_RUNTIME_FILES) | {
            'tests/integration/owned_maintenance_native.py', 'tests/integration/runtime_controller_native.py',
            'tests/test_surveyctl.py'}))
        native._sampling_inventory_names = names
    return {name: hashlib.sha256((PRODUCTION / name).read_bytes()).hexdigest() for name in sorted(names)}


def project_case(native, case):
    if (not isinstance(case, dict) or case.get('scenario') != 'backup-purge'
            or case.get('status') not in ('passed', 'failed')
            or case.get('fixtureCleanup') not in ('verified', 'unknown', 'unknown-or-refused', 'preserved-failed-or-unknown')):
        raise SamplingError('case-shape')
    result = {key: case[key] for key in ('scenario', 'status', 'fixtureCleanup')}
    result['fixtureCleanupScope'] = 'docker-objects-only'
    booleans = set(native.REQUIRED['backup-purge']) | {'privateStateRetained'}
    for key in booleans.intersection(case):
        if type(case[key]) is not bool:
            raise SamplingError('case-shape')
        result[key] = case[key]
    if 'failureStage' in case:
        if case['failureStage'] not in {'preflight', 'initialize', 'backup', 'verify', 'verify-archive-content',
                                      'restore-denial', 'owned-upgrade', 'uninstall', 'purge'}:
            raise SamplingError('case-shape')
        result['failureStage'] = case['failureStage']
    if 'failureClasses' in case:
        values = case['failureClasses']
        if (not isinstance(values, list) or len(values) > 6
                or any(not isinstance(v, str) or re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', v) is None for v in values)):
            raise SamplingError('case-shape')
        result['failureClasses'] = values
    if 'failureLocations' in case:
        values = case['failureLocations']
        allowed = {Path(name).name for name in getattr(native, '_sampling_inventory_names', DIAGNOSTIC_FILES)}
        if (not isinstance(values, list) or len(values) > 6 or any(row is not None and (
                not isinstance(row, dict) or set(row) != {'file', 'line'} or row['file'] not in allowed
                or type(row['line']) is not int or not 1 <= row['line'] <= 100000) for row in values)):
            raise SamplingError('case-shape')
        result['failureLocations'] = values
    if 'initialHealth' in case:
        value = case['initialHealth']
        if not isinstance(value, dict) or set(value) != {'services', 'engineInit', 'elapsedMs'}:
            raise SamplingError('case-shape')
        error = types.SimpleNamespace(health_evidence={k: value[k] for k in ('services', 'engineInit')},
                                      health_wait_elapsed_ms=value['elapsedMs'])
        projected = native.bounded_health_failure(error, native.LONG_RUNNING_SERVICES)
        if projected is None:
            raise SamplingError('case-shape')
        result['initialHealth'] = projected
    # Keep the original passed() per-case predicates without changing its two-case
    # suite contract or fabricating the deliberately unexecuted second case.
    if case['status'] == 'passed':
        if (case['fixtureCleanup'] != 'verified' or case.get('fixtureCleanupScope') != 'docker-objects-only'
                or type(case.get('retainedNetworkCount')) is not int or case['retainedNetworkCount'] != 1
                or any(case.get(k) is not True for k in native.REQUIRED['backup-purge'])
                or any(case.get(k) is not True for k in ('syntheticManifestOnly', 'hostAndTlsAreSynthetic'))
                or any(case.get(k) is not False for k in ('businessServicesCovered', 'defaultInstallCovered',
                    'restorationEligible', 'upgradeBusinessDataVerified', 'releaseEligible'))):
            raise SamplingError('case-shape')
        result['retainedNetworkCount'] = 1
    return result


def write_report(path, value):
    temporary_root = Path(os.environ.get('RUNNER_TEMP', '/nonexistent')).resolve()
    if not temporary_root.is_dir() or not path.resolve().is_relative_to(temporary_root):
        raise SamplingError('report-path')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.sampling-')
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def run(report_path, expected_sha):
    report = {'schemaVersion': 1, 'scope': 'ownership-health-sampling', 'samplingStatus': 'unknown',
              'entireSuitePassed': False, 'candidateQualified': False, 'restorationEligible': False,
              'releaseEligible': False, 'cleanupVerified': False, 'baseCommit': BASE,
              'diagnosticCommit': DIAGNOSTIC, 'phase': 'preflight'}
    try:
        write_report(report_path, report)
        report['execution'] = execution_context(expected_sha)
        verify_source(expected_sha)
        report['driverSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        native = load_component()
        report['imageMetadata'] = image_preflight(native)
        report['sourceBefore'] = inventory(native)
        report['phase'] = 'case-running'
        write_report(report_path, report)
        refs = list(IMAGES)
        images = {key: refs[0] for key in ('ADMIN_IMAGE', 'PLATFORM_IMAGE', 'PUBLISH_GATEWAY_IMAGE', 'ENGINE_IMAGE')}
        images.update(CADDY_IMAGE=refs[1], POSTGRES_IMAGE=refs[2], MARIADB_IMAGE=refs[3])
        case = native.run_case(images, 'backup-purge')  # Exactly one call. No retry or exceptional teardown here.
        report['sourceAfter'] = inventory(native)
        if report['sourceAfter'] != report['sourceBefore']:
            raise SamplingError('source-drift')
        report['case'] = project_case(native, case)
        complete = case['status'] == 'passed' and case['fixtureCleanup'] == 'verified'
        report['samplingStatus'] = 'completed-no-failure-reproduced' if complete else 'failure-observed'
        report['cleanupVerified'] = complete
        report['phase'] = 'finished'
        write_report(report_path, report)
        return 0 if complete else 1
    except Exception as error:
        category = str(error) if isinstance(error, SamplingError) else None
        report['failureCategory'] = category if category in {
            'context', 'source-identity', 'image-identity', 'case-shape', 'source-drift', 'report-path'
        } else 'driver-failure'
        report['samplingStatus'] = 'unknown'
        report['cleanupVerified'] = False
        try:
            write_report(report_path, report)
        except Exception:
            pass  # A missing/stale unknown report cannot authorize a replay or cleanup.
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-execution-sha', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.report, args.expected_execution_sha))
