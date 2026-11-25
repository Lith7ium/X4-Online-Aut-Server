"""Transfer deadlines with real local TCP; no X4 or external network."""
import hashlib
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from authority.save_transfer import SaveOffer, receive
from lockstep.launcher import joinfiles
from lockstep.launcher.joinfiles import FetchDeadline


class TransferDeadlineTests(unittest.TestCase):
    def slow_server(self, data, *, slow_header=False):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(2)
        stop = threading.Event()
        errors = []
        header = f'OK|{len(data)}|{hashlib.sha256(data).hexdigest()}\n'.encode('ascii')

        def serve():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(2)
                    request = b''
                    while not request.endswith(b'\n'):
                        part = conn.recv(512)
                        if not part:
                            return
                        request += part
                    first, slow = (b'', header) if slow_header else (header, data)
                    if first:
                        conn.sendall(first)
                    for byte in slow:
                        if stop.wait(.025):
                            return
                        conn.sendall(bytes([byte]))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # The client hung up at its deadline. Linux reports EPIPE/ECONNRESET
                # on the next send; Windows reports WSAECONNABORTED (10053).
                pass
            except OSError as error:
                errors.append(error)
            finally:
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        def cleanup():
            stop.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
        self.addCleanup(cleanup)
        return listener.getsockname()[1]

    def test_deadline_stops_active_header_and_body_without_publishing(self):
        data = bytes(range(128))
        digest = hashlib.sha256(data).hexdigest()
        offer = SaveOffer('j1.xml.gz', digest, len(data), 'checkpoint-' + 'd' * 32)
        for slow_header in (True, False):
            with self.subTest(slow_header=slow_header), tempfile.TemporaryDirectory() as root:
                port = self.slow_server(data, slow_header=slow_header)
                directory = Path(root) / 'owned-download'
                start = time.monotonic()
                with self.assertRaises(FetchDeadline):
                    receive('127.0.0.1', port, 'a' * 32, 1, offer, directory,
                            retries=0, timeout=2, deadline=start + .35)
                self.assertLess(time.monotonic() - start, 2)
                self.assertTrue(directory.is_dir())
                self.assertFalse((directory / 'save.xml.gz').exists())
                part = directory / 'save.xml.gz.part'
                if part.exists():
                    self.assertTrue(data.startswith(part.read_bytes()))

    def test_expired_deadline_does_not_replace_existing_file(self):
        from lockstep.launcher.joinfiles import fetch
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'save.xml.gz'
            target.write_bytes(b'original')
            with self.assertRaises(FetchDeadline):
                fetch('127.0.0.1', 1, 'a' * 32, 1, 'j1.xml.gz', target,
                      retries=0, deadline=time.monotonic() - 1)
            self.assertEqual(target.read_bytes(), b'original')

    def test_each_resolved_address_uses_remaining_connect_budget(self):
        now = [10.0]
        timeouts = []
        closed = []
        class FailedSocket:
            def __init__(self, *args): pass
            def settimeout(self, timeout): timeouts.append(timeout)
            def connect(self, address):
                now[0] += min(.2, timeouts[-1])
                raise OSError('unreachable')
            def close(self): closed.append(True)
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', n))
                     for n in (1, 2, 3)]
        with patch.object(joinfiles.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(joinfiles.socket, 'getaddrinfo', return_value=addresses), \
             patch.object(joinfiles.socket, 'socket', FailedSocket):
            with self.assertRaises(FetchDeadline):
                joinfiles._connect('example.invalid', 1, 2, 10.3)
        self.assertEqual(len(timeouts), 2)
        self.assertAlmostEqual(timeouts[0], .3)
        self.assertAlmostEqual(timeouts[1], .1)
        self.assertEqual(len(closed), 2)

    def test_connect_skips_unavailable_address_family(self):
        class ConnectedSocket:
            def settimeout(self, timeout): self.timeout = timeout
            def connect(self, address): self.address = address
        good = ConnectedSocket()
        addresses = [(socket.AF_INET6, socket.SOCK_STREAM, 0, '', ('::1', 1)),
                     (socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 1))]
        with patch.object(joinfiles.socket, 'getaddrinfo', return_value=addresses), \
             patch.object(joinfiles.socket, 'socket', side_effect=[OSError('unavailable'), good]):
            self.assertIs(joinfiles._connect('example.invalid', 1, 2, time.monotonic() + 3), good)
        self.assertEqual(good.address, ('127.0.0.1', 1))

    def test_expired_connect_does_not_begin_dns(self):
        with patch.object(joinfiles.socket, 'getaddrinfo') as resolve:
            with self.assertRaises(FetchDeadline):
                joinfiles._connect('example.invalid', 1, 2, time.monotonic() - 1)
        resolve.assert_not_called()

    def test_deadline_after_replace_can_leave_verified_target(self):
        data = b'checkpoint contents'
        digest = hashlib.sha256(data).hexdigest()
        offer = SaveOffer('j1.xml.gz', digest, len(data), 'checkpoint-' + 'd' * 32)
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'source'
            source.write_bytes(data)
            service = joinfiles.FileService('a' * 32, slots=(1,)).start()
            self.addCleanup(service.close)
            service.offer(offer.name, source)
            replaced = [False]
            original_replace = Path.replace
            def replace_then_expire(path, target):
                result = original_replace(path, target)
                replaced[0] = True
                return result
            def check(deadline):
                if replaced[0]:
                    raise FetchDeadline('simulated expiry after replace')
                return 2 if deadline is not None else None
            directory = root / 'owned-download'
            with patch.object(Path, 'replace', autospec=True, side_effect=replace_then_expire), \
                 patch.object(joinfiles, '_remaining', side_effect=check):
                with self.assertRaises(FetchDeadline):
                    receive('127.0.0.1', service.port, 'a' * 32, 1, offer, directory,
                            retries=0, deadline=time.monotonic() + 2)
            self.assertTrue(replaced[0])
            self.assertEqual((directory / 'save.xml.gz').read_bytes(), data)
            self.assertEqual(source.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
