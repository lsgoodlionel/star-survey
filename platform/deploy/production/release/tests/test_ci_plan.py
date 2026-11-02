import importlib.util
from pathlib import Path
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
