#!/usr/bin/env python3
"""Disposable CI experiment only; assumes a trusted runner and Docker daemon."""
import json, os, re, secrets, select, signal, subprocess, sys, tempfile, time, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'platform/deploy/production/release'))
from fixture_cgroup import CgroupScope
from fixture_compensation import build_compensation_sql

D = ['docker', '--host', 'unix:///var/run/docker.sock']

def run(args, check=True, timeout=30):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('isolated experiment command failed')
    return result

class Session:
    def __init__(self, cid, db):
        self.p = subprocess.Popen(D + ['exec', '-i', cid, 'psql', '-X', '-q', '-A', '-t',
                                  '-U', 'postgres', '-d', db, '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.buffer = b''
    def sql(self, sql):
        self.p.stdin.write((sql + '\n\\echo FIXTURE_END\n').encode()); self.p.stdin.flush()
        marker = b'FIXTURE_END\n'; deadline = time.monotonic() + 10
        while marker not in self.buffer:
            remain = deadline - time.monotonic()
            if remain <= 0 or not select.select([self.p.stdout], [], [], remain)[0]:
                raise RuntimeError('session did not respond')
            chunk = os.read(self.p.stdout.fileno(), 4096)
            if not chunk:
                detail = self.p.stderr.read(4096).decode('utf-8', errors='replace')
                codes = re.findall(r'(?m)^ERROR:\s+([0-9A-Z]{5}):', detail)
                line = re.search(r'(?m)^LINE ([0-9]+):', detail)
                token = re.search(r'syntax error at or near "([^"\n]+)"', detail)
                safe_tokens = {'SELECT', 'FROM', 'WHERE', 'AND', 'BEGIN', 'COMMIT', 'END', 'DO', ';', ')', '(', '$', '\\',
                               'FIXTURE_END', 'SET', 'ROLE', 'ON', 'NULL', 'THEN', 'ELSE', 'IF', 'EXISTS',
                               'INSERT', 'UPDATE', 'DELETE', 'VALUES', 'AS', 'OR', 'NOT', 'INTO', 'LIMIT',
                               'ALTER', 'TABLE', 'CREATE', 'USING', 'LANGUAGE', 'IS', 'DISTINCT', 'RETURNING',
                               ':', '=', ',', '*', '-', '+', '/', '}', '{', 'EOF'}
                safe_tokens.update(word.upper() for word in re.findall(r'[AA-Za-z_]{2,40}', Path(__file__).read_text()))
                near = token.group(1).upper() if token and token.group(1).upper() in safe_tokens else 'other'
                shape = ('word' if token and re.fullmatch(r'[A-Za-z_]+', token.group(1)) else
                         'punctuation' if token and re.fullmatch(r'[^A-Za-z0-9]+', token.group(1)) else 'mixed')
                punctuation = ','.join(str(ord(c)) for c in token.group(1)) if token and shape == 'punctuation' and len(token.group(1)) <= 8 else 'none'
                caret = re.search(r'(?m)^([ ]*)\^', detail)
                raise RuntimeError('session ended; SQLSTATE=' + (codes[-1] if codes else 'unavailable') +
                                   ' error_line=' + (line.group(1) if line else 'unavailable') + ' near=' + near + ' shape=' + shape + ' punctuation_codes=' + punctuation + ' caret=' + (str(len(caret.group(1))) if caret else 'unavailable'))
            self.buffer += chunk
            if len(self.buffer) > 8192: raise RuntimeError('unexpected session output')
        output, self.buffer = self.buffer.split(marker, 1)
        return output.decode().strip()
    def close(self):
        if self.p.poll() is None:
            self.p.stdin.close(); self.p.wait(timeout=10)

def main():
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or os.environ.get('GITHUB_REPOSITORY') != 'lsgoodlionel/star-survey' or os.geteuid() != 0):
        raise RuntimeError('requires dedicated GitHub-hosted repository runner')
    # A separate unprivileged worker receives a minimal environment and no
    # inherited secrets. This probes OS permissions, not arbitrary root code.
    with tempfile.TemporaryDirectory(prefix='survey-private-') as private:
        os.chmod(private, 0o700)
        credential = os.path.join(private, 'fixture-credential')
        with open(credential, 'w', encoding='ascii') as stream: stream.write('fixture-only')
        os.chmod(credential, 0o600)
        worker_code = '''import os,socket,sys
if os.geteuid() == 0 or os.getgroups(): sys.exit(21)
if not set(os.environ) <= {'PATH', 'LC_CTYPE'}: sys.exit(22)
try:
 open(sys.argv[1]).read()
except PermissionError: pass
else: sys.exit(23)
try:
 os.listdir('/var/lib/docker')
except PermissionError: pass
else: sys.exit(24)
s=socket.socket(socket.AF_UNIX)
try:
 s.connect('/var/run/docker.sock')
except PermissionError: pass
else: sys.exit(25)
'''
        worker = run(['setpriv', '--reuid=nobody', '--regid=nogroup', '--clear-groups', '--no-new-privs', '/usr/bin/env', '-i', 'PATH=/usr/bin:/bin',
                      '/usr/bin/python3', '-c', worker_code, credential], check=False)
        if worker.returncode != 0: raise RuntimeError('unprivileged worker isolation not demonstrated: code=' + str(worker.returncode))
        sleeper = subprocess.Popen(['setpriv', '--reuid=nobody', '--regid=nogroup', '--clear-groups', '--no-new-privs', '/usr/bin/env', '-i',
                                    'PATH=/usr/bin:/bin', '/usr/bin/sleep', '30'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        os.killpg(sleeper.pid, signal.SIGKILL)
        sleeper.wait(timeout=5)
        if sleeper.returncode == 0: raise RuntimeError('forced worker termination not detected')
    nonce = secrets.token_hex(16)
    image = run(D + ['image', 'inspect', 'postgres:16-alpine', '--format', '{{.Id}}']).stdout.strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image): raise RuntimeError('invalid image')
    cid = run(D + ['run', '-d', '--rm', '--network', 'none', '--tmpfs', '/var/lib/postgresql/data',
                   '--label', 'survey.exclusive.prototype=' + nonce, '-e', 'POSTGRES_PASSWORD=fixture-only-password', image]).stdout.strip()
    if not re.fullmatch(r'[0-9a-f]{64}', cid): raise RuntimeError('invalid container identity')
    started = None; sessions = []
    def verify():
        nonlocal started
        obj = json.loads(run(D + ['inspect', '--type', 'container', cid]).stdout)
        if not isinstance(obj, list) or len(obj) != 1: raise RuntimeError('ambiguous container')
        x = obj[0]; state = x['State']; host = x['HostConfig']
        if (x['Id'] != cid or x['Image'] != image or x['Config']['Labels'].get('survey.exclusive.prototype') != nonce
            or host['NetworkMode'] != 'none' or host['AutoRemove'] is not True or state['Running'] is not True
            or host['Tmpfs'] != {'/var/lib/postgresql/data': ''} or x['Mounts']):
            raise RuntimeError('container isolation mismatch')
        if started is None: started = state['StartedAt']
        elif started != state['StartedAt']: raise RuntimeError('container restarted')
    def connect(db):
        return run(D + ['exec', cid, 'psql', '-X', '-q', '-A', '-t', '-U', 'postgres', '-d', db, '-c', 'SELECT 1'], check=False)
    try:
        verify(); deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            verify(); logs = run(D + ['logs', cid], check=False)
            if logs.returncode == 0 and 'postgresql init process complete; ready for start up' in (logs.stdout + logs.stderr).lower() and connect('postgres').returncode == 0: break
            time.sleep(.25)
        else: raise RuntimeError('test database not ready')
        supervisor, stale, maintenance = Session(cid, 'postgres'), Session(cid, 'postgres'), Session(cid, 'template1')
        sessions.extend([supervisor, stale, maintenance])
        supervisor.sql("CREATE ROLE platform_owner NOLOGIN; CREATE ROLE platform_app NOLOGIN; ALTER DATABASE postgres OWNER TO platform_owner;")
        migration_dir = Path(__file__).resolve().parents[1] / 'platform/services/business/src/main/resources/db/migration'
        migrations = []
        for version in (1, 100, 101, 103, 104, 105, 106, 107, 108, 110, 111, 200, 201, 400, 401):
            matches = list(migration_dir.glob('V' + str(version) + '__*.sql'))
            if len(matches) != 1: raise RuntimeError('migration not uniquely found')
            migrations.append(matches[0].read_text())
        supervisor.sql('SET ROLE platform_owner;\n' + '\n'.join(migrations) + '\nRESET ROLE;')
        tenant, owner_plan, fixture_plan, principal, connection, original_sub, fixture_sub, restore_sub, owner_grant = (str(uuid.uuid4()) for _ in range(9))
        grant, handoff_hash, browser_hash = str(uuid.uuid4()), secrets.token_hex(32), secrets.token_hex(32)
        scope = "SELECT set_config('app.tenant_id','" + tenant + "',true);"
        supervisor.sql(f"""BEGIN; SET LOCAL ROLE platform_owner; {scope}
INSERT INTO tenant(id,code,name,status) VALUES ('{tenant}','ci-probe','CI probe','provisioning');
UPDATE tenant SET status='active' WHERE id='{tenant}';
INSERT INTO plan_version(id,plan_code,version,capabilities,quotas,export_window_days)
VALUES ('{owner_plan}','ci-probe',1,ARRAY['survey.read','survey.write','response.collect','response.export'], '{{"member.seats":1,"response.valid_completed":100}}',1);
INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
VALUES ('{original_sub}','{tenant}','{owner_plan}','trial',now()-interval '1 day',now()+interval '10 days');
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by) VALUES ('{tenant}','production-probe-owner','active',true,'ci');
INSERT INTO access_grant(tenant_id,id,actor_id,role_code,resource_id,granted_by) VALUES ('{tenant}','{owner_grant}','production-probe-owner','tenant_owner',NULL,'ci'); COMMIT;""")
        supervisor.sql(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
INSERT INTO plan_version(id,plan_code,version,capabilities,quotas,export_window_days)
SELECT '{fixture_plan}','ci-fixture',1,capabilities,jsonb_set(quotas,'{{member.seats}}','2'),export_window_days FROM plan_version WHERE id='{owner_plan}';
INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
SELECT '{fixture_sub}','{tenant}','{fixture_plan}',kind,now(),ends_at FROM subscription WHERE id='{original_sub}';
INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by)
VALUES ('{connection}','{tenant}','wecom','ci-corp','ci-app','CI_SECRET','require_preauthorized',true,'ci');
INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id) VALUES ('{principal}','{tenant}','wecom','ci-corp','ci-user');
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by) VALUES ('{tenant}','{principal}','active',true,'ci');
INSERT INTO access_grant(tenant_id,id,actor_id,role_code,resource_id,granted_by,expires_at)
VALUES ('{tenant}','{grant}','{principal}','org_admin',NULL,'ci',now()+interval '15 minutes');
INSERT INTO org_login_handoff(handoff_hash,browser_hash,tenant_id,principal_id,connection_id,provider,expires_at)
VALUES ('{handoff_hash}','{browser_hash}','{tenant}','{principal}','{connection}','wecom',now()+interval '1 minute'); COMMIT;""")
        compensation = build_compensation_sql(tenant=tenant,owner_plan=owner_plan,fixture_plan=fixture_plan,principal=principal,
            connection=connection,original_sub=original_sub,fixture_sub=fixture_sub,restore_sub=restore_sub,owner_grant=owner_grant,
            handoff_hash=handoff_hash,browser_hash=browser_hash)
        pid = supervisor.sql('SELECT pg_backend_pid();'); stale_pid = stale.sql('SELECT pg_backend_pid();')
        if not re.fullmatch(r'[0-9]+', pid) or not re.fullmatch(r'[0-9]+', stale_pid) or pid == stale_pid: raise RuntimeError('bad client identity')
        identity_sql = ("SELECT a.pid::text||':'||a.backend_start::text||':'||d.oid::text "
                        "FROM pg_stat_activity a JOIN pg_database d ON d.oid=a.datid WHERE a.pid=pg_backend_pid();")
        db_identity = supervisor.sql(identity_sql)
        if not db_identity.startswith(pid + ':') or not re.search(r':[0-9]+$', db_identity):
            raise RuntimeError('database identity invalid')
        maintenance.sql('ALTER DATABASE postgres WITH ALLOW_CONNECTIONS false;')
        if supervisor.sql("SELECT NOT datallowconn FROM pg_database WHERE datname='postgres';") != 't': raise RuntimeError('gate not closed')
        if supervisor.sql('SELECT pg_terminate_backend(' + stale_pid + ');') != 't': raise RuntimeError('old client not terminated')
        maintenance.close()
        # The sole retained target connection also closes the maintenance DB.
        # This is only a disposable cluster and is never reopened on failure.
        supervisor.sql('ALTER DATABASE template1 WITH ALLOW_CONNECTIONS false;')
        if supervisor.sql('SELECT count(*) FROM pg_database WHERE datallowconn;') != '0':
            raise RuntimeError('connectable database remains')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if supervisor.sql("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid();") == '0': break
            time.sleep(.05)
        else: raise RuntimeError('unexpected client remains')
        if supervisor.sql("SELECT count(*) FROM pg_prepared_xacts;") != '0': raise RuntimeError('prepared transaction remains')
        if connect('postgres').returncode == 0 or connect('template1').returncode == 0:
            raise RuntimeError('closed gate accepted new client')
        if supervisor.sql("SELECT current_database() || ':' || pg_backend_pid()::text;") != 'postgres:' + pid: raise RuntimeError('supervisor identity changed')
        verify()
        if supervisor.sql(identity_sql) != db_identity: raise RuntimeError('bound database identity changed')
        # Browser stand-in: a real unprivileged worker starts a setsid child.
        # Nothing may touch the database for recovery until its cgroup is empty.
        with tempfile.TemporaryDirectory(prefix='survey-worker-') as worker_dir:
            os.chown(worker_dir, 65534, 65534); os.chmod(worker_dir, 0o700)
            descendant_file = Path(worker_dir) / 'descendant'
            child_code = f"import os,time; from pathlib import Path; Path({str(descendant_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
            worker_code = ('import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' +
                           repr(child_code) + '],start_new_session=True); time.sleep(60)')
            worker_scope = CgroupScope(65534, 65534)
            proc = None
            try:
                saved = dict(os.environ)
                try:
                    os.environ.clear(); os.environ.update({'PATH': '/usr/bin:/bin'})
                    proc = worker_scope.spawn(['/usr/bin/python3', '-c', worker_code])
                finally:
                    os.environ.clear(); os.environ.update(saved)
                deadline = time.monotonic() + 10
                while not descendant_file.exists() and time.monotonic() < deadline: time.sleep(.05)
                if not descendant_file.exists() or worker_scope.empty(): raise RuntimeError('worker descendants not observed')
                descendant_pid = descendant_file.read_text().strip()
                if not re.fullmatch(r'[0-9]+', descendant_pid): raise RuntimeError('descendant identity invalid')
                worker_scope.kill_and_wait(); proc.wait(timeout=5)
                if not worker_scope.empty(): raise RuntimeError('worker worker_scope remains active')
                stat = Path('/proc') / descendant_pid / 'stat'
                if stat.exists() and stat.read_text().rsplit(')', 1)[1].strip().split()[0] != 'Z':
                    raise RuntimeError('detached descendant remains alive')
            finally:
                if not worker_scope.empty(): worker_scope.kill_and_wait()
                if proc is not None: proc.wait(timeout=5)
                if worker_scope.empty(): worker_scope.close()
        # Re-establish the same bound identity and exclusive closed state before
        # a harmless synthetic transaction; this is not product compensation.
        verify()
        if (supervisor.sql(identity_sql) != db_identity or
            supervisor.sql('SELECT count(*) FROM pg_database WHERE datallowconn;') != '0' or
            supervisor.sql("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid();") != '0' or
            supervisor.sql('SELECT count(*) FROM pg_prepared_xacts;') != '0'):
            raise RuntimeError('post-worker exclusivity not established')
        supervisor.sql(compensation)
        checks = [
            f"SELECT id='{restore_sub}' AND plan_version_id='{owner_plan}' AND kind='trial' AND ends_at=(SELECT ends_at FROM subscription WHERE id='{original_sub}') FROM subscription WHERE tenant_id='{tenant}' AND starts_at<=now() ORDER BY starts_at DESC,seq DESC LIMIT 1",
            f"SELECT NOT EXISTS (SELECT 1 FROM access_grant WHERE tenant_id='{tenant}' AND actor_id='{principal}')",
            f"SELECT EXISTS (SELECT 1 FROM org_connection WHERE tenant_id='{tenant}' AND id='{connection}' AND NOT enabled)",
            f"SELECT EXISTS (SELECT 1 FROM identity_binding_revocation WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND reinstated_at IS NULL)",
            f"SELECT count(*)=1 FROM access_member WHERE tenant_id='{tenant}' AND uses_seat AND status<>'removed'",
        ]
        for index, check in enumerate(checks):
            result = supervisor.sql(f"BEGIN; SET LOCAL ROLE platform_app; {scope} {check}; COMMIT;")
            if result.splitlines()[-1] != 't': raise RuntimeError('fixture compensation check failed: ' + str(index))
        supervisor.sql(compensation)
        print('product compensation completed and retry was idempotent on bound exclusive session')
        # Inject a failure after closure. The handler verifies closure, does not reopen.
        try: raise RuntimeError('synthetic recovery failure')
        except RuntimeError:
            if (supervisor.sql('SELECT count(*) FROM pg_database WHERE datallowconn;') != '0' or
                    connect('postgres').returncode == 0 or connect('template1').returncode == 0):
                raise RuntimeError('gate reopened after failure')
        print('closed gate retained only bound target session; old client terminated; failure did not reopen gate')
        print('scope: trusted runner and daemon; no guarantee against same-privilege malicious process')
    finally:
        for session in sessions:
            try: session.close()
            except (OSError, subprocess.SubprocessError):
                if session.p.poll() is None: session.p.kill(); session.p.wait(timeout=10)
        verify()
        run(D + ['stop', '--time', '3', cid], timeout=15)
        if run(D + ['inspect', '--type', 'container', cid], check=False).returncode == 0:
            raise RuntimeError('disposable container remains')

if __name__ == '__main__': main()
