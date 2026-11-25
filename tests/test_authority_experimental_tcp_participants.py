"""Real loopback TCP, synthetic clients and game; no native X4 or WAN."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from authority.checkpoints import CheckpointStore
from authority.experimental_local_host import ExperimentalLocalHost
from authority.experimental_tcp_job_delivery import ExperimentalTCPJobDelivery
from authority.experimental_tcp_control import ExperimentalTCPControl, offer_request
from authority.experimental_tcp_participants import ExperimentalTCPParticipants
from authority.save_transfer import SaveOffers
from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
from authority.trusted_marker_load_session import TrustedMarkerLoadSession
from authority.world import WorldCoordinator
from lockstep.launcher.joinfiles import FileService
from test_authority_experimental_local_host import Engine
from test_authority_trusted_marker_load_session import claim_for


class ParticipantTCPFixture(unittest.TestCase):
    retire_disconnected_on_resume = False
    retire_disconnected_on_pause = False

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); (root/'store').mkdir()
        self.store = CheckpointStore(root/'store')
        self.service = FileService('a'*32, bind='127.0.0.1', port=0, slots=(1,2)).start()
        self.addCleanup(self.service.thread.join, 2); self.addCleanup(self.service.close)
        self.engine = Engine(root)
        self.world = WorldCoordinator('a'*32, self.engine, self.store, SaveOffers(self.service, self.store))
        self.control = ExperimentalTCPControl(TrustedMarkerLoadSession(self.world, enabled=True), enabled=True)
        self.addCleanup(lambda: self.control.close() if not (self.control._closed or self.control._failed) else None)
        self.host = ExperimentalLocalHost(self.world, self.control, self.service, enabled=True)
        options = {}
        if self.retire_disconnected_on_resume:
            options['retire_disconnected_on_resume'] = True
        if self.retire_disconnected_on_pause:
            options['retire_disconnected_on_pause'] = True
        self.participants = ExperimentalTCPParticipants(self.host, enabled=True, timeout=.3, **options)
        self.world._participants = self.participants
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0)); self.listener.listen(3); self.addCleanup(self.listener.close)
        self.host.tick()

    def connect(self):
        client = socket.create_connection(self.listener.getsockname(), timeout=1)
        self.addCleanup(client.close)
        conn, _ = self.listener.accept(); self.host.adopt(conn)
        return client

    def offer_and_claim(self, client):
        self.host.tick()  # confirmed save
        client.sendall(offer_request()); self.host.tick(.1); self.host.tick(.1)
        bound = decode_bound_offer(client.recv(8192).rstrip(b'\n'))
        client.sendall(encode_load_claim(claim_for(bound.offer))+b'\n'); self.host.tick(.1)
        return bound.offer

    def responder(self, client, mode='ok', *, on_command=None):
        records = []; stop = threading.Event()
        self.receipt_sent = threading.Event()
        client.settimeout(.1)
        def run():
            data = b''
            while not stop.is_set():
                try: block = client.recv(8192)
                except socket.timeout: continue
                except OSError: break
                if not block: break
                data += block
                while b'\n' in data:
                    raw, data = data.split(b'\n', 1); item = json.loads(raw)
                    if item['kind'] == 'accepted': continue
                    records.append(item)
                    if on_command is not None:
                        on_command(item)
                    if mode == 'timeout' or (mode == 'pause_timeout' and item['operation'] == 'pause'):
                        continue
                    if mode == 'pause_eof' and item['operation'] == 'pause':
                        client.shutdown(socket.SHUT_WR)
                        return
                    if mode == 'disconnect': client.shutdown(socket.SHUT_RDWR); return
                    answer = dict(item, kind='participant_receipt', status='confirmed')
                    if mode == 'wrong_id' or (mode == 'pause_wrong_id' and item['operation'] == 'pause'):
                        answer['request_id'] += 1
                    if mode == 'bool_id': answer['request_id'] = True
                    if mode == 'wrong_epoch': answer['epoch'] = 'b'*32
                    payload = json.dumps(answer, separators=(',', ':')).encode()+b'\n'
                    if mode == 'duplicate': payload += payload
                    if mode == 'malformed_fin': payload = b'{"version":}\n'
                    try:
                        client.sendall(payload[:8])
                        if mode == 'incomplete_fin':
                            client.shutdown(socket.SHUT_WR)
                            return
                        client.sendall(payload[8:])
                        if mode in ('fin_after', 'malformed_fin'): client.shutdown(socket.SHUT_WR)
                        self.receipt_sent.set()
                    except OSError: return
        thread = threading.Thread(target=run); thread.start()
        def finish():
            stop.set(); thread.join(2)
            self.assertFalse(thread.is_alive())
        self.addCleanup(finish)
        return records


class ParticipantTCPTest(ParticipantTCPFixture):
    def test_pause_resume_and_socket_handle_binding(self):
        client = self.connect(); self.offer_and_claim(client)
        records = self.responder(client)
        self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'running')
        second = self.connect()  # a join requires pausing the existing client
        self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'saving')
        self.assertEqual([r['operation'] for r in records], ['resume','pause'])
        self.assertEqual([r['request_id'] for r in records], [1,2])
        self.assertEqual(self.engine.calls, ['pause','save','resume','pause'])
        second.close()

    def test_wrong_receipt_timeout_and_disconnect_fault_without_retry(self):
        # Each failure uses a fresh, separate host; unittest subtests cannot
        # share one after an intentionally indeterminate operation.
        for mode in ('wrong_id', 'bool_id', 'wrong_epoch', 'duplicate', 'timeout', 'disconnect', 'fin_after'):
            with self.subTest(mode=mode):
                with ParticipantCase(mode) as case:
                    case.exercise()

    def test_partial_two_client_resume_faults_and_repauses_server(self):
        alice = self.connect(); bob = self.connect()
        self.offer_and_claim(alice); a_records = self.responder(alice)
        self.offer_and_claim(bob); b_records = self.responder(bob, 'wrong_id')
        with self.assertRaises(ValueError): self.host.tick(.1)
        self.assertEqual([r['operation'] for r in a_records], ['resume'])
        self.assertEqual([r['operation'] for r in b_records], ['resume'])
        self.assertEqual(self.engine.calls, ['pause','save','resume','pause'])
        self.assertEqual(self.world.session.state, 'faulted')
        self.assertTrue(self.host._failed); self.assertTrue(self.participants._failed)

    def test_adapter_refuses_use_outside_active_host_operation(self):
        with self.assertRaises(ValueError): self.participants.resume((1,))
        self.assertFalse(self.participants._failed)

    def test_expired_select_never_sends_command(self):
        client = self.connect(); conn = next(iter(self.control._peers)); peer = self.control._peers[conn]
        with patch('authority.experimental_tcp_participants.time.monotonic', side_effect=(0, 2)), \
             patch('authority.experimental_tcp_participants.select.select', return_value=([], [conn], [])):
            with self.assertRaises(TimeoutError):
                self.participants._exchange(conn, peer, [], b'command\n', {}, 1)
        client.settimeout(.02)
        with self.assertRaises(socket.timeout): client.recv(1)


class ResumeDisconnectTest(ParticipantTCPFixture):
    retire_disconnected_on_resume = True

    def assert_departure_saved(self, initial):
        self.assertEqual(self.world.session.pending.operation, 'pause')
        self.assertEqual(self.world.session.players, ())
        self.assertEqual(len(self.control._peers), 0)
        self.host.tick()
        self.host.tick()
        self.assertEqual(self.world.session.state, 'paused')
        self.assertIsNone(self.world.session.pending)
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause', 'save'])
        saved = self.store.latest_marked_confirmed()
        self.assertNotEqual(initial.checkpoint_id, saved.checkpoint_id)
        return saved

    def test_last_pilot_closes_before_resume_command_is_written(self):
        pilot = self.connect()
        self.offer_and_claim(pilot)
        initial = self.store.latest_marked_confirmed()
        resume = self.engine.resume

        def close_after_native_resume():
            resume()
            pilot.shutdown(socket.SHUT_WR)

        with patch.object(self.engine, 'resume', side_effect=close_after_native_resume):
            self.host.tick(.1)
        self.assert_departure_saved(initial)
        with pilot.makefile('rb') as stream:
            self.assertEqual(json.loads(stream.readline())['kind'], 'accepted')
            self.assertEqual(stream.read(), b'')

    def test_last_pilot_closes_after_resume_command_and_rejoins_with_new_handle(self):
        pilot = self.connect()
        self.offer_and_claim(pilot)
        initial = self.store.latest_marked_confirmed()
        peer = next(iter(self.control._peers))
        handle = self.control._peers[peer]['handle']
        motion = Mock()
        self.control._motion_service = motion
        records = self.responder(pilot, 'disconnect')
        self.host.tick(.1)
        self.assertEqual([record['operation'] for record in records], ['resume'])
        motion.transport_lost.assert_called_once_with(handle)
        self.control._motion_service = None
        with self.assertRaisesRegex(ValueError, 'unknown or stale connection context'):
            self.control._session._peer(peer)
        saved = self.assert_departure_saved(initial)

        replacement = self.connect()
        replacement_handle = self.world.session.players[0][0]
        self.assertGreater(replacement_handle, handle)
        self.assertEqual(self.world.session.ready_players, ())
        replacement.sendall(offer_request())
        self.host.tick(.1)
        self.host.tick(.1)
        offer = decode_bound_offer(replacement.recv(8192).rstrip(b'\n')).offer
        self.assertEqual(offer.checkpoint_id, saved.checkpoint_id)
        self.assertEqual(offer.connection_handle, replacement_handle)
        with self.assertRaisesRegex(ValueError, 'unknown or stale connection handle'):
            self.world.disconnect(handle)
        self.assertEqual(self.world.session.players[0][0], replacement_handle)
        replacement.sendall(encode_load_claim(claim_for(offer)) + b'\n')
        self.host.tick(.1)
        records = self.responder(replacement)
        self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'running')
        self.assertEqual([record['connection_handle'] for record in records], [replacement_handle])

    def test_sibling_closes_while_survivor_confirms_resume(self):
        survivor = self.connect()
        leaving = self.connect()
        self.offer_and_claim(survivor)
        self.offer_and_claim(leaving)
        initial = self.store.latest_marked_confirmed()
        survivor_handle = self.world.session.ready_players[0][0]
        records = self.responder(survivor, on_command=lambda _: leaving.shutdown(socket.SHUT_WR))
        self.host.tick(.1)
        self.assertEqual([record['operation'] for record in records], ['resume'])
        self.assertEqual([handle for handle, _ in self.world.session.ready_players], [survivor_handle])
        self.assertEqual(self.world.session.state, 'running')
        self.assertIsNone(self.world.session.pending)
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume'])
        survivor.shutdown(socket.SHUT_WR)
        self.host.tick(.1)
        self.assertEqual(self.world.session.pending.operation, 'save')
        self.host.tick()
        self.assertEqual(self.world.session.state, 'paused')
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause', 'save'])
        self.assertNotEqual(initial.checkpoint_id, self.store.latest_marked_confirmed().checkpoint_id)

    def test_four_pilots_keep_two_survivors_when_two_connections_close_during_resume(self):
        pilots = [self.connect() for _ in range(4)]
        for pilot in pilots:
            self.offer_and_claim(pilot)
        handles = [handle for handle, _ in self.world.session.ready_players]
        initial = self.store.latest_marked_confirmed()
        first = self.responder(pilots[0], 'disconnect')
        second = self.responder(pilots[1], on_command=lambda _: pilots[3].shutdown(socket.SHUT_WR))
        third = self.responder(pilots[2])

        self.host.tick(.1)
        remaining = [handle for handle, _ in self.world.session.ready_players]
        self.assertEqual(remaining, handles[1:3])
        self.assertEqual(self.world.session.state, 'running')
        for handle, commands in zip(handles[:3], (first, second, third), strict=True):
            self.assertEqual([(cmd['connection_handle'], cmd['operation']) for cmd in commands],
                             [(handle, 'resume')])
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume'])
        self.assertEqual(self.store.latest_marked_confirmed(), initial)

        for pilot in pilots[1:3]:
            pilot.shutdown(socket.SHUT_WR)
        self.host.tick(.1)
        self.host.tick()
        self.assertEqual(self.world.session.state, 'paused')
        self.assertNotEqual(self.store.latest_marked_confirmed(), initial)
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause', 'save'])

    def test_last_pilot_closes_after_valid_resume_receipt(self):
        pilot = self.connect()
        self.offer_and_claim(pilot)
        initial = self.store.latest_marked_confirmed()
        peer = next(iter(self.control._peers))
        records = self.responder(pilot, 'fin_after')
        read = socket.socket.recv

        def read_receipt(sock, *args):
            data = read(sock, *args)
            if sock is peer and b'\n' in data:
                self.assertTrue(self.receipt_sent.wait(.2))
            return data

        with patch.object(socket.socket, 'recv', read_receipt):
            self.host.tick(.1)
        self.assertEqual([record['operation'] for record in records], ['resume'])
        self.assert_departure_saved(initial)

    def test_resume_still_fences_if_departing_pilot_had_delegated_authority(self):
        delivery = ExperimentalTCPJobDelivery(self.host, enabled=True)
        pilot = self.connect()
        self.offer_and_claim(pilot)
        handle = self.world.session.ready_players[0][0]
        delegated_owner = Mock()
        delivery._client_authority_selected_owners = {handle: delegated_owner}
        self.responder(pilot, 'disconnect')
        with self.assertRaises(RuntimeError):
            self.host.tick(.1)
        delegated_owner._revoke_for_disconnect.assert_called_once_with()
        self.assertTrue(delivery._failed)
        self.assertTrue(self.host._failed)
        self.assertEqual(self.world.session.state, 'faulted')
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause'])

    def test_opt_in_keeps_non_eof_and_invalid_receipts_fatal(self):
        for mode in ('wrong_id', 'wrong_epoch', 'duplicate', 'timeout', 'malformed_fin'):
            with self.subTest(mode=mode):
                with ParticipantCase(mode, retire_disconnected_on_resume=True) as case:
                    case.exercise()

    def test_legacy_resume_opt_in_keeps_pause_eof_strict(self):
        pilot = self.connect()
        self.offer_and_claim(pilot)
        records = self.responder(pilot, 'pause_eof')
        self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'running')
        self.connect()
        with self.assertRaisesRegex(RuntimeError, 'participant disconnected during operation'):
            self.host.tick(.1)
        self.assertEqual([record['operation'] for record in records], ['resume', 'pause'])
        self.assertEqual(self.world.session.state, 'faulted')

    def test_last_pilot_closes_after_partial_expected_resume_receipt(self):
        pilot = self.connect()
        self.offer_and_claim(pilot)
        initial = self.store.latest_marked_confirmed()
        handle = self.world.session.ready_players[0][0]
        motion = Mock()
        self.control._motion_service = motion
        records = self.responder(pilot, 'incomplete_fin')
        self.host.tick(.1)
        self.assert_departure_saved(initial)
        motion.transport_lost.assert_called_once_with(handle)
        self.assertEqual([record['operation'] for record in records], ['resume'])

    def interrupt_partial_resume_write(self, *, disconnect):
        pilot = self.connect()
        self.offer_and_claim(pilot)
        initial = self.store.latest_marked_confirmed()
        peer = next(iter(self.control._peers))
        handle = self.control._peers[peer]['handle']
        motion = Mock()
        self.control._motion_service = motion
        write = socket.socket.send
        resume_started = False
        writes = []
        resume = self.engine.resume

        def begin_resume():
            nonlocal resume_started
            resume()
            resume_started = True

        def fail_resume_write(sock, data, *args):
            if sock is not peer or not resume_started:
                return write(sock, data, *args)
            writes.append(data)
            if len(writes) == 2:
                if disconnect:
                    pilot.close()
                    raise BrokenPipeError('resume control socket closed')
                raise OSError('indeterminate resume command write')
            return write(sock, data[:8], *args)

        with patch.object(self.engine, 'resume', side_effect=begin_resume), \
                patch.object(socket.socket, 'send', fail_resume_write):
            if disconnect:
                self.host.tick(.1)
                self.assert_departure_saved(initial)
                motion.transport_lost.assert_called_once_with(handle)
            else:
                with self.assertRaisesRegex(OSError, 'indeterminate resume command write'):
                    self.host.tick(.1)
                self.assertEqual(self.world.session.state, 'faulted')
                self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause'])
                motion.transport_lost.assert_not_called()
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[1], writes[0][8:])

    def test_last_pilot_closes_after_partial_resume_command(self):
        self.interrupt_partial_resume_write(disconnect=True)

    def test_ambiguous_partial_resume_write_still_fences(self):
        self.interrupt_partial_resume_write(disconnect=False)

    def test_unsolicited_sibling_bytes_followed_by_fin_are_fatal(self):
        replying = self.connect()
        unexpected = self.connect()
        self.offer_and_claim(replying)
        self.offer_and_claim(unexpected)

        def send_unsolicited_data(_command):
            unexpected.sendall(b'{"version":1}\n')
            unexpected.shutdown(socket.SHUT_WR)

        self.responder(replying, on_command=send_unsolicited_data)
        with self.assertRaises((RuntimeError, ValueError)):
            self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'faulted')
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause'])


class PauseDisconnectTest(ParticipantTCPFixture):
    retire_disconnected_on_pause = True

    def running_pilot(self, mode='pause_eof'):
        pilot = self.connect()
        self.offer_and_claim(pilot)
        records = self.responder(pilot, mode)
        self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'running')
        return records

    def test_last_ready_eof_during_join_saves_before_new_pilot_can_resume(self):
        records = self.running_pilot()
        initial = self.store.latest_marked_confirmed()
        joining = self.connect()
        self.host.tick(.1)
        self.assertEqual([record['operation'] for record in records], ['resume', 'pause'])
        self.assertEqual(self.world.session.ready_players, ())
        self.assertEqual(len(self.control._peers), 1)
        self.assertEqual(self.world.session.pending.operation, 'save')
        self.assertEqual(self.store.latest_marked_confirmed(), initial)
        offer = self.offer_and_claim(joining)
        saved = self.store.latest_marked_confirmed()
        self.assertNotEqual(saved.checkpoint_id, initial.checkpoint_id)
        self.assertEqual((offer.checkpoint_id, offer.sha256), (saved.checkpoint_id, saved.sha256))
        self.assertEqual(self.world.session.pending.operation, 'resume')
        join_records = self.responder(joining)
        self.host.tick(.1)
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause', 'save', 'resume'])
        self.assertEqual([record['operation'] for record in join_records], ['resume'])
        self.assertEqual(self.world.session.state, 'running')

    def test_other_ready_pilot_still_confirms_pause(self):
        departing, survivor = self.connect(), self.connect()
        self.offer_and_claim(departing)
        self.offer_and_claim(survivor)
        departed_records = self.responder(departing, 'pause_eof')
        survivor_records = self.responder(survivor)
        self.host.tick(.1)
        joining = self.connect()
        self.host.tick(.1)
        self.assertEqual([record['operation'] for record in departed_records], ['resume', 'pause'])
        self.assertEqual([record['operation'] for record in survivor_records], ['resume', 'pause'])
        self.assertEqual(len(self.world.session.ready_players), 1)
        self.assertEqual(self.world.session.pending.operation, 'save')
        self.offer_and_claim(joining)
        join_records = self.responder(joining)
        self.host.tick(.1)
        self.assertEqual([record['operation'] for record in survivor_records], ['resume', 'pause', 'resume'])
        self.assertEqual([record['operation'] for record in join_records], ['resume'])

    def test_eof_during_pause_does_not_release_join_when_save_is_unconfirmed(self):
        self.running_pilot()
        initial = self.store.latest_marked_confirmed()
        joining = self.connect()
        records = self.responder(joining)
        self.host.tick(.1)
        self.assertEqual(self.world.session.pending.operation, 'save')
        with patch.object(self.engine, 'checkpoint_published', create=True, return_value=False):
            with self.assertRaisesRegex(ValueError, 'did not confirm checkpoint'):
                self.host.tick()
        self.assertEqual(self.world.session.state, 'faulted')
        self.assertEqual(self.world.session.ready_players, ())
        self.assertEqual(self.store.latest_marked_confirmed(), initial)
        self.assertEqual(records, [])

    def test_pause_timeout_still_fences_joining_pilot(self):
        self.running_pilot('pause_timeout')
        self.connect()
        with self.assertRaises(TimeoutError):
            self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'faulted')
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause'])


    def test_invalid_pause_receipt_still_fences_joining_pilot(self):
        self.running_pilot('pause_wrong_id')
        self.connect()
        with self.assertRaisesRegex(ValueError, 'unexpected participant receipt'):
            self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'faulted')
        self.assertEqual(self.engine.calls, ['pause', 'save', 'resume', 'pause'])


class ParticipantCase:
    """Reuse fixture lifecycle for one deliberate failure without re-running tests."""
    def __init__(self, mode, *, retire_disconnected_on_resume=False):
        self.mode = mode
        self.case = ParticipantTCPTest('test_adapter_refuses_use_outside_active_host_operation')
        self.case.retire_disconnected_on_resume = retire_disconnected_on_resume
    def __enter__(self): self.case.setUp(); return self
    def __exit__(self, *args): self.case.doCleanups()
    def exercise(self):
        c = self.case; client = c.connect(); c.offer_and_claim(client)
        records = c.responder(client, self.mode)
        original = c.participants._quiet
        def after_fin(peers, deadline, *, retire=None):
            c.assertTrue(c.receipt_sent.wait(.2))
            return original(peers, deadline, retire=retire)
        if self.mode == 'fin_after':
            with patch.object(c.participants, '_quiet', side_effect=after_fin):
                with c.assertRaises((ValueError, TimeoutError, RuntimeError)): c.host.tick(.1)
        else:
            with c.assertRaises((ValueError, TimeoutError, RuntimeError)): c.host.tick(.1)
        c.assertTrue(c.participants._failed); c.assertTrue(c.host._failed)
        c.assertEqual(c.world.session.state, 'faulted')
        c.assertEqual(c.engine.calls, ['pause','save','resume','pause'])
        c.assertEqual(len(records), 1)
        with c.assertRaises(RuntimeError): c.host.tick()


if __name__ == '__main__': unittest.main()
