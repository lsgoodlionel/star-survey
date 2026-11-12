"""Local Engine API exec with persisted identity, bounded output, no CLI fallback.

The caller supplies an already verified container/operation binding and keeps
the output lease after UncertainExec. This module cannot assert global cohort
quiescence or make an unknown operation safe to clean up.
"""
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import json
import os
import re
import socket
import secrets
import shlex
import stat
import time
import uuid

from owned_children import _pid, _start


class ExecProtocolError(RuntimeError):
    pass


class ExecMetadataGone(ExecProtocolError):
    """Only an exact ExecInspect HTTP 404, never a transport/daemon failure."""


class KnownExecFailure(OSError):
    """Exec start was never called, or exact exec end was confirmed."""


class UncertainExec(RuntimeError):
    """Retain lease and released receipt; absence is not completion evidence."""


HEX = re.compile(r'[0-9a-f]{64}')
CLOSE_SCRIPT = (
    "import hashlib,hmac,json,sys,time,urllib.request;"
    "b=sys.argv[1].encode();t=str(int(time.time()));"
    "s=open('/run/secrets/platform_pubgw_secret','rb').read().strip();"
    "q=urllib.request.Request('http://127.0.0.1:8080/v1/close',data=b,method='POST',headers={"
    "'Content-Type':'application/json','X-Pubgw-Timestamp':t,'X-Pubgw-Signature':"
    "hmac.new(s,t.encode()+b'.'+b,hashlib.sha256).hexdigest()});"
    "r=urllib.request.urlopen(q,timeout=60);sys.exit(0 if r.status==200 else 1)"
)
PROFILES = {
    'exec-preflight': (5, ['/bin/echo', 'survey-owned-exec-v1']),
    'postgres-dump': (120, ['pg_dump', '-U', 'postgres', '--format=custom', '--serializable-deferrable', 'platform']),
    'mariadb-dump': (120, ['/bin/sh', '-c',
        'password=$(cat /run/secrets/engine_db_root_password) || exit 70; '
        '[ -n "$password" ] || exit 70; '
        'exec mariadb-dump --single-transaction --quick --skip-lock-tables -u root --password="$password" limesurvey']),
    'postgres-schema': (15, ['psql', '-U', 'postgres', '-d', 'platform', '-Atqc',
        'SELECT version FROM flyway_schema_history WHERE success ORDER BY installed_rank DESC LIMIT 1']),
    'engine-status': (30, ['php', 'application/commands/console.php', 'productionInit', 'status']),
    'engine-event-cron': (75, ['/bin/sh', '/opt/survey/run-with-secrets.sh',
        'MJY_PLATFORM_EVENTS_SECRET=/run/secrets/engine_instance_events_secret', '--',
        'php', 'application/commands/console.php', 'plugin', 'cron']),
    'gateway-close': (75, ['python', '-c', CLOSE_SCRIPT]),
}


class PayloadProof:
    PREFIX = b'\nSURVEY-EXEC-SHA256:'
    SIZE = len(PREFIX) + 64 + 1 + 64 + 1

    def __init__(self, nonce, output, limit):
        if not isinstance(nonce, str) or not HEX.fullmatch(nonce):
            raise ExecProtocolError('exec stream binding is invalid')
        self.nonce, self.output, self.limit = nonce, output, limit
        self.tail, self.total, self.closed = b'', 0, False
        self.digest = hashlib.sha256()

    def feed(self, block):
        if self.closed or not isinstance(block, bytes) or self.total + len(block) > self.limit + self.SIZE:
            raise ExecProtocolError('exec output proof exceeded its bound')
        self.total += len(block)
        pending = self.tail + block
        if len(pending) > self.SIZE:
            payload, pending = pending[:-self.SIZE], pending[-self.SIZE:]
            self.digest.update(payload)
            self.output(payload)
        self.tail = pending

    def finish(self):
        expected = self.PREFIX + self.nonce.encode('ascii') + b':' + self.digest.hexdigest().encode('ascii') + b'\n'
        if self.closed or self.tail != expected:
            raise ExecProtocolError('exec output completion proof is absent or invalid')
        self.closed = True


