"""Real loopback TCP with a fake engine and fake checkpoint service; no X4/WAN."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from authority.checkpoints import CheckpointStore
from authority.company_ships import CompanyShip
from authority.experimental_tcp_control import ExperimentalTCPControl, offer_request
from authority.experimental_remote_load_client import ExperimentalRemoteLoadClient
from authority.save_transfer import SaveOffers
from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
from authority.trusted_marker_load_session import TrustedMarkerLoadSession
from authority.world import WorldCoordinator
from test_authority_trusted_marker_load_session import FakeEngine, FakeService, claim_for


class TCPControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); (root / 'store').mkdir()
        self.service = FakeService(); self.store = CheckpointStore(root / 'store')
        self.world = WorldCoordinator('epoch-1', FakeEngine(root), self.store, SaveOffers(self.service, self.store))
        self.read_company_ships = Mock(return_value=(
            CompanyShip(7, '[0x345]', 'sector_01_macro', 'Scout', 'ABC-123'),
        ))
        self.session = TrustedMarkerLoadSession(
            self.world, enabled=True, company_ship_reader=self.read_company_ships,
        )
        self.owner = ExperimentalTCPControl(self.session, enabled=True, idle_timeout=3)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0)); self.listener.listen(8)
        self.clients = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if not self.owner._closed and not self.owner._failed: self.owner.close()
        for conn in self.clients: conn.close()
        self.listener.close()

    def connect(self):
        remote = socket.create_connection(self.listener.getsockname(), timeout=1)
        remote.settimeout(1); local, _ = self.listener.accept()
        self.owner.adopt(local); self.clients.append(remote)
        return remote

    def ready_world(self):
        self.world.step(); self.world.step()
        self.assertEqual(self.world.session.state, 'paused')

    def deliver(self, peer, data):
        peer.sendall(data)
        self.owner.poll(.1)

    def output(self, peer):
        self.owner.poll(.1)
        with peer.makefile('rb') as reader:
            value = reader.readline(8193)
        self.assertTrue(value.endswith(b'\n'))
        return value[:-1]

    def offer(self, peer):
        self.deliver(peer, offer_request())
        return decode_bound_offer(self.output(peer))

    @staticmethod
    def company_request(kind, offer, **fields):
        request = {'version': 1, 'kind': kind, 'attempt_id': offer.attempt_id, **fields}
        return json.dumps(request).encode('ascii') + b'\n'

    def test_company_ship_choice_is_exclusive_and_released_on_disconnect(self):
        alice, bob = self.connect(), self.connect()
        self.ready_world()
        offers = {alice: self.offer(alice).offer, bob: self.offer(bob).offer}
        public_ship = {'ship_index': 7, 'sector_macro': 'sector_01_macro',
                       'name': 'Scout', 'code': 'ABC-123'}

        for peer, offer in offers.items():
            self.deliver(peer, self.company_request('list_company_ships', offer, after_index=0))
            self.assertEqual(json.loads(self.output(peer)), {
                'version': 1, 'kind': 'company_ships', 'attempt_id': offer.attempt_id,
                'ships': [public_ship], 'next_index': None,
            })
        self.read_company_ships.assert_called_once_with(self.store.latest_marked_confirmed())

        self.deliver(bob, self.company_request('choose_company_ship', offers[alice], ship_index=7))
        self.assertEqual(json.loads(self.output(bob)), {
            'version': 1, 'kind': 'stale_ship_choice', 'attempt_id': offers[alice].attempt_id,
        })

        # Queue both requests on different live sockets before pumping the host.
        for peer, offer in offers.items():
            peer.sendall(self.company_request('choose_company_ship', offer, ship_index=7))
        self.owner.poll(.1)
        replies = {peer: json.loads(self.output(peer)) for peer in offers}
        self.assertCountEqual([reply['kind'] for reply in replies.values()],
                              ['company_ship_chosen', 'company_ship_unavailable'])
        winner = next(peer for peer, reply in replies.items() if reply['kind'] == 'company_ship_chosen')
        waiting = bob if winner is alice else alice
        winner_offer, waiting_offer = offers[winner], offers[waiting]
        self.assertEqual(replies[winner], {
            'version': 1, 'kind': 'company_ship_chosen', 'attempt_id': winner_offer.attempt_id,
            'ship': public_ship,
        })
        unavailable = {'version': 1, 'kind': 'company_ship_unavailable',
                       'attempt_id': waiting_offer.attempt_id}
        self.assertEqual(replies[waiting], unavailable)
        self.assertEqual(self.world.session.ready_players, ())

        self.deliver(winner, encode_load_claim(claim_for(winner_offer)) + b'\n')
        self.assertEqual(json.loads(self.output(winner)), {
            'version': 1, 'kind': 'accepted', 'attempt_id': winner_offer.attempt_id,
        })
        self.assertEqual([handle for handle, _ in self.world.session.ready_players],
                         [winner_offer.connection_handle])
        choose = self.company_request('choose_company_ship', waiting_offer, ship_index=7)
        self.deliver(waiting, choose)
        self.assertEqual(json.loads(self.output(waiting)), unavailable)

        winner.shutdown(socket.SHUT_WR)
        self.owner.poll(.1)
        self.assertEqual(winner.recv(1), b'')
        self.deliver(waiting, choose)
        self.assertEqual(json.loads(self.output(waiting)), {
            'version': 1, 'kind': 'company_ship_chosen', 'attempt_id': waiting_offer.attempt_id,
            'ship': public_ship,
        })

    def test_opt_in_keepalive_ack_is_bound_to_attempt_and_sequence(self):
        self.owner._keepalive_enabled = True
        peer = self.connect(); self.ready_world(); bound = self.offer(peer)
        record = next(iter(self.owner._peers.values()))
        record['deadline'] = time.monotonic() + .05
        frame = lambda seq, attempt: json.dumps({'version': 1, 'kind': 'keepalive',
            'attempt_id': attempt, 'sequence': seq}).encode() + b'\n'
        self.deliver(peer, frame(1, bound.offer.attempt_id))
        self.assertEqual(json.loads(self.output(peer)), {'version': 1, 'kind': 'alive',
            'attempt_id': bound.offer.attempt_id, 'sequence': 1})
        self.assertGreater(record['deadline'], time.monotonic() + 1)
        self.deliver(peer, frame(2, bound.offer.attempt_id))
        self.assertEqual(json.loads(self.output(peer))['sequence'], 2)
        self.deliver(peer, frame(2, bound.offer.attempt_id))
        self.assertEqual(peer.recv(1), b'')
        self.assertEqual(self.world.session.players, ())

    def test_keepalive_wrong_stage_attempt_and_default_disabled_disconnect(self):
        frame = lambda attempt: json.dumps({'version': 1, 'kind': 'keepalive',
            'attempt_id': attempt, 'sequence': 1}).encode() + b'\n'
        self.owner._keepalive_enabled = True
        peer = self.connect(); self.deliver(peer, frame('old'))
        self.assertEqual(peer.recv(1), b'')
        peer = self.connect(); self.ready_world(); self.offer(peer)
        self.deliver(peer, frame('old')); self.assertEqual(peer.recv(1), b'')
        self.owner._keepalive_enabled = False
        peer = self.connect(); bound = self.offer(peer)
        self.deliver(peer, frame(bound.offer.attempt_id)); self.assertEqual(peer.recv(1), b'')

    def test_host_owned_offer_and_accept_checks_receive_bounded_credit(self):
        peer = self.connect(); self.ready_world()
        record = next(iter(self.owner._peers.values()))
        begin = self.session.begin
        def delayed_begin(conn):
            time.sleep(.06)
            return begin(conn)
        record['deadline'] = time.monotonic() + .04
        with patch.object(self.session, 'begin', side_effect=delayed_begin):
            bound = self.offer(peer)
        self.assertGreater(record['host_blocked_grace'], .05)
        self.assertGreater(record['deadline'], time.monotonic())
        accept = self.session.accept
        def delayed_accept(conn, claim, *, pre_accept):
            time.sleep(.06)
            return accept(conn, claim, pre_accept=pre_accept)
        record['deadline'] = time.monotonic() + .04
        with patch.object(self.session, 'accept', side_effect=delayed_accept):
            self.deliver(peer, encode_load_claim(claim_for(bound.offer)) + b'\n')
        self.assertEqual(json.loads(self.output(peer))['kind'], 'accepted')
        self.assertGreater(record['host_blocked_grace'], .11)

    def test_fragmented_request_claim_and_disconnect_are_socket_bound(self):
        peer = self.connect(); self.ready_world()
        request = offer_request(); self.deliver(peer, request[:9]); self.deliver(peer, request[9:])
        bound = decode_bound_offer(self.output(peer))
        self.deliver(peer, encode_load_claim(claim_for(bound.offer)) + b'\n')
        ack = json.loads(self.output(peer))
        self.assertEqual(ack, {'version': 1, 'kind': 'accepted', 'attempt_id': bound.offer.attempt_id})
        self.assertEqual(len(self.world.session.ready_players), 1)
        self.read_company_ships.assert_not_called()
        # Duplicate claims cannot add another player and disconnect the context.
        self.deliver(peer, encode_load_claim(claim_for(bound.offer)) + b'\n')
        self.assertEqual(self.world.session.players, ())
        self.assertEqual(peer.recv(1), b'')

    def test_early_request_reports_not_ready_then_can_retry(self):
        peer = self.connect()
        self.deliver(peer, offer_request())
        self.assertEqual(json.loads(self.output(peer)), {'version': 1, 'kind': 'not_ready'})
        self.assertFalse(self.owner._failed)
        self.assertEqual(len(self.world.session.players), 1)
        self.ready_world()
        bound = self.offer(peer)
        self.assertEqual(bound.offer.connection_handle, self.world.session.players[0][0])

    def test_remote_client_retries_while_world_is_preparing(self):
        peer = self.connect()
        handle = self.world.session.players[0][0]
        done = threading.Event(); result = []; errors = []
        def request():
            client = ExperimentalRemoteLoadClient(peer, enabled=True, file_host='127.0.0.1',
                file_port=1, file_token='a'*32, file_slot=1, deadline=time.monotonic()+3, io_timeout=2)
            try: result.append(client.request_offer())
            except BaseException as error: errors.append(error)
            finally:
                client.close(); done.set()
        worker = threading.Thread(target=request, daemon=True); worker.start()
        self.addCleanup(lambda: worker.join(3))
        self.owner.poll(.5)  # request before pause/save has completed
        self.assertEqual(next(iter(self.owner._peers.values()))['out'],
                         b'{"version":1,"kind":"not_ready"}\n')
        self.owner.poll(.5)  # send not_ready
        self.ready_world()
        end = time.monotonic()+2
        while not done.is_set() and time.monotonic()<end:
            self.owner.poll(.05)
        worker.join(1)
        self.assertFalse(errors)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0].offer.connection_handle, handle)

    def test_resume_gate_requires_drained_ack_and_fences_eof(self):
        peer = self.connect(); self.ready_world(); bound = self.offer(peer)
        self.deliver(peer, encode_load_claim(claim_for(bound.offer)) + b'\n')
        self.assertEqual(len(self.world.session.ready_players), 1)
        self.assertFalse(self.owner.ready_for_resume())
        self.assertEqual(json.loads(self.output(peer))['kind'], 'accepted')
        self.assertTrue(self.owner.ready_for_resume())
        peer.shutdown(socket.SHUT_WR)
        self.assertFalse(self.owner.ready_for_resume())
        self.assertEqual(self.world.session.ready_players, ())
        self.world.step()  # unstarted resume is cancelled by coordinator
        self.assertNotEqual(self.world.session.state, 'running')

    def test_wrong_peer_and_old_attempt_never_become_ready(self):
        alice, bob = self.connect(), self.connect(); self.ready_world()
        a = self.offer(alice); self.offer(bob)
        self.deliver(bob, encode_load_claim(claim_for(a.offer)) + b'\n')
        self.assertEqual(self.world.session.ready_players, ())
        self.assertEqual(len(self.world.session.players), 1)
        new = self.offer(alice)
        self.assertNotEqual(a.offer.attempt_id, new.offer.attempt_id)
        self.deliver(alice, encode_load_claim(claim_for(a.offer)) + b'\n')
        self.assertEqual(self.world.session.ready_players, ())
        self.assertEqual(self.world.session.players, ())

    def test_invalid_json_and_oversized_frame_disconnect(self):
        a, b = self.connect(), self.connect(); self.ready_world()
        self.deliver(a, b'{"version":1,"version":1,"kind":"offer_request"}\n')
        self.deliver(b, b'x' * 4097)
        self.assertEqual(self.world.session.players, ())
        self.assertEqual(self.world.session.ready_players, ())

    def test_peer_fin_before_claim_fences_attempt(self):
        peer = self.connect(); self.ready_world(); bound = self.offer(peer)
        peer.sendall(encode_load_claim(claim_for(bound.offer)) + b'\n')
        peer.shutdown(socket.SHUT_WR)
        self.owner.poll(.1)
        self.assertEqual(self.world.session.ready_players, ())
        self.assertEqual(self.world.session.players, ())

    def test_expired_partial_request_is_disconnected(self):
        peer = self.connect(); self.ready_world()
        self.deliver(peer, b'{')
        record = next(iter(self.owner._peers.values()))
        deadline = record['deadline']
        self.deliver(peer, b'"')
        self.assertEqual(record['deadline'], deadline)  # trickling is not a timeout reset
        record['deadline'] = 0
        self.owner.poll(0)
        self.assertEqual(self.world.session.players, ())
        self.assertEqual(self.world.session.ready_players, ())

    def test_reconnect_cannot_replay_the_previous_connection_claim(self):
        old_peer = self.connect(); self.ready_world(); old = self.offer(old_peer)
        old_peer.close(); self.owner.poll(.1)
        new_peer = self.connect(); new = self.offer(new_peer)
        self.assertNotEqual(old.offer.connection_handle, new.offer.connection_handle)
        self.deliver(new_peer, encode_load_claim(claim_for(old.offer)) + b'\n')
        self.assertEqual(self.world.session.ready_players, ())
        self.assertEqual(self.world.session.players, ())

    def test_partial_admission_error_faults_and_fences_all_sockets(self):
        peer = self.connect(); self.ready_world(); bound = self.offer(peer)
        original = self.world.mark_loaded_trusting_marker
        def partial(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('indeterminate after mutation')
        with patch.object(self.world, 'mark_loaded_trusting_marker', side_effect=partial):
            with self.assertRaisesRegex(RuntimeError, 'fence coordinator'):
                self.deliver(peer, encode_load_claim(claim_for(bound.offer)) + b'\n')
        self.assertTrue(self.owner._failed)
        self.assertEqual(self.owner._peers, {})
        self.assertEqual(len(self.world.session.ready_players), 1)  # state is indeterminate, not rolled back
        self.assertEqual(peer.recv(1), b'')

    def test_peer_closing_during_admission_is_disconnected_in_same_poll(self):
        peer = self.connect(); self.ready_world(); bound = self.offer(peer)
        original = self.world.mark_loaded_trusting_marker
        def close_after(*args, **kwargs):
            original(*args, **kwargs)
            peer.shutdown(socket.SHUT_WR)
        with patch.object(self.world, 'mark_loaded_trusting_marker', side_effect=close_after):
            self.deliver(peer, encode_load_claim(claim_for(bound.offer)) + b'\n')
        self.assertEqual(self.world.session.ready_players, ())
        self.assertEqual(self.world.session.players, ())

    def test_partial_attach_failure_faults_and_closes_other_sockets(self):
        other = self.connect()
        remote = socket.create_connection(self.listener.getsockname(), timeout=1)
        remote.settimeout(1); local, _ = self.listener.accept(); self.clients.append(remote)
        original = self.session.attach
        def partial(conn):
            original(conn)
            raise KeyboardInterrupt('after attach')
        with patch.object(self.session, 'attach', side_effect=partial):
            with self.assertRaisesRegex(RuntimeError, 'fence coordinator'):
                self.owner.adopt(local)
        self.assertTrue(self.owner._failed)
        self.assertEqual(self.owner._peers, {})
        self.assertEqual(other.recv(1), b'')
        self.assertEqual(remote.recv(1), b'')

    def test_close_exception_faults_and_closes_remaining_peers(self):
        first, second = self.connect(), self.connect()
        target = next(iter(self.owner._peers))
        original = socket.socket.close
        def partial(conn):
            original(conn)
            if conn is target: raise KeyboardInterrupt('after close')
        with patch.object(socket.socket, 'close', new=partial):
            with self.assertRaisesRegex(RuntimeError, 'fence coordinator'):
                self.owner.close()
        self.assertTrue(self.owner._failed)
        self.assertEqual(self.owner._peers, {})
        self.assertEqual(first.recv(1), b'')
        self.assertEqual(second.recv(1), b'')

    def test_interruption_after_peer_pop_faults_and_closes_remaining_peers(self):
        first, second = self.connect(), self.connect()
        class InterruptedPop(dict):
            def pop(self, key):
                super().pop(key)
                raise KeyboardInterrupt('after pop')
        self.owner._peers = InterruptedPop(self.owner._peers)
        with self.assertRaisesRegex(RuntimeError, 'fence coordinator'):
            self.owner.close()
        self.assertTrue(self.owner._failed)
        self.assertEqual(self.owner._peers, {})
        self.assertEqual(first.recv(1), b'')
        self.assertEqual(second.recv(1), b'')

    def test_unix_socket_is_refused_before_session_mutation(self):
        if not hasattr(socket, 'socketpair'): self.skipTest('socketpair unavailable')
        a, b = socket.socketpair()
        try:
            with self.assertRaises(ValueError): self.owner.adopt(a)
            self.assertEqual(self.world.session.players, ())
        finally: a.close(); b.close()


if __name__ == '__main__': unittest.main()
