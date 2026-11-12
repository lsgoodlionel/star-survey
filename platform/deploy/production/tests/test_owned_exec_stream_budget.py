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
                    status = 200
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
            status = 200
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
