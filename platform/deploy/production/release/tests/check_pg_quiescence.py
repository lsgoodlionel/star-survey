#!/usr/bin/env python3
"""Synthetic server-side cancellation test for an isolated CI service only.

This experiment never connects to a candidate tenant database. Its success is
not authority to terminate a candidate session or evidence of fixture cleanup.
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
    nonce = secrets.token_hex(16)
    app = 'fixture_' + nonce
    table = 'fixture_' + nonce
    base = ['psql', '-X', '-q', '-h', '127.0.0.1', '-p', port, '-U', 'postgres', '-d', 'postgres',
            '-v', 'ON_ERROR_STOP=1']
    env = dict(os.environ, PGPASSWORD='fixture-only-password', PGCONNECT_TIMEOUT='5', PGOPTIONS='-c statement_timeout=5000')
    def query(sql):
        result = subprocess.run(base + ['-A', '-t', '-c', sql], env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15, check=True)
        return result.stdout.strip()
    def activity(sleeping=False):
        extra = " AND wait_event='PgSleep' AND state='active'" if sleeping else ''
        return query("SELECT pid FROM pg_stat_activity WHERE application_name='" + app +
                     "' AND datname=current_database() AND usename=current_user AND pid<>pg_backend_pid()" + extra)
    query('CREATE TABLE ' + table + ' (id integer NOT NULL)')
    child = None
    try:
        worker_env = dict(env, PGAPPNAME=app, PGOPTIONS='-c statement_timeout=120000')
        child = subprocess.Popen(base + ['-c', 'BEGIN; INSERT INTO ' + table + ' VALUES (1); SELECT pg_sleep(60); COMMIT'],
                                 env=worker_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 15
        backend = ''
        while time.monotonic() < deadline:
            backend = activity(sleeping=True)
            if backend:
                break
            if child.poll() is not None:
                raise RuntimeError('client exited before transaction was observed')
            time.sleep(.1)
        if not re.fullmatch(r'[0-9]+', backend):
            raise RuntimeError('expected a single isolated backend')
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        # The client disappearing does not prove the server-side query ended.
        # Match both the random application name and the observed backend PID.
        query("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='" + app +
              "' AND pid=" + backend + " AND datname=current_database() AND usename=current_user AND pid<>pg_backend_pid()")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and activity():
            time.sleep(.1)
        if activity() or query('SELECT count(*) FROM ' + table) != '0':
            raise RuntimeError('backend remained active or transaction did not roll back')
        print('isolated backend terminated and uncommitted synthetic insert rolled back')
    finally:
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=10)
        # Only a randomly named table created above in the disposable service.
        query('DROP TABLE ' + table)


if __name__ == '__main__':
    main()
