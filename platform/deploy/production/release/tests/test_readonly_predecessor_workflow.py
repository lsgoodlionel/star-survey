import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[5]
PATH = ROOT / '.github/workflows/verify-rc17-predecessor.yml'


class ReadonlyPredecessorWorkflowTest(unittest.TestCase):
    def load(self):
        workflow = yaml.safe_load(PATH.read_text())
        if True in workflow:
            workflow['on'] = workflow.pop(True)
        return workflow

    def test_manual_main_only_read_permissions_and_no_publication(self):
        workflow = self.load()
        self.assertEqual({'workflow_dispatch'}, set(workflow['on']))
        self.assertEqual({'contents': 'read'}, workflow['permissions'])
        self.assertEqual({'verify'}, set(workflow['jobs']))
        job = workflow['jobs']['verify']
        self.assertIn("github.ref == 'refs/heads/main'", job['if'])
        self.assertIn("github.repository == 'lsgoodlionel/star-survey'", job['if'])
        self.assertEqual({'contents': 'read', 'actions': 'read', 'packages': 'read'}, job['permissions'])
        text = PATH.read_text()
        for forbidden in ('contents: write', 'packages: write', 'id-token:', 'publish_release.sh', 'gh release', 'docker push', 'candidate_run_id', 'promote_run_id'):
            self.assertNotIn(forbidden, text)
        for step in job['steps']:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'@[0-9a-f]{40}$')

    def test_imagetools_uses_existing_cli_without_starting_a_builder(self):
        job = self.load()['jobs']['verify']
        for step in job['steps']:
            self.assertFalse(step.get('uses', '').startswith('docker/setup-buildx-action@'))
            for forbidden in ('buildx create', 'buildx inspect --bootstrap', 'buildkitd-flags', 'entitlements'):
                self.assertNotIn(forbidden, step.get('run', ''))
        probe = next(s for s in job['steps'] if s.get('id') == 'buildx')
        self.assertEqual('docker buildx version', probe['run'])
        self.assertLess(job['steps'].index(probe), next(i for i, s in enumerate(job['steps']) if s.get('id') == 'verify'))
        verifier = (ROOT / 'platform/deploy/production/release/verify_release.sh').read_text()
        self.assertIn('docker buildx imagetools inspect', verifier)
        self.assertNotIn('buildx create', verifier)

    def test_fixed_source_exact_artifact_and_full_verifier(self):
        job = self.load()['jobs']['verify']
        steps = {step['id']: step for step in job['steps'] if 'id' in step}
        download = steps['download']['with']
        self.assertEqual('13289572394', str(download['artifact-ids']))
        self.assertEqual('41756770743', str(download['run-id']))
        self.assertEqual('lsgoodlionel/star-survey', download['repository'])
        self.assertNotIn('name', download)
        verify = steps['verify']['run']
        self.assertIn('verify_release.sh', verify)
        self.assertIn('--target-tag v0.3.17-rc.1 --source-tag v0.3.17-rc.1', verify)
        self.assertIn('--target-commit d745a5eeb7eca6af59d63986db004249efbd2acc', verify)
        self.assertEqual(2, sum('candidate_provenance.py' in step.get('run', '') for step in job['steps']))
        self.assertIn('95b113aa6c554c8e918bdf8d7b0e4ed47bfa59d6b129741d131e7ca4c611379b', steps['manifest']['run'])
        self.assertEqual('always()', steps['evidence']['if'])
        self.assertIn('predecessor-reverification-only', steps['evidence']['run'])
        self.assertIn('upgradeVerified', steps['evidence']['run'])
        self.assertIn("'publicationApproved': False", steps['evidence']['run'])

    def test_evidence_cannot_turn_failed_skipped_or_unknown_checks_into_success(self):
        job = self.load()['jobs']['verify']
        for step in job['steps']:
            self.assertNotIn('continue-on-error', step)
        step = next(s for s in job['steps'] if s.get('id') == 'evidence')
        script = step['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        stages = ('SOURCE', 'ARTIFACT', 'DOWNLOAD', 'MANIFEST', 'VERIFY', 'RECHECK')
        with tempfile.TemporaryDirectory() as temporary:
            previous = Path.cwd()
            try:
                os.chdir(temporary)
                for outcome in ('success', 'failure', 'skipped', 'cancelled', ''):
                    for changed in stages:
                        env = {key: 'success' for key in stages}
                        env[changed] = outcome
                        env['GITHUB_STEP_SUMMARY'] = str(Path(temporary) / 'summary')
                        env['GH_TOKEN'] = 'must-not-be-recorded'
                        with self.subTest(outcome=outcome, stage=changed), mock.patch.dict(os.environ, env, clear=True):
                            exec(compile(script, 'bounded-evidence', 'exec'), {})
                            raw = Path('verification-evidence/rc17.json').read_text()
                            evidence = json.loads(raw)
                            self.assertEqual('passed' if outcome == 'success' else 'not-passed', evidence['status'])
                            self.assertIs(False, evidence['upgradeVerified'])
                            self.assertIs(False, evidence['publicationApproved'])
                            self.assertNotIn('must-not-be-recorded', raw)
            finally:
                os.chdir(previous)

    def test_artifact_metadata_mutations_fail_closed(self):
        step = next(s for s in self.load()['jobs']['verify']['steps'] if s.get('id') == 'artifact')
        script = step['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
        original = {'id': 13289572394, 'name': 'verified-release-assets', 'expired': False,
                    'digest': 'sha256:5634d53ea99a324b51136fc7c3bd414715bc066c9b41d3ba7b14a1b27af4f45b',
                    'workflow_run': {'id': 41756770743, 'head_sha': 'd745a5eeb7eca6af59d63986db004249efbd2acc',
                                     'head_branch': 'v0.3.17-rc.1', 'repository_id': 1377727385, 'head_repository_id': 1377727385}}
        def execute(value):
            with mock.patch('subprocess.check_output', return_value=json.dumps(value)), contextlib.redirect_stdout(io.StringIO()):
                exec(compile(script, 'artifact-guard', 'exec'), {})
        execute(original)
        mutations = [('id', 1), ('name', 'release-candidate'), ('expired', True), ('expired', 0), ('digest', 'sha256:'+'0'*64)]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                execute({**original, key: value})
        for key, value in [('id', 1), ('head_sha', '0'*40), ('head_branch', 'v0.3.18-rc.1'),
                           ('repository_id', 1), ('head_repository_id', 1)]:
            with self.subTest(source=key), self.assertRaises(SystemExit):
                execute({**original, 'workflow_run': {**original['workflow_run'], key: value}})


if __name__ == '__main__':
    unittest.main()
