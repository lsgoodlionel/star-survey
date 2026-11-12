"""Authenticated data-only new-copy component. No product restore/CLI caller.

Backup HMAC is not a target image signature/config approval. No application is
started; transfers/exit0 do not qualify business data, personnel reset or release.
Trusted exclusive controller; no hostile same-UID/host-admin TOCTOU guarantee.
"""
from copy import deepcopy
import hashlib
import os
import re
import stat
from pathlib import Path
from types import SimpleNamespace
import uuid

from owned_backup_verify import ControlledVerifier
from owned_docker_exec import OwnedDockerExec, KnownExecFailure, UncertainExec
from owned_maintenance import OwnedMaintenance
from owned_memory import AnonymousWorkspace
from recovery_handoff import ReservationHandoff
from runtime_cohort import CohortProbe
from runtime_docker import APIOwnershipDriver
from runtime_ownership import OwnershipProtocol, PrivateStore
from restore import RestoreIntegrityError, EXPECTED_PAYLOADS, CONTROL_FILES

OPERATIONS = {'postgres-empty': 'platform-db', 'mariadb-empty': 'engine-db',
              'postgres-import': 'platform-db', 'mariadb-import': 'engine-db'}
PAYLOADS = {'postgres-import': 'postgres.dump', 'mariadb-import': 'mariadb.sql'}
_ISSUER = object()


def _uuid(value):
    try: return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError): return False


ISOLATION_FAILURES = frozenset({'network-mode','published-ports','privileged','host-namespace',
    'extra-capabilities','devices','security-options','resource-limits','binding-observation'})


def _isolation_error(value):
    if not isinstance(value, dict) or value.get('mode') != 'none': return 'network-mode'
    if (value.get('ports') not in (None,{}) or value.get('publishAll') is not False
            or value.get('published') is not None and (not isinstance(value['published'],dict)
                or any(item not in (None,[]) for item in value['published'].values()))): return 'published-ports'
    if not isinstance(value.get('networks'),dict) or set(value['networks'])-{'none'}: return 'network-mode'
    if value.get('privileged') is not False: return 'privileged'
    if (value.get('pid') not in ('','private') or value.get('ipc') not in ('','private')
            or value.get('uts') not in ('','private') or value.get('cgroup') not in ('','private')
            or value.get('userns') not in ('','private')): return 'host-namespace'
    if value.get('capAdd') not in (None,[]): return 'extra-capabilities'
    if any(value.get(name) not in (None,[]) for name in ('devices','deviceRequests','volumesFrom')): return 'devices'
    opts=value.get('securityOpt')
    if (not isinstance(opts,list) or not opts or any(item not in
            ('no-new-privileges','no-new-privileges:true') for item in opts)): return 'security-options'
    if any(type(value.get(name)) is not int or value[name]<=0 for name in ('memory','cpu','pids')): return 'resource-limits'
    return None


