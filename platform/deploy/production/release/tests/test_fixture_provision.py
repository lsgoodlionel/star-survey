from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_provision import build_fixture_provision_sql


class FixtureProvisionTest(unittest.TestCase):
    def setUp(self):
        self.params = {key: str(uuid.uuid4()) for key in ('tenant', 'owner_plan', 'original_sub', 'owner_grant',
                       'fixture_plan', 'fixture_sub', 'principal', 'connection', 'author_grant')}
        self.params.update(handoff_hash='a'*64, browser_hash='b'*64,
                           expires_at=datetime.now(timezone.utc) + timedelta(seconds=60))

    def test_rejects_untrusted_identifiers_and_expiry(self):
        for key, value in (('tenant', "x'; SELECT 1;--"), ('handoff_hash', 'a'*63),
                           ('browser_hash', 'a'*64), ('expires_at', datetime.now())):
            with self.subTest(key=key):
                values = dict(self.params, **{key: value})
                with self.assertRaises(ValueError): build_fixture_provision_sql(**values)
        values = dict(self.params, principal=self.params['connection'])
        with self.assertRaises(ValueError): build_fixture_provision_sql(**values)

    def test_opaque_secrets_are_not_part_of_sql_contract(self):
        sql = build_fixture_provision_sql(**self.params)
        self.assertIn(self.params['handoff_hash'], sql)
        self.assertIn(self.params['browser_hash'], sql)
        self.assertNotIn('handoff.json', sql)

if __name__ == '__main__': unittest.main()
