from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_pinned_database import PinnedDatabaseSession, PinnedSessionError


class PinnedDatabaseTest(unittest.TestCase):
    def session(self):
        session = PinnedDatabaseSession.__new__(PinnedDatabaseSession)
        session.identity = {'pid': 12, 'started': 'time', 'database_oid': 5, 'database': 'platform'}
        session.poisoned = False
        session._binding = mock.Mock()
        session._identity = mock.Mock(return_value=dict(session.identity))
        session._query = mock.Mock(return_value='ok')
        session.close = mock.Mock()
        return session

    def test_identity_and_binding_checked_before_statement(self):
        session = self.session()
        self.assertEqual(session.execute('SELECT 1;'), 'ok')
        session._binding.assert_called_once_with()
        session._identity.assert_called_once_with()
        session._query.assert_called_once_with('SELECT 1;')

    def test_disconnect_or_rebinding_poisons_session_without_retries(self):
        for failure in ('binding', 'identity', 'query'):
            with self.subTest(failure=failure):
                session = self.session()
                if failure == 'binding': session._binding.side_effect = RuntimeError('sensitive')
                elif failure == 'identity': session._identity.return_value = dict(session.identity, pid=99)
                else: session._query.side_effect = RuntimeError('sensitive')
                with self.assertRaisesRegex(PinnedSessionError, '^database session outcome remains unverified$'):
                    session.execute('SELECT 1;')
                session.close.assert_called_once_with()
                with self.assertRaises(PinnedSessionError): session.execute('SELECT 2;')
                self.assertLessEqual(session._query.call_count, 1)

    def test_rejects_unapproved_environment_before_process_creation(self):
        with mock.patch('fixture_pinned_database.os.geteuid', return_value=0), \
             mock.patch.dict('fixture_pinned_database.os.environ', {'GITHUB_ACTIONS': 'false'}, clear=True), \
             mock.patch('fixture_pinned_database.subprocess.Popen') as spawn:
            with self.assertRaises(PinnedSessionError):
                PinnedDatabaseSession('a'*64, 'platform', lambda _: True)
            spawn.assert_not_called()


if __name__ == '__main__': unittest.main()
