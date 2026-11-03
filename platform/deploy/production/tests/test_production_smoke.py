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