def command_for(profile, nonce, *, body=None):
    seconds, command = PROFILES[profile]
    if profile == 'gateway-close':
        if (not isinstance(body, dict) or set(body) != {'requestId', 'engineInstanceId', 'surveyId'}
                or not isinstance(body['requestId'], str) or str(uuid.UUID(body['requestId'])) != body['requestId']
                or not isinstance(body['engineInstanceId'], str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', body['engineInstanceId'])
                or type(body['surveyId']) is not int or not 1 <= body['surveyId'] <= 2**31 - 1):
            raise ValueError('probe close binding is invalid')
        command = [*command, json.dumps(body, sort_keys=True, separators=(',', ':'))]
    elif body is not None:
        raise ValueError('this fixed exec profile has no parameters')
    # fd3 is the original stdout. tee sends the same bytes to fd3 and sha256sum;
    # command substitution captures only the digest, never the dump. pipefail
    # forbids an end record when dump/tee/hash fails. No named plaintext/tempfile.
    script = ('set -euo pipefail; exec 3>&1; digest=$(' + shlex.join(command)
        + ' | tee /dev/fd/3 | sha256sum); exec 3>&-; '
        + 'printf "\\nSURVEY-EXEC-SHA256:%s:%s\\n" "$1" "${digest%% *}"')
    return ['/usr/bin/timeout', '--signal=KILL', str(seconds), '/bin/bash', '-c', script, 'survey-owned-exec', nonce]


def read_stream(response, output, limit):
    """Docker non-TTY multiplexing. Discard stderr, never log it."""
    stdout, stderr = 0, 0
    deadline = time.monotonic() + 125
    def exact(size):
        data = bytearray()
        while len(data) < size:
            if time.monotonic() > deadline:
                raise ExecProtocolError('exec stream deadline exceeded')
            block = response.read(min(65536, size - len(data)))
            if not block:
                raise ExecProtocolError('truncated exec stream')
            data.extend(block)
        return bytes(data)
    while True:
        header = response.read(1)
        if not header:
            return
        header += exact(7)
        size = int.from_bytes(header[4:8], 'big')
        if header[0] not in (1, 2) or header[1:4] != b'\x00\x00\x00' or size > 1024 * 1024:
            raise ExecProtocolError('invalid exec frame')
        if header[0] == 1:
            if stdout + size > limit:
                raise ExecProtocolError('exec output exceeds anonymous budget')
            stdout += size
        else:
            stderr += size
            if stderr > 65536:
                raise ExecProtocolError('exec error stream exceeds bound')
        while size:
            block = exact(min(size, 65536)); size -= len(block)
            if header[0] == 1:
                output(block)


class _UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


class DockerRequestTimeout(TimeoutError):
    def __init__(self, operation):
        self.operation = operation
        super().__init__('Docker response deadline exceeded; outcome remains unknown')


class LocalDockerAPI:
    VERSION = '/v1.47'

    def _connect(self):
        if os.environ.get('DOCKER_CONTEXT') or os.environ.get('DOCKER_HOST') not in (None, '', 'unix:///var/run/docker.sock'):
            raise ExecProtocolError('exec requires the fixed local daemon')
        return _UnixConnection('localhost', timeout=5)

    @staticmethod
    def _id(identity):
        if not isinstance(identity, str) or not HEX.fullmatch(identity):
            raise ExecProtocolError('Docker identity is invalid')
        return identity

    def _json(self, method, path, body=None, *, accepted=(200, 201)):
        connection = self._connect()
        # Connect/request retain the five-second transport timeout. Only this
        # fixed, bodyless stop acknowledgement may wait beyond its 20s grace.
        stop = (method == 'POST' and body is None
                and re.fullmatch(r'/containers/[0-9a-f]{64}/stop\?t=20', path) is not None)
        try:
            data = None if body is None else json.dumps(body, allow_nan=False).encode('ascii')
            connection.request(method, self.VERSION + path, data, {'Content-Type': 'application/json'})
            if stop:
                connection.sock.settimeout(25)
            response = connection.getresponse()
            if (response.status == 404 and method == 'GET'
                    and re.fullmatch(r'/exec/[0-9a-f]{64}/json', path)):
                raise ExecMetadataGone('exec metadata is absent')
            if stop and response.status not in (204, 304):
                raise ExecProtocolError('Docker stop acknowledgement was not accepted')
            if response.status not in accepted:
                raise ExecProtocolError('Docker request was not accepted')
            if response.status in (204, 304):
                return None
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ExecProtocolError('Docker metadata exceeded its bound')
            from runtime_ownership import PrivateStore
            return json.loads(raw, object_pairs_hook=PrivateStore._unique)
        except TimeoutError as exc:
            raise DockerRequestTimeout('container-stop' if stop else 'docker-json') from exc
        finally:
            connection.close()

    def identity(self):
        value = self._json('GET', '/info').get('ID')
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9:_.-]{1,128}', value):
            raise ExecProtocolError('daemon identity is unavailable')
        return value

    def create(self, container, command):
        value = self._json('POST', '/containers/' + self._id(container) + '/exec', {
            'Cmd': command, 'AttachStdin': False, 'AttachStdout': True, 'AttachStderr': True,
            'Tty': False, 'Privileged': False})
        if not isinstance(value, dict) or set(value) != {'Id'}:
            raise ExecProtocolError('exec create response is invalid')
        return self._id(value['Id'])

    def inspect(self, identity):
        raw = self._json('GET', '/exec/' + self._id(identity) + '/json')
        if not isinstance(raw, dict):
            raise ExecProtocolError('exec inspection is invalid')
        return {key: raw.get(key) for key in ('ID', 'ContainerID', 'Running', 'ExitCode')}

    def container_absent(self, identity):
        connection = self._connect()
        try:
            connection.request('GET', self.VERSION + '/containers/' + self._id(identity) + '/json')
            response = connection.getresponse()
            if response.status not in (200, 404):
                raise ExecProtocolError('container presence is unverified')
            return response.status == 404  # Do not read Config.Env or raw body.
        finally:
            connection.close()

    @contextmanager
    def attach_input(self, identity):
        connection = self._connect()
        try:
            connection.request('POST', self.VERSION + '/containers/' + self._id(identity)
                + '/attach?logs=0&stream=1&stdin=1&stdout=1&stderr=1', headers={
                    'Connection': 'Upgrade', 'Upgrade': 'tcp'})
            raw_socket = connection.sock
            response = connection.getresponse()
            if response.status != 101 or response.fp is None:
                raise ExecProtocolError('container input attachment was not established')
            raw_socket.settimeout(31)  # Helper's own timeout is 30 seconds; not a lifecycle hard limit.
            class Channel:
                def finish(self, payload, limit):
                    if not isinstance(payload, bytes) or len(payload) > 1025:
                        raise ExecProtocolError('helper input exceeded its bound')
                    raw_socket.sendall(payload)
                    raw_socket.shutdown(socket.SHUT_WR)
                    chunks = []
                    read_stream(response.fp, chunks.append, limit)
                    return b''.join(chunks)
            yield Channel()
        finally:
            connection.close()

    def stream(self, identity, output, limit, *, profile):
        if not isinstance(profile, str) or profile not in PROFILES:
            raise ExecProtocolError('exec stream profile is invalid')
        # Keep connect at five seconds; this fixed profile owns a separate
        # execution/read budget. No caller-supplied duration and no retry.
        budget = PROFILES[profile][0] + 5
        connection = self._connect()
        try:
            connection.request('POST', self.VERSION + '/exec/' + self._id(identity) + '/start',
                b'{"Detach":false,"Tty":false}', {'Content-Type': 'application/json'})
            raw_socket = connection.sock
            deadline = time.monotonic() + budget
            raw_socket.settimeout(budget)
            response = connection.getresponse()
            if response.status != 200:
                raise OSError('exec start result is unknown')
            class BoundedResponse:
                def read(self, size):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DockerRequestTimeout('exec-stream')
                    raw_socket.settimeout(remaining)
                    # One buffered read/recv permits deadline checks even when
                    # a peer trickles bytes instead of filling a whole frame.
                    return response.read1(size)
            read_stream(BoundedResponse(), output, limit)
        finally:
            connection.close()


