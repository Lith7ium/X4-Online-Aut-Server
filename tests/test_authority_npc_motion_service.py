"""Loopback concurrency, visibility, recovery, and 10k-registry tests for authoritative NPC motion.

Exercises real CompanyMotionService + real CompanyMotionClient + NpcMotionObserver
over loopback TCP sockets (and WorldCoordinator pause/save/resume window transitions).
Synthetic NPC sample sources are used here; native X4 behaviour is verified separately.
"""
import time
import unittest
from unittest.mock import patch

from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_service import CompanyMotionService, MAX_SAMPLE_AGE
from authority.npc_motion_observer import NpcMotionObserver

EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
SESSION = 'a' * 32
GEN_NPC_1 = '1' * 32
GEN_NPC_2 = '2' * 32
GEN_NPC_1_NEXT = '3' * 32
SECTOR_A = 'cluster_01_sector001_macro'
SECTOR_B = 'cluster_06_sector001_macro'
SECTOR_C = 'cluster_14_sector001_macro'


class IdlePlayerSource:
    """Minimal read-only source stub for an admitted observer pilot."""

    def __init__(self, epoch, index):
        self.registry = (epoch, index)
        self.local_generation = 'f' * 32

    def poll(self, _validate_peer):
        return None


def make_npc_sample(index, sequence, sector, x, *, y=100.0, z=-50.0, vel=None,
                    generation=GEN_NPC_1, epoch=EPOCH, source_time=None, capture_time=None):
    return {
        'version': 3,
        'epoch': epoch,
        'generation': generation,
        'index': index,
        'sequence': sequence,
        'sector_macro': sector,
        'source_time': float(sequence) if source_time is None else float(source_time),
        'capture_time': float(sequence * 2) if capture_time is None else float(capture_time),
        'x': float(x),
        'y': float(y),
        'z': float(z),
        'real_velocity': vel,
    }


