import hashlib
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'candidate_provenance.py'
spec = importlib.util.spec_from_file_location('candidate_provenance', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CandidateProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.run = {'id': 123, 'workflow_id': 456, 'event': 'push', 'status': 'completed',
                    'conclusion': 'success', 'head_sha': 'a' * 40, 'head_branch': 'v1.2.3-rc.1',
                    'repository': {'full_name': 'lsgoodlionel/star-survey'},
                    'head_repository': {'full_name': 'lsgoodlionel/star-survey'}}

    def validate(self, run):
        module.verify_run(run, 123, 456, 'lsgoodlionel/star-survey', 'v1.2.3-rc.1', 'a' * 40)

    def test_accepts_only_exact_successful_tag_push(self):
        self.validate(self.run)
        for key, value in [('id', 124), ('workflow_id', 457), ('event', 'workflow_dispatch'),
                           ('status', 'in_progress'), ('conclusion', 'failure'),
                           ('head_sha', 'b' * 40), ('head_branch', 'v1.2.3-rc.2'),
                           ('repository', {'full_name': 'other/repo'}),
                           ('head_repository', {'full_name': 'other/repo'})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate({**self.run, key: value})

    def test_tag_and_digest_syntax_fail_closed(self):
        for tag, commit in [('v1.2.3', 'a'*40), ('v1.2.3-rc.1', 'bad'), ('refs/tags/v1.2.3-rc.1', 'a'*40)]:
            with self.subTest(tag=tag, commit=commit), self.assertRaises(ValueError):
                module.verify_run(self.run, 123, 456, 'lsgoodlionel/star-survey', tag, commit)

class HumanApprovalTest(unittest.TestCase):
    def setUp(self):
        self.issue = {'number': 41, 'state': 'closed', 'state_reason': 'completed',
                      'closed_at': '2026-11-03T10:00:00Z'}
        self.body = ('SURVEY-RELEASE-APPROVAL v1\n'
                     'run: 123\ntag: v1.2.3-rc.1\ncommit: ' + 'a' * 40 + '\n'
                     'manifest-sha256: ' + 'b' * 64 + '\n'
                     'voiceover-result: passed\nvoiceover-evidence: https://github.com/lsgoodlionel/star-survey/issues/41#issuecomment-1234\n'
                     'decision: approve-publication')
        self.comment = {'user': {'login': 'lsgoodlionel'}, 'author_association': 'OWNER',
                        'created_at': '2026-11-03T10:01:00Z', 'body': self.body}
        self.evidence = {'id': 1234, 'issue_url': 'https://api.github.com/repos/lsgoodlionel/star-survey/issues/41',
                         'user': {'login': 'lsgoodlionel'}, 'author_association': 'OWNER',
                         'updated_at': '2026-11-03T09:59:00Z', 'body': ('SURVEY-VOICEOVER-ACCEPTANCE v1\nrun: 123\ntag: v1.2.3-rc.1\ncommit: ' + 'a'*40 + '\n'
                                  'manifest-sha256: ' + 'b'*64 + '\nresult: passed\n'
                                  'coverage: editor,keyboard,focus,errors,save,preview,publish,respond\n'
                                  'method: manual-voiceover\ntester: alice\nindependent-reviewer: bob\n'
                                  'evidence: manual transcript verified and screenshots recorded')}
        self.comment['body'] += '\nvoiceover-evidence-sha256: ' + hashlib.sha256(self.evidence['body'].encode()).hexdigest()

    def validate(self, issue=None, approval=None, evidence=None):
        module.verify_human_approval(issue or self.issue, approval or self.comment, evidence or self.evidence,
                                     'lsgoodlionel', 123, 'v1.2.3-rc.1', 'a'*40, 'b'*64)

    def test_matching_owner_evidence_and_approval(self):
        self.validate()

    def test_fail_closed_on_missing_acceptance_and_forgery(self):
        cases = [({'state': 'open'}, None, None),
                 ({'state_reason': 'not_planned'}, None, None),
                 (None, {'user': {'login': 'someone'}}, None),
                 (None, {'created_at': '2026-11-03T09:00:00Z'}, None),
                 (None, {'body': self.body.replace('b'*64, 'c'*64)}, None),
                 (None, None, {'user': {'login': 'someone'}}),
                 (None, None, {'body': self.evidence['body'].replace('result: passed', 'result: failed')}),
                 (None, None, {'id': 1235}),
                 (None, None, {'updated_at': '2026-11-03T10:02:00Z'}),
                 (None, None, {'body': self.evidence['body'].replace('independent-reviewer: bob', 'independent-reviewer: alice')})]
        for issue_patch, approval_patch, evidence_patch in cases:
            with self.subTest(case=(issue_patch, approval_patch, evidence_patch)), self.assertRaises(ValueError):
                self.validate({**self.issue, **(issue_patch or {})}, {**self.comment, **(approval_patch or {})},
                              {**self.evidence, **(evidence_patch or {})})

class WorkflowArtifactPredecessorTest(unittest.TestCase):
    def test_artifact_predecessor_is_pinned_and_reverified(self):
        import yaml
        root = PATH.parents[4]
        workflow = yaml.safe_load((root / '.github/workflows/release.yml').read_text())
        job = workflow['jobs']['candidate-assets']
        text = str(job)
        self.assertEqual('read', job['permissions'].get('actions'))
        for required in ('predecessor-artifact', 'BASELINE_RUN_ID', 'BASELINE_COMMIT',
                         'candidate_provenance.py', 'gh run download', 'verified-release-assets',
                         'verify_release.sh', 'BASELINE_MANIFEST_SHA256'):
            self.assertIn(required, text)
        self.assertNotIn('gh release list --limit 1', text)

class ManualPromotionWorkflowTest(unittest.TestCase):
    def test_manual_promotion_is_explicit_and_reverifies_candidate(self):
        import yaml
        workflow = yaml.safe_load((PATH.parents[4] / '.github/workflows/release.yml').read_text())
        job = workflow['jobs']['manual-promotion']
        self.assertIn("github.event_name == 'workflow_dispatch'", job['if'])
        self.assertIn("github.ref == 'refs/heads/main'", job['if'])
        self.assertEqual({'actions': 'read', 'contents': 'write', 'packages': 'read'}, job['permissions'])
        text = str(job)
        for expected in ('candidate_provenance.py', 'verified-release-assets', 'verify_release.sh',
                         'render_publication.py', 'publish_release.sh', 'manifest_sha256',
                         'approval_comment_id', 'evidence_comment_id', 'upgrade-verified'):
            self.assertIn(expected, text)

    def test_promotion_guard_and_verification_order(self):
        import yaml
        workflow = yaml.safe_load((PATH.parents[4] / '.github/workflows/release.yml').read_text())
        job = workflow['jobs']['manual-promotion']
        condition = job['if']
        self.assertIn("github.actor == 'lsgoodlionel'", condition)
        self.assertIn("inputs.promote_run_id != ''", condition)
        self.assertNotIn('continue-on-error', str(job))
        steps = job['steps']
        names = [step.get('name') for step in steps]
        self.assertLess(names.index('Verify exact candidate run and tag before download'), names.index('Download only the bound candidate artifact'))
        self.assertLess(names.index('Reverify signatures, attestations and native upgrade evidence'), names.index('Verify candidate-bound human acceptance and publication approval'))
        self.assertEqual('Recheck human approval immediately before publishing', names[-2])
        self.assertEqual('Publish the approved immutable candidate', names[-1])
        for step in steps:
            self.assertNotIn('continue-on-error', step)
            if 'run' in step:
                self.assertNotIn('|| true', step['run'])

class LiveTagTest(unittest.TestCase):
    def test_live_lightweight_and_annotated_tags_are_peeled(self):
        commit = 'a' * 40
        tag_object = 'b' * 40
        def api(path):
            if path.endswith('/git/ref/tags/v1.2.3-rc.1'):
                return {'object': {'type': 'tag', 'sha': tag_object}}
            if path.endswith('/git/tags/' + tag_object):
                return {'object': {'type': 'commit', 'sha': commit}}
            raise AssertionError(path)
        self.assertEqual(commit, module.resolve_live_tag(api, 'lsgoodlionel/star-survey', 'v1.2.3-rc.1'))
        self.assertEqual(commit, module.resolve_live_tag(lambda _: {'object': {'type': 'commit', 'sha': commit}}, 'lsgoodlionel/star-survey', 'v1.2.3-rc.1'))
        for value in ({'object': {'type': 'blob', 'sha': commit}}, {'object': {'type': 'commit', 'sha': 'bad'}}, {}):
            with self.assertRaises(ValueError):
                module.resolve_live_tag(lambda _: value, 'lsgoodlionel/star-survey', 'v1.2.3-rc.1')

    def test_moved_tag_does_not_match_approved_commit(self):
        with self.assertRaises(ValueError):
            module.verify_live_tag(lambda _: {'object': {'type': 'commit', 'sha': 'b'*40}},
                                   'lsgoodlionel/star-survey', 'v1.2.3-rc.1', 'a'*40)
