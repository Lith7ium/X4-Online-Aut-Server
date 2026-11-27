"""Offline LuaJIT 2.1 and Python tests for opt-in one-shot (constantvel=false) native
velocity integration into the genuine receiving cockpit.

NOTE: These offline tests use a mocked X4 physics/MD harness in LuaJIT 2.1 to exercise
the real Lua modules (motion_buffer, motion_playback, company_hull_playback,
company_hull_playback_x4, company_cockpit_playback, company_cockpit_velocity,
company_cockpit_observer, company_observer_stream). They are synthetic/mocked checks,
not native X4 evidence.
"""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

try:
    from lupa import luajit21
except ImportError:  # pragma: no cover
    luajit21 = None

from authority.client_seat_overlay import company_motion_md, company_motion_ui
from authority.company_motion_source import CompanyMotionSource

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / 'src/authority/md/company_cockpit_velocity.xml'


class CompanyCockpitVelocityMdContractTests(unittest.TestCase):
    def test_md_uses_only_constantvel_false_and_enforces_safety_guards(self):
        root = ET.parse(MD_PATH).getroot()
        self.assertEqual(root.tag, 'mdscript')
        self.assertEqual(root.attrib.get('name'), 'X4AuthorityCompanyCockpitVelocity')
        vel_nodes = list(root.iter('set_object_velocity'))
        self.assertEqual(len(vel_nodes), 2)
        for node in vel_nodes:
            self.assertEqual(node.attrib.get('constantvel'), 'false')
        text = MD_PATH.read_text(encoding='utf-8')
        self.assertNotIn('constantvel="true"', text)
        guards = ' '.join(node.get('value') or node.get('exact', '') for node in root.iter())
        for required in (
            "$slot == 'cockpit' or $slot == 'cockpit2' or $slot == 'cockpit3'",
            'State.$epoch == $args.$epoch',
            'not md.X4AuthorityShipRegistry.State.$faulted',
            '$ship != player.controlled',
            'not $ship.pilot.exists',
            'not $ship.assignedpilot.exists',
            '$basisError lt 0.00000001',
            '$active_ship == $ship',
            'State.$cockpit',
            'State.$cockpit2',
            'State.$cockpit3',
            '$args.$vx ge -2800 and $args.$vx le 2800',
        ):
            self.assertIn(required, guards)
        # Stop branch must NOT require currentradarrange so withdrawal out of radar still zeroes velocity.
        stop_branch = next(
            node for node in root.iter('do_if')
            if node.get('value') == "$op == 'stop' and $active_ship == $ship"
        )
        self.assertNotIn('currentradarrange', ET.tostring(stop_branch, encoding='unicode'))


class CompanyCockpitVelocityConfigTests(unittest.TestCase):
    def test_default_off_and_explicit_opt_in_overlay_selection(self):
        default_src = CompanyMotionSource(cockpit_radar_playback=True, cockpit_continuous_playback=True)
        try:
            self.assertFalse(default_src.cockpit_native_velocity)
            self.assertNotIn('company_cockpit_velocity.lua', company_motion_ui(default_src))
            self.assertNotIn('company_cockpit_velocity.xml', company_motion_md(default_src))
            cfg = default_src.configure('a' * 32, 1, operational_bits='1' * 10)
            self.assertNotIn('cockpit_native_velocity', cfg)
        finally:
            default_src.close()

        with self.assertRaises(ValueError):
            CompanyMotionSource(cockpit_native_velocity=True)
        with self.assertRaises(ValueError):
            CompanyMotionSource(cockpit_radar_playback=True, cockpit_native_velocity='yes')

        opt_src = CompanyMotionSource(
            cockpit_radar_playback=True,
            cockpit_continuous_playback=True,
            cockpit_native_velocity=True,
        )
        try:
            self.assertTrue(opt_src.cockpit_native_velocity)
            ui = company_motion_ui(opt_src)
            md = company_motion_md(opt_src)
            self.assertIn('company_cockpit_velocity.lua', ui)
            self.assertIn('company_cockpit_velocity.xml', md)
            ui_keys = list(ui.keys())
            self.assertLess(ui_keys.index('company_cockpit_velocity.lua'), ui_keys.index('company_motion_source.lua'))
            cfg = opt_src.configure('a' * 32, 1, operational_bits='1' * 10)
            self.assertIs(cfg['cockpit_native_velocity'], True)
            self.assertIs(cfg['cockpit_continuous_playback'], True)
        finally:
            opt_src.close()


