import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'platform/deploy/test/hosted-crosslayer/fixture.sh'
WORKFLOW = ROOT / '.github/workflows/browser-relay-crosslayer.yml'
NAME = 'survey-crosslayer-123-1-mariadb-reminder'


class HostedCrosslayerTest(unittest.TestCase):
    def test_workflow_is_explicit_manual_and_does_not_publish(self):
        text = WORKFLOW.read_text()
        self.assertNotIn('  workflow_dispatch:', text)
        self.assertIn('  workflow_call:', text)
        self.assertNotIn('  push:', text)
        self.assertNotIn('  pull_request:', text)
        self.assertIn('engine: [mariadb, postgresql]', text)
        self.assertIn('scenario: [reminder, crash]', text)
        self.assertIn('fail-fast: false', text)
        self.assertNotIn('continue-on-error', text)
        self.assertIn('persist-credentials: false', text)
        self.assertIn('contents: read', text)
        self.assertIn("always() && steps.prepare.outcome != 'skipped'", text)
        self.assertNotIn('upload-artifact', text)
        self.assertNotIn('release.yml', text)
        self.assertNotIn('docker push', text)

    def test_cleanup_rejects_unknown_or_busy_volume(self):
        fake = r'''#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$CALLS"
case "$1:$2" in
  ps:-aq)
    if [[ "$SCENARIO" == bad_list ]]; then exit 1; fi
    if [[ "$*" == *volume=* && "$SCENARIO" == busy ]]; then echo abc123; fi
    if [[ "$*" == *name=* && "$SCENARIO" == wrong_container ]]; then echo abcdef123456; fi ;;
  inspect:-f)
    if [[ "$*" == *Config.Labels* ]]; then echo foreign; else printf '%064d\n' 0; fi ;;
  rm:-f) touch "$REMOVED" ;;
  volume:ls)
    if [[ ! -f "$REMOVED" ]]; then echo "$FIXTURE_NAME"; fi ;;
  volume:inspect)
    if [[ "$SCENARIO" == wrong_owner ]]; then echo other; else echo "$FIXTURE_NAME"; fi ;;
  volume:rm) touch "$REMOVED" ;;
  *) exit 9 ;;
esac
'''
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            docker = d / 'docker'
            docker.write_text(fake)
            docker.chmod(0o700)
            log_dir = d / f'survey-crosslayer-private-{NAME}'
            log_dir.mkdir()
            marker = log_dir / 'owned-volume'
            marker.write_text(NAME + '\n')
            base = {**os.environ, 'PATH': f'{d}:' + os.environ['PATH'], 'GITHUB_RUN_ID': '123',
                    'GITHUB_RUN_ATTEMPT': '1', 'FIXTURE_ENGINE': 'mariadb', 'FIXTURE_SCENARIO': 'reminder',
                    'RUNNER_TEMP': tmp, 'FIXTURE_NAME': NAME, 'CALLS': str(d / 'calls'), 'REMOVED': str(d / 'removed')}
            for scenario in ('wrong_owner', 'busy', 'bad_list', 'wrong_container'):
                with self.subTest(scenario=scenario):
                    (d / 'calls').write_text('')
                    result = subprocess.run(['bash', str(SCRIPT), 'cleanup'], env={**base, 'SCENARIO': scenario}, capture_output=True)
                    self.assertNotEqual(0, result.returncode)
                    self.assertFalse((d / 'removed').exists())
            result = subprocess.run(['bash', str(SCRIPT), 'cleanup'], env={**base, 'SCENARIO': 'ok'}, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((d / 'removed').exists())
            (d / 'removed').unlink()
            marker.unlink()
            result = subprocess.run(['bash', str(SCRIPT), 'cleanup'], env={**base, 'SCENARIO': 'ok'}, capture_output=True)
            self.assertNotEqual(0, result.returncode)  # resource without proof is retained
            self.assertFalse((d / 'removed').exists())
            (d / 'removed').touch()  # simulate an empty inventory
            result = subprocess.run(['bash', str(SCRIPT), 'cleanup'], env={**base, 'SCENARIO': 'ok'}, capture_output=True)
            self.assertEqual(0, result.returncode)

    def test_prepare_records_owned_cache_and_checks_online_then_offline(self):
        fake = r'''#!/usr/bin/env bash
set -eu
printf "%s\n" "$*" >> "$CALLS"
case "$1:$2" in
  volume:ls|ps:-aq) ;;
  volume:create|pull:*|compose:*|build:*|run:*) ;;
  volume:inspect) echo "$FIXTURE_NAME" ;;
  image:inspect) printf 'sha256:%064d\n' 0 ;;
  *) exit 9 ;;
esac
'''
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            docker = d / 'docker'
            docker.write_text(fake)
            docker.chmod(0o700)
            output = d / 'outputs'
            env = {**os.environ, 'PATH': f'{d}:' + os.environ['PATH'], 'GITHUB_RUN_ID': '123',
                   'GITHUB_RUN_ATTEMPT': '1', 'FIXTURE_ENGINE': 'mariadb', 'FIXTURE_SCENARIO': 'reminder',
                   'RUNNER_TEMP': tmp, 'GITHUB_OUTPUT': str(output), 'FIXTURE_NAME': NAME, 'CALLS': str(d / 'calls')}
            result = subprocess.run(['bash', str(SCRIPT), 'prepare'], cwd=ROOT, env=env, capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(NAME, (d / f'survey-crosslayer-private-{NAME}/owned-volume').read_text().strip())
            self.assertIn('cache=' + NAME, output.read_text())
            calls = (d / 'calls').read_text()
            self.assertEqual(2, calls.count('-Dtest=ProbePayloadPolicyTest'))
            self.assertIn('mvn -o -B -q -Dtest=ProbePayloadPolicyTest', calls)
            self.assertNotIn(' push ', calls)

    def test_invalid_identity_fails_before_docker(self):
        env = {**os.environ, 'GITHUB_RUN_ID': '../../wrong', 'GITHUB_RUN_ATTEMPT': '1',
               'FIXTURE_ENGINE': 'mariadb', 'FIXTURE_SCENARIO': 'reminder', 'RUNNER_TEMP': '/tmp'}
        self.assertNotEqual(0, subprocess.run(['bash', str(SCRIPT), 'cleanup'], env=env).returncode)


if __name__ == '__main__':
    unittest.main()