class NewCopyTarget:
    """Bind real data-only ownership, existing lock and kernel cohort, not config approval."""
    def __init__(self, handoff, protocol, probe, operations):
        if (not isinstance(handoff, ReservationHandoff) or not isinstance(protocol, OwnershipProtocol)
                or not isinstance(probe, CohortProbe) or not isinstance(protocol.driver, APIOwnershipDriver)
                or not isinstance(operations, dict) or set(operations) != set(OPERATIONS)
                or any(not _uuid(value) for value in operations.values()) or len(set(operations.values())) != 4):
            raise KnownExecFailure('new-copy controller binding is invalid')
        view = handoff.inspect()
        project = view['projectProposal']
        if (protocol.project != project or protocol.driver.project != project or probe.project != project
                or probe.target != handoff.path or probe.fd != handoff._files['operation.lock'][0]
                or protocol.driver.api is not probe.api or protocol.driver.probe is not probe
                or protocol.target_hash != hashlib.sha256(str(handoff.path).encode()).hexdigest()
                or set(protocol.driver.contract['services']) != {'platform-db', 'engine-db'}
                or protocol.driver.contract['networks']
                or any(service.get('workingDirs') != [str(handoff.path)]
                    or not isinstance(service.get('imageIds'), list) or len(service['imageIds']) != 1
                    or not isinstance(service['imageIds'][0], str)
                    or re.fullmatch(r'sha256:[0-9a-f]{64}', service['imageIds'][0]) is None
                    for service in protocol.driver.contract['services'].values())
                or probe() != 'quiescent'
                or any('runtime-exec-' + value.replace('-', '') + '.json' not in handoff.receipts
                       for value in operations.values())):
            raise KnownExecFailure('new-copy namespace, lock or receipt plan differs')
        self.root, self.handoff, self.protocol, self.probe = handoff.path, handoff, protocol, probe
        self.operations, self.failed = deepcopy(operations), False
        runtime = SimpleNamespace(manager=SimpleNamespace(target=self.root, _owned_contexts=[]),
            driver=protocol.driver, probe=probe, data={'contract': protocol.driver.contract},
            preflight_cleanup=protocol.preflight_cleanup)
        self.maintenance = OwnedMaintenance(runtime)
        self.record, self.services = self.maintenance.record, self.maintenance.services
        self.api, self.guard = self.maintenance.api, self.maintenance.guard
        self.admit_unused()

    def check(self, service, container, allowed=None):
        self.last_failure = 'binding-observation'
        try:
            if self.failed or self.services.get(service) != container: return False
            self.handoff.inspect()
            if self.protocol.store.load() != self.record: return False
            self.protocol._assert_claim(self.record)
            driver = self.protocol.driver
            rows = driver._rows('containers')
            if {row['id'] for row in rows} != set(self.record['resources']['containers']): return False
            for name, cid in self.services.items():
                if not self.maintenance.container_check(cid, allowed if name == service else None): return False
                row = driver._last_container_snapshot
                if row.get('owner') != self.record['installationId'] or row.get('service') != name: return False
                network = driver._json(['container', 'inspect', '--format',
                    '{"mode":{{json .HostConfig.NetworkMode}},"ports":{{json .HostConfig.PortBindings}},'
                    '"publishAll":{{json .HostConfig.PublishAllPorts}},"published":{{json .NetworkSettings.Ports}},'
                    '"networks":{{json .NetworkSettings.Networks}},"privileged":{{json .HostConfig.Privileged}},'
                    '"pid":{{json .HostConfig.PidMode}},"ipc":{{json .HostConfig.IpcMode}},"uts":{{json .HostConfig.UTSMode}},'
                    '"cgroup":{{json .HostConfig.CgroupnsMode}},"userns":{{json .HostConfig.UsernsMode}},'
                    '"capAdd":{{json .HostConfig.CapAdd}},"devices":{{json .HostConfig.Devices}},'
                    '"deviceRequests":{{json .HostConfig.DeviceRequests}},"volumesFrom":{{json .HostConfig.VolumesFrom}},'
                    '"securityOpt":{{json .HostConfig.SecurityOpt}},"memory":{{json .HostConfig.Memory}},'
                    '"cpu":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}}}', cid])
                reason = _isolation_error(network)
                if reason is not None:
                    self.last_failure=reason; return False
            if driver._rows('networks') or self.record['resources']['networks']: return False
            if {row['name'] for row in driver._rows('volumes')} != set(self.record['resources']['volumes']): return False
            for name, expected in self.record['resources']['volumes'].items():
                actual, _ = driver._volume(name, self.record['resources']['containers'])
                if actual != expected: return False
            if allowed is None:
                if self.probe() != 'quiescent' or self.protocol._record({'ready'}) != self.record: return False
            self.last_failure=None
            return True
        except Exception:
            return False

    def executor(self, profile):
        service = OPERATIONS[profile]
        return OwnedDockerExec(self.api, PrivateStore(self.root,
            name='runtime-exec-' + self.operations[profile].replace('-', '') + '.json'), self.guard,
            operation_id=self.operations[profile], installation_id=self.record['installationId'],
            container_check=lambda cid, allowed=None: self.check(service, cid, allowed))

    def admit_unused(self):
        if self.failed: raise KnownExecFailure('copy target has already failed')
        for service, cid in self.services.items():
            if not self.check(service, cid):
                error=KnownExecFailure('copy target identity differs')
                error.target_reason=getattr(self,'last_failure','binding-observation')
                raise error
        for profile in OPERATIONS:
            if PrivateStore(self.root, name='runtime-exec-' + self.operations[profile].replace('-', '') + '.json').load() is not None:
                raise KnownExecFailure('copy single-use receipt already exists')

    def preflight(self, copy):
        for profile in ('postgres-empty', 'mariadb-empty'):
            output, lease = copy.output()
            self.executor(profile).execute(profile, self.services[OPERATIONS[profile]], lease)
            if os.pread(lease.fd, 4, 0) != b'0\n':
                raise KnownExecFailure('new-copy database is not empty')
            # The output lease stays bound until the copy is known ended.


