"""Observe registered maintenance cohorts under the real installation lock.

Only a fresh, empty-generation startup can have no owner record. This is not
legacy process discovery: controller code must never launch unregistered old
helpers after adopting this protocol, and a previous pending intent blocks it.
"""
import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

import runtime_ownership as ownership
from owned_children import read_process_identity, _pid, _start
from owned_docker_exec import ExecMetadataGone, LocalDockerAPI, PROFILES


def _uuid(value):
    return ownership.canonical_uuid(value)


class CohortProbe:
    def __init__(self, target, lock_fd, active_lock, *, api=None, fresh=False, _process_reader=None):
        self.target, self.fd, self.active = Path(target), lock_fd, active_lock
        self.api, self.fresh = api or LocalDockerAPI(), fresh is True
        self.reader = _process_reader or read_process_identity
        self.lock_identity = self._lock()

    def _lock(self):
        if type(self.fd) is not int or self.fd <= 2 or not callable(self.active) or self.active() is not True:
            raise ownership.OwnershipError('cohort requires the active controller lock')
        info = os.fstat(self.fd)
        path = os.lstat(self.target / '.surveyctl/operation.lock')
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or (info.st_dev, info.st_ino) != (path.st_dev, path.st_ino)):
            raise ownership.OwnershipError('cohort lock identity is unsafe')
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid

    def _ended(self, pid, start, *, owner=False):
        if not _pid(pid) or not _start(start):
            return False
        value = self.reader(pid)
        if value is None:
            return True
        if (not isinstance(value, dict) or set(value) != {'pid', 'start', 'state', 'parentPid'}
                or value['pid'] != pid or not _start(value['start'])
                or value['state'] not in {'R', 'S', 'D', 'T', 't', 'I', 'W', 'P', 'Z', 'X'}):
            return False
        return (value['start'] != start or value['state'] in ('Z', 'X')
            or owner and pid == os.getpid() and value['start'] == start)

    def _receipt(self, name, record, owner):
        if (not isinstance(record, dict) or type(record.get('schemaVersion')) is not int or record['schemaVersion'] not in (1, 2)
                or not _uuid(record.get('operationId'))
                or record.get('installationId') not in [owner['installationId'], *owner.get('retiredInstallations', [])]):
            return False
        version = record['schemaVersion']
        if record.get('kind') != 'controlled-docker-exec' and version != 1:
            return False
        retired = record['installationId'] != owner['installationId']
        actor = record.get('owner')
        if (not isinstance(actor, dict) or set(actor) != {'pid', 'start', 'parentPid', 'parentStart'}
                or not _pid(actor['parentPid']) or not _start(actor['parentStart'])
                or not self._ended(actor['pid'], actor['start'], owner=not retired)):
            return False
        suffix = record['operationId'].replace('-', '') + '.json'
        if record.get('kind') == 'controlled-container-helper':
            if (name != 'runtime-launch-' + suffix or set(record) != {'schemaVersion', 'kind', 'operationId',
                    'installationId', 'owner', 'daemonId', 'profile', 'image', 'phase', 'containerId', 'exitCode'}
                    or record['daemonId'] != owner['daemonId'] or record['profile'] != 'caddy-hash'
                    or record['phase'] not in ('completed', 'failed') or type(record['exitCode']) is not int
                    or not 0 <= record['exitCode'] <= 255
                    or record['phase'] == 'completed' and record['exitCode'] != 0
                    or not isinstance(record['image'], str) or record['image'].startswith('-')
                    or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', record['image'])
                    or not isinstance(record['containerId'], str) or not ownership.HEX64.fullmatch(record['containerId'])):
                return False
            # A terminal flag or stopped container is insufficient: a retained
            # container can be started again. Pending never reaches this check.
            return self.api.container_absent(record['containerId']) is True
        if record.get('kind') == 'controlled-docker-exec':
            fields = {'schemaVersion', 'kind', 'operationId', 'installationId', 'owner', 'daemonId',
                      'containerId', 'profile', 'phase', 'execId', 'exitCode'}
            if version == 2:
                fields.add('completionEvidence')
            if (name != 'runtime-exec-' + suffix or set(record) != fields
                    or record['daemonId'] != owner['daemonId'] or record['phase'] not in ('completed', 'failed')
                    or record['profile'] not in PROFILES or type(record['exitCode']) is not int
                    or not 0 <= record['exitCode'] <= 255 or record['phase'] == 'completed' and record['exitCode'] != 0
                    or not isinstance(record['execId'], str) or not ownership.HEX64.fullmatch(record['execId'])
                    or not isinstance(record['containerId'], str) or not ownership.HEX64.fullmatch(record['containerId'])):
                return False
            durable = False
            if version == 2:
                proof = record['completionEvidence']
                durable = (isinstance(proof, dict) and set(proof) == {'kind', 'ID', 'ContainerID', 'Running', 'ExitCode'}
                    and proof['kind'] == 'exec-inspect-terminal-v1' and proof['ID'] == record['execId']
                    and proof['ContainerID'] == record['containerId'] and proof['Running'] is False
                    and type(proof['ExitCode']) is int and proof['ExitCode'] == record['exitCode'])
                if not durable:
                    return False
            if self.api.container_absent(record['containerId']) is True:
                return True  # Immutable original container gone; this exec cannot start again.
            if retired:
                return False
            try:
                ended = self.api.inspect(record['execId'])
            except ExecMetadataGone:
                # Moby GC discards ended exec configs; the single-shot ExecID
                # cannot restart. Trust only this locked private writer's exact
                # prior observation. v1/missing proof and all other errors fail.
                return durable
            return (isinstance(ended, dict) and ended.get('ID') == record['execId']
                and ended.get('ContainerID') == record['containerId'] and ended.get('Running') is False
                and type(ended.get('ExitCode')) is int and ended['ExitCode'] == record['exitCode'])
        if record.get('kind') == 'controlled-helper-children':
            if (name != 'runtime-child-' + suffix or set(record) != {'schemaVersion', 'kind', 'operationId',
                    'installationId', 'owner', 'children'} or not isinstance(record['children'], list)
                    or len(record['children']) > 64):
                return False
            for number, entry in enumerate(record['children'], 1):
                if (not isinstance(entry, dict) or set(entry) != {'sequence', 'profile', 'phase', 'pid', 'start', 'exitCode', 'reason'}
                        or type(entry['sequence']) is not int or entry['sequence'] != number
                        or entry['profile'] not in ('openssl-encrypt', 'openssl-decrypt')):
                    return False
                if entry['phase'] == 'not-started':
                    if entry['pid'] is not None or entry['start'] is not None or entry['exitCode'] is not None or entry['reason'] != 'bootstrap':
                        return False
                elif (entry['phase'] not in ('completed', 'failed') or type(entry['exitCode']) is not int
                        or not -64 <= entry['exitCode'] <= 255 or not self._ended(entry['pid'], entry['start'])
                        or entry['phase'] == 'completed' and (entry['exitCode'] != 0 or entry['reason'] != 'completed')
                        or entry['phase'] == 'failed' and entry['reason'] not in ('exit', 'deadline', 'bootstrap', 'journal')):
                    return False
            return True
        return False  # Unknown launch formats never become evidence by being absent.

    def __call__(self):
        try:
            if self._lock() != self.lock_identity:
                return 'unknown'
            store = ownership.PrivateStore(self.target)
            parent, chain = store._open_parent()
            try:
                names = sorted(os.listdir(parent))
                if len(names) > 256:
                    return 'unknown'
                owner = store.load()
                receipt_names = [name for name in names if name.lstrip('.').startswith('runtime-')
                    and name not in ('runtime-owner.json', 'runtime-contract.json', 'runtime-owner.compose.json')]
                if owner is None:
                    return ('quiescent' if self.fresh and not receipt_names
                        and not os.path.lexists(self.target / '.surveyctl/state.json')
                        and not os.path.lexists(self.target / 'current') else 'unknown')
                keys = {'schemaVersion', 'project', 'daemonId', 'installationId', 'targetSha256', 'claimId', 'image', 'phase', 'resources'}
                retired = owner.get('retiredInstallations', [])
                if (set(owner) not in (keys, keys | {'retiredInstallations'}) or type(owner['schemaVersion']) is not int
                        or owner['schemaVersion'] != 1 or owner['project'] != ownership.PROJECT
                        or not _uuid(owner['installationId']) or owner['daemonId'] != self.api.identity()
                        or owner['targetSha256'] != hashlib.sha256(str(self.target).encode()).hexdigest()
                        or owner['phase'] not in ownership.PHASES or not isinstance(retired, list)
                        or len(retired) > 16 or len(set(retired)) != len(retired)
                        or any(not _uuid(value) or value == owner['installationId'] for value in retired)):
                    return 'unknown'
                for name in receipt_names:
                    if not re.fullmatch(r'runtime-(?:child|exec|launch)-[0-9a-f]{32}\.json', name):
                        return 'unknown'
                    record = ownership.PrivateStore(self.target, name=name).load()
                    if not self._receipt(name, record, owner):
                        return 'unknown'
                if names != sorted(os.listdir(parent)) or self._lock() != self.lock_identity:
                    return 'unknown'
                store._recheck(parent, chain)
                return 'quiescent'
            finally:
                os.close(parent)
        except Exception:
            return 'unknown'


