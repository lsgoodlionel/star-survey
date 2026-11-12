"""Fixture uncertainty and evidence contracts, not a product recovery gate."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


class DatabaseCopyFixtureTest(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parent / 'integration/recovery_database_native.py'
        spec = importlib.util.spec_from_file_location('recovery_database_probe', path)
        self.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.module)

    def test_uncertain_exec_is_never_replayed_and_disallows_cleanup(self):
        for result in (subprocess.TimeoutExpired(['fixture'], 1),
                       subprocess.CompletedProcess([], 1, b'', b''),
                       subprocess.CompletedProcess([], 0, 'not-bytes', b'')):
            calls = []
            def runner(args, **kwargs):
                calls.append(args)
                if isinstance(result, Exception): raise result
                return result
            with self.subTest(result=type(result).__name__):
                guard = self.module.FixtureExec(runner)
                with self.assertRaises(self.module.CopyError): guard.execute('a' * 64, ['psql'])
                with self.assertRaises(self.module.CopyError): guard.execute('a' * 64, ['psql'])
                self.assertEqual(1, len(calls)); self.assertTrue(guard.uncertain)
                self.assertFalse(guard.may_cleanup())

    def test_known_success_and_stdin_stay_in_memory_without_shell_interpolation(self):
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs)); return subprocess.CompletedProcess(args, 0, b'1\n', b'')
        guard = self.module.FixtureExec(runner)
        self.assertEqual(b'1\n', guard.execute('a' * 64, ['psql'], b'SELECT 1;'))
        self.assertEqual(b'SELECT 1;', calls[0][1]['input'])
        self.assertNotIn('SELECT 1;', calls[0][0]); self.assertNotIn('shell', calls[0][1])
        self.assertTrue(guard.may_cleanup())

    def test_invalid_container_or_tool_cannot_start_a_command(self):
        calls = []
        guard = self.module.FixtureExec(lambda *a, **k: calls.append(a))
        for cid, argv in (('name', ['psql']), ('a' * 64, ['sh', '-c', 'unknown'])):
            with self.assertRaises(self.module.CopyError): guard.execute(cid, argv)
        self.assertEqual([], calls)

    def test_evidence_requires_all_real_checks_and_explicit_nonqualification(self):
        hashes = {'fixture': 'a' * 64}
        report = dict(schemaVersion=1, status='passed', fixtureCleanup='verified',
            sourceBefore=hashes, sourceAfter=hashes,
            **{key: True for key in self.module.CHECKS}, **{key: False for key in self.module.UNQUALIFIED})
        self.assertTrue(self.module.passed(report, hashes))
        for key in self.module.CHECKS:
            for value in (False, None, 1):
                self.assertFalse(self.module.passed({**report, key: value}, hashes))
        for key in self.module.UNQUALIFIED:
            self.assertFalse(self.module.passed({**report, key: True}, hashes))
        self.assertFalse(self.module.passed({**report, 'fixtureCleanup': 'unknown'}, hashes))
        self.assertFalse(self.module.passed({**report, 'schemaVersion': True}, hashes))

    def test_pending_intent_is_private_durable_and_detects_same_byte_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'operation.intent'
            intent = self.module.FixtureIntent(path, 'survey-recovery-' + 'a' * 32)
            try:
                self.assertTrue(intent.check())
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
                self.assertIn(b'"state": "pending"', path.read_bytes())
                replacement = path.with_name('replaced'); replacement.write_bytes(path.read_bytes()); replacement.chmod(0o600)
                replacement.replace(path)
                self.assertFalse(intent.check())
                with self.assertRaises(FileExistsError): self.module.FixtureIntent(path, 'survey-recovery-' + 'a' * 32)
            finally: intent.close()

    def test_unfresh_or_shared_target_refuses_before_any_import(self):
        for fresh, isolated in ((False, True), (True, False)):
            guard = self.module.FixtureExec(lambda *a, **k: self.fail('no import allowed'))
            with self.assertRaises(self.module.CopyError): self.module.require_fresh_target(fresh, isolated, guard)
            self.assertTrue(guard.uncertain)
            self.assertFalse(guard.may_cleanup())


if __name__ == '__main__': unittest.main()
