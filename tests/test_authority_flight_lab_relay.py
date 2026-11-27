"""Check the flight lab's link model against real sockets and the real movement transport."""
import json
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import threading
import time
import unittest

from authority_flight_lab_relay import PROFILES, ImpairedRelay, LinkModel, measure_motion_transport, path_model

ROOT = Path(__file__).resolve().parents[1]


def _server(handler):
    listener = socket.create_server(('127.0.0.1', 0))

    def serve():
        conn, _ = listener.accept()
        with conn:
            handler(conn)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener, thread


def _send_chunks(port, chunks, gap):
    client = socket.create_connection(('127.0.0.1', port))
    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    for chunk in chunks:
        client.sendall(chunk)
        time.sleep(gap)
    return client


class LinkModelTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_python_and_lua_models_agree(self):
        for profile in PROFILES:
            done = subprocess.run(['luajit', 'tools/dev/authority_flight_lab.lua', '--arrivals', profile, '5', '2000'],
                                  cwd=ROOT, capture_output=True, text=True, timeout=60, check=True)
            lua = json.loads(done.stdout)
            model = LinkModel(profile, 5)
            python = [model.arrival(i * .1) for i in range(2000)]
            self.assertEqual(len(lua), len(python))
            for i, (a, b) in enumerate(zip(lua, python)):
                self.assertAlmostEqual(a, b, delta=1e-9, msg=f'{profile} packet {i}')

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_python_and_lua_paths_agree(self):
        departures = [i * .1 for i in range(300)]
        for profile in PROFILES:
            done = subprocess.run(['luajit', 'tools/dev/authority_flight_lab.lua', '--path', profile, '5', '300'],
                                  cwd=ROOT, capture_output=True, text=True, timeout=60, check=True)
            lua = json.loads(done.stdout)
            python = path_model(profile, 5, departures)
            self.assertEqual([k for k, _ in lua], [k for k, _ in python], profile)
            for (_, a), (_, b) in zip(lua, python):
                self.assertAlmostEqual(a, b, delta=1e-9, msg=profile)


class ImpairedRelayTests(unittest.TestCase):
    def test_relay_keeps_bytes_and_order(self):
        def echo(conn):
            while data := conn.recv(65536):
                conn.sendall(data)

        listener, thread = _server(echo)
        relay = ImpairedRelay(listener.getsockname()[1], 'home', 1)
        try:
            chunks = [i.to_bytes(4, 'big') * 16 for i in range(300)]
            client = _send_chunks(relay.port, chunks, .005)
            expected, received = b''.join(chunks), b''
            client.settimeout(10)
            while len(received) < len(expected):
                data = client.recv(65536)
                self.assertTrue(data, 'relay closed early')
                received += data
            client.close()
            self.assertEqual(received, expected)
        finally:
            relay.close()
            listener.close()

    def test_relay_timing_follows_model(self):
        arrivals = []

        def sink(conn):
            buffer = b''
            while data := conn.recv(65536):
                now = time.monotonic()
                buffer += data
                while len(buffer) >= 32:
                    arrivals.append(now)
                    buffer = buffer[32:]

        listener, thread = _server(sink)
        relay = ImpairedRelay(listener.getsockname()[1], 'good', 1)
        try:
            client = _send_chunks(relay.port, [i.to_bytes(32, 'big') for i in range(200)], .01)
            client.close()
            thread.join(10)
            self.assertEqual(len(arrivals), 200)
            scheduled, offset = [], 0
            for direction, _received, released, nbytes in relay.log:
                if direction == 0:
                    scheduled.extend([released] * (nbytes // 32))
                    offset += nbytes
            self.assertEqual(offset, 200 * 32)
            self.assertLess(statistics.median(a - s for a, s in zip(arrivals, scheduled)), .010)
        finally:
            relay.close()
            listener.close()


class MotionTransportTests(unittest.TestCase):
    def test_motion_path_matches_model(self):
        """The real client and service over two home links deliver what the lab's path model says."""
        measured = measure_motion_transport('home', count=100)
        seen, queued = measured['seen'], measured['queued']
        self.assertEqual(sorted(seen, key=seen.get), sorted(seen), 'samples arrived out of order')
        departures = [i * .1 for i in range(100)]
        modelled = [path_model('home', seed * 10, departures) for seed in range(1, 6)]
        model_count = statistics.mean(len(run) for run in modelled)
        model_latency = statistics.median(arrival - departures[k - 1] for run in modelled for k, arrival in run)
        real_latency = statistics.median(seen[k] - queued[k] for k in seen)
        print(f'\nhome link: real {len(seen)} of 100 delivered, median {real_latency * 1000:.0f} ms; '
              f'model {model_count:.1f} of 100, median {model_latency * 1000:.0f} ms')
        self.assertLessEqual(abs(len(seen) - model_count), 12)
        self.assertLessEqual(abs(real_latency - model_latency), .060)


if __name__ == '__main__':
    unittest.main()
