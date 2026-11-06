import importlib.util
from pathlib import Path
import unittest
import uuid

path = Path(__file__).resolve().parents[1] / 'fixture_compensation.py'
spec = importlib.util.spec_from_file_location('fixture_compensation', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CompensationBuilderTest(unittest.TestCase):
    def values(self):
        names = ('tenant', 'owner_plan', 'fixture_plan', 'principal', 'connection',
                 'original_sub', 'fixture_sub', 'restore_sub', 'owner_grant')
        result = {name: str(uuid.uuid4()) for name in names}
        result.update(handoff_hash='a' * 64, browser_hash='b' * 64)
        return result

    def test_rejects_noncanonical_or_duplicated_identities_and_sql_injection(self):
        for field, value in (('tenant', "x'; DROP TABLE subscription;--"),
                             ('principal', str(uuid.UUID(int=0))), ('handoff_hash', 'a' * 63 + "'"),
                             ('browser_hash', None)):
            values = self.values()
            values[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                module.build_compensation_sql(**values)
        values = self.values()
        values['principal'] = values['tenant']
        with self.assertRaises(ValueError):
            module.build_compensation_sql(**values)

    def test_build_does_not_contact_database(self):
        # The result is a transaction, not execution or an isolation proof.
        sql = module.build_compensation_sql(**self.values())
        self.assertTrue(sql.strip().startswith('BEGIN;'))
        self.assertTrue(sql.strip().endswith('COMMIT;'))


if __name__ == '__main__':
    unittest.main()
