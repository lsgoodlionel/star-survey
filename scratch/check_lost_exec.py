#!/usr/bin/env python3
"""Hosted-only disposable PostgreSQL experiment; never operates on candidate instances."""
import json
import os
import re
import secrets
import subprocess
import time

if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true' or
    os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or
    os.environ.get('GITHUB_REPOSITORY') != 'lsgoodlionel/star-survey'):
    raise RuntimeError('dedicated hosted runner required')
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
    app = 'probe_' + nonce
    client = subprocess.Popen(docker + ['exec', '-i', '-e', 'PGAPPNAME=' + app, cid, 'psql', '-XAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client.stdin.write(b'SELECT pg_sleep(8); INSERT INTO lost_probe VALUES (1);\n'); client.stdin.flush()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        observed = sql("SELECT pid::text || ',' || backend_start::text FROM pg_stat_activity WHERE application_name='" + app + "' AND wait_event='PgSleep';")
        if observed: break
        time.sleep(.1)
    else: raise RuntimeError('active backend not observed')
    client.kill(); client.wait(timeout=5)
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
