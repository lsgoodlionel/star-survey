"""Actual HMAC/OpenSSL/memfd -> fixed new-copy PG/Maria consumers.

Fixture setup/queries/personnel reset still use synchronous test tools. No app,
product restore, release signature/config approval, or whole-host qualification.
"""
import argparse
import io
import secrets
import tarfile
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
from owned_docker_exec import LocalDockerAPI, KnownExecFailure
from owned_children import ControllerProcessGuard, ChildJournal
from owned_memory import AnonymousWorkspace
from owned_crypto import OpenSSLProcess
from owned_cipher_adapter import ControlledCipher
from owned_backup_verify import ControlledVerifier
from controlled_database_import import ControlledDatabaseCopy, NewCopyTarget, OPERATIONS, ISOLATION_FAILURES
from recovery_reservation import RecoveryReservation
from runtime_cohort import CohortProbe
from backup import IMAGE_KEYS, VOLUME_SOURCES, rewrite_authenticated_control
from runtime_docker import APIOwnershipDriver
from runtime_ownership import OwnershipProtocol, PrivateStore
from restore import RestoreManager, RestoreIntegrityError
from recovery_namespace_native import Runner, empty_namespace

CHECKS = ('targetWasFresh', 'resourceIsolationVerified', 'postgresDataVerified', 'mariadbDataVerified',
          'postSnapshotWritesPreserved', 'sourcePersonnelUnchanged', 'targetPersonnelInvalidated',
          'targetRestoreAuditRecorded', 'applicationsNeverStarted', 'dumpTransportHostMemoryOnly',
          'cohortProbeIsModel', 'pendingIntentsRetainedDuringCopy')
UNQUALIFIED = ('sourceAuthenticationVerified', 'targetSignatureVerified', 'controlledExecReceiptsCovered',
               'restoreAuthorized', 'releaseEligible', 'crossDatabaseTransactionVerified',
               'productionAuthenticationVerified', 'namedHostDumpWritten', 'candidateQualified', 'targetConfigurationVerified')


AUTH_QUERY = 'IFS= read -r password || exit 70; [ -n "$password" ] || exit 70; MYSQL_PWD="$password" exec mariadb --binary-mode -u root -N -B limesurvey'
INIT_SECRET = ("IFS= read -r password || exit 70; mkdir -p /run/secrets; umask 077; "
    "printf '%s\\n' \"$password\" > /run/secrets/engine_db_root_password; "
    "printf \"ALTER USER 'root'@'localhost' IDENTIFIED BY '%s';\\n\" \"$password\" | mariadb -uroot limesurvey")
REPLACE_SECRET = 'IFS= read -r password || exit 70; umask 077; printf "%s\\n" "$password" > /run/secrets/engine_db_root_password'


def fixture_shell(tools, cid, script, data):
    if tools.uncertain or script not in (AUTH_QUERY, INIT_SECRET, REPLACE_SECRET):
        raise CopyError('fixture shell is not admissible')
    tools.uncertain = True
    result = subprocess.run(['docker','--host','unix:///var/run/docker.sock','exec','-i',cid,'/bin/sh','-c',script],
        input=data,capture_output=True,timeout=30,check=False)
    if result.returncode != 0 or not isinstance(result.stdout,bytes) or len(result.stdout)>1024*1024:
        raise CopyError('fixture setup/query result unknown')
    tools.uncertain = False
    return result.stdout


class HandoffIntent:
    def __init__(self, handoff): self.handoff=handoff
    def check(self):
        try: return self.handoff.inspect()['executable'] is False
        except Exception: return False
    def close(self): self.handoff.close()


