"""Offline LuaJIT 2.1 and loopback verification for opt-in receiving-cockpit Company ship playback.

Covers:
  1. Read-only MD XML contract for src/authority/md/company_cockpit_observer.xml (HUD target +
     nearby radar-visible unoccupied Company pilot without a target lock; own-ship exclusions).
  2. Real LuaJIT 2.1 integration of company_cockpit_observer.lua + company_observer_stream.lua +
     company_cockpit_playback.lua + company_hull_playback.lua + company_hull_playback_x4.lua +
     motion_buffer.lua + motion_playback.lua:
       - Selected HUD target playback with velocity & delayed later-update readbacks (elapsed > 0).
       - Seamless hud -> radar transition (target unlocked while nearby on radar) and untargeted
         radar-only discovery and continued playback with an unrelated Company HUD target.
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
            'set_value', 'do_if', 'do_elseif', 'do_else', 'do_while', 'do_all', 'break',
            'append_to_list', 'find_ship', 'do_for_each', 'raise_lua_event',
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
            CompanyMotionSource(cockpit_radar_playback=True, cockpit_max_pilots=4)
        with self.assertRaises(ValueError):
            CompanyMotionSource(cockpit_radar_playback=True, cockpit_max_pilots=0)
        with self.assertRaises(ValueError):
            CompanyMotionSource(playback_index=1074, cockpit_radar_playback=True, cockpit_max_pilots=3)
        with self.assertRaises(ValueError):
            CompanyMotionSource(cockpit_radar_playback=False, cockpit_max_pilots=3)

        multi_src = CompanyMotionSource(cockpit_radar_playback=True)
        try:
            self.assertTrue(multi_src.cockpit_radar_playback)
            self.assertEqual(multi_src.cockpit_max_pilots, 3)
            self.assertIsNone(multi_src.playback_index)
            self.assertIn('company_cockpit_observer.lua', company_motion_ui(multi_src))
            self.assertIn('company_cockpit_playback.lua', company_motion_ui(multi_src))
            self.assertIn('company_cockpit_observer.xml', company_motion_md(multi_src))
            cfg = multi_src.configure('a' * 32, 1073, operational_bits='1' * 2000)
            self.assertIs(cfg['cockpit_radar_playback'], True)
            self.assertEqual(cfg['cockpit_max_pilots'], 3)
            self.assertNotIn('playback_index', cfg)
        finally:
            multi_src.close()

        opt_src = CompanyMotionSource(playback_index=1074, cockpit_radar_playback=True)
        try:
            self.assertTrue(opt_src.cockpit_radar_playback)
            self.assertEqual(opt_src.cockpit_max_pilots, 1)
            self.assertIn('company_cockpit_observer.lua', company_motion_ui(opt_src))
            self.assertIn('company_cockpit_playback.lua', company_motion_ui(opt_src))
            self.assertIn('company_cockpit_observer.xml', company_motion_md(opt_src))
            cfg = opt_src.configure('a' * 32, 1073, operational_bits='1' * 2000)
            self.assertIs(cfg['cockpit_radar_playback'], True)
            self.assertEqual(cfg['cockpit_max_pilots'], 1)
            self.assertEqual(cfg['playback_index'], 1074)
        finally:
            opt_src.close()


@unittest.skipIf(luajit21 is None, 'lupa.luajit21 required')
class CompanyCockpitPlaybackLuaTests(unittest.TestCase):
    def test_x4_log_restarts_displacement_and_throttle_at_new_binding(self):
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        lua.globals()._ROOT = str(ROOT).replace('\\', '/')
        lua.execute(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local module = dofile(root .. 'company_cockpit_playback.lua')
            local now, logs = 10, {}
            local positions = {cockpit = 0, cockpit2 = 2000}
            package.loaded.ffi = {cdef = function() end, C = {
                GetCurrentGameTime = function() return now end,
                IsGamePaused = function() return false end,
            }}
            DebugError = function(line)
                if line:find('AUTH_COMPANY_COCKPIT_READBACK', 1, true) then
                    local fields = {}
                    for name, value in line:gmatch('(%w+)=([^ ]+)') do fields[name] = value end
                    logs[#logs + 1] = fields
                end
            end
            X4LAN_COMPANY_HULL_PLAYBACK_X4 = {new_x4 = function()
                return {bind = function(_, binding)
                    local slot = binding.slot
                    local function move(_, pose) positions[slot] = pose.x; return true end
                    return {
                        guard = function() return true end,
                        read = function() return {x = positions[slot], y = 0, z = 0} end,
                        write = move, align = move, close = function() end,
                    }
                end}
            end}
            local controller = module.new_multi_x4({index = 1}, {}, {cockpit = {}, cockpit2 = {}},
                function() return true end, function() return now end)
            local epoch, generation = ('a'):rep(32), ('b'):rep(32)
            local binding = {epoch = epoch, sector_macro = 'sector_a', index = 2,
                             actor = '0x20', code = 'ABC-002'}
            local sequence = 0
            local function push(slot, serial, x)
                sequence = sequence + 1
                return controller:sample(binding, serial, {
                    version = 3, epoch = binding.epoch, generation = generation,
                    sector_macro = binding.sector_macro, index = binding.index, sequence = sequence,
                    source_time = now, capture_time = now, x = x, y = 0, z = 0,
                    real_velocity = {x = 10, y = 0, z = 0},
                }, now, slot)
            end
            local function tick(count, step)
                for _ = 1, count do now = now + step; controller:tick(now) end
            end
            assert(push('cockpit', 1, 5))
            tick(7, .05)
            assert(#logs == 3 and logs[1].serial == '1')
            assert(tonumber(logs[1].displacement) >= 5)
            tick(1, .5)
            assert(#logs == 4 and tonumber(logs[4].displacement) > tonumber(logs[1].displacement))

            -- The same ship in another slot has its own origin and log budget.
            assert(push('cockpit2', 1, 2001))
            tick(2, .05)
            assert(#logs == 5 and logs[5].slot == 'cockpit2')
            assert(tonumber(logs[5].displacement) >= 1 and tonumber(logs[5].displacement) < 3)
            controller:view(nil, 2, 'cockpit2')

            -- The same slot and ship can now refer to a different view and sector.
            controller:view(nil, 2, 'cockpit')
            binding.sector_macro = 'sector_b'
            positions.cockpit = 10000
            assert(push('cockpit', 3, 10004))
            tick(2, .05)
            assert(#logs == 6 and logs[6].slot == 'cockpit' and logs[6].serial == '3')
            assert(logs[6].sector == 'sector_b')
            assert(tonumber(logs[6].displacement) >= 4 and tonumber(logs[6].displacement) < 6)

            -- Rejoining with a new grant must reset the origin even in the same view.
            controller:end_track(binding, 3, generation, 'cockpit')
            generation = ('c'):rep(32)
            positions.cockpit = 15000
            assert(push('cockpit', 3, 15003))
            tick(2, .05)
            assert(#logs == 7 and logs[7].generation == generation)
            assert(tonumber(logs[7].displacement) >= 3 and tonumber(logs[7].displacement) < 5)

            -- A loss and alignment on the same binding starts a new position baseline.
            tick(1, 5.1)
            local aligned, reason = push('cockpit', 3, 20000)
            assert(aligned and reason == 'aligned')
            tick(1, .05)
            local alignment = logs[#logs]
            assert(alignment.kind == 'alignment_readback' and alignment.displacement == '0')
            tick(1, .55)
            assert(push('cockpit', 3, 20002))
            tick(2, .05)
            local resumed = logs[#logs]
            assert(resumed.kind == 'readback' and resumed.serial == '3')
            assert(tonumber(resumed.displacement) >= 2 and tonumber(resumed.displacement) < 4)
            controller:close()
        """)

    def test_indexed_observer_reports_each_actual_withdrawal_reason_once(self):
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        lua.globals()._ROOT = str(ROOT).replace('\\', '/')
        lua.execute(r"""
            local module = dofile(_ROOT .. '/src/authority/lua/company_cockpit_observer.lua')
            local epoch = ('a'):rep(32)
            local pinned = {epoch=epoch, nonce=('b'):rep(32), issued_count=10}
            local target = {actor=22, space=1, sector_macro='sector_a', code='RUN-002'}
            local function run(index)
                local now, available, valid, verified, issue = 0, true, true, true, true
                local register, request
                local reasons = {}
                local reader = module.new(pinned, 1, {
                    target_index=index,
                    clock=function() return now end,
                    probe=function() return available and {mode='radar'} or nil end,
                    validate_actor=function() return valid end,
                    parse_actor=tonumber,
                    verify_radar_actor=function() return verified and target or nil end,
                    register=function(callback) register=callback; return true end,
                    issue=function(args) request=args; return issue end,
                    on_withdrawal=function(reason) reasons[#reasons+1]=reason end,
                })
                local function reply(status)
                    register(nil, table.concat({request.request_id,epoch,status,2,'22',
                        target.sector_macro,target.code,'radar'}, '|'))
                end
                local function establish()
                    now=now+.2
                    reader:tick()
                    reply('OBSERVED')
                    assert(reader:current().index==2)
                end
                local function fault(reason, effect)
                    local previous = #reasons
                    effect()
                    assert(reader:current()==nil)
                    if index then
                        assert(#reasons==previous+1 and reasons[#reasons]==reason)
                    else
                        assert(#reasons==0)
                    end
                end

                issue=false
                reader:tick()
                assert(#reasons==0)
                issue=true
                establish()
                fault('expired', function() now=now+.46 end)
                establish()
                now=now+.46
                assert(reader:current()==nil and #reasons==(index and 1 or 0))

                establish()
                fault('probe_unavailable', function() available=false end)
                available=true
                establish()
                fault('revalidation_failed', function() valid=false end)
                valid=true
                establish()
                fault('response_rejected', function()
                    now=now+.2
                    reader:tick()
                    register(nil,'other-request|ignored')
                    assert(reader:current())
                    reply('UNAVAILABLE')
                end)
                establish()
                fault('observation_rejected', function()
                    now=now+.2
                    reader:tick()
                    verified=false
                    reply('OBSERVED')
                end)
                verified=true
                establish()
                fault('issue_failed', function()
                    now=now+.2
                    issue=false
                    reader:tick()
                end)
                issue=true
                establish()
                fault('clock_invalid', function() now=now-.01 end)
                reader:close()
                assert(#reasons==(index and 7 or 0))
            end
            run(2)
            run(nil)
        """)

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
            local map_actor, idle_actor = 400, 500
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

            local withdrawal
            DebugError = function(message) withdrawal = message end
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
                if id == tostring(idle_actor) then
                    return 'player', true, true, false, false, false, nil, nil, false
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
                assert(args.target_index == remote_index, 'explicit index must reach MD in every mode')
                st.requested_mode = args.mode
                local mode = args.mode
                if (mode == 'hud' or mode == 'hudmap') and st.hud_target ~= remote_actor then
                    mode = 'radar'
                end
                local ok_ship = mode == 'hud' or st.remote_distance <= st.radar_range
                local reply
                if ok_ship then
                    reply = table.concat({
                        args.request_id, epoch, 'OBSERVED', tostring(remote_index),
                        ('0x%x'):format(remote_actor), 'cluster_06_sector002_macro', 'KHI-630', mode,
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
                    IsComponentOperational = function(id) return id == own_actor or id == remote_actor or id == idle_actor end,
                    IsComponentClass = function(id, cls)
                        if cls == 'sector' then return id == sector_a or id == sector_b end
                        if cls == 'zone' then return id == zone_a or id == zone_own end
                        return id == own_actor or id == remote_actor or id == idle_actor
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
                        if id == idle_actor then return 'IDL-500' end
                        return 'HVC-420'
                    end,
                    GetComponentScreenPosition = function(id) return {onScreen = id ~= remote_actor or st.remote_on_screen} end,
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
            local function playback_clock()
                if st.clock_reads then
                    st.clock_reads = st.clock_reads + 1
                    return st.now + st.clock_reads * 1e-6
                end
                return st.now
            end
            local ctrl = playback_mod.new({
                enabled = true, own_index = own_index, target_index = remote_index,
                allow_reacquire_align = true, adapter = adapter,
                clock = playback_clock, game = function() return st.gt end,
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
            }, prefix, playback_clock, ctrl)

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

            -- Preserve the configured HUD ship through map opening, then clear its HUD lock.
            st.map_open = true
            Menus[1].shown = true
            step(0.16); step(0.05)
            assert(cockpit:mode() == 'hudmap' and latest_cockpit_serial() == hud_serial)
            st.hud_target = nil
            st.remote_on_screen = false
            step(0.16); step(0.05)
            assert(cockpit:mode() == 'radar', 'expected seamless fallback to radar mode without HUD lock')
            assert(latest_cockpit_serial() == hud_serial, 'seamless hud->radar must not churn cockpit serial!')
            -- Verify independent map slot emitted VIEW|map while cockpit stayed alive!
            local saw_map_view = false
            for _, m in ipairs(st.sent) do
                if m:find('|VIEW|map|.*|' .. tostring(map_index) .. '|') then saw_map_view = true end
            end
            assert(saw_map_view, 'independent map view must subscribe alongside cockpit radar playback')

            -- An eligible but idle Company HUD target cannot divert indexed playback.
            st.map_open = false
            Menus[1].shown = false
            st.hud_target = idle_actor
            for seq = 6, 9 do
                deliver(seq, 1000 + seq * 4, 20, gen1, hud_serial)
                step(0.1); step(0.1)
            end
            assert(st.requested_mode == 'hud', 'idle ship must be a valid HUD candidate')
            assert(cockpit:mode() == 'radar' and cockpit:current().index == remote_index)
            assert(latest_cockpit_serial() == hud_serial, 'unrelated Company target must leave the route intact')
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
            -- A high-resolution clock can advance between datagrams in one game update.
            st.clock_reads = 0
            deliver(1, align_x, nil, gen2, hud_serial)
            deliver(2, align_x, nil, gen2, hud_serial)
            local readbacks_before_align = #st.readbacks
            step(0.05)
            assert(st.pending_write and #st.readbacks == readbacks_before_align)
            step(0.05)
            assert(#st.readbacks == readbacks_before_align + 1)
            st.clock_reads = nil
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

            -- The old sample may not be applied once the configured ship leaves radar.
            st.remote_distance = st.radar_range + 1
            local before_out_of_range = #st.writes
            deliver(7, st.poses[remote_actor].x + 3, 20, gen2, hud_serial)
            step(0.2); step(0.1)
            assert(cockpit:current() == nil)
            assert(withdrawal == '[X4LAN] AUTH_COMPANY_COCKPIT_WITHDRAWAL revalidation_failed')
            assert(latest_cockpit_serial() ~= hud_serial and #st.writes == before_out_of_range)

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

    def test_multi_pilot_three_slots_sticky_independent_loss_removal_and_reacquisition(self):
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        lua.globals()._ROOT = str(ROOT).replace('\\', '/')
        summary = lua.execute(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local adapter_mod = dofile(root .. 'company_hull_playback_x4.lua')
            local observer_mod = dofile(root .. 'company_cockpit_observer.lua')
            local playback_mod = dofile(root .. 'company_cockpit_playback.lua')
            local stream_mod = dofile(root .. 'company_observer_stream.lua')

            local epoch = ('a'):rep(32)
            local genB1, genB2 = ('1'):rep(32), ('4'):rep(32)
            local genC1 = ('2'):rep(32)
            local genD1 = ('3'):rep(32)
            local prefix = 'C|lease123|localgen|'

            local own_actor, actorB, actorC, actorD, map_actor = 100, 200, 201, 202, 400
            local own_index, idxB, idxC, idxD, idxMap = 1, 2, 3, 4, 5
            local sector_a, sector_b, zone_a, zone_own = 10, 20, 30, 31
            local pinned = {epoch = epoch, issued_count = 5048, nonce = ('e'):rep(32)}

            local st = {
                now = 10.0, gt = 50.0, paused = false, map_open = true,
                hud_target = actorB, radar_range = 40000,
                visible = {[actorB] = true, [actorC] = true, [actorD] = false},
                dists = {[actorB] = 800, [actorC] = 950, [actorD] = 1100},
                codes = {[own_actor] = 'OWN-001', [actorB] = 'PLB-002', [actorC] = 'PLC-003', [actorD] = 'PLD-004'},
                indices = {[actorB] = idxB, [actorC] = idxC, [actorD] = idxD},
                poses = {
                    [own_actor] = {x = 0, y = 0, z = 0, yaw = 0.1, pitch = 0.2, roll = 0.3},
                    [actorB] = {x = 0, y = 0, z = 800, yaw = 0.4, pitch = 0.5, roll = 0.6},
                    [actorC] = {x = 200, y = 40, z = 900, yaw = 0.7, pitch = 0.8, roll = 0.9},
                    [actorD] = {x = -100, y = -20, z = 1050, yaw = 0.2, pitch = 0.3, roll = 0.4},
                },
                pending_writes = {},
                writes_by_actor = {[actorB] = 0, [actorC] = 0, [actorD] = 0},
                readbacks_by_index = {[idxB] = 0, [idxC] = 0, [idxD] = 0},
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
            function GetComponentData(id, f1, f2)
                if id == tostring(sector_a) and f1 == 'macro' then return 'cluster_14_sector001_macro' end
                if id == tostring(sector_b) and f1 == 'macro' then return 'cluster_409_sector001_macro' end
                if id == tostring(own_actor) and f1 == 'maxradarrange' then return st.radar_range end
                local aid = tonumber(id)
                if st.visible[aid] ~= nil then
                    if f1 == 'owner' and f2 == 'isdocked' then
                        return 'player', false, false, false, nil, nil, false
                    end
                    return 'player', true, st.visible[aid], false, false, false, nil, nil, false
                end
                return nil
            end

            RegisterEvent = function(name, cb) st.md_handlers[name] = cb; return true end
            AddUITriggeredEvent = function(screen, control, args)
                assert(screen == 'X4AuthorityCompanyCockpitObserver' and control == 'Read' and args.mode == 'multi')
                local items = {}
                local used = {}
                if args.hud_mode == 'hud' and st.hud_target and st.visible[st.hud_target] then
                    local a = st.hud_target
                    used[a] = true
                    items[#items + 1] = table.concat({st.indices[a], ('0x%x'):format(a), 'cluster_14_sector001_macro', st.codes[a], 'hud'}, ',')
                end
                for _, a in ipairs({actorB, actorC, actorD}) do
                    if #items < (args.max_pilots or 3) and st.visible[a] and not used[a] then
                        used[a] = true
                        items[#items + 1] = table.concat({st.indices[a], ('0x%x'):format(a), 'cluster_14_sector001_macro', st.codes[a], 'radar'}, ',')
                    end
                end
                local entries = #items > 0 and table.concat(items, ';') or '-'
                st.md_queue[#st.md_queue + 1] = table.concat({args.request_id, epoch, 'MULTI', tostring(#items), entries}, '|')
                return true
            end

            package.loaded.ffi = {
                NULL = {},
                typeof = function() return true end,
                cdef = function() end,
                string = function(v) return v end,
                new = function(kind, val) return kind == 'UIPosRot[1]' and {[0] = {}} or val end,
                C = {
                    IsPlayerValid = function() return true end,
                    IsGamePaused = function() return st.paused end,
                    IsHUDActive = function() return true end,
                    IsExternalTargetMode = function() return false end,
                    IsExternalViewActive = function() return false end,
                    IsValidComponent = function(id) return id == own_actor or id == sector_a or id == sector_b or id == zone_a or id == zone_own or st.visible[id] == true end,
                    IsComponentOperational = function(id) return id == own_actor or st.visible[id] == true end,
                    IsComponentClass = function(id, cls)
                        if cls == 'sector' then return id == sector_a or id == sector_b end
                        if cls == 'zone' then return id == zone_a or id == zone_own end
                        return id == own_actor or st.visible[id] == true
                    end,
                    GetPlayerControlledShipID = function() return own_actor end,
                    GetPlayerOccupiedShipID = function() return own_actor end,
                    GetPlayerContainerID = function() return own_actor end,
                    GetContextByClass = function(id, cls)
                        if cls == 'sector' then return sector_a end
                        return id == own_actor and zone_own or zone_a
                    end,
                    GetObjectIDCode = function(id) return st.codes[id] end,
                    GetComponentScreenPosition = function() return {onScreen = true} end,
                    GetDistanceBetween = function(_, id) return st.dists[id] or 99999 end,
                    GetObjectPositionInSector = function(id) return st.poses[id] end,
                    GetZoneAt = function() return zone_a end,
                    SetObjectSectorPos = function(id, sec, pose)
                        assert(id ~= own_actor and sec == sector_a, 'NEVER write own cockpit ship!')
                        st.pending_writes[id] = pose
                        st.writes_by_actor[id] = (st.writes_by_actor[id] or 0) + 1
                    end,
                },
            }

            local multi_obs = observer_mod.new_multi_x4(pinned, own_index, function() return true end, {
                max_pilots = 3, clock = function() return st.now end,
            })
            local map_binding = {epoch = epoch, index = idxMap, sector_macro = 'cluster_409_sector001_macro',
                                 actor = '0x190', code = 'MAP-005'}
            local map_reader = {
                tick = function() return st.map_open and map_binding or nil end,
                matches = function(_, ep, sec, idx, act, code)
                    return st.map_open and ep == epoch and sec == map_binding.sector_macro
                        and idx == idxMap and act == map_binding.actor and code == map_binding.code
                end,
                close = function() end,
            }
            local adapters = {}
            for _, sname in ipairs({'cockpit', 'cockpit2', 'cockpit3'}) do
                adapters[sname] = adapter_mod.new_x4({
                    enabled = true, pinned = pinned, own_index = own_index,
                    observer = multi_obs.readers[sname], guard = function() return not st.paused end,
                })
            end
            local ctrl = playback_mod.new_multi({
                enabled = true, slot_names = {'cockpit', 'cockpit2', 'cockpit3'}, own_index = own_index,
                allow_reacquire_align = true, adapters = adapters,
                clock = function() return st.now end, game = function() return st.gt end,
                paused = function() return st.paused end,
                record = function(ev)
                    if ev.kind == 'readback' or ev.kind == 'alignment_readback' then
                        st.readbacks_by_index[ev.index] = (st.readbacks_by_index[ev.index] or 0) + 1
                    end
                    return true
                end,
                fault = function() end,
                on_event = function() end,
            })
            local readers = {map = map_reader}
            for k, v in pairs(multi_obs.readers) do readers[k] = v end
            local stream = stream_mod.new(readers, epoch, {
                receive = function() return table.remove(st.incoming, 1) end,
                send = function(_, msg) st.sent[#st.sent + 1] = msg; return true end,
            }, prefix, function() return st.now end, ctrl)

            local function step(dt)
                st.now = st.now + dt
                st.gt = st.gt + dt
                for id, pose in pairs(st.pending_writes) do st.poses[id] = pose end
                st.pending_writes = {}
                while #st.md_queue > 0 do
                    st.md_handlers['X4AuthorityCompanyCockpitObserver.Result'](nil, table.remove(st.md_queue, 1))
                end
                stream:tick(not st.paused)
            end

            local function slot_serial(slot)
                for i = #st.sent, 1, -1 do
                    local s = st.sent[i]:match('|VIEW|' .. slot .. '|(%d+)|')
                    if s then return tonumber(s) end
                end
                return 0
            end

            local function deliver(slot, idx, seq, pos, vel, gen)
                local vstr = vel and {vel[1], vel[2], vel[3]} or {'-', '-', '-'}
                st.incoming[#st.incoming + 1] = prefix .. table.concat({
                    'REMOTE', slot, slot_serial(slot), epoch, idx, gen, seq,
                    'cluster_14_sector001_macro', pos[1], pos[2], pos[3], vstr[1], vstr[2], vstr[3],
                    st.gt, st.now, st.now,
                }, '|')
            end

            -- 1. Two visible pilots with different velocities: Pilot B (HUD in cockpit) + Pilot C (radar in cockpit2) + Map!
            step(0.05); step(0.05)
            local b_ser, c_ser = slot_serial('cockpit'), slot_serial('cockpit2')
            assert(multi_obs:mode('cockpit') == 'hud' and multi_obs:mode('cockpit2') == 'radar')
            local seqB, seqC, seqD = 0, 0, 0
            for k = 1, 6 do
                seqB = seqB + 1; seqC = seqC + 1
                deliver('cockpit', idxB, seqB, {seqB * 3.6, seqB * 0.8, 800 + seqB * 2.4}, seqB == 1 and nil or {18, 4, 12}, genB1)
                deliver('cockpit2', idxC, seqC, {200 - seqC * 2.8, 40 + seqC * 2.0, 900 - seqC * 4.8}, seqC == 1 and nil or {-14, 10, -24}, genC1)
                step(0.1); step(0.1)
            end
            assert(st.poses[actorB].x > 12 and st.poses[actorC].x < 190 and st.poses[actorC].y > 46)

            -- 2. Third pilot D joins into cockpit3 while B transitions hud->radar seamlessly!
            st.hud_target = nil
            st.visible[actorD] = true
            step(0.16); step(0.05)
            assert(slot_serial('cockpit') == b_ser and slot_serial('cockpit2') == c_ser)
            assert(multi_obs:mode('cockpit') == 'radar' and multi_obs:mode('cockpit3') == 'radar')
            for k = 1, 4 do
                seqB = seqB + 1; seqC = seqC + 1; seqD = seqD + 1
                deliver('cockpit', idxB, seqB, {seqB * 3.6, seqB * 0.8, 800 + seqB * 2.4}, {18, 4, 12}, genB1)
                deliver('cockpit2', idxC, seqC, {200 - seqC * 2.8, 40 + seqC * 2.0, 900 - seqC * 4.8}, {-14, 10, -24}, genC1)
                deliver('cockpit3', idxD, seqD, {-100, -20 - seqD * 3.0, 1050 + seqD * 4.0}, seqD == 1 and nil or {0, -15, 20}, genD1)
                step(0.1); step(0.1)
            end
            assert(st.poses[actorD].z > 1056)

            -- 3. Independent 5s loss on Pilot B ONLY while Pilot C keeps streaming continuously!
            local b_loss_t0 = st.now
            local b_w_5_5, b_w_6_2, c_x_before_loss = nil, nil, st.poses[actorC].x
            for k = 1, 64 do
                if k % 2 == 0 then
                    seqC = seqC + 1
                    deliver('cockpit2', idxC, seqC, {200 - seqC * 2.8, 40 + seqC * 2.0, 900 - seqC * 4.8}, {-14, 10, -24}, genC1)
                end
                step(0.1)
                local e = st.now - b_loss_t0
                if not b_w_5_5 and e >= 5.5 then b_w_5_5 = st.writes_by_actor[actorB] end
                if not b_w_6_2 and e >= 6.2 then b_w_6_2 = st.writes_by_actor[actorB] end
            end
            assert(b_w_5_5 == b_w_6_2 and ctrl:slot_stats('cockpit').stale_cutoffs == 1)
            assert(ctrl:slot_stats('cockpit2').stale_cutoffs == 0)
            assert(st.poses[actorC].x < c_x_before_loss - 60, 'Pilot C must keep moving during Pilot B 5s cutoff')

            -- 4. Remove Pilot B (END genB1 + remove from radar) -> cockpit clears, Pilot C stays in cockpit2 with zero serial churn and keeps moving!
            st.incoming[#st.incoming + 1] = prefix .. table.concat({'END', 'cockpit', b_ser, epoch, idxB, genB1, 'cluster_14_sector001_macro'}, '|')
            step(0.05)
            st.visible[actorB] = false
            step(0.16); step(0.05)
            assert(multi_obs.readers.cockpit:current() == nil)
            assert(slot_serial('cockpit2') == c_ser, 'Pilot C must remain sticky in cockpit2 without serial churn')
            local c_x_after_remove_start = st.poses[actorC].x
            for k = 1, 5 do
                seqC = seqC + 1
                deliver('cockpit2', idxC, seqC, {200 - seqC * 2.8, 40 + seqC * 2.0, 900 - seqC * 4.8}, {-14, 10, -24}, genC1)
                step(0.1); step(0.1)
            end
            assert(st.poses[actorC].x < c_x_after_remove_start - 10, 'Pilot C must keep moving after Pilot B removal')

            -- 5. Pilot B returns on fresh genB2 120 m ahead -> late genB1 rejected, genB2 aligns once on next update, resumes ordinary playback without affecting C!
            st.visible[actorB] = true
            step(0.16); step(0.05)
            local b_new_ser = slot_serial('cockpit')
            assert(b_new_ser > b_ser)
            local b_w_before_revoked = st.writes_by_actor[actorB]
            deliver('cockpit', idxB, 99, {st.poses[actorB].x + 5, st.poses[actorB].y, st.poses[actorB].z}, {18, 4, 12}, genB1)
            step(0.05)
            assert(st.writes_by_actor[actorB] == b_w_before_revoked, 'revoked genB1 must be rejected after re-entry')
            local align_x = st.poses[actorB].x + 120
            deliver('cockpit', idxB, 1, {align_x, st.poses[actorB].y, st.poses[actorB].z}, nil, genB2)
            step(0.05); step(0.05)
            assert(ctrl:slot_stats('cockpit').alignments == 1)
            for s = 2, 5 do
                deliver('cockpit', idxB, s, {align_x + (s - 1) * 3.6, st.poses[actorB].y + (s - 1) * 0.8, st.poses[actorB].z + (s - 1) * 2.4}, {18, 4, 12}, genB2)
                step(0.1); step(0.1)
            end
            assert(st.poses[actorB].x > align_x + 8)
            assert(st.poses[own_actor].x == 0 and st.poses[own_actor].y == 0 and st.poses[own_actor].z == 0)
            local st_all = ctrl:stats()
            stream:close()
            return {
                writesB = st.writes_by_actor[actorB], writesC = st.writes_by_actor[actorC], writesD = st.writes_by_actor[actorD],
                rbB = st.readbacks_by_index[idxB], rbC = st.readbacks_by_index[idxC], rbD = st.readbacks_by_index[idxD],
                b_stale = st_all.slots.cockpit.stale_cutoffs, c_stale = st_all.slots.cockpit2.stale_cutoffs,
                b_align = st_all.slots.cockpit.alignments, c_align = st_all.slots.cockpit2.alignments,
                b_gen_rej = st_all.slots.cockpit.generation_rejections,
                own_x = st.poses[own_actor].x,
            }
        """)
        self.assertGreater(summary['writesB'], 15)
        self.assertGreater(summary['writesC'], 40)
        self.assertGreater(summary['writesD'], 5)
        self.assertEqual((summary['b_stale'], summary['c_stale']), (1, 0))
        self.assertEqual((summary['b_align'], summary['c_align']), (1, 0))
        self.assertGreaterEqual(summary['b_gen_rej'], 1)
        self.assertEqual(summary['own_x'], 0)

    def test_multi_observer_sticky_slots_survive_luajit_uint64_cdata_reordering(self):
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        lua.globals()._ROOT = str(ROOT).replace('\\', '/')
        ok = lua.execute(r"""
            local ffi = require('ffi')
            local observer_mod = dofile(_ROOT .. '/src/authority/lua/company_cockpit_observer.lua')
            local epoch = ('a'):rep(32)
            local pinned = {epoch = epoch, issued_count = 100, nonce = ('f'):rep(32)}
            local now = 1.0
            local handler, pending_id = nil, nil
            local order = {
                {2, '0x178fba', 'WKS-310', 'hud'},
                {4, '0x179080', 'MZQ-490', 'radar'},
                {3, '0x179002', 'UNS-575', 'radar'},
            }
            local multi = observer_mod.new_multi(pinned, 1, 3, {
                clock = function() return now end,
                probe = function()
                    return {
                        mode = 'multi', hud_mode = order[1][4] == 'hud' and 'hud' or 'none',
                        hud_hull = order[1][4] == 'hud' and {
                            actor = ffi.new('uint64_t', tonumber(order[1][2])),
                            space = ffi.new('uint64_t', 10),
                            sector_macro = 'cluster_14_sector001_macro', code = order[1][3],
                        } or nil,
                    }
                end,
                parse_actor = function(text) return ffi.new('uint64_t', tonumber(text)) end,
                verify_radar_actor = function(actor_id, sector, code)
                    return {actor = actor_id, space = ffi.new('uint64_t', 10), sector_macro = sector, code = code}
                end,
                validate_actor = function() return true end,
                register = function(cb) handler = cb; return true end,
                issue = function(args) pending_id = args.request_id; return true end,
            })
            local function reply()
                local chunks = {}
                for _, item in ipairs(order) do
                    chunks[#chunks + 1] = table.concat({item[1], item[2], 'cluster_14_sector001_macro', item[3], item[4]}, ',')
                end
                handler(nil, table.concat({pending_id, epoch, 'MULTI', tostring(#chunks), table.concat(chunks, ';')}, '|'))
            end
            multi.readers.cockpit:tick()
            reply()
            assert(multi.readers.cockpit:current().index == 2)
            assert(multi.readers.cockpit2:current().index == 4)
            assert(multi.readers.cockpit3:current().index == 3)
            -- Reverse distance order and clear HUD lock with freshly allocated uint64_t cdata objects!
            order = {
                {3, '0x179002', 'UNS-575', 'radar'},
                {4, '0x179080', 'MZQ-490', 'radar'},
                {2, '0x178fba', 'WKS-310', 'radar'},
            }
            now = now + 0.2
            multi.readers.cockpit:tick()
            reply()
            assert(multi.readers.cockpit:current().index == 2, 'cockpit slot must stay sticky on uint64_t cdata!')
            assert(multi.readers.cockpit2:current().index == 4, 'cockpit2 slot must stay sticky on uint64_t cdata!')
            assert(multi.readers.cockpit3:current().index == 3, 'cockpit3 slot must stay sticky on uint64_t cdata!')
            assert(multi:mode('cockpit') == 'radar')
            multi:close()
            return true
        """)
        self.assertTrue(ok)


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

    def test_multi_pilot_loopback_independent_loss_removal_and_map_interest(self):
        from authority.company_motion_reconnect import ReconnectingCompanyMotionClient

        class _QueueSource:
            def __init__(self, ep, idx, gen):
                self.registry = (ep, idx)
                self.local_generation = gen
                self.items = []

            def poll(self, _):
                return self.items.pop(0) if self.items else None

        epoch, session = 'a' * 32, 'b' * 32
        sector_a, sector_b = 'cluster_14_sector001_macro', 'cluster_409_sector001_macro'
        service = CompanyMotionService()
        try:
            g_recv = service.admit(1, '1' * 32, 1, sector_a, session, epoch)
            g_b = service.admit(2, '2' * 32, 2, sector_a, session, epoch)
            g_c = service.admit(3, '3' * 32, 3, sector_a, session, epoch)
            g_map = service.admit(4, '4' * 32, 5, sector_b, session, epoch)
            service.set_running(True)

            source = CompanyMotionSource(cockpit_radar_playback=True)
            source.configure(epoch, 1, operational_bits='1' * 10)
            sent_to_x4 = []
            client_recv = ReconnectingCompanyMotionClient.observing(
                service.port, g_recv, source, lambda _: True,
            )
            observer = source._observer
            observer._send = lambda msg: (sent_to_x4.append(msg) or True)

            src_b = _QueueSource(epoch, 2, 'c' * 32)
            src_c = _QueueSource(epoch, 3, 'd' * 32)
            src_map = _QueueSource(epoch, 5, 'e' * 32)
            cli_b = CompanyMotionClient(service.port, g_b, src_b, lambda _: True)
            cli_c = CompanyMotionClient(service.port, g_c, src_c, lambda _: True)
            cli_map = CompanyMotionClient(service.port, g_map, src_map, lambda _: True)

            b_alive = True

            def pump_until(pred, timeout=4.0):
                deadline = time.monotonic() + timeout
                while not pred():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('multi-pilot loopback timed out')
                    service.poll()
                    client_recv.poll()
                    if b_alive:
                        cli_b.poll()
                    cli_c.poll()
                    cli_map.poll()
                    time.sleep(0.005)

            try:
                pump_until(lambda: client_recv._client is not None and client_recv._client._running
                           and cli_b._running and cli_c._running and cli_map._running)
                observer.receive(['VIEW', 'cockpit', '1', epoch, sector_a, '2', '0x200', 'PLB-002'])
                observer.receive(['VIEW', 'cockpit2', '1', epoch, sector_a, '3', '0x201', 'PLC-003'])
                observer.receive(['VIEW', 'map', '1', epoch, sector_b, '5', '0x400', 'MAP-005'])
                pump_until(lambda: client_recv._client._sent_view == {
                    sector_a: frozenset({2, 3}), sector_b: frozenset({5}),
                })
                src_b.items.append({'version': 3, 'epoch': epoch, 'generation': 'c' * 32, 'index': 2, 'sequence': 1,
                                    'sector_macro': sector_a, 'source_time': 10.0, 'capture_time': 20.0,
                                    'x': 10.0, 'y': 0.0, 'z': 800.0, 'real_velocity': None})
                src_c.items.append({'version': 3, 'epoch': epoch, 'generation': 'd' * 32, 'index': 3, 'sequence': 1,
                                    'sector_macro': sector_a, 'source_time': 10.0, 'capture_time': 20.0,
                                    'x': 200.0, 'y': 40.0, 'z': 900.0, 'real_velocity': None})
                src_map.items.append({'version': 3, 'epoch': epoch, 'generation': 'e' * 32, 'index': 5, 'sequence': 1,
                                      'sector_macro': sector_b, 'source_time': 10.0, 'capture_time': 20.0,
                                      'x': 50.0, 'y': 0.0, 'z': 0.0, 'real_velocity': None})
                pump_until(lambda: (
                    any(m.startswith(f'REMOTE|cockpit|1|{epoch}|2|') and len(m.split('|')) == 17 for m in sent_to_x4)
                    and any(m.startswith(f'REMOTE|cockpit2|1|{epoch}|3|') and len(m.split('|')) == 17 for m in sent_to_x4)
                    and any(m.startswith(f'REMOTE|map|1|{epoch}|5|') and len(m.split('|')) == 16 for m in sent_to_x4)
                ))
                # Transport loss on Pilot B only -> no END|cockpit, and Pilot C keeps receiving live samples!
                service.transport_lost(2)
                b_alive = False
                src_c.items.append({'version': 3, 'epoch': epoch, 'generation': 'd' * 32, 'index': 3, 'sequence': 2,
                                    'sector_macro': sector_a, 'source_time': 10.2, 'capture_time': 20.2,
                                    'x': 197.2, 'y': 42.0, 'z': 895.2, 'real_velocity': {'x': -14.0, 'y': 10.0, 'z': -24.0}})
                pump_until(lambda: any(m.startswith(f'REMOTE|cockpit2|1|{epoch}|3|{g_c["generation"]}|2|') for m in sent_to_x4))
                self.assertFalse(any(m.startswith('END|cockpit|') for m in sent_to_x4))
                self.assertFalse(any(m.startswith('END|cockpit2|') for m in sent_to_x4))
                # Revoking Pilot B (via re-admit) sends END|cockpit ONLY; Pilot C continues unaffected!
                g_b2 = service.admit(5, '5' * 32, 2, sector_a, session, epoch)
                pump_until(lambda: any(m.startswith(f'END|cockpit|1|{epoch}|2|{g_b["generation"]}|') for m in sent_to_x4))
                self.assertFalse(any(m.startswith('END|cockpit2|') for m in sent_to_x4))
                self.assertEqual([r[0] for r in observer.resume_routes()], [3])
                self.assertNotEqual(g_b2['generation'], g_b['generation'])
            finally:
                client_recv.close()
                cli_b.close()
                cli_c.close()
                cli_map.close()
                source.close()
        finally:
            service.close()


if __name__ == '__main__':
    unittest.main()
