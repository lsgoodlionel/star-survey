"""Serialization/guard regressions only; no Docker or child helper execution."""
import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import types
import unittest
from unittest import mock

SOURCE = Path(__file__).parent / 'integration/helper_parent_death_probe.py'


class ParentProbeLauncherTest(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SOURCE.read_text())
        tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        self.module = types.ModuleType('probe_launcher_regression')
        self.module.__file__ = str(SOURCE)
        exec(compile(tree, str(SOURCE), 'exec'), self.module.__dict__)
        self.calls, self.stdin, self.namespace = [], None, None
        self.include_digest = True
        self.digest_override = None
        self.inspect_override = {}

    def run_fixture(self, image):
        def run(command, **kwargs):
            self.calls.append(command)
            arguments = command[3:]
            output = ''
            if arguments[:2] == ['image', 'inspect']:
                output = 'null'
            elif arguments[:2] == ['container', 'create']:
                self.namespace = arguments[arguments.index('--name') + 1]
                output = 'c' * 64
            elif arguments[:2] == ['container', 'inspect']:
                item = {'id': 'c' * 64, 'name': '/' + self.namespace, 'image': image,
                    'fixture': self.namespace, 'running': False, 'pidMode': '', 'readOnly': True,
                    'network': 'none', 'privileged': False, 'binds': None, 'user': '65534:65534',
                    'capDrop': ['ALL'], 'securityOpt': ['no-new-privileges:true'],
                    'tmpfs': {'/tmp': 'rw,nosuid,nodev,noexec,size=16m'}, 'ports': None,
                    'pidsLimit': 32, 'memory': 134217728, 'nanoCpus': 1000000000,
                    'capAdd': None, 'devices': [], 'deviceRequests': None}
                item.update(self.inspect_override)
                output = json.dumps(item)
            elif arguments[:2] == ['container', 'start']:
                self.stdin = kwargs['input']
                compile(self.stdin, '<probe-stdin>', 'exec')
                evidence = {'schemaVersion': 1, 'scope': 'unprivileged-parent-death-feasibility-only',
                    'protectedChainEnded': True, 'opensslExecObserved': True, 'earlyParentRefused': True,
                    'unwrappedChildEscapeObserved': True, 'cleanup': 'verified-processes-ended'}
                if self.include_digest:
                    evidence['sourceSha256'] = self.digest_override or hashlib.sha256(SOURCE.read_bytes()).hexdigest()
                output = json.dumps(evidence)
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr='')
        with mock.patch.object(self.module.subprocess, 'run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            return self.module.docker_fixture(image)

    def test_stdin_contains_real_statement_separators(self):
        result = self.run_fixture('python@sha256:' + 'a' * 64)
        self.assertIsNotNone(self.stdin)
        try:
            compile(self.stdin, '<probe-stdin>', 'exec')
        except SyntaxError:
            self.fail('fixture stdin contains invalid statement separators')
        self.assertEqual(0, result)

    def test_repository_names_containing_s_are_valid(self):
        self.assertEqual(0, self.run_fixture('registry/survey/python@sha256:' + 'a' * 64))

    def test_whitespace_image_is_rejected_before_any_docker_command(self):
        with self.assertRaises(SystemExit):
            self.run_fixture('python @sha256:' + 'a' * 64)
        self.assertEqual([], self.calls)

    def test_missing_or_wrong_source_digest_is_not_verified(self):
        for supplied, override in ((False, None), (True, 'b' * 64)):
            with self.subTest(supplied=supplied):
                self.include_digest, self.digest_override = supplied, override
                self.assertEqual(1, self.run_fixture('python@sha256:' + 'a' * 64))

    def test_unverified_isolation_attributes_prevent_start(self):
        for key, value in (('capDrop', []), ('capAdd', ['SYS_ADMIN']), ('securityOpt', []),
                ('tmpfs', {}), ('ports', {'80/tcp': []}), ('pidsLimit', -1), ('memory', 0),
                ('nanoCpus', 0), ('devices', [{'PathOnHost': '/dev/sda'}]), ('deviceRequests', [{}])):
            with self.subTest(key=key):
                self.calls.clear()
                self.inspect_override = {key: value}
                self.assertEqual(1, self.run_fixture('python@sha256:' + 'a' * 64))
                self.assertFalse(any(command[3:5] == ['container', 'start'] for command in self.calls))


if __name__ == '__main__':
    unittest.main()
