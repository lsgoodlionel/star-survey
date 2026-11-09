import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[5]


class CIPlanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('ci_plan', ROOT / 'scripts/ci/plan.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_docs_and_baseline_are_the_only_lightweight_allowlists(self):
        plan = self.module.plan
        self.assertEqual(('docs', False), plan(['docs/agent/PROGRESS.md', 'README.en.md']))
        self.assertEqual(('policy', False), plan(['.github/release/baseline-policy.json', 'docs/agent/PROGRESS.md']))
        for path in ['platform/apps/admin-web/src/App.tsx', '.github/workflows/ci.yml',
                     'scripts/ci/plan.py', 'docs/run.sh', 'unknown.txt',
                     '.github/release/vulnerability-policy.json', 'platform/deploy/production/compose.yml']:
            with self.subTest(path=path):
                self.assertEqual(('full', True), plan([path]))

    def test_unknown_empty_invalid_and_incomplete_changes_fail_closed(self):
        for paths in [None, [], ['../README.md'], ['/README.md'], [None]]:
            self.assertEqual(('full', True), self.module.plan(paths))

    def test_main_and_manual_keep_full_quality(self):
        self.assertEqual(('full', False), self.module.plan(['README.md'], force_full=True))
        self.assertEqual(('full', True), self.module.plan(None, force_full=True))

    def test_rename_preserves_old_runtime_path(self):
        paths = self.module.paths_from_records([{'filename': 'docs/removed.md', 'previous_filename': 'platform/app.py'}])
        self.assertEqual(('full', True), self.module.plan(paths))

    def test_summary_rejects_failure_cancel_and_unexpected_skip(self):
        m = self.module
        for status in ['failure', 'cancelled', 'skipped', None]:
            with self.subTest(status=status):
                self.assertFalse(m.accept_results('full', True, 'success', status, 'success'))
        self.assertTrue(m.accept_results('full', True, 'success', 'success', 'success'))
        self.assertTrue(m.accept_results('policy', False, 'success', 'success', 'skipped'))
        self.assertTrue(m.accept_results('docs', False, 'success', 'skipped', 'skipped'))
        self.assertFalse(m.accept_results('docs', False, 'failure', 'skipped', 'skipped'))
        self.assertFalse(m.accept_results('invalid', False, 'success', 'skipped', 'skipped'))

    def test_relay_is_optional_but_requested_relay_must_succeed(self):
        m = self.module
        base = ('full', True, 'success', 'success', 'success')
        self.assertTrue(m.accept_results(*base, relay_requested=False, relay='skipped'))
        self.assertFalse(m.accept_results(*base, relay_requested=False, relay='success'))
        self.assertTrue(m.accept_results(*base, relay_requested=True, relay='success'))
        for status in ('failure', 'cancelled', 'skipped', None):
            with self.subTest(status=status):
                self.assertFalse(m.accept_results(*base, relay_requested=True, relay=status))

    def test_manual_relay_reuses_isolated_diagnostic_and_preserves_triggers(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        original = (ROOT / '.github/workflows/relay-devstack-diagnostic.yml').read_text()
        self.assertIn('  pull_request:\n  push:\n    branches: [main]\n', workflow)
        self.assertIn('      run_relay:\n        description: Run the isolated dual-database relay diagnostic\n'
                      '        type: boolean\n        default: false\n', workflow)
        actual = workflow.split('  relay:\n', 1)[1].split('\n  crosslayer:\n', 1)[0].strip()
        expected = original.split('  real-relay:\n', 1)[1].strip()
        guard = "if: ${{ github.event_name == 'workflow_dispatch' && inputs.run_relay == true }}"
        self.assertTrue(actual.startswith(guard + '\n'))
        self.assertEqual(expected, actual[len(guard):].strip())
        self.assertIn('needs: [changes, quality, images, relay, crosslayer]', workflow)

    def test_summary_command_fails_closed_for_requested_relay(self):
        needs = {'changes': {'result': 'success', 'outputs': {'profile': 'full', 'images': 'true'}},
                 'quality': {'result': 'success'}, 'images': {'result': 'success'},
                 'relay': {'result': 'success'}, 'crosslayer': {'result': 'skipped'}}
        for requested, status, expected in [('true', 'success', 0), ('true', 'failure', 1),
                                            ('true', 'skipped', 1), ('false', 'skipped', 0),
                                            ('false', 'failure', 1), ('invalid', 'success', 1)]:
            with self.subTest(requested=requested, status=status):
                needs['relay']['result'] = status
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/ci/plan.py'), 'summary'],
                                        env={**os.environ, 'CI_NEEDS': json.dumps(needs),
                                             'RELAY_REQUESTED': requested, 'CROSSLAYER_REQUESTED': 'false'}, capture_output=True, text=True)
                self.assertEqual(expected, result.returncode, result.stderr)

    def test_crosslayer_is_manual_opt_in_and_summary_fails_closed(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assertIn('      run_crosslayer:\n        description: Run the isolated MariaDB and PostgreSQL browser relay diagnostics\n'
                      '        type: boolean\n        default: false\n', workflow)
        self.assertIn("if: ${{ github.event_name == 'workflow_dispatch' && inputs.run_crosslayer == true }}", workflow)
        self.assertIn('uses: ./.github/workflows/browser-relay-crosslayer.yml', workflow)
        crosslayer_job = workflow.split('  crosslayer:\n', 1)[1].split('\n  summary:\n', 1)[0]
        self.assertNotIn('secrets: inherit', crosslayer_job)
        self.assertNotIn('continue-on-error', crosslayer_job)
        base = ('full', True, 'success', 'success', 'success')
        for requested, status, expected in [(False, 'skipped', True), (False, 'success', False),
                                            (True, 'success', True), (True, 'failure', False),
                                            (True, 'cancelled', False), (True, 'skipped', False),
                                            (True, None, False)]:
            with self.subTest(requested=requested, status=status):
                self.assertEqual(expected, self.module.accept_results(*base, crosslayer_requested=requested, crosslayer=status))
        for relay_requested in (False, True):
            for crosslayer_requested in (False, True):
                relay_status = 'success' if relay_requested else 'skipped'
                crosslayer_status = 'success' if crosslayer_requested else 'skipped'
                self.assertTrue(self.module.accept_results(*base, relay_requested=relay_requested, relay=relay_status,
                                                          crosslayer_requested=crosslayer_requested, crosslayer=crosslayer_status))
                self.assertFalse(self.module.accept_results(*base, relay_requested=relay_requested, relay='failure',
                                                           crosslayer_requested=crosslayer_requested, crosslayer=crosslayer_status))
                self.assertFalse(self.module.accept_results(*base, relay_requested=relay_requested, relay=relay_status,
                                                           crosslayer_requested=crosslayer_requested, crosslayer='failure'))
        needs = {'changes': {'result': 'success', 'outputs': {'profile': 'full', 'images': 'true'}},
                 'quality': {'result': 'success'}, 'images': {'result': 'success'},
                 'relay': {'result': 'skipped'}, 'crosslayer': {'result': 'success'}}
        for requested, status, expected in [('true', 'success', 0), ('true', 'failure', 1),
                                            ('true', 'skipped', 1), ('false', 'skipped', 0),
                                            ('false', 'success', 1), ('invalid', 'success', 1)]:
            with self.subTest(requested=requested, status=status):
                needs['crosslayer']['result'] = status
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/ci/plan.py'), 'summary'],
                                        env={**os.environ, 'CI_NEEDS': json.dumps(needs),
                                             'RELAY_REQUESTED': 'false', 'CROSSLAYER_REQUESTED': requested},
                                        capture_output=True, text=True)
                self.assertEqual(expected, result.returncode, result.stderr)

    def test_pr_file_pages_match_count_and_head_or_use_full(self):
        event = {'pull_request': {'number': 28, 'head': {'sha': 'abc'}}}
        def api(path):
            if '/files?' in path:
                return [{'filename': 'README.md'}]
            return {'head': {'sha': 'abc'}, 'changed_files': 2}
        self.assertIsNone(self.module.changed_paths('pull_request', event, api))
        def moved(path):
            return {'head': {'sha': 'moved'}, 'changed_files': 1}
        self.assertIsNone(self.module.changed_paths('pull_request', event, moved))

    def test_large_push_diff_is_not_treated_as_docs_only(self):
        event = {'before': 'a' * 40, 'after': 'b' * 40}
        api = lambda path: {'files': [{'filename': 'README.md'}] * 300}
        self.assertIsNone(self.module.changed_paths('push', event, api))

    def test_complete_paginated_pr_uses_all_files(self):
        event = {'pull_request': {'number': 28, 'head': {'sha': 'abc'}}}
        def api(path):
            if '/files?' not in path:
                return {'head': {'sha': 'abc'}, 'changed_files': 101}
            if path.endswith('page=1'):
                return [{'filename': 'README.md'}] * 100
            return [{'filename': 'platform/service.py'}]
        self.assertEqual(('full', True), self.module.plan(self.module.changed_paths('pull_request', event, api)))
