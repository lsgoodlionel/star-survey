from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[5]


def workflow(name):
    doc = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
    if True in doc:
        doc['on'] = doc.pop(True)
    return doc


class CIOrchestrationTest(unittest.TestCase):
    def test_single_pr_entry_and_full_release_quality(self):
        ci, release, quality = map(workflow, ['ci.yml', 'release.yml', 'platform-quality.yml'])
        self.assertIn('pull_request', ci['on'])
        self.assertNotIn('paths', ci['on']['pull_request'] or {})
        self.assertNotIn('pull_request', release['on'])
        self.assertNotIn('pull_request', quality['on'])
        self.assertNotIn('push', quality['on'])
        self.assertEqual('full', release['jobs']['quality']['with']['profile'])
        self.assertEqual('./.github/workflows/platform-quality.yml', ci['jobs']['quality']['uses'])

    def test_summary_always_checks_every_dependency(self):
        ci = workflow('ci.yml')
        summary = ci['jobs']['summary']
        self.assertEqual('always()', summary['if'])
        self.assertEqual({'changes', 'quality', 'images'}, set(summary['needs']))
        self.assertIn('plan.py summary', str(summary))
        self.assertIn('toJSON(needs)', str(summary))
        for name, job in workflow('platform-quality.yml')['jobs'].items():
            if name != 'productization-production':
                self.assertIn("inputs.profile != 'policy'", job['if'])

    def test_image_build_is_readonly_reusable_and_shared(self):
        ci, release, images = map(workflow, ['ci.yml', 'release.yml', 'build-images.yml'])
        for job in [ci['jobs']['images'], release['jobs']['build-local']]:
            self.assertEqual('./.github/workflows/build-images.yml', job['uses'])
        self.assertEqual({'workflow_call'}, set(images['on']))
        text = str(images)
        self.assertNotIn('docker/login-action', text)
        self.assertNotIn("'push': True", text)

