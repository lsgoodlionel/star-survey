"""Real Caddy stdin/hash/removal, isolated fixture; no production/cohort proof."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import runtime_ownership as ownership
from owned_docker_exec import LocalDockerAPI
from owned_container_hash import OwnedContainerHash


class FixtureAlreadyRemoved(Exception):
    pass


def main(image):
    namespace = 'survey-hash-fixture-' + uuid.uuid4().hex
    ownership.PROJECT = namespace
    if not namespace.startswith('survey-hash-fixture-'):
        raise SystemExit('fixture namespace invalid')
    target = Path(tempfile.mkdtemp(prefix='survey-hash-receipts-')).resolve()
    (target / '.surveyctl').mkdir(mode=0o700)
    operation, installation = str(uuid.uuid4()), str(uuid.uuid4())
    store = ownership.PrivateStore(target, name='runtime-launch-' + operation.replace('-', '') + '.json')
    paths = [ROOT / name for name in ('owned_container_hash.py', 'owned_docker_exec.py', 'owned_children.py', 'runtime_ownership.py')]
    hashes = lambda: {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = {'scope': 'caddy-stdin-container-lifetime-only', 'ownerGuard': 'fixture-injected-not-process-proof',
        'sourceBefore': hashes(), 'containerCleanup': 'unknown'}
    class FixtureGuard:
        def check(self): return {'pid': os.getpid(), 'start': '1', 'parentPid': os.getppid(), 'parentStart': '1'}
    class FixtureAPI(LocalDockerAPI):
        def _json(self, method, path, body=None, **kwargs):
            value = super()._json(method, path, body, **kwargs)
            if method == 'GET' and path.startswith('/containers/') and path.endswith('/json'):
                host = value.get('HostConfig', {})
                expected = helper.saved and helper.saved.get('containerId')
                if expected and value.get('Id') == expected:
                    # Fixed non-secret isolation fields only; never Config.Env,
                    # request payloads, raw logs, stdin or hash output.
                    report['isolationObserved'] = {key: host.get(key) for key in (
                        'NetworkMode', 'ReadonlyRootfs', 'CapDrop', 'SecurityOpt', 'PidsLimit', 'Memory', 'NanoCpus', 'Tmpfs')}
            return value
    api = FixtureAPI()
    helper = OwnedContainerHash(api, store, FixtureGuard(), operation_id=operation,
        installation_id=installation, image=image, target_sha256=hashlib.sha256(str(target).encode()).hexdigest())
    try:
        output = helper.run(secrets.token_hex(32).encode('ascii'))
        record = store.load()
        report['validHashShape'] = re.fullmatch(rb'\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}\n', output) is not None
        report['receiptCompleted'] = record['phase'] == 'completed' and record['exitCode'] == 0
        report['containerCleanup'] = 'verified' if api.container_absent(record['containerId']) else 'failed-or-unknown'
    except Exception as exc:
        report['failure'] = 'helper-evidence-unavailable'
        chain = []
        while exc is not None and len(chain) < 5:
            trace = exc.__traceback__
            while trace and trace.tb_next: trace = trace.tb_next
            chain.append({'class': type(exc).__name__, 'file': Path(trace.tb_frame.f_code.co_filename).name if trace else None,
                'line': trace.tb_lineno if trace else None})
            exc = exc.__cause__
        report['failureLocations'] = chain  # No raw messages, API bodies, passwords or hash.
        record = store.load()
        if record:
            report['receiptPhase'] = record.get('phase')
            report['receiptExitCode'] = record.get('exitCode')
            cid = record.get('containerId')
            if isinstance(cid, str) and re.fullmatch(r'[0-9a-f]{64}', cid):
                try:
                    if api.container_absent(cid):
                        report['containerCleanup'] = 'verified'
                        raise FixtureAlreadyRemoved()
                    value = api._json('GET', '/containers/' + cid + '/json')
                    labels, host = value.get('Config', {}).get('Labels', {}), value.get('HostConfig', {})
                    if (value.get('Name') != '/' + helper.name or labels.get('io.survey.installation') != installation
                            or value.get('Config', {}).get('Image') != image or host.get('NetworkMode') != 'none'
                            or host.get('Binds') or host.get('Privileged') or host.get('PortBindings')):
                        raise RuntimeError('fixture identity changed')
                    if value['State']['Running']:
                        api._json('POST', '/containers/' + cid + '/stop?t=5', accepted=(204, 304))
                    api._json('DELETE', '/containers/' + cid + '?force=false&v=false', accepted=(204,))
                    report['containerCleanup'] = 'verified' if api.container_absent(cid) else 'failed-or-unknown'
                except FixtureAlreadyRemoved:
                    pass
                except Exception:
                    report['containerCleanup'] = 'failed-or-unknown'
    report['sourceAfter'] = hashes()
    passed = ('failure' not in report and report['sourceAfter'] == report['sourceBefore']
        and report.get('validHashShape') is True and report.get('receiptCompleted') is True
        and report['containerCleanup'] == 'verified')
    if passed:
        shutil.rmtree(target)
    else:
        report['receiptEvidenceRetained'] = str(target)
    print(json.dumps(report, sort_keys=True))
    return 0 if passed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    raise SystemExit(main(parser.parse_args().image))
