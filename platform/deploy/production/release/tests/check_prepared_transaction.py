#!/usr/bin/env python3
"""CI-only PostgreSQL prepared transaction experiment in a disposable container.

This test does not authorize or inspect a candidate installation. Cleanup touches
only the full container ID created here after rechecking its random label.
"""
import hashlib
import json
import os
import re
import secrets
import subprocess
import time


DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']


def summarize_sql_error(stderr):
    # Never log raw stderr: even test tools may echo SQL or environment data.
    normalized = stderr.lower()
    categories = (
        ('connection_refused', 'connection refused'),
        ('starting_up', 'the database system is starting up'),
        ('server_closed', 'server closed the connection unexpectedly'),
        ('connection_terminated', 'terminating connection'),
    )
    category = next((name for name, marker in categories if marker in normalized), 'other')
    digest = hashlib.sha256(stderr.encode('utf-8', errors='replace')).hexdigest()
    return 'category=' + category + ' stderr_sha256=' + digest


def summarize_container_diagnostics(inspect_output, logs):
    """Report fixed state fields and log marker counts, never raw Docker output."""
    try:
        objects = json.loads(inspect_output)
        if not isinstance(objects, list) or len(objects) != 1:
            raise ValueError('ambiguous inspection')
        item = objects[0]
        state = item['State']
        status = state['Status']
        if status not in {'created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'}:
            raise ValueError('unknown state')
        restart_count, exit_code, oom = item['RestartCount'], state['ExitCode'], state['OOMKilled']
        if (type(restart_count) is not int or restart_count < 0 or type(exit_code) is not int or
                type(oom) is not bool):
            raise ValueError('invalid state')
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 'container_state=unavailable'
    lower = logs.lower()
    markers = {
        'ready': 'database system is ready to accept connections',
        'shutdown': 'database system is shut down',
        'startup': 'starting postgresql',
        'init_complete': 'postgresql init process complete; ready for start up',
    }
    counts = ' '.join(name + '=' + str(lower.count(marker)) for name, marker in markers.items())
    return (f'container_state={status} restarts={restart_count} exit_code={exit_code} '
            f'oom={str(oom).lower()} {counts}')


def wait_for_final_server(initialized, query_ready, *, clock=time.monotonic, sleep=time.sleep, timeout=60):
    """Wait with a soft deadline; individual Docker operations have their own timeouts."""
    deadline = clock() + timeout
    while clock() < deadline:
        if initialized() and query_ready():
            return
        sleep(.25)
    raise RuntimeError('isolated test database did not start')


def run(args, *, check=True, timeout=30):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('isolated prepared transaction test command failed')
    return result


