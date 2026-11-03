import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'production_smoke.py'


class ProductionSmokeTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('production_smoke', SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_refuses_local_self_hosted_or_arm_execution(self):
        for env in ({}, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'self-hosted', 'RUNNER_ARCH': 'X64'},
                    {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_ARCH': 'ARM64'}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.module.require_disposable_runner(env)

    def test_accepts_only_explicit_hosted_amd64_runner(self):
        self.module.require_disposable_runner({'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted', 'RUNNER_ARCH': 'X64'})

    def test_cleanup_failure_cannot_be_reported_as_pass(self):
        self.assertEqual('failed', self.module.overall_status(True, False))
        self.assertEqual('failed', self.module.overall_status(False, True))
        self.assertEqual('passed', self.module.overall_status(True, True))

    def test_compose_cleanup_failure_still_attempts_registry_cleanup(self):
        calls = []
        def compose():
            calls.append('compose')
            raise RuntimeError('fixture failure')
        def registry():
            calls.append('registry')
        self.assertFalse(self.module.attempt_cleanups([compose, registry]))
        self.assertEqual(['compose', 'registry'], calls)

    def test_created_or_exited_container_blocks_collision_and_cleanup(self):
        from types import SimpleNamespace
        def fake(command):
            names = 'survey-production-engine-1\n' if command[1] == 'container' and '--all' in command else ''
            return SimpleNamespace(stdout=names)
        for prefixes in (('survey-production', 'survey-smoke-registry'), ('survey-production',)):
            with self.subTest(prefixes=prefixes), self.assertRaises(ValueError):
                self.module.assert_resources_absent(prefixes, runner=fake)

    def test_engine_subprobes_do_not_copy_cli_output_or_http_body(self):
        import subprocess
        from types import SimpleNamespace
        def fake(command):
            if command[-1] == 'status':
                raise subprocess.CalledProcessError(1, command, output='secret=fixture-private')
            return SimpleNamespace(stdout='{"httpStatus":503,"requestCompleted":true,"bodyHasError":false,"edgeResolved":false,"gatewayResolved":true}')
        result = self.module.engine_subprobes('fixture', runner=fake)
        self.assertEqual(503, result['httpStatus'])
        self.assertEqual(1, result['initStatusExitCode'])
        self.assertNotIn('fixture-private', str(result))
