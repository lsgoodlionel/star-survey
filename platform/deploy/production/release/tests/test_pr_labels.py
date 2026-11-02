from unittest.mock import Mock
from test_ci_orchestration import workflow
import unittest


class PRLabelsTest(unittest.TestCase):
    def test_labels_use_main_and_one_job_without_pr_checkout(self):
        labels = workflow('labeler.yml')
        self.assertEqual(['main'], labels['on']['push']['branches'])
        self.assertEqual(1, len(labels['jobs']))
        self.assertNotIn('actions/checkout', str(labels))
        self.assertNotIn('|| true', str(labels))
        self.assertIn('unknown', str(labels))

    def label_script(self):
        step = workflow('labeler.yml')['jobs']['labels']['steps'][0]
        body = step['run'].split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        namespace = {'__name__': 'label_test'}
        exec(compile(body, 'labeler.yml', 'exec'), namespace)
        namespace['time'] = Mock()
        return namespace

    def test_unknown_mergeability_preserves_label_and_real_errors_propagate(self):
        script = self.label_script()
        script['api'] = Mock(return_value={'mergeable_state': 'unknown'})
        script['sync'](27)
        self.assertEqual(12, script['api'].call_count)
        self.assertTrue(all(call.args == ('pulls/27',) for call in script['api'].call_args_list))
        script['api'] = Mock(side_effect=RuntimeError('HTTP 403'))
        with self.assertRaises(RuntimeError):
            script['sync'](27)

    def test_known_conflict_state_adds_or_removes_label(self):
        for state, method in [('dirty', 'POST'), ('clean', 'DELETE')]:
            script = self.label_script()
            script['api'] = Mock(side_effect=[{'mergeable_state': state}, None])
            script['sync'](27)
            self.assertEqual(method, script['api'].call_args.args[1])
