import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'fixture_quiescence.py'
spec = importlib.util.spec_from_file_location('fixture_quiescence', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FixtureQuiescenceTest(unittest.TestCase):
    def setUp(self):
        self.client = {'pid': 123, 'role': 'fixture', 'database': 'db', 'started': '2026-01-01 00:00:00+00'}
        self.expected = {'database_oid': 42, 'clients': [self.client]}
        self.snapshot = {'database_oid': 42, 'allow_connections': False, 'prepared': 0,
                         'unsafe': 0, 'clients': [copy.deepcopy(self.client)]}

    def test_accepts_exact_closed_quiet_snapshot(self):
        module.require_quiescent_snapshot(self.snapshot, self.expected)

    def test_rejects_missing_or_boolean_expected_identity(self):
        for expected in (None, {}, {'database_oid': True, 'clients': [self.client]},
                         {'database_oid': 42, 'clients': []}):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                module.require_quiescent_snapshot(self.snapshot, expected)

    def test_rejects_unknown_restarted_or_ambiguous_clients_and_work(self):
        for mutation in ('gate', 'oid', 'prepared', 'unsafe', 'extra', 'restart', 'duplicate', 'invalid'):
            value = copy.deepcopy(self.snapshot)
            if mutation == 'gate': value['allow_connections'] = True
            if mutation == 'oid': value['database_oid'] = 43
            if mutation == 'prepared': value['prepared'] = 1
            if mutation == 'unsafe': value['unsafe'] = 1
            if mutation == 'extra': value['clients'].append(dict(self.client, pid=456))
            if mutation == 'restart': value['clients'][0]['started'] = '2026-01-01 00:00:01+00'
            if mutation == 'duplicate': value['clients'].append(copy.deepcopy(self.client))
            if mutation == 'invalid': value['prepared'] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.require_quiescent_snapshot(value, self.expected)
