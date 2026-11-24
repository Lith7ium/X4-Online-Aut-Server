"""Offline LuaJIT 2.1 and loopback verification for opt-in receiving-cockpit Company ship playback.

Covers:
  1. Read-only MD XML contract for src/authority/md/company_cockpit_observer.xml (HUD target +
     nearby radar-visible unoccupied Company pilot without a target lock; own-ship exclusions).
  2. Real LuaJIT 2.1 integration of company_cockpit_observer.lua + company_observer_stream.lua +
     company_cockpit_playback.lua + company_hull_playback.lua + company_hull_playback_x4.lua +
     motion_buffer.lua + motion_playback.lua:
       - Selected HUD target playback with velocity & delayed later-update readbacks (elapsed > 0).
       - Seamless hud -> radar transition (target unlocked while nearby on radar) and untargeted
         radar-only discovery from scratch (including when targeting an unrelated non-Company ship).
       - Independent map slot unaffected by cockpit target/radar transitions.
       - Local own-ship control invariant (own cockpit actor is never written).
       - Five-second sample-loss prediction cutoff with zero post-cutoff writes.
       - Strict generation checks (rejecting mismatched active generation & revoked generations after END,
         accepting a fresh pilot grant generation after END).
       - Reacquisition (direct <=50 m resumption, refusal of unaligned >50 m jump, single authorized
         post-loss alignment confirmed by later readback before resuming ordinary <=50 m playback).
  3. Python opt-in configuration & overlay wiring (CompanyMotionSource cockpit_radar_playback +
     client_seat_overlay default-off vs opt-in inclusion) and real loopback CompanyMotionService ->
     CompanyMotionClient -> CompanyMotionObserver integration.
"""
from pathlib import Path
import time
import unittest
import xml.etree.ElementTree as ET

try:
    from lupa import luajit21
except ImportError:  # pragma: no cover
    luajit21 = None

from authority.client_seat_overlay import company_motion_md, company_motion_ui
from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_service import CompanyMotionService, MAX_SAMPLE_AGE
from authority.company_motion_source import CompanyMotionSource

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / 'src/authority/md/company_cockpit_observer.xml'


class CompanyCockpitObserverMdTests(unittest.TestCase):
    def test_md_is_strictly_readonly_and_guards_hud_and_radar_modes(self):
        root = ET.parse(MD_PATH).getroot()
        self.assertEqual(root.tag, 'mdscript')
        self.assertEqual(root.attrib.get('name'), 'X4AuthorityCompanyCockpitObserver')
        allowed_tags = {
            'mdscript', 'cues', 'cue', 'conditions', 'event_ui_triggered', 'actions',
            'set_value', 'do_if', 'do_elseif', 'do_else', 'find_ship', 'do_for_each', 'raise_lua_event',
        }
        self.assertTrue({node.tag for node in root.iter()}.issubset(allowed_tags))
        # Must never mutate positions, velocity, or orders.
        for forbidden in ('warp', 'set_object_velocity', 'create_order', 'create_ship'):
            self.assertIsNone(root.find(f'.//{forbidden}'))
        guards = ' '.join(node.get('value', '') for node in root.iter() if node.get('value'))
        for required in (
            'State.$epoch == $args.$epoch',
            'State.$last == $args.$count',
            'not md.X4AuthorityShipRegistry.State.$faulted',
            '$target.owner == faction.player',
            '$target != player.controlled',
            'not $target.pilot.exists',
            'not $target.assignedpilot.exists',
            '$target.sector == player.sector',
            '$target.attention ge attention.visible',
            '$target.isphysicsready',
            'player.controlled.currentradarrange',
            'State.$ships.{$registered} == $target',
            '$registered != $args.$own',
        ):
            self.assertIn(required, guards)


