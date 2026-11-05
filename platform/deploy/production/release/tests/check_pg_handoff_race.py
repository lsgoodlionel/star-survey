#!/usr/bin/env python3
"""Synthetic handoff/cleanup race against the disposable CI PostgreSQL service.

This deliberately models only relevant transaction ordering; it does not run
product code and is not proof of candidate authorization or recovery.
"""
import os
import re
import secrets
import signal
import subprocess
import time


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('CI service test only')
    port = os.environ.get('FIXTURE_PG_PORT', '')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise RuntimeError('invalid service port')
    suffix = secrets.token_hex(16)
    schema, app = 'race_' + suffix, 'race_' + suffix
    base = ['psql', '-X', '-q', '-h', '127.0.0.1', '-p', port, '-U', 'postgres', '-d', 'postgres',
            '-v', 'ON_ERROR_STOP=1']
    env = dict(os.environ, PGPASSWORD='fixture-only-password', PGCONNECT_TIMEOUT='5',
               PGOPTIONS='-c statement_timeout=5000')

    def query(sql):
        done = subprocess.run(base + ['-A', '-t', '-c', sql], env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15, check=True)
        return done.stdout.strip()

    def active(sleeping=False):
        suffix_filter = " AND state='active' AND wait_event='PgSleep'" if sleeping else ''
        return query("SELECT pid FROM pg_stat_activity WHERE application_name='" + app +
                     "' AND datname=current_database() AND usename=current_user AND pid<>pg_backend_pid()" + suffix_filter)

    query('CREATE SCHEMA ' + schema)
    worker = None
    try:
        query('CREATE TABLE ' + schema + '.handoff (id integer PRIMARY KEY, consumed boolean NOT NULL); '
              'CREATE TABLE ' + schema + '.revoked (principal integer PRIMARY KEY); '
              'CREATE TABLE ' + schema + '.session (principal integer NOT NULL, revoked boolean NOT NULL); '
              'INSERT INTO ' + schema + '.handoff VALUES (1,false),(2,false)')
        # An unconsumed handoff must be invalidated before asserting cleanup.
        query('UPDATE ' + schema + '.handoff SET consumed=true WHERE id=1 AND NOT consumed')
        if query('SELECT count(*) FROM ' + schema + '.handoff WHERE id=1 AND NOT consumed') != '0':
            raise RuntimeError('pending handoff remained available')
        # Exchange consumes inside a transaction, then pauses before inserting a
        # session. Concurrent compensation cannot see its uncommitted session.
        worker_sql = ('BEGIN; UPDATE ' + schema + '.handoff SET consumed=true WHERE id=2 AND NOT consumed; '
                      'SELECT pg_sleep(6); INSERT INTO ' + schema + '.session VALUES (2,false); COMMIT')
        worker = subprocess.Popen(base + ['-c', worker_sql], env=dict(env, PGAPPNAME=app, PGOPTIONS='-c statement_timeout=15000'),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not active(sleeping=True):
            if worker.poll() is not None:
                raise RuntimeError('exchange ended before the race was reached')
            time.sleep(.05)
        if not re.fullmatch(r'[0-9]+', active(sleeping=True)):
            raise RuntimeError('exchange backend was not uniquely observed')
        query('BEGIN; INSERT INTO ' + schema + '.revoked VALUES (2); UPDATE ' + schema +
              '.session SET revoked=true WHERE principal=2; COMMIT')
        if worker.wait(timeout=10) != 0:
            raise RuntimeError('synthetic exchange failed')
        if query('SELECT count(*) FROM ' + schema + '.session WHERE principal=2 AND NOT revoked') != '1':
            raise RuntimeError('late session race was not reproduced')
        if query('SELECT count(*) FROM ' + schema + '.session s WHERE principal=2 AND NOT revoked '
                 'AND NOT EXISTS (SELECT 1 FROM ' + schema + '.revoked r WHERE r.principal=s.principal)') != '0':
            raise RuntimeError('revoked binding did not deny late session')
        # A cleanup result is valid only after the exchange is quiet and a
        # second targeted session revocation and handoff check have completed.
        if active():
            raise RuntimeError('exchange backend was not quiet')
        query('UPDATE ' + schema + '.session SET revoked=true WHERE principal=2 AND NOT revoked')
        if (query('SELECT count(*) FROM ' + schema + '.session WHERE principal=2 AND NOT revoked') != '0' or
                query('SELECT count(*) FROM ' + schema + '.handoff WHERE id IN (1,2) AND NOT consumed') != '0'):
            raise RuntimeError('post-quiescence recheck failed')
        print('late session reproduced; binding denial and post-quiescence recheck verified')
    finally:
        if worker is not None and worker.poll() is None:
            os.killpg(worker.pid, signal.SIGKILL)
            worker.wait(timeout=10)
        # The database is an isolated CI service; this schema has a fresh nonce.
        query('DROP SCHEMA ' + schema + ' CASCADE')


if __name__ == '__main__':
    main()
