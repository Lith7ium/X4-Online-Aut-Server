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
            '$lease ge 1 and $lease == ($lease)i',
            'State.$epoch == $args.$epoch',
            'not md.X4AuthorityShipRegistry.State.$faulted',
            '$ship != player.controlled',
            'not $ship.pilot.exists',
            'not $ship.assignedpilot.exists',
            "$ship.sector.macro.id == $args.$sector",
            "'%s'.[$ship.parent] == @$args.$zone",
            'not @$active_ship.exists or ($active_ship == $ship and $active_lease == $lease)',
            '$basisError lt 0.00000001',
            '$active_ship == $ship and $active_lease == $lease',
            '$lease gt $stopped_lease',
            'State.$cockpit',
            'State.$cockpit_lease',
            'State.$cockpit_stopped',
            'State.$cockpit2',
            'State.$cockpit2_lease',
            'State.$cockpit2_stopped',
            'State.$cockpit3',
            'State.$cockpit3_lease',
            'State.$cockpit3_stopped',
            '$args.$vx ge -2800 and $args.$vx le 2800',
            "'%s|%s|%s'.[$args.$request,$status,$lease]",
        ):
            self.assertIn(required, guards)
        # Stop branch must NOT require currentradarrange so out-of-radar stops remain possible when anchored.
        stop_branch = next(
            node for node in root.iter('do_if')
            if node.get('value') == "$op == 'stop' and $active_ship == $ship and $active_lease == $lease"
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

    def test_forward_turn_5s_cutoff_and_explicit_revoke_without_double_movement_or_snapback(self):
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
            local zone_hex = ('0x%x'):format(zone_id)

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
                    md_lease = {},
                    md_stopped = {},
                    md_queue = {},
                    md_handler = nil,
                    readbacks = {},
                    deviations = 0,
                    vel_events = {},
                    stop_anchor_drifts = {},
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
                            args._queued_at = st.now
                            st.md_queue[#st.md_queue + 1] = args
                            return true
                        end,
                        on_event = function(row)
                            st.vel_events[#st.vel_events + 1] = row
                            if row.op == 'stop' and row.ok and row.confirmed_zero then
                                st.stop_anchor_drifts[#st.stop_anchor_drifts + 1] = row.anchor_drift_m
                            end
                        end,
                    })
                end

                local vels = {cockpit = make_vel('cockpit'), cockpit2 = make_vel('cockpit2')}
                local shared_claims = {by_actor = {}, by_slot = {}}
                local adapters = {}
                for _, slot in ipairs({'cockpit', 'cockpit2'}) do
                    adapters[slot] = native.new({
                        enabled = true,
                        pinned = {epoch = epoch, issued_count = 10},
                        own_index = own_index,
                        observer = make_observer(slot),
                        guard = function() return not st.paused end,
                        velocity = vels[slot],
                        exclusive_claims = shared_claims,
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
                        local stopped_lease = st.md_stopped[args.slot] or 0
                        local active_ship = st.md_active[args.slot]
                        local active_lease = st.md_lease[args.slot] or 0
                        if args.op == 'set' and aid and st.in_radar[aid]
                                and args.zone == zone_hex and args.sector == 'sector_a'
                                and args.lease > stopped_lease
                                and (active_ship == nil or (active_ship == aid and active_lease == args.lease)) then
                            st.native_vel[aid] = {x = args.vx, y = args.vy, z = args.vz}
                            st.md_active[args.slot] = aid
                            st.md_lease[args.slot] = args.lease
                            status = 'set'
                        elseif args.op == 'stop' and aid and active_ship == aid and active_lease == args.lease
                                and args.zone == zone_hex and args.sector == 'sector_a' then
                            local p = st.poses[aid]
                            before_pos = {x = p.x, y = p.y, z = p.z}
                            before_vel = {x = st.native_vel[aid].x, y = st.native_vel[aid].y, z = st.native_vel[aid].z}
                            -- Model real X4 9.00 constantvel=false stop behavior: returns to last SetObjectSectorPos.
                            -- When anchor_live_pose() ran at stop dispatch, this lands within 0 m of the stop anchor!
                            local anchor = st.last_set_pos[aid]
                            st.poses[aid].x, st.poses[aid].y, st.poses[aid].z = anchor.x, anchor.y, anchor.z
                            st.native_vel[aid] = {x = 0, y = 0, z = 0}
                            st.md_active[args.slot] = nil
                            st.md_stopped[args.slot] = args.lease
                            status = 'stopped'
                        end
                        local reply = ('%s|%s|%s'):format(tostring(args.request), status, tostring(args.lease))
                        if status ~= 'refused' then
                            local p = st.poses[aid]
                            local v = st.native_vel[aid]
                            local right, up, fwd = basis_from_yaw(p.yaw)
                            local lv = {
                                x = v.x * right.x + v.y * right.y + v.z * right.z,
                                y = v.x * up.x + v.y * up.y + v.z * up.z,
                                z = v.x * fwd.x + v.y * fwd.y + v.z * fwd.z,
                            }
                            reply = reply .. ('|%s|sector_a|%s|%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f'):format(
                                args.actor, zone_hex, st.gt, p.x, p.y, p.z, lv.x, lv.y, lv.z,
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
                assert(fwd_disp > 100 and fwd_disp < 130, 'unexpected forward displacement: ' .. tostring(fwd_disp))
                assert(math.abs(st.native_vel[target_actor].z - 60) < 1.0, 'expected ~60 m/s native velocity')

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
                assert(turn_pos_err < 25.0, 'turn tracking error too high: ' .. tostring(turn_pos_err))
                assert(math.abs(st.poses[target_actor].yaw - ryaw) < 0.15, 'turn yaw did not follow')

                -- 3. 0.4 s render gap (continuous mode only) verifies no double movement or position_deviation.
                if continuous_mode then
                    local pre_gap_pos = {x = st.poses[target_actor].x, y = 0, z = st.poses[target_actor].z}
                    step(0.4)
                    local gap_move = dist3(st.poses[target_actor], pre_gap_pos)
                    assert(gap_move > 30 and gap_move < 45, 'render gap should advance ~36 m (1x): ' .. tostring(gap_move))
                    assert(st.deviations == 0, 'expected zero position_deviation across 0.4 s render gap')
                end

                -- 4. Five-second silent packet gap on cockpit ONLY (cockpit2 keeps streaming).
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
                assert(coast_dist > 340 and coast_dist < 490, 'unexpected 5s coast distance: ' .. tostring(coast_dist))
                local post_cutoff_drift = dist3(st.poses[target_actor], pos_at_cutoff)
                assert(post_cutoff_drift < 1e-6, 'ship drifted after 5s cutoff: ' .. tostring(post_cutoff_drift))
                assert(norm3(st.native_vel[target_actor]) == 0, 'native velocity was not zeroed at 5s cutoff')
                assert(ctrl:slot_stats('cockpit').stale_cutoffs == 1)
                assert(ctrl:slot_stats('cockpit').closed == false)
                assert(ctrl:slot_stats('cockpit').velocity_cleanup_uncertain == false)
                assert(vels.cockpit:stats().confirmed_stops == 1)
                assert(st.poses[other_actor].x > other_x_before_loss + 150)
                assert(norm3(st.native_vel[other_actor]) > 25)

                -- 5. Explicit END revoke of gen1, reacquire on gen2, forward flight, then explicit END revoke of gen2!
                ctrl:end_track(views.cockpit, 1, gen1, 'cockpit')
                local revoked_ok, revoked_reason = push_sample('cockpit', target_index, gen1,
                    st.poses[target_actor].x, 0, st.poses[target_actor].z + 5, 0, 0, 60, 0)
                assert(revoked_ok == false and revoked_reason == 'revoked_generation')

                local w_x, w_z = st.poses[target_actor].x, st.poses[target_actor].z
                assert(push_sample('cockpit', target_index, gen2, w_x, 0, w_z, nil, nil, nil, 0))
                for k = 1, 8 do
                    w_z = w_z + 6.0
                    step(0.05); step(0.05)
                    assert(push_sample('cockpit', target_index, gen2, w_x, 0, w_z, 0, 0, 60, 0))
                end
                step(0.05)
                assert(norm3(st.native_vel[target_actor]) > 50)
                ctrl:end_track(views.cockpit, 1, gen2, 'cockpit')
                local pos_after_end_trigger = {x = st.poses[target_actor].x, y = 0, z = st.poses[target_actor].z}
                for _ = 1, 20 do step(0.05) end
                local end_drift = dist3(st.poses[target_actor], pos_after_end_trigger)
                assert(end_drift <= 3.1, 'excessive drift after END revoke: ' .. tostring(end_drift))
                local frozen_end = {x = st.poses[target_actor].x, y = 0, z = st.poses[target_actor].z}
                for _ = 1, 20 do step(0.05) end
                assert(dist3(st.poses[target_actor], frozen_end) == 0)
                assert(norm3(st.native_vel[target_actor]) == 0)
                assert(ctrl:slot_stats('cockpit').closed == false)
                assert(ctrl:slot_stats('cockpit').velocity_cleanup_uncertain == false)
                assert(vels.cockpit:stats().confirmed_stops == 2)

                -- Own ship untouched throughout and zero anchor drift (no snap-back) on every confirmed stop.
                assert(dist3(st.poses[own_actor], {x = 0, y = 0, z = 0}) == 0)
                local max_anchor_drift = 0
                for _, ad in ipairs(st.stop_anchor_drifts) do
                    if ad > max_anchor_drift then max_anchor_drift = ad end
                end
                assert(max_anchor_drift < 1e-6, 'unexpected anchor drift on stop: ' .. tostring(max_anchor_drift))

                ctrl:close()
                step(0.05)
                assert(norm3(st.native_vel[other_actor]) == 0)
                assert(vels.cockpit2:stats().confirmed_stops == 1)
                return {
                    readbacks = #st.readbacks,
                    deviations = st.deviations,
                    fwd_disp = fwd_disp,
                    coast_dist = coast_dist,
                    max_anchor_drift = max_anchor_drift,
                    stops = #st.stop_anchor_drifts,
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
            self.assertLess(res['max_anchor_drift'], 1e-6)
            self.assertGreaterEqual(res['stops'], 2)

    def test_readback_guard_detects_artificial_double_movement_fault(self):
        detected = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            local hull = dofile(root .. 'company_hull_playback.lua')

            local now, gt = 10.0, 50.0
            local actual = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}
            local active_v = {x = 0, y = 0, z = 0, game_time = 50.0, request = 1}
            local req = 1
            local faults = {}
            local binding = {slot = 'cockpit', serial = 1, epoch = ('a'):rep(32),
                sector_macro = 'sector_a', index = 2, actor = '0xca', code = 'TGT-002', generation = ('1'):rep(32)}
            local ctrl = hull.new({
                enabled = true, own_index = 1, binding = binding, velocity_steps = true,
                continuous_playback = false, native_velocity = true, sample_version = 4,
                acknowledged_velocity = function() return active_v end,
                clock = function() return now end, game = function() return gt end,
                paused = function() return false end, guard = function() return true end,
                read = function() return actual end,
                write = function(_, pose, _, _, _, vel)
                    actual = {x = pose.x, y = pose.y, z = pose.z, yaw = 0, pitch = 0, roll = 0}
                    if vel and vel.z ~= active_v.z then
                        req = req + 1
                        active_v = {x = vel.x, y = vel.y, z = vel.z, game_time = gt, request = req}
                    end
                    return true
                end,
                record = function() return true end,
                fault = function(reason) faults[#faults + 1] = reason end,
            })
            for k = 1, 6 do
                now, gt = now + 0.1, gt + 0.1
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

    def test_anchor_live_pose_guards_and_ownership_loss_refuse_raw_unanchored_stop(self):
        results = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local native = dofile(root .. 'company_hull_playback_x4.lua')
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local cockpit = dofile(root .. 'company_cockpit_playback.lua')

            local epoch = ('a'):rep(32)
            local gen1 = ('1'):rep(32)
            local own_actor, target_actor, sector_id, zone_id = 101, 202, 303, 404
            local zone_hex = ('0x%x'):format(zone_id)

            local function run_fault_case(fault_mode)
                local now, gt = 10.0, 50.0
                local is_paused = false
                local guard_ok = true
                local obs_matches = true
                local cur_cockpit_actor = own_actor
                local cur_target_code = 'TGT-002'
                local cur_target_zone = zone_id
                local pose = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}
                local native_vel = {x = 0, y = 0, z = 0}
                local write_count, md_stop_count = 0, 0
                local events, faults = {}, {}
                local md_handler = nil
                local claims = {by_actor = {}, by_slot = {}}
                local view = {epoch = epoch, index = 2, actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a'}

                local obs = {
                    matches = function(_, ep, frame, idx, act, code)
                        return obs_matches and ep == epoch and frame == 'sector_a'
                            and idx == 2 and act == '0xca' and code == 'TGT-002'
                    end,
                    is_active_pilot = function(_, idx) return idx == 2 end,
                    current_radar_range = function() return 40000 end,
                }
                local vel_ctrl = velocity_mod.new({
                    enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                    own_index = 1, clock = function() return now end,
                    register = function(cb) md_handler = cb; return true end,
                    issue = function(args)
                        if args.op == 'set' then
                            native_vel = {x = args.vx, y = args.vy, z = args.vz}
                            local reply = ('%d|set|%d|%s|sector_a|%s|%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f'):format(
                                args.request, args.lease, args.actor, zone_hex, gt,
                                pose.x, pose.y, pose.z, args.vx, args.vy, args.vz, args.vx, args.vy, args.vz)
                            md_handler(nil, reply)
                            return true
                        elseif args.op == 'stop' then
                            md_stop_count = md_stop_count + 1
                            return true
                        end
                        return false
                    end,
                })
                local adapter = native.new({
                    enabled = true, pinned = {epoch = epoch, issued_count = 10}, own_index = 1,
                    observer = obs, guard = function() return guard_ok and not is_paused end,
                    velocity = vel_ctrl, exclusive_claims = claims,
                }, {
                    cockpit = function()
                        if is_paused then return nil end
                        return {actor = cur_cockpit_actor, sector = sector_id, zone = zone_id}
                    end,
                    target = function()
                        return {actor = target_actor, sector = sector_id, zone = cur_target_zone,
                                sector_macro = 'sector_a', code = cur_target_code}
                    end,
                    read = function() return {x = pose.x, y = pose.y, z = pose.z, yaw = 0, pitch = 0, roll = 0} end,
                    write = function(_, _, p)
                        write_count = write_count + 1
                        pose = {x = p.x, y = p.y, z = p.z, yaw = p.yaw, pitch = p.pitch, roll = p.roll}
                        return true
                    end,
                    destination_zone = function() return zone_id end,
                    within_radar = function() return true end,
                })
                local adapter2 = native.new({
                    enabled = true, pinned = {epoch = epoch, issued_count = 10}, own_index = 1,
                    observer = obs, guard = function() return true end, exclusive_claims = claims,
                }, {
                    cockpit = function() return {actor = own_actor, sector = sector_id, zone = zone_id} end,
                    target = function() return {actor = target_actor, sector = sector_id, zone = zone_id, sector_macro = 'sector_a', code = 'TGT-002'} end,
                    read = function() return pose end,
                    write = function() return true end,
                    destination_zone = function() return zone_id end,
                    within_radar = function() return true end,
                })

                local ctrl = cockpit.new_multi({
                    enabled = true, continuous_playback = true, native_velocity = true, allow_reacquire_align = true,
                    own_index = 1, slot_names = {'cockpit'}, adapters = {cockpit = adapter},
                    clock = function() return now end, game = function() return gt end, paused = function() return is_paused end,
                    record = function() return true end,
                    fault = function(reason, unc) faults[#faults + 1] = {reason = reason, uncertain = unc} end,
                    on_event = function(kind, detail) events[#events + 1] = {kind = kind, detail = detail} end,
                })

                local seq = 0
                local function push(z, vz)
                    seq = seq + 1
                    return ctrl:sample(view, 1, {
                        version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                        sequence = seq, source_time = gt, capture_time = now, x = 0, y = 0, z = z,
                        real_velocity = vz and {x = 0, y = 0, z = vz} or nil,
                        attitude = {yaw = 0, pitch = 0, roll = 0},
                    }, now, 'cockpit')
                end

                assert(push(1000, nil))
                for k = 1, 6 do
                    now, gt = now + 0.1, gt + 0.1
                    pose.z = pose.z + native_vel.z * 0.1
                    assert(push(1000 + k * 6, 60))
                    ctrl:tick(now)
                end
                assert(native_vel.z == 60)

                -- While cockpit holds exclusive claim on 0xca, cockpit2 CANNOT bind 0xca!
                local dup_bind = adapter2:bind({
                    slot = 'cockpit2', serial = 1, epoch = epoch, index = 2,
                    actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a', generation = gen1,
                })
                assert(dup_bind == nil, 'second slot must not acquire exclusive binding while active')

                local writes_before = write_count
                if fault_mode == 'view_loss' then
                    obs_matches = false
                    ctrl:view(nil, 1, 'cockpit')
                elseif fault_mode == 'cockpit_changed' then
                    cur_cockpit_actor = 999
                    ctrl:end_track(view, 1, gen1, 'cockpit')
                elseif fault_mode == 'code_changed' then
                    cur_target_code = 'BAD-999'
                    ctrl:end_track(view, 1, gen1, 'cockpit')
                elseif fault_mode == 'zone_changed' then
                    cur_target_zone = 777
                    ctrl:end_track(view, 1, gen1, 'cockpit')
                elseif fault_mode == 'exclusive_lost' then
                    claims.by_actor['0xca'] = {slot = 'cockpit2', actor = '0xca'}
                    ctrl:end_track(view, 1, gen1, 'cockpit')
                elseif fault_mode == 'paused' then
                    is_paused = true
                    now = now + 0.05
                    ctrl:tick(now)
                end

                -- Model remains untouched (keeps coasting without broadened write authority),
                -- and reacquisition/samples on this closed uncertain slot are rejected!
                local sample_after_ok, sample_after_reason = push(1200, 60)
                local st_c = ctrl:slot_stats('cockpit')
                local saw_uncertain_event = false
                for _, ev in ipairs(events) do
                    if ev.kind == 'closed_uncertain' and ev.detail.reason == 'velocity_cleanup_uncertain' then
                        saw_uncertain_event = true
                    end
                end
                return {
                    writes_after = write_count - writes_before,
                    md_stops = md_stop_count,
                    closed = st_c.closed,
                    uncertain = st_c.velocity_cleanup_uncertain,
                    reason = st_c.velocity_uncertain_reason,
                    saw_uncertain_event = saw_uncertain_event,
                    vel_uncertain = vel_ctrl:cleanup_uncertain(),
                    native_vel_after = native_vel.z,
                    sample_after_ok = sample_after_ok,
                    sample_after_reason = sample_after_reason,
                }
            end

            local now_m = 10.0
            local raw_stops = 0
            local v_missing = velocity_mod.new({
                enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                own_index = 1, clock = function() return now_m end,
                register = function() return true end,
                issue = function(args)
                    if args.op == 'stop' then raw_stops = raw_stops + 1 end
                    return true
                end,
            })
            assert(v_missing:apply({
                epoch = epoch, index = 2, actor = '0xca', sector_macro = 'sector_a', code = 'TGT-002', generation = gen1,
            }, {x = 0, y = 0, z = 60}, nil, zone_hex))
            local ok_s, s_reason = v_missing:stop(nil)

            return {
                view_loss = run_fault_case('view_loss'),
                cockpit_changed = run_fault_case('cockpit_changed'),
                code_changed = run_fault_case('code_changed'),
                zone_changed = run_fault_case('zone_changed'),
                exclusive_lost = run_fault_case('exclusive_lost'),
                paused = run_fault_case('paused'),
                missing_anchor = {
                    ok = ok_s, reason = s_reason, raw_stops = raw_stops,
                    uncertain = v_missing:cleanup_uncertain(),
                },
            }
        """)
        for case_name, expected_reason in (
            ('view_loss', 'anchor_observer_mismatch'),
            ('cockpit_changed', 'anchor_cockpit_changed'),
            ('code_changed', 'anchor_target_identity_changed'),
            ('zone_changed', 'anchor_target_zone_changed'),
            ('exclusive_lost', 'anchor_exclusive_binding_lost'),
            ('paused', 'anchor_guard_failed'),
        ):
            r = results[case_name]
            self.assertEqual(r['writes_after'], 0, f'{case_name} must not write SetObjectSectorPos')
            self.assertEqual(r['md_stops'], 0, f'{case_name} must not dispatch raw unanchored MD stop')
            self.assertTrue(r['closed'], f'{case_name} must seal slot closed')
            self.assertTrue(r['uncertain'], f'{case_name} must expose velocity_cleanup_uncertain')
            self.assertTrue(r['vel_uncertain'], f'{case_name} velocity adapter must retain uncertain cleanup')
            self.assertTrue(r['saw_uncertain_event'], f'{case_name} must emit closed_uncertain event')
            self.assertEqual(r['reason'], expected_reason)
            self.assertEqual(r['native_vel_after'], 60, f'{case_name} model continues coasting without unanchored write')
            self.assertFalse(r['sample_after_ok'], f'{case_name} must keep identity checks intact and reject reuse')
            self.assertEqual(r['sample_after_reason'], 'velocity_cleanup_uncertain')

        ma = results['missing_anchor']
        self.assertFalse(ma['ok'])
        self.assertEqual(ma['reason'], 'anchor_missing')
        self.assertEqual(ma['raw_stops'], 0)
        self.assertTrue(ma['uncertain'])

    def test_delayed_receipt_fencing_stop_verification_and_pending_bounds(self):
        summary = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local epoch = ('a'):rep(32)
            local gen1, gen2 = ('1'):rep(32), ('2'):rep(32)
            local zone_a, zone_b = '0x194', '0x195'
            local bind1 = {epoch = epoch, index = 2, actor = '0xca', sector_macro = 'sector_a', code = 'TGT-002', generation = gen1}
            local bind2 = {epoch = epoch, index = 2, actor = '0xca', sector_macro = 'sector_a', code = 'TGT-002', generation = gen2}

            local function anchor_ok(x, y, z, zone)
                return function()
                    return {x = x, y = y, z = z, yaw = 0, pitch = 0, roll = 0,
                            zone = zone or zone_a, actor = '0xca', sector_macro = 'sector_a'}
                end
            end

            local function set_reply(req, lease, actor, sector, zone, vx, vy, vz, gt)
                return ('%d|set|%d|%s|%s|%s|%.6f|0,0,1000|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f'):format(
                    req, lease, actor, sector, zone, gt or 10.0, vx, vy, vz, vx, vy, vz)
            end

            local function stop_reply(req, lease, actor, sector, zone, x, y, z, vx, vy, vz)
                return ('%d|stopped|%d|%s|%s|%s|10.1|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f|0,0,60'):format(
                    req, lease, actor, sector, zone, x, y, z, vx, vy, vz, x, y, z)
            end

            -- 1. Pending STOP serializes new dispatches, and delayed set/stop receipts are fenced!
            local now = 10.0
            local issued, handler = {}, nil
            local v = velocity_mod.new({
                enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                own_index = 1, clock = function() return now end,
                register = function(cb) handler = cb; return true end,
                issue = function(args) issued[#issued + 1] = args; return true end,
            })
            assert(v:apply(bind1, {x = 0, y = 0, z = 60}, anchor_ok(0, 0, 1000), zone_a))
            local req1, lease1 = issued[1].request, issued[1].lease
            now = now + 0.1
            assert(v:stop(anchor_ok(0, 0, 1006)))
            local req2 = issued[2].request
            assert(v:has_pending_stop() == true)
            now = now + 0.1
            -- While lease1 STOP is unacknowledged, a new apply on this slot MUST refuse with 'stop_pending'.
            local ok_during_stop, why_during_stop = v:apply(bind2, {x = 0, y = 0, z = 40}, anchor_ok(0, 0, 1006), zone_a)
            assert(ok_during_stop == false and why_during_stop == 'stop_pending')
            assert(#issued == 2)

            -- Once lease1 STOP is confirmed cleanly, lease2 can dispatch cleanly.
            handler(nil, stop_reply(req2, lease1, '0xca', 'sector_a', zone_a, 0, 0, 1006, 0, 0, 0))
            assert(v:has_pending_stop() == false)
            assert(v:cleanup_uncertain() == false)
            assert(v:stats().confirmed_stops == 1)

            assert(v:apply(bind2, {x = 0, y = 0, z = 40}, anchor_ok(0, 0, 1006), zone_a))
            local req3, lease2 = issued[3].request, issued[3].lease
            assert(lease2 > lease1)
            handler(nil, set_reply(req3, lease2, '0xca', 'sector_a', zone_a, 0, 0, 40, 10.2))
            local ack_gen2 = v:acknowledged_velocity(bind2, zone_a)
            assert(ack_gen2 and ack_gen2.z == 40 and ack_gen2.game_time == 10.2 and ack_gen2.request == req3)

            -- Delayed set receipt from lease1 arriving after lease2 is active MUST be fenced!
            handler(nil, set_reply(req1, lease1, '0xca', 'sector_a', zone_a, 0, 0, 60, 10.0))
            local ack_after_late_set = v:acknowledged_velocity(bind2, zone_a)
            assert(ack_after_late_set and ack_after_late_set.z == 40)
            assert(v:cleanup_uncertain() == false)

            now = now + 0.1
            assert(v:stop(anchor_ok(0, 0, 1010)))
            local req4 = issued[4].request
            handler(nil, stop_reply(req4, lease2, '0xca', 'sector_a', zone_a, 0, 0, 1010, 0, 0, 0))
            assert(v:cleanup_uncertain() == false)
            assert(v:stats().confirmed_stops == 2)
            assert(v:stats().fenced_receipts == 1)

            -- 2. Stop receipt validation failures MUST refuse to confirm and retain explicit uncertainty.
            local function test_bad_stop(mode)
                local t = 20.0
                local q, h = {}, nil
                local vc = velocity_mod.new({
                    enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                    own_index = 1, clock = function() return t end,
                    receipt_timeout = 1.0,
                    register = function(cb) h = cb; return true end,
                    issue = function(args) q[#q + 1] = args; return true end,
                })
                assert(vc:apply(bind1, {x = 0, y = 0, z = 60}, anchor_ok(0, 0, 1000), zone_a))
                h(nil, set_reply(q[1].request, q[1].lease, '0xca', 'sector_a', zone_a, 0, 0, 60))
                t = t + 0.1
                assert(vc:stop(anchor_ok(0, 0, 1006)))
                local sr, sl = q[2].request, q[2].lease
                if mode == 'wrong_actor' then
                    h(nil, stop_reply(sr, sl, '0xdead', 'sector_a', zone_a, 0, 0, 1006, 0, 0, 0))
                elseif mode == 'wrong_zone' then
                    h(nil, stop_reply(sr, sl, '0xca', 'sector_a', zone_b, 0, 0, 1006, 0, 0, 0))
                elseif mode == 'nonzero_speed' then
                    h(nil, stop_reply(sr, sl, '0xca', 'sector_a', zone_a, 0, 0, 1006, 0, 0, 1.5))
                elseif mode == 'refused' then
                    h(nil, ('%d|refused|%d'):format(sr, sl))
                elseif mode == 'timeout' then
                    t = t + 1.5
                    vc:tick()
                elseif mode == 'late_set_after_stop' then
                    local q2, h2 = {}, nil
                    local t2 = 30.0
                    local vc2 = velocity_mod.new({
                        enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                        own_index = 1, clock = function() return t2 end,
                        register = function(cb) h2 = cb; return true end,
                        issue = function(args) q2[#q2 + 1] = args; return true end,
                    })
                    assert(vc2:apply(bind1, {x = 0, y = 0, z = 60}, anchor_ok(0, 0, 1000), zone_a))
                    t2 = t2 + 0.1
                    assert(vc2:stop(anchor_ok(0, 0, 1006)))
                    h2(nil, stop_reply(q2[2].request, q2[2].lease, '0xca', 'sector_a', zone_a, 0, 0, 1006, 0, 0, 0))
                    assert(vc2:cleanup_uncertain() == false)
                    h2(nil, set_reply(q2[1].request, q2[1].lease, '0xca', 'sector_a', zone_a, 0, 0, 60))
                    local u2, r2 = vc2:cleanup_uncertain()
                    return {uncertain = u2, reason = r2, confirmed = vc2:stats().confirmed_stops}
                end
                local u, r = vc:cleanup_uncertain()
                return {uncertain = u, reason = r, confirmed = vc:stats().confirmed_stops}
            end

            -- 3. Bounded pending receipts: never exceeds max_pending and prunes on timeout.
            local tb = 40.0
            local qb = {}
            local vb = velocity_mod.new({
                enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                own_index = 1, clock = function() return tb end,
                max_pending = 8, receipt_timeout = 1.0,
                register = function() return true end,
                issue = function(args) qb[#qb + 1] = args; return true end,
            })
            local peak_pending = 0
            for k = 1, 25 do
                tb = tb + 0.06
                vb:apply(bind1, {x = 0, y = 0, z = 10 + k * 5}, anchor_ok(0, 0, 1000), zone_a)
                local pcount = vb:stats().pending_count
                if pcount > peak_pending then peak_pending = pcount end
            end
            tb = tb + 2.0
            vb:tick()
            local after_prune = vb:stats().pending_count

            return {
                fenced_ok = true,
                wrong_actor = test_bad_stop('wrong_actor'),
                wrong_zone = test_bad_stop('wrong_zone'),
                nonzero_speed = test_bad_stop('nonzero_speed'),
                refused = test_bad_stop('refused'),
                timeout = test_bad_stop('timeout'),
                late_set_after_stop = test_bad_stop('late_set_after_stop'),
                peak_pending = peak_pending,
                after_prune = after_prune,
            }
        """)
        self.assertTrue(summary['fenced_ok'])
        for mode, expected_reason in (
            ('wrong_actor', 'stop_target_mismatch'),
            ('wrong_zone', 'stop_zone_mismatch'),
            ('nonzero_speed', 'stop_speed_nonzero'),
            ('refused', 'stop_refused'),
            ('timeout', 'stop_receipt_timeout'),
            ('late_set_after_stop', 'late_set_after_stop'),
        ):
            r = summary[mode]
            self.assertTrue(r['uncertain'], f'{mode} must retain explicit uncertain outcome')
            self.assertEqual(r['reason'], expected_reason)
            if mode != 'late_set_after_stop':
                self.assertEqual(r['confirmed'], 0, f'{mode} must not acknowledge stop')
        self.assertLessEqual(summary['peak_pending'], 8)
        self.assertEqual(summary['after_prune'], 0)

    def test_possible_motion_preserved_across_refused_missing_and_delayed_zero_replies(self):
        summary = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local epoch = ('a'):rep(32)
            local gen1 = ('1'):rep(32)
            local zone_a = '0x194'
            local bind1 = {epoch = epoch, index = 2, actor = '0xca', sector_macro = 'sector_a', code = 'TGT-002', generation = gen1}

            local function anchor_ok()
                return {x = 0, y = 0, z = 1200, yaw = 0, pitch = 0, roll = 0,
                        zone = zone_a, actor = '0xca', sector_macro = 'sector_a'}
            end

            local function set_reply(req, lease, vx, vy, vz, gt)
                return ('%d|set|%d|0xca|sector_a|%s|%.6f|0,0,1200|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f'):format(
                    req, lease, zone_a, gt, vx, vy, vz, vx, vy, vz)
            end

            local function stop_reply(req, lease, gt)
                return ('%d|stopped|%d|0xca|sector_a|%s|%.6f|0,0,1200|0,0,0|1,0,0|0,1,0|0,0,1|0,0,1200|0,0,100'):format(
                    req, lease, zone_a, gt)
            end

            local function run_case(mode)
                local now = 10.0
                local issued, handler = {}, nil
                local engine_vel = 0
                local anchor_calls = 0
                local vc = velocity_mod.new({
                    enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                    own_index = 1, clock = function() return now end,
                    receipt_timeout = 1.0,
                    register = function(cb) handler = cb; return true end,
                    issue = function(args)
                        issued[#issued + 1] = args
                        if args.op == 'set' and args.vz == 100 then
                            -- Physical engine actually applied 100 m/s, even though its receipt will be lost!
                            engine_vel = 100
                        elseif args.op == 'set' and args.vz == 80 then
                            engine_vel = 80
                        elseif args.op == 'stop' then
                            engine_vel = 0
                        end
                        return true
                    end,
                })
                local function anchored()
                    anchor_calls = anchor_calls + 1
                    return anchor_ok()
                end

                -- 1. Dispatch 100 m/s; engine applies it, receipt is LOST (never delivered).
                assert(vc:apply(bind1, {x = 0, y = 0, z = 100}, anchored, zone_a))
                local req_100, lease1 = issued[1].request, issued[1].lease

                -- 2. Dispatch ordinary 0 m/s set (must NOT clear possible_motion before MD confirms!).
                now = now + 0.1
                assert(vc:apply(bind1, {x = 0, y = 0, z = 0}, anchored, zone_a))
                local req_0 = issued[2].request
                assert(vc:stats().possible_motion == true)

                if mode == 'refused_zero' then
                    -- Zero set is explicitly refused by MD.
                    handler(nil, ('%d|refused|%d|op'):format(req_0, lease1))
                    -- First 100 m/s receipt expires; pending_count drops to 0 and acked_velocity is nil!
                    now = now + 1.5
                    vc:tick()
                    assert(vc:stats().pending_count == 0 and vc:stats().acked_velocity == nil)
                    assert(vc:stats().possible_motion == true)
                    local ok_s, res_s = vc:stop(anchored, 'test_refused_zero')
                    assert(ok_s and res_s == 'stop_dispatched:test_refused_zero')
                    local stop_req = issued[#issued].request
                    handler(nil, stop_reply(stop_req, lease1, now))
                    return {
                        stop_res = res_s, engine_vel = engine_vel, anchor_calls = anchor_calls,
                        stops = vc:stats().stops, confirmed_stops = vc:stats().confirmed_stops,
                        uncertain = vc:cleanup_uncertain(),
                    }
                elseif mode == 'missing_zero' then
                    -- Neither 100 m/s nor 0 m/s receipt ever arrives; both expire!
                    now = now + 1.5
                    vc:tick()
                    assert(vc:stats().pending_count == 0 and vc:stats().acked_velocity == nil)
                    assert(vc:stats().possible_motion == true)
                    local ok_s, res_s = vc:stop(anchored, 'test_missing_zero')
                    assert(ok_s and res_s == 'stop_dispatched:test_missing_zero')
                    local stop_req = issued[#issued].request
                    handler(nil, stop_reply(stop_req, lease1, now))
                    return {
                        stop_res = res_s, engine_vel = engine_vel, anchor_calls = anchor_calls,
                        stops = vc:stats().stops, confirmed_stops = vc:stats().confirmed_stops,
                        uncertain = vc:cleanup_uncertain(),
                    }
                elseif mode == 'delayed_zero_stop_first' then
                    -- First 100 m/s receipt expires while 0 m/s receipt is still delayed; stop() runs first!
                    now = now + 0.95
                    vc:tick()
                    local ok_s, res_s = vc:stop(anchored, 'test_delayed_stop_first')
                    assert(ok_s and res_s == 'stop_dispatched:test_delayed_stop_first')
                    local stop_req = issued[#issued].request
                    handler(nil, stop_reply(stop_req, lease1, now))
                    -- Delayed zero reply arrives AFTER confirmed stop: must be fenced cleanly, NOT uncertain!
                    handler(nil, set_reply(req_0, lease1, 0, 0, 0, now))
                    return {
                        stop_res = res_s, engine_vel = engine_vel, anchor_calls = anchor_calls,
                        stops = vc:stats().stops, confirmed_stops = vc:stats().confirmed_stops,
                        fenced = vc:stats().fenced_receipts, uncertain = vc:cleanup_uncertain(),
                    }
                elseif mode == 'delayed_zero_confirmed_before_stop' then
                    -- First 100 m/s receipt expires; then delayed matching near-zero receipt arrives!
                    now = now + 0.95
                    vc:tick()
                    engine_vel = 0
                    handler(nil, set_reply(req_0, lease1, 0, 0, 0, now))
                    assert(vc:stats().possible_motion == false)
                    local ok_s, res_s = vc:stop(anchored, 'test_confirmed_zero')
                    return {
                        stop_res = res_s, engine_vel = engine_vel, anchor_calls = anchor_calls,
                        stops = vc:stats().stops, confirmed_stops = vc:stats().confirmed_stops,
                        uncertain = vc:cleanup_uncertain(),
                    }
                elseif mode == 'delayed_zero_superseded_by_newer_nonzero' then
                    -- Newer 80 m/s set is dispatched BEFORE delayed 0 m/s receipt arrives!
                    now = now + 0.1
                    assert(vc:apply(bind1, {x = 0, y = 0, z = 80}, anchored, zone_a))
                    -- Older delayed 0 m/s receipt arrives now: request < last_nonzero_request -> MUST NOT clear possible_motion!
                    handler(nil, set_reply(req_0, lease1, 0, 0, 0, now))
                    assert(vc:stats().possible_motion == true)
                    now = now + 1.5
                    vc:tick()
                    assert(vc:stats().possible_motion == true)
                    local ok_s, res_s = vc:stop(anchored, 'test_superseded_zero')
                    assert(ok_s and res_s == 'stop_dispatched:test_superseded_zero')
                    handler(nil, stop_reply(issued[#issued].request, lease1, now))
                    return {
                        stop_res = res_s, engine_vel = engine_vel, anchor_calls = anchor_calls,
                        stops = vc:stats().stops, confirmed_stops = vc:stats().confirmed_stops,
                        uncertain = vc:cleanup_uncertain(),
                    }
                elseif mode == 'zero_reply_reports_nonzero_speed' then
                    -- Zero set reply reports actual speed 100 m/s -> rejected, possible_motion stays true!
                    handler(nil, set_reply(req_0, lease1, 0, 0, 100, now))
                    now = now + 1.5
                    vc:tick()
                    assert(vc:stats().possible_motion == true)
                    local ok_s, res_s = vc:stop(anchored, 'test_bad_zero_speed')
                    assert(ok_s and res_s == 'stop_dispatched:test_bad_zero_speed')
                    handler(nil, stop_reply(issued[#issued].request, lease1, now))
                    return {
                        stop_res = res_s, engine_vel = engine_vel, anchor_calls = anchor_calls,
                        stops = vc:stats().stops, confirmed_stops = vc:stats().confirmed_stops,
                        uncertain = vc:cleanup_uncertain(),
                    }
                end
            end

            return {
                refused_zero = run_case('refused_zero'),
                missing_zero = run_case('missing_zero'),
                delayed_zero_stop_first = run_case('delayed_zero_stop_first'),
                delayed_zero_confirmed_before_stop = run_case('delayed_zero_confirmed_before_stop'),
                delayed_zero_superseded_by_newer_nonzero = run_case('delayed_zero_superseded_by_newer_nonzero'),
                zero_reply_reports_nonzero_speed = run_case('zero_reply_reports_nonzero_speed'),
            }
        """)
        for key in (
            'refused_zero',
            'missing_zero',
            'delayed_zero_stop_first',
            'delayed_zero_superseded_by_newer_nonzero',
            'zero_reply_reports_nonzero_speed',
        ):
            r = summary[key]
            self.assertTrue(r['stop_res'].startswith('stop_dispatched:'), f'{key}: {r}')
            self.assertEqual(r['engine_vel'], 0, f'{key} must zero engine velocity')
            self.assertEqual(r['anchor_calls'], 1, f'{key} must anchor before stop')
            self.assertEqual(r['stops'], 1)
            self.assertEqual(r['confirmed_stops'], 1)
            self.assertFalse(r['uncertain'])

        cz = summary['delayed_zero_confirmed_before_stop']
        self.assertEqual(cz['stop_res'], 'already_stopped')
        self.assertEqual(cz['anchor_calls'], 0)
        self.assertEqual(cz['stops'], 0)
        self.assertFalse(cz['uncertain'])
        self.assertEqual(summary['delayed_zero_stop_first']['fenced'], 1)

    def test_transition_interval_unconfirmed_when_physical_duration_unestablished(self):
        summary = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            local hull = dofile(root .. 'company_hull_playback.lua')
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')

            local epoch = ('a'):rep(32)
            local gen1 = ('1'):rep(32)
            local zone_a = '0x194'
            local binding = {slot = 'cockpit', serial = 1, epoch = epoch,
                sector_macro = 'sector_a', index = 2, actor = '0xca', code = 'TGT-002', generation = gen1}

            local function set_reply(req, lease, vz, gt)
                return ('%d|set|%d|0xca|sector_a|%s|%.6f|0,0,1000|0,0,%.6f|1,0,0|0,1,0|0,0,1|0,0,%.6f'):format(
                    req, lease, zone_a, gt, vz, vz)
            end

            local function run_repro(continuous_mode)
                local now, gt = 10.0, 50.0
                local actual = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}
                local issued, handler = {}, nil
                local records, faults = {}, {}
                local vbridge = velocity_mod.new({
                    enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                    own_index = 1, clock = function() return now end,
                    register = function(cb) handler = cb; return true end,
                    issue = function(args) issued[#issued + 1] = args; return true end,
                })
                local ctrl = hull.new({
                    enabled = true, own_index = 1, binding = binding, velocity_steps = true,
                    continuous_playback = continuous_mode, native_velocity = true, sample_version = 4,
                    acknowledged_velocity = function() return vbridge:acknowledged_velocity(binding, zone_a) end,
                    clock = function() return now end, game = function() return gt end,
                    paused = function() return false end, guard = function() return true end,
                    read = function() return {x = actual.x, y = actual.y, z = actual.z, yaw = 0, pitch = 0, roll = 0} end,
                    write = function(_, pose, _, _, _, vel)
                        actual = {x = pose.x, y = pose.y, z = pose.z, yaw = 0, pitch = 0, roll = 0}
                        if vel then vbridge:apply(binding, vel, nil, zone_a) end
                        return true
                    end,
                    record = function(ev)
                        if ev.kind == 'readback' or ev.kind == 'position_deviation' then
                            records[#records + 1] = ev
                        end
                        return true
                    end,
                    fault = function(reason) faults[#faults + 1] = reason end,
                })

                -- Push initial pair spanning INTERPOLATION_DELAY (0.15 s) so the first write at gt = 50.0
                -- renders interpolated 100 m/s motion and dispatches set(100) with no prior acked rate.
                assert(ctrl:push(binding, {
                    version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                    sequence = 1, source_time = 49.80, capture_time = 10.00,
                    x = 0, y = 0, z = 1000, real_velocity = {x = 0, y = 0, z = 100},
                    attitude = {yaw = 0, pitch = 0, roll = 0},
                }, 10.00))
                if continuous_mode then
                    now, gt = 10.00, 49.80
                    assert(ctrl:tick())
                    assert(#issued == 0)
                end
                now, gt = 10.20, 50.00
                assert(ctrl:push(binding, {
                    version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                    sequence = 2, source_time = 50.00, capture_time = 10.20,
                    x = 0, y = 0, z = 1020, real_velocity = {x = 0, y = 0, z = 100},
                    attitude = {yaw = 0, pitch = 0, roll = 0},
                }, 10.20))
                assert(ctrl:tick())
                assert(#issued == 1)
                assert(vbridge:acknowledged_velocity(binding, zone_a) == nil)

                -- Advance 0.2 game seconds to gt = 50.2, inject a 20 m offset, AND stamp the FIRST
                -- 100 m/s native receipt AT gt = 50.2 right before position readback!
                now, gt = 10.40, 50.20
                actual.z = actual.z + 20.0
                handler(nil, set_reply(issued[1].request, issued[1].lease, 100, 50.20))
                local ack1 = vbridge:acknowledged_velocity(binding, zone_a)
                assert(ack1 and ack1.z == 100 and ack1.game_time == 50.20 and ack1.request == issued[1].request)

                ctrl:tick()
                local first_ev = records[1]

                if not continuous_mode then
                    return {
                        records = #records,
                        first_kind = first_ev and first_ev.kind or nil,
                        fault = faults[1],
                        ack_game_time = ack1.game_time,
                    }
                end

                -- In continuous mode: first transition interval MUST be unconfirmed ('position_deviation',
                -- error == 20 m, applied_velocity == nil), and slot stays alive!
                -- Subsequent stable frame at gt = 50.40 MUST confirm cleanly because 100 m/s was already
                -- independently acknowledged at gt = 50.20 <= t_w!
                assert(ctrl:push(binding, {
                    version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                    sequence = 3, source_time = 50.20, capture_time = 10.40,
                    x = 0, y = 0, z = 1040, real_velocity = {x = 0, y = 0, z = 100},
                    attitude = {yaw = 0, pitch = 0, roll = 0},
                }, 10.40))
                now, gt = 10.60, 50.40
                actual.z = actual.z + 20.0 -- Genuine 100 m/s * 0.2 s established physical motion!
                assert(ctrl:tick())
                local second_ev = records[2]

                -- Next, test an unproved large mid-interval jump (100 -> 250 m/s over 0.2 s -> 30 m span)
                -- MUST ALSO stay unconfirmed even if actual lands inside [20 m, 50 m]!
                assert(ctrl:push(binding, {
                    version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                    sequence = 4, source_time = 50.40, capture_time = 10.60,
                    x = 0, y = 0, z = 1090, real_velocity = {x = 0, y = 0, z = 250},
                    attitude = {yaw = 0, pitch = 0, roll = 0},
                }, 10.60))
                now, gt = 10.65, 50.45
                actual.z = actual.z + 5.0 -- 100 m/s * 0.05 s (stable!)
                assert(ctrl:tick())
                -- That tick dispatched set(250). Now jump gt by 0.2 s, stamp set(250) AT 50.65,
                -- and inject an arbitrary 35 m offset (midpoint of [20 m, 50 m])!
                now, gt = 10.85, 50.65
                actual.z = actual.z + 35.0
                handler(nil, set_reply(issued[#issued].request, issued[#issued].lease, 250, 50.65))
                assert(ctrl:tick())
                local jump_ev = records[#records]

                -- Following stable 250 m/s frame (gt 50.65 -> 50.85, +50 m) MUST confirm cleanly!
                assert(ctrl:push(binding, {
                    version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                    sequence = 5, source_time = 50.65, capture_time = 10.85,
                    x = 0, y = 0, z = 1140, real_velocity = {x = 0, y = 0, z = 250},
                    attitude = {yaw = 0, pitch = 0, roll = 0},
                }, 10.85))
                now, gt = 11.05, 50.85
                actual.z = actual.z + 50.0
                assert(ctrl:tick())
                local stable_after_jump = records[#records]

                return {
                    first_kind = first_ev.kind,
                    first_error = first_ev.error,
                    first_credited = first_ev.applied_velocity ~= nil,
                    second_kind = second_ev.kind,
                    second_error = second_ev.error,
                    second_credited_z = second_ev.applied_velocity and second_ev.applied_velocity.z or nil,
                    jump_kind = jump_ev.kind,
                    jump_credited = jump_ev.applied_velocity ~= nil,
                    stable_after_jump_kind = stable_after_jump.kind,
                    stable_after_jump_error = stable_after_jump.error,
                    stable_after_jump_z = stable_after_jump.applied_velocity and stable_after_jump.applied_velocity.z or nil,
                    faults = #faults,
                }
            end

            return {
                continuous = run_repro(true),
                standard = run_repro(false),
            }
        """)
        c = summary['continuous']
        self.assertEqual(c['first_kind'], 'position_deviation')
        self.assertAlmostEqual(c['first_error'], 20.0, delta=0.05)
        self.assertFalse(c['first_credited'])
        self.assertEqual(c['second_kind'], 'readback')
        self.assertAlmostEqual(c['second_error'], 0.0, delta=1e-6)
        self.assertAlmostEqual(c['second_credited_z'], 100.0, delta=1e-6)
        self.assertEqual(c['jump_kind'], 'position_deviation')
        self.assertFalse(c['jump_credited'])
        self.assertEqual(c['stable_after_jump_kind'], 'readback')
        self.assertAlmostEqual(c['stable_after_jump_error'], 0.0, delta=1e-6)
        self.assertAlmostEqual(c['stable_after_jump_z'], 250.0, delta=1e-6)
        self.assertEqual(c['faults'], 0)

        s = summary['standard']
        self.assertEqual(s['records'], 0)
        self.assertEqual(s['fault'], 'readback_out_of_bounds')
        self.assertAlmostEqual(s['ack_game_time'], 50.2, delta=1e-6)

    def test_control_loss_roster_reuses_slot_and_pending_zone_stays_fenced(self):
        summary = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local native = dofile(root .. 'company_hull_playback_x4.lua')
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local cockpit = dofile(root .. 'company_cockpit_playback.lua')
            local stream_mod = dofile(root .. 'company_observer_stream.lua')

            local epoch = ('a'):rep(32)
            local gen1, gen2 = ('1'):rep(32), ('2'):rep(32)
            local prefix = 'C|lease123|localgen|'
            local own_actor, target_actor, sector_id, zone_a, zone_b = 101, 202, 303, 404, 405
            local zone_a_hex, zone_b_hex = ('0x%x'):format(zone_a), ('0x%x'):format(zone_b)

            -- Part A: Actual control-loss roster (73174762 bundled roster + 9ced39e8 pre-dispatch refusal)
            -- while moving with active velocity -> clean guarded stop, confirmed zero, slot REUSABLE for gen2!
            local now, gt = 10.0, 50.0
            local rostered = false
            local pose = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}
            local last_set = {x = 0, y = 0, z = 1000}
            local native_vel = {x = 0, y = 0, z = 0}
            local md_active, md_lease, md_stopped = nil, 0, 0
            local md_queue, md_handler = {}, nil
            local packets, events, faults = {}, {}, {}
            local observed = {epoch = epoch, index = 2, actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a'}

            local reader = {
                tick = function() return observed end,
                matches = function(_, ep, sec, idx, act, code)
                    return ep == epoch and sec == 'sector_a' and idx == 2 and act == '0xca' and code == 'TGT-002'
                end,
                is_active_pilot = function(_, idx) return rostered and idx == 2 end,
                close = function() end,
            }
            local vel_ctrl = velocity_mod.new({
                enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                own_index = 1, clock = function() return now end,
                register = function(cb) md_handler = cb; return true end,
                issue = function(args) md_queue[#md_queue + 1] = args; return true end,
            })
            local adapter = native.new({
                enabled = true, pinned = {epoch = epoch, issued_count = 10}, own_index = 1,
                observer = reader, guard = function() return true end, velocity = vel_ctrl,
            }, {
                cockpit = function() return {actor = own_actor, sector = sector_id, zone = zone_a} end,
                target = function() return {actor = target_actor, sector = sector_id, zone = zone_a, sector_macro = 'sector_a', code = 'TGT-002'} end,
                read = function() return {x = pose.x, y = pose.y, z = pose.z, yaw = 0, pitch = 0, roll = 0} end,
                write = function(_, _, p)
                    pose = {x = p.x, y = p.y, z = p.z, yaw = p.yaw, pitch = p.pitch, roll = p.roll}
                    last_set = {x = p.x, y = p.y, z = p.z}
                    return true
                end,
                destination_zone = function() return zone_a end,
                within_radar = function() return true end,
            })
            local playback = cockpit.new({
                enabled = true, own_index = 1, adapter = adapter,
                continuous_playback = true, native_velocity = true, allow_reacquire_align = true,
                clock = function() return now end, game = function() return gt end,
                paused = function() return false end, record = function() return true end,
                fault = function(r) faults[#faults + 1] = r end,
                on_event = function(kind) events[kind] = (events[kind] or 0) + 1 end,
            })
            local stream = stream_mod.new({
                cockpit = reader, map = {tick = function() end, close = function() end},
            }, epoch, {
                send = function() return true end,
                receive = function() return table.remove(packets, 1) end,
            }, prefix, function() return now end, playback, {
                set_pilot_roster = function(_, indices) rostered = (indices[1] == 2); return true end,
            })

            local function step(dt)
                now, gt = now + dt, gt + dt
                pose.z = pose.z + native_vel.z * dt
                local q = md_queue
                md_queue = {}
                for _, args in ipairs(q) do
                    if args.op == 'set' and args.lease > md_stopped and (md_active == nil or md_lease == args.lease) then
                        native_vel = {x = args.vx, y = args.vy, z = args.vz}
                        md_active, md_lease = target_actor, args.lease
                        md_handler(nil, ('%d|set|%d|0xca|sector_a|%s|%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f'):format(
                            args.request, args.lease, zone_a_hex, gt, pose.x, pose.y, pose.z,
                            args.vx, args.vy, args.vz, args.vx, args.vy, args.vz))
                    elseif args.op == 'stop' and md_active == target_actor and md_lease == args.lease then
                        local b = {x = pose.x, y = pose.y, z = pose.z}
                        pose.x, pose.y, pose.z = last_set.x, last_set.y, last_set.z
                        native_vel = {x = 0, y = 0, z = 0}
                        md_active, md_stopped = nil, args.lease
                        md_handler(nil, ('%d|stopped|%d|0xca|sector_a|%s|%.6f|%.6f,%.6f,%.6f|0,0,0|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f|0,0,60'):format(
                            args.request, args.lease, zone_a_hex, gt, pose.x, pose.y, pose.z, b.x, b.y, b.z))
                    else
                        md_handler(nil, ('%d|refused|%d|op'):format(args.request, args.lease))
                    end
                end
                stream:tick(true)
            end

            -- Establish serial 1 view, then stream gen1 with bundled @roster|1|2 at 60 m/s!
            step(0.05)
            local z = 1000.0
            for seq = 1, 8 do
                z = z + 6.0
                packets[#packets + 1] = prefix .. table.concat({
                    'REMOTE4', 'cockpit', 1, epoch, 2, gen1, seq, 'sector_a',
                    0, 0, z, 0, 0, 60, gt, now, now, 0, 0, 0,
                }, '|') .. '|@roster|1|2'
                step(0.05); step(0.05)
            end
            assert(rostered == true and native_vel.z == 60)

            -- Control-loss roster update (drops pilot 2, NO END packet): must stop immediately via
            -- pre-dispatch refusal, anchor + zero native velocity, and leave slot REUSABLE!
            packets[#packets + 1] = prefix .. 'ROSTER|' .. epoch .. '|2'
            step(0.05)
            step(0.05)
            local closs_vel = native_vel.z
            local closs_stats = playback:stats()
            local closs_stops = vel_ctrl:stats().confirmed_stops
            local pos_stopped = pose.z
            for _ = 1, 6 do step(0.05) end
            local closs_drift = math.abs(pose.z - pos_stopped)

            -- Reacquire with gen2 on the SAME slot (proving slot reuse after control-loss roster)!
            local z2 = pose.z
            for seq = 1, 6 do
                z2 = z2 + 6.0
                packets[#packets + 1] = prefix .. table.concat({
                    'REMOTE4', 'cockpit', 1, epoch, 2, gen2, seq, 'sector_a',
                    0, 0, z2, 0, 0, 60, gt, now, now, 0, 0, 0,
                }, '|') .. '|@roster|3|2'
                step(0.05); step(0.05)
            end
            local gen2_vel = native_vel.z
            local gen2_disp = pose.z - pos_stopped

            -- Part B: Unresolved pending_zone crossing with position deviation MUST fence slot closed (e2346578)!
            local pz_now, pz_gt = 20.0, 60.0
            local pz_pose = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}
            local pz_target_zone, pz_dest_zone = zone_a, zone_a
            local pz_readbacks, pz_devs = 0, 0
            local pz_handler, pz_queue = nil, {}
            local pz_vel = velocity_mod.new({
                enabled = true, slot = 'cockpit2', pinned = {epoch = epoch, issued_count = 10},
                own_index = 1, clock = function() return pz_now end,
                register = function(cb) pz_handler = cb; return true end,
                issue = function(args) pz_queue[#pz_queue + 1] = args; return true end,
            })
            local pz_obs = {
                matches = function() return true end,
                is_active_pilot = function() return true end,
                current_radar_range = function() return 40000 end,
            }
            local pz_adapter = native.new({
                enabled = true, pinned = {epoch = epoch, issued_count = 10}, own_index = 1,
                observer = pz_obs, guard = function() return true end, velocity = pz_vel,
            }, {
                cockpit = function() return {actor = own_actor, sector = sector_id, zone = zone_a} end,
                target = function() return {actor = target_actor, sector = sector_id, zone = pz_target_zone, sector_macro = 'sector_a', code = 'TGT-002'} end,
                read = function() return {x = pz_pose.x, y = pz_pose.y, z = pz_pose.z, yaw = 0, pitch = 0, roll = 0} end,
                write = function(_, _, p) pz_pose = {x = p.x, y = p.y, z = p.z, yaw = 0, pitch = 0, roll = 0}; return true end,
                destination_zone = function() return pz_dest_zone end,
                within_radar = function() return true end,
            })
            local pz_ctrl = cockpit.new({
                enabled = true, slot = 'cockpit2', own_index = 1, adapter = pz_adapter,
                continuous_playback = true, native_velocity = true,
                clock = function() return pz_now end, game = function() return pz_gt end,
                paused = function() return false end,
                record = function(ev)
                    if ev.kind == 'readback' then pz_readbacks = pz_readbacks + 1 end
                    if ev.kind == 'position_deviation' then pz_devs = pz_devs + 1 end
                    return true
                end,
                fault = function() end,
            })
            local pz_crossing = false
            for f = 1, 14 do
                pz_now, pz_gt = pz_now + 0.05, pz_gt + 0.05
                if pz_crossing then
                    pz_target_zone = zone_b
                    pz_pose.z = pz_pose.z + 25.0 -- Force position deviation during zone crossing!
                end
                if pz_readbacks >= 2 and not pz_crossing then
                    pz_dest_zone = zone_b
                    pz_crossing = true
                end
                pz_ctrl:sample(observed, 1, {
                    version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                    sequence = f, source_time = pz_gt, capture_time = pz_now,
                    x = 0, y = 0, z = 1000 + f * 3, real_velocity = {x = 0, y = 0, z = 60},
                    attitude = {yaw = 0, pitch = 0, roll = 0},
                }, pz_now)
                pz_ctrl:tick(pz_now)
                if pz_ctrl:stats().closed then break end
            end

            return {
                closs_rostered = rostered,
                closs_vel = closs_vel,
                closs_closed = closs_stats.closed,
                closs_uncertain = closs_stats.velocity_cleanup_uncertain,
                closs_stops = closs_stops,
                closs_drift = closs_drift,
                closs_track_stopped = events.track_stopped or 0,
                closs_closed_uncertain = events.closed_uncertain or 0,
                gen2_vel = gen2_vel,
                gen2_disp = gen2_disp,
                pz_devs = pz_devs,
                pz_closed = pz_ctrl:stats().closed,
            }
        """)
        self.assertEqual(summary['closs_vel'], 0)
        self.assertFalse(summary['closs_closed'])
        self.assertFalse(summary['closs_uncertain'])
        self.assertEqual(summary['closs_stops'], 1)
        self.assertEqual(summary['closs_drift'], 0)
        self.assertGreaterEqual(summary['closs_track_stopped'], 1)
        self.assertEqual(summary['closs_closed_uncertain'], 0)
        self.assertAlmostEqual(summary['gen2_vel'], 60.0, delta=1.0)
        self.assertGreater(summary['gen2_disp'], 20.0)
        self.assertGreaterEqual(summary['pz_devs'], 1)
        self.assertTrue(summary['pz_closed'])

    def test_valid_3000_mps_preserves_continuous_position_track_and_only_acked_velocity_is_credited(self):
        summary = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local native = dofile(root .. 'company_hull_playback_x4.lua')
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local cockpit = dofile(root .. 'company_cockpit_playback.lua')

            local epoch = ('a'):rep(32)
            local gen1 = ('1'):rep(32)
            local own_actor, target_actor, sector_id, zone_id = 101, 202, 303, 404
            local zone_hex = ('0x%x'):format(zone_id)

            local function build_harness(opts)
                local st = {
                    now = 10.0, gt = 50.0,
                    pose = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0},
                    last_set_pos = {x = 0, y = 0, z = 1000},
                    native_vel = {x = 0, y = 0, z = 0},
                    md_queue = {}, md_handler = nil,
                    md_active = nil, md_lease = 0, md_stopped = 0,
                    readbacks = {}, deviations = 0, faults = {},
                    hold_set_receipts = opts.hold_set_receipts == true,
                    refuse_sets = opts.refuse_sets == true,
                    refuse_above = opts.refuse_above or nil,
                }
                local view = {epoch = epoch, index = 2, actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a'}
                local obs = {
                    matches = function(_, ep, frame, idx, act, code)
                        return ep == epoch and frame == 'sector_a' and idx == 2 and act == '0xca' and code == 'TGT-002'
                    end,
                    is_active_pilot = function(_, idx) return idx == 2 end,
                    current_radar_range = function() return 40000 end,
                }
                local vel_ctrl = velocity_mod.new({
                    enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                    own_index = 1, clock = function() return st.now end,
                    register = function(cb) st.md_handler = cb; return true end,
                    issue = function(args) st.md_queue[#st.md_queue + 1] = args; return true end,
                })
                local adapter = native.new({
                    enabled = true, pinned = {epoch = epoch, issued_count = 10}, own_index = 1,
                    observer = obs, guard = function() return true end, velocity = vel_ctrl,
                }, {
                    cockpit = function() return {actor = own_actor, sector = sector_id, zone = zone_id} end,
                    target = function() return {actor = target_actor, sector = sector_id, zone = zone_id, sector_macro = 'sector_a', code = 'TGT-002'} end,
                    read = function() return {x = st.pose.x, y = st.pose.y, z = st.pose.z, yaw = 0, pitch = 0, roll = 0} end,
                    write = function(_, _, p)
                        st.pose = {x = p.x, y = p.y, z = p.z, yaw = p.yaw, pitch = p.pitch, roll = p.roll}
                        st.last_set_pos = {x = p.x, y = p.y, z = p.z}
                        return true
                    end,
                    destination_zone = function() return zone_id end,
                    within_radar = function() return true end,
                })
                local ctrl = cockpit.new_multi({
                    enabled = true, continuous_playback = opts.continuous ~= false, native_velocity = true,
                    allow_reacquire_align = true, own_index = 1, slot_names = {'cockpit'},
                    adapters = {cockpit = adapter},
                    clock = function() return st.now end, game = function() return st.gt end,
                    paused = function() return false end,
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

                local function drain_md()
                    local q = st.md_queue
                    st.md_queue = {}
                    for _, args in ipairs(q) do
                        if args.op == 'set' then
                            local spd = math.sqrt(args.vx^2 + args.vy^2 + args.vz^2)
                            if st.hold_set_receipts then
                                -- Hold receipt (queued/unacknowledged), do not reply!
                            elseif st.refuse_sets or (st.refuse_above and spd > st.refuse_above) then
                                st.md_handler(nil, ('%d|refused|%d'):format(args.request, args.lease))
                            elseif args.lease > st.md_stopped and (st.md_active == nil or st.md_lease == args.lease) then
                                st.native_vel = {x = args.vx, y = args.vy, z = args.vz}
                                st.md_active = target_actor
                                st.md_lease = args.lease
                                st.md_handler(nil, ('%d|set|%d|%s|sector_a|%s|%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f'):format(
                                    args.request, args.lease, args.actor, zone_hex, st.gt,
                                    st.pose.x, st.pose.y, st.pose.z,
                                    args.vx, args.vy, args.vz, args.vx, args.vy, args.vz))
                            else
                                st.md_handler(nil, ('%d|refused|%d'):format(args.request, args.lease))
                            end
                        elseif args.op == 'stop' then
                            if st.md_active == target_actor and st.md_lease == args.lease and args.zone == zone_hex then
                                local before_p = {x = st.pose.x, y = st.pose.y, z = st.pose.z}
                                st.pose.x, st.pose.y, st.pose.z = st.last_set_pos.x, st.last_set_pos.y, st.last_set_pos.z
                                st.native_vel = {x = 0, y = 0, z = 0}
                                st.md_active = nil
                                st.md_stopped = args.lease
                                st.md_handler(nil, ('%d|stopped|%d|%s|sector_a|%s|%.6f|%.6f,%.6f,%.6f|0,0,0|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f|0,0,600'):format(
                                    args.request, args.lease, args.actor, zone_hex, st.gt,
                                    st.pose.x, st.pose.y, st.pose.z, before_p.x, before_p.y, before_p.z))
                            else
                                st.md_handler(nil, ('%d|refused|%d'):format(args.request, args.lease))
                            end
                        end
                    end
                end

                local seq = 0
                local function step(dt)
                    st.now = st.now + dt
                    st.gt = st.gt + dt
                    st.pose.x = st.pose.x + st.native_vel.x * dt
                    st.pose.y = st.pose.y + st.native_vel.y * dt
                    st.pose.z = st.pose.z + st.native_vel.z * dt
                    drain_md()
                    ctrl:tick(st.now)
                end

                local function push(z, vz)
                    seq = seq + 1
                    return ctrl:sample(view, 1, {
                        version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                        sequence = seq, source_time = st.gt, capture_time = st.now, x = 0, y = 0, z = z,
                        real_velocity = vz and {x = 0, y = 0, z = vz} or nil,
                        attitude = {yaw = 0, pitch = 0, roll = 0},
                    }, st.now, 'cockpit')
                end

                return st, ctrl, vel_ctrl, step, push
            end

            -- Case A: Valid 3,000 m/s source sample from start (cleanly stopped auxiliary velocity path).
            local stA, ctrlA, velA, stepA, pushA = build_harness({continuous = true})
            local zA = 1000.0
            assert(pushA(zA, 3000))
            for k = 1, 15 do
                stepA(0.05)
                zA = zA + 300.0
                stepA(0.05)
                assert(pushA(zA, 3000))
            end
            stepA(0.05)
            local stA_c = ctrlA:slot_stats('cockpit')
            local vA_st = velA:stats()
            local any_credited_A = false
            for _, rb in ipairs(stA.readbacks) do
                if rb.applied_velocity ~= nil then any_credited_A = true end
            end

            -- Case B: Start moving at 600 m/s (acked), accelerate to 3,000 m/s (clean auxiliary stop),
            -- keep tracking continuous position at 3,000 m/s, then drop back to 400 m/s (velocity resumes)!
            local stB, ctrlB, velB, stepB, pushB = build_harness({continuous = true})
            local zB = 1000.0
            assert(pushB(zB, 600))
            for k = 1, 8 do
                stepB(0.05); zB = zB + 60.0; stepB(0.05)
                assert(pushB(zB, 600))
            end
            assert(stB.native_vel.z == 600)
            for k = 1, 10 do
                stepB(0.05); zB = zB + 300.0; stepB(0.05)
                assert(pushB(zB, 3000))
            end
            stepB(0.05)
            local vel_during_3000 = stB.native_vel.z
            local stops_during_3000 = velB:stats().confirmed_stops
            local z_after_3000 = stB.pose.z
            for k = 1, 8 do
                stepB(0.05); zB = zB + 40.0; stepB(0.05)
                assert(pushB(zB, 400))
            end
            stepB(0.05)
            local vel_resumed = stB.native_vel.z
            local stB_c = ctrlB:slot_stats('cockpit')

            -- Case C: Queued (unacknowledged) or refused set MUST NOT credit displacement compensation!
            local function check_unacked_not_credited(mode)
                local stC, ctrlC, velC, stepC, pushC = build_harness({
                    continuous = true,
                    hold_set_receipts = (mode == 'queued'),
                    refuse_sets = (mode == 'refused'),
                })
                local zC = 1000.0
                assert(pushC(zC, nil))
                for k = 1, 6 do
                    stepC(0.05); zC = zC + 10.0; stepC(0.05)
                    stC.pose.z = stC.pose.z + 10.0
                    assert(pushC(zC, 100))
                end
                stepC(0.05)
                local credited = 0
                for _, rb in ipairs(stC.readbacks) do
                    if rb.applied_velocity ~= nil then credited = credited + 1 end
                end
                return {deviations = stC.deviations, credited = credited, sets = velC:stats().sets}
            end

            -- Case D: Acked at 60 m/s, then 180 m/s refused -> ONLY 60 m/s credited, 180 m/s drift triggers deviation!
            local stD, ctrlD, velD, stepD, pushD = build_harness({continuous = true, refuse_above = 100})
            local zD = 1000.0
            assert(pushD(zD, nil))
            for k = 1, 6 do
                stepD(0.05); zD = zD + 6.0; stepD(0.05)
                assert(pushD(zD, 60))
            end
            assert(stD.deviations == 0 and stD.native_vel.z == 60)
            for k = 1, 6 do
                stepD(0.05); zD = zD + 18.0; stepD(0.05)
                stD.pose.z = stD.pose.z + 12.0
                assert(pushD(zD, 180))
            end
            stepD(0.05)

            return {
                caseA = {
                    closed = stA_c.closed,
                    uncertain = stA_c.velocity_cleanup_uncertain,
                    writes = stA_c.writes,
                    readbacks = #stA.readbacks,
                    deviations = stA.deviations,
                    disp = stA.pose.z - 1000.0,
                    sets = vA_st.sets,
                    skips = vA_st.unsupported_speed_skips,
                    any_credited = any_credited_A,
                },
                caseB = {
                    closed = stB_c.closed,
                    uncertain = stB_c.velocity_cleanup_uncertain,
                    readbacks = #stB.readbacks,
                    deviations = stB.deviations,
                    vel_during_3000 = vel_during_3000,
                    stops_during_3000 = stops_during_3000,
                    z_after_3000 = z_after_3000,
                    vel_resumed = vel_resumed,
                },
                caseC_queued = check_unacked_not_credited('queued'),
                caseC_refused = check_unacked_not_credited('refused'),
                caseD = {deviations = stD.deviations, refusals = velD:stats().refusals},
            }
        """)
        a = summary['caseA']
        self.assertFalse(a['closed'])
        self.assertFalse(a['uncertain'])
        self.assertGreater(a['writes'], 15)
        self.assertGreater(a['readbacks'], 15)
        self.assertEqual(a['deviations'], 0)
        self.assertGreater(a['disp'], 3500.0)
        self.assertEqual(a['sets'], 0)
        self.assertGreater(a['skips'], 10)
        self.assertFalse(a['any_credited'])

        b = summary['caseB']
        self.assertFalse(b['closed'])
        self.assertFalse(b['uncertain'])
        self.assertGreater(b['readbacks'], 40)
        # Transition frames when MD switches 600 -> 0 -> 400 remain honestly unconfirmed if unestablished,
        # while all subsequent stable frames confirm cleanly without closing the slot.
        self.assertLessEqual(b['deviations'], 2)
        self.assertEqual(b['vel_during_3000'], 0)
        self.assertEqual(b['stops_during_3000'], 1)
        self.assertGreater(b['z_after_3000'], 3500.0)
        self.assertAlmostEqual(b['vel_resumed'], 400.0, delta=5.0)

        for key in ('caseC_queued', 'caseC_refused'):
            c = summary[key]
            self.assertGreater(c['sets'], 0)
            self.assertEqual(c['credited'], 0, f'{key} must never credit unacknowledged velocity')
            self.assertGreater(c['deviations'], 0, f'{key} unacknowledged drift must trigger position_deviation')

        d = summary['caseD']
        self.assertGreater(d['refusals'], 0)
        self.assertGreater(d['deviations'], 0)

    def test_unequal_real_and_game_clocks_measurement_without_policy_change(self):
        """Measure the real-seconds vs. game-seconds handoff under unequal clocks (dt_game = k * dt_real)
        without altering clock policy."""
        results = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local native = dofile(root .. 'company_hull_playback_x4.lua')
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local cockpit = dofile(root .. 'company_cockpit_playback.lua')

            local epoch = ('a'):rep(32)
            local gen1 = ('1'):rep(32)
            local own_actor, target_actor, sector_id, zone_id = 101, 202, 303, 404
            local zone_hex = ('0x%x'):format(zone_id)

            local function measure(clock_scale)
                local now, gt = 10.0, 50.0
                local pose = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0}
                local last_set = {x = 0, y = 0, z = 1000}
                local native_vel = {x = 0, y = 0, z = 0}
                local md_active, md_lease, md_stopped = nil, 0, 0
                local md_queue, md_handler = {}, nil
                local readbacks, deviations = {}, 0
                local handoff_deltas = {}
                local view = {epoch = epoch, index = 2, actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a'}
                local obs = {
                    matches = function() return true end,
                    is_active_pilot = function() return true end,
                    current_radar_range = function() return 40000 end,
                }
                local vel_ctrl = velocity_mod.new({
                    enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                    own_index = 1, clock = function() return now end,
                    register = function(cb) md_handler = cb; return true end,
                    issue = function(args) md_queue[#md_queue + 1] = args; return true end,
                })
                local adapter = native.new({
                    enabled = true, pinned = {epoch = epoch, issued_count = 10}, own_index = 1,
                    observer = obs, guard = function() return true end, velocity = vel_ctrl,
                }, {
                    cockpit = function() return {actor = own_actor, sector = sector_id, zone = zone_id} end,
                    target = function() return {actor = target_actor, sector = sector_id, zone = zone_id, sector_macro = 'sector_a', code = 'TGT-002'} end,
                    read = function() return {x = pose.x, y = pose.y, z = pose.z, yaw = 0, pitch = 0, roll = 0} end,
                    write = function(_, _, p)
                        if native_vel.z > 50 then
                            handoff_deltas[#handoff_deltas + 1] = math.abs(p.z - pose.z)
                        end
                        pose = {x = p.x, y = p.y, z = p.z, yaw = 0, pitch = 0, roll = 0}
                        last_set = {x = p.x, y = p.y, z = p.z}
                        return true
                    end,
                    destination_zone = function() return zone_id end,
                    within_radar = function() return true end,
                })
                local ctrl = cockpit.new_multi({
                    enabled = true, continuous_playback = true, native_velocity = true,
                    own_index = 1, slot_names = {'cockpit'}, adapters = {cockpit = adapter},
                    clock = function() return now end, game = function() return gt end,
                    paused = function() return false end,
                    record = function(ev)
                        if ev.kind == 'readback' then readbacks[#readbacks + 1] = ev end
                        if ev.kind == 'position_deviation' then deviations = deviations + 1 end
                        return true
                    end,
                    fault = function() end,
                })

                local function step(dt_real)
                    local dt_game = dt_real * clock_scale
                    now = now + dt_real
                    gt = gt + dt_game
                    -- Native X4 physics integrates in game seconds!
                    pose.z = pose.z + native_vel.z * dt_game
                    local q = md_queue
                    md_queue = {}
                    for _, args in ipairs(q) do
                        if args.op == 'set' and args.lease > md_stopped and (md_active == nil or md_lease == args.lease) then
                            native_vel = {x = args.vx, y = args.vy, z = args.vz}
                            md_active, md_lease = target_actor, args.lease
                            md_handler(nil, ('%d|set|%d|0xca|sector_a|%s|%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f'):format(
                                args.request, args.lease, zone_hex, gt, pose.x, pose.y, pose.z,
                                args.vx, args.vy, args.vz, args.vx, args.vy, args.vz))
                        elseif args.op == 'stop' and md_active == target_actor and md_lease == args.lease then
                            local b = {x = pose.x, y = pose.y, z = pose.z}
                            pose.x, pose.y, pose.z = last_set.x, last_set.y, last_set.z
                            native_vel = {x = 0, y = 0, z = 0}
                            md_active, md_stopped = nil, args.lease
                            md_handler(nil, ('%d|stopped|%d|0xca|sector_a|%s|%.6f|%.6f,%.6f,%.6f|0,0,0|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f|0,0,100'):format(
                                args.request, args.lease, zone_hex, gt, pose.x, pose.y, pose.z, b.x, b.y, b.z))
                        end
                    end
                    ctrl:tick(now)
                end

                local seq, z = 0, 1000.0
                local function push()
                    seq = seq + 1
                    return ctrl:sample(view, 1, {
                        version = 4, epoch = epoch, index = 2, generation = gen1, sector_macro = 'sector_a',
                        sequence = seq, source_time = gt, capture_time = now, x = 0, y = 0, z = z,
                        real_velocity = {x = 0, y = 0, z = 100},
                        attitude = {yaw = 0, pitch = 0, roll = 0},
                    }, now, 'cockpit')
                end

                -- 2.0 real seconds of steady 100 m/s samples (dt_real = 0.05 s, 10 Hz sample cadence).
                assert(push())
                for k = 1, 20 do
                    step(0.05)
                    z = z + 10.0
                    step(0.05)
                    assert(push())
                end
                step(0.05)
                local net_disp_2s_real = pose.z - 1000.0

                -- Measure steady-state readback error and per-frame physics->setter handoff sawtooth delta.
                local max_rb_err = 0
                for _, rb in ipairs(readbacks) do
                    if rb.error > max_rb_err then max_rb_err = rb.error end
                end
                local steady_sum, steady_n = 0, 0
                for i = math.max(1, #handoff_deltas - 15), #handoff_deltas do
                    steady_sum = steady_sum + handoff_deltas[i]
                    steady_n = steady_n + 1
                end
                local mean_handoff_m = steady_n > 0 and (steady_sum / steady_n) or 0

                -- Now trigger 5-second packet loss and verify stale_5s fires at 5.0 s real time!
                local loss_real_0, loss_game_0 = now, gt
                local cutoff_real, cutoff_game = nil, nil
                for _ = 1, 120 do
                    step(0.05)
                    if not cutoff_real and ctrl:slot_stats('cockpit').stale_cutoffs == 1 then
                        cutoff_real = now - loss_real_0
                        cutoff_game = gt - loss_game_0
                    end
                end
                return {
                    scale = clock_scale,
                    readbacks = #readbacks,
                    deviations = deviations,
                    max_rb_err = max_rb_err,
                    mean_handoff_m = mean_handoff_m,
                    expected_handoff_m = 100.0 * math.abs(clock_scale - 1.0) * 0.05,
                    net_disp_2s_real = net_disp_2s_real,
                    cutoff_real_s = cutoff_real,
                    cutoff_game_s = cutoff_game,
                    stopped_vel = native_vel.z,
                    confirmed_stops = vel_ctrl:stats().confirmed_stops,
                    closed = ctrl:slot_stats('cockpit').closed,
                }
            end

            return {
                k0 = measure(0.0),
                k05 = measure(0.5),
                k1 = measure(1.0),
                k2 = measure(2.0),
                k6 = measure(6.0),
            }
        """)
        for key, scale in (('k0', 0.0), ('k05', 0.5), ('k1', 1.0), ('k2', 2.0), ('k6', 6.0)):
            r = results[key]
            self.assertEqual(r['deviations'], 0, f'{key} should have zero steady deviations')
            self.assertAlmostEqual(r['max_rb_err'], 0.0, delta=1e-5, msg=f'{key} readback matches target + v * dt_game')
            self.assertAlmostEqual(
                r['mean_handoff_m'],
                100.0 * abs(scale - 1.0) * 0.05,
                delta=0.25,
                msg=f'{key} sawtooth handoff jump must match |v|*|k-1|*dt_real',
            )
            self.assertGreater(r['net_disp_2s_real'], 175.0)
            self.assertLess(r['net_disp_2s_real'], 235.0)
            self.assertAlmostEqual(r['cutoff_real_s'], 5.0, delta=0.1)
            self.assertAlmostEqual(r['cutoff_game_s'], 5.0 * scale, delta=0.1 * max(1.0, scale))
            self.assertEqual(r['stopped_vel'], 0)
            self.assertEqual(r['confirmed_stops'], 1)
            self.assertFalse(r['closed'])

    def test_callback_expiry_for_set_stop_zero_and_out_of_order_without_housekeeping(self):
        """Replies arriving past RECEIPT_TIMEOUT (+2.0 s) BEFORE any housekeeping (tick/apply/stop)
        must be rejected inside the MD callback for SET, STOP, ordinary zero SET, and out-of-order replies."""
        summary = self._run_lua(r"""
            local root = _ROOT .. '/src/authority/lua/'
            local velocity_mod = dofile(root .. 'company_cockpit_velocity.lua')
            local epoch = ('a'):rep(32)
            local gen1 = ('1'):rep(32)
            local zone_a = '0x194'
            local bind1 = {epoch = epoch, index = 2, actor = '0xca', sector_macro = 'sector_a', code = 'TGT-002', generation = gen1}

            local function anchor_ok()
                return {x = 0, y = 0, z = 1200, yaw = 0, pitch = 0, roll = 0,
                        zone = zone_a, actor = '0xca', sector_macro = 'sector_a'}
            end

            local function set_reply(req, lease, vx, vy, vz, gt)
                return ('%d|set|%d|0xca|sector_a|%s|%.6f|0,0,1200|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f'):format(
                    req, lease, zone_a, gt, vx, vy, vz, vx, vy, vz)
            end

            local function stop_reply(req, lease, gt)
                return ('%d|stopped|%d|0xca|sector_a|%s|%.6f|0,0,1200|0,0,0|1,0,0|0,1,0|0,0,1|0,0,1200|0,0,100'):format(
                    req, lease, zone_a, gt)
            end

            local function make_v(clock_ref)
                local issued, events, handler = {}, {}, nil
                local v = velocity_mod.new({
                    enabled = true, slot = 'cockpit', pinned = {epoch = epoch, issued_count = 10},
                    own_index = 1, clock = function() return clock_ref.now end,
                    register = function(cb) handler = cb; return true end,
                    issue = function(args) issued[#issued + 1] = args; return true end,
                    on_event = function(row) events[#events + 1] = row end,
                })
                return v, issued, events, function(text) handler(nil, text) end
            end

            -- (a) Expired nonzero SET at +2.0 s BEFORE housekeeping: rejected, never updates acked_velocity,
            --     preserves possible_motion so stop() still anchors & dispatches STOP.
            local c_set = {now = 10.0}
            local v_set, q_set, ev_set, deliver_set = make_v(c_set)
            assert(v_set:apply(bind1, {x = 0, y = 0, z = 100}, anchor_ok, zone_a))
            local r1, l1 = q_set[1].request, q_set[1].lease
            c_set.now = 12.0 -- +2.0 s (> 1.5 s RECEIPT_TIMEOUT), NO tick()/housekeeping!
            deliver_set(set_reply(r1, l1, 0, 0, 100, 12.0))
            local ev_a = ev_set[#ev_set]
            local ack_a = v_set:acknowledged_velocity(bind1, zone_a)
            local pm_a = v_set:stats().possible_motion
            local anchor_a = 0
            local ok_sa, res_sa = v_set:stop(function() anchor_a = anchor_a + 1; return anchor_ok() end, 'after_exp_set')

            -- (b) Expired STOP at +2.0 s BEFORE housekeeping: rejected, marks stop_receipt_timeout uncertain,
            --     never increments confirmed_stops or reports clean cleanup!
            local c_stop = {now = 20.0}
            local v_stop, q_stop, ev_stop, deliver_stop = make_v(c_stop)
            assert(v_stop:apply(bind1, {x = 0, y = 0, z = 100}, anchor_ok, zone_a))
            deliver_stop(set_reply(q_stop[1].request, q_stop[1].lease, 0, 0, 100, 20.0))
            c_stop.now = 20.1
            assert(v_stop:stop(anchor_ok, 'test_exp_stop'))
            local sr, sl = q_stop[2].request, q_stop[2].lease
            c_stop.now = 22.1 -- +2.0 s (> 1.5 s), NO tick()/housekeeping!
            deliver_stop(stop_reply(sr, sl, 22.1))
            local ev_b = ev_stop[#ev_stop]
            local unc_b, why_b = v_stop:cleanup_uncertain()

            -- (c) Expired ordinary 0 m/s SET at +2.0 s BEFORE housekeeping: rejected, MUST NOT clear
            --     possible_motion, so subsequent stop() still anchors & dispatches MD STOP!
            local c_zero = {now = 30.0}
            local v_zero, q_zero, ev_zero, deliver_zero = make_v(c_zero)
            assert(v_zero:apply(bind1, {x = 0, y = 0, z = 100}, anchor_ok, zone_a)) -- lost receipt
            c_zero.now = 30.1
            assert(v_zero:apply(bind1, {x = 0, y = 0, z = 0}, anchor_ok, zone_a))
            local zr, zl = q_zero[2].request, q_zero[2].lease
            c_zero.now = 32.1 -- +2.0 s (> 1.5 s), NO tick()/housekeeping!
            deliver_zero(set_reply(zr, zl, 0, 0, 0, 32.1))
            local ev_c = ev_zero[#ev_zero]
            local pm_c = v_zero:stats().possible_motion
            local ack_c = v_zero:acknowledged_velocity(bind1, zone_a)
            local anchor_c = 0
            local ok_sc, res_sc = v_zero:stop(function() anchor_c = anchor_c + 1; return anchor_ok() end, 'after_exp_zero')
            assert(ok_sc and res_sc == 'stop_dispatched:after_exp_zero')
            deliver_zero(stop_reply(q_zero[#q_zero].request, zl, 32.1))

            -- (d) Out-of-order delayed reply: (d1) in-window fenced without overwriting acked_velocity;
            --     (d2) expired at +2.0 s before housekeeping rejected as set_receipt_timeout!
            local c_ooo = {now = 40.0}
            local v_ooo, q_ooo, ev_ooo, deliver_ooo = make_v(c_ooo)
            assert(v_ooo:apply(bind1, {x = 0, y = 0, z = 100}, anchor_ok, zone_a))
            local ooo_r1, ooo_l1 = q_ooo[1].request, q_ooo[1].lease
            c_ooo.now = 40.1
            assert(v_ooo:apply(bind1, {x = 0, y = 0, z = 60}, anchor_ok, zone_a))
            local ooo_r2 = q_ooo[2].request
            c_ooo.now = 40.2
            deliver_ooo(set_reply(ooo_r2, ooo_l1, 0, 0, 60, 40.2))
            c_ooo.now = 40.3 -- in-window out-of-order
            deliver_ooo(set_reply(ooo_r1, ooo_l1, 0, 0, 100, 40.0))
            local ev_d1 = ev_ooo[#ev_ooo]
            local ack_d1 = v_ooo:acknowledged_velocity(bind1, zone_a)

            local c_ooo_exp = {now = 50.0}
            local v_ooo_exp, q_ooo_exp, ev_ooo_exp, deliver_ooo_exp = make_v(c_ooo_exp)
            assert(v_ooo_exp:apply(bind1, {x = 0, y = 0, z = 100}, anchor_ok, zone_a))
            local oe_r1, oe_l1 = q_ooo_exp[1].request, q_ooo_exp[1].lease
            c_ooo_exp.now = 50.1
            assert(v_ooo_exp:apply(bind1, {x = 0, y = 0, z = 60}, anchor_ok, zone_a))
            local oe_r2 = q_ooo_exp[2].request
            c_ooo_exp.now = 50.2
            deliver_ooo_exp(set_reply(oe_r2, oe_l1, 0, 0, 60, 50.2))
            c_ooo_exp.now = 52.1 -- +2.1 s (> 1.5 s), NO tick()/housekeeping!
            deliver_ooo_exp(set_reply(oe_r1, oe_l1, 0, 0, 100, 50.0))
            local ev_d2 = ev_ooo_exp[#ev_ooo_exp]
            local ack_d2 = v_ooo_exp:acknowledged_velocity(bind1, zone_a)

            -- (e) Expired late nonzero SET arriving after confirmed STOP (active == nil) at +2.0 s
            --     WITHOUT housekeeping MUST still surface late_set_after_stop uncertainty!
            local c_late = {now = 60.0}
            local v_late, q_late, ev_late, deliver_late = make_v(c_late)
            assert(v_late:apply(bind1, {x = 0, y = 0, z = 100}, anchor_ok, zone_a))
            local lr1, ll1 = q_late[1].request, q_late[1].lease
            c_late.now = 60.1
            assert(v_late:stop(anchor_ok, 'clean_first'))
            deliver_late(stop_reply(q_late[2].request, ll1, 60.1))
            assert(v_late:cleanup_uncertain() == false)
            c_late.now = 62.1 -- +2.1 s (> 1.5 s), NO tick()/housekeeping!
            deliver_late(set_reply(lr1, ll1, 0, 0, 100, 62.1))
            local ev_e = ev_late[#ev_late]
            local unc_e, why_e = v_late:cleanup_uncertain()

            return {
                set_case = {
                    ok = ev_a.ok, expired = ev_a.expired, fenced = ev_a.fenced, fail = ev_a.fail_reason,
                    acked = ack_a ~= nil, possible_motion = pm_a,
                    timeouts = v_set:stats().timeouts, receipts = v_set:stats().receipts,
                    stop_ok = ok_sa, stop_res = res_sa, anchor_calls = anchor_a,
                },
                stop_case = {
                    ok = ev_b.ok, expired = ev_b.expired, fenced = ev_b.fenced,
                    confirmed_zero = ev_b.confirmed_zero, fail = ev_b.fail_reason,
                    confirmed_stops = v_stop:stats().confirmed_stops,
                    pending_stops = v_stop:stats().pending_stops,
                    timeouts = v_stop:stats().timeouts,
                    uncertain = unc_b, why = why_b,
                },
                zero_case = {
                    ok = ev_c.ok, expired = ev_c.expired, fenced = ev_c.fenced, fail = ev_c.fail_reason,
                    possible_motion = pm_c, acked = ack_c ~= nil,
                    anchor_calls = anchor_c, stop_res = res_sc,
                    confirmed_stops = v_zero:stats().confirmed_stops,
                    uncertain = v_zero:cleanup_uncertain(),
                },
                ooo_in_window = {
                    fenced = ev_d1.fenced, expired = ev_d1.expired == true,
                    acked_z = ack_d1 and ack_d1.z or nil,
                    fenced_count = v_ooo:stats().fenced_receipts,
                },
                ooo_expired = {
                    ok = ev_d2.ok, expired = ev_d2.expired, fenced = ev_d2.fenced, fail = ev_d2.fail_reason,
                    acked_z = ack_d2 and ack_d2.z or nil,
                    timeouts = v_ooo_exp:stats().timeouts,
                },
                late_after_stop = {
                    ok = ev_e.ok, expired = ev_e.expired, fenced = ev_e.fenced,
                    uncertain = unc_e, why = why_e,
                    timeouts = v_late:stats().timeouts,
                },
            }
        """)
        a = summary['set_case']
        self.assertFalse(a['ok'])
        self.assertTrue(a['expired'])
        self.assertTrue(a['fenced'])
        self.assertEqual(a['fail'], 'set_receipt_timeout')
        self.assertFalse(a['acked'])
        self.assertTrue(a['possible_motion'])
        self.assertEqual(a['timeouts'], 1)
        self.assertEqual(a['receipts'], 0)
        self.assertTrue(a['stop_ok'])
        self.assertEqual(a['stop_res'], 'stop_dispatched:after_exp_set')
        self.assertEqual(a['anchor_calls'], 1)

        b = summary['stop_case']
        self.assertFalse(b['ok'])
        self.assertTrue(b['expired'])
        self.assertTrue(b['fenced'])
        self.assertFalse(b['confirmed_zero'])
        self.assertEqual(b['fail'], 'stop_receipt_timeout')
        self.assertEqual(b['confirmed_stops'], 0)
        self.assertEqual(b['pending_stops'], 0)
        self.assertEqual(b['timeouts'], 1)
        self.assertTrue(b['uncertain'])
        self.assertEqual(b['why'], 'stop_receipt_timeout')

        c = summary['zero_case']
        self.assertFalse(c['ok'])
        self.assertTrue(c['expired'])
        self.assertTrue(c['fenced'])
        self.assertEqual(c['fail'], 'set_receipt_timeout')
        self.assertTrue(c['possible_motion'])
        self.assertFalse(c['acked'])
        self.assertEqual(c['anchor_calls'], 1)
        self.assertEqual(c['stop_res'], 'stop_dispatched:after_exp_zero')
        self.assertEqual(c['confirmed_stops'], 1)
        self.assertFalse(c['uncertain'])

        d1 = summary['ooo_in_window']
        self.assertTrue(d1['fenced'])
        self.assertFalse(d1['expired'])
        self.assertEqual(d1['acked_z'], 60)
        self.assertEqual(d1['fenced_count'], 1)

        d2 = summary['ooo_expired']
        self.assertFalse(d2['ok'])
        self.assertTrue(d2['expired'])
        self.assertTrue(d2['fenced'])
        self.assertEqual(d2['fail'], 'set_receipt_timeout')
        self.assertEqual(d2['acked_z'], 60)
        self.assertEqual(d2['timeouts'], 1)

        e = summary['late_after_stop']
        self.assertFalse(e['ok'])
        self.assertTrue(e['expired'])
        self.assertTrue(e['fenced'])
        self.assertTrue(e['uncertain'])
        self.assertEqual(e['why'], 'late_set_after_stop')
        self.assertEqual(e['timeouts'], 1)

    def test_pending_stop_serializes_same_actor_handoff_and_fences_ambiguous_actor(self):
        """After a guarded STOP is dispatched, a new generation of the same actor MUST NOT dispatch
        either pose or velocity before the old STOP is confirmed; if the STOP fails/expires or view
        loss blocks anchoring, the ambiguous actor must remain fenced across all slots."""
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
            local zone_hex = ('0x%x'):format(zone_id)

            local function build_rig()
                local st = {
                    now = 10.0, gt = 50.0,
                    obs_matches = true,
                    pose = {x = 0, y = 0, z = 1000, yaw = 0, pitch = 0, roll = 0},
                    last_set = {x = 0, y = 0, z = 1000},
                    native_vel = {x = 0, y = 0, z = 0},
                    writes = 0, md_sets = 0, md_stops = 0,
                    hold_stops = false,
                    held_stops = {},
                    md_queue = {},
                    md_handler = nil,
                    md_active = {}, md_lease = {}, md_stopped = {},
                    faults = {}, events = {},
                }
                local view = {epoch = epoch, index = 2, actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a'}
                local obs = {
                    matches = function(_, ep, frame, idx, act, code)
                        return st.obs_matches and ep == epoch and frame == 'sector_a'
                            and idx == 2 and act == '0xca' and code == 'TGT-002'
                    end,
                    is_active_pilot = function(_, idx) return st.obs_matches and idx == 2 end,
                    current_radar_range = function() return 40000 end,
                }
                local claims = {by_actor = {}, by_slot = {}, fenced_actors = {}}
                local vels, adapters = {}, {}
                for _, slot in ipairs({'cockpit', 'cockpit2'}) do
                    vels[slot] = velocity_mod.new({
                        enabled = true, slot = slot, pinned = {epoch = epoch, issued_count = 10},
                        own_index = 1, clock = function() return st.now end,
                        register = function(cb) st.md_handler = cb; return true end,
                        issue = function(args)
                            if args.op == 'set' then st.md_sets = st.md_sets + 1 end
                            if args.op == 'stop' then st.md_stops = st.md_stops + 1 end
                            if args.op == 'stop' and st.hold_stops then
                                st.held_stops[#st.held_stops + 1] = args
                            else
                                st.md_queue[#st.md_queue + 1] = args
                            end
                            return true
                        end,
                    })
                    adapters[slot] = native.new({
                        enabled = true, pinned = {epoch = epoch, issued_count = 10}, own_index = 1,
                        observer = obs, guard = function() return true end,
                        velocity = vels[slot], exclusive_claims = claims,
                    }, {
                        cockpit = function() return {actor = own_actor, sector = sector_id, zone = zone_id} end,
                        target = function() return {actor = target_actor, sector = sector_id, zone = zone_id, sector_macro = 'sector_a', code = 'TGT-002'} end,
                        read = function() return {x = st.pose.x, y = st.pose.y, z = st.pose.z, yaw = 0, pitch = 0, roll = 0} end,
                        write = function(_, _, p)
                            st.writes = st.writes + 1
                            st.pose = {x = p.x, y = p.y, z = p.z, yaw = p.yaw, pitch = p.pitch, roll = p.roll}
                            st.last_set = {x = p.x, y = p.y, z = p.z}
                            return true
                        end,
                        destination_zone = function() return zone_id end,
                        within_radar = function() return true end,
                    })
                end
                local ctrl = cockpit.new_multi({
                    enabled = true, continuous_playback = true, native_velocity = true,
                    allow_reacquire_align = true, own_index = 1,
                    slot_names = {'cockpit', 'cockpit2'}, adapters = adapters,
                    clock = function() return st.now end, game = function() return st.gt end,
                    paused = function() return false end, record = function() return true end,
                    fault = function(reason, unc, stage, slot)
                        st.faults[#st.faults + 1] = {reason = reason, unc = unc, stage = stage, slot = slot}
                    end,
                    on_event = function(kind, detail)
                        st.events[#st.events + 1] = {kind = kind, detail = detail}
                    end,
                })

                local function exec_md(args)
                    local stopped = st.md_stopped[args.slot] or 0
                    local active_ship = st.md_active[args.slot]
                    local active_lease = st.md_lease[args.slot] or 0
                    if args.op == 'set' and args.lease > stopped
                            and (active_ship == nil or (active_ship == target_actor and active_lease == args.lease)) then
                        st.native_vel = {x = args.vx, y = args.vy, z = args.vz}
                        st.md_active[args.slot] = target_actor
                        st.md_lease[args.slot] = args.lease
                        st.md_handler(nil, ('%d|set|%d|0xca|sector_a|%s|%.6f|%.6f,%.6f,%.6f|%.6f,%.6f,%.6f|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f'):format(
                            args.request, args.lease, zone_hex, st.gt, st.pose.x, st.pose.y, st.pose.z,
                            args.vx, args.vy, args.vz, args.vx, args.vy, args.vz))
                    elseif args.op == 'stop' and active_ship == target_actor and active_lease == args.lease then
                        local b = {x = st.pose.x, y = st.pose.y, z = st.pose.z}
                        st.pose.x, st.pose.y, st.pose.z = st.last_set.x, st.last_set.y, st.last_set.z
                        st.native_vel = {x = 0, y = 0, z = 0}
                        st.md_active[args.slot] = nil
                        st.md_stopped[args.slot] = args.lease
                        st.md_handler(nil, ('%d|stopped|%d|0xca|sector_a|%s|%.6f|%.6f,%.6f,%.6f|0,0,0|1,0,0|0,1,0|0,0,1|%.6f,%.6f,%.6f|0,0,60'):format(
                            args.request, args.lease, zone_hex, st.gt, st.pose.x, st.pose.y, st.pose.z, b.x, b.y, b.z))
                    else
                        st.md_handler(nil, ('%d|refused|%d|op'):format(args.request, args.lease))
                    end
                end

                local function drain_md()
                    local q = st.md_queue
                    st.md_queue = {}
                    for _, args in ipairs(q) do exec_md(args) end
                end

                local function step(dt)
                    st.now, st.gt = st.now + dt, st.gt + dt
                    st.pose.z = st.pose.z + st.native_vel.z * dt
                    drain_md()
                    ctrl:tick(st.now)
                end

                local seq = 0
                local function push(slot, gen, z, vz)
                    seq = seq + 1
                    return ctrl:sample(view, 1, {
                        version = 4, epoch = epoch, index = 2, generation = gen, sector_macro = 'sector_a',
                        sequence = seq, source_time = st.gt, capture_time = st.now, x = 0, y = 0, z = z,
                        real_velocity = vz and {x = 0, y = 0, z = vz} or nil,
                        attitude = {yaw = 0, pitch = 0, roll = 0},
                    }, st.now, slot)
                end

                -- Establish moving gen1 at 60 m/s on cockpit.
                local z = 1000.0
                assert(push('cockpit', gen1, z, nil))
                for _ = 1, 6 do
                    step(0.05); z = z + 6.0; step(0.05)
                    assert(push('cockpit', gen1, z, 60))
                end
                step(0.05)
                assert(st.native_vel.z == 60)

                return st, view, claims, vels, adapters, ctrl, exec_md, step, push, z
            end

            -- Path A: Pending STOP blocks gen2 pose & velocity on BOTH same slot and second slot,
            --         then confirmed STOP releases claim so gen2 cleanly binds and flies!
            local stA, viewA, claimsA, velsA, adaptersA, ctrlA, execA, stepA, pushA, zA = build_rig()
            stA.hold_stops = true
            ctrlA:end_track(viewA, 1, gen1, 'cockpit')
            assert(#stA.held_stops == 1)
            local writes_at_stop, sets_at_stop = stA.writes, stA.md_sets

            -- Advance 0.05 s (within timeout) and try gen2 on BOTH cockpit and cockpit2 BEFORE STOP confirms!
            stA.now, stA.gt = stA.now + 0.05, stA.gt + 0.05
            local ok_same, why_same = pushA('cockpit', gen2, stA.pose.z + 3, 50)
            local ok_other, why_other = pushA('cockpit2', gen2, stA.pose.z + 3, 50)
            local a_bind_same, a_bind_same_why = adaptersA.cockpit:bind({
                slot = 'cockpit', serial = 1, epoch = epoch, index = 2,
                actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a', generation = gen2,
            })
            local a_bind_other, a_bind_other_why = adaptersA.cockpit2:bind({
                slot = 'cockpit2', serial = 1, epoch = epoch, index = 2,
                actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a', generation = gen2,
            })
            local writes_while_pending = stA.writes - writes_at_stop
            local sets_while_pending = stA.md_sets - sets_at_stop
            local faults_while_pending = #stA.faults

            -- Now confirm the held gen1 STOP within timeout!
            execA(stA.held_stops[1])
            stA.held_stops = {}
            stA.hold_stops = false
            assert(velsA.cockpit:stats().confirmed_stops == 1)
            assert(claimsA.by_actor['0xca'] == nil and claimsA.by_slot.cockpit == nil)

            -- gen2 now cleanly binds, writes pose, and applies native velocity (50 m/s)!
            local zA2 = stA.pose.z
            assert(pushA('cockpit', gen2, zA2, nil))
            for _ = 1, 6 do
                stepA(0.05); zA2 = zA2 + 5.0; stepA(0.05)
                assert(pushA('cockpit', gen2, zA2, 50))
            end
            stepA(0.05)

            -- Path B: Held STOP expires (+2.0 s) inside callback BEFORE housekeeping ->
            --         fences BOTH original slot (closed_uncertain) AND ambiguous actor across ALL slots!
            local stB, viewB, claimsB, velsB, adaptersB, ctrlB, execB, _, pushB = build_rig()
            stB.hold_stops = true
            ctrlB:end_track(viewB, 1, gen1, 'cockpit')
            assert(#stB.held_stops == 1)
            local writes_B0, sets_B0 = stB.writes, stB.md_sets
            stB.now, stB.gt = stB.now + 2.0, stB.gt + 2.0 -- +2.0 s (> 1.5 s), NO tick()!
            execB(stB.held_stops[1]) -- Late STOP reply arrives in callback past timeout!
            local b_same_ok, b_same_why = pushB('cockpit', gen2, stB.pose.z + 3, 50)
            local b2_sess, b2_why = adaptersB.cockpit2:bind({
                slot = 'cockpit2', serial = 1, epoch = epoch, index = 2,
                actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a', generation = gen2,
            })
            local b2_sample_ok, b2_sample_why = pushB('cockpit2', gen2, stB.pose.z + 3, 50)

            -- Path C1: Held STOP times out silently (+2.0 s) and slot 2 tries to bind BEFORE slot 1 ticks ->
            --          cross-slot claim tick expires slot 1 stop and fences actor immediately!
            local stC1, viewC1, claimsC1, velsC1, adaptersC1, ctrlC1 = build_rig()
            stC1.hold_stops = true
            ctrlC1:end_track(viewC1, 1, gen1, 'cockpit')
            local writes_C1, sets_C1 = stC1.writes, stC1.md_sets
            stC1.now, stC1.gt = stC1.now + 2.0, stC1.gt + 2.0 -- NO slot 1 tick!
            local c1_sess, c1_why = adaptersC1.cockpit2:bind({
                slot = 'cockpit2', serial = 1, epoch = epoch, index = 2,
                actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a', generation = gen2,
            })

            -- Path C2: View-loss unanchored stop failure fences ambiguous actor across ALL slots!
            local stC2, _, claimsC2, _, adaptersC2, ctrlC2 = build_rig()
            local writes_C2, sets_C2 = stC2.writes, stC2.md_sets
            stC2.obs_matches = false
            ctrlC2:view(nil, 1, 'cockpit')
            stC2.obs_matches = true -- Even if observer reappears on slot 2, ambiguous actor stays fenced!
            local c2_sess, c2_why = adaptersC2.cockpit2:bind({
                slot = 'cockpit2', serial = 1, epoch = epoch, index = 2,
                actor = '0xca', code = 'TGT-002', sector_macro = 'sector_a', generation = gen2,
            })

            return {
                pathA = {
                    ok_same = ok_same, why_same = why_same,
                    ok_other = ok_other, why_other = why_other,
                    b_same_nil = a_bind_same == nil, b_same_why = a_bind_same_why,
                    b_other_nil = a_bind_other == nil, b_other_why = a_bind_other_why,
                    writes_while_pending = writes_while_pending,
                    sets_while_pending = sets_while_pending,
                    faults_while_pending = faults_while_pending,
                    writes_after_confirm = stA.writes - writes_at_stop,
                    sets_after_confirm = stA.md_sets - sets_at_stop,
                    gen2_vel = stA.native_vel.z,
                    closed = ctrlA:slot_stats('cockpit').closed,
                    uncertain = ctrlA:slot_stats('cockpit').velocity_cleanup_uncertain,
                },
                pathB = {
                    vel_unc = velsB.cockpit:cleanup_uncertain(),
                    confirmed_stops = velsB.cockpit:stats().confirmed_stops,
                    fenced_reason = claimsB.fenced_actors['0xca'],
                    same_ok = b_same_ok, same_why = b_same_why,
                    same_closed = ctrlB:slot_stats('cockpit').closed,
                    same_unc = ctrlB:slot_stats('cockpit').velocity_cleanup_uncertain,
                    other_bind_nil = b2_sess == nil, other_bind_why = b2_why,
                    other_sample_ok = b2_sample_ok, other_sample_why = b2_sample_why,
                    writes_after = stB.writes - writes_B0,
                    sets_after = stB.md_sets - sets_B0,
                },
                pathC1 = {
                    bind_nil = c1_sess == nil, bind_why = c1_why,
                    vel1_unc = velsC1.cockpit:cleanup_uncertain(),
                    fenced_reason = claimsC1.fenced_actors['0xca'],
                    writes_after = stC1.writes - writes_C1,
                    sets_after = stC1.md_sets - sets_C1,
                },
                pathC2 = {
                    bind_nil = c2_sess == nil, bind_why = c2_why,
                    fenced_reason = claimsC2.fenced_actors['0xca'],
                    writes_after = stC2.writes - writes_C2,
                    sets_after = stC2.md_sets - sets_C2,
                },
            }
        """)
        a = summary['pathA']
        self.assertFalse(a['ok_same'])
        self.assertEqual(a['why_same'], 'stop_pending')
        self.assertFalse(a['ok_other'])
        self.assertEqual(a['why_other'], 'stop_pending')
        self.assertTrue(a['b_same_nil'])
        self.assertEqual(a['b_same_why'], 'stop_pending')
        self.assertTrue(a['b_other_nil'])
        self.assertEqual(a['b_other_why'], 'stop_pending')
        self.assertEqual(a['writes_while_pending'], 0)
        self.assertEqual(a['sets_while_pending'], 0)
        self.assertEqual(a['faults_while_pending'], 0)
        self.assertGreater(a['writes_after_confirm'], 5)
        self.assertGreater(a['sets_after_confirm'], 0)
        self.assertAlmostEqual(a['gen2_vel'], 50.0, delta=1.0)
        self.assertFalse(a['closed'])
        self.assertFalse(a['uncertain'])

        b = summary['pathB']
        self.assertTrue(b['vel_unc'])
        self.assertEqual(b['confirmed_stops'], 0)
        self.assertEqual(b['fenced_reason'], 'stop_receipt_timeout')
        self.assertFalse(b['same_ok'])
        self.assertEqual(b['same_why'], 'velocity_cleanup_uncertain')
        self.assertTrue(b['same_closed'])
        self.assertTrue(b['same_unc'])
        self.assertTrue(b['other_bind_nil'])
        self.assertEqual(b['other_bind_why'], 'actor_fenced')
        self.assertFalse(b['other_sample_ok'])
        self.assertEqual(b['other_sample_why'], 'native_binding')
        self.assertEqual(b['writes_after'], 0)
        self.assertEqual(b['sets_after'], 0)

        c1 = summary['pathC1']
        self.assertTrue(c1['bind_nil'])
        self.assertEqual(c1['bind_why'], 'actor_fenced')
        self.assertTrue(c1['vel1_unc'])
        self.assertEqual(c1['fenced_reason'], 'stop_receipt_timeout')
        self.assertEqual(c1['writes_after'], 0)
        self.assertEqual(c1['sets_after'], 0)

        c2 = summary['pathC2']
        self.assertTrue(c2['bind_nil'])
        self.assertEqual(c2['bind_why'], 'actor_fenced')
        self.assertEqual(c2['fenced_reason'], 'anchor_observer_mismatch')
        self.assertEqual(c2['writes_after'], 0)
        self.assertEqual(c2['sets_after'], 0)


if __name__ == '__main__':
    unittest.main()