class CompanyCockpitOptInConfigTests(unittest.TestCase):
    def test_opt_in_flag_default_off_and_overlay_selection(self):
        default_src = CompanyMotionSource(playback_index=1074)
        try:
            self.assertFalse(default_src.cockpit_radar_playback)
            self.assertNotIn('company_cockpit_observer.lua', company_motion_ui(default_src))
            self.assertNotIn('company_cockpit_observer.xml', company_motion_md(default_src))
            cfg = default_src.configure('a' * 32, 1073, operational_bits='1' * 2000)
            self.assertNotIn('cockpit_radar_playback', cfg)
        finally:
            default_src.close()

        with self.assertRaises(ValueError):
            CompanyMotionSource(cockpit_radar_playback=True)

        opt_src = CompanyMotionSource(playback_index=1074, cockpit_radar_playback=True)
        try:
            self.assertTrue(opt_src.cockpit_radar_playback)
            self.assertIn('company_cockpit_observer.lua', company_motion_ui(opt_src))
            self.assertIn('company_cockpit_playback.lua', company_motion_ui(opt_src))
            self.assertIn('company_cockpit_observer.xml', company_motion_md(opt_src))
            cfg = opt_src.configure('a' * 32, 1073, operational_bits='1' * 2000)
            self.assertIs(cfg['cockpit_radar_playback'], True)
            self.assertEqual(cfg['playback_index'], 1074)
        finally:
            opt_src.close()


