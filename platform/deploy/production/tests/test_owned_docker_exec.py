"""ExecID state transitions; fake API is not daemon evidence."""
from copy import deepcopy
import importlib
import hashlib
import io
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from test_owned_children import Guard, Store
from test_owned_memory import PortableFDBackend

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class OwnedDockerExecTest(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('owned_docker_exec')
        import owned_memory
        self.workspace = owned_memory.AnonymousWorkspace(_backend=PortableFDBackend())
        self.addCleanup(self.workspace.close)
        self.store, self.guard, self.events = Store(), Guard(), []
        self.fail, self.exit = None, 0
        self.container, self.exec_id = 'c' * 64, 'e' * 64
        owner = self
        class API:
            def identity(self):
                return 'daemon-fixture'
            def create(self, container, command):
                owner.assertEqual('create-pending', owner.store.value['phase'])
                owner.events.append('create')
                owner.nonce = command[-1]
                if owner.fail == 'create': raise OSError('unknown create')
                return owner.exec_id
            def inspect(self, exec_id):
                if owner.fail == 'inspect': raise OSError('unavailable')
                return {'ID': owner.exec_id, 'ContainerID': owner.container,
                    'Running': owner.fail == 'running', 'ExitCode': None if owner.fail == 'not-started' else owner.exit}
            def stream(self, exec_id, output, limit):
                owner.assertEqual('released', owner.store.value['phase'])
                owner.events.append('start')
                if owner.fail == 'stream': raise OSError('unknown stream')
                payload = b'synthetic dump'
                output(payload)
                if owner.fail != 'boundary':
                    output(owner.module.PayloadProof.PREFIX + owner.nonce.encode() + b':'
                        + hashlib.sha256(payload).hexdigest().encode() + b'\n')
        self.api = API()
        self.executor = self.module.OwnedDockerExec(self.api, self.store, self.guard,
            operation_id='00000000-0000-4000-8000-000000000001',
            installation_id='00000000-0000-4000-8000-000000000002',
            container_check=lambda identity, allowed=None: identity == self.container)
        self.executor.pause = lambda seconds: None

    def execute(self):
        with (self.workspace / 'dump').export_for_child(write=True) as lease:
            return self.executor.execute('postgres-dump', self.container, lease)

    def test_diagnostic_profiles_are_fixed_and_close_body_is_strictly_bounded(self):
        nonce = 'a' * 64
        for profile in ('postgres-schema', 'engine-status', 'engine-event-cron'):
            self.assertIn('/usr/bin/timeout', self.module.command_for(profile, nonce))
        body = {'requestId': '00000000-0000-4000-8000-000000000003',
                'engineInstanceId': 'production-engine-01', 'surveyId': 123}
        command = self.module.command_for('gateway-close', nonce, body=body)
        self.assertIn('http://127.0.0.1:8080/v1/close', command[5])
        for updates in ({'surveyId': True}, {'surveyId': 0}, {'engineInstanceId': 'bad/route'},
                        {'requestId': 'bad'}, {'url': 'http://elsewhere'}):
            with self.assertRaises(ValueError):
                self.module.command_for('gateway-close', nonce, body=dict(body, **updates))
        with self.assertRaises(ValueError): self.module.command_for('postgres-dump', nonce, body=body)

    def test_bad_close_parameters_cannot_create_an_exec(self):
        with (self.workspace / 'close').export_for_child(write=True) as lease:
            with self.assertRaises(self.module.KnownExecFailure):
                self.executor.execute('gateway-close', self.container, lease, body={'surveyId': True})
        self.assertEqual([], self.events)

    def test_mariadb_secret_read_failure_cannot_be_masked_by_successful_dump_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker, credential = root / 'dump-invoked', root / 'credential'
            program = root / 'mariadb-dump'
            program.write_text('#!/bin/sh\nprintf attempted > "$SURVEY_FIXTURE_MARKER"\nprintf "%s\\n" "-- MariaDB dump fixture"\n')
            program.chmod(0o700)
            command = self.module.PROFILES['mariadb-dump'][1][-1].replace(
                '/run/secrets/engine_db_root_password', shlex.quote(str(credential)))
            environment = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                               SURVEY_FIXTURE_MARKER=str(marker))
            for empty in (False, True):
                if empty: credential.write_text('')
                result = subprocess.run(['/bin/sh', '-c', command], env=environment,
                                        capture_output=True, timeout=5)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(marker.exists())

    def test_start_follows_persisted_id_and_released_receipt_then_exact_end(self):
        self.execute()
        self.assertEqual(['create', 'start'], self.events)
        self.assertEqual('completed', self.store.value['phase'])
        self.assertEqual(self.exec_id, self.store.value['execId'])
        self.assertEqual(b'synthetic dump', (self.workspace / 'dump').read_bytes())

    def test_persistence_failure_cannot_start_and_create_failure_remains_pending(self):
        self.store.fail = True
        with self.assertRaises(self.module.KnownExecFailure): self.execute()
        self.assertEqual([], self.events)
        self.store.fail = False
        self.fail = 'create'
        with self.assertRaises(self.module.KnownExecFailure): self.execute()
        self.assertEqual('create-pending', self.store.value['phase'])
        self.assertNotIn('start', self.events)

    def test_unknown_start_or_end_never_claims_completed(self):
        for fail in ('stream', 'inspect', 'running', 'not-started'):
            with self.subTest(fail=fail):
                self.setUp()
                self.fail = fail
                with self.assertRaises(self.module.UncertainExec): self.execute()
                self.assertEqual('released', self.store.value['phase'])

    def test_wrong_exec_container_and_boolean_exit_are_not_end_evidence(self):
        for field, value in (('ContainerID', 'd' * 64), ('ID', 'f' * 64), ('ExitCode', False)):
            with self.subTest(field=field):
                self.setUp()
                inspect = self.api.inspect
                def changed(identity):
                    result = inspect(identity); result[field] = value; return result
                self.api.inspect = changed
                with self.assertRaises(self.module.UncertainExec): self.execute()
                self.assertEqual('released', self.store.value['phase'])

    def test_known_nonzero_is_failed_and_prior_journal_cannot_be_reused(self):
        self.exit = 1
        with self.assertRaises(self.module.KnownExecFailure): self.execute()
        self.assertEqual('failed', self.store.value['phase'])
        self.assertEqual(1, self.store.value['exitCode'])
        with self.assertRaises(self.module.KnownExecFailure): self.execute()
        self.assertEqual(['create', 'start'], self.events)

    def test_completed_receipt_cannot_be_overwritten_with_a_new_empty_output(self):
        self.execute()
        before = deepcopy(self.store.value)
        with (self.workspace / 'second').export_for_child(write=True) as lease:
            with self.assertRaises(self.module.KnownExecFailure):
                self.executor.execute('postgres-dump', self.container, lease)
        self.assertEqual(before, self.store.value)
        self.assertEqual(['create', 'start'], self.events)

    def test_stream_parser_accepts_only_complete_bounded_multiplex_frames(self):
        def frame(stream, data):
            return bytes((stream, 0, 0, 0)) + len(data).to_bytes(4, 'big') + data
        values = []
        self.module.read_stream(io.BytesIO(frame(1, b'abc') + frame(2, b'private error')), values.append, 3)
        self.assertEqual([b'abc'], values)
        for payload in (frame(1, b'long'), b'\x01', frame(3, b'x'), frame(1, b'abc')[:-1]):
            with self.assertRaises(self.module.ExecProtocolError):
                self.module.read_stream(io.BytesIO(payload), lambda block: None, 3)

    def test_frame_boundary_eof_then_later_exit_zero_cannot_prove_complete_dump(self):
        self.fail = 'boundary'
        original = self.api.inspect
        checks = []
        def delayed(identity):
            result = original(identity)
            checks.append(identity)
            result['Running'] = len(checks) == 1
            return result
        self.api.inspect = delayed
        with self.assertRaises(self.module.KnownExecFailure):
            self.execute()  # No application-level end proof from this fake stream.
        self.assertEqual('failed', self.store.value['phase'])

    def test_payload_proof_binds_nonce_digest_and_tail_across_arbitrary_chunks(self):
        payload, nonce = b'PGDMP\x00synthetic', 'a' * 64
        trailer = self.module.PayloadProof.PREFIX + nonce.encode() + b':' + hashlib.sha256(payload).hexdigest().encode() + b'\n'
        values = []
        proof = self.module.PayloadProof(nonce, values.append, len(payload))
        for byte in payload + trailer:
            proof.feed(bytes([byte]))
        proof.finish()
        self.assertEqual(payload, b''.join(values))
        for damaged in (payload, payload + trailer[:-1], payload + trailer.replace(b'a' * 64, b'b' * 64),
                payload + trailer[:-65] + b'0' * 64 + b'\n'):
            proof = self.module.PayloadProof(nonce, lambda block: None, len(payload))
            proof.feed(damaged)
            with self.assertRaises(self.module.ExecProtocolError): proof.finish()


if __name__ == '__main__':
    unittest.main()
