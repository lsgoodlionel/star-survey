"""Input state machine models; kernel seals and real Engine tested separately."""
import hashlib
import os
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_owned_docker_exec as output_fixtures


class OwnedExecInputTest(unittest.TestCase):
    def setUp(self):
        output_fixtures.OwnedDockerExecTest.setUp(self)
        self.data = b'controlled input fixture' * 4000
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.source = self.workspace / 'input'
        with self.source.open('xb') as handle: handle.write(self.data)
        create = self.api.create
        def with_stdin(container, command, *, stdin=False):
            self.assertIs(True, stdin)
            return create(container, command)
        self.api.create = with_stdin
        def stream_input(identity, output, limit, *, profile, source):
            self.events.append('input-start')
            self.assertEqual('released', self.store.value['phase'])
            self.assertIs(False, self.store.value['inputEvidence']['delivered'])
            payload = b''.join(source.chunks())
            self.assertEqual(self.data, payload)
            if self.fail == 'partial': raise OSError('unknown partial input')
            result = (('0' * 64 if self.fail == 'digest' else self.sha) + '  -\n').encode()
            output(result)
            if self.fail != 'boundary':
                output(self.module.PayloadProof.PREFIX + self.nonce.encode() + b':'
                    + hashlib.sha256(result).hexdigest().encode() + b'\n')
        self.api.stream_input = stream_input

    def input_execute(self):
        with self.workspace.export_files(inputs=(self.source,), output=self.workspace / 'proof') as (source, output):
            # Portable backend has no kernel seals. Only the model's seal-query
            # seam is replaced; production has no bypass or portable fallback.
            with mock.patch.object(self.module, '_input_seals', return_value=True):
                self.executor.execute('stdin-proof', self.container, output, input_lease=source,
                    input_sha256=self.sha)

    def test_input_success_binds_digest_length_and_exact_terminal_exec(self):
        self.input_execute()
        self.assertEqual(['create', 'input-start'], self.events)
        self.assertEqual(3, self.store.value['schemaVersion'])
        self.assertEqual('completed', self.store.value['phase'])
        self.assertEqual({'sha256': self.sha, 'size': len(self.data), 'delivered': True},
                         self.store.value['inputEvidence'])
        self.assertEqual(self.exec_id, self.store.value['completionEvidence']['ID'])
        before = deepcopy(self.store.value)
        with self.assertRaises(self.module.KnownExecFailure): self.input_execute()
        self.assertEqual(before, self.store.value)

    def test_partial_input_retains_released_receipt_without_replay(self):
        self.fail = 'partial'
        with self.assertRaises(self.module.UncertainExec): self.input_execute()
        self.assertEqual('released', self.store.value['phase'])
        self.assertIsNone(self.store.value['completionEvidence'])
        self.assertIs(False, self.store.value['inputEvidence']['delivered'])
        self.assertEqual(self.data, self.source.read_bytes())
        with self.assertRaises(self.module.KnownExecFailure): self.input_execute()
        self.assertEqual(['create', 'input-start'], self.events)

    def test_wrong_digest_or_missing_output_footer_never_completes(self):
        for failure in ('digest', 'boundary'):
            with self.subTest(failure=failure):
                self.setUp(); self.fail = failure
                with self.assertRaises(self.module.KnownExecFailure): self.input_execute()
                self.assertEqual('failed', self.store.value['phase'])
                self.assertIs(False, self.store.value['inputEvidence']['delivered'])

    def test_unsealed_source_rejects_before_creating_exec(self):
        with self.workspace.export_files(inputs=(self.source,), output=self.workspace / 'proof') as (source, output):
            with mock.patch.object(self.module, '_input_seals', return_value=False):
                with self.assertRaises(self.module.KnownExecFailure):
                    self.executor.execute('stdin-proof', self.container, output,
                        input_lease=source, input_sha256=self.sha)
        self.assertEqual([], self.events)

    def test_hash_size_and_profile_mismatch_reject_before_create(self):
        for profile, sha, size in (('stdin-proof', '0' * 64, len(self.data)),
                ('stdin-proof', True, len(self.data)), ('stdin-proof', self.sha, len(self.data) - 1),
                ('postgres-dump', self.sha, len(self.data))):
            with self.subTest(profile=profile, size=size):
                self.setUp()
                with self.workspace.export_files(inputs=(self.source,), output=self.workspace / 'proof') as (source, output):
                    source.max_file_bytes = size
                    with mock.patch.object(self.module, '_input_seals', return_value=True):
                        with self.assertRaises(self.module.KnownExecFailure):
                            self.executor.execute(profile, self.container, output,
                                input_lease=source, input_sha256=sha)
                    source.max_file_bytes = len(self.data)
                self.assertEqual([], self.events)



    def test_input_descriptor_replacement_is_detected_without_closing_foreign_fd(self):
        with self.workspace.export_files(inputs=(self.source,), output=self.workspace / 'proof') as (lease, output):
            with mock.patch.object(self.module, '_input_seals', return_value=True):
                source = self.module.SealedExecInput(lease, self.sha)
                import tempfile
                with tempfile.TemporaryFile() as foreign:
                    os.write(foreign.fileno(), self.data)
                    original = os.dup(lease.fd)
                    try:
                        os.dup2(foreign.fileno(), lease.fd, inheritable=False)
                        with self.assertRaises(self.module.ExecProtocolError): list(source.chunks())
                        source.close()
                        self.assertEqual(self.data, os.pread(lease.fd, len(self.data), 0))
                    finally:
                        os.dup2(original, lease.fd, inheritable=False); os.close(original)

    def test_terminal_identity_error_keeps_input_unconfirmed(self):
        self.api.inspect = lambda _: {'ID': self.exec_id, 'ContainerID': 'd' * 64,
                                     'Running': False, 'ExitCode': 0}
        with self.assertRaises(self.module.UncertainExec): self.input_execute()
        self.assertEqual('released', self.store.value['phase'])
        self.assertIs(False, self.store.value['inputEvidence']['delivered'])

    def test_duplex_protocol_rejects_200_and_closes_once(self):
        from types import SimpleNamespace
        closed, sent = [], []
        raw = SimpleNamespace(settimeout=lambda _: None, sendall=sent.append)
        response = SimpleNamespace(status=200, fp=object(), getheader=lambda _: None)
        connection = SimpleNamespace(sock=raw, request=lambda *a, **k: None,
            getresponse=lambda: response, close=lambda: closed.append(True))
        api = self.module.LocalDockerAPI(); api._connect = lambda: connection
        with mock.patch.object(self.module, '_input_seals', return_value=True):
            with self.source.export_for_child(write=False) as lease:
                source = self.module.SealedExecInput(lease, self.sha)
                try:
                    with self.assertRaises(OSError):
                        api.stream_input(self.exec_id, lambda _: None, 256, profile='stdin-proof', source=source)
                finally: source.close()
        self.assertEqual([], sent); self.assertEqual([True], closed)


    def test_duplex_preserves_buffered_frame_and_uses_one_budget_and_eof(self):
        import io
        from types import SimpleNamespace
        events, closed = [], []
        payload = b'ack'
        frame = bytes((1, 0, 0, 0)) + len(payload).to_bytes(4, 'big') + payload
        raw = SimpleNamespace(settimeout=lambda _: None, setblocking=lambda _: None,
            send=lambda block: (events.append(('send', len(block))) or len(block)),
            shutdown=lambda how: events.append(('eof', how)))
        response = SimpleNamespace(status=101, fp=io.BytesIO(frame),
            getheader=lambda name: 'tcp' if name == 'Upgrade' else None)
        connection = SimpleNamespace(sock=raw, request=lambda *a, **k: events.append(('start', 1)),
            getresponse=lambda: response, close=lambda: closed.append(True))
        api = self.module.LocalDockerAPI(); api._connect = lambda: connection
        with mock.patch.object(self.module, '_input_seals', return_value=True):
            with self.source.export_for_child(write=False) as lease:
                source = self.module.SealedExecInput(lease, self.sha)
                try:
                    output = []
                    class Poll:
                        def __enter__(self): return self
                        def __exit__(self, *args): pass
                        def register(self, *args): pass
                        def modify(self, *args): pass
                        def select(self, timeout):
                            eof = any(kind == 'eof' for kind, _ in events)
                            return [(None, self_module.selectors.EVENT_READ if eof else self_module.selectors.EVENT_WRITE)]
                    self_module = self.module
                    with mock.patch.object(self.module.selectors, 'DefaultSelector', Poll):
                        api.stream_input(self.exec_id, output.append, 256, profile='stdin-proof', source=source)
                    self.assertEqual([payload], output)
                finally: source.close()
        self.assertEqual(('start', 1), events[0])
        self.assertEqual(('eof', self.module.socket.SHUT_WR), events[-1])
        self.assertEqual(len(self.data), sum(size for kind, size in events if kind == 'send'))
        self.assertEqual([True], closed)

    def test_input_send_timeout_closes_once_and_does_not_retry(self):
        import io
        from types import SimpleNamespace
        attempts, closed = [], []
        def send(block):
            attempts.append(len(block)); raise TimeoutError('partial send')
        raw = SimpleNamespace(settimeout=lambda _: None, setblocking=lambda _: None, send=send)
        response = SimpleNamespace(status=101, fp=io.BytesIO(),
            getheader=lambda name: 'tcp' if name == 'Upgrade' else None)
        connection = SimpleNamespace(sock=raw, request=lambda *a, **k: None,
            getresponse=lambda: response, close=lambda: closed.append(True))
        api = self.module.LocalDockerAPI(); api._connect = lambda: connection
        with mock.patch.object(self.module, '_input_seals', return_value=True):
            with self.source.export_for_child(write=False) as lease:
                source = self.module.SealedExecInput(lease, self.sha)
                try:
                    class Poll:
                        def __enter__(self): return self
                        def __exit__(self, *args): pass
                        def register(self, *args): pass
                        def select(self, timeout): return [(None, self_module.selectors.EVENT_WRITE)]
                    self_module = self.module
                    with mock.patch.object(self.module.selectors, 'DefaultSelector', Poll):
                        with self.assertRaises(TimeoutError):
                            api.stream_input(self.exec_id, lambda _: None, 256, profile='stdin-proof', source=source)
                finally: source.close()
        self.assertEqual([65536], attempts); self.assertEqual([True], closed)


    def test_partial_send_and_input_close_failure_preserves_unknown_semantics(self):
        self.fail = 'partial'
        original = self.module.SealedExecInput.close
        calls = []
        def failed_close(source):
            calls.append(source.fd)
            original(source)  # Model an ambiguous OS close that did release.
            raise OSError('input pin close result unknown')
        with mock.patch.object(self.module.SealedExecInput, 'close', failed_close):
            with self.assertRaises(self.module.UncertainExec) as error:
                self.input_execute()
        self.assertIn('unknown', str(error.exception))
        self.assertEqual('unknown partial input', str(error.exception.__cause__))
        self.assertEqual(1, len(calls))
        self.assertEqual('released', self.store.value['phase'])
        self.assertIsNone(self.store.value['completionEvidence'])
        self.assertIs(False, self.store.value['inputEvidence']['delivered'])
        self.assertEqual(self.data, self.source.read_bytes())
        self.assertEqual(['create', 'input-start'], self.events)


