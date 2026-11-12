"""Real new-copy PG/Maria imports of synthetic rows, never product restore.

Four fresh DB containers/two owned namespaces, no host bind/ports/network/apps.
Local-trust DB auth, synchronous fixture exec and host-memory dumps are explicit
models/gaps; no signed backup, controlled stdin receipt or business qualification.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from owned_docker_exec import LocalDockerAPI
from runtime_docker import APIOwnershipDriver
from runtime_ownership import OwnershipProtocol, PrivateStore
from restore import RestoreManager
from recovery_namespace_native import Runner, empty_namespace

CHECKS = ('targetWasFresh', 'resourceIsolationVerified', 'postgresDataVerified', 'mariadbDataVerified',
          'postSnapshotWritesPreserved', 'sourcePersonnelUnchanged', 'targetPersonnelInvalidated',
          'targetRestoreAuditRecorded', 'applicationsNeverStarted', 'dumpTransportHostMemoryOnly',
          'cohortProbeIsModel', 'pendingIntentsRetainedDuringCopy')
UNQUALIFIED = ('sourceAuthenticationVerified', 'targetSignatureVerified', 'controlledExecReceiptsCovered',
               'restoreAuthorized', 'releaseEligible', 'crossDatabaseTransactionVerified',
               'productionAuthenticationVerified', 'namedHostDumpWritten')


class CopyError(Exception): pass


class FixtureIntent:
    """Test-owned pending marker; never a product recovery permission token."""
    def __init__(self, path, project):
        self.path, self.fd = path, None
        self.data = (json.dumps({'schemaVersion': 1, 'state': 'pending',
            'operation': 'recovery_database_fixture', 'project': project}, sort_keys=True) + '\n').encode()
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            if os.write(self.fd, self.data) != len(self.data): raise CopyError('fixture intent write incomplete')
            os.fsync(self.fd); os.fsync(parent)
            self.identity = self.snapshot(os.fstat(self.fd))
            if not self.check(): raise CopyError('fixture intent publication unverified')
        except BaseException:
            self.close()
            raise
        finally: os.close(parent)

    @staticmethod
    def snapshot(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def check(self):
        try:
            if self.fd is None: return False
            info = os.fstat(self.fd)
            return (stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_uid == os.geteuid() and info.st_nlink == 1
                and self.snapshot(info) == self.snapshot(self.path.lstat()) == self.identity
                and os.pread(self.fd, len(self.data) + 1, 0) == self.data)
        except OSError: return False

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None: os.close(fd)  # A failed close is never retried.


def require_fresh_target(fresh, isolated, tools):
    if fresh is not True or isolated is not True:
        tools.uncertain = True
        raise CopyError('new target is not demonstrably fresh and isolated')


class FixtureExec:
    """Synchronous test tool only. Unknown/nonzero results poison all later use."""
    def __init__(self, runner=subprocess.run):
        self.runner, self.uncertain = runner, False
        self.diagnostic = None

    def may_cleanup(self): return not self.uncertain

    def execute(self, cid, argv, data=None):
        if (self.uncertain or not isinstance(cid, str) or re.fullmatch('[0-9a-f]{64}', cid) is None
                or not isinstance(argv, list) or not argv or argv[0] not in
                {'psql', 'pg_dump', 'pg_restore', 'mariadb', 'mariadb-dump'}
                or (data is not None and not isinstance(data, bytes))):
            raise CopyError('fixture command is not admissible')
        self.uncertain = True
        self.diagnostic = {'tool': argv[0], 'kind': 'exception', 'exitCode': None, 'sqlstate': None}
        try:
            result = self.runner(['docker', '--host', 'unix:///var/run/docker.sock', 'exec', '-i', cid, *argv],
                input=data, capture_output=True, timeout=45, check=False)
            if type(result.returncode) is int and -255 <= result.returncode <= 255:
                self.diagnostic['exitCode'] = result.returncode
            if isinstance(result.stderr, bytes):
                state = re.search(rb'(?:ERROR|FATAL):\s+([0-9A-Z]{5})(?:\s|$)', result.stderr)
                if state: self.diagnostic['sqlstate'] = state[1].decode('ascii')
            if type(result.returncode) is not int or result.returncode != 0:
                self.diagnostic['kind'] = 'command-nonzero'
                raise CopyError('fixture exec failed or its outcome is unknown')
            if (not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes)
                    or len(result.stdout) > 32 * 1024**2):
                self.diagnostic['kind'] = 'invalid-output'
                raise CopyError('fixture exec failed or its outcome is unknown')
            self.uncertain = False
            self.diagnostic = None
            return result.stdout
        except BaseException as exc:
            if isinstance(exc, subprocess.TimeoutExpired): self.diagnostic['kind'] = 'timeout'
            raise CopyError('fixture exec failed or its outcome is unknown') from exc


def passed(report, hashes):
    return (type(report.get('schemaVersion')) is int and report['schemaVersion'] == 1
        and report.get('status') == 'passed' and report.get('fixtureCleanup') == 'verified'
        and report.get('sourceBefore') == report.get('sourceAfter') == hashes
        and all(report.get(key) is True for key in CHECKS)
        and all(report.get(key) is False for key in UNQUALIFIED))


def migrations():
    return sorted((ROOT.parents[2] / 'platform/services/business/src/main/resources/db/migration').glob('V*.sql'),
                  key=lambda p: int(p.name.split('__')[0][1:]))


def hashes():
    values = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
        ('runtime_ownership.py', 'runtime_docker.py', 'owned_docker_exec.py', 'restore.py', 'backup.py',
         'tests/integration/recovery_database_native.py', 'tests/integration/recovery_namespace_native.py')}
    values['migrationInventory'] = hashlib.sha256(json.dumps([(p.name, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in migrations()], separators=(',', ':')).encode()).hexdigest()
    return values


def run(claim_image, pg_image, maria_image):
    api, tools = LocalDockerAPI(), FixtureExec()
    refs = {'platform-db': pg_image, 'engine-db': maria_image}
    destinations = {'platform-db': '/var/lib/postgresql/data', 'engine-db': '/var/lib/mysql'}
    info = {}
    for name, ref in {**refs, 'claim': claim_image}.items():
        if re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', ref) is None: raise CopyError('fixed local images required')
        info[name] = api._json('GET', '/images/' + quote(ref, safe='') + '/json')
        expected = set() if name == 'claim' else {destinations[name]}
        if info[name].get('Os') != 'linux' or set(info[name].get('Config', {}).get('Volumes') or {}) != expected:
            raise CopyError('image implicit volumes differ from explicit fixture contract')
        if (re.fullmatch('sha256:[0-9a-f]{64}', info[name].get('Id', '')) is None
                or info[name].get('Architecture') not in {'amd64', 'arm64'}):
            raise CopyError('image identity or architecture is invalid')
    if len({value['Architecture'] for value in info.values()}) != 1: raise CopyError('mixed image architectures')
    before = hashes()
    root = Path(tempfile.mkdtemp(prefix='survey-new-copy-db-')).resolve()
    protocols, locks, projects, containers, intents = [], [], [], [], []
    report = dict(schemaVersion=1, scope='new-copy-real-database-synthetic-rows', status='failed',
        fixtureCleanup='unknown', sourceBefore=before, nativeArchitecture=info['platform-db']['Architecture'],
        **{key: False for key in CHECKS}, **{key: False for key in UNQUALIFIED})
    report.update(cohortProbeIsModel=True, dumpTransportHostMemoryOnly=True)
    report['images'] = {**refs, 'claim': claim_image}
    report['imageIds'] = {name: value['Id'] for name, value in info.items()}
    stage = 'initialization'
    def query(pair, kind, text):
        argv = (['psql', '-U', 'postgres', '-d', 'platform', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=sqlstate', '-qAt']
            if kind == 'platform-db' else ['mariadb', '-uroot', '-N', '-B', 'limesurvey'])
        return tools.execute(pair[kind], argv, text.encode())

    try:
        for index in range(2):
            project = 'survey-recovery-' + uuid.uuid4().hex; projects.append(project)
            target = root / str(index); target.mkdir(mode=0o700)
            control = target / '.surveyctl'; control.mkdir(mode=0o700)
            intents.append(FixtureIntent(control / 'operation.intent', project))
            fd = os.open(control / 'operation.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            locks.append(fd); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            volumes = {project + '_' + name: {'logical': name, 'driver': 'local'} for name in refs}
            contract = {'services': {name: {'imageIds': [info[name]['Id']], 'workingDirs': [str(target)],
                'mounts': [{'type': 'volume', 'name': project + '_' + name,
                            'target': destinations[name], 'readOnly': False}]} for name in refs},
                'volumes': volumes, 'networks': {}}
            driver = APIOwnershipDriver(Runner(), contract,
                lambda: 'quiescent' if tools.may_cleanup() else 'unknown', api=api, project=project)
            protocol = OwnershipProtocol(target, driver, PrivateStore(target), claim_image, project=project)
            protocols.append(protocol); pair = {}; containers.append(pair)
            stage = 'claim-' + str(index); protocol.acquire()
            def create():
                labels = {'com.docker.compose.project': project, 'io.survey.installation': driver.binding['installationId']}
                for name in refs:
                    value = api._json('POST', '/volumes/create', {'Name': project + '_' + name, 'Driver': 'local',
                        'Labels': {**labels, 'com.docker.compose.volume': name}})
                    if value.get('Name') != project + '_' + name: raise CopyError('volume create unknown')
                    env = (['POSTGRES_USER=postgres', 'POSTGRES_DB=platform', 'POSTGRES_HOST_AUTH_METHOD=trust']
                           if name == 'platform-db' else ['MARIADB_DATABASE=limesurvey', 'MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1'])
                    health = (['CMD-SHELL', "pg_isready -h 127.0.0.1 -U postgres -d platform && psql -U postgres -d platform -Atqc 'SELECT 1' | grep -qx 1"]
                              if name == 'platform-db' else ['CMD', 'healthcheck.sh', '--connect', '--innodb_initialized'])
                    value = api._json('POST', '/containers/create?name=' + project + '-' + name + '-1', {
                        'Image': refs[name], 'Env': env,
                        'Labels': {**labels, 'com.docker.compose.service': name, 'com.docker.compose.project.working_dir': str(target)},
                        'Healthcheck': {'Test': health, 'Interval': 2_000_000_000, 'Timeout': 3_000_000_000, 'Retries': 60},
                        'HostConfig': {'NetworkMode': 'none', 'SecurityOpt': ['no-new-privileges:true'],
                            'Memory': 512 * 1024**2, 'NanoCpus': 1_000_000_000, 'PidsLimit': 128,
                            'Mounts': [{'Type': 'volume', 'Source': project + '_' + name, 'Target': destinations[name], 'ReadOnly': False}]}})
                    cid = value.get('Id')
                    if not isinstance(cid, str) or re.fullmatch('[0-9a-f]{64}', cid) is None: raise CopyError('container create unknown')
                    pair[name] = cid; api._json('POST', '/containers/' + cid + '/start', accepted=(204,))
                deadline = time.monotonic() + 120
                while True:
                    states = [api._json('GET', '/containers/' + cid + '/json')['State'] for cid in pair.values()]
                    if all(row.get('Running') is True and row.get('Health', {}).get('Status') == 'healthy' for row in states): break
                    if time.monotonic() >= deadline: raise CopyError('fixture databases not healthy')
                    time.sleep(.5)
            stage = 'create-' + str(index); protocol.mutate('install', create)
            query(pair, 'platform-db', 'CREATE ROLE platform_owner LOGIN NOSUPERUSER CREATEDB NOBYPASSRLS; '
                'CREATE ROLE platform_app LOGIN NOSUPERUSER NOCREATEDB NOBYPASSRLS; '
                'ALTER DATABASE platform OWNER TO platform_owner; GRANT CONNECT ON DATABASE platform TO platform_app;')
        source, target = containers
        report['resourceIsolationVerified'] = all(not set(protocols[0].store.load()['resources'][kind]) &
            set(protocols[1].store.load()['resources'][kind]) for kind in ('containers', 'volumes'))
        report['targetWasFresh'] = (query(target, 'platform-db', "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';").strip() == b'0'
            and query(target, 'engine-db', "SELECT count(*) FROM information_schema.tables WHERE table_schema='limesurvey';").strip() == b'0')
        require_fresh_target(report['targetWasFresh'], report['resourceIsolationVerified'], tools)
        if not all(intent.check() for intent in intents): raise CopyError('fixture intent changed')
        stage = 'source-migrations'
        files = migrations()
        if not files: raise CopyError('actual migrations unavailable')
        for path in files:
            report['migrationFile'] = path.name
            query(source, 'platform-db', 'SET ROLE platform_owner;\n' + path.read_text())
        del report['migrationFile']
        report['actualMigrationsApplied'] = len(files)
        tenant, connection, actor, session, answer, after = [str(uuid.uuid4()) for _ in range(6)]
        query(source, 'platform-db', f"""
