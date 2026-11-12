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
import selectors
import shlex
import stat
import sys
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
DATABASE_INPUT_PROFILES = frozenset({'postgres-import', 'mariadb-import'})
INPUT_PROFILES = DATABASE_INPUT_PROFILES | {'stdin-proof'}
_MARIA_CLIENT = ('password=$(cat /run/secrets/engine_db_root_password) || exit 70; '
    '[ -n "$password" ] || exit 70; MYSQL_PWD="$password" exec mariadb --no-defaults --binary-mode --skip-reconnect --local-infile=0 --protocol=socket --socket=/run/mysqld/mysqld.sock -u root limesurvey')


def _database_consumer(command):
    # Dynamic process-substitution FD is inherited by tee; Bash deliberately
    # hides coprocess descriptors from pipeline children. Both hash pipeline
    # and the fixed SQL client are waited before any acknowledgement.
    script = ('set -euo pipefail; dbfd=""; dbpid=""; '
        'finish(){ rc=$?; trap - EXIT; if [ -n "$dbfd" ]; then exec {dbfd}>&-; fi; '
        'pending=${dbpid:-${!:-}}; if [ -n "$pending" ] && [ "$pending" != 0 ]; '
        'then wait "$pending" || :; fi; exit "$rc"; }; trap finish EXIT; '
        'exec {dbfd}> >(' + shlex.join(command) + ' 1>&2); dbpid=$!; '
        'digest=$(tee /dev/fd/"$dbfd" | sha256sum); '
        'exec {dbfd}>&-; dbfd=""; wait "$dbpid"; dbpid=0; '
        'printf "%s  -\\n" "${digest%% *}"')
    return ['/bin/bash', '-c', script]

PROFILES = {
    'postgres-empty': (15, ['psql', '-X', '-w', '-h', '/var/run/postgresql', '-U', 'postgres', '-d', 'platform', '-Atqc', "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"]),
    'mariadb-empty': (15, ['/bin/sh', '-c', _MARIA_CLIENT + " -N -B -e \"SELECT count(*) FROM information_schema.tables WHERE table_schema='limesurvey'\""]),
    'postgres-import': (120, _database_consumer(['pg_restore', '--no-password', '-h', '/var/run/postgresql', '-U', 'postgres', '-d', 'platform', '--single-transaction', '--exit-on-error', '--clean', '--if-exists'])),
    'mariadb-import': (120, _database_consumer(['/bin/sh', '-c', _MARIA_CLIENT])),
    'stdin-proof': (30, ['sha256sum']),
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


def _input_seals(fd):
    names = ('F_GET_SEALS', 'F_SEAL_WRITE', 'F_SEAL_GROW', 'F_SEAL_SHRINK', 'F_SEAL_SEAL')
    if not all(hasattr(fcntl, name) for name in names):
        return False
    required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    return fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required == required


class SealedExecInput:
    """Pin one sealed anonymous input. No named-file or portable fallback.

    The expected hash is a caller-supplied binding, not backup authentication.
    The trusted exclusive controller retains its workspace on unknown results.
    """
    def __init__(self, lease, expected):
        self.fd = None
        if (type(lease.fd) is not int or lease.fd <= 2 or type(lease.max_file_bytes) is not int
                or not 1 <= lease.max_file_bytes <= 2**30 or not isinstance(expected, str)
                or not HEX.fullmatch(expected)):
            raise ExecProtocolError('input binding is invalid')
        self.original, self.size, self.sha256 = lease.fd, lease.max_file_bytes, expected
        self.fd = os.dup(lease.fd)
        try:
            os.set_inheritable(self.fd, False)
            info = os.fstat(self.fd)
            self.identity = (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_size)
            self.check()
            digest = hashlib.sha256()
            for block in self.chunks(): digest.update(block)
            if digest.hexdigest() != expected:
                raise ExecProtocolError('input digest does not match its binding')
        except BaseException:
            self.close()
            raise

    def check(self):
        for fd in (self.original, self.fd):
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size != self.size
                    or (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_size) != self.identity
                    or not _input_seals(fd) or os.get_inheritable(fd)):
                raise ExecProtocolError('sealed input identity is unverified')

    def chunks(self):
        offset = 0
        while offset < self.size:
            self.check()
            block = os.pread(self.fd, min(65536, self.size - offset), offset)
            if not block: raise ExecProtocolError('input ended before its bound')
            offset += len(block)
            yield block
        self.check()

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None: os.close(fd)


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


