"""Explicit non-root Linux FD-only crypto experiment, not a lifecycle gate.

Run with --image LOCAL_PYTHON_REPO@sha256:DIGEST. Never pulls or mounts host data.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

MODULES = ('owned_memory.py', 'owned_children.py', 'owned_crypto.py', 'owned_crypto_child.py',
           'runtime_ownership.py', 'owned_cipher_adapter.py', 'backup.py', 'restore.py', 'owned_backup_verify.py',
           'surveyctl.py', 'release.schema.json', 'tests/fixtures/release.json')
CHECKS = ('roundTrip', 'truncatedCipherRejected', 'deadlineChildEnded', 'noNamedPlaintext',
          'authenticatedRoundTrip', 'badHmacStartsNoChild', 'allSyntheticPayloadFormatsVerified', 'spawnHasNoPlaintextFDs',
          'recoveryTargetMetadataVerified', 'recoveryTargetHashMismatchRejected', 'recoveryTargetVersionRejected',
          'recoveryTargetFloorRejected', 'recoveryBadControlHmacStartsNoChild', 'recoveryNoNamedPayload',
          'recoveryRestoreRemainsDisabled')


def passed(report, hashes):
    evidence = report.get('evidence', {})
    return (type(report.get('exitCode')) is int and report['exitCode'] == 0
        and report.get('sourceAfter') == hashes and report.get('containerCleanup') == 'verified'
        and 'failure' not in report and isinstance(evidence, dict)
        and all(evidence.get(key) is True for key in CHECKS)
        and evidence.get('sourceHashes') == hashes and evidence.get('roundTripExitCodes') == [0, 0]
        and all(type(code) is int for code in evidence.get('roundTripExitCodes', [])))


def inside():
    sys.path.insert(0, '/tmp/code')
    from owned_memory import AnonymousWorkspace
    from owned_children import establish_parent_guard, read_process_identity, ChildJournal
    from owned_crypto import OpenSSLProcess, KnownCryptoFailure, LocalBackend, GuardedCryptoProcess
    from runtime_ownership import PrivateStore
    from owned_cipher_adapter import ControlledCipher, BackupError
    parent = read_process_identity(os.getppid())
    guard = establish_parent_guard(parent['pid'], parent['start'])
    root = Path('/tmp/receipts')
    root.mkdir(mode=0o700)
    (root / '.surveyctl').mkdir(mode=0o700)
    report = {key: False for key in CHECKS}
    def execute(profile, workspace, source, output, key, timeout=10):
        store = PrivateStore(root, name='runtime-child-' + uuid.uuid4().hex + '.json')
        journal = ChildJournal(store, guard, operation_id=str(uuid.uuid4()), installation_id=str(uuid.uuid4()))
        with workspace.export_files(inputs=(source, key), output=output) as leases:
            runner = OpenSSLProcess(journal, guard)
            try:
                runner.execute(profile, leases[0], leases[2], leases[1], timeout=timeout)
            except KnownCryptoFailure:
                return False, store.load()['children'][-1]
        return True, store.load()['children'][-1]
    with AnonymousWorkspace(max_bytes=1024 * 1024) as workspace:
        plain, key, cipher, restored, broken, badout, timed = [workspace / name for name in
            ('plain', 'key', 'cipher', 'restored', 'broken', 'badout', 'timed')]
        for item, content in ((plain, b'controlled synthetic answer\n' * 100), (key, b'a' * 64)):
            with item.open('xb') as handle:
                handle.write(content)
        encrypted, receipt = execute('encrypt', workspace, plain, cipher, key)
        decrypted, receipt2 = execute('decrypt', workspace, cipher, restored, key)
        report['roundTripExitCodes'] = [receipt['exitCode'], receipt2['exitCode']]
        report['roundTrip'] = (encrypted and decrypted and restored.read_bytes() == plain.read_bytes()
            and receipt['phase'] == receipt2['phase'] == 'completed')
        with broken.open('xb') as handle:
            handle.write(cipher.read_bytes()[:-1])
        accepted, failed = execute('decrypt', workspace, broken, badout, key)
        report['truncatedCipherRejected'] = not accepted and failed['phase'] == 'failed' and failed['reason'] == 'exit'
        accepted, deadline = execute('encrypt', workspace, plain, timed, key, timeout=0.001)
        report['deadlineChildEnded'] = not accepted and deadline['phase'] == 'failed' and deadline['reason'] == 'deadline'
        # Inspect the actual Linux child before sending its release packet:
        # neither key/plaintext nor the other data/binary FD may be inherited.
        store = PrivateStore(root, name='runtime-child-' + uuid.uuid4().hex + '.json')
        journal = ChildJournal(store, guard, operation_id=str(uuid.uuid4()), installation_id=str(uuid.uuid4()))
        backend = LocalBackend()
        with workspace.export_files(inputs=(plain, key), output=workspace / 'guard-only') as leases, backend.binary() as binary:
            descriptors = (leases[0].fd, leases[2].fd, leases[1].fd, binary)
            identities = {(os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in descriptors}
            token = journal.begin('openssl-encrypt')
            process, child_start = None, None
            try:
                process = backend.spawn(guard.check(), descriptors)
                child_start = backend.identity(process.pid)['start']
                journal.created(token, process.pid, child_start)
                process.control.settimeout(5)
                ready = process.control.recv(64) == GuardedCryptoProcess.READY
                child_fds = {(entry.stat().st_dev, entry.stat().st_ino)
                             for entry in (Path('/proc') / str(process.pid) / 'fd').iterdir()}
                report['spawnHasNoPlaintextFDs'] = ready and not identities.intersection(child_fds)
            finally:
                if process is not None:
                    code = OpenSSLProcess(journal, guard)._end(process, child_start)
                    journal.finished(token, code, succeeded=False, reason='bootstrap')
    encrypted_root = Path('/tmp/encrypted')
    encrypted_root.mkdir(mode=0o700)
    store = PrivateStore(root, name='runtime-child-' + uuid.uuid4().hex + '.json')
    journal = ChildJournal(store, guard, operation_id=str(uuid.uuid4()), installation_id=str(uuid.uuid4()))
    with AnonymousWorkspace(max_bytes=1024 * 1024) as workspace:
        cipher = ControlledCipher(workspace, OpenSSLProcess(journal, guard), encrypted_root, b'c' * 64)
        plain, restored = workspace / 'answer', workspace / 'restored'
        with plain.open('xb') as handle:
            handle.write(b'synthetic authenticated answer')
        destination = encrypted_root / 'postgres.dump.enc'
        cipher.encrypt(plain, destination, cipher.key)
        cipher.decrypt(destination, restored, cipher.key)
        report['authenticatedRoundTrip'] = (restored.read_bytes() == plain.read_bytes()
            and len(store.load()['children']) == 2
            and all(item['phase'] == 'completed' for item in store.load()['children']))
        damaged = bytearray(destination.read_bytes())
        damaged[-1] ^= 1
        destination.write_bytes(damaged)
        try:
            cipher.decrypt(destination, workspace / 'rejected', cipher.key)
        except BackupError:
            report['badHmacStartsNoChild'] = len(store.load()['children']) == 2
        cipher.close()
    import io, tarfile
    from backup import IMAGE_KEYS, VOLUME_SOURCES, rewrite_authenticated_control
    from owned_backup_verify import ControlledVerifier
    target = Path('/tmp/verify-target')
    target.mkdir(mode=0o700)
    release = target / 'releases' / '0.2.0'
    release.mkdir(mode=0o700, parents=True)
    images = {name: 'synthetic/' + name.lower() + '@sha256:' + 'a' * 64 for name in IMAGE_KEYS}
    release_bytes = json.dumps({'version': '0.2.0', 'minimumSourceVersion': '0.1.0',
        'database': {'schema': '920', 'compatibleSourceSchemas': ['920']}, 'images': images}).encode()
    (release / 'release.json').write_bytes(release_bytes)
    (release / '.env').write_text('PUBLIC_HOST=synthetic.invalid\n')
    (release / 'compose.yml').write_text('services: {}\n')
    (target / 'current').write_text('0.2.0\n')
    payload_root = target / 'backups' / 'synthetic'
    payload_root.mkdir(mode=0o700, parents=True)
    store = PrivateStore(root, name='runtime-child-' + uuid.uuid4().hex + '.json')
    journal = ChildJournal(store, guard, operation_id=str(uuid.uuid4()), installation_id=str(uuid.uuid4()))
    with AnonymousWorkspace(max_bytes=1024 * 1024) as workspace:
        cipher = ControlledCipher(workspace, OpenSSLProcess(journal, guard), payload_root, b'd' * 64)
        tar = io.BytesIO()
        with tarfile.open(fileobj=tar, mode='w') as archive:
            member = tarfile.TarInfo('synthetic.txt'); member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
        payloads = dict({'postgres.dump': b'PGDMP\x00synthetic', 'mariadb.sql': b'-- MariaDB dump\n'},
            **{name: tar.getvalue() for name in VOLUME_SOURCES})
        for name, data in payloads.items():
            plain = workspace / ('input-' + name)
            with plain.open('xb') as handle:
                handle.write(data)
            cipher.encrypt(plain, payload_root / (name + '.enc'), cipher.key)
            plain.unlink()
        rewrite_authenticated_control(payload_root, cipher.key, {'schemaVersion': 2,
            'version': '0.2.0', 'databaseSchema': '920', 'releaseManifestSha256': hashlib.sha256(release_bytes).hexdigest(),
            'images': images, 'files': {}})
        metadata = ControlledVerifier(target, cipher).verify_only(payload_root)
        report['allSyntheticPayloadFormatsVerified'] = (metadata['version'] == '0.2.0'
            and len(store.load()['children']) == 2 * len(payloads)
            and all(item['phase'] == 'completed' for item in store.load()['children']))
        cipher.close()
    # The legacy/source-only check above does not qualify a recovery target.
    # Now exercise #140 with the real sealed memfd/OpenSSL path, retaining an
    # explicit synthetic release/SQL format scope and no database import.
    from restore import RestoreIntegrityError, EXPECTED_PAYLOADS, CONTROL_FILES
    source_release = json.loads(release_bytes)
    source_release['securityCapabilities'] = {'platform-admin-session-v1': images['PLATFORM_IMAGE']}
    release_bytes = json.dumps(source_release).encode()
    (release / 'release.json').write_bytes(release_bytes)
    (release / '.env').write_text('PLATFORM_ADMIN_ENFORCE_PERSONNEL=true\n')
    (target / '.surveyctl').mkdir(mode=0o700)
    (target / '.surveyctl/auth-security-floor').write_text('platform-admin-session-v1\n')
    with AnonymousWorkspace(max_bytes=1024 * 1024) as workspace:
        cipher = ControlledCipher(workspace, None, payload_root, b'd' * 64)
        metadata['releaseManifestSha256'] = hashlib.sha256(release_bytes).hexdigest()
        rewrite_authenticated_control(payload_root, cipher.key, metadata)
        cipher.close()
    candidate = json.loads(Path('/tmp/code/tests/fixtures/release.json').read_text())
    candidate.update(version='0.3.0', images=images,
        securityCapabilities={'platform-admin-session-v1': images['PLATFORM_IMAGE']})
    for architecture, bundle in candidate['assets']['bundles'].items():
        bundle['name'] = 'survey-0.3.0-linux-' + architecture + '.tar.gz'
    candidate_path = target / 'recovery-target.json'

    def assess(patch=None, *, wrong_hash=False, corrupt_control=False):
        value = {**candidate, **(patch or {})}
        candidate_path.write_text(json.dumps(value, sort_keys=True))
        expected = '0' * 64 if wrong_hash else hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        control = payload_root / 'CONTROL-HMAC'
        original = control.read_bytes()
        if corrupt_control: control.write_text('0' * 64 + '\n')
        store = PrivateStore(root, name='runtime-child-' + uuid.uuid4().hex + '.json')
        journal = ChildJournal(store, guard, operation_id=str(uuid.uuid4()), installation_id=str(uuid.uuid4()))
        try:
            with AnonymousWorkspace(max_bytes=1024 * 1024) as workspace:
                cipher = ControlledCipher(workspace, OpenSSLProcess(journal, guard), payload_root, b'd' * 64)
                verifier = ControlledVerifier(target, cipher)
                try:
                    outcome = ('verified', verifier.verify_target_compatibility(payload_root, candidate_path, expected))
                except RestoreIntegrityError as error:
                    outcome = ('rejected', str(error))  # Compared locally, never emitted.
                denied = False
                try: verifier.restore(payload_root)
                except RestoreIntegrityError: denied = True
                rows = (store.load() or {}).get('children', [])
                ended = not cipher.uncertain and all(row['phase'] == 'completed' for row in rows)
                cipher.close()
                return outcome, len(rows), ended, denied
        finally:
            if corrupt_control: control.write_bytes(original)

    outcome, count, ended, denied = assess()
    report['recoveryTargetMetadataVerified'] = (outcome[0] == 'verified' and count == len(payloads) and ended
        and outcome[1]['sourcePayloadsVerified'] is True and outcome[1]['targetMetadataCompatible'] is True
        and all(outcome[1][key] is False for key in ('targetSignatureVerified', 'targetConfigurationVerified', 'restoreAuthorized')))
    report['recoveryRestoreRemainsDisabled'] = denied
    outcome, count, ended, _ = assess(wrong_hash=True)
    report['recoveryTargetHashMismatchRejected'] = (outcome == ('rejected', 'recovery target manifest binding is invalid') and count == 0 and ended)
    outcome, count, ended, _ = assess({'minimumSourceVersion': '0.2.1'})
    report['recoveryTargetVersionRejected'] = (outcome == ('rejected', 'backup version is incompatible with the recovery target') and count == len(payloads) and ended)
    outcome, count, ended, _ = assess({'securityCapabilities': {}})
    report['recoveryTargetFloorRejected'] = (outcome == ('rejected', 'recovery target would downgrade the personnel security floor') and count == len(payloads) and ended)
    outcome, count, ended, _ = assess(corrupt_control=True)
    report['recoveryBadControlHmacStartsNoChild'] = (outcome == ('rejected', 'backup control authentication failed') and count == 0 and ended)
    report['recoveryNoNamedPayload'] = ({path.name for path in payload_root.iterdir()} == EXPECTED_PAYLOADS | CONTROL_FILES
        and not any(path.name in payloads for path in target.rglob('*')))
    report['noNamedPlaintext'] = (all(path.name in ('code', 'receipts', 'encrypted', 'verify-target') for path in Path('/tmp').iterdir())
        and {path.name for path in encrypted_root.iterdir()} == {'postgres.dump.enc'})
    report['sourceHashes'] = {name: hashlib.sha256((Path('/tmp/code') / name).read_bytes()).hexdigest() for name in MODULES}
    print(json.dumps(report, sort_keys=True))
    return 0 if all(report[key] is True for key in CHECKS) else 1


def host(image):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image) or image.startswith('-'):
        raise SystemExit('a local digest-pinned Python image is required')
    source_root = Path(__file__).resolve().parents[2]
    sources = {name: (source_root / name).read_text() for name in MODULES}
    hashes = {name: hashlib.sha256(value.encode()).hexdigest() for name, value in sources.items()}
    name, cid = 'survey-crypto-fixture-' + uuid.uuid4().hex, None
    command = ['docker', '--host', 'unix:///var/run/docker.sock']
    report = {'scope': 'controlled-crypto-only', 'containerCleanup': 'unknown', 'sourceBefore': hashes}
    def docker(args):
        return subprocess.run(command + args, check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    def inspect():
        item = json.loads(docker(['inspect', cid]))[0]
        config, runtime = item['Config'], item['HostConfig']
        if (item['Id'] != cid or item['Name'] != '/' + name or config['Image'] != image
                or config['Labels'].get('io.survey.fixture') != name or config['User'] != '65534:65534'
                or runtime['NetworkMode'] != 'none' or runtime['Binds'] or runtime['Privileged']
                or not runtime['ReadonlyRootfs'] or runtime['PidMode'] not in ('', 'private')
                or runtime['CapDrop'] != ['ALL'] or runtime['CapAdd'] or runtime['PortBindings']
                or runtime['Devices'] or runtime['PidsLimit'] != 32 or runtime['Memory'] != 134217728
                or runtime['NanoCpus'] != 1000000000 or runtime['SecurityOpt'] != ['no-new-privileges:true']
                or runtime['Tmpfs'] != {'/tmp': 'rw,nosuid,nodev,noexec,size=16m'}):
            raise RuntimeError('fixture identity or isolation differs')
        return item['State']['Running']
    stage = 'image'
    try:
        if json.loads(docker(['image', 'inspect', '--format', '{{json .Config.Volumes}}', image])) not in (None, {}):
            raise RuntimeError('implicit image volumes are forbidden')
        stage = 'create'
        cid = docker(['create', '--pull=never', '--name', name, '--label', 'io.survey.fixture=' + name,
            '--init', '--network', 'none', '--read-only', '--user', '65534:65534', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--pids-limit', '32', '--memory', '128m', '--cpus', '1',
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=16m', '-i', '--entrypoint', 'python3', image, '-B', '-'])
        if not re.fullmatch(r'[0-9a-f]{64}', cid):
            raise RuntimeError('creation identity unavailable')
        stage = 'inspect'
        inspect()
        payload = ('import os\nfrom pathlib import Path\np=Path("/tmp/code");p.mkdir(mode=0o700)\n'
            + 'sources=' + repr(sources) + '\nfor n,s in sources.items():\n (p/n).parent.mkdir(parents=True,exist_ok=True)\n (p/n).write_text(s)\n (p/n).chmod(0o600)\n'
            + 'exec(compile(' + repr(Path(__file__).read_text()) + ',"<crypto-probe>","exec"))\n')
        stage = 'execute'
        result = subprocess.run(command + ['start', '-ai', cid], input=payload, text=True,
            capture_output=True, timeout=60)
        stage = 'evidence'
        evidence = json.loads(result.stdout)
        if (set(evidence) != {*CHECKS, 'sourceHashes', 'roundTripExitCodes'} or evidence['sourceHashes'] != hashes
                or not isinstance(evidence['roundTripExitCodes'], list) or len(evidence['roundTripExitCodes']) != 2
                or any(type(code) is not int or not -64 <= code <= 255 for code in evidence['roundTripExitCodes'])
                or any(type(evidence[key]) is not bool for key in CHECKS)):
            raise RuntimeError('crypto evidence is not bound to source')
        report.update(exitCode=result.returncode, evidence=evidence)
    except Exception:
        report['failure'] = 'crypto-evidence-unavailable'
        report['failureStage'] = stage
    finally:
        if cid and re.fullmatch(r'[0-9a-f]{64}', cid):
            try:
                if inspect():
                    docker(['stop', '--time', '5', cid])
                docker(['rm', cid])
                if docker(['ps', '-aq', '--no-trunc', '--filter', 'id=' + cid]):
                    raise RuntimeError('fixture remains')
                report['containerCleanup'] = 'verified'
            except Exception:
                report['containerCleanup'] = 'failed-or-unknown'
    report['sourceAfter'] = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in MODULES}
    print(json.dumps(report, sort_keys=True))
    return 0 if passed(report, hashes) else 1


if __name__ == '__main__':
    if Path('/tmp/code/owned_crypto.py').is_file() and sys.argv[1:] == []:
        raise SystemExit(inside())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    raise SystemExit(host(parser.parse_args().image))
