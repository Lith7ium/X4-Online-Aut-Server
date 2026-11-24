"""Local composition only: real loopback sockets with fake game controls."""
import json
import gzip
from pathlib import Path
import socket
import select
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests'), str(ROOT/'tools/dev')]
from authority.checkpoints import CheckpointStore
from authority.experimental_local_host import ExperimentalLocalHost
from authority.experimental_remote_load_client import ExperimentalRemoteLoadClient
from authority.experimental_tcp_control import ExperimentalTCPControl, offer_request
from authority.local_control_bridge import LocalControlBridge
from authority.save_transfer import SaveOffers
from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
from authority.trusted_marker_load_session import TrustedMarkerLoadSession
from authority.world import WorldCoordinator
from lockstep.launcher.joinfiles import FileService
from test_authority_trusted_marker_load_session import FakeEngine, claim_for, XML, MARKER
from authority_control_cycle_evidence import read_cycle_evidence


class Engine(FakeEngine):
    def __init__(self, root):
        super().__init__(root); self.calls = []
    def pause(self): self.calls.append('pause')
    def save(self): self.calls.append('save'); return super().save()
    def resume(self): self.calls.append('resume')


class Participants:
    def __init__(self): self.calls = []
    def pause(self, handles): self.calls.append(('pause', handles)); return True
    def resume(self, handles): self.calls.append(('resume', handles)); return True


class HostTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); (root/'store').mkdir()
        self.store = CheckpointStore(root/'store')
        self.service = FileService('a'*32, bind='127.0.0.1', port=0, slots=(1,)).start()
        self.addCleanup(self.service.thread.join, 2)
        self.addCleanup(self.service.close)
        self.engine = Engine(root); self.participants = Participants()
        self.world = WorldCoordinator('a'*32, self.engine, self.store, SaveOffers(self.service, self.store),
                                      participants=self.participants)
        self.control = ExperimentalTCPControl(TrustedMarkerLoadSession(self.world, enabled=True), enabled=True)
        self.addCleanup(lambda: self.control.close() if not (self.control._closed or self.control._failed) else None)
        self.host = ExperimentalLocalHost(self.world, self.control, self.service, enabled=True)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0)); self.listener.listen(1)
        self.addCleanup(self.listener.close)

    def test_pose_socket_wakes_six_idle_control_peers_without_consuming_the_datagram(self):
        remotes = []
        for _ in range(6):
            remote = socket.create_connection(self.listener.getsockname(), timeout=1)
            self.addCleanup(remote.close)
            local, _ = self.listener.accept()
            self.host.adopt(local, defer_attach=True)
            remotes.append(remote)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM) as sender:
            receiver.bind(('127.0.0.1', 0))
            receiver.setblocking(False)
            sender.sendto(b'pose reply', receiver.getsockname())
            with patch('authority.experimental_tcp_control.select.select', wraps=select.select) as wait:
                self.host.tick(.5, wake_reader=receiver)
            readers, writers, exceptional, timeout = wait.call_args.args
            self.assertEqual(set(readers), set(self.control._peers) | {receiver})
            self.assertNotIn(receiver, writers)
            self.assertNotIn(receiver, exceptional)
            self.assertEqual(timeout, .5)
            self.assertEqual(len(self.control._peers), 6)

            # A simultaneously ready control socket is serviced in the same tick.
            remotes[-1].shutdown(socket.SHUT_WR)
            self.host.tick(.5, wake_reader=receiver)
            self.assertEqual(len(self.control._peers), 5)
            self.assertEqual(receiver.recv(32), b'pose reply')

    def test_tcp_claim_ack_resume_and_last_disconnect_save(self):
        self.host.tick(); self.assertEqual(self.engine.calls, ['pause'])
        client = socket.create_connection(self.listener.getsockname(), timeout=1)
        self.addCleanup(client.close)
        conn, _ = self.listener.accept(); self.host.adopt(conn)
        self.host.tick(); self.assertEqual(self.engine.calls, ['pause', 'save'])
        client.sendall(offer_request()); self.host.tick(.1); self.host.tick(.1)
        bound = decode_bound_offer(client.recv(8192).rstrip(b'\n'))
        client.sendall(encode_load_claim(claim_for(bound.offer))+b'\n')
        self.host.tick(.1)
        self.assertEqual(self.engine.calls, ['pause', 'save'])  # ACK still queued
        self.host.tick(.1)
        self.assertEqual(json.loads(client.recv(8192))['kind'], 'accepted')
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume'])
        self.assertEqual(self.participants.calls[0][0], 'resume')
        client.shutdown(socket.SHUT_WR)
        self.host.tick(.1); self.host.tick(.1)
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause', 'save'])
        self.assertEqual(self.world.session.state, 'paused')
        self.assertEqual(self.world.session.players, ())
        self.assertIsNotNone(self.store.latest_marked_confirmed())

    def test_keepalive_during_client_wait_survives_idle_limit(self):
        # Real loopback sockets and file service; X4 observations are simulated.
        self.control._keepalive_enabled = True
        self.control._timeout = .4
        self.host.tick()
        remote = socket.create_connection(self.listener.getsockname(), timeout=2)
        conn, _ = self.listener.accept(); handle = self.host.adopt(conn)
        done = threading.Event(); release = threading.Event(); results = []; errors = []
        def run_client():
            client = accepted = None
            try:
                client = ExperimentalRemoteLoadClient(remote, enabled=True, file_host='127.0.0.1',
                    file_port=self.service.port, file_token='a'*32, file_slot=1,
                    deadline=time.monotonic()+6, io_timeout=1, keepalive_interval=.05)
                bound = client.request_offer()
                path = client.download(Path(self.temp.name)/'keepalive-download')
                client.verify_staged(path)
                attempt = bound.offer.attempt_id
                client.load_requested(attempt)
                until = time.monotonic() + 1.0
                while time.monotonic() < until:
                    time.sleep(.02); client.poll()
                client.loaded_marker(attempt, bound.marker); client.paused(attempt, True)
                client.claim(attempt); accepted = client.take_connection()
                results.append(path.read_bytes())
            except BaseException as error:
                errors.append(error)
            finally:
                done.set(); release.wait(3)
                if accepted is not None: accepted.close()
                if client is not None: client.close()
                else: remote.close()
        worker = threading.Thread(target=run_client, daemon=True); worker.start()
        try:
            end = time.monotonic() + 5
            while not done.is_set() and time.monotonic() < end:
                self.host.tick(.02)
            self.assertTrue(done.is_set())
            self.assertFalse(errors, repr(errors))
            self.assertEqual(len(results), 1)
            self.assertIn(handle, [player[0] for player in self.world.session.players])
        finally:
            release.set(); worker.join(4)
            self.assertFalse(worker.is_alive())

    def test_keepalive_during_slow_real_file_transfer_then_claim(self):
        # Delay the file body's first send, after the real FileService has
        # accepted and answered its request. The control and file sockets and
        # file bytes are real; X4 observations below are simulated.
        self.control._keepalive_enabled = True
        self.control._timeout = .8
        original_handle = self.service.handle
        class SlowBody:
            def __init__(self, conn): self.conn = conn; self.sends = 0
            def __getattr__(self, name): return getattr(self.conn, name)
            def sendall(self, data):
                self.sends += 1
                if self.sends == 2: time.sleep(2.0)
                return self.conn.sendall(data)
        self.service.handle = lambda conn, address: original_handle(SlowBody(conn), address)
        self.host.tick()
        remote = socket.create_connection(self.listener.getsockname(), timeout=2)
        conn, _ = self.listener.accept(); handle = self.host.adopt(conn)
        done = threading.Event(); release = threading.Event(); downloading = threading.Event()
        results = []; errors = []; observed_sequences = []
        def run_client():
            client = accepted = None
            try:
                client = ExperimentalRemoteLoadClient(remote, enabled=True, file_host='127.0.0.1',
                    file_port=self.service.port, file_token='a'*32, file_slot=1,
                    deadline=time.monotonic()+10, io_timeout=4, keepalive_interval=.1)
                bound = client.request_offer()
                downloading.set(); started = time.monotonic()
                path = client.download(Path(self.temp.name)/'slow-download')
                elapsed = time.monotonic()-started; downloading.clear()
                client.verify_staged(path)
                attempt = bound.offer.attempt_id
                client.load_requested(attempt); client.loaded_marker(attempt, bound.marker)
                client.paused(attempt, True); client.claim(attempt)
                accepted = client.take_connection()
                results.append((elapsed, path.read_bytes(), bound.offer.size))
            except BaseException as error:
                errors.append(error)
            finally:
                downloading.clear(); done.set(); release.wait(5)
                if accepted is not None: accepted.close()
                if client is not None: client.close()
                else: remote.close()
        worker = threading.Thread(target=run_client, daemon=True); worker.start()
        try:
            end = time.monotonic() + 9
            while not done.is_set() and time.monotonic() < end:
                self.host.tick(.02)
                if downloading.is_set() and conn in self.control._peers:
                    observed_sequences.append(self.control._peers[conn].get('keepalive_sequence', 0))
            self.assertTrue(done.is_set())
            self.assertFalse(errors, repr(errors))
            self.assertEqual(len(results), 1)
            self.assertGreater(results[0][0], self.control._timeout * 2)
            self.assertEqual(len(results[0][1]), results[0][2])
            self.assertGreaterEqual(max(observed_sequences, default=0), 2)
            self.assertIn(handle, [player[0] for player in self.world.session.players])
            report_deadline = time.monotonic() + 2
            while True:
                with self.service.lock:
                    reported = bool(self.service.served)
                if reported or time.monotonic() >= report_deadline: break
                time.sleep(.01)
            self.assertTrue(reported)
        finally:
            release.set(); worker.join(5)
            self.assertFalse(worker.is_alive())

    def test_uncertain_engine_error_poison_owner(self):
        def fail(): raise TimeoutError('uncertain engine state')
        self.engine.pause = fail
        with self.assertRaises(TimeoutError): self.host.tick()
        with self.assertRaisesRegex(RuntimeError, 'fence all'): self.host.tick()
        self.assertEqual(self.world.session.state, 'faulted')

    def test_slow_host_save_preserves_only_the_remaining_idle_time(self):
        clock = [100.0]
        with patch('time.monotonic', side_effect=lambda: clock[0]):
            self.host.tick()  # initial pause
            client = socket.create_connection(self.listener.getsockname(), timeout=1)
            self.addCleanup(client.close)
            conn, _ = self.listener.accept(); self.host.adopt(conn)
            peer = self.control._peers[conn]
            initial_deadline = peer['deadline']
            clock[0] += 4
            original_save = self.engine.save
            def slow_save():
                clock[0] += 40
                return original_save()
            self.engine.save = slow_save
            self.host.tick()
            self.assertEqual(peer['deadline'], initial_deadline + 40)
            self.assertEqual(peer['host_blocked_grace'], 40)
            self.assertIn(conn, self.control._peers)
            clock[0] = peer['deadline']
            self.control.poll()
            self.assertNotIn(conn, self.control._peers)

    def test_save_grace_does_not_hide_disconnect(self):
        clock = [100.0]
        with patch('time.monotonic', side_effect=lambda: clock[0]):
            self.host.tick()
            client = socket.create_connection(self.listener.getsockname(), timeout=1)
            self.addCleanup(client.close)
            conn, _ = self.listener.accept(); self.host.adopt(conn)
            original_save = self.engine.save
            def slow_save():
                client.shutdown(socket.SHUT_WR)
                clock[0] += 40
                return original_save()
            self.engine.save = slow_save
            self.host.tick()
            self.control.poll(.1)
            self.assertNotIn(conn, self.control._peers)
            self.assertEqual(self.world.session.players, ())

    def test_remote_client_is_admitted_after_save_exceeds_idle_timeout(self):
        # Real loopback control and file sockets, with fake engine/observations.
        # Keep the accepted control socket open until the owner checks state.
        self.control._timeout = 1.0
        self.host.tick()
        remote = socket.create_connection(self.listener.getsockname(), timeout=2)
        conn, _ = self.listener.accept(); handle = self.host.adopt(conn)
        start_client = threading.Event(); done = threading.Event(); release = threading.Event()
        results = []; errors = []; save_times = []
        original_save = self.engine.save
        def slow_save():
            started = time.monotonic()
            time.sleep(1.25)
            value = original_save()
            save_times.append(time.monotonic() - started)
            return value
        self.engine.save = slow_save
        def run_client():
            client = None; accepted = None
            try:
                client = ExperimentalRemoteLoadClient(remote, enabled=True, file_host='127.0.0.1',
                    file_port=self.service.port, file_token='a'*32, file_slot=1,
                    deadline=time.monotonic()+8, io_timeout=3)
                start_client.set()
                bound = client.request_offer()
                path = client.download(Path(self.temp.name)/'client-download')
                client.verify_staged(path)
                attempt = bound.offer.attempt_id
                client.load_requested(attempt)
                client.loaded_marker(attempt, bound.marker)
                client.paused(attempt, True)
                client.claim(attempt)
                accepted = client.take_connection()
                results.append((bound, path.read_bytes()))
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()
                release.wait(5)
                if accepted is not None: accepted.close()
                if client is not None: client.close()
                else: remote.close()
        worker = threading.Thread(target=run_client, daemon=True); worker.start()
        try:
            self.assertTrue(start_client.wait(2))
            self.host.tick()  # save blocks the socket pump longer than idle timeout
            end = time.monotonic() + 6
            while not done.is_set() and time.monotonic() < end:
                self.host.tick(.05)
            self.assertTrue(done.is_set())
            self.assertFalse(errors, repr(errors))
            self.assertEqual(len(results), 1)
            bound, downloaded = results[0]
            self.assertGreater(save_times[0], self.control._timeout)
            self.assertEqual(bound.offer.connection_handle, handle)
            self.assertEqual(downloaded, self.world._baseline[1].path.read_bytes())
            self.assertEqual(tuple(h for h, _ in self.world.session.ready_players), (handle,))
            self.assertEqual(self.world.session.state, 'running')
            self.assertGreater(self.control._peers[conn]['host_blocked_grace'], self.control._timeout)
        finally:
            release.set(); worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_synchronous_action_grace_is_cumulative_and_bounded(self):
        clock = [100.0]
        with patch('time.monotonic', side_effect=lambda: clock[0]):
            self.host.tick()
            client = socket.create_connection(self.listener.getsockname(), timeout=1)
            self.addCleanup(client.close)
            conn, _ = self.listener.accept(); self.host.adopt(conn)
            peer = self.control._peers[conn]
            original_deadline = peer['deadline']
            self.control._account_host_blocked(100, 300)
            self.control._account_host_blocked(300, 500)
            self.assertEqual(peer['host_blocked_grace'], 300)
            self.assertEqual(peer['deadline'], original_deadline + 300)
            clock[0] = peer['deadline']
            self.control.poll()
            self.assertNotIn(conn, self.control._peers)

    def test_peer_expiring_after_poll_before_dispatch_gets_no_grace(self):
        clock = [100.0]
        with patch('time.monotonic', side_effect=lambda: clock[0]):
            self.host.tick()
            client = socket.create_connection(self.listener.getsockname(), timeout=1)
            self.addCleanup(client.close)
            conn, _ = self.listener.accept(); self.host.adopt(conn)
            peer = self.control._peers[conn]
            self.control.poll()
            deadline = peer['deadline']
            self.control._account_host_blocked(deadline, deadline + 40)
            self.assertEqual(peer['deadline'], deadline)
            self.assertEqual(peer['host_blocked_grace'], 0)
            clock[0] = deadline + 40
            self.control.poll()
            self.assertNotIn(conn, self.control._peers)

    def test_failed_save_does_not_credit_a_peer_and_fences_host(self):
        clock = [100.0]
        with patch('time.monotonic', side_effect=lambda: clock[0]):
            self.host.tick()
            client = socket.create_connection(self.listener.getsockname(), timeout=1)
            self.addCleanup(client.close)
            conn, _ = self.listener.accept(); self.host.adopt(conn)
            peer = self.control._peers[conn]
            deadline = peer['deadline']
            def fail_save():
                clock[0] += 40
                raise TimeoutError('uncertain save')
            self.engine.save = fail_save
            with self.assertRaisesRegex(TimeoutError, 'uncertain save'): self.host.tick()
            self.assertEqual(peer['deadline'], deadline)
            self.assertEqual(peer['host_blocked_grace'], 0)
            with self.assertRaisesRegex(RuntimeError, 'fence all'): self.host.tick()

    def test_pause_failure_preserves_cause_when_offer_invalidation_fails(self):
        failure = TimeoutError('server pause not confirmed')
        def fail_pause():
            raise failure
        self.engine.pause = fail_pause
        self.world._load_offers[1] = object()
        with patch.object(self.world._offers, 'invalidate', side_effect=OSError('withdraw failed')) as invalidate, \
             patch.object(self.world.session, 'complete', wraps=self.world.session.complete) as complete:
            with self.assertRaises(TimeoutError) as raised:
                self.host.tick()
        self.assertIs(raised.exception, failure)
        self.assertEqual(failure.__notes__, ['offer invalidation also failed: OSError'])
        invalidate.assert_called_once_with()
        self.assertEqual(self.world._load_offers, {})
        complete.assert_called_once_with(self.world.session.world_epoch, 1, False)
        self.assertEqual(self.world.session.state, 'faulted')
        with self.assertRaisesRegex(RuntimeError, 'fence all'):
            self.host.tick()

    def test_pause_interruption_survives_both_cleanup_failures(self):
        failure = KeyboardInterrupt('server pause interrupted')
        def fail_pause():
            raise failure
        self.engine.pause = fail_pause
        calls = []
        def fail_invalidation():
            calls.append('invalidate')
            raise OSError('withdraw failed')
        def fail_fault_marking(*_args):
            calls.append('fault marking')
            raise RuntimeError('mark failed')
        with patch.object(self.world._offers, 'invalidate', side_effect=fail_invalidation), \
             patch.object(self.world.session, 'complete', side_effect=fail_fault_marking):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.host.tick()
        self.assertIs(raised.exception, failure)
        self.assertEqual(calls, ['invalidate', 'fault marking'])
        self.assertEqual(failure.__notes__, ['offer invalidation also failed: OSError',
                                              'session fault marking also failed: RuntimeError'])
        # Fault marking itself failed; the host fence is the remaining protection.
        with self.assertRaisesRegex(RuntimeError, 'fence all'):
            self.host.tick()

    def test_pause_failure_preserves_cause_when_fault_marking_fails(self):
        failure = TimeoutError('server pause not confirmed')
        def fail_pause():
            raise failure
        self.engine.pause = fail_pause
        with patch.object(self.world._offers, 'invalidate', wraps=self.world._offers.invalidate) as invalidate, \
             patch.object(self.world.session, 'complete', side_effect=RuntimeError('mark failed')) as complete:
            with self.assertRaises(TimeoutError) as raised:
                self.host.tick()
        self.assertIs(raised.exception, failure)
        self.assertEqual(failure.__notes__, ['session fault marking also failed: RuntimeError'])
        invalidate.assert_called_once_with()
        complete.assert_called_once_with(self.world.session.world_epoch, 1, False)
        with self.assertRaisesRegex(RuntimeError, 'fence all'):
            self.host.tick()

    def test_invalid_exception_notes_do_not_prevent_failure_fencing(self):
        failure = TimeoutError('server pause not confirmed')
        failure.__notes__ = 'invalid notes container'
        def fail_pause():
            raise failure
        self.engine.pause = fail_pause
        self.world._load_offers[1] = object()
        with patch.object(self.world._offers, 'invalidate', side_effect=OSError('withdraw failed')), \
             patch.object(self.world.session, 'complete', wraps=self.world.session.complete) as complete:
            with self.assertRaises(TimeoutError) as raised:
                self.host.tick()
        self.assertIs(raised.exception, failure)
        self.assertEqual(failure.__notes__, 'invalid notes container')
        self.assertEqual(self.world._load_offers, {})
        complete.assert_called_once_with(self.world.session.world_epoch, 1, False)
        self.assertEqual(self.world.session.state, 'faulted')
        with self.assertRaisesRegex(RuntimeError, 'fence all'):
            self.host.tick()

    def test_failed_load_offer_clear_does_not_skip_fault_marking(self):
        failure = TimeoutError('server pause not confirmed')
        class BrokenOffers(dict):
            def clear(self):
                raise OSError('clear failed')
        def fail_pause():
            raise failure
        self.engine.pause = fail_pause
        self.world._load_offers = BrokenOffers({1: object()})
        with patch.object(self.world._offers, 'invalidate', wraps=self.world._offers.invalidate) as invalidate, \
             patch.object(self.world.session, 'complete', wraps=self.world.session.complete) as complete:
            with self.assertRaises(TimeoutError) as raised:
                self.host.tick()
        self.assertIs(raised.exception, failure)
        self.assertEqual(failure.__notes__, ['load offer cleanup also failed: OSError'])
        invalidate.assert_called_once_with()
        complete.assert_called_once_with(self.world.session.world_epoch, 1, False)
        self.assertEqual(self.world.session.state, 'faulted')
        with self.assertRaisesRegex(RuntimeError, 'fence all'):
            self.host.tick()

    def test_engine_callback_cannot_reenter_host(self):
        self.engine.pause = lambda: self.host.tick()
        with self.assertRaisesRegex(RuntimeError, 'reentrant'):
            self.host.tick()
        with self.assertRaisesRegex(RuntimeError, 'fence all'): self.host.tick()
        self.assertEqual(self.world.session.state, 'faulted')

    def test_closed_control_faults_owner_and_requires_fencing(self):
        self.control.close()
        with self.assertRaisesRegex(RuntimeError, 'fence all'): self.host.tick()
        self.assertTrue(self.host._failed)

    def test_real_local_bridge_with_synthetic_udp_engine_and_tcp_client(self):
        profile = Path(self.temp.name)/'45841'; (profile/'save').mkdir(parents=True)
        bridge_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        bridge_socket.bind(('127.0.0.1', 0)); self.addCleanup(bridge_socket.close)
        engine_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        engine_socket.bind(('127.0.0.1', 0)); engine_socket.settimeout(.1)
        stop = threading.Event(); commands = []
        def fake_engine():
            while not stop.is_set():
                try: raw, peer = engine_socket.recvfrom(768)
                except socket.timeout: continue
                fields = raw.decode('ascii').split('|'); commands.append(fields[-1])
                if fields[0] == 'VERIFY':
                    reply = 'VERIFIED|'+'|'.join(fields[1:])
                else:
                    _, lease, epoch, request, operation, *marker = fields
                    if operation == 'save':
                        title = 'X4 Authority '+epoch+' '+request
                        xml = XML.replace(MARKER, marker[0]).replace('<savegame>',
                            '<savegame><info><save name="'+title+'"/><game time="12"/></info>')
                        (profile/'save/save_009.xml.gz').write_bytes(gzip.compress(xml.encode()))
                    status = 'native_event_only' if operation == 'save' else 'confirmed'
                    reply = '|'.join(('RECEIPT',lease,epoch,request,operation,status))
                engine_socket.sendto(reply.encode('ascii'), peer)
        thread = threading.Thread(target=fake_engine); thread.start()
        def stop_engine():
            stop.set(); thread.join(2); engine_socket.close()
            self.assertFalse(thread.is_alive())
        self.addCleanup(stop_engine)
        self.world._engine = LocalControlBridge(enabled=True, sock=bridge_socket,
            peer=engine_socket.getsockname(), lease='b'*32, world_epoch='a'*32,
            profile=profile, store=self.store, verify_checkpoint=read_cycle_evidence,
            timeout=2, marker_required=True)
        self.host.tick()
        client = socket.create_connection(self.listener.getsockname(), timeout=1); self.addCleanup(client.close)
        conn, _ = self.listener.accept(); self.host.adopt(conn); self.host.tick()
        client.sendall(offer_request()); self.host.tick(.1); self.host.tick(.1)
        bound = decode_bound_offer(client.recv(8192).rstrip(b'\n'))
        claim = claim_for(bound.offer)
        from dataclasses import replace
        claim = replace(claim, marker=bound.marker)
        client.sendall(encode_load_claim(claim)+b'\n'); self.host.tick(.1); self.host.tick(.1)
        self.assertEqual(json.loads(client.recv(8192))['kind'], 'accepted')
        self.assertEqual(self.world.session.state, 'running')
        client.shutdown(socket.SHUT_WR); self.host.tick(.1); self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'paused')
        self.assertEqual(len(commands), 7)  # pause, save+verify, resume, pause, save+verify
        self.assertIsNotNone(self.store.latest_marked_confirmed())

    def test_service_and_control_must_belong_to_same_world(self):
        with self.assertRaises(ValueError):
            ExperimentalLocalHost(self.world, self.control, self.service)
        other = WorldCoordinator('b'*32, self.engine, self.store, SaveOffers(self.service, self.store))
        with self.assertRaises(ValueError):
            ExperimentalLocalHost(other, self.control, self.service, enabled=True)


if __name__ == '__main__': unittest.main()