class InputReceiptTest(unittest.TestCase):
    def test_v3_input_receipt_is_strict_and_unknown_never_counts_as_quiescent(self):
        import test_runtime_cohort as fixtures
        fixture = fixtures.RuntimeCohortTest()
        fixture.setUp()
        try:
            fixture.owner()
            fixture.receipt(schemaVersion=3, profile='stdin-proof',
                completionEvidence={'kind': 'exec-inspect-terminal-v1', 'ID': 'e' * 64,
                    'ContainerID': 'c' * 64, 'Running': False, 'ExitCode': 0},
                inputEvidence={'size': 42, 'sha256': 'a' * 64, 'delivered': True})
            name = 'runtime-exec-' + '00000000000040008000000000000001' + '.json'
            store = fixture.store_type(fixture.target, name=name)
            baseline = store.load()
            self.assertEqual('quiescent', fixture.probe())
            for patch in ({'size': True}, {'size': 0}, {'size': 2**30+1},
                    {'sha256': 'bad'}, {'delivered': 1}, {'delivered': False}, {'extra': True}):
                with self.subTest(patch=patch):
                    record = deepcopy(baseline); record['inputEvidence'].update(patch); store.save(record)
                    self.assertEqual('unknown', fixture.probe())
            record = deepcopy(baseline); record['phase'] = 'released'; store.save(record)
            self.assertEqual('unknown', fixture.probe())
        finally: fixture.doCleanups()

if __name__ == '__main__': unittest.main()
