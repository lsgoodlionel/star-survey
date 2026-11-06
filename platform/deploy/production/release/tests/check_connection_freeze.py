#!/usr/bin/env python3
"""CI-only experiment: drain a disposable database without expanding app privileges.

This does not establish Docker isolation or supervise a candidate installation.
Existing sessions and other privileged roles remain separate hazards.
"""
import os
import re
import secrets
import subprocess
import time


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('CI service test only')
    port = os.environ.get('FIXTURE_PG_PORT', '')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise RuntimeError('invalid service port')
    suffix = secrets.token_hex(10)
    db, owner, app = 'freeze_' + suffix, 'owner_' + suffix, 'app_' + suffix
    env = dict(os.environ, PGPASSWORD='fixture-only-password', PGCONNECT_TIMEOUT='3',
               PGOPTIONS='-c statement_timeout=15000')
    def command(user, database):
        return ['psql', '-X', '-q', '-A', '-t', '-h', '127.0.0.1', '-p', port,
                '-U', user, '-d', database, '-v', 'ON_ERROR_STOP=1']
    def query(sql, user='postgres', database='postgres', check=True):
        result = subprocess.run(command(user, database) + ['-c', sql], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        if check and result.returncode:
            raise RuntimeError('isolated connection experiment failed: ' + result.stderr[-1000:])
        return result
    query(f"CREATE ROLE {owner} LOGIN PASSWORD 'fixture-only-password'; CREATE ROLE {app} LOGIN PASSWORD 'fixture-only-password';")
    query(f'CREATE DATABASE {db} OWNER {owner}')
    client = None
    try:
        query(f'GRANT CONNECT ON DATABASE {db} TO {app}', owner, db)
        # Compare effective ACL entries as a set, not PostgreSQL display order.
        acl_sql = ("SELECT coalesce(string_agg(format('%s:%s:%s',grantee,privilege_type,is_grantable),',' "
                   "ORDER BY grantee,privilege_type,is_grantable),'') FROM pg_database d, "
                   "LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a WHERE d.datname='" + db + "'")
        before = query(acl_sql).stdout.strip()
        client = subprocess.Popen(command(app, db), env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        client.stdin.write('SELECT 1;\n')
        client.stdin.flush()
        def sessions():
            return query("SELECT count(*) FROM pg_stat_activity WHERE datname='" + db + "' AND usename='" + app + "'").stdout.strip()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and sessions() != '1':
            if client.poll() is not None:
                raise RuntimeError('app connection failed before freeze')
            time.sleep(.05)
        if sessions() != '1':
            raise RuntimeError('existing app connection not observed')
        query(f'REVOKE CONNECT ON DATABASE {db} FROM PUBLIC, {app}', owner, db)
        denied = query('SELECT 1', app, db, check=False)
        if denied.returncode == 0 or 'permission denied for database' not in denied.stderr:
            raise RuntimeError('new app connection was not rejected')
        if sessions() != '1' or client.poll() is not None:
            raise RuntimeError('freeze unexpectedly removed the old session')
        # Revoking CONNECT is not a write barrier for a session already open.
        client.stdin.write('SELECT pg_sleep(8);\n')
        client.stdin.flush()
        running_sql = ("SELECT count(*) FROM pg_stat_activity WHERE datname='" + db + "' AND usename='" + app +
                       "' AND state='active' AND wait_event='PgSleep'")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and query(running_sql).stdout.strip() != '1':
            time.sleep(.05)
        if query(running_sql).stdout.strip() != '1':
            raise RuntimeError('existing connection could not demonstrate continued execution')
        backend = query("SELECT pid FROM pg_stat_activity WHERE datname='" + db + "' AND usename='" + app + "'").stdout.strip()
        if not re.fullmatch(r'[0-9]+', backend):
            raise RuntimeError('existing app backend was not uniquely identified')
        # The DB owner is not automatically entitled to terminate another
        # role's backend. Never assume privilege escalation is available.
        terminated = query('SELECT pg_terminate_backend(' + backend + ')', owner, db, check=False)
        if terminated.returncode == 0 or sessions() != '1':
            raise RuntimeError('unexpected cross-role termination privilege')
        # Closing the actual client leaves no running SQL or transaction in this
        # experiment; a live query needs separate cancellation and verification.
        client.stdin.close()
        if client.wait(timeout=15) != 0:
            raise RuntimeError('existing app client did not close cleanly')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and sessions() != '0':
            time.sleep(.05)
        if sessions() != '0':
            raise RuntimeError('old app sessions were not drained')
        query(f'GRANT CONNECT ON DATABASE {db} TO PUBLIC, {app}', owner, db)
        if query(acl_sql).stdout.strip() != before:
            raise RuntimeError('database connect ACL was not restored exactly')
        query('SELECT 1', app, db)
        print('isolated database blocked new app connections, drained old session, and restored ACL')
    finally:
        if client is not None and client.poll() is None:
            client.kill()
            client.wait(timeout=10)
        # Only random objects created by this job on its disposable service.
        query('DROP DATABASE ' + db + ' WITH (FORCE)')
        query(f'DROP ROLE {app}; DROP ROLE {owner}')


if __name__ == '__main__':
    main()
