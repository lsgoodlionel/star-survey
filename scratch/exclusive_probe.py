#!/usr/bin/env python3
"""Disposable CI experiment only; assumes a trusted runner and Docker daemon."""
import json, os, re, secrets, select, subprocess, time

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
                                  '-U', 'postgres', '-d', db, '-v', 'ON_ERROR_STOP=1'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buffer = b''
    def sql(self, sql):
        self.p.stdin.write((sql + '\n\\echo FIXTURE_END\n').encode()); self.p.stdin.flush()
        marker = b'FIXTURE_END\n'; deadline = time.monotonic() + 10
        while marker not in self.buffer:
            remain = deadline - time.monotonic()
            if remain <= 0 or not select.select([self.p.stdout], [], [], remain)[0]:
                raise RuntimeError('session did not respond')
            chunk = os.read(self.p.stdout.fileno(), 4096)
            if not chunk: raise RuntimeError('session ended')
            self.buffer += chunk
            if len(self.buffer) > 8192: raise RuntimeError('unexpected session output')
        output, self.buffer = self.buffer.split(marker, 1)
        return output.decode().strip()
    def close(self):
        if self.p.poll() is None:
            self.p.stdin.close(); self.p.wait(timeout=10)

def main():
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or os.environ.get('GITHUB_REPOSITORY') != 'lsgoodlionel/star-survey'):
        raise RuntimeError('requires dedicated GitHub-hosted repository runner')
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
        pid = supervisor.sql('SELECT pg_backend_pid();'); stale_pid = stale.sql('SELECT pg_backend_pid();')
        if not re.fullmatch(r'[0-9]+', pid) or not re.fullmatch(r'[0-9]+', stale_pid) or pid == stale_pid: raise RuntimeError('bad client identity')
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
