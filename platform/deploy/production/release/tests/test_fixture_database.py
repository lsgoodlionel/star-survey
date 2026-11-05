import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'fixture_database.py'
spec = importlib.util.spec_from_file_location('fixture_database', PATH)
module = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(PATH.parent))
try:
    spec.loader.exec_module(module)
finally:
    sys.path.pop(0)


class FixtureDatabaseTest(unittest.TestCase):
    @staticmethod
    def complete_container(container, release):
        target = release.rsplit('/releases/', 1)[0]
        config = container['Config']
        config.update({'Env': ['PGDATA=/var/lib/postgresql/data', 'POSTGRES_USER=postgres',
                               'POSTGRES_DB=postgres', 'POSTGRES_PASSWORD_FILE=/run/secrets/platform_db_superuser_password',
                               'PLATFORM_DB_OWNER_PASSWORD_FILE=/run/secrets/platform_db_owner_password',
                               'PLATFORM_DB_APP_PASSWORD_FILE=/run/secrets/platform_db_app_password'],
                       'Entrypoint': ['docker-entrypoint.sh'], 'Cmd': ['postgres']})
        container['Mounts'].append({'Type': 'bind', 'Source': release + '/init/init-platform-db.sh',
                                    'Destination': '/docker-entrypoint-initdb.d/10-platform-roles.sh', 'RW': False})
        for name in ('platform_db_superuser_password', 'platform_db_owner_password', 'platform_db_app_password'):
            container['Mounts'].append({'Type': 'bind', 'Source': target + '/shared/runtime-secrets/' + name,
                                        'Destination': '/run/secrets/' + name, 'RW': False})
    def test_inspection_binds_running_database_to_the_verified_candidate_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            release = work / 'primary/releases/0.3.19-rc.1'
            release.mkdir(parents=True)
            image = 'postgres@sha256:' + 'a' * 64
            payload = json.dumps({'version': '0.3.19-rc.1', 'images': {'POSTGRES_IMAGE': image}}).encode()
            container = {'Id': 'b' * 64, 'State': {'Running': True}, 'Config': {'Image': image, 'Labels': {
                'com.docker.compose.project': 'survey-production', 'com.docker.compose.service': 'platform-db',
                'com.docker.compose.project.working_dir': str(release),
                'com.docker.compose.project.config_files': str(release / 'compose.yml'),
            }}, 'Mounts': [{'Type': 'volume', 'Name': 'survey-production_platform-db-data',
                           'Destination': '/var/lib/postgresql/data'}]}
            self.complete_container(container, str(release))
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps([container]), stderr='')
            with mock.patch.object(module, 'verify_fixture_owner'), \
                    mock.patch.object(module, 'verify_fixture_volume_snapshot'), \
                    mock.patch.object(module.os, 'geteuid', return_value=0), \
                    mock.patch.object(module, '_read_regular', side_effect=[b'0.3.19-rc.1\n', payload]), \
                    mock.patch.dict(os.environ, {'CANDIDATE_MANIFEST_SHA256': hashlib.sha256(payload).hexdigest()}), \
                    mock.patch.object(module.subprocess, 'run', return_value=result) as run:
                self.assertEqual((release, 'b' * 64), module.inspect_fixture_database(work, work / 'owner.json'))
                self.assertEqual(['docker', '--host', 'unix:///var/run/docker.sock', 'inspect', '--type',
                                  'container', 'survey-production-platform-db-1'], run.call_args.args[0])

    def test_manifest_version_mismatch_fails_before_container_inspection(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / 'primary/releases/0.3.19-rc.1').mkdir(parents=True)
            payload = json.dumps({'version': '0.3.18', 'images': {'POSTGRES_IMAGE': 'unused'}}).encode()
            with mock.patch.object(module, 'verify_fixture_owner'), \
                    mock.patch.object(module, 'verify_fixture_volume_snapshot'), \
                    mock.patch.object(module.os, 'geteuid', return_value=0), \
                    mock.patch.object(module, '_read_regular', side_effect=[b'0.3.19-rc.1\n', payload]), \
                    mock.patch.dict(os.environ, {'CANDIDATE_MANIFEST_SHA256': hashlib.sha256(payload).hexdigest()}), \
                    mock.patch.object(module.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    module.inspect_fixture_database(work, work / 'owner.json')
                run.assert_not_called()

    def test_database_inspection_is_never_attempted_if_owner_or_volumes_fail(self):
        with mock.patch.object(module, 'verify_fixture_owner', side_effect=ValueError('wrong owner')), \
                mock.patch.object(module.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                module.inspect_fixture_database(Path('/tmp/work'), Path('/tmp/work/owner.json'))
            run.assert_not_called()
        with mock.patch.object(module, 'verify_fixture_owner'), \
                mock.patch.object(module.os, 'geteuid', return_value=501), \
                mock.patch.object(module, 'verify_fixture_volume_snapshot') as volumes, \
                mock.patch.object(module.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                module.inspect_fixture_database(Path('/tmp/work'), Path('/tmp/work/owner.json'))
            volumes.assert_not_called()
            run.assert_not_called()
        with mock.patch.object(module, 'verify_fixture_owner'), \
                mock.patch.object(module.os, 'geteuid', return_value=0), \
                mock.patch.object(module, 'verify_fixture_volume_snapshot', side_effect=ValueError('wrong volumes')), \
                mock.patch.object(module.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                module.inspect_fixture_database(Path('/tmp/work'), Path('/tmp/work/owner.json'))
            run.assert_not_called()

    def test_runtime_secret_paths_are_bound_to_primary_target(self):
        release = '/tmp/example/primary/releases/0.3.19-rc.1'
        image = 'postgres@sha256:' + 'a' * 64
        container = {'Id': 'b' * 64, 'State': {'Running': True}, 'Config': {'Image': image, 'Labels': {
            'com.docker.compose.project': 'survey-production', 'com.docker.compose.service': 'platform-db',
            'com.docker.compose.project.working_dir': release,
            'com.docker.compose.project.config_files': release + '/compose.yml',
        }}, 'Mounts': [{'Type': 'volume', 'Name': 'survey-production_platform-db-data',
                       'Destination': '/var/lib/postgresql/data'}]}
        self.complete_container(container, release)
        self.assertEqual('/tmp/example/primary/shared/runtime-secrets/platform_db_superuser_password',
                         container['Mounts'][2]['Source'])
        module.validate_database_container(container, release, image)
        container['Mounts'][2]['Source'] = '/tmp/example/shared/runtime-secrets/platform_db_superuser_password'
        with self.assertRaises(ValueError):
            module.validate_database_container(container, release, image)

    def test_revalidation_fails_when_candidate_or_container_changes(self):
        release = Path('/tmp/target/releases/0.3.19-rc.1')
        container_id = 'a' * 64
        with mock.patch.object(module, 'inspect_fixture_database', return_value=(release, container_id)) as inspect:
            self.assertEqual(container_id, module.revalidate_fixture_database(Path('/tmp/work'), Path('/tmp/marker'), release, container_id))
            with self.assertRaises(ValueError):
                module.revalidate_fixture_database(Path('/tmp/work'), Path('/tmp/marker'), release, 'b' * 64)
            with self.assertRaises(ValueError):
                module.revalidate_fixture_database(Path('/tmp/work'), Path('/tmp/marker'), Path('/tmp/other'), container_id)
            with self.assertRaises(ValueError):
                module.revalidate_fixture_database(Path('/tmp/work'), Path('/tmp/marker'), release, 'container-name')
            self.assertEqual(3, inspect.call_count)

    def test_container_must_be_running_and_bound_to_exact_candidate_release(self):
        release = '/tmp/survey-native-acceptance.test/primary/releases/0.3.19-rc.1'
        image = 'postgres@sha256:' + 'a' * 64
        expected = {'Id': 'b' * 64, 'State': {'Running': True}, 'Config': {'Image': image, 'Labels': {
            'com.docker.compose.project': 'survey-production',
            'com.docker.compose.service': 'platform-db',
            'com.docker.compose.project.working_dir': release,
            'com.docker.compose.project.config_files': release + '/compose.yml',
        }}, 'Mounts': [{'Type': 'volume', 'Name': 'survey-production_platform-db-data',
                       'Destination': '/var/lib/postgresql/data'}]}
        self.complete_container(expected, release)
        module.validate_database_container(expected, release, image)
        import copy
        for mutation in ('stopped', 'project', 'service', 'workdir', 'config', 'image', 'mount', 'extra', 'pgdata', 'duplicate_env', 'command', 'entrypoint', 'overlay', 'secret_source', 'pgoptions', 'missing_bind', 'writable_bind', 'container_id'):
            value = copy.deepcopy(expected)
            if mutation == 'stopped': value['State']['Running'] = False
            if mutation == 'project': value['Config']['Labels']['com.docker.compose.project'] = 'other'
            if mutation == 'service': value['Config']['Labels']['com.docker.compose.service'] = 'engine-db'
            if mutation == 'workdir': value['Config']['Labels']['com.docker.compose.project.working_dir'] = '/tmp/other'
            if mutation == 'config': value['Config']['Labels']['com.docker.compose.project.config_files'] = '/tmp/other.yml'
            if mutation == 'image': value['Config']['Image'] = 'postgres:latest'
            if mutation == 'mount': value['Mounts'][0]['Name'] = 'other'
            if mutation == 'extra': value['Mounts'].append(dict(value['Mounts'][0]))
            if mutation == 'pgdata': value['Config']['Env'][0] = 'PGDATA=/tmp/other'
            if mutation == 'duplicate_env': value['Config']['Env'].append('PGDATA=/tmp/other')
            if mutation == 'command': value['Config']['Cmd'] = ['postgres', '-D', '/tmp/other']
            if mutation == 'entrypoint': value['Config']['Entrypoint'] = ['/bin/sh']
            if mutation == 'overlay': value['Mounts'].append({'Type': 'bind', 'Destination': '/var/lib/postgresql/data/base'})
            if mutation == 'secret_source': value['Mounts'][2]['Source'] = '/tmp/other'
            if mutation == 'pgoptions': value['Config']['Env'].append('PGOPTIONS=-c data_directory=/tmp/other')
            if mutation == 'missing_bind': value['Mounts'].pop()
            if mutation == 'writable_bind': value['Mounts'][1]['RW'] = True
            if mutation == 'container_id': value['Id'] = 'survey-production-platform-db-1'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.validate_database_container(value, release, image)