def reserve_fixture(path, project, observation, receipts):
    control=path/'.surveyctl'; control.mkdir(mode=0o700)
    records={'operation.lock':b'', 'operation.intent':json.dumps({'schemaVersion':1,'state':'pending','operation':'recovery_reserve'}).encode(),
        'recovery.reservation.json':json.dumps({'schemaVersion':1,'state':'reserved-not-authorized','executable':False,
            'reservationId':project.removeprefix('survey-recovery-'),'projectProposal':project,
            'sourceObservationSha256':observation,'targetIdentity':[path.stat().st_dev,path.stat().st_ino],
            'controlIdentity':[control.stat().st_dev,control.stat().st_ino],
            'backupAuthenticated':False,'runtimeResourcesCreated':False}).encode()}
    for name,value in records.items():
        fd=os.open(control/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            if os.write(fd,value)!=len(value):raise CopyError('reservation fixture write incomplete')
            os.fsync(fd)
        finally:os.close(fd)
    with RecoveryReservation(path,project.removeprefix('survey-recovery-'),observation) as guard:
        handoff=guard.handoff(receipts=receipts)
    return handoff


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
    common=('targetWasFresh','resourceIsolationVerified','postgresDataVerified','postSnapshotWritesPreserved',
        'sourcePersonnelUnchanged','applicationsNeverStarted','pendingIntentsRetainedDuringCopy',
        'backupHmacAuthenticated','controlledDatabaseInputReceipts','sourceCryptoChildrenEnded',
        'targetCohortUsesKernelReader','retryPrevented','badHmacStartsNoExec','badHmacStartsNoChild')
    outcome = (report.get('mariadbUntouched') is True and report.get('knownPartialCopy') is True
        if report.get('failMaria') else report.get('mariadbDataVerified') is True
            and report.get('targetPersonnelInvalidated') is True and report.get('targetRestoreAuditRecorded') is True)
    return (type(report.get('schemaVersion')) is int and report['schemaVersion']==1
        and type(report.get('failMaria')) is bool and report.get('status')=='passed' and report.get('fixtureCleanup')=='verified'
        and report.get('sourceBefore')==report.get('sourceAfter')==hashes and outcome
        and all(report.get(key) is True for key in common)
        and all(report.get(key) is False for key in UNQUALIFIED))


def migrations():
    return sorted((ROOT.parents[2] / 'platform/services/business/src/main/resources/db/migration').glob('V*.sql'),
                  key=lambda p: int(p.name.split('__')[0][1:]))


def hashes():
    values = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
        ('runtime_ownership.py', 'runtime_docker.py', 'owned_docker_exec.py', 'restore.py', 'backup.py',
         'controlled_database_import.py','owned_backup_verify.py','owned_memory.py','owned_cipher_adapter.py',
         'owned_crypto.py','owned_children.py','owned_crypto_child.py','owned_maintenance.py',
         'recovery_reservation.py','recovery_handoff.py','runtime_cohort.py',
         'tests/integration/authenticated_database_copy_native.py','tests/integration/recovery_namespace_native.py')}
    values['migrationInventory'] = hashlib.sha256(json.dumps([(p.name, hashlib.sha256(p.read_bytes()).hexdigest())
        for p in migrations()], separators=(',', ':')).encode()).hexdigest()
    return values


def cleanup_protocols(protocols, allowed):
    if allowed is not True: return False
    try:
        for protocol in protocols: protocol.preflight_cleanup(True)
    except Exception: return False
    for protocol in reversed(protocols):
        try: protocol.cleanup(True)
        except Exception: return False
    return True


