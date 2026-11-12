"""Fail-closed lifecycle protocol; observations must come from a trusted adapter.

This module does not turn caller-supplied labels or JSON into ownership proof.
The adapter must bind actual daemon IDs, creation observations and cohort state;
the store must be private, durable and protected by the installation lock.
Controller integration and native evidence are separate requirements.
"""
from copy import deepcopy
import hashlib
import json
import os
import stat
import re
import uuid
from pathlib import Path


class OwnershipError(RuntimeError):
    pass


class SettledMutationFailure(OwnershipError):
    """Internal caller proved all mutating requests ended before this failure.

    Never use for a lost reply, failed startup command, unknown helper/exec, or
    a guessed empty inventory. Post-observation must still succeed.
    """


PROJECT = 'survey-production'
CLAIM_NAME = PROJECT + '-ownership-v1'
KINDS = ('containers', 'networks', 'volumes')
HEX64 = re.compile(r'[0-9a-f]{64}')
PHASES = {'claim-pending', 'ready', 'mutation-pending', 'settled-failure', 'cleanup-pending', 'retained-data', 'removed'}


def project_name(value=None):
    """Internal explicit scope, never derived from an existing ownership record."""
    value = PROJECT if value is None else value
    if not isinstance(value, str) or (value != PROJECT and re.fullmatch(r'survey-recovery-[0-9a-f]{32}', value) is None):
        raise OwnershipError('ownership project is not an explicit recovery namespace')
    return value


def canonical_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


