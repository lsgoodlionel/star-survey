"""Owner decision 2026-11-11 (#116, P2): Q3 overlap hint and Q5 board sync are hint-only."""
from unittest import mock
from test_ci_orchestration import workflow
import io
import json
import os
import tempfile
import unittest


def script(name, job):
    step = workflow(name)['jobs'][job]['steps'][0]
    body = step['run'].split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    namespace = {'__name__': 'collab_hint_test'}
    exec(compile(body, name, 'exec'), namespace)
    return namespace


class OverlapHintWorkflowTest(unittest.TestCase):
    def test_overlap_hint_is_read_only_and_never_checks_out_code(self):
        doc = workflow('pr-overlap-hint.yml')
        self.assertEqual({'contents': 'read', 'pull-requests': 'read'}, doc['permissions'])
        self.assertEqual(['opened', 'synchronize', 'reopened', 'ready_for_review'], doc['on']['pull_request']['types'])
        self.assertEqual(['pull_request'], list(doc['on']))
        self.assertNotIn('actions/checkout', str(doc))
        self.assertNotIn('secrets.', str(doc))
        self.assertNotIn('|| true', str(doc))
        self.assertEqual(1, len(doc['jobs']))

    def files(self, mapping):
        def api(path):
            if path.startswith('pulls?'):
                return mapping['open']
            number = int(path.split('/')[1])
            page = int(path.rsplit('page=', 1)[1])
            return [{'filename': name} for name in mapping[number]] if page == 1 else []
        return api

    def test_reports_shared_files_and_excludes_itself(self):
        hint = script('pr-overlap-hint.yml', 'overlap')
        hint['api'] = self.files({'open': [{'number': 1}, {'number': 2, 'draft': True}, {'number': 3}],
                                  1: ['a.md', 'b.py'], 2: ['b.py', 'c.py'], 3: ['d.py']})
        rows, complete = hint['overlaps'](1, hint['api']('pulls?state=open'))
        self.assertTrue(complete)
        self.assertEqual([(2, ['b.py'])], [(pr['number'], shared) for pr, shared in rows])
        text = hint['report'](1, rows, complete)
        self.assertIn('#2（Draft）', text)
        self.assertIn('`b.py`', text)

    def test_no_overlap_text_does_not_claim_no_semantic_conflict(self):
        hint = script('pr-overlap-hint.yml', 'overlap')
        text = hint['report'](1, [], True)
        self.assertIn('不代表没有语义或接口冲突', text)
        self.assertIn('部分未检查', hint['report'](1, [], False))

    def test_api_failure_is_reported_as_not_checked_without_failing(self):
        hint = script('pr-overlap-hint.yml', 'overlap')
        hint['api'] = mock.Mock(side_effect=RuntimeError('GitHub API failed: HTTP 403'))
        with tempfile.TemporaryDirectory() as temporary:
            event = os.path.join(temporary, 'event.json')
            summary = os.path.join(temporary, 'summary.md')
            with open(event, 'w', encoding='utf-8') as handle:
                json.dump({'pull_request': {'number': 9}}, handle)
            with mock.patch.dict(os.environ, {'GITHUB_EVENT_PATH': event, 'GITHUB_STEP_SUMMARY': summary}), \
                 mock.patch('sys.stdout', new_callable=io.StringIO):
                hint['main']()
            with open(summary, encoding='utf-8') as handle:
                text = handle.read()
        self.assertIn('未检查', text)
        self.assertNotIn('没有发现文件重叠', text)

    def test_open_pr_limit_marks_the_result_incomplete(self):
        hint = script('pr-overlap-hint.yml', 'overlap')
        others = [{'number': n} for n in range(2, 2 + hint['MAX_OPEN_PRS'] + 1)]
        mapping = {'open': others, 1: ['a.md']}
        mapping.update({pr['number']: ['z.md'] for pr in others})
        hint['api'] = self.files(mapping)
        _, complete = hint['overlaps'](1, others)
        self.assertFalse(complete)


class ProjectSyncWorkflowTest(unittest.TestCase):
    def test_board_sync_runs_only_from_main_without_checkout(self):
        doc = workflow('project-sync.yml')
        self.assertEqual({'push': {'branches': ['main']}}, doc['on'])
        self.assertEqual({'contents': 'read', 'pull-requests': 'read'}, doc['permissions'])
        self.assertNotIn('actions/checkout', str(doc))
        self.assertNotIn('pull_request_target', str(doc))
        self.assertNotIn('|| true', str(doc))
        env = doc['jobs']['sync']['steps'][0]['env']
        self.assertEqual('${{ secrets.PROJECT_SYNC_TOKEN }}', env['PROJECT_TOKEN'])

    def run_main(self, sync_script, environment):
        with tempfile.TemporaryDirectory() as temporary:
            summary = os.path.join(temporary, 'summary.md')
            env = {'GITHUB_SHA': 'abc', 'GITHUB_STEP_SUMMARY': summary, 'PROJECT_ID': 'P', **environment}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch('sys.stdout', new_callable=io.StringIO):
                sync_script['main']()
            with open(summary, encoding='utf-8') as handle:
                return handle.read()

    def test_missing_secret_is_pending_and_touches_nothing(self):
        sync_script = script('project-sync.yml', 'sync')
        sync_script['rest'] = mock.Mock()
        sync_script['graphql'] = mock.Mock()
        text = self.run_main(sync_script, {})
        self.assertIn('待同步', text)
        sync_script['rest'].assert_not_called()
        sync_script['graphql'].assert_not_called()

    def test_api_failure_is_pending_not_synced(self):
        sync_script = script('project-sync.yml', 'sync')
        sync_script['rest'] = mock.Mock(side_effect=RuntimeError('GitHub API failed: HTTP 500'))
        text = self.run_main(sync_script, {'PROJECT_TOKEN': 't'})
        self.assertIn('待同步', text)
        self.assertNotIn('已设为完成', text)

    def test_only_the_merged_pr_card_on_this_project_is_updated(self):
        sync_script = script('project-sync.yml', 'sync')
        sync_script['rest'] = mock.Mock(return_value=[
            {'number': 5, 'node_id': 'PR5', 'merged_at': 'x', 'merge_commit_sha': 'abc', 'base': {'ref': 'main'}},
            {'number': 6, 'node_id': 'PR6', 'merged_at': None, 'merge_commit_sha': 'abc', 'base': {'ref': 'main'}},
            {'number': 7, 'node_id': 'PR7', 'merged_at': 'x', 'merge_commit_sha': 'other', 'base': {'ref': 'main'}},
        ])
        calls = []

        def graphql(query, variables):
            calls.append(variables)
            if 'node' in variables:
                return {'node': {'projectItems': {'nodes': [{'id': 'OTHER', 'project': {'id': 'Q'}},
                                                            {'id': 'ITEM', 'project': {'id': 'P'}}]}}}
            return {}

        sync_script['graphql'] = graphql
        text = self.run_main(sync_script, {'PROJECT_TOKEN': 't', 'STATUS_FIELD_ID': 'F', 'DONE_OPTION_ID': 'D'})
        self.assertEqual([{'node': 'PR5'}, {'project': 'P', 'item': 'ITEM', 'field': 'F', 'option': 'D'}], calls)
        self.assertIn('#5 卡片已设为完成', text)


if __name__ == '__main__':
    unittest.main()