def run(claim_image, pg_image, maria_image, fail_maria=False):
    class EvidenceAPI(LocalDockerAPI):
        def __init__(self): self.controlled_creates=0
        def create(self,cid,command,*,stdin=False):
            self.controlled_creates+=1
            return super().create(cid,command,stdin=stdin)
    api, tools = EvidenceAPI(), FixtureExec()
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
    handoff=None; target_probe=None; copier=None; cipher=None; workspace=None; crypto_store=None; bound=None; key=secrets.token_hex(32).encode()
    operations={name:str(uuid.uuid4()) for name in OPERATIONS}
    passwords={}
    report = dict(schemaVersion=1, scope='authenticated-new-copy-database-component', status='failed',
        fixtureCleanup='unknown', sourceBefore=before, nativeArchitecture=info['platform-db']['Architecture'],
        **{key: False for key in CHECKS}, **{key: False for key in UNQUALIFIED})
    report.update(cohortProbeIsModel=True, dumpTransportHostMemoryOnly=True, failMaria=fail_maria,
        fixtureSetupQueriesAndResetTracked=False, sourceReleaseSignaturesVerified=False, applicationLoginVerified=False,
        realUnknownExecFaultTested=False, realControllerCrashTested=False)
    report['images'] = {**refs, 'claim': claim_image}
    report['imageIds'] = {name: value['Id'] for name, value in info.items()}
    stage = 'initialization'
    def query(pair, kind, text):
        argv = (['psql', '-U', 'postgres', '-d', 'platform', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=sqlstate', '-qAt']
            if kind == 'platform-db' else ['mariadb', '-uroot', '-N', '-B', 'limesurvey'])
        if kind=='engine-db' and pair[kind] in passwords:
            return fixture_shell(tools,pair[kind],AUTH_QUERY,passwords[pair[kind]]+b'\n'+text.encode())
        return tools.execute(pair[kind], argv, text.encode())

    try:
        for index in range(2):
            project = 'survey-recovery-' + uuid.uuid4().hex; projects.append(project)
            target = root / str(index); target.mkdir(mode=0o700)
            if index == 1:
                observation=hashlib.sha256(json.dumps(protocols[0].store.load(),sort_keys=True).encode()).hexdigest()
                handoff=reserve_fixture(target,project,observation,
                    tuple('runtime-exec-'+value.replace('-','')+'.json' for value in operations.values()))
                intents.append(HandoffIntent(handoff))
                fd=handoff._files['operation.lock'][0]
                target_probe=CohortProbe(target,fd,lambda:handoff._active,api=api,fresh=True,project=project)
            else:
                control=target/'.surveyctl';control.mkdir(mode=0o700)
                intents.append(FixtureIntent(control/'operation.intent',project))
                fd=os.open(control/'operation.lock',os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(fd)
            volumes = {project + '_' + name: {'logical': name, 'driver': 'local'} for name in refs}
            contract = {'services': {name: {'imageIds': [info[name]['Id']], 'workingDirs': [str(target)],
                'mounts': [{'type': 'volume', 'name': project + '_' + name,
                            'target': destinations[name], 'readOnly': False}]} for name in refs},
                'volumes': volumes, 'networks': {}}
            driver = APIOwnershipDriver(Runner(), contract,
                (target_probe if index==1 else lambda: 'quiescent' if tools.may_cleanup() else 'unknown'), api=api, project=project)
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
        password=secrets.token_hex(24).encode()
        fixture_shell(tools,target['engine-db'],INIT_SECRET,password+b'\n')
        passwords[target['engine-db']]=password
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
        stage = 'authenticated-backup-build'
        source_root=root/'0';release=source_root/'releases/0.2.0';release.mkdir(parents=True,mode=0o700)
        images={name:'synthetic/'+name.lower()+'@sha256:'+'a'*64 for name in IMAGE_KEYS}
        schema=str(int(files[-1].name.split('__')[0][1:]))
        release_bytes=json.dumps({'version':'0.2.0','minimumSourceVersion':'0.1.0',
            'database':{'schema':schema,'compatibleSourceSchemas':[schema]},'images':images}).encode()
        (release/'release.json').write_bytes(release_bytes);(release/'compose.yml').write_text('services: {}\n')
        (release/'.env').write_text('PUBLIC_HOST=synthetic.invalid\n');(source_root/'current').write_text('0.2.0\n')
        backup=source_root/'backups/snapshot';backup.mkdir(parents=True,mode=0o700)
        guard=ControllerProcessGuard()
        crypto_store=PrivateStore(source_root,name='runtime-child-'+uuid.uuid4().hex+'.json')
        journal=ChildJournal(crypto_store,guard,operation_id=str(uuid.uuid4()),
            installation_id=protocols[0].store.load()['installationId'])
        workspace=AnonymousWorkspace(max_bytes=64*1024*1024,max_files=64)
        cipher=ControlledCipher(workspace,OpenSSLProcess(journal,guard),backup,key)
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w') as tar:
            member=tarfile.TarInfo('synthetic.txt');member.size=1;tar.addfile(member,io.BytesIO(b'x'))
        payloads={'postgres.dump':pg,'mariadb.sql':maria,**{name:archive.getvalue() for name in VOLUME_SOURCES}}
        for name,data in payloads.items():
            plain=workspace/('input-'+name)
            with plain.open('xb') as handle:handle.write(data)
            cipher.encrypt(plain,backup/(name+'.enc'),cipher.key);plain.unlink()
        rewrite_authenticated_control(backup,cipher.key,{'schemaVersion':2,'version':'0.2.0','databaseSchema':schema,
            'releaseManifestSha256':hashlib.sha256(release_bytes).hexdigest(),'images':images,'files':{}})
        pg,maria,payloads=None,None,None
        verifier=ControlledVerifier(source_root,cipher)
        bound=NewCopyTarget(handoff,protocols[1],target_probe,operations)
        # Authentication failure is before every DB ExecCreate, not merely before writes.
        control=backup/'CONTROL-HMAC';original=control.read_bytes();control.write_text('0'*64+'\n')
        before_execs=[name for name in (root/'1/.surveyctl').iterdir() if name.name.startswith('runtime-exec-')]
        rejected=False;controlled_before=api.controlled_creates;crypto_before=len(crypto_store.load()['children'])
        bad=ControlledDatabaseCopy(verifier,bound)
        try:bad.execute(backup)
        except RestoreIntegrityError as error:rejected='authentication' in str(error)
        finally:control.write_bytes(original)
        after_execs=[name for name in (root/'1/.surveyctl').iterdir() if name.name.startswith('runtime-exec-')]
        report['badHmacStartsNoExec']=(rejected and not before_execs and not after_execs
            and api.controlled_creates==controlled_before)
        report['badHmacStartsNoChild']=len(crypto_store.load()['children'])==crypto_before and not cipher.uncertain
        # Bad authenticated input never began an exec. A new bound controller is
        # explicit here; no unknown or partially written operation is retried.
        bound=NewCopyTarget(handoff,protocols[1],target_probe,operations)
        copier=ControlledDatabaseCopy(verifier,bound)
        if fail_maria:
            create=api.create
            def fault_create(cid,command,*,stdin=False):
                if stdin and cid==target['engine-db']:
                    fixture_shell(tools,cid,REPLACE_SECRET,secrets.token_hex(24).encode()+b'\n')
                return create(cid,command,stdin=stdin)
            api.create=fault_create
        stage = 'authenticated-new-copy-import'
        known_partial=False
        try:
            outcome=copier.execute(backup)
            report['backupHmacAuthenticated']=outcome['backupHmacAuthenticated']
            report['transferReport']=outcome
        except KnownExecFailure:
            if not fail_maria:raise
            known_partial=copier.failed and not copier.uncertain
            report['backupHmacAuthenticated']=copier.evidence is not None
        report['knownPartialCopy']=known_partial
        before_retry=sorted(p.name for p in (root/'1/.surveyctl').glob('runtime-exec-*.json'));create_before_retry=api.controlled_creates
        try:copier.execute(backup)
        except KnownExecFailure:report['retryPrevented']=(before_retry==sorted(p.name for p in (root/'1/.surveyctl').glob('runtime-exec-*.json'))
            and api.controlled_creates==create_before_retry)
        receipts=[]
        for profile in ('postgres-import','mariadb-import'):
            row=PrivateStore(root/'1',name='runtime-exec-'+operations[profile].replace('-','')+'.json').load()
            receipts.append({key:row[key] for key in ('schemaVersion','profile','phase','exitCode','sourceEvidence','inputEvidence')})
        report['inputReceipts']=receipts
        report['controlledDatabaseInputReceipts']=(all(row['schemaVersion']==4 for row in receipts)
            and receipts[0]['phase']=='completed' and receipts[0]['inputEvidence']['delivered'] is True
            and receipts[1]['phase']==('failed' if fail_maria else 'completed'))
        report['sourceCryptoChildrenEnded']=all(row['phase']=='completed' for row in crypto_store.load()['children'])
        report['targetCohortUsesKernelReader']=target_probe()=='quiescent'
        check = f"SELECT answer FROM fixture_recovery_response WHERE id='{answer}';"
        report['postgresDataVerified'] = query(target, 'platform-db', check).strip() == b'synthetic snapshot answer'
        if fail_maria:
            report['mariadbUntouched']=query(target,'engine-db',"SELECT count(*) FROM information_schema.tables WHERE table_schema='limesurvey';").strip()==b'0'
        else:
            report['mariadbDataVerified'] = query(target, 'engine-db', check).strip() == b'synthetic snapshot answer'
        report['postSnapshotWritesPreserved'] = all(query(source, kind, 'SELECT count(*) FROM fixture_recovery_response;').strip() == b'2'
            and (fail_maria and kind=='engine-db' or query(target, kind, 'SELECT count(*) FROM fixture_recovery_response;').strip() == b'1') for kind in refs)
        grant = f"SELECT platform_admin_active('{tenant}','{actor}');"
        active_session = f"SELECT revoked_at IS NULL FROM org_login_session WHERE id='{session}';"
        report['sourcePersonnelUnchanged']=(query(source,'platform-db',grant).strip()==b't' and query(source,'platform-db',active_session).strip()==b't')
        if not fail_maria:
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
            report['targetRestoreAuditRecorded'] = query(target, 'platform-db', "SELECT count(*) FROM platform_security_restore_audit r JOIN platform_admin_audit a USING(trace_id) WHERE a.action='identity_invalidated' AND a.operator_db_user='postgres' AND r.operator_db_user='postgres';").strip() == b'1'
        for protocol in protocols: protocol.preflight_cleanup(True)
        report['applicationsNeverStarted'] = True  # Closed topology contains only the two DB services.
        report['pendingIntentsRetainedDuringCopy'] = len(intents) == 2 and all(intent.check() for intent in intents)
        report['namedHostDumpWritten']=any(path.is_file() and path.name in {'postgres.dump','mariadb.sql',*VOLUME_SOURCES} for path in root.rglob('*'))
        report['status'] = 'passed'
    except Exception as error:
        diagnostic=[]
        for profile,operation in operations.items():
            try:
                row=PrivateStore(root/'1',name='runtime-exec-'+operation.replace('-','')+'.json').load()
                if row:
                    phase=row.get('phase')
                    diagnostic.append({'profile':profile,'phase':phase if phase in
                        ('create-pending','created','released','completed','failed') else 'invalid',
                        'exitCode':row.get('exitCode') if type(row.get('exitCode')) is int else None})
            except Exception: diagnostic.append({'profile':profile,'phase':'unreadable','exitCode':None})
        report['receiptDiagnostic']=diagnostic
        if crypto_store is not None:
            try:
                rows=(crypto_store.load() or {}).get('children',[])
                report['cryptoDiagnostic']=[{
                    'profile':row.get('profile') if row.get('profile') in ('openssl-encrypt','openssl-decrypt') else 'unknown',
                    'phase':row.get('phase') if row.get('phase') in ('pending','created','released','completed','failed') else 'unknown',
                    'reason':row.get('reason') if row.get('reason') in ('completed','exit','deadline','bootstrap','journal') else None,
                    'exitCode':row.get('exitCode') if type(row.get('exitCode')) is int else None} for row in rows[-3:]]
            except Exception:report['cryptoObservation']='unreadable'
        if isinstance(error, __import__('owned_docker_exec').UncertainExec) or cipher is not None and cipher.uncertain:tools.uncertain=True
        reason=getattr(error,'target_reason',None)
        if reason not in ISOLATION_FAILURES and bound is not None:reason=getattr(bound,'last_failure',None)
        if reason in ISOLATION_FAILURES:report['targetReason']=reason
        report['failureCategory']=type(error).__name__
        report['failureStage'] = stage  # Never emit SQL, rows, raw stderr or dumps.
        if tools.diagnostic is not None: report['execDiagnostic'] = tools.diagnostic
    finally:
        if not tools.uncertain:
            try:
                if cipher is not None:cipher.close()
                if workspace is not None:workspace.close()
            except Exception:tools.uncertain=True
        safe_cleanup = tools.may_cleanup() and len(intents) == len(projects) and all(intent.check() for intent in intents)
        clean = cleanup_protocols(protocols, safe_cleanup)
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
    parser.add_argument('--fail-maria',action='store_true')
    args = parser.parse_args()
    try: report = run(args.claim_image, args.postgres_image, args.mariadb_image,args.fail_maria)
    except Exception: report = {'schemaVersion': 1, 'status': 'failed', 'failureStage': 'preflight', 'fixtureCleanup': 'not-started'}
    print(json.dumps(report, sort_keys=True)); sys.exit(0 if report['status'] == 'passed' else 1)
