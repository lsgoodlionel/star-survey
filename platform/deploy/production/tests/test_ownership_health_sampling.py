"""Thin-entry contracts only: no Docker, DB or repeated health-model tests."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[4]
ENTRY = ROOT / 'platform/deploy/production/tests/integration/ownership_health_sampling.py'


class OwnershipSamplingEntryTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('ownership_sampling_entry', ENTRY)
        self.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.entry)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.report = Path(self.tmp.name) / 'sampling.json'
        self.context = {'repository': 'lsgoodlionel/star-survey', 'runId': '123', 'attempt': 1,
                        'job': 'ownership-health-sample', 'runner': 'GitHub Actions 1',
                        'executionSha': 'a' * 40}
        self.native = types.SimpleNamespace(
            REQUIRED={'backup-purge': ('backupVerified', 'ownedUpgradeCompleted', 'allHelpersEnded'),
                      'doctor-refusal': ('doctorBlockedWithoutLeak',)},
            load_module=mock.Mock(return_value=types.SimpleNamespace(
                OWNED_RUNTIME_FILES=(), LONG_RUNNING_SERVICES={'edge'})),
            run_case=mock.Mock(), fixture_cleanup=mock.Mock(),
            bounded_health_failure=mock.Mock(return_value={
                'services': {'edge': {'state': 'running', 'health': 'starting'}},
                'engineInit': {'state': 'running', 'exitCode': 0}, 'elapsedMs': 25}))

    def invoke(self, case, *, preflight=None, inventories=None):
        self.native.run_case.return_value = case
        before = copy.deepcopy(self.native.REQUIRED)
        with mock.patch.dict(os.environ, {'RUNNER_TEMP': self.tmp.name}), \
                mock.patch.object(self.entry, 'execution_context', return_value=self.context), \
                mock.patch.object(self.entry, 'verify_source'), \
                mock.patch.object(self.entry, 'load_component', return_value=self.native), \
                mock.patch.object(self.entry, 'image_preflight', side_effect=preflight,
                                  return_value={'fixed': {'id': 'sha256:' + '1' * 64}}), \
                mock.patch.object(self.entry, 'inventory', side_effect=inventories or
                                  [{'source.py': '1' * 64}, {'source.py': '1' * 64}]):
            code = self.entry.run(self.report, 'a' * 40)
        self.assertEqual(before, self.native.REQUIRED)
        self.native.fixture_cleanup.assert_not_called()
        return code, json.loads(self.report.read_text())

    def case(self, passed=False):
        return {'scenario': 'backup-purge', 'status': 'passed' if passed else 'failed',
                'fixtureCleanup': 'verified' if passed else 'preserved-failed-or-unknown',
                'backupVerified': True, 'ownedUpgradeCompleted': passed, 'allHelpersEnded': passed,
                'retainedNetworkCount': 1, 'fixtureCleanupScope': 'docker-objects-only',
                'syntheticManifestOnly': True, 'hostAndTlsAreSynthetic': True,
                'businessServicesCovered': False, 'defaultInstallCovered': False,
                'upgradeBusinessDataVerified': False, 'restorationEligible': False, 'releaseEligible': False,
                'failureStage': 'owned-upgrade', 'privateStateRetained': True,
                'initialHealth': {'services': {'edge': {'state': 'running', 'health': 'starting'}},
                                  'engineInit': {'state': 'running', 'exitCode': 0}, 'elapsedMs': 25},
                'raw': 'SECRET_MUST_NOT_BE_EXPORTED'}

    def test_preflight_failure_never_starts_a_case(self):
        code, report = self.invoke(self.case(), preflight=self.entry.SamplingError('image-identity'))
        self.assertEqual(1, code)
        self.native.run_case.assert_not_called()
        self.assertEqual('unknown', report['samplingStatus'])
        self.assertEqual('image-identity', report['failureCategory'])

    def test_failed_case_is_called_once_and_unknown_is_not_cleaned_or_replayed(self):
        code, report = self.invoke(self.case())
        self.assertEqual(1, code)
        self.assertEqual('failure-observed', report['samplingStatus'])
        self.assertEqual('preserved-failed-or-unknown', report['case']['fixtureCleanup'])
        self.assertEqual(1, self.native.run_case.call_count)
        self.assertEqual('backup-purge', self.native.run_case.call_args.args[1])
        self.assertNotIn('SECRET_MUST_NOT_BE_EXPORTED', json.dumps(report))
        self.assertEqual(25, report['case']['initialHealth']['elapsedMs'])
        self.assertFalse(report['candidateQualified'])
        self.assertFalse(report['entireSuitePassed'])

    def test_success_is_a_single_sample_not_suite_or_release_acceptance(self):
        code, report = self.invoke(self.case(passed=True))
        self.assertEqual(0, code)
        self.assertEqual('completed-no-failure-reproduced', report['samplingStatus'])
        self.assertEqual(1, self.native.run_case.call_count)
        for key in ('candidateQualified', 'restorationEligible', 'releaseEligible', 'entireSuitePassed'):
            self.assertIs(report[key], False)

    def test_source_drift_after_one_case_stays_unknown(self):
        code, report = self.invoke(self.case(passed=True), inventories=[{'x': '1' * 64}, {'x': '2' * 64}])
        self.assertEqual(1, code)
        self.assertEqual('source-drift', report['failureCategory'])
        self.assertEqual('unknown', report['samplingStatus'])
        self.assertEqual(1, self.native.run_case.call_count)

    def test_invalid_health_shape_cannot_be_uploaded(self):
        case = self.case()
        case['initialHealth']['raw'] = 'SECRET_MUST_NOT_BE_EXPORTED'
        code, report = self.invoke(case)
        self.assertEqual(1, code)
        self.assertNotIn('SECRET_MUST_NOT_BE_EXPORTED', json.dumps(report))
        self.assertEqual('case-shape', report['failureCategory'])

    def test_real_component_boundary_preserves_a_valid_health_failure_without_docker(self):
        real = self.entry.load_component()
        self.assertFalse(hasattr(real, 'LONG_RUNNING_SERVICES'))
        controller = real.load_module()
        case = self.case()
        case['initialHealth']['services'] = {
            name: {'state': 'running', 'health': 'healthy'} for name in controller.LONG_RUNNING_SERVICES}
        expected = copy.deepcopy(case['initialHealth'])
        events = []
        load = real.load_module
        def metadata():
            events.append('metadata')
            return load()
        def sample(*args):
            events.append('case')
            return case
        with mock.patch.object(real, 'load_module', side_effect=metadata), \
                mock.patch.object(real, 'run_case', side_effect=sample), \
                mock.patch.object(real, 'fixture_cleanup') as cleanup, \
                mock.patch.object(real, 'LocalDockerAPI', side_effect=AssertionError('no Docker')) as docker:
            self.native = real
            code, report = self.invoke(case)
        self.assertEqual(1, code)
        self.assertEqual('failure-observed', report['samplingStatus'])
        self.assertEqual(expected, report['case']['initialHealth'])
        self.assertEqual(['metadata', 'case'], events)
        cleanup.assert_not_called()
        docker.assert_not_called()

    def test_real_health_guard_distinguishes_missing_exit_observation_from_explicit_one(self):
        real = self.entry.load_component()
        controller = real.load_module()
        for missing in (True, False):
            with self.subTest(missing=missing):
                rows = [{'Service': name, 'State': 'running', 'Health': 'healthy'}
                        for name in controller.LONG_RUNNING_SERVICES]
                init = {'Service': 'engine-init', 'State': 'exited', 'Health': ''}
                if not missing:
                    init['ExitCode'] = 1
                rows.append(init)
                def response(command, *args, **kwargs):
                    if '--services' in command:
                        text = '\n'.join(sorted(controller.LONG_RUNNING_SERVICES | controller.ONE_SHOT_SERVICES))
                    elif 'ps' in command:
                        text = json.dumps(rows)
                    else:
                        raise AssertionError('unexpected runtime query')
                    return self.entry.subprocess.CompletedProcess(command, 0, stdout=text, stderr='')
                manager = object.__new__(controller.SurveyManager)
                manager._run = mock.Mock(side_effect=response)
                with mock.patch.object(controller.time, 'sleep') as sleep:
                    with self.assertRaises(controller.RuntimeHealthError) as caught:
                        manager._wait_healthy(['docker', 'compose'], 'bounded fixture')
                sleep.assert_not_called()
                self.assertIn('engine-init-ok=False', str(caught.exception))
                value = real.bounded_health_failure(caught.exception, controller.LONG_RUNNING_SERVICES)
                self.assertEqual(None if missing else 1, value['engineInit']['exitCode'])
                self.assertEqual(2, manager._run.call_count)

    def test_unexpected_error_text_is_never_an_upload_field(self):
        code, report = self.invoke(self.case(), preflight=self.entry.SamplingError('SECRET_MUST_NOT_BE_EXPORTED'))
        self.assertEqual(1, code)
        self.assertNotIn('SECRET_MUST_NOT_BE_EXPORTED', json.dumps(report))
        self.assertEqual('driver-failure', report['failureCategory'])

    def test_report_write_failure_does_not_leave_a_success_report_or_replay(self):
        original = self.entry.write_report
        calls = []
        def write(path, value):
            calls.append(value['samplingStatus'])
            if len(calls) == 3:
                raise OSError('synthetic evidence write failure')
            return original(path, value)
        with mock.patch.object(self.entry, 'write_report', side_effect=write):
            code, report = self.invoke(self.case(passed=True))
        self.assertEqual(1, code)
        self.assertEqual('unknown', report['samplingStatus'])
        self.assertIs(report['cleanupVerified'], False)
        self.assertEqual(1, self.native.run_case.call_count)

    def test_wrong_image_architecture_refuses_before_other_pulls(self):
        api = types.SimpleNamespace(identity=mock.Mock())
        native = types.SimpleNamespace(LocalDockerAPI=mock.Mock(return_value=api),
            image_metadata=mock.Mock(return_value={'id': next(iter(self.entry.IMAGES.values())),
                                                   'architecture': 'arm64', 'os': 'linux'}))
        with mock.patch.object(self.entry.subprocess, 'run') as pull:
            with self.assertRaises(self.entry.SamplingError):
                self.entry.image_preflight(native)
        api.identity.assert_called_once()
        self.assertEqual(1, pull.call_count)

    def test_selected_case_keeps_original_network_and_qualification_predicates(self):
        for key, value in [('retainedNetworkCount', 0), ('syntheticManifestOnly', False),
                           ('hostAndTlsAreSynthetic', False), ('businessServicesCovered', True),
                           ('defaultInstallCovered', True), ('upgradeBusinessDataVerified', True),
                           ('restorationEligible', True), ('releaseEligible', True)]:
            with self.subTest(key=key):
                case = self.case(passed=True)
                case[key] = value
                code, report = self.invoke(case)
                self.assertEqual(1, code)
                self.assertEqual('case-shape', report['failureCategory'])

    def test_after_case_inventory_does_not_import_the_controller_again(self):
        production = Path(self.tmp.name) / 'production'
        for relative in ('tests/integration/owned_maintenance_native.py',
                         'tests/integration/runtime_controller_native.py', 'tests/test_surveyctl.py'):
            path = production / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture source')
        native = types.SimpleNamespace(load_module=mock.Mock(return_value=types.SimpleNamespace(OWNED_RUNTIME_FILES=())))
        with mock.patch.object(self.entry, 'PRODUCTION', production):
            self.assertEqual(self.entry.inventory(native), self.entry.inventory(native))
        self.assertEqual(1, native.load_module.call_count)

    def test_source_preflight_refuses_an_unrelated_runtime_change(self):
        def git(*args):
            if args == ('rev-parse', 'HEAD'):
                return 'a' * 40
            if args == ('diff', '--name-only', self.entry.BASE, 'HEAD'):
                return '\n'.join(sorted(self.entry.CHANGED_FILES | {'platform/deploy/production/restore.py'}))
            return ''
        with mock.patch.object(self.entry, 'git', side_effect=git):
            with self.assertRaises(self.entry.SamplingError):
                self.entry.verify_source('a' * 40)

    def test_non_github_execution_refuses_before_loading_fixture(self):
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'false', 'RUNNER_TEMP': self.tmp.name}, clear=True), \
                mock.patch.object(self.entry, 'load_component') as load:
            code = self.entry.run(self.report, 'a' * 40)
        self.assertEqual(1, code)
        load.assert_not_called()
        self.assertEqual('context', json.loads(self.report.read_text())['failureCategory'])

    def test_context_binds_exact_ref_sha_repository_and_first_attempt(self):
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
               'GITHUB_REPOSITORY': 'lsgoodlionel/star-survey', 'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64',
               'GITHUB_REF': 'refs/heads/' + self.entry.BRANCH, 'GITHUB_JOB': 'ownership-health-sample',
               'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_RUN_ID': '123', 'RUNNER_NAME': 'GitHub Actions 1',
               'GITHUB_SHA': 'a' * 40}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.entry.sys, 'platform', 'linux'), \
                mock.patch.object(self.entry.os, 'uname', return_value=types.SimpleNamespace(machine='x86_64')), \
                mock.patch.object(Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="24.04"\n'):
            self.assertEqual('a' * 40, self.entry.execution_context('a' * 40)['executionSha'])
            for key, value in [('GITHUB_SHA', 'b' * 40), ('GITHUB_REF', 'refs/heads/main'),
                               ('GITHUB_REPOSITORY', 'foreign/repository'), ('GITHUB_RUN_ATTEMPT', '2')]:
                with self.subTest(key=key), mock.patch.dict(os.environ, {key: value}):
                    with self.assertRaises(self.entry.SamplingError):
                        self.entry.execution_context('a' * 40)

    def test_report_cannot_write_outside_runner_temporary_directory(self):
        with tempfile.TemporaryDirectory() as outside, \
                mock.patch.dict(os.environ, {'RUNNER_TEMP': self.tmp.name}):
            path = Path(outside) / 'not-ours.json'
            with self.assertRaises(self.entry.SamplingError):
                self.entry.write_report(path, {'samplingStatus': 'unknown'})
            self.assertFalse(path.exists())

    def test_workflow_is_manual_single_job_and_has_no_write_or_release_permissions(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/production-smoke.yml').read_text())
        events = workflow.get('on', workflow.get(True))
        self.assertEqual({'workflow_dispatch'}, set(events))
        self.assertEqual({'contents': 'read'}, workflow['permissions'])
        self.assertEqual({'ownership-health-sample'}, set(workflow['jobs']))
        job = workflow['jobs']['ownership-health-sample']
        self.assertEqual('ubuntu-24.04', job['runs-on'])
        self.assertEqual(10, job['timeout-minutes'])
        runs = '\n'.join(step.get('run', '') for step in job['steps'])
        self.assertEqual(1, runs.count('integration/ownership_health_sampling.py'))
        self.assertIn('PyYAML==6.0.3', runs)
        self.assertNotIn('integration/owned_maintenance_native.py', runs)
        self.assertNotIn('gh release', runs)
        self.assertNotIn('git push', runs)

    def test_runner_temporary_path_is_initialized_after_runner_assignment(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/production-smoke.yml').read_text())
        job = workflow['jobs']['ownership-health-sample']
        self.assertNotIn('runner.', json.dumps(job.get('env', {})))
        initialize = next(step['run'] for step in job['steps'] if step.get('name') == 'Initialize bounded unknown evidence')
        self.assertIn('export PYTHONPYCACHEPREFIX="$RUNNER_TEMP/ownership-sampling-isolated-pycache"', initialize)
        self.assertIn('>> "$GITHUB_ENV"', initialize)


if __name__ == '__main__':
    unittest.main()