class AuthoritativeNpcMotionLoopbackTests(unittest.TestCase):
    def setUp(self):
        self.service = CompanyMotionService()
        self.addCleanup(self.service.close)
        self.pilot_a_grant = self.service.admit(1, 'a1' * 16, 1970, SECTOR_A, SESSION, EPOCH)
        self.pilot_b_grant = self.service.admit(2, 'b2' * 16, 7354, SECTOR_B, SESSION, EPOCH)
        self.service.set_running(True)

        self.playback_a = []
        self.playback_ends_a = []
        self.obs_a = NpcMotionObserver(
            EPOCH, 1970,
            on_playback=lambda s, rec: self.playback_a.append((s['index'], s['sequence'], s['sector_macro'])),
            on_playback_end=lambda idx, gen, why: self.playback_ends_a.append((idx, gen, why)),
        )
        self.obs_b = NpcMotionObserver(EPOCH, 7354)

        self.client_a = CompanyMotionClient(
            self.service.port, self.pilot_a_grant, IdlePlayerSource(EPOCH, 1970), lambda: True,
            on_snapshot=self.obs_a.on_snapshot, on_end=self.obs_a.on_end,
            on_view_reset=self.obs_a.on_view_reset,
        )
        self.addCleanup(self.client_a.close)
        self.obs_a.bind_views(self.client_a.set_views)

        self.client_b = CompanyMotionClient(
            self.service.port, self.pilot_b_grant, IdlePlayerSource(EPOCH, 7354), lambda: True,
            on_snapshot=self.obs_b.on_snapshot, on_end=self.obs_b.on_end,
            on_view_reset=self.obs_b.on_view_reset,
        )
        self.addCleanup(self.client_b.close)
        self.obs_b.bind_views(self.client_b.set_views)

        self.pump_until(lambda: self.client_a._running and self.client_b._running)

    def pump_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.service.poll()
            self.client_a.poll()
            self.client_b.poll()
            time.sleep(0.002)
        self.assertTrue(predicate())

    def test_two_tracks_two_observers_withdrawal_and_reacquisition_with_velocity_retention(self):
        # Pilot A observes NPC 1318 in high_attention (SECTOR_B) AND NPC 4001 on open map (SECTOR_A).
        # Pilot B observes NPC 1318 in SECTOR_B concurrently.
        self.obs_a.set_slot_view('high_attention', SECTOR_B, [1318])
        self.obs_a.set_slot_view('map', SECTOR_A, [4001])
        self.obs_b.set_slot_view('high_attention', SECTOR_B, [1318])
        self.pump_until(lambda: self.service.subscribed_npc_targets() == {
            SECTOR_B: frozenset({1318}),
            SECTOR_A: frozenset({4001}),
        })

        # Publish initial baselines (real_velocity=None), then moving samples (nonzero real_velocity).
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(1318, 1, SECTOR_B, 1000.0)))
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(4001, 1, SECTOR_A, 200.0, generation=GEN_NPC_2)))
        self.pump_until(lambda: (
            self.obs_a.stats()['tracks'][1318]['last_sequence'] == 1
            and self.obs_a.stats()['tracks'][4001]['last_sequence'] == 1
            and self.obs_b.stats()['tracks'][1318]['last_sequence'] == 1
        ))

        vel_1318 = {'x': 42.5, 'y': 0.0, 'z': -3.0}
        vel_4001 = {'x': 15.0, 'y': 0.0, 'z': 0.0}
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(1318, 2, SECTOR_B, 1085.0, vel=vel_1318)))
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(4001, 2, SECTOR_A, 230.0, vel=vel_4001, generation=GEN_NPC_2)))
        self.pump_until(lambda: (
            self.obs_a.stats()['tracks'][1318]['last_sequence'] == 2
            and self.obs_a.stats()['tracks'][4001]['last_sequence'] == 2
            and self.obs_b.stats()['tracks'][1318]['last_sequence'] == 2
        ))

        # Stationary sample afterwards must NOT overwrite retained nonzero-velocity sample.
        self.assertTrue(self.service.publish_npc_sample(
            make_npc_sample(1318, 3, SECTOR_B, 1085.0, vel={'x': 0.0, 'y': 0.0, 'z': 0.0}),
        ))
        self.pump_until(lambda: self.obs_a.stats()['tracks'][1318]['last_sequence'] == 3)
        track_a_1318 = self.obs_a.stats()['tracks'][1318]
        self.assertEqual(track_a_1318['last_confirmed']['sequence'], 3)
        self.assertEqual(track_a_1318['last_nonzero_velocity_confirmed']['sequence'], 2)
        self.assertEqual(track_a_1318['last_nonzero_velocity_confirmed']['real_velocity'], vel_1318)

        # Interest withdrawal: NPC 1318 leaves Pilot A's view; NPC 4001 on Pilot A and NPC 1318 on Pilot B continue!
        self.obs_a.clear_slot('high_attention')
        self.pump_until(lambda: self.client_a._sent_view == {SECTOR_A: frozenset({4001})})
        self.assertEqual(self.obs_a.stats()['tracks'][1318]['withdrawal_count'], 1)
        self.assertIn((1318, GEN_NPC_1, 'withdrawn'), self.playback_ends_a)

        # Publish moving updates while 1318 is offscreen for Pilot A.
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(1318, 4, SECTOR_B, 1170.0, vel=vel_1318)))
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(4001, 3, SECTOR_A, 260.0, vel=vel_4001, generation=GEN_NPC_2)))
        self.pump_until(lambda: (
            self.obs_b.stats()['tracks'][1318]['last_sequence'] == 4
            and self.obs_a.stats()['tracks'][4001]['last_sequence'] == 3
        ))
        # Pilot A received zero deliveries for withdrawn 1318 (still at sequence 3).
        self.assertEqual(self.obs_a.stats()['tracks'][1318]['last_sequence'], 3)

        # Unseen sector change while withdrawn -> reacquisition on Pilot A in SECTOR_C!
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(1318, 5, SECTOR_C, 5000.0, vel=None)))
        self.pump_until(lambda: self.obs_b.stats()['tracks'][1318]['end_count'] == 1)
        self.obs_a.set_slot_view('high_attention', SECTOR_C, [1318])
        self.pump_until(lambda: self.obs_a.stats()['tracks'][1318]['last_sequence'] == 5)
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(1318, 6, SECTOR_C, 5120.0, vel={'x': 60.0, 'y': 0.0, 'z': 0.0})))
        self.pump_until(lambda: self.obs_a.stats()['tracks'][1318]['last_sequence'] == 6)

        reacquired = self.obs_a.stats()['tracks'][1318]
        self.assertEqual(reacquired['reacquisition_count'], 1)
        self.assertTrue(reacquired['reacquired_after_withdrawal'])
        self.assertTrue(reacquired['reacquired_after_sector_change'])
        self.assertEqual(reacquired['last_sector'], SECTOR_C)
        self.assertEqual(reacquired['last_nonzero_velocity_confirmed']['sequence'], 6)

    def test_disappearance_stale_lifetime_and_player_seat_ownership_boundaries(self):
        self.obs_a.set_slot_view('high_attention', SECTOR_B, [1318])
        self.pump_until(lambda: self.service.subscribed_npc_targets() == {SECTOR_B: frozenset({1318})})

        # Initial sample with nonzero velocity must be refused.
        with self.assertRaisesRegex(ValueError, 'baseline'):
            self.service.publish_npc_sample(make_npc_sample(1318, 1, SECTOR_B, 100.0, vel={'x': 1, 'y': 0, 'z': 0}))
        # Publishing an NPC sample on a player-owned index (1970 or 7354) must be refused.
        with self.assertRaisesRegex(ValueError, 'admitted player grant'):
            self.service.publish_npc_sample(make_npc_sample(1970, 1, SECTOR_A, 100.0))

        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(1318, 1, SECTOR_B, 100.0)))
        self.pump_until(lambda: self.obs_a.stats()['tracks'][1318]['last_sequence'] == 1)

        # Stale sequence and foreign epoch rejected fail-closed.
        with self.assertRaises(ValueError):
            self.service.publish_npc_sample(make_npc_sample(1318, 1, SECTOR_B, 110.0))
        with self.assertRaises(ValueError):
            self.service.publish_npc_sample(make_npc_sample(1318, 2, SECTOR_B, 110.0, epoch='0' * 32))

        # Disappearance (explicit retirement) delivers motion_end without dropping Pilot A.
        self.assertTrue(self.service.retire_npc_track(1318))
        self.pump_until(lambda: self.obs_a.stats()['tracks'][1318]['end_count'] == 1)
        self.assertTrue(self.client_a._running)

        # New lifetime (fresh generation) starts cleanly after disappearance.
        self.assertTrue(self.service.publish_npc_sample(
            make_npc_sample(1318, 1, SECTOR_B, 300.0, generation=GEN_NPC_1_NEXT),
        ))
        self.pump_until(lambda: self.obs_a.stats()['tracks'][1318]['last_generation'] == GEN_NPC_1_NEXT)

        # If a player later takes seat 1318, admit() cleanly retires the NPC track first.
        self.service.admit(3, 'c3' * 16, 1318, SECTOR_B, SESSION, EPOCH)
        self.pump_until(lambda: self.obs_a.stats()['tracks'][1318]['end_count'] == 2)
        self.assertNotIn(1318, self.service.subscribed_npc_targets().get(SECTOR_B, ()))

    def test_delayed_npc_expiry_and_pause_save_resume_keep_observers_admitted(self):
        self.obs_a.set_slot_view('high_attention', SECTOR_B, [1318])
        self.obs_a.set_slot_view('map', SECTOR_A, [4001])
        self.pump_until(lambda: self.service.subscribed_npc_targets() == {
            SECTOR_B: frozenset({1318}),
            SECTOR_A: frozenset({4001}),
        })

        with patch('authority.company_motion_service.time.monotonic', return_value=100.0):
            self.assertTrue(self.service.publish_npc_sample(make_npc_sample(1318, 1, SECTOR_B, 100.0)))
            self.assertTrue(self.service.publish_npc_sample(make_npc_sample(4001, 1, SECTOR_A, 200.0, generation=GEN_NPC_2)))
            self.pump_until(lambda: (
                self.obs_a.stats()['tracks'][1318]['last_sequence'] == 1
                and self.obs_a.stats()['tracks'][4001]['last_sequence'] == 1
            ))

        # Refresh track 4001 mid-window so ONLY track 1318 stalls past MAX_SAMPLE_AGE.
        with patch('authority.company_motion_service.time.monotonic', return_value=100.0 + MAX_SAMPLE_AGE / 2):
            self.assertTrue(self.service.publish_npc_sample(
                make_npc_sample(4001, 2, SECTOR_A, 225.0, vel={'x': 10.0, 'y': 0.0, 'z': 0.0}, generation=GEN_NPC_2),
            ))
            self.pump_until(lambda: self.obs_a.stats()['tracks'][4001]['last_sequence'] == 2)

        # Track 1318 stalls past MAX_SAMPLE_AGE while Track 4001 keeps moving:
        # 1318 expires cleanly (motion_end), Pilot A stays admitted and 4001 keeps flowing!
        with patch('authority.company_motion_service.time.monotonic', return_value=100.0 + MAX_SAMPLE_AGE + 0.5):
            self.service.poll()
            self.assertTrue(self.service.publish_npc_sample(
                make_npc_sample(4001, 3, SECTOR_A, 250.0, vel={'x': 10.0, 'y': 0.0, 'z': 0.0}, generation=GEN_NPC_2),
            ))
            self.pump_until(lambda: (
                self.obs_a.stats()['tracks'][1318]['end_count'] == 1
                and self.obs_a.stats()['tracks'][4001]['last_sequence'] == 3
            ))
        self.assertTrue(self.client_a._running)

        # Pause / save / resume clock transition resets windows without dropping observers.
        self.service.set_running(False)
        self.assertFalse(self.service.publish_npc_sample(
            make_npc_sample(4001, 4, SECTOR_A, 280.0, generation=GEN_NPC_2),
        ))
        self.pump_until(lambda: not self.client_a._running and not self.client_b._running)

        self.service.set_running(True)
        self.pump_until(lambda: (
            self.client_a._running and self.client_b._running
            and self.service.subscribed_npc_targets() == {
                SECTOR_B: frozenset({1318}),
                SECTOR_A: frozenset({4001}),
            }
        ))
        self.assertGreaterEqual(self.obs_a.stats()['world_resets'], 2)

        # Post-resume baseline + moving sample flow immediately on both tracks.
        self.assertTrue(self.service.publish_npc_sample(make_npc_sample(4001, 4, SECTOR_A, 300.0, generation=GEN_NPC_2)))
        self.assertTrue(self.service.publish_npc_sample(
            make_npc_sample(4001, 5, SECTOR_A, 330.0, vel={'x': 15.0, 'y': 0.0, 'z': 0.0}, generation=GEN_NPC_2),
        ))
        self.pump_until(lambda: self.obs_a.stats()['tracks'][4001]['last_sequence'] == 5)

    def test_10082_registry_bounded_multi_ship_subscription_fanout(self):
        # Subscribe to 64 distinct indices spanning the 10,082-entry registry across 2 sectors.
        sector_a_indices = [100 + i * 100 for i in range(32)]  # up to 3200
        sector_b_indices = [5000 + i * 150 for i in range(32)]  # up to 9650 (within 10,082)
        self.obs_a.set_slot_view('high_attention', SECTOR_A, sector_a_indices)
        self.obs_a.set_slot_view('map', SECTOR_B, sector_b_indices)
        self.pump_until(lambda: (
            len(self.service.subscribed_npc_targets().get(SECTOR_A, ())) == 32
            and len(self.service.subscribed_npc_targets().get(SECTOR_B, ())) == 32
        ))
        for idx in sector_a_indices:
            self.assertTrue(self.service.publish_npc_sample(
                make_npc_sample(idx, 1, SECTOR_A, float(idx), generation=f'{idx:032x}'),
            ))
        for idx in sector_b_indices:
            self.assertTrue(self.service.publish_npc_sample(
                make_npc_sample(idx, 1, SECTOR_B, float(idx), generation=f'{idx:032x}'),
            ))
        self.pump_until(lambda: self.obs_a.stats()['snapshots_total'] == 64, timeout=5.0)
        self.assertEqual(len(self.obs_a.stats()['tracks']), 64)

    def test_local_udp_playback_bridge_with_admitted_client_loss_and_reacquisition(self):
        import socket
        from authority.npc_motion_observer import NpcLocalPlaybackBridge

        bridge = NpcLocalPlaybackBridge(EPOCH, 9001, lambda addr: addr[0] == '127.0.0.1')
        peer_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer_sock.bind(('127.0.0.1', 0))
        peer_sock.setblocking(False)
        grant_c = self.service.admit(3, 'c3' * 16, 9001, SECTOR_A, SESSION, EPOCH)
        client_c = CompanyMotionClient(
            self.service.port, grant_c, IdlePlayerSource(EPOCH, 9001), lambda: True,
            on_snapshot=bridge.observer.on_snapshot, on_end=bridge.observer.on_end,
            on_transport_lost=bridge.observer.on_transport_lost,
            on_view_reset=bridge.observer.on_view_reset,
            trial_index=1318,
        )
        bridge.observer.bind_views(client_c.set_views)
        prefix = f'N|{bridge.lease}|{EPOCH}|'.encode('ascii')
        received = []

        def pump_bridge():
            bridge.poll()
            self.service.poll()
            client_c.poll()
            while True:
                try:
                    data, _ = peer_sock.recvfrom(1024)
                except BlockingIOError:
                    break
                if data.startswith(prefix):
                    received.append(data[len(prefix):].decode('ascii'))

        def wait_bridge(pred, timeout=3.0):
            deadline = time.monotonic() + timeout
            while not pred():
                if time.monotonic() >= deadline:
                    raise TimeoutError('bridge test timeout')
                pump_bridge()
                time.sleep(0.005)

        try:
            # 1. X4 peer HELLO + VIEW subscription over loopback UDP
            peer_sock.sendto(prefix + b'HELLO', ('127.0.0.1', bridge.port))
            wait_bridge(lambda: 'ACK|HELLO' in received and client_c._running)
            peer_sock.sendto(prefix + f'VIEW|high_attention|1|{SECTOR_B}|1318'.encode('ascii'),
                             ('127.0.0.1', bridge.port))
            wait_bridge(lambda: 1318 in self.service.subscribed_npc_targets().get(SECTOR_B, ()))

            # 2. Authority publishes baseline + moving sample -> REMOTE delivered over UDP -> X4 sends SEEN + READBACK
            with patch('authority.company_motion_service.time.monotonic', return_value=200.0):
                self.assertTrue(self.service.publish_npc_sample(make_npc_sample(1318, 1, SECTOR_B, 100.0)))
                self.assertTrue(self.service.publish_npc_sample(
                    make_npc_sample(1318, 2, SECTOR_B, 110.0, vel={'x': 20.0, 'y': 0.0, 'z': 0.0}),
                ))
                wait_bridge(lambda: any(m.startswith(f'REMOTE|1318|{GEN_NPC_1}|2|') for m in received))
            peer_sock.sendto(prefix + f'SEEN|1318|{GEN_NPC_1}|2'.encode('ascii'), ('127.0.0.1', bridge.port))
            peer_sock.sendto(
                prefix + f'READBACK|readback|1318|{GEN_NPC_1}|2|interpolated|0.016|0.001|110|20|30|110|20|30|12.0|50.0'.encode('ascii'),
                ('127.0.0.1', bridge.port),
            )
            wait_bridge(lambda: bridge.stats()['readback_count'] == 1)
            self.assertEqual(bridge.stats()['seen_by_x4'][1318]['last_sequence'], 2)

            # 3. Sample loss (MAX_SAMPLE_AGE expiry) -> motion_hidden -> UDP LOSS packet!
            with patch('authority.company_motion_service.time.monotonic', return_value=200.0 + MAX_SAMPLE_AGE + 0.5):
                self.service.poll()
                wait_bridge(lambda: any(m.startswith(f'LOSS|1318|{GEN_NPC_1}|transport_lost') for m in received))
            self.assertEqual(bridge.stats()['loss_sent'], 1)

            # 4. Offscreen withdrawal -> UDP END|...|withdrawn and 0 deliveries while withdrawn
            peer_sock.sendto(prefix + b'VIEW|high_attention|2|-|-', ('127.0.0.1', bridge.port))
            wait_bridge(lambda: 1318 not in self.service.subscribed_npc_targets().get(SECTOR_B, ()))
            self.assertTrue(any(m.startswith(f'END|1318|{GEN_NPC_1}|withdrawn') for m in received))
        finally:
            client_c.close()
            peer_sock.close()
            bridge.close()


if __name__ == '__main__':
    unittest.main()