class VerifiedDatabaseInput:
    """Issued only after the controller's full backup verification. Not a JSON DTO."""
    def __init__(self, issuer, copy, profile, lease, digest):
        if issuer is not _ISSUER: raise RestoreIntegrityError('authenticated input issuer differs')
        self._copy, self.profile, self._lease, self.sha256 = copy, profile, lease, digest
        self.claimed = False
        self._source_evidence = deepcopy(copy.evidence)

    @property
    def source_evidence(self): return deepcopy(self._source_evidence)

    @property
    def fd(self): return self._lease.fd

    @property
    def max_file_bytes(self): return self._lease.max_file_bytes

    def claim(self, profile, container, operation, installation):
        copy, target = self._copy, self._copy.target
        if (self.claimed or not copy.active or copy.uncertain or copy.failed or profile != self.profile
                or target.operations[profile] != operation or target.record['installationId'] != installation
                or target.services[OPERATIONS[profile]] != container):
            raise KnownExecFailure('authenticated input single-use binding differs')
        copy.recheck_source()
        if not target.check(OPERATIONS[profile], container):
            raise KnownExecFailure('authenticated input target changed')
        self.claimed = True

    def unknown(self): self._copy.poison_unknown()


class ControlledDatabaseCopy:
    def __init__(self, verifier, target):
        if not isinstance(verifier, ControlledVerifier) or not isinstance(target, NewCopyTarget):
            raise RestoreIntegrityError('controlled verifier and bound new-copy target are required')
        if verifier.target == target.root or verifier.target in target.root.parents or target.root in verifier.target.parents:
            raise RestoreIntegrityError('copy source and destination overlap')
        self.verifier, self.target = verifier, target
        self._exports, self._outputs = [], []
        self.used = self.active = self.failed = self.uncertain = False
        self.control, self.evidence, self._source_view = None, None, None
        target.maintenance.held.append(self)  # Keep unknown plaintext/leases alive.

    def poison_unknown(self):
        self.uncertain = self.failed = self.target.failed = True
        self.target.maintenance.uncertain = True

    def source_inventory(self):
        directory = self.verifier.cipher.directory
        directory.check()
        names, total = set(), 0
        allowed = EXPECTED_PAYLOADS | CONTROL_FILES
        with os.scandir(directory.fd) as entries:
            for entry in entries:
                if entry.name not in allowed or entry.name in names:
                    raise RestoreIntegrityError('authenticated source inventory is unsafe')
                info = entry.stat(follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077 or info.st_size <= 0):
                    raise RestoreIntegrityError('authenticated source file is unsafe')
                if entry.name in EXPECTED_PAYLOADS: total += info.st_size
                names.add(entry.name)
        if names != allowed or total > self.verifier.workspace.max_bytes + len(EXPECTED_PAYLOADS)*65536:
            raise RestoreIntegrityError('authenticated source inventory exceeds its bound')
        directory.check()

    def control_snapshot(self):
        directory = self.verifier.cipher.directory
        directory.check()
        values = {}
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        for name, bound in (('manifest.json',65536),('SHA256SUMS',65536),('CONTROL-HMAC',512)):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory.fd)
                try:
                    before = os.fstat(fd)
                    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.geteuid()
                            or before.st_mode & 0o077 or not 0 < before.st_size <= bound):
                        raise RestoreIntegrityError('authenticated source control is unsafe')
                    data = bytearray()
                    while len(data) <= bound:
                        block = os.read(fd, bound+1-len(data))
                        if not block: break
                        data.extend(block)
                    if (len(data)>bound or identity(os.fstat(fd))!=identity(before)
                            or identity(os.stat(name,dir_fd=directory.fd,follow_symlinks=False))!=identity(before)):
                        raise RestoreIntegrityError('authenticated source control changed during read')
                    values[name] = bytes(data)
                finally:
                    try: os.close(fd)
                    except OSError as exc:
                        raise UncertainExec('control descriptor release unknown; retain copy context') from exc
            except OSError as exc:
                raise RestoreIntegrityError('authenticated source control cannot be read safely') from exc
        directory.check()
        return values

    def source_view(self):
        current = self.verifier._load_current()
        root = self.verifier.target
        release = root/'releases'/current[0]
        view = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (root/'current', release/'release.json', release/'.env', release/'compose.yml')}
        floor = root/'.surveyctl/auth-security-floor'
        view['personnelFloor'] = hashlib.sha256(floor.read_bytes()).hexdigest() if floor.exists() else None
        return view

    def recheck_source(self):
        self.verifier.cipher.directory.check()
        if self.source_view() != self._source_view:
            raise RestoreIntegrityError('effective source release or security floor changed')
        self.source_inventory()
        if self.control_snapshot() != self.control:
            raise RestoreIntegrityError('authenticated source control changed')

    def output(self):
        workspace = AnonymousWorkspace(max_bytes=1024*1024, max_files=1)
        self._outputs.append(workspace)
        context = (workspace/'ack').export_for_child(write=True)
        lease = context.__enter__(); self._exports.append(context)
        return workspace, lease

    def execute(self, backup):
        if self.used: raise KnownExecFailure('database copy cannot be replayed')
        self.used = True
        try:
            self.target.admit_unused()
            if backup != self.verifier.cipher.root or backup.parent != self.verifier.target/'backups':
                raise RestoreIntegrityError('authenticated backup operation binding differs')
            self._source_view = self.source_view()
            self.source_inventory()
            self.control = self.control_snapshot()
            metadata, plain = self.verifier.verify_backup(backup, self.verifier.workspace)
            # Re-authenticate the complete encrypted inventory after decrypting,
            # never substitute a caller hash for HMAC/source/floor validation.
            version, _, _, compatible, minimum = self.verifier._load_current()
            if self.verifier._verify(backup, version, compatible, minimum) != metadata:
                raise RestoreIntegrityError('authenticated backup snapshot changed')
            self.recheck_source()
            digest = hashlib.sha256(b'survey-backup-control-v1\0')
            for name in sorted(self.control):
                value = self.control[name]
                digest.update(name.encode('ascii') + b'\0' + len(value).to_bytes(8, 'big') + value)
            self.evidence = {'kind':'backup-hmac-control-v1',
                'controlSha256':digest.hexdigest(),
                'releaseManifestSha256':metadata['releaseManifestSha256']}
            context = self.verifier.workspace.export_files(inputs=tuple(plain[PAYLOADS[p]] for p in PAYLOADS))
            leases = context.__enter__(); self._exports.append(context)
            self.active = True
            inputs = {}
            for profile, lease in zip(PAYLOADS, leases):
                digest = hashlib.sha256()
                for offset in range(0, lease.max_file_bytes, 65536):
                    digest.update(os.pread(lease.fd, min(65536, lease.max_file_bytes-offset), offset))
                inputs[profile] = VerifiedDatabaseInput(_ISSUER, self, profile, lease, digest.hexdigest())
            self.target.preflight(self)
            for profile, item in inputs.items():
                _, output = self.output()
                self.target.executor(profile).execute(profile, self.target.services[OPERATIONS[profile]], output,
                    input_lease=item, input_sha256=item.sha256)
            return {'schemaVersion':1,'backupHmacAuthenticated':True,'databaseInputsTransferred':True,
                'databaseStateVerified':False,'personnelInvalidationVerified':False,
                'targetSignatureVerified':False,'targetConfigurationVerified':False,
                'restoreAuthorized':False,'candidateQualified':False}
        except BaseException as exc:
            self.failed = self.target.failed = True
            if isinstance(exc, UncertainExec) or self.verifier.cipher.uncertain:
                self.poison_unknown()
                if not isinstance(exc, UncertainExec):
                    raise UncertainExec('source child outcome unknown; retain copy context') from exc
            raise
        finally:
            if not self.uncertain: self.release()

    def release(self):
        if self.uncertain: raise UncertainExec('copy leases remain bound to an unknown result')
        self.active = False
        try:
            while self._exports:
                context = self._exports.pop()  # Never retry a potentially completed close.
                context.__exit__(None,None,None)
            while self._outputs: self._outputs.pop().close()
        except BaseException as exc:
            self.poison_unknown()
            raise UncertainExec('copy descriptor release is unknown; retain context') from exc
        if self in self.target.maintenance.held: self.target.maintenance.held.remove(self)
