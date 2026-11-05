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
    def test_inspection_binds_running_database_to_the_verified_candidate_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            release = work / 'primary/releases/0.3.19-rc.1'
            release.mkdir(parents=True)
            image = 'postgres@sha256:' + 'a' * 64
            payload = json.dumps({'images': {'POSTGRES_IMAGE': image}}).encode()
            container = {'State': {'Running': True}, 'Config': {'Image': image, 'Labels': {
                'com.docker.compose.project': 'survey-production', 'com.docker.compose.service': 'platform-db',
                'com.docker.compose.project.working_dir': str(release),
                'com.docker.compose.project.config_files': str(release / 'compose.yml'),
            }}, 'Mounts': [{'Type': 'volume', 'Name': 'survey-production_platform-db-data',
                           'Destination': '/var/lib/postgresql/data'}]}
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps([container]), stderr='')
            with mock.patch.object(module, 'verify_fixture_owner'), \
                    mock.patch.object(module, 'verify_fixture_volume_snapshot'), \
                    mock.patch.object(module.os, 'geteuid', return_value=0), \
                    mock.patch.object(module, '_read_regular', side_effect=[b'0.3.19-rc.1\n', payload]), \
                    mock.patch.dict(os.environ, {'CANDIDATE_MANIFEST_SHA256': hashlib.sha256(payload).hexdigest()}), \
                    mock.patch.object(module.subprocess, 'run', return_value=result) as run:
                self.assertEqual(release, module.inspect_fixture_database(work, work / 'owner.json'))
                self.assertEqual(['docker', '--host', 'unix:///var/run/docker.sock', 'inspect', '--type',
                                  'container', 'survey-production-platform-db-1'], run.call_args.args[0])

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

    def test_container_must_be_running_and_bound_to_exact_candidate_release(self):
        release = '/tmp/survey-native-acceptance.test/primary/releases/0.3.19-rc.1'
        image = 'postgres@sha256:' + 'a' * 64
        expected = {'State': {'Running': True}, 'Config': {'Image': image, 'Labels': {
            'com.docker.compose.project': 'survey-production',
            'com.docker.compose.service': 'platform-db',
            'com.docker.compose.project.working_dir': release,
            'com.docker.compose.project.config_files': release + '/compose.yml',
        }}, 'Mounts': [{'Type': 'volume', 'Name': 'survey-production_platform-db-data',
                       'Destination': '/var/lib/postgresql/data'}]}
        module.validate_database_container(expected, release, image)
        import copy
        for mutation in ('stopped', 'project', 'service', 'workdir', 'config', 'image', 'mount', 'extra'):
            value = copy.deepcopy(expected)
            if mutation == 'stopped': value['State']['Running'] = False
            if mutation == 'project': value['Config']['Labels']['com.docker.compose.project'] = 'other'
            if mutation == 'service': value['Config']['Labels']['com.docker.compose.service'] = 'engine-db'
            if mutation == 'workdir': value['Config']['Labels']['com.docker.compose.project.working_dir'] = '/tmp/other'
            if mutation == 'config': value['Config']['Labels']['com.docker.compose.project.config_files'] = '/tmp/other.yml'
            if mutation == 'image': value['Config']['Image'] = 'postgres:latest'
            if mutation == 'mount': value['Mounts'][0]['Name'] = 'other'
            if mutation == 'extra': value['Mounts'].append(dict(value['Mounts'][0]))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.validate_database_container(value, release, image)
