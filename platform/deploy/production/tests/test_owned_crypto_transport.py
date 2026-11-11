"""No plaintext/key descriptors may cross spawn before the guard handshake."""
import importlib
import array
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class CryptoTransportTest(unittest.TestCase):
    def test_spawn_inherits_only_control_socket_and_never_plaintext_descriptors(self):
        module = importlib.import_module('owned_crypto')
        with tempfile.TemporaryFile() as first, tempfile.TemporaryFile() as second, \
             tempfile.TemporaryFile() as third, tempfile.TemporaryFile() as fourth:
            descriptors = (first.fileno(), second.fileno(), third.fileno(), fourth.fileno())
            pair = socket.socketpair
            # Packet transport model for macOS unit tests; production is Linux
            # SOCK_SEQPACKET and must fail closed if that facility is absent.
            with patch('socket.socketpair', side_effect=lambda *args, **kwargs: pair(socket.AF_UNIX, socket.SOCK_DGRAM)), \
                 patch.object(module.subprocess, 'Popen', return_value=Mock()) as spawned:
                process = module.LocalBackend.spawn({'pid': os.getpid(), 'start': '1'}, descriptors)
                try:
                    inherited = spawned.call_args.kwargs['pass_fds']
                    self.assertEqual(1, len(inherited))
                    self.assertFalse(set(inherited) & set(descriptors))
                    self.assertTrue(spawned.call_args.kwargs['close_fds'])
                finally:
                    close = getattr(process, 'close_control', None)
                    if close is not None: close()

    def test_no_readiness_cannot_receive_descriptors_and_handoff_follows_ready_packet(self):
        module = importlib.import_module('owned_crypto')
        pair = socket.socketpair
        for ready in (False, True):
            with tempfile.TemporaryFile() as first, tempfile.TemporaryFile() as second, \
                 tempfile.TemporaryFile() as third, tempfile.TemporaryFile() as fourth:
                descriptors = (first.fileno(), second.fileno(), third.fileno(), fourth.fileno())
                peer, sends = [], []
                class RecordedParent:
                    def __init__(self, raw): self.raw = raw
                    def __getattr__(self, name): return getattr(self.raw, name)
                    def sendmsg(self, *args):
                        sends.append(True)
                        return self.raw.sendmsg(*args)
                def packet_pair(*args, **kwargs):
                    parent, child = pair(socket.AF_UNIX, socket.SOCK_DGRAM)
                    return RecordedParent(parent), child
                fake = Mock(pid=123, returncode=0)
                fake.wait.return_value = 0
                def spawn(*args, **kwargs):
                    peer.append(socket.socket(fileno=os.dup(kwargs['pass_fds'][0])))
                    return fake
                with patch('socket.socketpair', side_effect=packet_pair), \
                     patch.object(module.subprocess, 'Popen', side_effect=spawn):
                    process = module.LocalBackend.spawn({'pid': os.getpid(), 'start': '1'}, descriptors)
                try:
                    peer[0].settimeout(.05)
                    with self.assertRaises(socket.timeout): peer[0].recvmsg(100, socket.CMSG_SPACE(16))
                    if not ready:
                        with self.assertRaises(module.subprocess.TimeoutExpired): process.communicate(input=b'{}', timeout=.05)
                        self.assertEqual([], sends)  # Stronger than platform-specific peer EOF/reset semantics.
                        continue
                    received, errors = [], []
                    def receive():
                        try:
                            peer[0].settimeout(1)
                            peer[0].send(module.GuardedCryptoProcess.READY)
                            data, messages, flags, address = peer[0].recvmsg(16384, socket.CMSG_SPACE(16))
                            delivered = array.array('i'); delivered.frombytes(messages[0][2])
                            try:
                                received.append((data, [os.fstat(fd).st_ino for fd in delivered]))
                            finally:
                                for fd in delivered: os.close(fd)
                        except Exception as exc: errors.append(type(exc).__name__)
                    thread = threading.Thread(target=receive)
                    thread.start()
                    process.communicate(input=b'{}', timeout=1)
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual([], errors)
                    self.assertEqual([True], sends)
                    self.assertEqual([(b'{}', [os.fstat(fd).st_ino for fd in descriptors])], received)
                finally:
                    process.close_control(); peer[0].close()


if __name__ == '__main__': unittest.main()
