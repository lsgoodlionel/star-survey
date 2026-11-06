#!/usr/bin/env python3
"""Hosted-only disposable PostgreSQL experiment; never operates on candidate instances."""
import ast
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time

if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true' or
    os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or
    os.environ.get('GITHUB_REPOSITORY') != 'lsgoodlionel/star-survey'):
    raise RuntimeError('dedicated hosted runner required')
sys.path.insert(0, str(Path(os.environ['GITHUB_WORKSPACE']) / 'platform/deploy/production/release'))
from fixture_pinned_database import PinnedDatabaseSession, PinnedSessionError

docker = ['docker', '--host', 'unix:///var/run/docker.sock']
def run(args, *, check=True):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if check and p.returncode:
        raise RuntimeError('disposable container operation failed')
    return p

def sql(query):
    return run(docker + ['exec', cid, 'psql', '-XAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres', '-c', query]).stdout.strip()

daemon = run(docker + ['info', '--format', '{{.ID}}']).stdout.strip()
image = run(docker + ['image', 'inspect', 'postgres:16-alpine', '--format', '{{.Id}}']).stdout.strip()
if not daemon or not re.fullmatch(r'sha256:[0-9a-f]{64}', image): raise RuntimeError('identity invalid')
nonce = secrets.token_hex(16)
cid = run(docker + ['run', '-d', '--rm', '--network', 'none', '--tmpfs', '/var/lib/postgresql/data',
                    '--label', 'survey.lost-exec.probe=' + nonce, '-e', 'POSTGRES_PASSWORD=temporary-probe', image]).stdout.strip()
if not re.fullmatch(r'[0-9a-f]{64}', cid): raise RuntimeError('container identity invalid')
started = None

def verify():
    global started
    item = json.loads(run(docker + ['inspect', '--type', 'container', cid]).stdout)[0]
    if (item['Id'] != cid or item['Image'] != image or item['Config']['Labels'].get('survey.lost-exec.probe') != nonce or
        item['HostConfig']['NetworkMode'] != 'none' or item['HostConfig']['AutoRemove'] is not True or
        set(item['HostConfig'].get('Tmpfs') or {}) != {'/var/lib/postgresql/data'} or item['Mounts'] or
        item['State']['Running'] is not True): raise RuntimeError('container identity changed')
    if started is None: started = item['State']['StartedAt']
    if started != item['State']['StartedAt']: raise RuntimeError('container restarted')
    if run(docker + ['info', '--format', '{{.ID}}']).stdout.strip() != daemon: raise RuntimeError('daemon changed')

client = None
session = None
try:
    verify()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        logs = run(docker + ['logs', cid], check=False)
        if logs.returncode == 0 and 'postgresql init process complete; ready for start up' in (logs.stdout + logs.stderr).lower():
            if run(docker + ['exec', cid, 'pg_isready', '-U', 'postgres'], check=False).returncode == 0: break
        time.sleep(.2)
    else: raise RuntimeError('database not ready')
    sql('CREATE TABLE lost_probe (value integer NOT NULL);')
    run(docker + ['exec', cid, 'sh', '-c', 'mkdir -p /run/secrets && printf %s temporary-probe > /run/secrets/platform_db_superuser_password && chmod 600 /run/secrets/platform_db_superuser_password'])
    session = PinnedDatabaseSession(cid, 'postgres', lambda expected: expected == cid and verify() is None)
    client = session.process
    outcomes = []
    def issue():
        try: session.execute('SELECT pg_sleep(8); INSERT INTO lost_probe VALUES (1);')
        except PinnedSessionError: outcomes.append('unverified')
    worker = threading.Thread(target=issue)
    worker.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        observed = sql("SELECT count(*) FROM pg_stat_activity WHERE pid=" + str(session.identity['pid']) + " AND backend_start::text='" + session.identity['started'] + "' AND wait_event='PgSleep';")
        if observed == '1': break
        time.sleep(.1)
    else: raise RuntimeError('active bound backend not observed')
    client.kill(); client.wait(timeout=5); worker.join(timeout=10)
    if worker.is_alive() or outcomes != ['unverified'] or not session.poisoned:
        raise RuntimeError('lost session did not fail closed')
    # Exercise the exact failure function from the same commit. Sentinel
    # sessions and callbacks reject any follow-up SQL or Docker operation.
    source = Path(os.environ['GITHUB_WORKSPACE']) / 'scratch_candidate_stop.py'
    tree = ast.parse(source.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'isolate_after_failure')
    class Sentinel:
        poisoned = False
        def execute(self, _): raise RuntimeError('unexpected SQL after lost session')
    def forbidden(*_args, **_kwargs): raise RuntimeError('unexpected Docker or uninstall after lost session')
    messages = []
    env = {'admin': session, 'target': Sentinel(), 'emergency': Sentinel(), 'emergency_target': Sentinel(),
           'run': forbidden, 'check_platform_identity': forbidden, 'print': messages.append}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), env)
    env['isolate_after_failure']()
    if messages != ['diagnostic: database session lost; instance unrecoverable, checkpoint remains pending']:
        raise RuntimeError('lost-session disposition invalid')
    print('actual stop failure function refused further SQL and Docker after real pinned-session loss')
    # This observer is strictly a test witness, never a recovery action. The
    # instance is discarded even when the delayed insert is observed.
    deadline = time.monotonic() + 15
    late = False
    while time.monotonic() < deadline:
        if sql('SELECT count(*) FROM lost_probe;') == '1': late = True; break
        time.sleep(.2)
    if not late: raise RuntimeError('delayed completion not observed; outcome unverified')
    print('host CLI terminated while backend was sleeping; delayed server insert observed')
    print('instance remains unrecoverable; no recovery or gate operation attempted')
finally:
    if client is not None and client.poll() is None:
        client.kill(); client.wait(timeout=5)
    verify()
    run(docker + ['stop', '--time', '5', cid])
    if run(docker + ['info', '--format', '{{.ID}}']).stdout.strip() != daemon: raise RuntimeError('daemon changed after stop')
    absent = run(docker + ['inspect', '--type', 'container', cid], check=False)
    if absent.returncode == 0 or ('No such container: ' + cid) not in absent.stderr:
        raise RuntimeError('disposable container absence unconfirmed')
    print('verified disposable container absent')
