"""Offline LuaJIT unit tests for opt-in Company cockpit native velocity controller.

Synthetic / mocked MD harness (not native X4 gameplay evidence): exercises the real
Lua module src/authority/lua/company_cockpit_native_velocity.lua under LuaJIT 2.1.
"""
from pathlib import Path
import unittest

try:
    from lupa.luajit21 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
LUA_MODULE = ROOT / 'src' / 'authority' / 'lua' / 'company_cockpit_native_velocity.lua'


@unittest.skipIf(LuaRuntime is None, 'lupa LuaJIT 2.1 runtime required')
class CompanyCockpitNativeVelocityTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.mod = self.lua.eval(f"assert(loadfile({str(LUA_MODULE)!r}))()")

    def test_default_off_enforced(self):
        ok, err = self.mod.new({})
        self.assertIsNone(ok)
        self.assertEqual(err, 'disabled')
        ok2, err2 = self.mod.new(self.lua.table_from({'enabled': False}))
        self.assertIsNone(ok2)
        self.assertEqual(err2, 'disabled')

    def test_hud_speed_formatter_matches_stock_targetsystem(self):
        fmt = self.mod.format_hud_speed
        self.assertEqual(fmt(0), '0 m/s')
        self.assertEqual(fmt(60), '60 m/s')
        self.assertEqual(fmt(-60), '-60 m/s')
        self.assertEqual(fmt(58.69), '58 m/s')
        self.assertEqual(fmt(9999), '9999 m/s')
        self.assertEqual(fmt(12540), '12.5 km/s')
        self.assertEqual(fmt(125400), '125 km/s')

    def test_forward_lateral_diagonal_reverse_and_v4_turn_speed_transition(self):
        code = f"""
        local M = assert(loadfile({str(LUA_MODULE)!r}))()
        local t_real, t_game = 10.0, 100.0
        local cb, rows, ops = nil, {{}}, {{}}
        local epoch, gen = string.rep('a', 32), string.rep('b', 32)
        local binding = {{
            epoch = epoch, slot = 'cockpit', serial = 1, index = 2,
            actor = '0x42', sector_macro = 'cluster_14_sector001_macro',
            code = 'TST-002', generation = gen,
        }}
        local pos = {{x = 0, y = 0, z = 1200}}
        local vel_sector = {{x = 0, y = 0, z = 0}}
        local yaw = 0.0
        local function basis(y)
            local c, s = math.cos(y), math.sin(y)
            return {{x = c, y = 0, z = -s}}, {{x = 0, y = 1, z = 0}}, {{x = s, y = 0, z = c}}
        end
        local function to_local(v, r, u, f)
            return {{
                x = v.x * r.x + v.y * r.y + v.z * r.z,
                y = v.x * u.x + v.y * u.y + v.z * u.z,
                z = v.x * f.x + v.y * f.y + v.z * f.z,
            }}
        end
        local function vec_str(v) return string.format('%.6f,%.6f,%.6f', v.x, v.y, v.z) end
        local anchor_calls = 0
        local adapter = M.new({{
            enabled = true,
            pinned = {{epoch = epoch, issued_count = 3}},
            own_index = 1,
            guard = function() return true end,
            paused = function() return false end,
            clock = function() return t_real end,
            game = function() return t_game end,
            observer = {{
                is_active_pilot = function() return true end,
                matches = function() return true end,
            }},
            register = function(fn) cb = fn; return true end,
            record = function(r) rows[#rows + 1] = r end,
            anchor = function() anchor_calls = anchor_calls + 1; return {{x = pos.x, y = pos.y, z = pos.z}} end,
            issue = function(args)
                ops[#ops + 1] = args.op
                local before_pos = {{x = pos.x, y = pos.y, z = pos.z}}
                local r, u, f = basis(yaw)
                local before_local = to_local(vel_sector, r, u, f)
                local status = ({{baseline='baseline', apply='dispatched', readback='readback',
                    zero='zeroed', stop='released', release_readback='release_readback'}})[args.op]
                if args.op == 'apply' then
                    vel_sector = {{x = args.vx, y = args.vy, z = args.vz}}
                elseif args.op == 'zero' or args.op == 'stop' then
                    vel_sector = {{x = 0, y = 0, z = 0}}
                end
                local loc = to_local(vel_sector, r, u, f)
                local extra = ''
                if args.op == 'apply' then
                    extra = '|' .. vec_str(vel_sector) .. '|' .. tostring(args.constantvel)
                elseif args.op == 'zero' or args.op == 'stop' then
                    extra = '|' .. vec_str(before_pos) .. '|' .. vec_str(before_local)
                end
                cb(nil, table.concat({{
                    args.request, status, binding.actor, binding.sector_macro, '0x99',
                    string.format('%.6f', t_game), vec_str(pos), vec_str(loc),
                    vec_str(r), vec_str(u), vec_str(f), '1200.0'
                }}, '|') .. extra)
                return true
            end,
        }})

        local function push_v4(seq, v, y_rad)
            yaw = y_rad or 0.0
            return adapter:sample(binding, {{
                version = 4, epoch = epoch, index = 2, generation = gen,
                sector_macro = binding.sector_macro, sequence = seq,
                source_time = t_game, capture_time = t_real,
                real_velocity = v,
                attitude = {{yaw = yaw, pitch = 0, roll = 0}},
            }})
        end

        -- 1. V4 baseline + forward +60 m/s
        assert(push_v4(1, nil, 0))
        t_real, t_game = 10.1, 100.1
        assert(push_v4(2, {{x = 0, y = 0, z = 60}}, 0))
        local r_fwd = rows[#rows]
        assert(r_fwd.kind == 'dispatched')
        assert(math.abs(r_fwd.forward_speed_mps - 60) < 1e-4)
        assert(math.abs(r_fwd.lateral_speed_mps) < 1e-4)

        -- 2. Pure lateral +60 m/s -> forward_speed_mps == 0, lateral_speed_mps == 60
        t_real, t_game = 10.2, 100.2
        pos.z = pos.z + 6.0
        assert(push_v4(3, {{x = 60, y = 0, z = 0}}, 0))
        local r_lat = rows[#rows]
        assert(math.abs(r_lat.forward_speed_mps) < 1e-4)
        assert(math.abs(r_lat.lateral_speed_mps - 60) < 1e-4)
        assert(math.abs(r_lat.total_speed_mps - 60) < 1e-4)

        -- 3. Diagonal (+60 fwd, +60 lat) -> forward == 60, lateral == 60, total == 84.8528
        t_real, t_game = 10.3, 100.3
        pos.x = pos.x + 6.0
        assert(push_v4(4, {{x = 60, y = 0, z = 60}}, 0))
        local r_diag = rows[#rows]
        assert(math.abs(r_diag.forward_speed_mps - 60) < 1e-4)
        assert(math.abs(r_diag.lateral_speed_mps - 60) < 1e-4)
        assert(math.abs(r_diag.total_speed_mps - math.sqrt(7200)) < 1e-3)

        -- 4. Reverse (-60 fwd)
        t_real, t_game = 10.4, 100.4
        assert(push_v4(5, {{x = 0, y = 0, z = -60}}, 0))
        assert(math.abs(rows[#rows].forward_speed_mps + 60) < 1e-4)

        -- 5. Speed transition frame (60 -> 90 m/s along 30 deg turn, midpoint displacement)
        t_real, t_game = 10.5, 100.5
        local y30 = math.rad(30)
        assert(push_v4(6, {{x = 60 * math.sin(y30), y = 0, z = 60 * math.cos(y30)}}, y30))
        t_real, t_game = 10.6, 100.6
        -- Ship moved 0.05s at 60 m/s + 0.05s at 90 m/s = 7.5 m along 30 deg
        pos.x = pos.x + 7.5 * math.sin(y30)
        pos.z = pos.z + 7.5 * math.cos(y30)
        assert(push_v4(7, {{x = 90 * math.sin(y30), y = 0, z = 90 * math.cos(y30)}}, y30))
        local r_trans = rows[#rows]
        assert(r_trans.speed_transition == true)
        assert(r_trans.transition_bounded == true)
        assert(r_trans.transition_segment_error_m < 1e-4)
        assert(math.abs(r_trans.forward_speed_mps - 90) < 1e-3)

        -- 6. 5-second packet gap -> anchor -> zero -> stop -> release_readback
        t_real, t_game = 15.605, 105.605
        pos.x = pos.x + 450.0 * math.sin(y30)
        pos.z = pos.z + 450.0 * math.cos(y30)
        assert(adapter:tick())
        assert(anchor_calls == 1 and rows[#rows].kind == 'zeroed' and rows[#rows].snapback_m == 0)
        t_real, t_game = 15.665, 105.665
        assert(adapter:tick())
        assert(rows[#rows].kind == 'released' and rows[#rows].snapback_m == 0)
        t_real, t_game = 15.725, 105.725
        assert(adapter:tick())
        assert(rows[#rows].kind == 'release_readback' and rows[#rows].observed_near_zero == true)
        assert(rows[#rows].anchor_drift_m < 1e-5)
        return table.concat(ops, ',')
        """
        ops = self.lua.execute(code)
        self.assertEqual(
            ops,
            'baseline,apply,apply,apply,apply,apply,apply,zero,stop,release_readback',
        )

    def test_anchor_zero_release_enforced_on_non_stale_stop_reasons(self):
        for stop_reason in ('explicit_loss', 'paused', 'view_or_roster_lost'):
            with self.subTest(stop_reason=stop_reason):
                code = f"""
                local M = assert(loadfile({str(LUA_MODULE)!r}))()
                local t_real, t_game, is_paused, view_ok = 1.0, 1.0, false, true
                local cb, ops, rows, anchors = nil, {{}}, {{}}, 0
                local epoch, gen = string.rep('c', 32), string.rep('d', 32)
                local binding = {{
                    epoch = epoch, slot = 'cockpit', serial = 1, index = 2,
                    actor = '0x55', sector_macro = 'sector_a', code = 'ABC-123', generation = gen,
                }}
                local adapter = M.new({{
                    enabled = true,
                    pinned = {{epoch = epoch, issued_count = 3}},
                    own_index = 1,
                    guard = function() return true end,
                    paused = function() return is_paused end,
                    clock = function() return t_real end,
                    game = function() return t_game end,
                    observer = {{
                        is_active_pilot = function() return view_ok end,
                        matches = function() return view_ok end,
                    }},
                    register = function(fn) cb = fn; return true end,
                    record = function(r) rows[#rows + 1] = r end,
                    anchor = function() anchors = anchors + 1; return {{x = 100, y = 0, z = 1200}} end,
                    issue = function(args)
                        ops[#ops + 1] = args.op
                        local st = ({{baseline='baseline', apply='dispatched', zero='zeroed',
                            stop='released', release_readback='release_readback'}})[args.op]
                        local vel = args.op == 'apply' and '0,0,60' or '0,0,0'
                        local extra = ''
                        if args.op == 'apply' then extra = '|0,0,60|true'
                        elseif args.op == 'zero' or args.op == 'stop' then extra = '|100,0,1200|' .. vel end
                        cb(nil, table.concat({{args.request, st, '0x55', 'sector_a', '0x7',
                            string.format('%.3f', t_game), '100,0,1200', vel,
                            '1,0,0', '0,1,0', '0,0,1', '1200'}}, '|') .. extra)
                        return true
                    end,
                }})
                assert(adapter:sample(binding, {{
                    version = 4, epoch = epoch, index = 2, generation = gen,
                    sector_macro = 'sector_a', sequence = 1, source_time = 1.0, capture_time = 1.0,
                    real_velocity = {{x = 0, y = 0, z = 60}},
                }}))
                t_real, t_game = 1.1, 1.1
                assert(adapter:tick())
                if {stop_reason!r} == 'explicit_loss' then
                    adapter:stop('explicit_loss')
                elseif {stop_reason!r} == 'paused' then
                    is_paused = true
                    t_real = 1.2
                    adapter:tick()
                else
                    view_ok = false
                    t_real = 1.2
                    adapter:tick()
                end
                t_real = 1.3
                adapter:tick()
                t_real = 1.4
                adapter:tick()
                assert(anchors == 1, 'anchor must run once before zero/stop')
                assert(rows[#rows].kind == 'release_readback' and rows[#rows].observed_near_zero)
                return table.concat(ops, ',')
                """
                ops = self.lua.execute(code)
                self.assertEqual(ops, 'baseline,apply,zero,stop,release_readback')


if __name__ == '__main__':
    unittest.main()
