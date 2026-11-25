"""Real loopback sockets and fake local inspect; no game or external network."""
import importlib.util
import json
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest import mock

from authority_socket_pair import non_ipv4_connected_pair

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('descriptor_loopback', ROOT / 'tools/dev/authority_descriptor_loopback.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def pair():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0)); listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=2)
    server, _ = listener.accept(); listener.close()
    return client, server


def desc():
    return dict(version=1, type='descriptor', attempt='a'*32, epoch='b'*32, seq=1, index=3,
                macro='ship_arg_s_fighter_01_a_macro', sector='sector_example_macro', owner='argon',
                seed=7183, x=-400, y=100000, z=500)


class Loopback(unittest.TestCase):
    def test_real_socket_inspect_precedes_ack(self):
        client, server = pair(); seen = []; errors = []
        def receive():
            try:
                got = m.receive_descriptor(server, 'a'*32, 'b'*32,
                    lambda d: seen.append(d) or True, time.monotonic()+3, lambda: True)
                seen.append(('completed', got))
            except BaseException as e: errors.append(e)
        thread = threading.Thread(target=receive); thread.start()
        self.assertTrue(m.send_descriptor(client, desc(), time.monotonic()+3, lambda: True))
        thread.join(3); self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(seen[0], desc()); self.assertEqual(seen[1], ('completed', desc()))
        self.assertEqual(client.fileno(), -1); self.assertEqual(server.fileno(), -1)

    def test_false_or_raising_inspect_sends_no_ack(self):
        for outcome in (False, 1, RuntimeError('not inspected')):
            with self.subTest(outcome=outcome):
                client, server = pair(); failures = []
                def inspect(_):
                    if isinstance(outcome, BaseException): raise outcome
                    return outcome
                def receive():
                    try: m.receive_descriptor(server, 'a'*32, 'b'*32, inspect, time.monotonic()+2, lambda: True)
                    except BaseException as e: failures.append(e)
                thread = threading.Thread(target=receive); thread.start()
                with self.assertRaises((ConnectionError, TimeoutError)):
                    m.send_descriptor(client, desc(), time.monotonic()+2, lambda: True)
                thread.join(2); self.assertFalse(thread.is_alive()); self.assertEqual(len(failures), 1)

    def test_malformed_foreign_extra_or_replayed_frame_no_ack(self):
        samples = [b'{"version":1,"version":1}\n', b'{"n":NaN}\n', b'x'*1025+b'\n']
        for field, value in [('attempt','c'*32), ('epoch','c'*32), ('index',4), ('seq',2), ('x',True), ('x',float('inf')), ('actor','42')]:
            d = desc(); d[field] = value; samples.append(json.dumps(d).encode()+b'\n')
        samples.append(json.dumps(desc()).encode()+b'\n'+json.dumps(desc()).encode()+b'\n')
        for data in samples:
            with self.subTest(data=data[:100]):
                client, server = pair(); called = []
                client.sendall(data)
                with self.assertRaises((ValueError, ConnectionError)):
                    m.receive_descriptor(server, 'a'*32, 'b'*32, lambda d: called.append(d) or True,
                                         time.monotonic()+2, lambda: True)
                client.settimeout(2)
                try: received = client.recv(2048)
                except ConnectionResetError: received = b''
                self.assertEqual(received, b''); self.assertEqual(called, []); client.close()

    def test_deadline_after_inspect_sends_no_ack(self):
        client, server = pair(); client.sendall(json.dumps(desc()).encode()+b'\n')
        def slow(_): time.sleep(.03); return True
        with self.assertRaises(TimeoutError):
            m.receive_descriptor(server, 'a'*32, 'b'*32, slow, time.monotonic()+.01, lambda: True)
        self.assertEqual(client.recv(1024), b''); client.close()

    def test_fake_process_loss_after_inspect_sends_no_ack(self):
        client, server = pair(); client.sendall(json.dumps(desc()).encode()+b'\n')
        processes = {'server': True, 'client': True}
        def inspect(_):
            processes['client'] = False
            return True
        def guard(): return all(processes.values())
        with self.assertRaisesRegex(RuntimeError, 'guard failed'):
            m.receive_descriptor(server, 'a'*32, 'b'*32, inspect, time.monotonic()+2, guard)
        self.assertEqual(client.recv(1024), b''); client.close()

    def test_guard_failure_during_blocked_receiver_and_sender_wait(self):
        for role in ('receiver', 'sender'):
            with self.subTest(role=role):
                client, server = pair()
                allowed = threading.Event(); allowed.set()
                waiting = threading.Event(); errors = []; inspected = []
                original_select = m.select.select
                watched = server if role == 'receiver' else client
                def observed_select(readable, writable, exceptional, timeout):
                    if watched in readable:
                        waiting.set()
                    return original_select(readable, writable, exceptional, timeout)
                def run():
                    try:
                        if role == 'receiver':
                            m.receive_descriptor(server, 'a'*32, 'b'*32,
                                lambda d: inspected.append(d) or True, time.monotonic()+5, allowed.is_set)
                        else:
                            m.send_descriptor(client, desc(), time.monotonic()+5, allowed.is_set)
                    except BaseException as e:
                        errors.append(e)
                thread = threading.Thread(target=run, daemon=True)
                try:
                    with mock.patch.object(m.select, 'select', side_effect=observed_select):
                        thread.start()
                        if role == 'sender':
                            server.settimeout(2)
                            data = b''
                            while not data.endswith(b'\n'):
                                chunk = server.recv(1024)
                                self.assertTrue(chunk)
                                data += chunk
                            self.assertEqual(m._decode(data[:-1]), desc())
                        self.assertTrue(waiting.wait(2))
                        allowed.clear()
                        thread.join(1.5)
                        self.assertFalse(thread.is_alive())
                    self.assertEqual(len(errors), 1)
                    self.assertIsInstance(errors[0], RuntimeError)
                    self.assertEqual(str(errors[0]), 'guard failed')
                    self.assertEqual(inspected, [])
                    self.assertEqual(watched.fileno(), -1)
                finally:
                    allowed.clear()
                    client.close(); server.close()
                    thread.join(2)

    def test_short_poll_does_not_replace_absolute_deadline(self):
        client, server = pair(); checks = []
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(TimeoutError, 'deadline expired'):
                m.receive_descriptor(server, 'a'*32, 'b'*32, lambda _: True,
                                     started + .36, lambda: checks.append(True) or True)
            self.assertGreaterEqual(time.monotonic() - started, .30)
            self.assertGreaterEqual(len(checks), 3)
            self.assertEqual(server.fileno(), -1)
        finally:
            client.close()

    def test_sender_rejects_stale_and_boolean_ack(self):
        for mutate in ({'seq': 2}, {'seq': True}, {'attempt':'f'*32}, {'extra':1}):
            with self.subTest(mutate=mutate):
                client, server = pair()
                def peer():
                    data = b''
                    while not data.endswith(b'\n'): data += server.recv(1)
                    ack = m._ack(desc()); ack.update(mutate)
                    server.sendall(json.dumps(ack).encode()+b'\n'); server.close()
                thread = threading.Thread(target=peer); thread.start()
                with self.assertRaises(ValueError): m.send_descriptor(client, desc(), time.monotonic()+2, lambda: True)
                thread.join(2); self.assertFalse(thread.is_alive())

    def test_no_non_ipv4_socket_or_nonbool_guard(self):
        client, server = non_ipv4_connected_pair()
        with self.assertRaises(ValueError): m.send_descriptor(client, desc(), time.monotonic()+2, lambda: True)
        server.close()
        client, server = pair()
        with self.assertRaises(RuntimeError): m.send_descriptor(client, desc(), time.monotonic()+2, lambda: 1)
        server.close()

if __name__ == '__main__': unittest.main()
