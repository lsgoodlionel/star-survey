"""Real socket-pair transport model; no Docker or database qualification."""
import hashlib
import importlib
import io
import os
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_owned_memory import PortableFDBackend


class ExecInputBackpressureTest(unittest.TestCase):
    def test_consumer_can_emit_beyond_socket_buffer_before_reading_input(self):
        import owned_memory
        module = importlib.import_module('owned_docker_exec')
        client, server = socket.socketpair()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        server.settimeout(8)
        data, prefix = b'input\n' * 65536, b'bounded-output\n' * 32768
        observed, errors, closed = [], [], []
        def frame(channel, payload):
            return bytes((channel, 0, 0, 0)) + len(payload).to_bytes(4, 'big') + payload
        def consumer():
            try:
                server.sendall(frame(2, b'fixture-stderr\n' * 2048))
                server.sendall(frame(1, prefix))
                digest = hashlib.sha256()
                while True:
                    block = server.recv(65536)
                    if not block: break
                    digest.update(block)
                observed.append(digest.hexdigest())
                server.sendall(frame(1, b'end'))
                server.shutdown(socket.SHUT_WR)
            except BaseException as exc: errors.append(type(exc).__name__)
            finally: server.close()
        thread = threading.Thread(target=consumer); thread.start()
        response = SimpleNamespace(status=101, fp=client.makefile('rb'),
            getheader=lambda name: 'tcp' if name == 'Upgrade' else None)
        def close():
            closed.append(True); response.fp.close(); client.close()
        connection = SimpleNamespace(sock=client, request=lambda *a, **k: None,
            getresponse=lambda: response, close=close)
        api = module.LocalDockerAPI(); api._connect = lambda: connection
        try:
            with owned_memory.AnonymousWorkspace(_backend=PortableFDBackend()) as workspace:
                path = workspace / 'source'
                with path.open('xb') as handle: handle.write(data)
                with path.export_for_child(write=False) as lease:
                    with mock.patch.object(module, '_input_seals', return_value=True):
                        source = module.SealedExecInput(lease, hashlib.sha256(data).hexdigest())
                        try:
                            output = []
                            with mock.patch.dict(module.PROFILES, {'stdin-proof': (0, ['sha256sum'])}):
                                api.stream_duplex_input('e' * 64, output.append, len(prefix) + 3,
                                    profile='stdin-proof', source=source)
                            self.assertEqual(prefix + b'end', b''.join(output))
                        finally: source.close()
        finally:
            try: client.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            if not closed: close()
            thread.join(9)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual([hashlib.sha256(data).hexdigest()], observed)
        self.assertEqual([True], closed)


class RecoveryCohortNamespaceTest(unittest.TestCase):
    def test_explicit_recovery_scope_cannot_accept_production_or_another_copy(self):
        import test_runtime_cohort as fixtures
        import runtime_ownership as ownership
        fixture = fixtures.RuntimeCohortTest(); fixture.setUp()
        try:
            fixture.owner()
            project = 'survey-recovery-' + 'a' * 32
            store = fixture.store_type(fixture.target)
            record = store.load(); record['project'] = project; store.save(record)
            self.assertEqual('unknown', fixture.probe())
            scoped = fixture.module.CohortProbe(fixture.target, fixture.lock, lambda: True,
                api=fixture.api, fresh=True, project=project, _process_reader=lambda _: None)
            self.assertEqual('quiescent', scoped())
            with mock.patch.object(ownership, 'PROJECT', 'survey-recovery-' + 'b' * 32):
                self.assertEqual('quiescent', scoped())
            for other in ('survey-production', 'survey-recovery-' + 'b' * 32):
                record['project'] = other; store.save(record)
                self.assertEqual('unknown', scoped())
            with self.assertRaises(ownership.OwnershipError):
                fixture.module.CohortProbe(fixture.target, fixture.lock, lambda: True,
                    api=fixture.api, project='arbitrary')
        finally: fixture.doCleanups()


class InputFrameDecoderTest(unittest.TestCase):
    def test_fragmented_frame_and_stderr_discard_keep_exact_output(self):
        import owned_docker_exec as module
        output = []
        decoder = module._InputFrameDecoder(output.append, 3)
        data = b'\2\0\0\0' + (4).to_bytes(4, 'big') + b'omit'
        data += b'\1\0\0\0' + (3).to_bytes(4, 'big') + b'ack'
        for value in data: decoder.feed(bytes((value,)))
        decoder.finish()
        self.assertEqual(b'ack', b''.join(output))

    def test_bounds_malformed_and_truncated_frames_fail_closed(self):
        import owned_docker_exec as module
        for header in (b'\3\0\0\0' + (0).to_bytes(4, 'big'),
                       b'\1\1\0\0' + (0).to_bytes(4, 'big'),
                       b'\1\0\0\0' + (4).to_bytes(4, 'big'),
                       b'\2\0\0\0' + (65537).to_bytes(4, 'big')):
            with self.subTest(header=header):
                with self.assertRaises(module.ExecProtocolError):
                    module._InputFrameDecoder(lambda _: None, 3).feed(header)
        for partial in (b'\1', b'\1\0\0\0' + (3).to_bytes(4, 'big') + b'a'):
            decoder = module._InputFrameDecoder(lambda _: None, 3); decoder.feed(partial)
            with self.assertRaises(module.ExecProtocolError): decoder.finish()
        with self.assertRaises(module.ExecProtocolError): module._InputFrameDecoder(lambda _: None, True)

if __name__ == '__main__': unittest.main()