class ReceiptRetirement:
    """Isolated primitive; no default caller. Callbacks are trusted controller code.

    Never clears intent, repairs unknown receipts, or raises directory capacity.
    The caller must supply real full resource/claim verification and durable
    begin-operation; injected callbacks in tests are not production evidence.
    """
    def __init__(self, probe, check_resources, begin_operation, operation):
        if not callable(check_resources) or not callable(begin_operation) or not isinstance(operation, str) or not re.fullmatch(r'[a-z_]{1,32}', operation):
            raise ownership.OwnershipError('retirement controller contract is invalid')
        self.probe, self.check, self.begin, self.operation = probe, check_resources, begin_operation, operation
        self._reservation_deadline=None
        self._full_passes=0

    def _budget(self, *, full=False):
        if self._reservation_deadline is None: return
        if time.monotonic()>=self._reservation_deadline:
            raise ownership.OwnershipError('receipt admission time budget exceeded')
        if full:
            self._full_passes+=1
            if self._full_passes>5:
                raise ownership.OwnershipError('receipt admission query pass budget exceeded')

    def _capture(self):
        self._budget(full=True)
        probe = self.probe
        if probe._lock() != probe.lock_identity or probe() != 'quiescent':
            raise ownership.OwnershipError('receipt cohort is unverified')
        self._budget()
        store = ownership.PrivateStore(probe.target)
        parent, chain = store._open_parent()
        try:
            names = sorted(os.listdir(parent))
            if len(names) > 256:
                raise ownership.OwnershipError('receipt directory capacity exceeded')
            owner = store.load()
            if not isinstance(owner, dict) or owner.get('phase') not in ('ready', 'retained-data', 'removed', 'settled-failure') or self.check() != owner:
                raise ownership.OwnershipError('retirement resources or owner are unverified')
            self._budget()
            receipts = {}
            for name in names:
                self._budget()
                if not re.fullmatch(r'runtime-(?:child|exec|launch)-[0-9a-f]{32}\.json', name): continue
                identity = store._regular(os.stat(name, dir_fd=parent, follow_symlinks=False))
                record = ownership.PrivateStore(probe.target, name=name).load()
                if not probe._receipt(name, record, owner) or identity != store._regular(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                    raise ownership.OwnershipError('receipt end or identity is unverified')
                self._budget()
                digest = hashlib.sha256(json.dumps(record,sort_keys=True,allow_nan=False).encode()).hexdigest()
                receipts[name] = (identity, digest)
            if names != sorted(os.listdir(parent)) or probe._lock() != probe.lock_identity:
                raise ownership.OwnershipError('retirement inventory changed')
            store._recheck(parent, chain)
            self._budget()
            return {'owner': owner, 'ownerIdentity':store._regular(os.stat(store.NAME,dir_fd=parent,follow_symlinks=False)),
                    'directory':store._directory_identity(os.fstat(parent)),
                    'names':[name for name in names if name != 'operation.intent'], 'receipts':receipts}
        finally:
            os.close(parent)

    def plan(self):
        snapshot = self._capture()
        return {'schemaVersion':1,'executable':False,'count':len(snapshot['receipts']),
                'files':sorted(snapshot['receipts']), 'batchCount':min(16,len(snapshot['receipts']))}

    def _intent(self, parent, store):
        fd=os.open('operation.intent',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        with os.fdopen(fd,'rb') as handle:
            before=store._regular(os.fstat(handle.fileno()))
            data=handle.read(1025)
            if len(data)>1024 or before!=store._regular(os.fstat(handle.fileno())) or before!=store._regular(os.stat('operation.intent',dir_fd=parent,follow_symlinks=False)):
                raise ownership.OwnershipError('retirement intent changed')
        record=json.loads(data,object_pairs_hook=store._unique)
        if record!={'schemaVersion':1,'state':'pending','operation':self.operation} or type(record.get('schemaVersion')) is not int:
            raise ownership.OwnershipError('durable pending retirement intent is absent')
        return before

    def _local_check(self, parent, chain, store, expected, intent):
        # Terminal identities already observed in this locked call cannot start
        # again. Keep proving the local witness unchanged; do not re-query the
        # whole daemon inventory for every file. No pending/unknown witness is
        # admitted by _capture. Full resource checks bracket the batch.
        self._budget()
        store._recheck(parent,chain)
        if self.probe._lock()!=self.probe.lock_identity or self._intent(parent,store)!=intent:
            raise ownership.OwnershipError('retirement lock or intent changed')
        names=sorted(os.listdir(parent))
        if len(names)>256 or [name for name in names if name!='operation.intent']!=expected['names']:
            raise ownership.OwnershipError('retirement directory set changed')
        if store._directory_identity(os.fstat(parent))!=expected['directory'] or store._regular(os.stat(store.NAME,dir_fd=parent,follow_symlinks=False))!=expected['ownerIdentity'] or store.load()!=expected['owner'] or store._regular(os.stat(store.NAME,dir_fd=parent,follow_symlinks=False))!=expected['ownerIdentity']:
            raise ownership.OwnershipError('retirement owner changed')
        for name,(identity,digest) in expected['receipts'].items():
            self._budget()
            if store._regular(os.stat(name,dir_fd=parent,follow_symlinks=False))!=identity:
                raise ownership.OwnershipError('retirement file identity changed')
            record=ownership.PrivateStore(self.probe.target,name=name).load()
            if identity!=store._regular(os.stat(name,dir_fd=parent,follow_symlinks=False)) or hashlib.sha256(json.dumps(record,sort_keys=True,allow_nan=False).encode()).hexdigest()!=digest:
                raise ownership.OwnershipError('retirement file contents changed')
        self._budget()

    def reserve(self, receipt_slots):
        """Capacity-driven admission, at most one batch and five full passes.

        Thirty seconds is a cooperative success-admission budget, not hard
        cancellation of an opaque resource callback or filesystem syscall.
        """
        if type(receipt_slots) is not int or not 1<=receipt_slots<=256:
            raise ownership.OwnershipError('receipt allocation bound is invalid')
        self._reservation_deadline=time.monotonic()+30
        self._full_passes=0
        def demand(snapshot):
            exists=os.path.lexists(self.probe.target/'.surveyctl/operation.intent')
            return len(snapshot['names'])+int(exists)+receipt_slots+int(not exists)+1-256
        snapshot=self._capture()
        needed=demand(snapshot)
        if needed<=0:
            self._budget()
            return  # No sweeping history or writing intent merely to free space.
        if needed>min(16,len(snapshot['receipts'])):
            raise ownership.OwnershipError('fixed operation cannot fit one retirement batch')
        if self.run()<needed:
            raise ownership.OwnershipError('receipt retirement did not free the reserved capacity')
        if demand(self._capture())>0:
            raise ownership.OwnershipError('receipt reservation remains unverified')
        self._budget()

    def run(self):
        expected=self._capture()
        if not expected['receipts']: return 0
        store=ownership.PrivateStore(self.probe.target)
        parent,chain=store._open_parent()
        try:
            names=sorted(os.listdir(parent))
            # Existing intent replacement also needs one temporary directory
            # entry; missing intent publication needs the same one-slot peak.
            if len(names)+1>256 or [name for name in names if name!='operation.intent']!=expected['names']:
                raise ownership.OwnershipError('pending intent capacity is unavailable')
            self.begin()  # No unlink before durable pending intent succeeds.
            intent=self._intent(parent,store)
            if self._capture()!=expected:
                raise ownership.OwnershipError('retirement plan is stale')
            count=0
            for name in sorted(expected['receipts'])[:16]:
                self._local_check(parent,chain,store,expected,intent)
                # POSIX has no compare-and-unlink here. This final identity
                # recheck narrows the window; it is not exclusion against an
                # arbitrary same-uid writer after the check.
                store._recheck(parent,chain)
                if store._regular(os.stat(name,dir_fd=parent,follow_symlinks=False))!=expected['receipts'][name][0]:
                    raise ownership.OwnershipError('retirement target changed before unlink')
                self._budget() # Detect expiry observed in the final stat before deletion.
                os.unlink(name,dir_fd=parent)
                os.fsync(parent)
                if name in os.listdir(parent): raise ownership.OwnershipError('retired receipt remains')
                del expected['receipts'][name]; expected['names'].remove(name)
                self._local_check(parent,chain,store,expected,intent)
                count+=1
            if self._capture()!=expected or self._intent(parent,store)!=intent:
                raise ownership.OwnershipError('retirement outcome is unverified')
            store._recheck(parent,chain)
            return count  # Pending belongs to the outer operation; never clear it.
        finally:
            os.close(parent)
