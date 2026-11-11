"""A captured, isolated Caddy hash container; no password in argv/API config."""
from copy import deepcopy
import re
from urllib.parse import quote

import runtime_ownership as ownership
from owned_docker_exec import LocalDockerAPI


class KnownHashFailure(OSError):
    pass


class UncertainHashHelper(RuntimeError):
    pass


class OwnedContainerHash:
    def __init__(self, api, store, guard, *, operation_id, installation_id, image, target_sha256):
        if (not ownership.canonical_uuid(operation_id) or not ownership.canonical_uuid(installation_id)
                or not isinstance(image, str) or image.startswith('-') or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image)
                or not isinstance(target_sha256, str) or not ownership.HEX64.fullmatch(target_sha256)):
            raise KnownHashFailure('helper binding is invalid')
        self.api, self.store, self.guard = api, store, guard
        self.operation, self.installation, self.image, self.target = operation_id, installation_id, image, target_sha256
        self.name = ownership.PROJECT + '-hash-' + operation_id.replace('-', '')
        self.actor, self.saved = guard.check(), None

    def _save(self, record):
        if self.guard.check() != self.actor or self.store.load() != self.saved:
            raise UncertainHashHelper('hash helper actor or receipt changed')
        self.store.save(deepcopy(record))
        if self.store.load() != record:
            raise UncertainHashHelper('hash helper receipt publication is unverified')
        self.saved = deepcopy(record)

    def _inspect(self, cid, config):
        value = self.api._json('GET', '/containers/' + cid + '/json')
        if (not isinstance(value, dict) or value.get('Id') != cid or value.get('Name') != '/' + self.name
                or value.get('Config', {}).get('Image') != self.image
                or not isinstance(value['Config'].get('Labels'), dict)
                or any(value['Config']['Labels'].get(key) != item for key, item in config['Labels'].items())
                or any(key.startswith(('io.survey.', 'com.docker.compose.')) and key not in config['Labels']
                       for key in value['Config']['Labels'])
                or value['Config'].get('Entrypoint') != config['Entrypoint']
                or value['Config'].get('Cmd') != config['Cmd'] or value['Config'].get('User') != '65534:65534'):
            raise UncertainHashHelper('hash helper container identity changed')
        host = value.get('HostConfig', {})
        for key in ('NetworkMode', 'ReadonlyRootfs', 'CapDrop', 'SecurityOpt', 'PidsLimit', 'Memory', 'NanoCpus', 'Tmpfs'):
            if host.get(key) != config['HostConfig'][key]:
                raise UncertainHashHelper('hash helper isolation changed')
        if (host.get('Privileged', False) is not False or host.get('Binds') or host.get('PortBindings')
                or host.get('CapAdd') or host.get('Devices') or host.get('PidMode') not in (None, '', 'private')
                or any(mount.get('Type') != 'tmpfs' for mount in value.get('Mounts', []))):
            raise UncertainHashHelper('hash helper has unexpected host access or mounts')
        return value['State']

    def run(self, password):
        started, cid = False, None
        try:
            if (self.saved is not None or self.store.load() is not None or not isinstance(password, bytes)
                    or not 32 <= len(password) <= 1024 or password.strip() != password
                    or any(value in password for value in (b'\n', b'\r', b'\x00'))):
                raise KnownHashFailure('hash helper input or prior receipt is invalid')
            image = self.api._json('GET', '/images/' + quote(self.image, safe='') + '/json')
            volumes = image.get('Config', {}).get('Volumes') or {}
            if not isinstance(volumes, dict) or set(volumes) - {'/data', '/config'}:
                raise KnownHashFailure('hash helper image declares unsupported volumes')
            tmpfs = {path: 'rw,nosuid,nodev,noexec,size=2m,mode=1777' for path in volumes}
            tmpfs['/tmp'] = 'rw,nosuid,nodev,noexec,size=1m,mode=1777'
            daemon = self.api.identity()
            record = {'schemaVersion': 1, 'kind': 'controlled-container-helper', 'operationId': self.operation,
                'installationId': self.installation, 'owner': self.actor, 'daemonId': daemon,
                'profile': 'caddy-hash', 'image': self.image, 'phase': 'create-pending', 'containerId': None, 'exitCode': None}
            self._save(record)
            config = {'Image': self.image, 'User': '65534:65534', 'Env': ['GOMAXPROCS=1'],
                'Entrypoint': ['/bin/busybox', 'timeout', '-s', 'KILL', '30', '/usr/bin/caddy', 'hash-password'],
                'Cmd': ['--algorithm', 'bcrypt'],
                'OpenStdin': True, 'StdinOnce': True, 'AttachStdin': True, 'AttachStdout': True, 'AttachStderr': True, 'Tty': False,
                'Labels': {'io.survey.installation': self.installation, 'io.survey.role': 'caddy-hash',
                    'io.survey.target-sha256': self.target, 'com.docker.compose.project': ownership.PROJECT},
                'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
                    'SecurityOpt': ['no-new-privileges:true'], 'PidsLimit': 64, 'Memory': 268435456,
                    'NanoCpus': 1000000000, 'Tmpfs': tmpfs}}
            created = self.api._json('POST', '/containers/create?name=' + quote(self.name, safe=''), config)
            if (not isinstance(created, dict) or created.get('Warnings') not in (None, [])
                    or not isinstance(created.get('Id'), str) or not ownership.HEX64.fullmatch(created['Id'])):
                raise UncertainHashHelper('hash helper creation identity is unknown')
            cid = created['Id']
            record.update(phase='created', containerId=cid)
            self._save(record)
            state = self._inspect(cid, config)
            if state.get('Status') != 'created' or state.get('Running') is not False or self.api.identity() != daemon:
                raise UncertainHashHelper('hash helper changed before start')
            with self.api.attach_input(cid) as channel:
                record['phase'] = 'released'
                self._save(record)
                started = True
                self.api._json('POST', '/containers/' + cid + '/start', accepted=(204,))
                output = channel.finish(password + b'\n', 61)
            ended = self.api._json('POST', '/containers/' + cid + '/wait?condition=not-running')
            state = self._inspect(cid, config)
            code = ended.get('StatusCode')
            if (type(code) is not int or not 0 <= code <= 255 or ended.get('Error')
                    or state.get('Running') is not False or state.get('Status') != 'exited'
                    or type(state.get('ExitCode')) is not int or state['ExitCode'] != code
                    or not isinstance(state.get('StartedAt'), str) or state['StartedAt'].startswith('0001-')
                    or self.api.identity() != daemon or self.guard.check() != self.actor):
                raise UncertainHashHelper('hash helper end is unverified')
            valid = code == 0 and isinstance(output, bytes) and re.fullmatch(rb'\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}\n', output) is not None
            self.api._json('DELETE', '/containers/' + cid + '?force=false&v=false', accepted=(204,))
            if self.api.container_absent(cid) is not True:
                raise UncertainHashHelper('hash helper removal is unverified')
            record.update(phase='completed' if valid else 'failed', exitCode=code)
            self._save(record)
            if not valid:
                raise KnownHashFailure('hash helper ended without a valid hash')
            return output
        except (KnownHashFailure, UncertainHashHelper):
            raise
        except BaseException as exc:
            if started or cid is not None or self.saved is not None:
                raise UncertainHashHelper('hash helper result is unknown; preserve receipt') from exc
            raise KnownHashFailure('hash helper was not started') from exc