class OwnershipProtocol:
    def __init__(self, target, driver, store, image, *, project=None):
        path = Path(target)
        if not path.is_absolute() or '..' in path.parts:
            raise OwnershipError('installation binding is invalid')
        if not isinstance(image, str) or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image):
            raise OwnershipError('ownership image must be digest pinned')
        self.target_hash = hashlib.sha256(str(path).encode('utf-8')).hexdigest()
        self.project = project_name(project)
        self.claim_name = self.project + '-ownership-v1'
        if getattr(driver, 'project', None if self.project.startswith('survey-recovery-') else self.project) != self.project:
            raise OwnershipError('ownership adapter project differs')
        self.driver, self.store, self.image = driver, store, image

    @staticmethod
    def _call(function, *args):
        try:
            return function(*args)
        except Exception as exc:
            raise OwnershipError('ownership operation failed or its outcome is unknown') from exc

    def _save(self, record):
        self._call(self.store.save, deepcopy(record))

    def _daemon(self):
        value = self._call(self.driver.identity)
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9:_.-]{1,128}', value):
            raise OwnershipError('daemon identity is unavailable')
        return value

    def _resources(self, value):
        if not isinstance(value, dict) or set(value) != set(KINDS):
            raise OwnershipError('resource observation shape is invalid')
        for kind, objects in value.items():
            if not isinstance(objects, dict) or len(objects) > 128:
                raise OwnershipError('resource observation is not bounded')
            for identity, fingerprint in objects.items():
                valid_id = (isinstance(identity, str) and (
                    bool(HEX64.fullmatch(identity)) if kind != 'volumes' else
                    bool(re.fullmatch(re.escape(self.project) + r'_[A-Za-z0-9_.-]{1,128}', identity))))
                if not valid_id or not isinstance(fingerprint, str) or not HEX64.fullmatch(fingerprint):
                    raise OwnershipError('resource identity is invalid')
        return deepcopy(value)

    def _observe(self):
        value = self._call(self.driver.observe)
        if (not isinstance(value, dict) or set(value) != {*KINDS, 'cohort', 'foreign'}
                or value['cohort'] != 'quiescent' or type(value['foreign']) is not bool or value['foreign']):
            raise OwnershipError('resource ownership or process quiescence is unverified')
        return self._resources({kind: value[kind] for kind in KINDS})

    @staticmethod
    def _binding(record):
        return {key: record[key] for key in ('daemonId', 'installationId', 'targetSha256')}

    def _assert_claim(self, record):
        claim = self._call(self.driver.inspect_claim, record['claimId'])
        expected = {'id': record['claimId'], 'name': self.claim_name,
                    'binding': self._binding(record), 'image': record['image'],
                    'stopped': True, 'isolated': True}
        if (not isinstance(claim, dict) or set(claim) != set(expected) or claim != expected
                or claim['stopped'] is not True or claim['isolated'] is not True):
            raise OwnershipError('daemon claim identity or isolation changed')

    def _record(self, phases):
        record = self._call(self.store.load)
        keys = {'schemaVersion', 'project', 'daemonId', 'installationId', 'targetSha256',
                'claimId', 'image', 'phase', 'resources'}
        if (not isinstance(record, dict) or set(record) not in (keys, keys | {'retiredInstallations'})
                or type(record['schemaVersion']) is not int or record['schemaVersion'] != 1
                or record['project'] != self.project or not isinstance(record['phase'], str) or record['phase'] not in PHASES
                or record['phase'] not in phases or record['targetSha256'] != self.target_hash
                or record['daemonId'] != self._daemon() or record['image'] != self.image
                or not isinstance(record['claimId'], str) or not HEX64.fullmatch(record['claimId'])):
            raise OwnershipError('installation ownership is absent, pending or mismatched')
        try:
            if str(uuid.UUID(record['installationId'])) != record['installationId']:
                raise ValueError('noncanonical identity')
        except (ValueError, TypeError, AttributeError) as exc:
            raise OwnershipError('installation identity is invalid') from exc
        retired = record.get('retiredInstallations', [])
        if (not isinstance(retired, list) or len(retired) > 16
                or any(not canonical_uuid(value) or value == record['installationId'] for value in retired)
                or len(set(retired)) != len(retired)):
            raise OwnershipError('retired installation history is invalid')
        self._resources(record['resources'])
        if record['phase'] == 'removed':
            if any(record['resources'][kind] for kind in KINDS) or self._call(self.driver.inspect_claim, record['claimId']) is not None:
                raise OwnershipError('removed generation still has runtime resources or claim')
        else:
            self._assert_claim(record)
        if self._observe() != record['resources']:
            raise OwnershipError('resource identity drifted or an unknown object is present')
        return deepcopy(record)

    def acquire(self):
        # No existing record or object is adopted, including a lost create reply.
        previous = self._call(self.store.load)
        retired = None
        if previous is not None:
            previous = self._record({'removed'})
            retired = previous.get('retiredInstallations', []) + [previous['installationId']]
            if len(retired) > 16:
                raise OwnershipError('closed generation history limit reached; use a new target')
        daemon = self._daemon()
        empty = self._observe()
        if any(empty[kind] for kind in KINDS):
            raise OwnershipError('project resources already exist')
        record = {'schemaVersion': 1, 'project': self.project, 'daemonId': daemon,
                  'installationId': str(uuid.uuid4()), 'targetSha256': self.target_hash,
                  'claimId': None, 'image': self.image, 'phase': 'claim-pending', 'resources': empty}
        if retired:
            record['retiredInstallations'] = retired
        self._save(record)
        claim_id = self._call(self.driver.create_claim, self.claim_name, self._binding(record), self.image)
        if not isinstance(claim_id, str) or not HEX64.fullmatch(claim_id):
            raise OwnershipError('claim create result is invalid')
        record['claimId'] = claim_id
        self._save(record)  # Still pending if identity/discovery fails next.
        self._assert_claim(record)
        if self._daemon() != daemon or self._observe() != empty:
            raise OwnershipError('daemon or project changed while acquiring ownership')
        record['phase'] = 'ready'
        self._save(record)

    def mutate(self, operation, effect):
        if not isinstance(operation, str) or operation not in {'install', 'upgrade', 'backup', 'doctor'} or not callable(effect):
            raise OwnershipError('unsupported owned mutation')
        record = self._record({'ready', 'retained-data'} if operation == 'install' else {'ready'})
        record['phase'] = 'mutation-pending'
        self._save(record)
        settled = None
        try:
            effect()
        except SettledMutationFailure as exc:
            settled = exc
        except Exception as exc:
            raise OwnershipError('mutation failed or its outcome is unknown') from exc
        if self._daemon() != record['daemonId']:
            raise OwnershipError('daemon changed during mutation')
        self._assert_claim(record)
        observed = self._observe()
        if any(observed['volumes'].get(name) != fingerprint
               for name, fingerprint in record['resources']['volumes'].items()):
            raise OwnershipError('non-purge mutation lost or replaced a recorded volume')
        record.update(phase='settled-failure' if settled else 'ready', resources=observed)
        self._save(record)
        if settled:
            raise settled

    def preflight_cleanup(self, purge):
        if type(purge) is not bool:
            raise OwnershipError('cleanup mode is invalid')
        return self._record({'ready', 'retained-data', 'settled-failure'})

    def cleanup(self, purge, after_resources=None):
        if after_resources is not None and (purge is not True or not callable(after_resources)):
            raise OwnershipError('host purge callback requires explicit purge')
        record = self.preflight_cleanup(purge)
        expected = {kind: {} for kind in KINDS}
        if not purge:
            expected['volumes'] = deepcopy(record['resources']['volumes'])
        record['phase'] = 'cleanup-pending'
        self._save(record)
        self._call(self.driver.delete_resources, deepcopy(record['resources']), purge)
        if self._daemon() != record['daemonId']:
            raise OwnershipError('daemon changed during cleanup')
        self._assert_claim(record)
        if self._observe() != expected:
            raise OwnershipError('cleanup outcome is unverified or resources remain')
        if purge:
            if after_resources is not None:
                self._call(after_resources)
                if self._daemon() != record['daemonId']:
                    raise OwnershipError('daemon changed during host purge')
                self._assert_claim(record)
                if self._observe() != expected:
                    raise OwnershipError('runtime changed during host purge')
            self._call(self.driver.delete_claim, record['claimId'])
            if self._call(self.driver.inspect_claim, record['claimId']) is not None:
                raise OwnershipError('claim removal is unverified')
        record.update(phase='removed' if purge else 'retained-data', resources=expected)
        self._save(record)