@unittest.skipIf(luajit21 is None, 'lupa.luajit21 required')
class CompanyCockpitPlaybackLuaTests(unittest.TestCase):
    def test_hud_target_then_untargeted_radar_independent_map_loss_generation_and_reacquisition(self):
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        lua.globals()._ROOT = str(ROOT).replace('\\', '/')
        summary = lua.execute(r"""
            local root = _ROOT .. '/src/authority/lua/'
            local resolve = dofile(_ROOT .. '/tools/dev/authority_registry_resolve_string.lua')
            X4LAN_RESOLVE_STRING = resolve
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local adapter_mod = dofile(root .. 'company_hull_playback_x4.lua')
            local observer_mod = dofile(root .. 'company_cockpit_observer.lua')
            local playback_mod = dofile(root .. 'company_cockpit_playback.lua')
            local stream_mod = dofile(root .. 'company_observer_stream.lua')

            local epoch = ('a'):rep(32)
            local gen1 = ('1'):rep(32)
            local gen2 = ('2'):rep(32)
            local gen_bad = ('9'):rep(32)
            local prefix = 'C|lease123|localgen|'

            local own_actor = 100
            local remote_actor = 200
            local npc_actor = 300
            local map_actor = 400
            local sector_a, sector_b, zone_a, zone_own = 10, 20, 30, 31
            local own_index, remote_index, map_index = 1073, 1074, 2050
            local pinned = {epoch = epoch, issued_count = 5048, nonce = ('d'):rep(32)}

            local st = {
                now = 10.0, gt = 50.0, paused = false, map_open = false,
                hud_target = remote_actor, remote_on_screen = true, remote_distance = 1200,
                radar_range = 40000,
                poses = {
                    [own_actor] = {x = 0, y = 0, z = 0, yaw = 0.1, pitch = 0.2, roll = 0.3},
                    [remote_actor] = {x = 1000, y = 0, z = 0, yaw = 0.4, pitch = 0.5, roll = 0.6},
                },
                pending_write = nil,
                writes = {},
                readbacks = {},
                events = {},
                faults = {},
                incoming = {},
                sent = {},
                md_queue = {},
                md_handlers = {},
            }

            Menus = {{name = 'MapMenu', shown = false}}
            GetCurRealTime = function() return st.now end
            GetPlayerTarget = function() return st.hud_target and tostring(st.hud_target) or nil end
            ConvertStringToLuaID = function(s) return tostring(s) end
            ConvertIDTo64Bit = function(s) return tonumber(s) end
            X4LAN_RESOLVE_STRING = {
                parse_hex = function(s) return tonumber(s) end,
                actor_u64 = function(v) return tonumber(v) end,
            }
            function GetComponentData(id, field)
                if id == tostring(sector_a) and field == 'macro' then return 'cluster_06_sector002_macro' end
                if id == tostring(sector_b) and field == 'macro' then return 'cluster_01_sector001_macro' end
                if id == tostring(own_actor) and field == 'maxradarrange' then return st.radar_range end
                if id == tostring(remote_actor) then
                    if field == 'owner' then
                        return 'player', true, st.remote_distance <= st.radar_range, false, false, false, nil, nil, false
                    end
                end
                if id == tostring(npc_actor) then
                    return 'argon', true, true, false, false, false, 'pilot', 'pilot', true
                end
                return nil
            end
            -- Handle both 4-arg / 7-arg / 9-arg GetComponentData calls from company_hull_playback_x4 and company_cockpit_observer!
            local orig_gcd = GetComponentData
            function GetComponentData(id, f1, f2)
                if id == tostring(remote_actor) and f1 == 'owner' and f2 == 'isdocked' then
                    return 'player', false, false, false, nil, nil, false
                end
                return orig_gcd(id, f1, f2)
            end

            RegisterEvent = function(name, cb)
                st.md_handlers[name] = cb
                return true
            end
            AddUITriggeredEvent = function(screen, control, args)
                assert(screen == 'X4AuthorityCompanyCockpitObserver' and control == 'Read')
                local ok_ship = (st.remote_distance <= st.radar_range)
                if args.mode == 'hud' and st.hud_target ~= remote_actor then ok_ship = false end
                local reply
                if ok_ship then
                    reply = table.concat({
                        args.request_id, epoch, 'OBSERVED', tostring(remote_index),
                        ('0x%x'):format(remote_actor), 'cluster_06_sector002_macro', 'KHI-630', args.mode,
                    }, '|')
                else
                    reply = table.concat({args.request_id, epoch, 'UNAVAILABLE', '0', '0', '0', '0', 'none'}, '|')
                end
                st.md_queue[#st.md_queue + 1] = reply
                return true
            end

            package.loaded.ffi = {
                NULL = {},
                typeof = function() return true end,
                cdef = function() end,
                string = function(v) return v end,
                new = function(kind, val)
                    if kind == 'UIPosRot[1]' then return {[0] = {}} end
                    return val
                end,
                C = {
                    IsPlayerValid = function() return true end,
                    IsGamePaused = function() return st.paused end,
                    IsHUDActive = function() return true end,
                    IsExternalTargetMode = function() return false end,
                    IsExternalViewActive = function() return false end,
                    IsValidComponent = function(id) return id ~= 0 end,
                    IsComponentOperational = function(id) return id == own_actor or id == remote_actor or id == npc_actor end,
                    IsComponentClass = function(id, cls)
                        if cls == 'sector' then return id == sector_a or id == sector_b end
                        if cls == 'zone' then return id == zone_a or id == zone_own end
                        return id == own_actor or id == remote_actor or id == npc_actor
                    end,
                    GetPlayerControlledShipID = function() return own_actor end,
                    GetPlayerOccupiedShipID = function() return own_actor end,
                    GetPlayerContainerID = function() return own_actor end,
                    GetContextByClass = function(id, cls)
                        if cls == 'sector' then return sector_a end
                        return id == own_actor and zone_own or zone_a
                    end,
                    GetObjectIDCode = function(id)
                        if id == remote_actor then return 'KHI-630' end
                        if id == npc_actor then return 'NPC-999' end
                        return 'HVC-420'
                    end,
                    GetComponentScreenPosition = function() return {onScreen = st.remote_on_screen} end,
                    GetDistanceBetween = function() return st.remote_distance end,
                    GetObjectPositionInSector = function(id) return st.poses[id] end,
                    GetZoneAt = function() return zone_a end,
                    SetObjectSectorPos = function(id, sec, pose)
                        assert(id == remote_actor and sec == sector_a, 'NEVER write own cockpit ship!')
                        assert(pose.yaw == 0.4 and pose.pitch == 0.5 and pose.roll == 0.6)
                        st.pending_write = {actor = id, pose = pose, at = st.now}
                        st.writes[#st.writes + 1] = st.pending_write
                    end,
                },
            }

            local cockpit = observer_mod.new_x4(pinned, own_index, function() return true end, {
                radar_fallback = true, target_index = remote_index, clock = function() return st.now end,
            })
            local map_binding = {epoch = epoch, index = map_index, sector_macro = 'cluster_01_sector001_macro',
                                 actor = '0x190', code = 'MAP-205'}
            local map_reader = {
                tick = function() return st.map_open and map_binding or nil end,
                matches = function(_, ep, sec, idx, act, code)
                    return st.map_open and ep == epoch and sec == map_binding.sector_macro
                        and idx == map_index and act == map_binding.actor and code == map_binding.code
                end,
                close = function() end,
            }
            local adapter = adapter_mod.new_x4({
                enabled = true, pinned = pinned, own_index = own_index, observer = cockpit,
                guard = function() return not st.paused end,
            })
            local ctrl = playback_mod.new({
                enabled = true, own_index = own_index, target_index = remote_index,
                allow_reacquire_align = true, adapter = adapter,
                clock = function() return st.now end, game = function() return st.gt end,
                paused = function() return st.paused end,
                record = function(ev)
                    if ev.kind == 'readback' or ev.kind == 'alignment_readback' then
                        st.readbacks[#st.readbacks + 1] = ev
                    end
                    return true
                end,
                fault = function(reason) st.faults[#st.faults + 1] = reason end,
                on_event = function(kind) st.events[#st.events + 1] = kind end,
            })
            local stream = stream_mod.new({cockpit = cockpit, map = map_reader}, epoch, {
                receive = function() return table.remove(st.incoming, 1) end,
                send = function(_, msg) st.sent[#st.sent + 1] = msg; return true end,
            }, prefix, function() return st.now end, ctrl)

            local function step(dt)
                st.now = st.now + dt
                if not st.paused then st.gt = st.gt + dt end
                if st.pending_write then
                    st.poses[st.pending_write.actor] = st.pending_write.pose
                    st.pending_write = nil
                end
                while #st.md_queue > 0 do
                    local r = table.remove(st.md_queue, 1)
                    st.md_handlers['X4AuthorityCompanyCockpitObserver.Result'](nil, r)
                end
                stream:tick(not st.paused)
            end

            local function latest_cockpit_serial()
                for i = #st.sent, 1, -1 do
                    local s = st.sent[i]:match('|VIEW|cockpit|(%d+)|')
                    if s then return tonumber(s) end
                end
                return 0
            end

            local function deliver(seq, x, vx, gen, serial)
                local ser = serial or latest_cockpit_serial()
                local vstr = vx and {vx, 0, 0} or {'-', '-', '-'}
                st.incoming[#st.incoming + 1] = prefix .. table.concat({
                    'REMOTE', 'cockpit', ser, epoch, remote_index, gen or gen1, seq,
                    'cluster_06_sector002_macro', x, 0, 0, vstr[1], vstr[2], vstr[3],
                    st.gt, st.now, st.now,
                }, '|')
            end

            -- 1. Selected HUD target playback with velocity (including repeated same-tick onUpdate calls!)
            step(0.05); step(0.05)
            assert(cockpit:mode() == 'hud', 'expected hud mode first')
            local hud_serial = latest_cockpit_serial()
            for seq = 1, 5 do
                deliver(seq, 1000 + seq * 4, seq == 1 and nil or 20, gen1, hud_serial)
                step(0.1); step(0.0); step(0.1)
            end
            assert(#st.writes >= 3 and #st.readbacks >= 2, 'expected active HUD playback writes and readbacks')
            local x_after_hud = st.poses[remote_actor].x
            assert(x_after_hud > 1006, 'expected HUD target movement')

            -- 2. Unlock target (no HUD lock, then lock unrelated NPC!) + open map independently -> seamless radar mode!
            st.hud_target = nil
            st.remote_on_screen = false
            st.map_open = true
            Menus[1].shown = true
            step(0.16); step(0.05)
            assert(cockpit:mode() == 'radar', 'expected seamless fallback to radar mode without HUD lock')
            assert(latest_cockpit_serial() == hud_serial, 'seamless hud->radar must not churn cockpit serial!')
            -- Verify independent map slot emitted VIEW|map while cockpit stayed alive!
            local saw_map_view = false
            for _, m in ipairs(st.sent) do
                if m:find('|VIEW|map|.*|' .. tostring(map_index) .. '|') then saw_map_view = true end
            end
            assert(saw_map_view, 'independent map view must subscribe alongside cockpit radar playback')

            -- Lock unrelated NPC ship while remote pilot stays on radar -> still stays on radar!
            st.hud_target = npc_actor
            for seq = 6, 9 do
                deliver(seq, 1000 + seq * 4, 20, gen1, hud_serial)
                step(0.1); step(0.1)
            end
            assert(cockpit:mode() == 'radar')
            local x_after_radar = st.poses[remote_actor].x
            assert(x_after_radar > x_after_hud + 10, 'expected continuous radar playback without HUD lock')

            -- Mismatched generation while active MUST be rejected!
            deliver(10, x_after_radar + 4, 20, gen_bad, hud_serial)
            step(0.05)
            assert(ctrl:stats().generation_rejections >= 1)

            -- 3. Five-second connection-loss limit: stop samples, watch extrapolation stop at 5.0 s!
            local loss_t0 = st.now
            local x_1s, x_4_5s, x_5_3s, x_6_1s, w_5_3s, w_6_1s
            for _ = 1, 65 do
                step(0.1)
                local e = st.now - loss_t0
                if not x_1s and e >= 1.0 then x_1s = st.poses[remote_actor].x end
                if not x_4_5s and e >= 4.5 then x_4_5s = st.poses[remote_actor].x end
                if not x_5_3s and e >= 5.3 then x_5_3s = st.poses[remote_actor].x; w_5_3s = #st.writes end
                if not x_6_1s and e >= 6.1 then x_6_1s = st.poses[remote_actor].x; w_6_1s = #st.writes end
            end
            assert(x_4_5s > x_1s + 50, 'expected 5s extrapolation')
            assert(x_6_1s == x_5_3s and w_6_1s == w_5_3s, 'must freeze after 5s cutoff')
            assert(ctrl:stats().stale_cutoffs == 1)

            -- 4. Revoke gen1 via END; late gen1 packet MUST be discarded; fresh gen2 >50 m away aligns ONCE then resumes ordinary playback!
            st.incoming[#st.incoming + 1] = prefix .. table.concat({
                'END', 'cockpit', hud_serial, epoch, remote_index, gen1, 'cluster_06_sector002_macro',
            }, '|')
            step(0.05)
            deliver(20, x_6_1s + 10, 20, gen1, hud_serial)
            step(0.05)
            assert(#st.writes == w_6_1s, 'revoked generation must never write')

            -- Fresh gen2 arrives 120 m ahead -> 1 authorized alignment + later readback -> ordinary <=50 m playback!
            local align_x = x_6_1s + 120
            deliver(1, align_x, nil, gen2, hud_serial)
            step(0.05)
            step(0.05)  -- Settles alignment_readback on later tick!
            assert(ctrl:stats().alignments == 1)
            -- Second unaligned >50 m jump must be refused!
            local w_before_bad = #st.writes
            deliver(2, align_x + 90, 20, gen2, hud_serial)
            step(0.05)
            assert(#st.writes == w_before_bad and ctrl:stats().refusals_over_50m >= 1)
            -- Ordinary <=50 m playback resumes cleanly under gen2!
            for seq = 3, 6 do
                deliver(seq, align_x + (seq - 2) * 4, 20, gen2, hud_serial)
                step(0.1); step(0.1)
            end
            assert(st.poses[remote_actor].x > align_x + 10)
            assert(st.poses[own_actor].x == 0 and st.poses[own_actor].y == 0 and st.poses[own_actor].z == 0)

            stream:close()
            local cstats = ctrl:stats()
            return {
                writes = #st.writes,
                readbacks = #st.readbacks,
                stale_cutoffs = cstats.stale_cutoffs,
                alignments = cstats.alignments,
                reacquisitions = cstats.reacquisitions,
                generation_rejections = cstats.generation_rejections,
                refusals_over_50m = cstats.refusals_over_50m,
                final_remote_x = st.poses[remote_actor].x,
                own_x = st.poses[own_actor].x,
            }
        """)
        self.assertGreater(summary['writes'], 20)
        self.assertGreater(summary['readbacks'], 20)
        self.assertEqual(summary['stale_cutoffs'], 1)
        self.assertEqual(summary['alignments'], 1)
        self.assertGreaterEqual(summary['generation_rejections'], 2)
        self.assertGreaterEqual(summary['refusals_over_50m'], 1)
        self.assertEqual(summary['own_x'], 0)