@unittest.skipIf(luajit21 is None, 'lupa.luajit21 required')
class CompanyCockpitNativeVelocityLuaIntegrationTests(unittest.TestCase):
    def _run_lua(self, body):
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        lua.globals()._ROOT = str(ROOT).replace('\\', '/')
        return lua.execute(body)

    def test_forward_turn_5s_cutoff_and_withdrawal_without_double_movement_or_snapback(self):
        summary = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local native = dofile(root .. 'company_hull_playback_x4.lua')
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local cockpit = dofile(root .. 'company_cockpit_playback.lua')

            local epoch = ('a'):rep(32)
            local gen1, gen2 = ('1'):rep(32), ('2'):rep(32)
            local own_index, target_index, other_index = 1, 2, 3
            local own_actor, target_actor, other_actor = 101, 202, 203
            local sector_id, zone_id = 303, 404

            local function dist3(a, b)
                return math.sqrt((a.x - b.x)^2 + (a.y - b.y)^2 + (a.z - b.z)^2)
            end
            local function norm3(v)
                return math.sqrt(v.x^2 + v.y^2 + v.z^2)
            end

            local function run_scenario(continuous_mode)
                local st = {
                    now = 10.0, gt = 50.0, paused = false,
                    can_observe = {[target_index] = true, [other_index] = true},
                    in_radar = {[target_actor] = true, [other_actor] = true},
                    poses = {
                        [own_actor] = {x = 0, y = 0, z = 0, yaw = 0, pitch = 0, roll = 0},
                        [target_actor] = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0},
                        [other_actor] = {x = 500, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0},
                    },
                    last_set_pos = {
                        [target_actor] = {x = 0, y = 0, z = 1000},
                        [other_actor] = {x = 500, y = 0, z = 1000},
                    },
                    last_set_at = {
                        [target_actor] = 10.0,
                        [other_actor] = 10.0,
                    },
                    native_vel = {
                        [target_actor] = {x = 0, y = 0, z = 0},
                        [other_actor] = {x = 0, y = 0, z = 0},
                    },
                    md_active = {},
                    md_queue = {},
                    md_handler = nil,
                    readbacks = {},
                    deviations = 0,
                    vel_events = {},
                    stop_snapbacks = {},
                    faults = {},
                }

                local views = {
                    cockpit = {epoch = epoch, index = target_index, actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a'},
                    cockpit2 = {epoch = epoch, index = other_index, actor = '0xcb', code = 'OTH-003', sector_macro = 'sector_a'},
                }
                local actor_by_hex = {['0xca'] = target_actor, ['0xcb'] = other_actor}
                local code_by_actor = {[target_actor] = 'TGT-002', [other_actor] = 'OTH-003'}

                local function make_observer(slot)
                    local obs_row = views[slot]
                    return {
                        matches = function(_, ep, frame, idx, act, code)
                            return st.can_observe[idx] == true and ep == obs_row.epoch
                                and frame == obs_row.sector_macro and idx == obs_row.index
                                and act == obs_row.actor and code == obs_row.code
                        end,
                        is_active_pilot = function(_, idx) return st.can_observe[idx] == true end,
                        current_radar_range = function() return 40000 end,
                    }
                end

                local function make_vel(slot)
                    return velocity_mod.new({
                        enabled = true,
                        slot = slot,
                        pinned = {epoch = epoch, issued_count = 10},
                        own_index = own_index,
                        clock = function() return st.now end,
                        register = function(cb) st.md_handler = cb; return true end,
                        issue = function(args)
                            -- Queue MD command to execute on the NEXT frame (1-frame async MD latency).
                            args._queued_at = st.now
                            st.md_queue[#st.md_queue + 1] = args
                            return true
                        end,
                        on_event = function(row)
                            st.vel_events[#st.vel_events + 1] = row
                            if row.op == 'stop' and row.ok then
                                st.stop_snapbacks[#st.stop_snapbacks + 1] = row.snapback_m
                            end
                        end,
                    })
                end

                local vels = {cockpit = make_vel('cockpit'), cockpit2 = make_vel('cockpit2')}
                local adapters = {}
                for _, slot in ipairs({'cockpit', 'cockpit2'}) do
                    adapters[slot] = native.new({
                        enabled = true,
                        pinned = {epoch = epoch, issued_count = 10},
                        own_index = own_index,
                        observer = make_observer(slot),
                        guard = function() return not st.paused end,
                        velocity = vels[slot],
                    }, {
                        cockpit = function()
                            if st.paused then return nil end
                            return {actor = own_actor, sector = sector_id, zone = zone_id}
                        end,
                        target = function(actor_hex)
                            local aid = actor_by_hex[actor_hex]
                            if not aid then return nil end
                            return {actor = aid, sector = sector_id, zone = zone_id,
                                sector_macro = 'sector_a', code = code_by_actor[aid]}
                        end,
                        read = function(aid)
                            local p = st.poses[aid]
                            return {x = p.x, y = p.y, z = p.z, yaw = p.yaw, pitch = p.pitch, roll = p.roll}
                        end,
                        write = function(aid, sec, pose)
                            assert(aid ~= own_actor and sec == sector_id, 'own ship must never be written')
                            st.poses[aid] = {x = pose.x, y = pose.y, z = pose.z,
                                yaw = pose.yaw, pitch = pose.pitch, roll = pose.roll}
                            st.last_set_pos[aid] = {x = pose.x, y = pose.y, z = pose.z}
                            st.last_set_at[aid] = st.now
                            return true
                        end,
                        destination_zone = function() return zone_id end,
                        within_radar = function() return true end,
                    })
                end

                local ctrl = cockpit.new_multi({
                    enabled = true,
                    continuous_playback = continuous_mode,
                    native_velocity = true,
                    allow_reacquire_align = true,
                    own_index = own_index,
                    slot_names = {'cockpit', 'cockpit2'},
                    adapters = adapters,
                    clock = function() return st.now end,
                    game = function() return st.gt end,
                    paused = function() return st.paused end,
                    record = function(ev)
                        if ev.kind == 'readback' then
                            st.readbacks[#st.readbacks + 1] = ev
                        elseif ev.kind == 'position_deviation' then
                            st.deviations = st.deviations + 1
                        end
                        return true
                    end,
                    fault = function(reason) st.faults[#st.faults + 1] = reason end,
                })

                local function basis_from_yaw(yaw)
                    -- X4 +yaw rotates forward (+Z) toward +X.
                    local s, c = math.sin(yaw), math.cos(yaw)
                    return {x = c, y = 0, z = -s}, {x = 0, y = 1, z = 0}, {x = s, y = 0, z = c}
                end

                local function process_md()
                    local q = st.md_queue
                    st.md_queue = {}
                    for _, args in ipairs(q) do
                        local aid = actor_by_hex[args.actor]
                        local status = 'refused'
                        local before_pos, before_vel
                        if args.op == 'set' and aid and st.in_radar[aid] then
                            st.native_vel[aid] = {x = args.vx, y = args.vy, z = args.vz}
                            st.md_active[args.slot] = aid
                            status = 'set'
                        elseif args.op == 'stop' and aid and st.md_active[args.slot] == aid then
                            local p = st.poses[aid]
                            before_pos = {x = p.x, y = p.y, z = p.z}
                            before_vel = {x = st.native_vel[aid].x, y = st.native_vel[aid].y, z = st.native_vel[aid].z}
                            -- Model X4's unanchored snap-back hazard: if SetObjectSectorPos was NOT
                            -- called to anchor the live pose when stop was queued, snap back!
                            local anchor = st.last_set_pos[aid]
                            if st.last_set_at[aid] ~= args._queued_at then
                                st.poses[aid].x, st.poses[aid].y, st.poses[aid].z = anchor.x, anchor.y, anchor.z
                            end
                            st.native_vel[aid] = {x = 0, y = 0, z = 0}
                            st.md_active[args.slot] = nil
                            status = 'stopped'
                        end
                        local reply = tostring(args.request) .. '|' .. status
                        if status ~= 'refused' then
                            local p = st.poses[aid]
                            local v = st.native_vel[aid]
                            local right, up, fwd = basis_from_yaw(p.yaw)
                            local lv = {
                                x = v.x * right.x + v.y * right.y + v.z * right.z,
                                y = v.x * up.x + v.y * up.y + v.z * up.z,
                                z = v.x * fwd.x + v.y * fwd.y + v.z * fwd.z,
                            }
                            reply = reply .. ('|%s|sector_a|zone1|%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f'):format(
                                args.actor, st.gt, p.x, p.y, p.z, lv.x, lv.y, lv.z,
                                right.x, right.y, right.z, up.x, up.y, up.z, fwd.x, fwd.y, fwd.z)
                            if status == 'set' then
                                reply = reply .. ('|%.6f,%.6f,%.6f'):format(args.vx, args.vy, args.vz)
                            else
                                reply = reply .. ('|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f'):format(
                                    before_pos.x, before_pos.y, before_pos.z,
                                    before_vel.x, before_vel.y, before_vel.z)
                            end
                        end
                        st.md_handler(nil, reply)
                    end
                end

                local function step(dt)
                    st.now = st.now + dt
                    if not st.paused then
                        st.gt = st.gt + dt
                        -- Realistic X4 physics integration of active native linear velocity!
                        for _, aid in ipairs({target_actor, other_actor}) do
                            local v = st.native_vel[aid]
                            local p = st.poses[aid]
                            p.x = p.x + v.x * dt
                            p.y = p.y + v.y * dt
                            p.z = p.z + v.z * dt
                        end
                    end
                    process_md()
                    ctrl:view(st.can_observe[target_index] and views.cockpit or nil, 1, 'cockpit')
                    ctrl:view(st.can_observe[other_index] and views.cockpit2 or nil, 1, 'cockpit2')
                    ctrl:tick(st.now)
                end

                local seq = {[target_index] = 0, [other_index] = 0}
                local function push_sample(slot, idx, gen, x, y, z, vx, vy, vz, yaw)
                    seq[idx] = seq[idx] + 1
                    local sample = {
                        version = 4, epoch = epoch, index = idx, generation = gen,
                        sector_macro = 'sector_a', sequence = seq[idx],
                        source_time = st.gt, capture_time = st.now,
                        x = x, y = y, z = z,
                        real_velocity = vx and {x = vx, y = vy, z = vz} or nil,
                        attitude = {yaw = yaw or 0, pitch = 0, roll = 0},
                    }
                    return ctrl:sample(views[slot], 1, sample, st.now, slot)
                end

                -- 1. Forward flight at +60 m/s for 2.0 s (plus second pilot in cockpit2 at +30 m/s X).
                local rx, rz, ryaw = 0.0, 1000.0, 0.0
                local ox = 500.0
                assert(push_sample('cockpit', target_index, gen1, rx, 0, rz, nil, nil, nil, 0))
                assert(push_sample('cockpit2', other_index, gen1, ox, 0, 1000, nil, nil, nil, 0))
                for k = 1, 20 do
                    step(0.05)
                    rx, rz = rx, rz + 6.0
                    ox = ox + 3.0
                    step(0.05)
                    assert(push_sample('cockpit', target_index, gen1, rx, 0, rz, 0, 0, 60, 0))
                    assert(push_sample('cockpit2', other_index, gen1, ox, 0, 1000, 30, 0, 0, math.pi / 2))
                end
                step(0.05)
                local fwd_disp = st.poses[target_actor].z - 1000.0
                -- True distance after 2.0 s at 60 m/s is ~120 m (lagged ~9 m by 0.15 s interpolation delay).
                -- Double movement would place it around ~220-240 m!
                assert(fwd_disp > 100 and fwd_disp < 130, 'unexpected forward displacement (double movement?): ' .. tostring(fwd_disp))
                assert(math.abs(st.native_vel[target_actor].z - 60) < 1.0, 'expected ~60 m/s native forward velocity')

                -- 2. Coupled turn 0 -> 35 deg + speed ramp 60 -> 90 m/s over 1.5 s.
                local turn_steps = 15
                local vx, vz = 0.0, 60.0
                for k = 1, turn_steps do
                    local frac = k / turn_steps
                    ryaw = math.rad(35.0) * frac
                    local spd = 60.0 + 30.0 * frac
                    vx, vz = spd * math.sin(ryaw), spd * math.cos(ryaw)
                    rx, rz = rx + vx * 0.1, rz + vz * 0.1
                    ox = ox + 3.0
                    step(0.05); step(0.05)
                    assert(push_sample('cockpit', target_index, gen1, rx, 0, rz, vx, 0, vz, ryaw))
                    assert(push_sample('cockpit2', other_index, gen1, ox, 0, 1000, 30, 0, 0, math.pi / 2))
                end
                step(0.05); step(0.05); step(0.05)
                local turn_pos_err = dist3(st.poses[target_actor], {x = rx, y = 0, z = rz})
                -- Lag of ~0.15 s at 90 m/s is ~13.5 m; double movement would be > 100 m!
                assert(turn_pos_err < 25.0, 'turn tracking error too high: ' .. tostring(turn_pos_err))
                assert(math.abs(st.poses[target_actor].yaw - ryaw) < 0.15, 'turn yaw did not follow')
                local _, _, fwd_axis = basis_from_yaw(st.poses[target_actor].yaw)
                local hud_fwd_speed = st.native_vel[target_actor].x * fwd_axis.x + st.native_vel[target_actor].z * fwd_axis.z
                assert(hud_fwd_speed > 75 and hud_fwd_speed < 95, 'expected aligned HUD forward speed during turn: ' .. tostring(hud_fwd_speed))

                -- 3. 0.4 s render gap (continuous mode only) verifies no double movement or position_deviation!
                if continuous_mode then
                    local pre_gap_pos = {x = st.poses[target_actor].x, y = 0, z = st.poses[target_actor].z}
                    step(0.4)
                    local gap_move = dist3(st.poses[target_actor], pre_gap_pos)
                    assert(gap_move > 30 and gap_move < 45, 'render gap should advance ~36 m (1x): ' .. tostring(gap_move))
                    assert(st.deviations == 0, 'expected zero position_deviation across 0.4 s render gap')
                end

                -- 4. Five-second packet gap on cockpit ONLY (cockpit2 keeps streaming!).
                local loss_start = st.now
                local pos_at_loss = {x = st.poses[target_actor].x, y = 0, z = st.poses[target_actor].z}
                local pos_at_cutoff = nil
                local other_x_before_loss = st.poses[other_actor].x
                for k = 1, 120 do
                    step(0.05)
                    if k % 2 == 0 then
                        ox = ox + 3.0
                        assert(push_sample('cockpit2', other_index, gen1, ox, 0, 1000, 30, 0, 0, math.pi / 2))
                    end
                    if not pos_at_cutoff and (st.now - loss_start) >= 5.2 then
                        pos_at_cutoff = {x = st.poses[target_actor].x, y = 0, z = st.poses[target_actor].z}
                    end
                end
                local coast_dist = dist3(pos_at_cutoff, pos_at_loss)
                -- Over ~5 s at 90 m/s, coast is ~400-460 m (1x, never 2x = ~900 m!).
                assert(coast_dist > 340 and coast_dist < 490, 'unexpected 5s coast distance: ' .. tostring(coast_dist))
                local post_cutoff_drift = dist3(st.poses[target_actor], pos_at_cutoff)
                assert(post_cutoff_drift < 1e-6, 'ship drifted after 5s cutoff: ' .. tostring(post_cutoff_drift))
                assert(norm3(st.native_vel[target_actor]) == 0, 'native velocity was not zeroed at 5s cutoff')
                assert(ctrl:slot_stats('cockpit').stale_cutoffs == 1)
                assert(st.poses[other_actor].x > other_x_before_loss + 150, 'cockpit2 must remain unaffected during cockpit cutoff')
                assert(norm3(st.native_vel[other_actor]) > 25, 'cockpit2 native velocity must stay active')

                -- 5. Fresh generation reacquisition -> forward flight -> out-of-radar withdrawal!
                ctrl:end_track(views.cockpit, 1, gen1, 'cockpit')
                local w_x, w_z = st.poses[target_actor].x, st.poses[target_actor].z
                assert(push_sample('cockpit', target_index, gen2, w_x, 0, w_z, nil, nil, nil, 0))
                for k = 1, 8 do
                    w_z = w_z + 6.0
                    step(0.05); step(0.05)
                    assert(push_sample('cockpit', target_index, gen2, w_x, 0, w_z, 0, 0, 60, 0))
                end
                step(0.05)
                assert(norm3(st.native_vel[target_actor]) > 50, 'expected active native velocity before withdrawal')
                -- Withdraw ship (leaves radar AND view becomes nil)!
                st.in_radar[target_actor] = false
                st.can_observe[target_index] = false
                step(0.05)
                local pos_after_withdraw_trigger = {x = st.poses[target_actor].x, y = 0, z = st.poses[target_actor].z}
                -- Process MD stop on next frame and verify zero snap-back and zero post-withdrawal drift!
                for _ = 1, 20 do step(0.05) end
                local withdraw_drift = dist3(st.poses[target_actor], pos_after_withdraw_trigger)
                -- At most 1 frame (0.05 s * 60 m/s = 3 m) while MD stop executes on next frame, then 0.000 m forever!
                assert(withdraw_drift <= 3.1, 'excessive drift after withdrawal: ' .. tostring(withdraw_drift))
                local frozen_withdraw = {x = st.poses[target_actor].x, y = 0, z = st.poses[target_actor].z}
                for _ = 1, 20 do step(0.05) end
                assert(dist3(st.poses[target_actor], frozen_withdraw) == 0, 'ship kept drifting after withdrawal stop')
                assert(norm3(st.native_vel[target_actor]) == 0, 'native velocity remained nonzero after withdrawal')

                -- Verify own ship untouched throughout and zero snap-back on every stop!
                assert(dist3(st.poses[own_actor], {x = 0, y = 0, z = 0}) == 0)
                assert(#st.stop_snapbacks >= 2, 'expected >= 2 MD stop receipts')
                local max_snapback = 0
                for _, sb in ipairs(st.stop_snapbacks) do
                    if sb > max_snapback then max_snapback = sb end
                end
                assert(max_snapback == 0, 'unexpected snap-back on stop: ' .. tostring(max_snapback))

                ctrl:close()
                step(0.05)
                assert(norm3(st.native_vel[other_actor]) == 0, 'close must stop cockpit2 native velocity')
                return {
                    readbacks = #st.readbacks,
                    deviations = st.deviations,
                    fwd_disp = fwd_disp,
                    coast_dist = coast_dist,
                    max_snapback = max_snapback,
                    stops = #st.stop_snapbacks,
                }
            end

            return {
                continuous = run_scenario(true),
                standard = run_scenario(false),
            }
        """)
        for mode in ('continuous', 'standard'):
            res = summary[mode]
            self.assertGreater(res['readbacks'], 20)
            self.assertEqual(res['deviations'], 0)
            self.assertEqual(res['max_snapback'], 0)
            self.assertGreaterEqual(res['stops'], 2)

    def test_readback_guard_detects_artificial_double_movement_fault(self):
        detected = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            local hull = dofile(root .. 'company_hull_playback.lua')

            local now, gt = 10.0, 50.0
            local actual = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}
            local active_v = {x = 0, y = 0, z = 0}
            local deviations, faults = 0, {}
            local binding = {slot = 'cockpit', serial = 1, epoch = ('a'):rep(32),
                sector_macro = 'sector_a', index = 2, actor = '0xca', code = 'TGT-002', generation = ('1'):rep(32)}
            local ctrl = hull.new({
                enabled = true, own_index = 1, binding = binding, velocity_steps = true,
                continuous_playback = false, native_velocity = true, sample_version = 4,
                clock = function() return now end, game = function() return gt end,
                paused = function() return false end, guard = function() return true end,
                read = function() return actual end,
                write = function(_, pose, _, _, _, vel)
                    actual = {x = pose.x, y = pose.y, z = pose.z, yaw = 0, pitch = 0, roll = 0}
                    if vel then active_v = {x = vel.x, y = vel.y, z = vel.z} end
                    return true
                end,
                record = function(ev)
                    if ev.kind == 'position_deviation' then deviations = deviations + 1 end
                    return true
                end,
                fault = function(reason) faults[#faults + 1] = reason end,
            })
            for k = 1, 6 do
                now, gt = now + 0.1, gt + 0.1
                -- Inject 2x double movement into mock physics!
                actual.z = actual.z + 2.0 * active_v.z * 0.1
                ctrl:push(binding, {
                    version = 4, epoch = binding.epoch, index = 2, generation = binding.generation,
                    sector_macro = 'sector_a', sequence = k, source_time = gt, capture_time = now,
                    x = 0, y = 0, z = 1000 + k * 10, real_velocity = {x = 0, y = 0, z = 100},
                    attitude = {yaw = 0, pitch = 0, roll = 0},
                }, now)
                ctrl:tick()
            end
            return #faults >= 1 and faults[1] == 'readback_out_of_bounds'
        """)
        self.assertTrue(detected)

    def test_pause_and_end_revocation_zero_velocity_and_allow_reacquisition(self):
        summary = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local native = dofile(root .. 'company_hull_playback_x4.lua')
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local cockpit = dofile(root .. 'company_cockpit_playback.lua')

            local epoch = ('a'):rep(32)
            local gen1, gen2 = ('1'):rep(32), ('2'):rep(32)
            local own_actor, target_actor, sector_id, zone_id = 101, 202, 303, 404
            local now, gt, is_paused = 10.0, 50.0, false
            local pose = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}
            local vel = {x = 0, y = 0, z = 0}
            local stops, anchors = 0, 0
            local view = {epoch = epoch, index = 2, actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a'}
            local obs = {
                matches = function() return true end,
                is_active_pilot = function(_, idx) return idx == 2 end,
                current_radar_range = function() return 40000 end,
            }
            local vel_ctrl = velocity_mod.new({
                enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                own_index = 1, clock = function() return now end,
                register = function() return true end,
                issue = function(args)
                    if args.op == 'set' then
                        vel = {x = args.vx, y = args.vy, z = args.vz}
                    elseif args.op == 'stop' then
                        vel = {x = 0, y = 0, z = 0}
                        stops = stops + 1
                    end
                    return true
                end,
            })
            local adapter = native.new({
                enabled = true, pinned = {epoch = epoch, issued_count = 10}, own_index = 1,
                observer = obs, guard = function() return not is_paused end, velocity = vel_ctrl,
            }, {
                cockpit = function()
                    if is_paused then return nil end
                    return {actor = own_actor, sector = sector_id, zone = zone_id}
                end,
                target = function() return {actor = target_actor, sector = sector_id, zone = zone_id, sector_macro = 'sector_a', code = 'TGT-002'} end,
                read = function() return {x = pose.x, y = pose.y, z = pose.z, yaw = pose.yaw, pitch = pose.pitch, roll = pose.roll} end,
                write = function(_, _, p)
                    pose = {x = p.x, y = p.y, z = p.z, yaw = p.yaw, pitch = p.pitch, roll = p.roll}
                    anchors = anchors + 1
                    return true
                end,
                destination_zone = function() return zone_id end,
                within_radar = function() return true end,
            })
            local ctrl = cockpit.new_multi({
                enabled = true, continuous_playback = true, native_velocity = true, allow_reacquire_align = true,
                own_index = 1, slot_names = {'cockpit'}, adapters = {cockpit = adapter},
                clock = function() return now end, game = function() return gt end, paused = function() return is_paused end,
                record = function() return true end, fault = function() end,
            })
            local seq = 0
            local function push(gen, z, vz)
                seq = seq + 1
                return ctrl:sample(view, 1, {
                    version = 4, epoch = epoch, index = 2, generation = gen, sector_macro = 'sector_a',
                    sequence = seq, source_time = gt, capture_time = now, x = 0, y = 0, z = z,
                    real_velocity = vz and {x = 0, y = 0, z = vz} or nil,
                    attitude = {yaw = 0, pitch = 0, roll = 0},
                }, now, 'cockpit')
            end
            assert(push(gen1, 1000, nil))
            for k = 1, 8 do
                now, gt = now + 0.1, gt + 0.1
                pose.z = pose.z + vel.z * 0.1
                assert(push(gen1, 1000 + k * 6, 60))
                ctrl:tick(now)
            end
            assert(vel.z > 50)
            -- 1. Pause stops playback and dispatches MD stop (constantvel=false 0,0,0) cleanly.
            is_paused = true
            now = now + 0.05
            ctrl:tick(now)
            assert(vel.z == 0 and stops == 1 and ctrl:slot_stats('cockpit').closed == false)
            is_paused = false
            -- 2. Revoke gen1 and reacquire on gen2, then withdraw via END!
            ctrl:end_track(view, 1, gen1, 'cockpit')
            local base_z = pose.z
            seq = 0
            now, gt = now + 0.1, gt + 0.1
            assert(push(gen2, base_z, nil))
            for k = 1, 6 do
                now, gt = now + 0.1, gt + 0.1
                pose.z = pose.z + vel.z * 0.1
                assert(push(gen2, base_z + k * 6, 60))
                ctrl:tick(now)
            end
            assert(vel.z > 50 and pose.z > base_z + 15)
            local anchors_before_end = anchors
            ctrl:end_track(view, 1, gen2, 'cockpit')
            assert(vel.z == 0 and stops == 2 and anchors == anchors_before_end + 1)
            local closed_before_close = ctrl:slot_stats('cockpit').closed
            assert(closed_before_close == false)
            ctrl:close()
            return {stops = stops, closed = closed_before_close}
        """)
        self.assertEqual(summary['stops'], 2)
        self.assertFalse(summary['closed'])


if __name__ == '__main__':
    unittest.main()
