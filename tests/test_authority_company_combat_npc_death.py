"""Deterministic offline trace and regression suite for a second already-queued hit
after server X4 destroys its registered NPC target (e.g. Antigone freighter MFQ-419).

NOTE: All tests here run offline against synthetic/mocked X4 MD state modeled directly on
tools/dev/authority_company_gun_damage.xml and the native Bazzite NPC death observations in
reports/AUTHORITY-NPC-DEATH-WITNESS-20261126.md and reports/AUTHORITY-LEGACY-TLS-NPC-DEATH-20261126.md.
They exercise the real Python CompanyCombatService, CompanyCombatClient, CompanyCombatSender,
NativeGunDamageProvider, and real tools/dev/authority_company_gun_damage.lua (via lupa LuaJIT 2.1),
not a live X4 process.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'src', ROOT / 'tools/dev'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

try:
    from lupa import luajit21
except ImportError:  # pragma: no cover
    luajit21 = None

from authority.company_combat_client import CompanyCombatClient
from authority.company_combat_frames import encode_frame
from authority.company_combat_service import (
    CombatImpactRejected,
    CompanyCombatService,
    derive_combat_ticket,
)
from authority.company_combat_source import CompanyCombatSender
from authority_company_gun_damage import (
    NativeGunDamageProvider,
    NativeGunDamageUncertain,
    _read_health,
)


EPOCH = 'b' * 32
SHOOTER_1 = 1073  # HVC-420
SHOOTER_2 = 1074  # KHI-630
NPC_TARGET = 943  # MFQ-419 (Antigone freighter)
SURVIVOR_NPC = 1700


def _encode_fixed_point(values):
    chunks = []
    for value in values:
        block = int(value // 67108864.0)
        remainder = value - block * 67108864.0
        whole = int(remainder)
        fraction = int((remainder - whole) * 1048576.0)
        chunks.append(f'{block},{whole},{fraction},')
    return ''.join(chunks)


def _hit(seq, target=NPC_TARGET, amount=26):
    return {'id': seq, 'target_index': target, 'amount': amount}


class _MockServerX4Bridge:
    """Faithful offline model of authority_company_gun_damage.xml + real authority_company_gun_damage.lua.

    Models the two native lifecycle stages observed in AUTHORITY-NPC-DEATH-WITNESS-20261126.md:
      - immediate (<7.8 ms): after apply_attackstrength kills target, MD and Lua can still see
        shield=0, hull=0, maxshield=5750, maxhull=6400;
      - post-wreck (>=7.8 ms): target stays registered in both directions as isclass.ship=true,
        isoperational=false, iswreck=true, with shield=0, hull=0, maxshield=0, maxhull=0;
      - despawned: target resolves to null in MD.
    """

    def __init__(self, provider, ships, *, ordered_hits=False, post_wreck_lua_on_kill=False,
                 pre_wreck_second_hit=False, despawn_after_kill=False):
        self.provider = provider
        self.ships = {idx: dict(state) for idx, state in ships.items()}
        self.ordered_hits = ordered_hits
        self.post_wreck_lua_on_kill = post_wreck_lua_on_kill
        self.pre_wreck_second_hit = pre_wreck_second_hit
        self.despawn_after_kill = despawn_after_kill
        self.now = 100.0
        self.game_age = 4.04348
        self.md_last_serial = 0
        self.md_lease = None
        self.md_impact_calls = []
        self.md_group_calls = []
        self.md_apply_stock_calls = []
        self.diagnostics = []
        self._peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._peer.connect(provider._socket.getsockname())
        self._peer.setblocking(False)
        self._rx_queue = []

        self.lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        self._callbacks = {}
        self._pending_md = []

        def debug_error(msg):
            self.diagnostics.append(str(msg))

        def register_event(name, cb):
            self._callbacks[name] = cb
            return True

        def add_ui_triggered(screen, action, args):
            assert screen == 'X4AuthorityCompanyGunDamage'
            py_args = {k: args[k] for k in args}
            self._pending_md.append((action, py_args))
            return True

        def net_pending(_self=None):
            self._drain_udp()
            return len(self._rx_queue) > 0

        def net_receive(_self=None):
            self._drain_udp()
            return self._rx_queue.pop(0) if self._rx_queue else None

        def net_send(_self, row):
            self._peer.send(row.encode('ascii'))
            return True

        def read_lua_health(actor_hex):
            for ship in self.ships.values():
                if ship['actor'] == actor_hex:
                    if ship.get('despawned'):
                        return self.lua.table_from({'ok': False, 'reason': 'id_convert_failed'})
                    return self.lua.table_from({
                        'ok': True,
                        'owner': ship['owner'],
                        'shield': ship['lua_shield'],
                        'hull': ship['lua_hull'],
                        'shieldmax': ship['lua_maxshield'],
                        'hullmax': ship['lua_maxhull'],
                    })
            return self.lua.table_from({'ok': False, 'reason': 'unavailable'})

        g.DebugError = debug_error
        g.RegisterEvent = register_event
        g.AddUITriggeredEvent = add_ui_triggered
        self.lua.execute((ROOT / 'tools/dev/authority_company_gun_damage.lua').read_text(encoding='ascii'))
        net_tbl = self.lua.table_from({
            'pending': net_pending,
            'receive': net_receive,
            'send': net_send,
        })
        self._lua_tick = g.X4LAN_AUTHORITY_COMPANY_GUN_DAMAGE.new(
            provider._lease,
            net_tbl,
            lambda: self.now,
            lambda: False,
            read_lua_health,
            lambda: self.game_age,
            ordered_hits,
        )

    def close(self):
        self._peer.close()

    def _drain_udp(self):
        while True:
            try:
                data = self._peer.recv(1024)
            except BlockingIOError:
                return
            self._rx_queue.append(data.decode('ascii'))

    def _apply_stock(self, source_idx, target_idx, amount):
        target = self.ships[target_idx]
        before_shield = target['shield']
        before_hull = target['hull']
        before_age = self.game_age
        self.md_apply_stock_calls.append((source_idx, target_idx, amount))
        shield_hit = min(before_shield, amount)
        after_shield = before_shield - shield_hit
        hull_hit = max(0.0, amount - before_shield)
        after_hull = max(0.0, before_hull - hull_hit)
        killed = after_hull == 0
        target['shield'] = after_shield
        target['hull'] = after_hull
        # Mirror native Bazzite observation: immediate MD maxshield/maxhull stay positive at apply time.
        max_shield = target['maxshield']
        max_hull = target['maxhull']
        if killed:
            if self.post_wreck_lua_on_kill:
                # Lua callback executes after the ~7.5-7.8 ms wreck transition.
                target['lua_shield'] = 0.0
                target['lua_hull'] = 0.0
                target['lua_maxshield'] = 0.0
                target['lua_maxhull'] = 0.0
            else:
                # Immediate callback before the wreck transition still sees original maxima.
                target['lua_shield'] = 0.0
                target['lua_hull'] = 0.0
                target['lua_maxshield'] = max_shield
                target['lua_maxhull'] = max_hull
            if self.despawn_after_kill:
                # Target remains a wreck during Hit 1's Lua readback, then despawns before Hit 2.
                target['isoperational'] = False
                target['iswreck'] = True
                target['maxshield'] = 0.0
                target['maxhull'] = 0.0
                target['_despawn_after_callback'] = True
            elif not self.pre_wreck_second_hit:
                # Wreck transition completes before follow-up impact arrives.
                target['isoperational'] = False
                target['iswreck'] = True
                target['maxshield'] = 0.0
                target['maxhull'] = 0.0
        else:
            target['lua_shield'] = after_shield
            target['lua_hull'] = after_hull
            target['lua_maxshield'] = max_shield
            target['lua_maxhull'] = max_hull
        witness = (
            f'{before_age},{self.game_age},{before_shield},{before_hull},'
            f'{after_shield},{after_hull},{max_shield},{max_hull}'
        )
        encoded = _encode_fixed_point(
            (amount, before_shield, before_hull, after_shield, after_hull, max_shield, max_hull)
        )
        return killed, witness, encoded, max_hull > 0

    def _run_md_action(self, action, args):
        lease = args['lease']
        serial = int(args['serial'])
        generation = args['generation']
        epoch = args['epoch']
        source_idx = int(args['source'])
        if action == 'Impact':
            target_idx = int(args['target'])
            amount = float(args['amount'])
            self.md_impact_calls.append((serial, source_idx, target_idx, amount))
            status, killed, witness, encoded = 'refused', '0', '-', '-'
            source_actor, target_actor = 'null', 'null'
            if lease == self.md_lease and serial <= self.md_last_serial:
                status = 'unknown'
            elif epoch == EPOCH:
                src = self.ships.get(source_idx)
                tgt = self.ships.get(target_idx)
                source_actor = src['actor'] if src and not src.get('despawned') else 'null'
                target_actor = tgt['actor'] if tgt and not tgt.get('despawned') else 'null'
                if (src and not src.get('despawned') and src['isoperational'] and src['owner'] == 'player'
                        and tgt and not tgt.get('despawned') and tgt['isoperational']
                        and source_idx != target_idx and tgt['shield'] >= 0 and tgt['hull'] > 0):
                    self.md_lease, self.md_last_serial = lease, serial
                    was_killed, witness, encoded, valid_post = self._apply_stock(source_idx, target_idx, amount)
                    killed = '1' if was_killed else '0'
                    status = 'applied' if valid_post else 'unknown'
            row = (
                f'{lease}|{serial}|{generation}|{epoch}|{source_idx}|{target_idx}|'
                f'{status}|{source_actor}|{target_actor}|{killed}|{witness}|{encoded}'
            )
            self._callbacks['X4AuthorityCompanyGunDamage.Result'](None, row)
            tgt = self.ships.get(target_idx)
            if tgt and tgt.pop('_despawn_after_callback', False):
                tgt['despawned'] = True
                tgt['iswreck'] = False
        elif action == 'Group':
            count = int(args['count'])
            impacts = [(int(args[f'target{i}']), float(args[f'amount{i}'])) for i in range(1, count + 1)]
            self.md_group_calls.append((serial, source_idx, tuple(impacts)))
            identity = f'{lease}|{serial}|{generation}|{epoch}|{source_idx}'
            status = 'refused'
            if lease == self.md_lease and serial <= self.md_last_serial:
                status = 'unknown'
            elif epoch == EPOCH:
                src = self.ships.get(source_idx)
                if src and not src.get('despawned') and src['isoperational'] and src['owner'] == 'player':
                    totals = {}
                    valid_rows = []
                    for target_idx, amount in impacts:
                        tgt = self.ships.get(target_idx)
                        if (tgt and not tgt.get('despawned') and tgt['isoperational']
                                and target_idx != source_idx and tgt['shield'] >= 0 and tgt['hull'] > 0):
                            total = amount + totals.get(target_idx, 0.0)
                            if total < tgt['shield'] + tgt['hull']:
                                totals[target_idx] = total
                                valid_rows.append((target_idx, amount))
                    if len(valid_rows) == count:
                        self.md_lease, self.md_last_serial = lease, serial
                        status = 'applied'
                        for pos, (target_idx, amount) in enumerate(valid_rows, 1):
                            tgt = self.ships[target_idx]
                            if not (status == 'applied' and tgt['isoperational'] and tgt['hull'] > 0):
                                status = 'unknown'
                                break
                            was_killed, _, encoded, valid_post = self._apply_stock(source_idx, target_idx, amount)
                            if valid_post and not was_killed and tgt['hull'] > 0:
                                row = (
                                    f'{identity}|row|{pos}|{target_idx}|{self.game_age}|'
                                    f"{src['actor']}|{tgt['actor']}|{encoded}"
                                )
                                self._callbacks['X4AuthorityCompanyGunDamage.GroupResult'](None, row)
                            else:
                                status = 'unknown'
                                break
                        if status == 'applied':
                            status = 'complete'
            self._callbacks['X4AuthorityCompanyGunDamage.GroupResult'](None, f'{identity}|{status}')

    def step(self):
        self._lua_tick()
        while self._pending_md:
            action, args = self._pending_md.pop(0)
            self._run_md_action(action, args)


class CompanyCombatNpcDeathReceiptParserTests(unittest.TestCase):
    """Cross-platform parser checks for pre-wreck and post-wreck lethal receipts."""

    def test_read_health_accepts_verified_post_wreck_zero_maxima_and_rejects_invalid_variants(self):
        # Immediate pre-wreck lethal readback (original maxima retained in Lua callback).
        immediate = 'applied|0x40acb|0x2380f|1|26|0|10|0|0|5750|6400|0|0|5750|6400'.split('|')
        self.assertEqual(_read_health(immediate, 26), (0.0, 0.0))

        # Post-wreck lethal readback (Lua callback after ~7.8 ms wreck transition -> 0,0,0,0).
        post_wreck = 'applied|0x40acb|0x2380f|1|26|0|10|0|0|5750|6400|0|0|0|0'.split('|')
        self.assertEqual(_read_health(post_wreck, 26), (0.0, 0.0))

        # Shieldless NPC post-wreck lethal readback.
        shieldless = 'applied|0x40acb|0x2380f|1|26|0|10|0|0|0|6400|0|0|0|0'.split('|')
        self.assertEqual(_read_health(shieldless, 26), (0.0, 0.0))

        # Invalid cases MUST still fail closed as NativeGunDamageUncertain.
        invalid_cases = (
            # Non-lethal hit whose Lua readback shows 0,0,0,0.
            'applied|0x40acb|0x2380f|0|26|0|100|0|74|5750|6400|0|0|0|0',
            # Killed=1 with partial zero maxima (only hullmax zeroed).
            'applied|0x40acb|0x2380f|1|26|0|10|0|0|5750|6400|0|0|5750|0',
            # Killed=1 with partial zero maxima (only shieldmax zeroed).
            'applied|0x40acb|0x2380f|1|26|0|10|0|0|5750|6400|0|0|0|6400',
            # Killed=1 with mismatched nonzero maxima.
            'applied|0x40acb|0x2380f|1|26|0|10|0|0|5750|6400|0|0|100|200',
            # Killed=1 with live Lua hull.
            'applied|0x40acb|0x2380f|1|26|0|10|0|0|5750|6400|0|10|5750|6400',
            # Killed=1 with nonzero Lua shield.
            'applied|0x40acb|0x2380f|1|26|0|10|0|0|5750|6400|5|0|5750|6400',
            # Killed=0 with MD zero hull (impossible kill flag).
            'applied|0x40acb|0x2380f|0|26|0|10|0|0|5750|6400|0|0|0|0',
            # MD itself reported zero max_hull.
            'applied|0x40acb|0x2380f|1|26|0|10|0|0|5750|0|0|0|0|0',
        )
        for row in invalid_cases:
            with self.subTest(row=row):
                with self.assertRaises(NativeGunDamageUncertain):
                    _read_health(row.split('|'), 26)


@unittest.skipIf(luajit21 is None, 'lupa.luajit21 required')
class QueuedSecondHitAfterNpcDeathTests(unittest.TestCase):
    """End-to-end offline tests across CompanyCombatService + NativeGunDamageProvider + Lua + MD model."""

    def _make_rig(self, *, ordered_hits=False, npc_hull=10.0, post_wreck_lua_on_kill=False,
                  pre_wreck_second_hit=False, despawn_after_kill=False):
        grants = {}

        def grant(index):
            ticket = f'{index:064x}'
            generation = f'{index:032x}'
            binding = (generation, EPOCH, index)
            grants[derive_combat_ticket(ticket)] = binding
            return dict(ticket=ticket, generation=generation, epoch=EPOCH, index=index, session_epoch='a' * 32)

        pilot1 = grant(SHOOTER_1)
        pilot2 = grant(SHOOTER_2)
        active_bindings = set(grants.values())

        now_box = [100.0]
        provider = NativeGunDamageProvider(
            lambda b: b in active_bindings,
            can_fire=lambda b: b in active_bindings,
            world_current=lambda ep: ep == EPOCH,
            timeout=4,
            clock=lambda: now_box[0],
        )
        self.addCleanup(provider.close)
        provider.bind(dict(pid=1, start_ticks=1, exe_dev=1, exe_ino=1), lambda: True)
        provider._ordered_hits = ordered_hits
        patcher = patch.object(provider, '_owns_peer', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        ships = {
            SHOOTER_1: dict(actor='0x40acb', owner='player', isoperational=True, iswreck=False,
                            shield=2366.0, hull=3600.0, maxshield=2366.0, maxhull=3600.0,
                            lua_shield=2366.0, lua_hull=3600.0, lua_maxshield=2366.0, lua_maxhull=3600.0),
            SHOOTER_2: dict(actor='0x40acc', owner='player', isoperational=True, iswreck=False,
                            shield=2366.0, hull=3600.0, maxshield=2366.0, maxhull=3600.0,
                            lua_shield=2366.0, lua_hull=3600.0, lua_maxshield=2366.0, lua_maxhull=3600.0),
            NPC_TARGET: dict(actor='0x2380f', owner='antigone', isoperational=True, iswreck=False,
                             shield=0.0, hull=npc_hull, maxshield=5750.0, maxhull=6400.0,
                             lua_shield=0.0, lua_hull=npc_hull, lua_maxshield=5750.0, lua_maxhull=6400.0),
            SURVIVOR_NPC: dict(actor='0x23810', owner='antigone', isoperational=True, iswreck=False,
                               shield=200.0, hull=2000.0, maxshield=200.0, maxhull=2000.0,
                               lua_shield=200.0, lua_hull=2000.0, lua_maxshield=200.0, lua_maxhull=2000.0),
        }
        x4 = _MockServerX4Bridge(
            provider,
            ships,
            ordered_hits=ordered_hits,
            post_wreck_lua_on_kill=post_wreck_lua_on_kill,
            pre_wreck_second_hit=pre_wreck_second_hit,
            despawn_after_kill=despawn_after_kill,
        )
        self.addCleanup(x4.close)
        # Send GUN_HELLO from Lua to Python provider.
        x4.step()

        service = CompanyCombatService(
            grants.get,
            provider.begin_impact,
            apply_impacts=provider.begin_impacts if ordered_hits else None,
        )
        self.addCleanup(service.close)
        return service, provider, x4, pilot1, pilot2

    def _open(self, service):
        conn = socket.create_connection(('127.0.0.1', service.port), timeout=2)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.addCleanup(conn.close)
        stream = conn.makefile('rb')
        self.addCleanup(stream.close)
        return conn, stream

    @staticmethod
    def _frame(credential, impacts):
        return encode_frame(dict(
            kind='combat_impacts',
            ticket=derive_combat_ticket(credential['ticket']),
            generation=credential['generation'],
            registry_epoch=credential['epoch'],
            shooter_index=credential['index'],
            impacts=impacts,
        ))

    @staticmethod
    def _pump(service, x4, steps=16):
        for _ in range(steps):
            service.poll()
            x4.step()

    def test_same_batch_queued_second_hit_serial_and_grouped_pre_and_post_wreck(self):
        for ordered_hits in (False, True):
            for timing in ('immediate_lua', 'post_wreck_lua', 'pre_wreck_second_hit', 'despawned_after_kill'):
                with self.subTest(ordered_hits=ordered_hits, timing=timing):
                    service, provider, x4, pilot1, _ = self._make_rig(
                        ordered_hits=ordered_hits,
                        npc_hull=10.0,
                        post_wreck_lua_on_kill=(timing == 'post_wreck_lua'),
                        pre_wreck_second_hit=(timing == 'pre_wreck_second_hit'),
                        despawn_after_kill=(timing == 'despawned_after_kill'),
                    )
                    conn, stream = self._open(service)
                    batch = [_hit(1, NPC_TARGET, 26), _hit(2, NPC_TARGET, 26), _hit(3, SHOOTER_2, 26)]
                    conn.sendall(self._frame(pilot1, batch))
                    self._pump(service, x4, steps=20)

                    receipts = json.loads(stream.readline())['receipts']
                    self.assertEqual(
                        [(r['id'], r['order'], r['status'], r['shield'], r['hull']) for r in receipts],
                        [
                            (1, 1, 'confirmed', 0.0, 0.0),
                            (2, 2, 'rejected', None, None),
                            (3, 3, 'confirmed', 2340.0, 3600.0),
                        ],
                    )
                    self.assertFalse(service.poisoned)
                    self.assertFalse(provider._fenced)
                    # ApplyStock ran ONLY for hit 1 (kill) and hit 3 (surviving ship), never hit 2.
                    self.assertEqual(x4.md_apply_stock_calls, [(SHOOTER_1, NPC_TARGET, 26.0), (SHOOTER_1, SHOOTER_2, 26.0)])
                    if ordered_hits:
                        self.assertEqual(len(x4.md_group_calls), 1)
                    self.assertEqual(len(x4.md_impact_calls), 3)

                    # Exact retry must replay cached receipts without any additional native MD calls.
                    conn.sendall(self._frame(pilot1, batch))
                    self._pump(service, x4, steps=4)
                    self.assertEqual(json.loads(stream.readline())['receipts'], receipts)
                    self.assertEqual(len(x4.md_impact_calls), 3)

    def test_cumulative_lethal_group_falls_back_kills_on_hit2_and_rejects_queued_hit3(self):
        service, provider, x4, pilot1, _ = self._make_rig(
            ordered_hits=True,
            npc_hull=30.0,
            post_wreck_lua_on_kill=True,
        )
        conn, stream = self._open(service)
        batch = [_hit(1, NPC_TARGET, 26), _hit(2, NPC_TARGET, 26), _hit(3, NPC_TARGET, 26), _hit(4, SHOOTER_2, 26)]
        conn.sendall(self._frame(pilot1, batch))
        self._pump(service, x4, steps=24)

        receipts = json.loads(stream.readline())['receipts']
        self.assertEqual(
            [(r['id'], r['order'], r['status'], r['shield'], r['hull']) for r in receipts],
            [
                (1, 1, 'confirmed', 0.0, 4.0),
                (2, 2, 'confirmed', 0.0, 0.0),
                (3, 3, 'rejected', None, None),
                (4, 4, 'confirmed', 2340.0, 3600.0),
            ],
        )
        self.assertFalse(service.poisoned)
        self.assertFalse(provider._fenced)
        self.assertEqual(len(x4.md_group_calls), 1)
        self.assertEqual(x4.md_apply_stock_calls, [
            (SHOOTER_1, NPC_TARGET, 26.0),
            (SHOOTER_1, NPC_TARGET, 26.0),
            (SHOOTER_1, SHOOTER_2, 26.0),
        ])

    def test_two_pilots_simultaneously_queued_second_pilot_hits_dead_npc_then_live_target(self):
        for ordered_hits in (False, True):
            with self.subTest(ordered_hits=ordered_hits):
                service, provider, x4, pilot1, pilot2 = self._make_rig(
                    ordered_hits=ordered_hits,
                    npc_hull=10.0,
                    post_wreck_lua_on_kill=True,
                )
                c1, s1 = self._open(service)
                c2, s2 = self._open(service)
                # Pilot 1 sends lethal hit; Pilot 2 already queues [dead NPC, live NPC] while hit 1 is in flight.
                c1.sendall(self._frame(pilot1, [_hit(1, NPC_TARGET, 26)]))
                service.poll()
                self.assertTrue(service.native_pending)
                c2.sendall(self._frame(pilot2, [_hit(1, NPC_TARGET, 26), _hit(2, SURVIVOR_NPC, 26)]))
                self._pump(service, x4, steps=24)

                r1 = json.loads(s1.readline())['receipts']
                r2 = json.loads(s2.readline())['receipts']
                self.assertEqual(r1, [dict(_hit(1, NPC_TARGET, 26), order=1, status='confirmed', shield=0.0, hull=0.0)])
                self.assertEqual(
                    [(r['id'], r['order'], r['status'], r['shield'], r['hull']) for r in r2],
                    [(1, 2, 'rejected', None, None), (2, 3, 'confirmed', 174.0, 2000.0)],
                )
                self.assertFalse(service.poisoned)
                self.assertFalse(provider._fenced)

    def test_disconnect_and_retry_mid_single_fallback_after_npc_kill_never_redispatches(self):
        service, provider, x4, pilot1, _ = self._make_rig(
            ordered_hits=True,
            npc_hull=10.0,
            post_wreck_lua_on_kill=True,
        )
        old_conn, old_stream = self._open(service)
        batch = [_hit(1, NPC_TARGET, 26), _hit(2, NPC_TARGET, 26), _hit(3, SHOOTER_2, 26)]
        frame = self._frame(pilot1, batch)
        old_conn.sendall(frame)
        # Poll 1: dispatch Group; step: Group refuses; Poll 2: settle refusal -> single_fallback;
        # Poll 3: dispatch hit 1 (lethal) -> still native_pending.
        service.poll()
        x4.step()
        service.poll()
        service.poll()
        self.assertTrue(service.native_pending)

        # Original connection drops while hit 1 is in-flight and hits 2..3 sit in single_fallback.
        old_stream.close()
        old_conn.close()
        retry_conn, retry_stream = self._open(service)
        retry_conn.sendall(frame)
        self._pump(service, x4, steps=24)

        receipts = json.loads(retry_stream.readline())['receipts']
        self.assertEqual(
            [(r['id'], r['order'], r['status'], r['shield'], r['hull']) for r in receipts],
            [
                (1, 1, 'confirmed', 0.0, 0.0),
                (2, 2, 'rejected', None, None),
                (3, 3, 'confirmed', 2340.0, 3600.0),
            ],
        )
        self.assertEqual(len(x4.md_impact_calls), 3)
        self.assertFalse(service.poisoned)

    def test_combat_sender_survives_rejected_second_hit_and_continues_live_fire(self):
        service, provider, x4, pilot1, _ = self._make_rig(
            ordered_hits=True,
            npc_hull=10.0,
            post_wreck_lua_on_kill=True,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / 'engine.log'
            log.write_text(
                f'[=ERROR=] 1.0 [X4LAN] AUTH_COMPANY_GUN_STATUS armed|{EPOCH}|{SHOOTER_1}\n'
                f'[=ERROR=] 1.1 [X4LAN] AUTH_COMPANY_GUN {EPOCH}|{SHOOTER_1}|{NPC_TARGET}|1\n'
                f'[=ERROR=] 1.1 [X4LAN] AUTH_COMPANY_GUN {EPOCH}|{SHOOTER_1}|{NPC_TARGET}|2\n'
                f'[=ERROR=] 1.2 [X4LAN] AUTH_COMPANY_GUN {EPOCH}|{SHOOTER_1}|{SHOOTER_2}|3\n',
                encoding='ascii',
            )
            info = log.stat()
            sender = CompanyCombatSender(
                log,
                service.port,
                pilot1,
                epoch=EPOCH,
                index=SHOOTER_1,
                log_identity=(info.st_dev, info.st_ino),
            )
            try:
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    service.poll()
                    x4.step()
                    if sender._counts['confirmed'] >= 2 and sender._counts['rejected'] >= 1:
                        break
                    time.sleep(0.005)
            finally:
                stats = sender.close()

        self.assertIsNone(stats['failure'])
        self.assertEqual((stats['observed'], stats['confirmed'], stats['rejected'], stats['unknown']), (3, 2, 1, 0))
        self.assertEqual(stats['last_confirmed_receipt']['target_index'], SHOOTER_2)
        self.assertEqual(stats['last_confirmed_receipt']['shield'], 2340.0)
        self.assertFalse(service.poisoned)
        self.assertFalse(provider._fenced)

    def test_corrupted_partial_wreck_readback_still_poisons_and_blocks_queued_hits(self):
        """Negative control: a partial zero-maxima Lua readback (5750, 0) MUST still fail closed."""
        service, provider, x4, pilot1, _ = self._make_rig(ordered_hits=False, npc_hull=10.0)

        original_apply_stock = x4._apply_stock

        def corrupt_lua_after_kill(source_idx, target_idx, amount):
            res = original_apply_stock(source_idx, target_idx, amount)
            # Corrupt Lua readback: shieldmax stayed 5750 while hullmax became 0.
            x4.ships[target_idx]['lua_maxshield'] = 5750.0
            x4.ships[target_idx]['lua_maxhull'] = 0.0
            return res

        x4._apply_stock = corrupt_lua_after_kill
        conn, stream = self._open(service)
        batch = [_hit(1, NPC_TARGET, 26), _hit(2, NPC_TARGET, 26), _hit(3, SHOOTER_2, 26)]
        conn.sendall(self._frame(pilot1, batch))
        with self.assertLogs('authority.company_combat_service', level='ERROR'):
            self._pump(service, x4, steps=16)

        receipts = json.loads(stream.readline())['receipts']
        self.assertEqual(
            [(r['id'], r['order'], r['status']) for r in receipts],
            [(1, 1, 'unknown'), (2, 2, 'rejected'), (3, 3, 'rejected')],
        )
        self.assertTrue(service.poisoned)
        self.assertTrue(provider._fenced)
        # Only hit 1 reached MD; hits 2 and 3 were never sent once poisoned.
        self.assertEqual(len(x4.md_impact_calls), 1)

    def test_despawn_before_hit1_lua_readback_fails_closed(self):
        """Negative control: if a killed target vanishes before Hit 1's Lua readback, fail closed."""
        service, provider, x4, pilot1, _ = self._make_rig(ordered_hits=False, npc_hull=10.0)
        original_apply_stock = x4._apply_stock

        def vanish_before_lua(source_idx, target_idx, amount):
            res = original_apply_stock(source_idx, target_idx, amount)
            x4.ships[target_idx]['despawned'] = True
            return res

        x4._apply_stock = vanish_before_lua
        conn, stream = self._open(service)
        batch = [_hit(1, NPC_TARGET, 26), _hit(2, NPC_TARGET, 26), _hit(3, SHOOTER_2, 26)]
        conn.sendall(self._frame(pilot1, batch))
        with self.assertLogs('authority.company_combat_service', level='ERROR'):
            self._pump(service, x4, steps=16)

        receipts = json.loads(stream.readline())['receipts']
        self.assertEqual(
            [(r['id'], r['order'], r['status']) for r in receipts],
            [(1, 1, 'unknown'), (2, 2, 'rejected'), (3, 3, 'rejected')],
        )
        self.assertTrue(service.poisoned)
        self.assertTrue(provider._fenced)
        self.assertTrue(any('reason=lua_readback_missing' in d for d in x4.diagnostics))
        self.assertEqual(len(x4.md_impact_calls), 1)


if __name__ == '__main__':
    unittest.main()