class CompanyCockpitLoopbackServiceTests(unittest.TestCase):
    def test_cockpit_radar_view_and_independent_map_over_real_loopback_service(self):
        class _DummySource:
            def __init__(self, epoch, index):
                self.registry = (epoch, index)
                self.local_generation = 'f' * 32

            def poll(self, _):
                return None

        epoch = 'a' * 32
        session = 'b' * 32
        sector_a = 'cluster_06_sector002_macro'
        sector_b = 'cluster_01_sector001_macro'
        service = CompanyMotionService()
        try:
            g_recv = service.admit(1, '1' * 32, 1073, sector_a, session, epoch)
            g_send = service.admit(2, '2' * 32, 1074, sector_a, session, epoch)
            g_map = service.admit(3, '3' * 32, 2050, sector_b, session, epoch)
            service.set_running(True)

            source = CompanyMotionSource(playback_index=1074, cockpit_radar_playback=True)
            source.configure(epoch, 1073, operational_bits='1' * 3000)
            sent_to_x4 = []
            observer = source.observe(lambda _: True)
            # Patch internal send for loopback inspection without a live X4 peer.
            observer._send = lambda msg: (sent_to_x4.append(msg) or True)
            client_recv = CompanyMotionClient(
                service.port, g_recv, _DummySource(epoch, 1073), lambda: True,
                on_snapshot=observer.snapshot, on_end=observer.end,
                on_transport_lost=observer.transport_lost,
                on_view_reset=observer.reset_world, trial_index=1074,
            )
            observer.bind_views(client_recv.set_views)

            # Remote pilot & map pilot sources
            pub_queue = []

            class _QueueSource:
                def __init__(self, idx, gen):
                    self.registry = (epoch, idx)
                    self.local_generation = gen
                    self.items = []

                def poll(self, _):
                    return self.items.pop(0) if self.items else None

            src_send = _QueueSource(1074, 'c' * 32)
            src_map = _QueueSource(2050, 'd' * 32)
            client_send = CompanyMotionClient(service.port, g_send, src_send, lambda: True)
            client_map = CompanyMotionClient(service.port, g_map, src_map, lambda: True)

            def pump_until(pred, timeout=4.0):
                deadline = time.monotonic() + timeout
                while not pred():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('loopback cockpit test timed out')
                    service.poll()
                    client_recv.poll()
                    client_send.poll()
                    client_map.poll()
                    time.sleep(0.005)

            try:
                pump_until(lambda: client_recv._running and client_send._running and client_map._running)
                # Receiving cockpit binds 1074 on cockpit slot AND 2050 on map slot simultaneously!
                observer.receive(['VIEW', 'cockpit', '1', epoch, sector_a, '1074', '0x200', 'KHI-630'])
                observer.receive(['VIEW', 'map', '1', epoch, sector_b, '2050', '0x400', 'MAP-205'])
                pump_until(lambda: client_recv._sent_view == {sector_a: frozenset({1074}), sector_b: frozenset({2050})})

                src_send.items.append({
                    'version': 3, 'epoch': epoch, 'generation': 'c' * 32, 'index': 1074, 'sequence': 1,
                    'sector_macro': sector_a, 'source_time': 10.0, 'capture_time': 20.0,
                    'x': 1000.0, 'y': 0.0, 'z': 0.0, 'real_velocity': None,
                })
                src_send.items.append({
                    'version': 3, 'epoch': epoch, 'generation': 'c' * 32, 'index': 1074, 'sequence': 2,
                    'sector_macro': sector_a, 'source_time': 10.2, 'capture_time': 20.2,
                    'x': 1004.0, 'y': 0.0, 'z': 0.0, 'real_velocity': {'x': 20.0, 'y': 0.0, 'z': 0.0},
                })
                src_map.items.append({
                    'version': 3, 'epoch': epoch, 'generation': 'd' * 32, 'index': 2050, 'sequence': 1,
                    'sector_macro': sector_b, 'source_time': 10.0, 'capture_time': 20.0,
                    'x': 500.0, 'y': 0.0, 'z': 0.0, 'real_velocity': None,
                })
                pump_until(lambda: (
                    any(m.startswith(f'REMOTE|cockpit|1|{epoch}|1074|{g_send["generation"]}|2|') for m in sent_to_x4)
                    and any(m.startswith(f'REMOTE|map|1|{epoch}|2050|{g_map["generation"]}|1|') for m in sent_to_x4)
                ))
                # Clearing map slot does NOT affect cockpit slot!
                observer.receive(['VIEW', 'map', '2', epoch, '-', '0', '-', '-'])
                pump_until(lambda: client_recv._sent_view == {sector_a: frozenset({1074})})
                # Dropping remote pilot's control link triggers transport_lost (NOT END), preserving 5s window!
                service.transport_lost(2)
                pump_until(lambda: client_recv.stats()['visibility_ends'] == 1)
                self.assertFalse(any(m.startswith('END|cockpit|') for m in sent_to_x4))
            finally:
                client_recv.close()
                client_send.close()
                client_map.close()
                source.close()
        finally:
            service.close()


if __name__ == '__main__':
    unittest.main()
