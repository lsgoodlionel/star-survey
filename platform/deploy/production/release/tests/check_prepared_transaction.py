#!/usr/bin/env python3
"""CI-only PostgreSQL prepared transaction experiment in a disposable container.

This test does not authorize or inspect a candidate installation. Cleanup touches
only the full container ID created here after rechecking its random label.
"""
import json
import os
import re
import secrets
import subprocess
import time


DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']


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
    def verify():
        objects = json.loads(run(DOCKER + ['inspect', '--type', 'container', container]).stdout)
        if not isinstance(objects, list) or len(objects) != 1:
            raise RuntimeError('test container inspection ambiguous')
        item = objects[0]
        if (item.get('Id') != container or item.get('Image') != image or
                item.get('Config', {}).get('Labels', {}).get('survey.fixture.prepared') != nonce or
                item.get('HostConfig', {}).get('NetworkMode') != 'none' or
                item.get('HostConfig', {}).get('AutoRemove') is not True):
            raise RuntimeError('test container identity changed')
        mounts = item.get('Mounts')
        if not isinstance(mounts, list) or len(mounts) != 1 or mounts[0].get('Type') != 'tmpfs' or mounts[0].get('Destination') != '/var/lib/postgresql/data':
            raise RuntimeError('test database storage is not an isolated tmpfs')
    def sql(statement, check=True):
        verify()
        return run(DOCKER + ['exec', container, 'psql', '-X', '-q', '-A', '-t', '-U', 'postgres',
                             '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', statement], check=check)
    try:
        verify()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if sql('SELECT 1', check=False).returncode == 0:
                break
            time.sleep(.25)
        else:
            raise RuntimeError('isolated test database did not start')
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
    finally:
        # Never address an inferred name or a nonmatching container. --rm and
        # tmpfs avoid retaining this test database, without touching named volumes.
        verify()
        run(DOCKER + ['stop', '--time', '3', container], timeout=15)
        remaining = run(DOCKER + ['inspect', '--type', 'container', container], check=False)
        if remaining.returncode == 0:
            raise RuntimeError('isolated test container still exists')


if __name__ == '__main__':
    main()