INSERT INTO tenant(id,code,name,status) VALUES('{tenant}','copy-fixture','copy fixture','provisioning');
UPDATE tenant SET status='active' WHERE id='{tenant}';
INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by)
VALUES('{connection}','{tenant}','wecom','fixture-corp','fixture-app','TEST','jit_staff',true,'fixture');
INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id) VALUES('{actor}','{tenant}','wecom','fixture-corp','first');
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by) VALUES('{tenant}','{actor}','active',true,'fixture');
INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at) VALUES('{session}','{tenant}','{actor}','{connection}',now()+interval '1 hour');
SELECT platform_admin_bootstrap_attributed('{tenant}','{actor}','fixture-bootstrap','copy fixture','fixture-operator');
SET ROLE platform_owner;
CREATE TABLE fixture_recovery_response(id uuid PRIMARY KEY, answer text NOT NULL);
INSERT INTO fixture_recovery_response VALUES('{answer}','synthetic snapshot answer');
""")
        query(source, 'engine-db', f"CREATE TABLE fixture_recovery_response(id char(36) PRIMARY KEY,answer text NOT NULL); INSERT INTO fixture_recovery_response VALUES('{answer}','synthetic snapshot answer');")
        stage = 'memory-dump'
        pg = tools.execute(source['platform-db'], ['pg_dump', '-U', 'postgres', '--format=custom', '--serializable-deferrable', 'platform'])
        maria = tools.execute(source['engine-db'], ['mariadb-dump', '-uroot', '--single-transaction', '--quick', '--skip-lock-tables', 'limesurvey'])
        report['snapshotDigests'] = {'postgres': hashlib.sha256(pg).hexdigest(), 'mariadb': hashlib.sha256(maria).hexdigest()}
        for kind in refs: query(source, kind, f"INSERT INTO fixture_recovery_response VALUES('{after}','source post snapshot answer');")
        stage = 'new-copy-import'
        tools.execute(target['platform-db'], ['pg_restore', '-U', 'postgres', '-d', 'platform', '--single-transaction', '--exit-on-error', '--clean', '--if-exists'], pg)
        tools.execute(target['engine-db'], ['mariadb', '-uroot', 'limesurvey'], maria)
        pg, maria = None, None  # No named dump file; this is not a zeroization claim.
        check = f"SELECT answer FROM fixture_recovery_response WHERE id='{answer}';"
        report['postgresDataVerified'] = query(target, 'platform-db', check).strip() == b'synthetic snapshot answer'
        report['mariadbDataVerified'] = query(target, 'engine-db', check).strip() == b'synthetic snapshot answer'
        report['postSnapshotWritesPreserved'] = all(query(source, kind, 'SELECT count(*) FROM fixture_recovery_response;').strip() == b'2'
            and query(target, kind, 'SELECT count(*) FROM fixture_recovery_response;').strip() == b'1' for kind in refs)
        grant = f"SELECT platform_admin_active('{tenant}','{actor}');"
        active_session = f"SELECT revoked_at IS NULL FROM org_login_session WHERE id='{session}';"
        if query(target, 'platform-db', grant).strip() != b't' or query(target, 'platform-db', active_session).strip() != b't':
            raise CopyError('target invalidation positive control missing')
        stage = 'target-personnel-invalidation'
        class InvalidationRunner:
            def run(self, args, **kwargs):
                if args[:4] != ['fixture-copy', 'exec', '-T', 'platform-db'] or args[4] != 'psql': raise CopyError('unexpected reset command')
                value = tools.execute(target['platform-db'], args[4:], kwargs['input'].encode())
                return subprocess.CompletedProcess(args, 0, value.decode(), '')
        RestoreManager(root / '1', runner=InvalidationRunner())._invalidate_restored_personnel(['fixture-copy'])
        report['targetPersonnelInvalidated'] = (query(target, 'platform-db', grant).strip() == b'f'
            and query(target, 'platform-db', active_session).strip() == b'f')
        report['sourcePersonnelUnchanged'] = (query(source, 'platform-db', grant).strip() == b't'
            and query(source, 'platform-db', active_session).strip() == b't')
        report['targetRestoreAuditRecorded'] = query(target, 'platform-db', "SELECT count(*) FROM platform_security_restore_audit r JOIN platform_admin_audit a USING(trace_id) WHERE a.action='identity_invalidated' AND a.operator_db_user='postgres' AND r.operator_db_user='postgres';").strip() == b'1'
        for protocol in protocols: protocol.preflight_cleanup(True)
        report['applicationsNeverStarted'] = True  # Closed topology contains only the two DB services.
        report['pendingIntentsRetainedDuringCopy'] = len(intents) == 2 and all(intent.check() for intent in intents)
        report['namedHostDumpWritten'] = any(path.is_file() and path.name not in
            {'operation.lock', 'operation.intent', 'runtime-owner.json'} for path in root.rglob('*'))
        report['status'] = 'passed'
    except Exception:
        report['failureStage'] = stage  # Never emit SQL, rows, raw stderr or dumps.
        if tools.diagnostic is not None: report['execDiagnostic'] = tools.diagnostic
    finally:
        safe_cleanup = tools.may_cleanup() and len(intents) == len(projects) and all(intent.check() for intent in intents)
        clean = safe_cleanup
        for protocol in reversed(protocols):
            try:
                record = protocol.store.load()
                if safe_cleanup and record and record['phase'] in ('ready', 'retained-data', 'settled-failure'):
                    protocol.cleanup(True)
                elif not record or record['phase'] != 'removed': clean = False
            except Exception: clean = False
        for project in projects:
            if not empty_namespace(api, project): clean = False
        for fd in reversed(locks):
            try: os.close(fd)
            except OSError: clean = False
        for intent in reversed(intents):
            try: intent.close()
            except OSError: clean = False
        report['sourceAfter'] = hashes()
        if clean:
            try: shutil.rmtree(root)
            except OSError: clean = False
        report['fixtureCleanup'] = 'verified' if clean else 'failed-or-unknown'
        report['privateStateRetained'] = not clean
        if not clean: report['retainedFixtureRoot'] = str(root)
        if not passed(report, before): report['status'] = 'failed'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--claim-image', required=True); parser.add_argument('--postgres-image', required=True); parser.add_argument('--mariadb-image', required=True)
    args = parser.parse_args()
    try: report = run(args.claim_image, args.postgres_image, args.mariadb_image)
    except Exception: report = {'schemaVersion': 1, 'status': 'failed', 'failureStage': 'preflight', 'fixtureCleanup': 'not-started'}
    print(json.dumps(report, sort_keys=True)); sys.exit(0 if report['status'] == 'passed' else 1)
