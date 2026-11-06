import hashlib
import importlib.util
import unittest
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'fixture_handoff.py'
spec = importlib.util.spec_from_file_location('fixture_handoff', PATH)
fixture_handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_handoff)


class FixtureHandoffTest(unittest.TestCase):
    def test_handoff_database_record_contains_only_hashes_and_short_expiry(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        tenant = '11111111-1111-4111-8111-111111111111'
        principal = '22222222-2222-4222-8222-222222222222'
        connection = '33333333-3333-4333-8333-333333333333'
        with mock.patch.object(fixture_handoff.secrets, 'token_urlsafe', side_effect=['a' * 43, 'b' * 43]):
            handoff = fixture_handoff.make_handoff(tenant, principal, connection, now)
        db = handoff.database_values()
        browser = handoff.browser_values()
        self.assertEqual(db['handoff_hash'], hashlib.sha256(browser['handoff'].encode()).hexdigest())
        self.assertEqual(db['browser_hash'], hashlib.sha256(browser['nonce'].encode()).hexdigest())
        self.assertEqual((db['expires_at'] - now).total_seconds(), 60)
        self.assertEqual(browser['tenantId'], tenant)
        self.assertEqual(browser['principalId'], principal)
        self.assertNotIn(browser['handoff'], repr(handoff))
        self.assertNotIn(browser['nonce'], repr(handoff))
        self.assertNotIn(browser['handoff'], repr(db))
        self.assertNotIn(browser['nonce'], repr(db))

    def test_rejects_malformed_ids_and_invalid_clock_or_randomness(self):
        valid = '11111111-1111-4111-8111-111111111111'
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for invalid in ('../x', 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA', '', None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                fixture_handoff.make_handoff(invalid, valid, valid, now)
        with self.assertRaises(ValueError):
            fixture_handoff.make_handoff(valid, valid, valid, datetime(2026, 1, 1))
        with mock.patch.object(fixture_handoff.secrets, 'token_urlsafe', return_value='bad'):
            with self.assertRaises(ValueError):
                fixture_handoff.make_handoff(valid, valid, valid, now)
        with mock.patch.object(fixture_handoff.secrets, 'token_urlsafe', return_value='a' * 43):
            with self.assertRaises(ValueError):
                fixture_handoff.make_handoff(valid, valid, valid, now)

    def test_browser_fixture_is_private_exclusive_and_does_not_follow_links(self):
        valid = '11111111-1111-4111-8111-111111111111'
        handoff = fixture_handoff.make_handoff(valid, valid, valid, datetime(2026, 1, 1, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = fixture_handoff.write_browser_fixture(handoff, directory)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(handoff.browser_values(), json.loads(path.read_text()))
            with self.assertRaises(ValueError):
                fixture_handoff.write_browser_fixture(handoff, directory)
            path.unlink()
            path.symlink_to(directory / 'outside')
            with self.assertRaises(ValueError):
                fixture_handoff.write_browser_fixture(handoff, directory)
            self.assertFalse((directory / 'outside').exists())
            path.unlink()
            os.chmod(directory, 0o755)
            with self.assertRaises(ValueError):
                fixture_handoff.write_browser_fixture(handoff, directory)

    def test_python_fixture_is_accepted_by_real_browser_handoff_reader(self):
        valid = '11111111-1111-4111-8111-111111111111'
        handoff = fixture_handoff.make_handoff(valid, valid, valid)
        module = Path(__file__).resolve().parents[5] / 'platform/apps/admin-web/e2e/native-candidate/handoff.mjs'
        with tempfile.TemporaryDirectory() as temporary:
            path = fixture_handoff.write_browser_fixture(handoff, Path(temporary))
            script = ('import {readHandoffFixture} from ' + json.dumps(module.as_uri()) + '; '
                      'await readHandoffFixture(process.argv[1], process.argv[2]);')
            result = subprocess.run(['node', '--input-type=module', '-e', script, str(path), temporary],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, 'cross-language handoff contract failed')
            self.assertEqual('', result.stdout)
            self.assertEqual('', result.stderr)


if __name__ == '__main__':
    unittest.main()