class PrivateStore:
    """Bounded private ledger I/O; caller must hold the installation lock.

    Snapshots detect observed replacement; they are not a lease against the
    same uid/root. Failed temporary writes are retained for controlled handling.
    """
    NAME = 'runtime-owner.json'
    LIMIT = 65536

    def __init__(self, target, *, name=None):
        self.target = Path(target)
        if not self.target.is_absolute() or '..' in self.target.parts:
            raise OwnershipError('ownership store path is invalid')
        if name is not None:
            if (not isinstance(name, str) or name not in {'runtime-contract.json', 'runtime-owner.compose.json'}
                    and not re.fullmatch(r'runtime-(?:child|exec|launch)-[0-9a-f]{32}\.json', name)):
                raise OwnershipError('child receipt name is invalid')
            self.NAME = name
        self.control = self.target / '.surveyctl'

    @staticmethod
    def _directory_identity(info):
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid

    @staticmethod
    def _file_identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _open_parent(self):
        chain = []
        for path in (*reversed(self.control.parents), self.control):
            info = os.lstat(path)
            inside = path == self.target or self.target in path.parents
            owners = {os.geteuid()} if inside else {0, os.geteuid()}
            write_safe = not info.st_mode & 0o022 or (
                not inside and info.st_uid == 0 and info.st_mode & stat.S_ISVTX)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners or not write_safe
                    or (path == self.control and info.st_mode & 0o077)):
                raise OwnershipError('ownership store directory is unsafe')
            chain.append((path, self._directory_identity(info)))
        fd = os.open(self.control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self._recheck(fd, chain)
        except BaseException:
            os.close(fd)
            raise
        return fd, chain

    def _recheck(self, fd, chain):
        if self._directory_identity(os.fstat(fd)) != chain[-1][1]:
            raise OwnershipError('ownership store directory identity changed')
        if any(self._directory_identity(os.lstat(path)) != expected for path, expected in chain):
            raise OwnershipError('ownership store path changed')

    def _regular(self, info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or info.st_nlink != 1 or not 2 <= info.st_size <= self.LIMIT):
            raise OwnershipError('ownership record is not a bounded private single-link file')
        return self._file_identity(info)

    @staticmethod
    def _unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('duplicate ownership field')
            value[key] = item
        return value

    def load(self):
        parent = None
        try:
            parent, chain = self._open_parent()
            try:
                fd = os.open(self.NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                self._recheck(parent, chain)
                return None
            with os.fdopen(fd, 'rb') as handle:
                before = self._regular(os.fstat(handle.fileno()))
                data = handle.read(self.LIMIT + 1)
                if len(data) > self.LIMIT:
                    raise OwnershipError('ownership record is oversized')
                value = json.loads(data.decode('utf-8'), object_pairs_hook=self._unique)
                if not isinstance(value, dict):
                    raise OwnershipError('ownership record is not an object')
                after = self._regular(os.fstat(handle.fileno()))
                observed = self._regular(os.stat(self.NAME, dir_fd=parent, follow_symlinks=False))
                if before != after or before != observed:
                    raise OwnershipError('ownership record changed while reading')
                self._recheck(parent, chain)
                return value
        except (OSError, UnicodeError, ValueError) as exc:
            raise OwnershipError('ownership record could not be read safely') from exc
        finally:
            if parent is not None: os.close(parent)

    def save(self, value):
        parent = None
        try:
            if not isinstance(value, dict):
                raise OwnershipError('ownership record is not an object')
            data = (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode('utf-8')
            if len(data) > self.LIMIT:
                raise OwnershipError('ownership record is oversized')
            parent, chain = self._open_parent()
            try:
                previous = self._regular(os.stat(self.NAME, dir_fd=parent, follow_symlinks=False))
            except FileNotFoundError:
                previous = None
            temporary = '.runtime-owner-' + uuid.uuid4().hex + '.tmp'
            fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(fd, 'w+b') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
                staged = self._regular(os.fstat(handle.fileno()))
                self._recheck(parent, chain)
                try:
                    current = self._regular(os.stat(self.NAME, dir_fd=parent, follow_symlinks=False))
                except FileNotFoundError:
                    current = None
                if previous != current or self._regular(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) != staged:
                    raise OwnershipError('ownership record changed before publication')
                os.replace(temporary, self.NAME, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                handle.seek(0)
                if handle.read(self.LIMIT + 1) != data:
                    raise OwnershipError('ownership record changed after publication')
                after = self._regular(os.fstat(handle.fileno()))
                if after != self._regular(os.stat(self.NAME, dir_fd=parent, follow_symlinks=False)):
                    raise OwnershipError('ownership record identity changed after publication')
                self._recheck(parent, chain)
        except (OSError, UnicodeError, ValueError) as exc:
            raise OwnershipError('ownership record could not be persisted safely') from exc
        finally:
            if parent is not None: os.close(parent)


class DockerOwnershipDriver:
    """Local-daemon adapter. Contract/probe are internal verified-source inputs.

    The cohort probe must actually inspect the controlled process cohort; no
    environment variable or request body may assert quiescence. Only whitelisted
    Docker inspect fields are requested, never Config.Env or raw logs.
    """
    HOST = 'unix:///var/run/docker.sock'
    LIMIT = 1024 * 1024
    LABEL = 'com.docker.compose.project'
    OWNER = 'io.survey.installation'
    CONTAINER = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
        '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"workingDir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},'
        '"owner":{{json (index .Config.Labels "io.survey.installation")}},'
        '"status":{{json .State.Status}},"paused":{{json .State.Paused}},'
        '"execs":{{json .ExecIDs}},"mounts":{{json .Mounts}}}')
    CLAIM = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},'
        '"installation":{{json (index .Config.Labels "io.survey.installation")}},'
        '"target":{{json (index .Config.Labels "io.survey.target-sha256")}},'
        '"daemon":{{json (index .Config.Labels "io.survey.daemon")}},'
        '"role":{{json (index .Config.Labels "io.survey.role")}},'
        '"status":{{json .State.Status}},"running":{{json .State.Running}},'
        '"network":{{json .HostConfig.NetworkMode}},"privileged":{{json .HostConfig.Privileged}},'
        '"readOnly":{{json .HostConfig.ReadonlyRootfs}},"mounts":{{json .Mounts}},'
        '"ports":{{json .HostConfig.PortBindings}},"capAdd":{{json .HostConfig.CapAdd}}}')
    NETWORK = ('{"id":{{json .Id}},"name":{{json .Name}},"driver":{{json .Driver}},'
        '"internal":{{json .Internal}},"created":{{json .Created}},'
        '"project":{{json (index .Labels "com.docker.compose.project")}},'
        '"logical":{{json (index .Labels "com.docker.compose.network")}},'
        '"owner":{{json (index .Labels "io.survey.installation")}},"members":{{json .Containers}}}')
    VOLUME = ('{"name":{{json .Name}},"driver":{{json .Driver}},"created":{{json .CreatedAt}},'
        '"project":{{json (index .Labels "com.docker.compose.project")}},'
        '"logical":{{json (index .Labels "com.docker.compose.volume")}},'
        '"owner":{{json (index .Labels "io.survey.installation")}},"optionsEmpty":{{eq (len .Options) 0}}}')

    def __init__(self, runner, contract, cohort_probe, *, project=None):
        if (not callable(cohort_probe) or not isinstance(contract, dict)
                or set(contract) != {'services', 'networks', 'volumes'}
                or not isinstance(contract['services'], dict) or not contract['services']):
            raise OwnershipError('verified topology and a real cohort probe are required')
        self.project = project_name(project)
        self.claim_name = self.project + '-ownership-v1'
        if self.project.startswith('survey-recovery-'):
            self._recovery_contract(contract)
        self.runner, self.contract, self.probe = runner, deepcopy(contract), cohort_probe
        self.binding = None
        self.claim_id = None

    def _recovery_contract(self, contract):
        """Data-only scope for the still-disabled recovery executor.

        No bind mounts or shared/external persistent names. Future application
        config mounts need their own reviewed target-root admission; this does
        not reuse the production topology or authorize an import/start.
        """
        if not isinstance(contract['networks'], dict) or not isinstance(contract['volumes'], dict):
            raise OwnershipError('recovery topology inventory is invalid')
        name = re.compile(re.escape(self.project) + r'_[A-Za-z0-9_.-]{1,128}')
        for key, value in contract['networks'].items():
            if (not isinstance(key, str) or not name.fullmatch(key) or not isinstance(value, dict)
                    or set(value) != {'logical', 'driver', 'internal'}
                    or value['driver'] != 'bridge' or value['internal'] is not True):
                raise OwnershipError('recovery network must be private and project bound')
        for key, value in contract['volumes'].items():
            if (not isinstance(key, str) or not name.fullmatch(key) or not isinstance(value, dict)
                    or set(value) != {'logical', 'driver'} or value['driver'] != 'local'):
                raise OwnershipError('recovery volume must be local and project bound')
        for service in contract['services'].values():
            if (not isinstance(service, dict) or set(service) != {'imageIds', 'workingDirs', 'mounts'}
                    or not isinstance(service['mounts'], list)):
                raise OwnershipError('recovery service contract is invalid')
            for mount in service['mounts']:
                if (not isinstance(mount, dict) or set(mount) != {'type', 'name', 'target', 'readOnly'}
                        or mount['type'] != 'volume' or mount['name'] not in contract['volumes']
                        or type(mount['readOnly']) is not bool):
                    raise OwnershipError('recovery mounts cannot reference external data or host paths')

    def _query(self, arguments):
        if (os.environ.get('DOCKER_CONTEXT') or os.environ.get('DOCKER_HOST') not in (None, '', self.HOST)):
            raise OwnershipError('ownership requires the local Docker daemon binding')
        environment = dict(os.environ, DOCKER_HOST=self.HOST)
        try:
            result = self.runner.run(['docker', '--host', self.HOST, *arguments],
                check=True, text=True, capture_output=True, timeout=30, env=environment)
            if type(result.returncode) is not int or result.returncode != 0:
                raise OwnershipError('Docker operation did not complete successfully')
            if not isinstance(result.stdout, str) or len(result.stdout.encode('utf-8')) > self.LIMIT:
                raise OwnershipError('Docker observation is missing or oversized')
            return result.stdout
        except Exception as exc:
            raise OwnershipError('Docker operation failed or its result is unknown') from exc

    def _json(self, arguments):
        try:
            return json.loads(self._query(arguments), object_pairs_hook=PrivateStore._unique)
        except (ValueError, UnicodeError) as exc:
            raise OwnershipError('Docker observation is not valid JSON') from exc

    def identity(self):
        return self._json(['info', '--format', '{{json .ID}}'])

    def create_claim(self, name, binding, image):
        if name != self.claim_name or not isinstance(binding, dict) or set(binding) != {'daemonId', 'installationId', 'targetSha256'}:
            raise OwnershipError('claim request binding is invalid')
        if (not isinstance(image, str) or image.startswith('-')
                or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image)):
            raise OwnershipError('claim image binding is invalid')
        # `docker create` instantiates image-declared VOLUMEs even without a
        # --mount argument. Such an image cannot serve as a metadata-only claim.
        if self._json(['image', 'inspect', '--format', '{{json .Config.Volumes}}', '--', image]) not in ({}, None):
            raise OwnershipError('claim image declares implicit volumes')
        args = ['container', 'create', '--name', self.claim_name, '--pull=never', '--network', 'none',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                '--label', self.OWNER + '=' + binding['installationId'],
                '--label', 'io.survey.target-sha256=' + binding['targetSha256'],
                '--label', 'io.survey.daemon=' + binding['daemonId'],
                '--label', 'io.survey.role=ownership-claim', '--entrypoint', '/bin/true', '--', image]
        identity = self._query(args).strip()
        if not HEX64.fullmatch(identity):
            raise OwnershipError('claim create identity is invalid')
        self.binding = deepcopy(binding)
        return identity

    def inspect_claim(self, identity):
        if not isinstance(identity, str) or not HEX64.fullmatch(identity):
            raise OwnershipError('claim identity is invalid')
        lines = self._query(['container', 'ls', '--all', '--quiet', '--no-trunc', '--filter', 'id=' + identity]).splitlines()
        if not lines: return None
        if lines != [identity]: raise OwnershipError('claim discovery is ambiguous')
        item = self._json(['container', 'inspect', '--format', self.CLAIM, identity])
        keys = {'id', 'name', 'image', 'installation', 'target', 'daemon', 'role', 'status',
                'running', 'network', 'privileged', 'readOnly', 'mounts', 'ports', 'capAdd'}
        if not isinstance(item, dict) or set(item) != keys or item['id'] != identity:
            raise OwnershipError('claim inspection shape is invalid')
        isolated = (item['network'] == 'none' and item['privileged'] is False and item['readOnly'] is True
                    and item['mounts'] == [] and item['ports'] in ({}, None) and item['capAdd'] in ([], None)
                    and item['role'] == 'ownership-claim')
        binding = {'daemonId': item['daemon'], 'installationId': item['installation'], 'targetSha256': item['target']}
        self.binding, self.claim_id = binding, identity
        return {'id': identity, 'name': item['name'].removeprefix('/'), 'binding': binding,
                'image': item['image'], 'stopped': item['status'] == 'created' and item['running'] is False,
                'isolated': isolated}

    @staticmethod
    def _fingerprint(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def _rows(self, kind):
        singular = 'container' if kind == 'containers' else 'network' if kind == 'networks' else 'volume'
        name = '.Names' if singular == 'container' else '.Name'
        fields = '"name":{{json ' + name + '}},"project":{{json (.Label "' + self.LABEL + '")}}'
        if singular != 'volume': fields = '"id":{{json .ID}},' + fields
        args = [singular, 'ls']
        if singular == 'container': args.append('--all')
        if singular != 'volume': args.append('--no-trunc')
        raw = self._query([*args, '--format', '{' + fields + '}'])
        lines = raw.splitlines()
        if len(lines) > 512: raise OwnershipError('Docker object inventory is not bounded')
        result = []
        for line in lines:
            try: item = json.loads(line, object_pairs_hook=PrivateStore._unique)
            except ValueError as exc: raise OwnershipError('Docker object list is malformed') from exc
            keys = {'name', 'project'} if singular == 'volume' else {'id', 'name', 'project'}
            if (not isinstance(item, dict) or set(item) != keys or not isinstance(item['name'], str)
                    or not isinstance(item['project'], str)):
                raise OwnershipError('Docker object list shape is invalid')
            if singular == 'container' and item['id'] == self.claim_id and item['name'] == self.claim_name:
                continue
            if item['project'] == self.project or item['name'].startswith((self.project + '-', self.project + '_')):
                result.append(item)
        return result

    def _container(self, identity):
        item = self._json(['container', 'inspect', '--format', self.CONTAINER, identity])
        keys = {'id', 'name', 'image', 'project', 'service', 'workingDir', 'owner', 'status', 'paused', 'execs', 'mounts'}
        if not isinstance(item, dict) or set(item) != keys or item['id'] != identity:
            raise OwnershipError('container inspection shape is invalid')
        service = self.contract['services'].get(item['service'])
        if not service or item['project'] != self.project or item['name'] != '/' + self.project + '-' + item['service'] + '-1':
            raise OwnershipError('unrecognized project container')
        if item['image'] not in service['imageIds'] or item['workingDir'] not in service['workingDirs']:
            raise OwnershipError('container image or creation path is not bound')
        if item['owner'] not in (None, '', (self.binding or {}).get('installationId')):
            raise OwnershipError('container installation label differs')
        mounts = []
        if not isinstance(item['mounts'], list): raise OwnershipError('container mounts are invalid')
        for mount in item['mounts']:
            if not isinstance(mount, dict) or type(mount.get('RW')) is not bool:
                raise OwnershipError('mount inspection is invalid')
            value = {'type': mount.get('Type'), 'target': mount.get('Destination'), 'readOnly': not mount['RW']}
            if value['type'] == 'volume': value['name'] = mount.get('Name')
            elif value['type'] == 'bind': value['source'] = mount.get('Source')
            else: raise OwnershipError('unknown mount type')
            mounts.append(value)
        canonical = lambda values: sorted(json.dumps(v, sort_keys=True) for v in values)
        if canonical(mounts) != canonical(service['mounts']): raise OwnershipError('container mounts differ from verified topology')
        unknown = (type(item['paused']) is not bool or item['paused'] or item['execs'] not in ([], None)
                   or item['status'] not in {'running', 'created', 'exited', 'dead'})
        stable = {key: item[key] for key in ('id', 'name', 'image', 'project', 'service', 'workingDir', 'owner')}
        stable['mounts'] = canonical(mounts)
        self._last_container_snapshot = deepcopy(item)  # Validated projection only, never Env/raw inspect.
        return self._fingerprint(stable), unknown, item['status']

    def _network(self, identity, owned_ids):
        item = self._json(['network', 'inspect', '--format', self.NETWORK, identity])
        expected = self.contract['networks'].get(item.get('name')) if isinstance(item, dict) else None
        if (not expected or item.get('id') != identity or item.get('project') != self.project
                or item.get('logical') != expected['logical'] or item.get('driver') != expected['driver']
                or item.get('internal') is not expected['internal'] or not item.get('created')
                or item.get('owner') != (self.binding or {}).get('installationId')
                or not isinstance(item.get('members'), dict) or set(item['members']) - set(owned_ids)):
            raise OwnershipError('network ownership or endpoints are unverified')
        return self._fingerprint({key: value for key, value in item.items() if key != 'members'}), item['members']

    def _volume(self, name, owned_ids):
        item = self._json(['volume', 'inspect', '--format', self.VOLUME, name])
        expected = self.contract['volumes'].get(name)
        if (not isinstance(item, dict) or not expected or item.get('name') != name or item.get('project') != self.project
                or item.get('logical') != expected['logical'] or item.get('driver') != expected['driver']
                or not isinstance(item.get('created'), str) or not item['created'] or item.get('optionsEmpty') is not True
                or item.get('owner') != (self.binding or {}).get('installationId')):
            raise OwnershipError('volume creation identity is unverified')
        users = self._query(['container', 'ls', '--all', '--quiet', '--no-trunc', '--filter', 'volume=' + name]).splitlines()
        if any(not HEX64.fullmatch(value) for value in users) or set(users) - set(owned_ids):
            raise OwnershipError('volume has an unknown container attachment')
        return self._fingerprint(item), users

    def observe(self):
        result = {kind: {} for kind in KINDS}
        result.update(cohort=OwnershipProtocol._call(self.probe), foreign=False)
        for item in self._rows('containers'):
            identity = item['id']
            if not isinstance(identity, str) or not HEX64.fullmatch(identity) or identity in result['containers']:
                raise OwnershipError('container list identity is invalid')
            fingerprint, unknown, _ = self._container(identity)
            result['containers'][identity] = fingerprint
            if unknown: result['cohort'] = 'unknown'
        for kind in ('networks', 'volumes'):
            for item in self._rows(kind):
                identity = item['id'] if kind == 'networks' else item['name']
                valid = (isinstance(identity, str) and (bool(HEX64.fullmatch(identity)) if kind == 'networks'
                         else bool(re.fullmatch(re.escape(self.project) + r'_[A-Za-z0-9_.-]{1,128}', identity))))
                if not valid: raise OwnershipError('resource list identity is invalid')
                if identity in result[kind]: raise OwnershipError('duplicate resource identity')
                if kind == 'networks': fingerprint, _ = self._network(identity, result['containers'])
                else: fingerprint, _ = self._volume(identity, result['containers'])
                result[kind][identity] = fingerprint
        return result

    def delete_resources(self, expected, purge):
        current = self.observe()
        if current['cohort'] != 'quiescent' or current['foreign'] or {key: current[key] for key in KINDS} != expected:
            raise OwnershipError('delete precondition changed')
        for identity, fingerprint in expected['containers'].items():
            actual, unknown, state = self._container(identity)
            if actual != fingerprint or unknown: raise OwnershipError('container changed before removal')
            if state == 'running':
                self._query(['container', 'stop', '--time', '20', identity])
            actual, unknown, state = self._container(identity)
            if actual != fingerprint or unknown or state == 'running': raise OwnershipError('container did not stop safely')
            self._query(['container', 'rm', identity])
        for identity, fingerprint in expected['networks'].items():
            actual, members = self._network(identity, set())
            if actual != fingerprint or members: raise OwnershipError('network changed before removal')
            self._query(['network', 'rm', identity])
        if purge:
            for name, fingerprint in expected['volumes'].items():
                actual, users = self._volume(name, set())
                if actual != fingerprint or users: raise OwnershipError('volume changed before removal')
                self._query(['volume', 'rm', name])

    def delete_claim(self, identity):
        item = self.inspect_claim(identity)
        if not item or not item['stopped'] or not item['isolated']:
            raise OwnershipError('claim changed before removal')
        self._query(['container', 'rm', identity])
