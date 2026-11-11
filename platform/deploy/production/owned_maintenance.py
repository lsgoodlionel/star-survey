"""In-controller maintenance on recorded objects; no legacy helper subprocess.

Only ciphertext is named. Unknown child/exec results retain their anonymous
workspace and descriptor leases until the controller exits; no restore entry.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import urlencode
import uuid

from backup import BackupError, BackupManager, MIN_FREE_BYTES, VOLUME_SOURCES, WRITER_SERVICES, rewrite_authenticated_control
from doctor import Doctor, DoctorRuntimeError, ProductProbe, ProbeStageError
from owned_backup_verify import ControlledVerifier
from owned_children import ChildJournal, ControllerProcessGuard
from owned_cipher_adapter import ControlledCipher
from owned_crypto import OpenSSLProcess
from owned_docker_exec import OwnedDockerExec, KnownExecFailure
from owned_memory import AnonymousWorkspace
from runtime_ownership import OwnershipError, PrivateStore, SettledMutationFailure
from restore import RestoreIntegrityError


class UncertainMaintenance(RuntimeError):
    pass


class OwnedMaintenance:
    def __init__(self, runtime, *, _guard=None):
        self.runtime, self.manager = runtime, runtime.manager
        self.api, self.driver = runtime.probe.api, runtime.driver
        self.record = runtime.preflight_cleanup(False)
        self.guard = _guard or ControllerProcessGuard()
        self.actor = self.guard.check()
        self.uncertain, self.paused, self.held = False, set(), []
        self.sleep = time.sleep
        self.services = {}
        for cid in self.record['resources']['containers']:
            if not self.container_check(cid, require_running=False):
                raise OwnershipError('maintenance container mapping is not bound')
            row = self.driver._last_container_snapshot
            name = row.get('service')
            if name not in runtime.data['contract']['services'] or name in self.services:
                raise OwnershipError('maintenance container mapping is not uniquely bound')
            self.services[name] = cid
        if not self.services or set(self.services) != set(runtime.data['contract']['services']):
            raise OwnershipError('maintenance requires the complete recorded source service set')
        contexts = getattr(self.manager, '_owned_contexts', None)
        if contexts is None:
            contexts = []
            self.manager._owned_contexts = contexts
        contexts.append(self)  # Keep unknown workspace/lease state alive after an exception.

    def _identity(self):
        if self.api.identity() != self.record['daemonId'] or self.guard.check() != self.actor:
            raise UncertainMaintenance('maintenance daemon or controller identity changed')

    def container_check(self, cid, allowed_exec=None, *, allow_paused=False, require_running=True):
        try:
            self._identity()
            if cid not in self.record['resources']['containers']: return False
            fingerprint, _, state = self.driver._container(cid, allowed_exec=allowed_exec,
                                                          allow_paused=allow_paused and cid in self.paused)
            row = self.driver._last_container_snapshot
            execs = row.get('execs')
            return (fingerprint == self.record['resources']['containers'][cid]
                and (state == 'running' or not require_running and state == 'exited'
                     or state == 'paused' and row.get('paused') is True and allow_paused and cid in self.paused)
                and row.get('id') == cid and type(row.get('paused')) is bool
                and (not row['paused'] or allow_paused and cid in self.paused)
                and (execs in (None, []) or isinstance(execs, list)
                     and allowed_exec is not None and execs == [allowed_exec]))
        except Exception:
            return False

    def _state(self, cid, *, allow_paused=False):
        if not self.container_check(cid, allow_paused=allow_paused):
            raise UncertainMaintenance('maintenance container identity or workload is unknown')
        value = self.api._json('GET', '/containers/' + cid + '/json')
        state = value.get('State', {}) if isinstance(value, dict) and value.get('Id') == cid else {}
        if type(state.get('Running')) is not bool or type(state.get('Paused')) is not bool:
            raise UncertainMaintenance('maintenance container state is unavailable')
        return state

    def _control(self, cid, action):
        if action not in ('pause', 'unpause') or cid not in self.paused:
            raise OwnershipError('unregistered writer control was refused')
        try:
            self._identity()
            self.api._json('POST', '/containers/' + cid + '/' + action, accepted=(204,))
        except BaseException:
            # The daemon handler may still finish after a transport timeout.
            # Later snapshots or unpause replies cannot prove this RPC ended.
            self.uncertain = True
            raise

    @contextmanager
    def frozen_writers(self):
        if self.uncertain: raise UncertainMaintenance('prior maintenance remains unverified')
        writers = [self.services[name] for name in WRITER_SERVICES if name in self.services]
        if not writers: raise OwnershipError('no verified source writers are configured')
        try:
            for cid in writers:
                if not self.container_check(cid): raise UncertainMaintenance('writer preflight failed')
                self.paused.add(cid)  # Before the request: a lost reply may still have paused it.
                self._control(cid, 'pause')
                if self._state(cid, allow_paused=True)['Paused'] is not True:
                    raise UncertainMaintenance('writer pause was not observed')
            yield
        finally:
            failed = False
            for cid in writers:
                if cid not in self.paused: continue
                try:
                    state = self._state(cid, allow_paused=True)
                    if state['Paused']:
                        self._control(cid, 'unpause')
                    if self._state(cid, allow_paused=True)['Paused'] is not False:
                        raise UncertainMaintenance('writer resume was not observed')
                    self.paused.remove(cid)
                except Exception:
                    failed = True  # Still try every other recorded writer.
            if not failed:
                for attempt in range(30):
                    states = [self._state(cid) for cid in writers]
                    if all(s['Running'] and s.get('Health', {}).get('Status') == 'healthy' for s in states): break
                    if attempt == 29: failed = True
                    else: self.sleep(2)
            if failed:
                self.uncertain = True
                raise UncertainMaintenance('writer resume or health recovery is unverified; preserve markers')

    def archive(self, cid, path, output, maximum):
        if self.uncertain: raise UncertainMaintenance('prior maintenance remains unverified')
        service = next((name for name, identity in self.services.items() if identity == cid), None)
        if ((service, path) not in set(VOLUME_SOURCES.values()) or type(maximum) is not int
                or not 1 <= maximum <= 2**30 or not self.container_check(cid, allow_paused=True)):
            raise OwnershipError('archive request is outside the verified source paths')
        connection = self.api._connect()
        try:
            query = urlencode({'path': path.rstrip('/') + '/.'})
            connection.request('GET', self.api.VERSION + '/containers/' + cid + '/archive?' + query)
            response = connection.getresponse()
            if response.status != 200: raise UncertainMaintenance('archive stream is unavailable')
            length, transfer = response.getheader('Content-Length'), response.getheader('Transfer-Encoding')
            expected = int(length) if isinstance(length, str) and re.fullmatch(r'[0-9]{1,12}', length) else None
            if (expected is not None and (not 1 <= expected <= maximum or transfer is not None)
                    or expected is None and transfer != 'chunked'):
                raise UncertainMaintenance('archive HTTP completion boundary is unsupported')
            count, deadline = 0, time.monotonic() + 120
            while True:
                if time.monotonic() > deadline: raise UncertainMaintenance('archive read deadline exceeded')
                block = response.read(65536)
                if not block: break
                count += len(block)
                if count > maximum: raise UncertainMaintenance('archive exceeds the anonymous budget')
                output.write(block)
            if not count or expected is not None and count != expected:
                raise UncertainMaintenance('archive HTTP stream ended incompletely')
            if not self.container_check(cid, allow_paused=True):
                raise UncertainMaintenance('archive source changed during capture')
        except Exception as exc:
            self.uncertain = True
            raise UncertainMaintenance('archive capture could not be verified') from exc
        finally:
            connection.close()

    def _journal(self):
        operation = str(uuid.uuid4())
        store = PrivateStore(self.manager.target, name='runtime-child-' + operation.replace('-', '') + '.json')
        return ChildJournal(store, self.guard, operation_id=operation, installation_id=self.record['installationId'])

    @contextmanager
    def cipher_session(self, root):
        if self.uncertain: raise UncertainMaintenance('prior maintenance remains unverified')
        workspace, cipher = AnonymousWorkspace(), None
        self.held.append(workspace)
        try:
            key = self.runtime._private_bytes(self.manager._managed('shared/secrets/backup_encryption_key'), 1025)
            key = key[:-1] if key.endswith(b'\n') else key
            cipher = ControlledCipher(workspace, OpenSSLProcess(self._journal(), self.guard), root, key)
            self.held.append(cipher)
            yield workspace, cipher
        finally:
            if cipher is not None and cipher.uncertain: self.uncertain = True
            if not self.uncertain:
                try:
                    if cipher is not None: cipher.close()
                    workspace.close()
                    if cipher is not None: self.held.remove(cipher)
                    self.held.remove(workspace)
                except Exception as exc:
                    self.uncertain = True
                    raise UncertainMaintenance('anonymous workspace cleanup is unverified') from exc

    def exec_file(self, profile, service, plain, *, body=None):
        if self.uncertain: raise UncertainMaintenance('prior maintenance remains unverified')
        if service not in self.services: raise OwnershipError('exec service is not in the source contract')
        operation = str(uuid.uuid4())
        store = PrivateStore(self.manager.target, name='runtime-exec-' + operation.replace('-', '') + '.json')
        executor = OwnedDockerExec(self.api, store, self.guard, container_check=self.container_check,
            operation_id=operation, installation_id=self.record['installationId'])
        export = plain.export_for_child(write=True)
        lease = export.__enter__()
        self.held.append(export)
        try:
            executor.execute(profile, self.services[service], lease, body=body)
        except KnownExecFailure:
            export.__exit__(None, None, None); self.held.remove(export)
            raise
        except BaseException:
            self.uncertain = True  # Do not release a lease on an unknown exec result.
            raise
        export.__exit__(None, None, None); self.held.remove(export)

    def exec_bytes(self, profile, service, *, body=None):
        if self.uncertain: raise UncertainMaintenance('prior maintenance remains unverified')
        workspace = AnonymousWorkspace(max_bytes=1024 * 1024, max_files=1)
        self.held.append(workspace)
        try:
            plain = workspace / 'diagnostic'
            self.exec_file(profile, service, plain, body=body)
            return plain.read_bytes()
        finally:
            if not self.uncertain:
                workspace.close(); self.held.remove(workspace)

    def _perform(self, kind, effect):
        if self.uncertain: raise UncertainMaintenance('prior maintenance remains unverified')
        result = []
        def run():
            try: result.append(effect())
            except Exception as exc:
                if not self.uncertain and self.runtime.probe() == 'quiescent':
                    raise SettledMutationFailure('maintenance failed with its cohort ended') from exc
                raise
        try:
            self.runtime.protocol.mutate(kind, run)
            return result[0]
        finally:
            if not self.uncertain and not self.held:
                self.manager._owned_contexts.remove(self)

    def backup(self, output):
        output = Path(output)
        self.preflight_backup(output)
        version = self.runtime.data['version']
        def create():
            self.manager._mkdir('backups')  # Only after the owned mutation intent is durable.
            state = BackupManager(self.manager.target)._state(version)
            staging = output.with_name('.' + output.name + '.partial-' + uuid.uuid4().hex)
            staging.mkdir(mode=0o700)
            with self.cipher_session(staging) as (workspace, cipher):
                with self.frozen_writers():
                    for name, service, profile in (('postgres.dump', 'platform-db', 'postgres-dump'),
                                                   ('mariadb.sql', 'engine-db', 'mariadb-dump')):
                        plain = workspace / name
                        self.exec_file(profile, service, plain)
                        if plain.stat().st_size == 0: raise BackupError('database dump is empty')
                        cipher.encrypt(plain, staging / (name + '.enc'), cipher.key)
                        plain.unlink()
                    for name, (service, path) in VOLUME_SOURCES.items():
                        if service not in self.services: raise OwnershipError('backup volume service is absent')
                        plain = workspace / name
                        with plain.open('xb') as handle:
                            self.archive(self.services[service], path, handle, workspace.max_bytes)
                        cipher.encrypt(plain, staging / (name + '.enc'), cipher.key)
                        plain.unlink()
                metadata = dict(schemaVersion=2, version=version, files={}, **state)
                rewrite_authenticated_control(staging, cipher.key, metadata)
                ControlledVerifier(self.manager.target, cipher).verify_backup(staging, workspace)
            if os.path.lexists(output): raise OwnershipError('backup destination appeared during creation')
            # All children/streams have ended and the complete old verification
            # path succeeded. Publishing still cannot bypass the outer intent.
            os.rename(staging, output)
            return output
        return self._perform('backup', create)

    def preflight_backup(self, output):
        output = Path(output)
        if (output.parent != self.manager.target / 'backups' or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', output.name)
                or output.name in ('.', '..') or os.path.lexists(output)):
            raise OwnershipError('backup output must be an absent managed child')
        parent = self.manager._managed('backups')
        if parent.exists() and not parent.is_dir(): raise BackupError('backup parent is not a directory')
        if shutil.disk_usage(parent if parent.exists() else self.manager.target).free < MIN_FREE_BYTES:
            raise BackupError('insufficient free space for backup')
        key = self.runtime._private_bytes(self.manager._managed('shared/secrets/backup_encryption_key'), 1025)
        key = key[:-1] if key.endswith(b'\n') else key
        if (not 32 <= len(key) <= 1024 or key.strip() != key
                or any(value in key for value in (b'\n', b'\r', b'\x00'))):
            raise BackupError('backup encryption key is invalid')

    def verify_backup(self, backup):
        with self.cipher_session(Path(backup)) as (workspace, cipher):
            return ControlledVerifier(self.manager.target, cipher).verify_only(Path(backup))

    def doctor(self):
        def run():
            runner = OwnedDoctorRunner(self)
            return OwnedDoctor(self, runner).run()
        return self._perform('doctor', run)


class OwnedProductProbe(ProductProbe):
    def __init__(self, operation, host, compose, runner):
        super().__init__(operation.manager.target, host, compose, runner)
        self.operation, self.close_failure = operation, None

    def _close_engine_survey(self, instance, sid):
        try:
            if self.operation.uncertain: raise UncertainMaintenance('prior exec is still unverified')
            self.operation.exec_bytes('gateway-close', 'publish-gateway', body={
                'requestId': str(uuid.uuid4()), 'engineInstanceId': instance, 'surveyId': sid})
        except Exception as exc:
            self.close_failure = exc  # Let the inherited finally still archive its project.

    def run(self):
        try:
            return super().run()
        finally:
            if self.close_failure is not None:
                if self.operation.uncertain: raise UncertainMaintenance('probe cleanup exec is unverified') from self.close_failure
                raise ProbeStageError('cleanup') from self.close_failure


class OwnedDoctor(Doctor):
    def __init__(self, operation, runner):
        self.operation = operation
        super().__init__(operation.manager.target, runner=runner,
            product_probe=lambda host, compose: OwnedProductProbe(operation, host, compose, runner).run())

    def _load_installation(self):
        version, schema, host, _ = super()._load_installation()
        if version != self.operation.runtime.data['version']: raise DoctorRuntimeError('doctor source version differs')
        return version, schema, host, self.operation.manager._compose(version)

    def _backup_check(self):
        root = self.target / 'backups'
        valid = 0
        if root.exists():
            for directory in root.iterdir():
                if directory.is_symlink() or not directory.is_dir() or directory.name.startswith('.'): continue
                try:
                    self.operation.verify_backup(directory)
                    valid += 1
                except (BackupError, OSError, RestoreIntegrityError):
                    continue
        return self._check('backups', 'ok' if valid else 'warning',
            f'{valid} verified backup(s) available' if valid else 'no completed backup exists yet')


class OwnedDoctorRunner:
    def __init__(self, operation):
        self.operation = operation
        self.compose = operation.manager._compose(operation.runtime.data['version'])

    def run(self, command, **kwargs):
        args = list(command)
        compose = self.compose
        profiles = {
            ('platform-db', 'psql', '-U', 'postgres', '-d', 'platform', '-Atqc',
             'SELECT version FROM flyway_schema_history WHERE success ORDER BY installed_rank DESC LIMIT 1'): 'postgres-schema',
            ('engine', 'sh', '-c', 'php application/commands/console.php productionInit status'): 'engine-status',
            ('engine', '/bin/sh', '/opt/survey/run-with-secrets.sh',
             'MJY_PLATFORM_EVENTS_SECRET=/run/secrets/engine_instance_events_secret', '--',
             'php', 'application/commands/console.php', 'plugin', 'cron'): 'engine-event-cron',
        }
        if args[:len(compose) + 2] == compose + ['exec', '-T']:
            suffix = tuple(args[len(compose) + 2:])
            if suffix not in profiles: raise DoctorRuntimeError('unregistered diagnostic exec was refused')
            output = self.operation.exec_bytes(profiles[suffix], suffix[0])
            return subprocess.CompletedProcess(command, 0, output.decode('utf-8', 'strict'), '')
        read_only = (args[:len(compose)] == compose and args[len(compose):] in (
            ['config', '--format', 'json'], ['ps', '--all', '--format', 'json']))
        read_only = read_only or args == ['ss', '-H', '-ltnp'] or (
            len(args) == 5 and args[:2] == ['ps', '-p'] and re.fullmatch(r'[1-9][0-9]{0,9}', args[2])
            and args[3:] == ['-o', 'args='])
        if args[:2] == ['docker', 'inspect']:
            ids = args[2:]
            if not ids or len(ids) != len(set(ids)) or set(ids) != set(self.operation.services.values()):
                raise DoctorRuntimeError('diagnostic inspection set is not bound')
            values = []
            for cid in ids:
                if not self.operation.container_check(cid, require_running=False): raise DoctorRuntimeError('diagnostic container drifted')
                value = self.operation.api._json('GET', '/containers/' + cid + '/json')
                values.append({'Id': cid, 'Config': {'Labels': {
                    'com.docker.compose.service': next(k for k, v in self.operation.services.items() if v == cid)}},
                    'HostConfig': {'PortBindings': value.get('HostConfig', {}).get('PortBindings')},
                    'NetworkSettings': {'Networks': value.get('NetworkSettings', {}).get('Networks')}})
            return subprocess.CompletedProcess(command, 0, json.dumps(values), '')
        if args[:3] == ['docker', 'volume', 'inspect']:
            read_only = (len(args[3:]) == len(set(args[3:]))
                         and set(args[3:]) == set(self.operation.record['resources'].get('volumes', {})))
        if not read_only: raise DoctorRuntimeError('unregistered diagnostic command was refused')
        self.operation._identity()
        options = dict(kwargs, check=True, capture_output=True, text=True, timeout=30)
        result = self.operation.manager._run(args, 'controlled diagnostic query failed', **options)
        if not isinstance(result.stdout, str) or len(result.stdout.encode()) > 1024 * 1024:
            raise DoctorRuntimeError('diagnostic output is unavailable or oversized')
        self.operation._identity()
        return result
