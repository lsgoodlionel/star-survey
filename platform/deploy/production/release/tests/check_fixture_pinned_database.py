#!/usr/bin/env python3
"""CI-only pinned session experiment against an isolated temporary container."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_pinned_database import PinnedDatabaseSession, PinnedSessionError

D = ['docker', '--host', 'unix:///var/run/docker.sock']

def run(args, check=True):
    p = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
    if check and p.returncode: raise RuntimeError('isolated database command failed')
    return p

if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true' or
        os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or
        os.environ.get('GITHUB_REPOSITORY') != 'lsgoodlionel/star-survey'):
    raise RuntimeError('dedicated hosted runner required')
nonce = secrets.token_hex(16)
daemon = run(D + ['info', '--format', '{{.ID}}']).stdout.strip()
image = run(D + ['image', 'inspect', 'postgres:16-alpine', '--format', '{{.Id}}']).stdout.strip()
if not daemon or re.fullmatch(r'sha256:[0-9a-f]{64}', image) is None: raise RuntimeError('identity invalid')
cid = run(D + ['run', '-d', '--rm', '--network', 'none', '--tmpfs', '/var/lib/postgresql/data',
               '--label', 'survey.pinned.probe=' + nonce, '-e', 'POSTGRES_PASSWORD=fixture-only-password', image]).stdout.strip()
if re.fullmatch(r'[0-9a-f]{64}', cid) is None: raise RuntimeError('container identity invalid')
started = None
session = None
target = None
emergency = None

def inspect(stable=True):
    global started
    rows = json.loads(run(D + ['inspect', '--type', 'container', cid]).stdout)
    if not isinstance(rows, list) or len(rows) != 1: raise RuntimeError('ambiguous container')
    item = rows[0]; state = item['State']; host = item['HostConfig']
    if (item['Id'] != cid or item['Image'] != image or item['Config']['Labels'].get('survey.pinned.probe') != nonce or
        host['NetworkMode'] != 'none' or host['AutoRemove'] is not True or state['Running'] is not True or
        set(host.get('Tmpfs') or {}) != {'/var/lib/postgresql/data'} or item['Mounts']):
        raise RuntimeError('container binding invalid')
    if started is None: started = state['StartedAt']
    elif stable and started != state['StartedAt']: raise RuntimeError('container restarted')
    return True

try:
    inspect(); deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        inspect(); logs = run(D + ['logs', cid], check=False)
        if logs.returncode == 0 and 'postgresql init process complete; ready for start up' in (logs.stdout + logs.stderr).lower():
            break
        time.sleep(.2)
    else: raise RuntimeError('temporary database not ready')
    run(D + ['exec', cid, 'sh', '-c', 'mkdir -p /run/secrets && printf %s fixture-only-password > /run/secrets/platform_db_superuser_password && chmod 600 /run/secrets/platform_db_superuser_password'])
    session = PinnedDatabaseSession(cid, 'postgres', lambda expected: expected == cid and inspect())
    if session.execute('SELECT 1;') != '1': raise RuntimeError('pinned query failed')
    session.execute('CREATE DATABASE platform;')
    target = PinnedDatabaseSession(cid, 'platform', lambda expected: expected == cid and inspect())
    emergency = PinnedDatabaseSession(cid, 'postgres', lambda expected: expected == cid and inspect())
    if session.execute("SELECT array_agg(datname::text ORDER BY datname::text)=ARRAY['platform','postgres','template1'] FROM pg_database WHERE datallowconn;") != 't':
        raise RuntimeError('unexpected initial database gate state')
    session.execute('ALTER DATABASE platform WITH ALLOW_CONNECTIONS false;')
    target.execute('ALTER DATABASE postgres WITH ALLOW_CONNECTIONS false;')
    target.execute('ALTER DATABASE template1 WITH ALLOW_CONNECTIONS false;')
    if emergency.execute('SELECT count(*) FROM pg_database WHERE datallowconn;') != '0':
        raise RuntimeError('database gates did not close')
    if emergency.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend';") != '3':
        raise RuntimeError('unknown database client present')
    # Exercise a failure after opening one maintenance gate: the pinned
    # emergency/target sessions close every gate again without reconnecting.
    target.execute('ALTER DATABASE postgres WITH ALLOW_CONNECTIONS true;')
    emergency.execute('ALTER DATABASE platform WITH ALLOW_CONNECTIONS false;')
    target.execute('ALTER DATABASE postgres WITH ALLOW_CONNECTIONS false;')
    target.execute('ALTER DATABASE template1 WITH ALLOW_CONNECTIONS false;')
    if emergency.execute('SELECT count(*) FROM pg_database WHERE datallowconn;') != '0':
        raise RuntimeError('emergency database gate closure unverified')
    target.execute('ALTER DATABASE postgres WITH ALLOW_CONNECTIONS true;')
    target.execute('ALTER DATABASE template1 WITH ALLOW_CONNECTIONS true;')
    session.execute('ALTER DATABASE platform WITH ALLOW_CONNECTIONS true;')
    target.close(); target = None
    emergency.close(); emergency = None
    run(D + ['restart', '--time', '3', cid])
    try: session.execute('SELECT 2;')
    except PinnedSessionError: pass
    else: raise RuntimeError('container restart was not rejected')
    try: session.execute('SELECT 3;')
    except PinnedSessionError: pass
    else: raise RuntimeError('poisoned session was reused')
    print('pinned session rejected container restart and remained poisoned')
finally:
    if target is not None: target.close()
    if emergency is not None: emergency.close()
    if session is not None: session.close()
    inspect(stable=False)
    run(D + ['stop', '--time', '3', cid])
    before = run(D + ['info', '--format', '{{.ID}}'], check=False)
    gone = run(D + ['inspect', '--type', 'container', cid], check=False)
    after = run(D + ['info', '--format', '{{.ID}}'], check=False)
    errors = {f'Error: No such object: {cid}', f'Error: No such container: {cid}',
              f'Error response from daemon: No such container: {cid}'}
    if (before.returncode or after.returncode or before.stdout.strip() != daemon or after.stdout.strip() != daemon or
            gone.returncode == 0 or gone.stderr.strip() not in errors):
        raise RuntimeError('temporary container absence unconfirmed')