class _DeadlineReader:
    """Preserve the existing buffered bytes and bound every chunk-parser read."""
    def __init__(self, buffered, raw_socket, deadline):
        self.buffered, self.socket, self.deadline = buffered, raw_socket, deadline

    def read1(self, size):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DockerRequestTimeout('exec-stream')
        self.socket.settimeout(remaining)
        block = self.buffered.read1(size)
        if time.monotonic() >= self.deadline:
            raise DockerRequestTimeout('exec-stream')
        return block

    def read(self, size):
        if type(size) is not int or size < 0:
            raise ExecProtocolError('unbounded exec parser read is unsupported')
        data = bytearray()
        while len(data) < size:
            block = self.read1(min(65536, size - len(data)))
            if not block: break
            data.extend(block)
        return bytes(data)

    def readline(self, limit):
        if type(limit) is not int or not 0 < limit <= 65537:
            raise ExecProtocolError('unbounded exec parser line is unsupported')
        data = bytearray()
        while len(data) < limit:
            block = self.read1(1)
            if not block: break
            data.extend(block)
            if block == b'\n': break
        return bytes(data)

    def flush(self):
        return self.buffered.flush()

    def close(self):
        self.buffered.close()


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


class _InputFrameDecoder:
    """Incremental bounded stdout, discarded bounded stderr, no raw logging."""
    def __init__(self, output, limit):
        if type(limit) is not int or not 1 <= limit <= 2**30 + PayloadProof.SIZE:
            raise ExecProtocolError('duplex output bound is invalid')
        self.output, self.limit = output, limit
        self.header, self.channel, self.remaining = bytearray(), None, 0
        self.stdout, self.stderr = 0, 0

    def feed(self, block):
        while block:
            if self.remaining == 0:
                count = min(8 - len(self.header), len(block))
                self.header.extend(block[:count]); block = block[count:]
                if len(self.header) != 8: continue
                header = self.header; self.header = bytearray()
                size = int.from_bytes(header[4:8], 'big')
                if header[0] not in (1, 2) or header[1:4] != b'\0\0\0' or size > 1024 * 1024:
                    raise ExecProtocolError('invalid duplex exec frame')
                self.channel, self.remaining = header[0], size
                if self.channel == 1:
                    self.stdout += size
                    if self.stdout > self.limit: raise ExecProtocolError('duplex stdout exceeds bound')
                else:
                    self.stderr += size
                    if self.stderr > 65536: raise ExecProtocolError('duplex stderr exceeds bound')
                if not size: continue
            count = min(self.remaining, len(block))
            if self.channel == 1: self.output(block[:count])
            self.remaining -= count; block = block[count:]

    def finish(self):
        if self.header or self.remaining:
            raise ExecProtocolError('truncated duplex exec frame')


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

    def create(self, container, command, *, stdin=False):
        if type(stdin) is not bool:
            raise ExecProtocolError('stdin mode is invalid')
        value = self._json('POST', '/containers/' + self._id(container) + '/exec', {
            'Cmd': command, 'AttachStdin': stdin, 'AttachStdout': True, 'AttachStderr': True,
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

    def stream_input(self, identity, output, limit, *, profile, source):
        return self.stream_duplex_input(identity, output, limit, profile=profile, source=source)

    def stream_duplex_input(self, identity, output, limit, *, profile, source):
        # One event loop, not background threads holding caller FDs after an
        # exception. Buffered HTTP bytes are consumed before readiness waits.
        if profile not in INPUT_PROFILES or not isinstance(source, SealedExecInput):
            raise ExecProtocolError('unsupported duplex input consumer')
        decoder = _InputFrameDecoder(output, limit)
        connection = self._connect()
        try:
            connection.request('POST', self.VERSION + '/exec/' + self._id(identity) + '/start',
                b'{"Detach":false,"Tty":false}', {'Content-Type': 'application/json',
                    'Connection': 'Upgrade', 'Upgrade': 'tcp'})
            raw = connection.sock
            deadline = time.monotonic() + PROFILES[profile][0] + 5
            def remaining():
                value = deadline - time.monotonic()
                if value <= 0: raise DockerRequestTimeout('exec-duplex-input')
                return value
            raw.settimeout(remaining())
            response = connection.getresponse()
            if (response.status != 101 or response.fp is None
                    or (response.getheader('Upgrade') or '').lower() != 'tcp'
                    or response.getheader('Transfer-Encoding') is not None):
                raise OSError('duplex exec was not established; outcome remains unknown')
            raw.setblocking(False)
            chunks, pending, eof = iter(source.chunks()), b'', False
            # read1 on a nonblocking buffered reader returns b'' for EAGAIN.
            # Here it only drains read-ahead; EOF is decided on READ readiness.
            while True:
                remaining()
                block = response.fp.read1(65536)
                if not block: break
                decoder.feed(block)
                remaining()
            finished = False
            with selectors.DefaultSelector() as poll:
                poll.register(raw, selectors.EVENT_READ | selectors.EVENT_WRITE)
                while not finished:
                    events = poll.select(remaining())
                    remaining()
                    for _, ready in events:
                        if ready & selectors.EVENT_READ:
                            block = response.fp.read1(65536)
                            remaining()
                            if not block:
                                if not eof: raise ExecProtocolError('consumer closed before input EOF')
                                source.check(); decoder.finish(); remaining()
                                finished = True
                                break
                            decoder.feed(block)
                        if ready & selectors.EVENT_WRITE and not eof:
                            if not pending:
                                try: pending = next(chunks)
                                except StopIteration:
                                    source.check(); remaining()
                                    raw.shutdown(socket.SHUT_WR)
                                    eof = True
                                    poll.modify(raw, selectors.EVENT_READ)
                            if pending:
                                remaining()
                                try: count = raw.send(pending)
                                except BlockingIOError: count = None
                                if count == 0: raise OSError('input send made no progress')
                                if count is not None: pending = pending[count:]
                        remaining()
        except BaseException:
            try: connection.close()
            except Exception: pass
            raise
        else:
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
            coding = response.getheader('Transfer-Encoding')
            if coding is not None and (coding.lower() != 'chunked' or response.chunked is not True):
                raise OSError('exec transfer coding is unsupported; outcome remains unknown')
            # A local Engine proxy may use chunked encoding even for hijack.
            # HTTPResponse.read1 can parse multiple chunk/trailer lines; wrap
            # its *existing* buffered reader so none gets a fresh full budget.
            response.fp = _DeadlineReader(response.fp, raw_socket, deadline)
            class BoundedResponse:
                def read(self, size):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DockerRequestTimeout('exec-stream')
                    raw_socket.settimeout(remaining)
                    # One buffered read/recv permits deadline checks even when
                    # a peer trickles bytes instead of filling a whole frame.
                    block = response.read1(size)
                    if time.monotonic() >= deadline:
                        raise DockerRequestTimeout('exec-stream')
                    return block
            read_stream(BoundedResponse(), output, limit)
        except BaseException:
            try:
                connection.close()
            except Exception:
                # Already unknown/failed. Preserve the primary exception, do
                # not retry a close whose outcome is uncertain, and do not
                # claim cleanup succeeded or release the caller's lease.
                pass
            raise
        else:
            connection.close()  # A close failure must not become success.


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

    def execute(self, profile, container, output, *, body=None, input_lease=None, input_sha256=None):
        released, source, authenticated = False, None, None
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
            if profile in DATABASE_INPUT_PROFILES:
                from controlled_database_import import VerifiedDatabaseInput
                if not isinstance(input_lease, VerifiedDatabaseInput) or input_sha256 != input_lease.sha256:
                    raise ValueError('database input requires the authenticated single-use binding')
                authenticated = input_lease
                authenticated.claim(profile, container, self.operation, self.installation)
            if profile in INPUT_PROFILES:
                if input_lease is None:
                    raise ValueError('fixed input consumer requires a sealed input')
                source = SealedExecInput(input_lease, input_sha256)
                if source.fd == output.fd or source.identity[:2] == (info.st_dev, info.st_ino):
                    raise ValueError('input and output must be distinct')
            elif input_lease is not None or input_sha256 is not None:
                raise ValueError('output-only profile cannot consume input')
            nonce = secrets.token_hex(32)  # Per-stream boundary metadata, not a credential.
            command = command_for(profile, nonce, body=body)  # Validate before creating any exec.
            daemon = self.api.identity()
            if self.check(container, None) is not True:
                raise ValueError('container is not bound to this operation')
            record = {'schemaVersion': 2, 'kind': 'controlled-docker-exec', 'operationId': self.operation,
                'installationId': self.installation, 'owner': self.actor, 'daemonId': daemon,
                'containerId': container, 'profile': profile, 'phase': 'create-pending', 'execId': None, 'exitCode': None,
                'completionEvidence': None}
            if source is not None:
                record['schemaVersion'] = 3
                record['inputEvidence'] = {'size': source.size, 'sha256': source.sha256, 'delivered': False}
                if authenticated is not None:
                    record['schemaVersion'] = 4
                    record['sourceEvidence'] = deepcopy(authenticated.source_evidence)
            # Input binding is durable before the one create attempt.
            if source is not None:
                self._save(record)
                identity = self.api.create(container, command, stdin=True)
            else:
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
                if source is None:
                    self.api.stream(identity, proof.feed, output.max_file_bytes + PayloadProof.SIZE, profile=profile)
                else:
                    self.api.stream_input(identity, proof.feed, output.max_file_bytes + PayloadProof.SIZE,
                        profile=profile, source=source)
                proof.finish()
                if source is not None:
                    source.check()
                    if os.pread(output.fd, 69, 0) != (source.sha256 + '  -\n').encode():
                        raise ExecProtocolError('input consumer digest acknowledgement is invalid')
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
            if source is not None and not code and not protocol_failure:
                source.check()
                record['inputEvidence']['delivered'] = True
            if source is not None:
                source.close()  # A release failure cannot leave a successful receipt.
            record.update(phase='failed' if code or protocol_failure else 'completed', exitCode=code,
                completionEvidence={'kind': 'exec-inspect-terminal-v1', 'ID': identity,
                                    'ContainerID': container, 'Running': False, 'ExitCode': code})
            self._save(record)
            if code or protocol_failure:
                raise KnownExecFailure('exec ended unsuccessfully')
        except (KnownExecFailure, UncertainExec) as exc:
            if authenticated is not None and isinstance(exc, UncertainExec): authenticated.unknown()
            raise
        except BaseException as exc:
            if released:
                if authenticated is not None: authenticated.unknown()
                raise UncertainExec('exec result is unknown; retain output lease') from exc
            raise KnownExecFailure('exec was not started; ownership intent remains') from exc

        finally:
            if source is not None:
                primary = sys.exc_info()[1]
                try:
                    source.close()
                except BaseException as close_error:
                    # Never replace an uncertain send/end with a plain close
                    # error. A caller must keep both leases and the pending
                    # receipt. No retry of a potentially reused descriptor.
                    if authenticated is not None: authenticated.unknown()
                    if not isinstance(primary, UncertainExec):
                        raise UncertainExec('input reference release is unknown; retain both leases') from close_error
