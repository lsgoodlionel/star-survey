#!/usr/bin/env python3
"""Exercise a database-wide connection gate on a disposable CI database.

Existing connections remain usable and must be inspected separately. This is
not an installation guard, a Docker ownership proof, or a recovery procedure.
"""
import os
import re
import secrets
import select
import subprocess
import time


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('CI service test only')
    port = os.environ.get('FIXTURE_PG_PORT', '')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise RuntimeError('invalid service port')
    suffix = secrets.token_hex(10)
    db, owner, app, other = ('gate_' + suffix, 'owner_' + suffix, 'app_' + suffix, 'other_' + suffix)
    env = dict(os.environ, PGPASSWORD='fixture-only-password', PGCONNECT_TIMEOUT='3', PGOPTIONS='-c statement_timeout=5000')
    def command(user, database):
        return ['psql', '-X', '-q', '-A', '-t', '-h', '127.0.0.1', '-p', port,
                '-U', user, '-d', database, '-v', 'ON_ERROR_STOP=1']
    def query(sql, user='postgres', database='postgres', check=True):
        result = subprocess.run(command(user, database) + ['-c', sql], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        if check and result.returncode:
            raise RuntimeError('isolated gate operation failed: ' + result.stderr[-1000:])
        return result
    class Session:
        def __init__(self, user, database=db):
            self.process = subprocess.Popen(command(user, database), env=env, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.buffer = b''
        def sql(self, statement):
            self.process.stdin.write((statement + '\n\\echo END_FIXTURE_QUERY\n').encode())
            self.process.stdin.flush()
            marker = b'END_FIXTURE_QUERY\n'
            deadline = time.monotonic() + 10
            while marker not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise RuntimeError('persistent session did not respond')
                chunk = os.read(self.process.stdout.fileno(), 4096)
                if not chunk:
                    detail = self.process.stderr.read(1200).decode('utf-8', errors='replace')
                    raise RuntimeError('persistent session ended unexpectedly: ' + detail)
                self.buffer += chunk
                if len(self.buffer) > 8192:
                    raise RuntimeError('persistent session returned excessive data')
            result, self.buffer = self.buffer.split(marker, 1)
            return result.decode('utf-8').strip()
        def close(self):
            if self.process.poll() is None:
                self.process.stdin.close()
                self.process.wait(timeout=10)
    query(f"CREATE ROLE {owner} LOGIN PASSWORD 'fixture-only-password'; CREATE ROLE {app} LOGIN PASSWORD 'fixture-only-password'; CREATE ROLE {other} LOGIN SUPERUSER PASSWORD 'fixture-only-password';")
    query(f'CREATE DATABASE {db} OWNER {owner}')
    sessions = []
    try:
        owner_session, app_session, inspector, extra_session = Session(owner), Session(app), Session(app), Session(other)
        maintenance_extra = Session(other, 'postgres')
        # An explicitly identified supervisor connection uses existing CI
        # superuser credentials for read-only cluster visibility; no grants are
        # added to platform_app. This does not authorize candidate use.
        cluster_inspector = Session('postgres', 'postgres')
        sessions.extend((owner_session, app_session, inspector, extra_session, maintenance_extra, cluster_inspector))
        owner_pid = owner_session.sql('SELECT pg_backend_pid();')
        app_pid = app_session.sql('SELECT pg_backend_pid();')
        inspector_pid = inspector.sql('SELECT pg_backend_pid();')
        extra_pid = extra_session.sql('SELECT pg_backend_pid();')
        maintenance_pid = maintenance_extra.sql('SELECT pg_backend_pid();')
        cluster_pid = cluster_inspector.sql('SELECT pg_backend_pid();')
        if any(not re.fullmatch(r'[0-9]+', value) for value in (owner_pid, app_pid, inspector_pid, extra_pid, maintenance_pid, cluster_pid)):
            raise RuntimeError('backend identities were invalid')
        identity_sql = 'SELECT oid FROM pg_database WHERE datname=current_database();'
        db_oid = owner_session.sql(identity_sql)
        if not re.fullmatch(r'[0-9]+', db_oid) or app_session.sql(identity_sql) != db_oid:
            raise RuntimeError('persistent sessions do not share a database identity')
        original_acl = query("SELECT coalesce(datacl::text,'DEFAULT') FROM pg_database WHERE oid=" + db_oid).stdout.strip()
        # PostgreSQL forbids closing the gate from the target database itself.
        # Use the owner through the separate postgres maintenance database.
        query(f'ALTER DATABASE {db} WITH ALLOW_CONNECTIONS false', owner, 'postgres')
        if owner_session.sql('SELECT NOT datallowconn FROM pg_database WHERE oid=' + db_oid + ';') != 't':
            raise RuntimeError('database gate was not closed')
        for role in (owner, app, 'postgres'):
            if query('SELECT 1', role, db, check=False).returncode == 0:
                raise RuntimeError('new connection unexpectedly succeeded for ' + role)
        # backend_type is hidden from non-members; only the pinned test
        # superuser observer may classify clients. Unknown or extra client
        # connections cause rejection, including on the maintenance database.
        def clients():
            raw = cluster_inspector.sql("SELECT coalesce(string_agg(pid::text||':'||usename||':'||datname,',' ORDER BY pid),'') FROM pg_stat_activity WHERE backend_type='client backend';")
            return set(raw.split(',')) if raw else set()
        # Match connection identity, role and database; PID alone is not enough.
        expected = {owner_pid + ':' + owner + ':' + db, app_pid + ':' + app + ':' + db,
                    inspector_pid + ':' + app + ':' + db, cluster_pid + ':postgres:postgres'}
        def require_only_expected_clients():
            actual = clients()
            if actual != expected:
                extras = sorted(actual - expected)
                pids = [item.split(':', 1)[0] for item in extras]
                details = 'unavailable'
                if pids and len(pids) <= 8 and all(re.fullmatch(r'[0-9]+', pid) for pid in pids):
                    # Synthetic disposable service only: omit query text, client
                    # addresses, credentials and arbitrary application names.
                    details = cluster_inspector.sql("SELECT coalesce(string_agg(pid::text||':'||"
                        "CASE WHEN application_name IN ('psql','pg_isready') THEN application_name "
                        "WHEN application_name='' THEN 'empty' ELSE 'other' END||':'||coalesce(state,'null')||':'||"
                        "coalesce(round(extract(epoch FROM clock_timestamp()-backend_start))::text,'null'),',' ORDER BY pid),'gone') "
                        "FROM pg_stat_activity WHERE pid IN (" + ','.join(pids) + ");")
                raise RuntimeError('unapproved database client: missing=' + str(sorted(expected - actual)) +
                                   ' extra=' + str(extras) + ' safe_details=' + details)
        if extra_pid + ':' + other + ':' + db not in clients() or maintenance_pid + ':' + other + ':postgres' not in clients():
            raise RuntimeError('unexpected target or maintenance client was not observed')
        try:
            require_only_expected_clients()
        except RuntimeError:
            pass
        else:
            raise RuntimeError('unapproved database client was accepted')
        if app_session.sql('SELECT 1;') != '1':
            raise RuntimeError('existing client behavior was not confirmed')
        # An independently connected superuser on the maintenance database can
        # reopen the gate. A snapshot without daemon/privileged exclusion is
        # therefore not a durable proof. Detect the change and re-close only the
        # synthetic database before proceeding.
        maintenance_extra.sql(f'ALTER DATABASE {db} WITH ALLOW_CONNECTIONS true;')
        if owner_session.sql('SELECT datallowconn FROM pg_database WHERE oid=' + db_oid + ';') != 't':
            raise RuntimeError('privileged gate reopening was not observed')
        query(f'ALTER DATABASE {db} WITH ALLOW_CONNECTIONS false', owner, 'postgres')
        if owner_session.sql('SELECT NOT datallowconn FROM pg_database WHERE oid=' + db_oid + ';') != 't':
            raise RuntimeError('synthetic gate did not close again')
        extra_session.close()
        try:
            require_only_expected_clients()
        except RuntimeError:
            pass
        else:
            raise RuntimeError('unknown maintenance-database superuser was accepted')
        maintenance_extra.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and clients() != expected:
            time.sleep(.05)
        require_only_expected_clients()
        def require_quiescent():
            require_only_expected_clients()
            if cluster_inspector.sql('SELECT NOT datallowconn FROM pg_database WHERE oid=' + db_oid + ';') != 't':
                raise RuntimeError('database gate is not closed')
            if cluster_inspector.sql("SELECT count(*) FROM pg_prepared_xacts WHERE database='" + db + "';") != '0':
                raise RuntimeError('prepared transaction remains')
            # Conservatively reject all database-associated background workers,
            # unknown backend types, active clients and open transactions.
            unsafe = cluster_inspector.sql("SELECT count(*) FROM pg_stat_activity WHERE datid=" + db_oid +
                " AND (backend_type IS DISTINCT FROM 'client backend' OR state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL);")
            if unsafe != '0':
                raise RuntimeError('database worker or transaction is not quiet')
        # A matching PID is insufficient: the known app backend can hold an
        # open transaction. A second same-role session can inspect its state
        # without granting pg_read_all_stats to any role.
        app_session.sql('BEGIN; SELECT 1;')
        unsafe = inspector.sql("SELECT count(*) FROM pg_stat_activity WHERE pid=" + app_pid +
                               " AND (state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL);")
        if unsafe != '1':
            raise RuntimeError('open transaction on known backend was not detected')
        try:
            require_quiescent()
        except RuntimeError:
            # A rejected recovery check must not open the synthetic database.
            if cluster_inspector.sql('SELECT NOT datallowconn FROM pg_database WHERE oid=' + db_oid + ';') != 't':
                raise RuntimeError('gate reopened after rejected recovery check')
            if query('SELECT 1', app, db, check=False).returncode == 0:
                raise RuntimeError('new connection succeeded after rejected recovery check')
        else:
            raise RuntimeError('quiescence guard accepted an open transaction')
        app_session.sql('ROLLBACK;')
        safe = inspector.sql("SELECT count(*) FROM pg_stat_activity WHERE pid=" + app_pid +
                             " AND state='idle' AND xact_start IS NULL;")
        if safe != '1':
            raise RuntimeError('known app backend did not become idle without a transaction')
        require_quiescent()
        query(f'ALTER DATABASE {db} WITH ALLOW_CONNECTIONS true', owner, 'postgres')
        if (query("SELECT coalesce(datacl::text,'DEFAULT') FROM pg_database WHERE oid=" + db_oid).stdout.strip() != original_acl
                or owner_session.sql('SELECT datallowconn FROM pg_database WHERE oid=' + db_oid + ';') != 't'):
            raise RuntimeError('database gate or ACL did not return to original state')
        query('SELECT 1', app, db)
        print('database gate rejected new connections, unknown superuser and known active transaction')
    finally:
        for session in sessions:
            try:
                session.close()
            except (OSError, subprocess.SubprocessError):
                session.process.kill()
                session.process.wait(timeout=10)
        # Global admin is used only to clean random test objects in CI service.
        query(f'ALTER DATABASE {db} WITH ALLOW_CONNECTIONS true')
        query('DROP DATABASE ' + db + ' WITH (FORCE)')
        query(f'DROP ROLE {app}; DROP ROLE {other}; DROP ROLE {owner}')


if __name__ == '__main__':
    main()
