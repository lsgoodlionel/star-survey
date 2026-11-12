"""Real private Unix socket; no Docker/production endpoint or credential."""
import http.client
import io
import importlib
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ExecStreamBudgetTest(unittest.TestCase):
    def test_fixed_profiles_own_bounded_read_budget_and_invalid_profile_cannot_start(self):
        module = importlib.import_module('owned_docker_exec')
        for profile, (seconds, _) in module.PROFILES.items():
            with self.subTest(profile=profile):
                timeouts, requests, closed = [], [], []
                class Socket:
                    def settimeout(self, value): timeouts.append(value)
                class Response(io.BytesIO):
                    status, chunked = 200, False
                    fp = io.BytesIO()
                    def getheader(self, name): return None
                class Connection:
                    sock = Socket()
                    def request(self, *args): requests.append(args)
                    def getresponse(self): return Response(b'\x01\0\0\0\0\0\0\x01x')
                    def close(self): closed.append(True)
                api = module.LocalDockerAPI(); api._connect = Connection
                output = []
                with patch.object(module.time, 'monotonic', return_value=100):
                    api.stream('e' * 64, output.append, 10, profile=profile)
                self.assertEqual(b'x', b''.join(output))
                self.assertTrue(timeouts); self.assertEqual({seconds + 5}, set(timeouts))
                self.assertEqual(1, len(requests)); self.assertEqual([True], closed)
        api = module.LocalDockerAPI()
        api._connect = lambda: self.fail('invalid profile opened a socket')
        for profile in (None, True, 'arbitrary', 120):
            with self.assertRaises(module.ExecProtocolError):
                api.stream('e' * 64, lambda value: None, 10, profile=profile)

    def test_trickling_frame_cannot_extend_profile_deadline_or_restart_exec(self):
        module = importlib.import_module('owned_docker_exec')
        clock, requests, closed = [100], [], []
        class Socket:
            def settimeout(self, value): pass
        class Response:
            status, chunked = 200, False
            fp = io.BytesIO()
            def getheader(self, name): return None
            def read1(self, size):
                clock[0] = 111  # exec-preflight has only a ten-second read budget.
                return b'\x01'
        class Connection:
            sock = Socket()
            def request(self, *args): requests.append(args)
            def getresponse(self): return Response()
            def close(self): closed.append(True)
        api = module.LocalDockerAPI(); api._connect = Connection
        with patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(module.DockerRequestTimeout) as caught:
                api.stream('e' * 64, lambda value: self.fail('partial frame exposed'), 10,
                           profile='exec-preflight')
        self.assertEqual('exec-stream', caught.exception.operation)
        self.assertEqual(1, len(requests)); self.assertEqual([True], closed)

    def test_unsupported_transfer_coding_is_refused_before_body_read(self):
        module = importlib.import_module('owned_docker_exec')
        for coding in ('gzip', 'identity'):
            with self.subTest(coding=coding):
                class WireSocket:
                    def makefile(self, mode):
                        return io.BytesIO(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: '
                            + coding.encode() + b'\r\n\r\n0\r\nSlow-Trailer: fixture\r\n\r\n')
                response = http.client.HTTPResponse(WireSocket()); response.begin()
                closed, requests = [], []
                class Socket:
                    def settimeout(self, value): pass
                class Connection:
                    sock = Socket()
                    def request(self, *args): requests.append(args)
                    def getresponse(self): return response
                    def close(self): closed.append(True); response.close()
                api = module.LocalDockerAPI(); api._connect = Connection
                with patch.object(response, 'read1', side_effect=AssertionError('unexpected body/trailer read')):
                    with self.assertRaises(OSError):
                        api.stream('e' * 64, lambda value: self.fail('unexpected output'), 10,
                                   profile='exec-preflight')
                self.assertEqual(1, len(requests)); self.assertEqual([True], closed)

    def test_chunked_internal_trailer_reads_share_absolute_deadline(self):
        module = importlib.import_module('owned_docker_exec')
        clock, closed, requests = [100], [], []
        class Wire(io.BytesIO):
            armed = False
            def read1(self, size):
                block = super().read1(size)
                if self.armed: clock[0] += len(block)
                return block
            def readline(self, limit=-1):
                block = super().readline(limit)
                if self.armed: clock[0] += len(block)
                return block
        wire = Wire(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'
                    b'0\r\nX: fixture\r\n\r\n')
        class Socket:
            def makefile(self, mode): return wire
            def settimeout(self, value): pass
        sock = Socket(); response = http.client.HTTPResponse(sock); response.begin()
        wire.armed = True
        class Connection:
            def __init__(self): self.sock = sock
            def request(self, *args): requests.append(args)
            def getresponse(self): return response
            def close(self): closed.append(True); response.close()
        api = module.LocalDockerAPI(); api._connect = Connection
        with patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(module.DockerRequestTimeout):
                api.stream('e' * 64, lambda value: self.fail('unexpected output'), 10,
                           profile='exec-preflight')
        self.assertLessEqual(clock[0], 110)
        self.assertEqual(1, len(requests)); self.assertEqual([True], closed)

    def test_flush_error_during_timeout_close_preserves_original_timeout_and_closes_buffer(self):
        module = importlib.import_module('owned_docker_exec')
        clock, calls = [100], []
        class Wire(io.BytesIO):
            def flush(self):
                calls.append('flush'); raise OSError('synthetic flush failure')
            def close(self): calls.append('buffer-close'); super().close()
        wire = Wire()
        class Socket:
            def settimeout(self, value): pass
        class Response(http.client.HTTPResponse):
            status, chunked = 200, False
            def read1(self, size): clock[0] = 111; return b''
            def getheader(self, name): return None
        # Use actual IOBase/HTTPResponse.close behavior without opening a socket.
        response = Response(type('SocketFile', (), {'makefile': lambda self, mode: wire})())
        response.status, response.chunked = 200, False
        class Connection:
            sock = Socket()
            def request(self, *args): pass
            def getresponse(self): return response
            def close(self): calls.append('connection-close'); response.close()
        api = module.LocalDockerAPI(); api._connect = Connection
        with patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(module.DockerRequestTimeout):
                api.stream('e' * 64, lambda value: self.fail('unexpected data'), 10, profile='exec-preflight')
        self.assertEqual(['connection-close', 'flush', 'buffer-close'], calls)
        self.assertTrue(wire.closed)

    def test_close_failure_after_eof_is_not_silent_success_or_retried(self):
        module = importlib.import_module('owned_docker_exec')
        calls = []
        class Socket:
            def settimeout(self, value): pass
        class Response(io.BytesIO):
            status, chunked = 200, False
            fp = io.BytesIO()
            def getheader(self, name): return None
        class Connection:
            sock = Socket()
            def request(self, *args): calls.append('start')
            def getresponse(self): return Response()
            def close(self): calls.append('close'); raise OSError('synthetic close failure')
        api = module.LocalDockerAPI(); api._connect = Connection
        with self.assertRaises(OSError):
            api.stream('e' * 64, lambda value: self.fail('unexpected data'), 10, profile='exec-preflight')
        self.assertEqual(['start', 'close'], calls)

    def test_late_eof_cannot_be_success_after_profile_deadline(self):
        module = importlib.import_module('owned_docker_exec')
        clock, closed = [100], []
        class Socket:
            def settimeout(self, value): pass
        class Response:
            status, chunked = 200, False
            fp = io.BytesIO()
            def getheader(self, name): return None
            def read1(self, size):
                clock[0] = 111
                return b''
        class Connection:
            sock = Socket()
            def request(self, *args): pass
            def getresponse(self): return Response()
            def close(self): closed.append(True)
        api = module.LocalDockerAPI(); api._connect = Connection
        with patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(module.DockerRequestTimeout):
                api.stream('e' * 64, lambda value: self.fail('unexpected output'), 10,
                           profile='exec-preflight')
        self.assertEqual([True], closed)

    def test_quiet_stream_can_exceed_connect_budget_inside_fixed_profile(self):
        module = importlib.import_module('owned_docker_exec')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'engine.sock')
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(server.close)
            server.bind(path); server.listen(1); server.settimeout(10)
            errors = []
            def serve():
                try:
                    client, _ = server.accept()
                    with client:
                        client.settimeout(10)
                        request = b''
                        while b'\r\n\r\n' not in request:
                            request += client.recv(4096)
                        client.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n')
                        time.sleep(5.5)  # Exceeds the actual five-second connect budget.
                        payload = b'synthetic'
                        client.sendall(b'\x01\0\0\0' + len(payload).to_bytes(4, 'big') + payload)
                except Exception as exc:
                    errors.append(type(exc).__name__)
            worker = threading.Thread(target=serve, daemon=True); worker.start()
            class Connection(http.client.HTTPConnection):
                def connect(self):
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(self.timeout); self.sock.connect(path)
            api = module.LocalDockerAPI()
            api._connect = lambda: Connection('localhost', timeout=5)
            output = []
            try:
                api.stream('e' * 64, output.append, 256, profile='engine-status')
            finally:
                worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertEqual([], errors)
            self.assertEqual(b'synthetic', b''.join(output))


if __name__ == '__main__': unittest.main()
