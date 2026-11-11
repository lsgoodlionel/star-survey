"""Fixed-stack source contract and lifecycle bridge. No legacy target adoption."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re

import runtime_ownership as ownership
from runtime_cohort import CohortProbe
from runtime_docker import APIOwnershipDriver
from owned_docker_exec import LocalDockerAPI


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_contract(manager, version):
    if os.environ.get('DOCKER_CONTEXT') or os.environ.get('DOCKER_HOST') not in (None, '', ownership.DockerOwnershipDriver.HOST):
        raise ownership.OwnershipError('owned lifecycle requires the local daemon')
    release = manager._release_dir(version)
    manager._verify_staged_release(release, version)
    manifest = manager._release_manifest(version)
    environment = dict(os.environ, DOCKER_HOST=ownership.DockerOwnershipDriver.HOST)
    def query(command):
        result = manager.runner.run(command, check=True, text=True, capture_output=True, timeout=30, env=environment)
        if (type(result.returncode) is not int or result.returncode != 0 or not isinstance(result.stdout, str)
                or len(result.stdout.encode()) > 1024 * 1024):
            raise ownership.OwnershipError('source topology query is unavailable or oversized')
        return json.loads(result.stdout, object_pairs_hook=ownership.PrivateStore._unique)
    try:
        config = query(manager._compose(version, owned_overlay=False) + ['config', '--format', 'json'])
        if (not isinstance(config, dict) or config.get('name', ownership.PROJECT) != ownership.PROJECT
                or not isinstance(config.get('services'), dict) or not 1 <= len(config['services']) <= 32):
            raise ValueError('unsupported topology')
        result = {'services': {}, 'networks': {}, 'volumes': {}}
        for kind in ('networks', 'volumes'):
            values = config.get(kind, {})
            if not isinstance(values, dict) or len(values) > 64:
                raise ValueError('unsupported object inventory')
            for logical, value in values.items():
                if (not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', logical) or not isinstance(value, dict)
                        or value.get('external') or value.get('driver_opts')
                        or any(key.startswith(('com.docker.compose.', 'io.survey.')) for key in value.get('labels', {}))):
                    raise ValueError('external objects or reserved labels are unsupported')
                name = value.get('name', ownership.PROJECT + '_' + logical)
                if name != ownership.PROJECT + '_' + logical:
                    raise ValueError('custom object names are unsupported')
                driver = value.get('driver', 'local' if kind == 'volumes' else 'bridge')
                if driver != ('local' if kind == 'volumes' else 'bridge'):
                    raise ValueError('unsupported driver')
                result[kind][name] = {'logical': logical, 'driver': driver}
                if kind == 'networks':
                    internal = value.get('internal', False)
                    if type(internal) is not bool: raise ValueError('invalid internal network flag')
                    result[kind][name]['internal'] = internal
        image_cache = {}
        for name, service in config['services'].items():
            if (not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name) or not isinstance(service, dict)
                    or service.get('container_name', ownership.PROJECT + '-' + name + '-1') != ownership.PROJECT + '-' + name + '-1'
                    or service.get('scale', 1) != 1 or service.get('deploy', {}).get('replicas', 1) != 1
                    or any(key.startswith(('com.docker.compose.', 'io.survey.')) for key in service.get('labels', {}))):
                raise ValueError('unsupported service identity')
            image = service.get('image')
            if image not in manifest.raw['images'].values(): raise ValueError('service image is not in source manifest')
            if image not in image_cache:
                prefix = ['docker', '--host', ownership.DockerOwnershipDriver.HOST, 'image', 'inspect', '--format']
                image_id = query([*prefix, '{{json .Id}}', '--', image])
                volumes = query([*prefix, '{{json .Config.Volumes}}', '--', image])
                if not isinstance(image_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
                    raise ValueError('local image identity is invalid')
                if volumes is not None and not isinstance(volumes, dict): raise ValueError('invalid image volumes')
                image_cache[image] = image_id, volumes or {}
            mounts = []
            for mount in service.get('volumes', []):
                if not isinstance(mount, dict): raise ValueError('invalid service mount')
                target, read_only = mount.get('target'), mount.get('read_only', False)
                if (not isinstance(target, str) or not target.startswith('/') or '..' in Path(target).parts
                        or type(read_only) is not bool): raise ValueError('invalid mount target or access')
                value = {'type': mount.get('type'), 'target': target, 'readOnly': read_only}
                if value['type'] == 'volume':
                    source = mount['source']
                    volume_name = config.get('volumes', {}).get(source, {}).get('name', ownership.PROJECT + '_' + source)
                    if volume_name not in result['volumes']: raise ValueError('undeclared volume')
                    value['name'] = volume_name
                elif value['type'] == 'bind':
                    source = Path(mount['source'])
                    if not source.is_absolute() or '..' in source.parts or not any(
                            parent == source or parent in source.parents for parent in (release, manager.target / 'shared')):
                        raise ValueError('host mount is outside managed source paths')
                    value['source'] = str(source)
                else: raise ValueError('unsupported mount kind')
                mounts.append(value)
            for secret in service.get('secrets', []):
                if not isinstance(secret, dict): raise ValueError('invalid source secret reference')
                definition = config.get('secrets', {}).get(secret['source'], {})
                source = Path(definition.get('file', '/outside'))
                if definition.get('external') or not source.is_absolute() or manager.target / 'shared' not in source.parents:
                    raise ValueError('secret bind is not a managed file reference')
                target = secret.get('target', secret['source'])
                if not target.startswith('/'): target = '/run/secrets/' + target
                if '..' in Path(target).parts: raise ValueError('invalid secret mount target')
                mounts.append({'type': 'bind', 'target': target, 'readOnly': True, 'source': str(source)})
            for declared in image_cache[image][1]:
                if not any(declared == mount['target'] or declared.startswith(mount['target'].rstrip('/') + '/') for mount in mounts):
                    raise ValueError('image would create an unmanaged implicit volume')
            result['services'][name] = {'imageIds': [image_cache[image][0]], 'workingDirs': [str(release)], 'mounts': mounts}
        return {'schemaVersion': 1, 'version': version, 'manifestSha256': manifest.sha256,
            'composeSha256': _sha(release / 'compose.yml'), 'environmentSha256': _sha(release / '.env'),
            'contract': result, 'claimImage': manifest.raw['images']['ADMIN_IMAGE']}
    except ownership.OwnershipError:
        raise
    except Exception as exc:
        raise ownership.OwnershipError('verified source topology is unsupported or unavailable') from exc


def overlay_for(contract, installation):
    if not ownership.canonical_uuid(installation):
        raise ownership.OwnershipError('overlay installation identity is invalid')
    label = {'io.survey.installation': installation}
    return {'services': {name: {'labels': label} for name in contract['services']},
        'networks': {value['logical']: {'labels': label} for value in contract['networks'].values()},
        'volumes': {value['logical']: {'labels': label} for value in contract['volumes'].values()}}


class RuntimeController:
    def __init__(self, manager, data, *, _driver_factory=None, _api=None, fresh=False):
        self.manager, self.data = manager, deepcopy(data)
        self.store = ownership.PrivateStore(manager.target)
        fd = manager._runtime_lock_fd
        self.probe = CohortProbe(manager.target, fd,
            lambda: manager._mutation_depth > 0 and manager._runtime_lock_fd == fd,
            api=_api, fresh=fresh)
        factory = _driver_factory or APIOwnershipDriver
        self.driver = factory(manager.runner, data['contract'], self.probe)
        self.protocol = ownership.OwnershipProtocol(manager.target, self.driver, self.store, data['claimImage'])
        self.next_data = deepcopy(data)

    @staticmethod
    def _load(manager, version):
        value = ownership.PrivateStore(manager.target, name='runtime-contract.json').load()
        keys = {'schemaVersion', 'version', 'manifestSha256', 'composeSha256', 'environmentSha256',
                'contract', 'claimImage', 'installationId'}
        owner = ownership.PrivateStore(manager.target).load()
        if (not isinstance(value, dict) or set(value) != keys or type(value['schemaVersion']) is not int
                or value['schemaVersion'] != 1 or value['version'] != version or not isinstance(owner, dict)
                or value['installationId'] != owner.get('installationId') or value['claimImage'] != owner.get('image')):
            raise ownership.OwnershipError('owned source contract is absent or mismatched; legacy adoption is prohibited')
        release = manager._release_dir(version)
        manager._verify_staged_release(release, version)
        if (manager._release_manifest(version).sha256 != value['manifestSha256']
                or _sha(release / 'compose.yml') != value['composeSha256'] or _sha(release / '.env') != value['environmentSha256']):
            raise ownership.OwnershipError('owned source configuration changed')
        overlay = ownership.PrivateStore(manager.target, name='runtime-owner.compose.json').load()
        if overlay != overlay_for(value['contract'], owner['installationId']):
            raise ownership.OwnershipError('owned Compose overlay changed')
        return value

    @classmethod
    def open(cls, manager, version, **kwargs):
        return cls(manager, cls._load(manager, version), **kwargs)

    @classmethod
    def prepare(cls, manager, version, **kwargs):
        owner = ownership.PrivateStore(manager.target).load()
        if owner is None:
            if os.path.lexists(manager.state_path) or os.path.lexists(manager.current_path):
                raise ownership.OwnershipError('legacy installation cannot be adopted into runtime ownership')
            data = source_contract(manager, version)
            runtime = cls(manager, data, fresh=True, **kwargs)
            runtime.protocol.acquire()
            runtime._save_source(data)
            return runtime
        previous = ownership.PrivateStore(manager.target, name='runtime-contract.json').load()
        if not isinstance(previous, dict) or not isinstance(previous.get('version'), str):
            raise ownership.OwnershipError('retained source contract is absent')
        runtime = cls.open(manager, previous['version'], **kwargs)
        if owner.get('phase') == 'removed':
            runtime.protocol.acquire()
        elif owner.get('phase') != 'retained-data':
            raise ownership.OwnershipError('existing active or pending installation cannot be recreated')
        runtime.next_data = source_contract(manager, version)
        runtime.next_data['claimImage'] = runtime.data['claimImage']
        return runtime

    def _save_source(self, data):
        owner = self.store.load()
        data = dict(data, installationId=owner['installationId'])
        ownership.PrivateStore(self.manager.target, name='runtime-contract.json').save(data)
        ownership.PrivateStore(self.manager.target, name='runtime-owner.compose.json').save(
            overlay_for(data['contract'], owner['installationId']))
        self.data = data
        self.driver.contract = deepcopy(data['contract'])

    def install(self, effect):
        def bound_effect():
            self._save_source(self.next_data)
            return effect()
        return self.protocol.mutate('install', bound_effect)

    def preflight_cleanup(self, purge):
        return self.protocol.preflight_cleanup(purge)

    def cleanup(self, purge, host_purge=None):
        return self.protocol.cleanup(purge, host_purge)