class OwnedDockerExec:
    def __init__(self, api, store, guard, *, operation_id, installation_id, container_check):
        for value in (operation_id, installation_id):
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                raise KnownExecFailure('exec operation identity is invalid')
        if not callable(container_check):
            raise KnownExecFailure('verified container check is required')
        self.api, self.store, self.guard, self.check = api, store, guard, container_check
        self.actor = guard.check()
        if (not isinstance(self.actor, dict) or set(self.actor) != {'pid', 'start', 'parentPid', 'parentStart'}
                or not _pid(self.actor['pid']) or not _pid(self.actor['parentPid'])
                or not _start(self.actor['start']) or not _start(self.actor['parentStart'])):
            raise KnownExecFailure('exec owner identity is invalid')
        self.operation, self.installation, self.saved = operation_id, installation_id, None
        self.pause = time.sleep

    def _save(self, record):
        if self.guard.check() != self.actor or self.store.load() != self.saved:
            raise ExecProtocolError('exec writer or receipt changed')
        self.store.save(deepcopy(record))
        if self.store.load() != record:
            raise ExecProtocolError('exec receipt publication is unverified')
        self.saved = deepcopy(record)

    def execute(self, profile, container, output, *, body=None):
        released = False
        try:
            if (self.saved is not None or not isinstance(profile, str) or profile not in PROFILES
                    or not isinstance(container, str) or not HEX.fullmatch(container)
                    or type(output.fd) is not int or output.fd <= 2
                    or type(output.max_file_bytes) is not int or not 1 <= output.max_file_bytes <= 2**30):
                raise ValueError('unsupported exec profile or output')
            info = os.fstat(output.fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size != 0
                    or fcntl.fcntl(output.fd, fcntl.F_GETFL) & os.O_ACCMODE not in (os.O_WRONLY, os.O_RDWR)):
                raise ValueError('exec output is not an empty private anonymous file')
            nonce = secrets.token_hex(32)  # Per-stream boundary metadata, not a credential.
            command = command_for(profile, nonce, body=body)  # Validate before creating any exec.
            daemon = self.api.identity()
            if self.check(container, None) is not True:
                raise ValueError('container is not bound to this operation')
            record = {'schemaVersion': 2, 'kind': 'controlled-docker-exec', 'operationId': self.operation,
                'installationId': self.installation, 'owner': self.actor, 'daemonId': daemon,
                'containerId': container, 'profile': profile, 'phase': 'create-pending', 'execId': None, 'exitCode': None,
                'completionEvidence': None}
            self._save(record)
            identity = self.api.create(container, command)
            if not isinstance(identity, str) or not HEX.fullmatch(identity):
                raise ValueError('exec identity is invalid')
            record.update(phase='created', execId=identity)
            self._save(record)
            if self.api.identity() != daemon or self.check(container, identity) is not True:
                raise ValueError('container or daemon changed before exec start')
            record['phase'] = 'released'
            self._save(record)
            released = True
            offset = 0
            def write(block):
                nonlocal offset
                actual = os.fstat(output.fd)
                if ((actual.st_dev, actual.st_ino, actual.st_uid, actual.st_mode) !=
                        (info.st_dev, info.st_ino, info.st_uid, info.st_mode) or actual.st_nlink != 0):
                    raise ExecProtocolError('exec output descriptor changed')
                if offset + len(block) > output.max_file_bytes:
                    raise ExecProtocolError('exec output budget exceeded')
                count = os.pwrite(output.fd, block, offset)
                if count != len(block):
                    raise ExecProtocolError('exec output write was incomplete')
                offset += count
            protocol_failure = False
            proof = PayloadProof(nonce, write, output.max_file_bytes)
            try:
                self.api.stream(identity, proof.feed, output.max_file_bytes + PayloadProof.SIZE, profile=profile)
                proof.finish()
            except ExecProtocolError:
                protocol_failure = True
            for attempt in range(20):
                ended = self.api.inspect(identity)
                if (not isinstance(ended, dict) or ended.get('ID') != identity
                        or ended.get('ContainerID') != container or type(ended.get('Running')) is not bool):
                    raise UncertainExec('exec end identity is unverified')
                if ended['Running'] is False and type(ended.get('ExitCode')) is int and 0 <= ended['ExitCode'] <= 255:
                    break
                if attempt == 19:
                    raise UncertainExec('exec end was not observed')
                self.pause(0.1)
            if self.api.identity() != daemon or self.guard.check() != self.actor:
                raise UncertainExec('exec daemon or owner changed')
            actual = os.fstat(output.fd)
            if ((actual.st_dev, actual.st_ino, actual.st_uid, actual.st_mode, actual.st_nlink, actual.st_size) !=
                    (info.st_dev, info.st_ino, info.st_uid, info.st_mode, 0, offset)):
                raise UncertainExec('exec output identity or size changed')
            code = ended['ExitCode']
            # Publish only after the exact terminal ExecInspect, daemon/actor
            # recheck and output identity check above. A phase flag is not this
            # observation, and an unknown operation never receives it.
            record.update(phase='failed' if code or protocol_failure else 'completed', exitCode=code,
                completionEvidence={'kind': 'exec-inspect-terminal-v1', 'ID': identity,
                                    'ContainerID': container, 'Running': False, 'ExitCode': code})
            self._save(record)
            if code or protocol_failure:
                raise KnownExecFailure('exec ended unsuccessfully')
        except (KnownExecFailure, UncertainExec):
            raise
        except BaseException as exc:
            if released:
                raise UncertainExec('exec result is unknown; retain output lease') from exc
            raise KnownExecFailure('exec was not started; ownership intent remains') from exc