def main():
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or
            os.environ.get('GITHUB_REPOSITORY') != 'lsgoodlionel/star-survey'):
        raise RuntimeError('requires dedicated GitHub-hosted repository runner')
    nonce = secrets.token_hex(16)
    label = 'survey.fixture.prepared=' + nonce
    image = run(DOCKER + ['image', 'inspect', 'postgres:16-alpine', '--format', '{{.Id}}']).stdout.strip()
    if re.fullmatch(r'sha256:[0-9a-f]{64}', image) is None:
        raise RuntimeError('test image identity invalid')
    started = run(DOCKER + ['run', '-d', '--rm', '--network', 'none', '--tmpfs', '/var/lib/postgresql/data',
                            '--label', label, '-e', 'POSTGRES_PASSWORD=fixture-only-password', image,
                            'postgres', '-c', 'max_prepared_transactions=10'])
    container = started.stdout.strip()
    if re.fullmatch(r'[0-9a-f]{64}', container) is None:
        raise RuntimeError('test container identity ambiguous')
    initial_started_at = None
    def verify(*, require_stable=True):
        nonlocal initial_started_at
        objects = json.loads(run(DOCKER + ['inspect', '--type', 'container', container]).stdout)
        if not isinstance(objects, list) or len(objects) != 1:
            raise RuntimeError('test container inspection ambiguous')
        item = objects[0]
        if (item.get('Id') != container or item.get('Image') != image or
                item.get('Config', {}).get('Labels', {}).get('survey.fixture.prepared') != nonce or
                item.get('HostConfig', {}).get('NetworkMode') != 'none' or
                item.get('HostConfig', {}).get('AutoRemove') is not True):
            raise RuntimeError('test container identity changed')
        state = item.get('State')
        if not isinstance(state, dict) or state.get('Running') is not True or not isinstance(state.get('StartedAt'), str):
            raise RuntimeError('test container is not running')
        if initial_started_at is None:
            initial_started_at = state['StartedAt']
        elif require_stable and state['StartedAt'] != initial_started_at:
            raise RuntimeError('test container restarted')
        mounts = item.get('Mounts')
        tmpfs = item.get('HostConfig', {}).get('Tmpfs')
        # Docker may omit tmpfs entries from Mounts; require its declared tmpfs
        # configuration and independently inspect the mounted filesystem.
        if (not isinstance(tmpfs, dict) or set(tmpfs) != {'/var/lib/postgresql/data'} or
                not isinstance(mounts, list) or len(mounts) > 1 or
                any(not isinstance(m, dict) or m.get('Type') != 'tmpfs' or
                    m.get('Destination') != '/var/lib/postgresql/data' for m in mounts)):
            raise RuntimeError('test database mount configuration is not isolated')
        mountinfo = run(DOCKER + ['exec', container, 'cat', '/proc/self/mountinfo']).stdout.splitlines()
        target_entries = []
        for line in mountinfo:
            left, separator, right = line.partition(' - ')
            fields, filesystem = left.split(), right.split()
            if separator and len(fields) >= 5 and filesystem and fields[4] == '/var/lib/postgresql/data':
                target_entries.append(filesystem[0])
        if target_entries != ['tmpfs']:
            raise RuntimeError('test database filesystem is not uniquely tmpfs')
    def sql(statement, check=True):
        verify()
        result = run(DOCKER + ['exec', container, 'psql', '-X', '-q', '-A', '-t', '-U', 'postgres',
                               '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', statement], check=False)
        if check and result.returncode:
            inspection = run(DOCKER + ['inspect', '--type', 'container', container], check=False)
            logs = run(DOCKER + ['logs', container], check=False)
            diagnostic = summarize_container_diagnostics(inspection.stdout, logs.stdout + logs.stderr)
            raise RuntimeError('synthetic PostgreSQL failure: ' + summarize_sql_error(result.stderr) + ' ' + diagnostic)
        return result
    try:
        verify()
        def initialized():
            verify()
            logs = run(DOCKER + ['logs', container], check=False)
            # The image briefly starts a temporary server during initdb. A
            # successful query there does not prove the final server is ready.
            return (logs.returncode == 0 and 'postgresql init process complete; ready for start up' in
                    (logs.stdout + logs.stderr).lower())
        wait_for_final_server(initialized, lambda: sql('SELECT 1', check=False).returncode == 0)
        gid = 'fixture_' + nonce
        sql('CREATE TABLE fixture_prepared (id integer NOT NULL)')
        sql("BEGIN; INSERT INTO fixture_prepared VALUES (1); PREPARE TRANSACTION '" + gid + "';")
        if sql("SELECT count(*) FROM pg_prepared_xacts WHERE database=current_database() AND gid='" + gid + "'").stdout.strip() != '1':
            raise RuntimeError('prepared transaction was not observed')
        # The creating psql process is gone, but the prepared work persists.
        if sql("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid()").stdout.strip() != '0':
            raise RuntimeError('unexpected test client remains')
        if sql('SELECT count(*) FROM fixture_prepared').stdout.strip() != '0':
            raise RuntimeError('uncommitted data became visible')
        sql("ROLLBACK PREPARED '" + gid + "'")
        if (sql("SELECT count(*) FROM pg_prepared_xacts WHERE database=current_database() AND gid='" + gid + "'").stdout.strip() != '0' or
                sql('SELECT count(*) FROM fixture_prepared').stdout.strip() != '0'):
            raise RuntimeError('prepared transaction cleanup was not verified')
        print('prepared transaction survived client exit and was detected and rolled back in isolated test')
        # Deliberately restart this uniquely identified throwaway container and
        # verify the stable-instance check rejects it before any further work.
        verify()
        run(DOCKER + ['restart', '--time', '3', container], timeout=30)
        try:
            verify()
        except RuntimeError as exc:
            if str(exc) != 'test container restarted':
                raise
        else:
            raise RuntimeError('test container restart was not detected')
        print('test container restart was rejected by stable-instance check')
    finally:
        # Never address an inferred name or a nonmatching container. --rm and
        # tmpfs avoid retaining this test database, without touching named volumes.
        # A deliberately restarted test container may still be stopped only
        # after its full ID, label, image, network and tmpfs are revalidated.
        verify(require_stable=False)
        run(DOCKER + ['stop', '--time', '3', container], timeout=15)
        remaining = run(DOCKER + ['inspect', '--type', 'container', container], check=False)
        if remaining.returncode == 0:
            raise RuntimeError('isolated test container still exists')


if __name__ == '__main__':
    main()
