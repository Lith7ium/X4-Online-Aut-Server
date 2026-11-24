"""Offline LuaJIT 2.1 verification for src/authority/lua/npc_hull_playback.lua.

Exercises the real motion_buffer.lua + motion_playback.lua + npc_hull_playback.lua
stack with synthetic clocks and hull callbacks (not native X4):
  1. Default-off opt-in enforcement and binding validation.
  2. Ordinary V3 capture trajectory playback + delayed later-update readbacks (elapsed > 0).
  3. Transport loss keeping 5.0 s local prediction alive, then freezing cleanly at 5.0 s
     with zero further writes after the cutoff.
  4. Offscreen visibility withdrawal stopping playback immediately and discarding offscreen samples.
  5. Active >50 m jump strictly refused; post-loss/withdrawal reacquisition allowing direct
     resumption when <=50 m OR strictly ONE authorized alignment (confirmed by later readback)
     when >50 m before resuming ordinary <=50 m continuous playback.
  6. Unseen sector change while withdrawn and multi-NPC pool concurrent ticking.
"""
from pathlib import Path
import unittest

try:
    from lupa import luajit21
except ImportError:  # pragma: no cover
    luajit21 = None

ROOT = Path(__file__).resolve().parents[1]
MOTION_BUFFER_LUA = ROOT / 'src/authority/lua/motion_buffer.lua'
MOTION_PLAYBACK_LUA = ROOT / 'src/authority/lua/motion_playback.lua'
NPC_PLAYBACK_LUA = ROOT / 'src/authority/lua/npc_hull_playback.lua'


def _load_npc_playback_runtime():
    lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
    buf_mod = lua.execute(MOTION_BUFFER_LUA.read_text(encoding='utf-8'))
    lua.globals().X4LAN_MOTION_BUFFER = buf_mod
    lua.execute(MOTION_PLAYBACK_LUA.read_text(encoding='utf-8'))
    lua.execute(NPC_PLAYBACK_LUA.read_text(encoding='utf-8'))
    return lua


@unittest.skipIf(luajit21 is None, 'lupa.luajit21 required')
class TestNpcHullPlaybackLua(unittest.TestCase):

    def test_opt_in_and_binding_validation(self):
        lua = _load_npc_playback_runtime()
        res = lua.execute("""
            local M = X4LAN_NPC_HULL_PLAYBACK
            local c1, e1 = M.new({})
            assert(c1 == nil and e1 == 'disabled')
            local noop = function() return true end
            local c2, e2 = M.new({
                enabled = true, own_index = 1970,
                binding = {epoch = string.rep('a', 32), index = 1970, sector_macro = 'sec_a', actor = '123', code = 'ABC-123'},
                clock = function() return 1 end, game = function() return 1 end, paused = function() return false end,
                guard = noop, read = function() return {x=0,y=0,z=0} end, write = noop, record = noop, fault = noop,
            })
            assert(c2 == nil and e2 == 'invalid_npc_binding')
            return true
        """)
        self.assertTrue(res)

    def test_capture_playback_loss_5s_cutoff_withdrawal_and_aligned_reacquisition(self):
        lua = _load_npc_playback_runtime()
        summary = lua.execute("""
            local M = X4LAN_NPC_HULL_PLAYBACK
            local EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
            local GEN = string.rep('1', 32)
            local now = 10.0
            local gt = 5.0
            local hull = {x = 0.0, y = 0.0, z = 0.0}
            local records, events, faults = {}, {}, {}

            local ctrl = assert(M.new({
                enabled = true,
                own_index = 1970,
                allow_reacquire_align = true,
                binding = {epoch = EPOCH, index = 7417, sector_macro = 'cluster_14_sector001_macro', actor = '478760', code = 'QNQ-601'},
                clock = function() return now end,
                game = function() return gt end,
                paused = function() return false end,
                guard = function() return true end,
                read = function() return {x = hull.x, y = hull.y, z = hull.z} end,
                write = function(_, target, _)
                    hull.x, hull.y, hull.z = target.x, target.y, target.z
                    return true
                end,
                align = function(_, target, _)
                    hull.x, hull.y, hull.z = target.x, target.y, target.z
                    return true
                end,
                record = function(ev)
                    records[#records + 1] = ev
                    return true
                end,
                fault = function(reason, _, stage)
                    faults[#faults + 1] = tostring(reason) .. ':' .. tostring(stage)
                end,
                on_event = function(kind, _, detail)
                    events[#events + 1] = kind
                end,
            }))

            local function step(dt)
                now = now + dt
                gt = gt + dt
                ctrl:tick()
            end

            -- 1. Ordinary V3 capture trajectory at 20 m/s over 1.0 s (seq 1..6)
            local ok1, r1 = ctrl:push({
                version = 3, epoch = EPOCH, generation = GEN, index = 7417, sequence = 1,
                sector_macro = 'cluster_14_sector001_macro', source_time = 5.0, capture_time = 10.0,
                x = 0.0, y = 0.0, z = 0.0, real_velocity = nil,
            }, now)
            assert(ok1 and r1 == 'baseline_recorded')

            for seq = 2, 6 do
                step(0.2)
                local px = (seq - 1) * 4.0
                local ok, r = ctrl:push({
                    version = 3, epoch = EPOCH, generation = GEN, index = 7417, sequence = seq,
                    sector_macro = 'cluster_14_sector001_macro', source_time = gt, capture_time = now,
                    x = px, y = 0.0, z = 0.0, real_velocity = {x = 20.0, y = 0.0, z = 0.0},
                }, now)
                assert(ok and r == 'accepted')
                step(0.05)
            end
            local active_writes = ctrl:stats().writes
            local active_readbacks = ctrl:stats().readbacks
            assert(active_writes > 0 and active_readbacks > 0)

            -- 2. Explicit >50 m guard probe strictly refused without moving hull
            local x_before_probe = hull.x
            local pok, preason = ctrl:probe_step_guard({x = hull.x + 120.0, y = 0.0, z = 0.0})
            assert(pok == false and preason == 'correction_out_of_bounds' and hull.x == x_before_probe)

            -- 3. Sample loss (transport_lost): continues extrapolating up to 5.0 s, then stops dead!
            ctrl:transport_lost('transport_lost')
            local loss_start = now
            local x_at_1s, x_at_4_5s, x_at_5_3s, x_at_6_0s
            local writes_at_5_3s, writes_at_6_0s
            for _ = 1, 65 do
                step(0.1)
                local e = now - loss_start
                if not x_at_1s and e >= 1.0 then x_at_1s = hull.x end
                if not x_at_4_5s and e >= 4.5 then x_at_4_5s = hull.x end
                if not x_at_5_3s and e >= 5.3 then x_at_5_3s = hull.x; writes_at_5_3s = ctrl:stats().writes end
                if not x_at_6_0s and e >= 6.0 then x_at_6_0s = hull.x; writes_at_6_0s = ctrl:stats().writes end
            end
            assert(x_at_4_5s > x_at_1s + 50.0, 'expected extrapolation during 5s window')
            assert(ctrl:state() == 'stale', 'expected stale state after 5s cutoff, got ' .. tostring(ctrl:state()))
            assert(x_at_6_0s == x_at_5_3s and writes_at_6_0s == writes_at_5_3s, 'hull moved after 5s cutoff!')

            -- 4. Offscreen visibility withdrawal discards offscreen samples
            ctrl:withdraw('offscreen')
            local dok, dreason = ctrl:push({
                version = 3, epoch = EPOCH, generation = GEN, index = 7417, sequence = 10,
                sector_macro = 'cluster_14_sector001_macro', source_time = gt, capture_time = now,
                x = hull.x + 80.0, y = 0.0, z = 0.0, real_velocity = {x = 20.0, y = 0.0, z = 0.0},
            }, now)
            assert(dok == false and dreason == 'discarded_offscreen')

            -- 5. Reacquisition after >50 m drift: ONE authorized alignment -> later readback -> ordinary <=50 m playback!
            assert(ctrl:reacquire_view({
                epoch = EPOCH, index = 7417, sector_macro = 'cluster_14_sector001_macro', actor = '478760', code = 'QNQ-601',
            }))
            local align_x = hull.x + 110.0
            local aok, areason = ctrl:push({
                version = 3, epoch = EPOCH, generation = GEN, index = 7417, sequence = 20,
                sector_macro = 'cluster_14_sector001_macro', source_time = gt, capture_time = now,
                x = align_x, y = 0.0, z = 0.0, real_velocity = nil,
            }, now)
            assert(aok and areason == 'aligned', 'expected single authorized alignment, got ' .. tostring(areason))
            assert(ctrl:state() == 'align_readback')
            step(0.05)  -- Later tick settles independent alignment_readback!
            assert(ctrl:state() == 'waiting')

            -- Attempting a second >50 m jump without a new loss/withdrawal must be REFUSED!
            local rok, rreason = ctrl:push({
                version = 3, epoch = EPOCH, generation = GEN, index = 7417, sequence = 21,
                sector_macro = 'cluster_14_sector001_macro', source_time = gt + 0.1, capture_time = now + 0.1,
                x = align_x + 90.0, y = 0.0, z = 0.0, real_velocity = {x = 20.0, y = 0.0, z = 0.0},
            }, now)
            assert(rok == false and rreason == 'correction_out_of_bounds', 'second unaligned jump should fail')

            -- Ordinary <=50 m playback resumes cleanly from aligned baseline!
            for k = 1, 5 do
                step(0.2)
                local px = align_x + k * 4.0
                local ok, r = ctrl:push({
                    version = 3, epoch = EPOCH, generation = GEN, index = 7417, sequence = 21 + k,
                    sector_macro = 'cluster_14_sector001_macro', source_time = gt, capture_time = now,
                    x = px, y = 0.0, z = 0.0, real_velocity = {x = 20.0, y = 0.0, z = 0.0},
                }, now)
                assert(ok and r == 'accepted', 'ordinary resumed push failed: ' .. tostring(r))
                step(0.05)
            end
            assert(hull.x > align_x + 12.0)

            local st = ctrl:stats()
            return {
                writes = st.writes,
                readbacks = st.readbacks,
                alignments = st.alignments,
                withdrawals = st.withdrawals,
                reacquisitions = st.reacquisitions,
                stale_cutoffs = st.stale_cutoffs,
                refusals_over_50m = st.refusals_over_50m,
                discarded_offscreen = st.discarded_offscreen,
                final_x = hull.x,
                fault_count = #faults,
            }
        """)
        self.assertGreater(summary['writes'], 10)
        self.assertGreater(summary['readbacks'], 10)
        self.assertEqual(summary['alignments'], 1)
        self.assertEqual(summary['withdrawals'], 1)
        self.assertEqual(summary['reacquisitions'], 1)
        self.assertEqual(summary['stale_cutoffs'], 1)
        self.assertEqual(summary['refusals_over_50m'], 2)
        self.assertEqual(summary['discarded_offscreen'], 1)
        self.assertEqual(summary['fault_count'], 0)

    def test_unseen_sector_change_and_multi_npc_pool(self):
        lua = _load_npc_playback_runtime()
        ok = lua.execute("""
            local M = X4LAN_NPC_HULL_PLAYBACK
            local EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
            local now = 1.0
            local gt = 1.0
            local poses = {[100] = {x=0,y=0,z=0}, [200] = {x=10,y=0,z=0}}
            local pool = assert(M.new_pool({
                enabled = true, max_tracks = 8, clock = function() return now end,
                make_controller = function(binding)
                    return M.new({
                        enabled = true, own_index = 1970, binding = binding, allow_reacquire_align = true,
                        clock = function() return now end, game = function() return gt end,
                        paused = function() return false end, guard = function() return true end,
                        read = function(b) local p = poses[b.index]; return {x=p.x, y=p.y, z=p.z} end,
                        write = function(b, t) poses[b.index] = {x=t.x, y=t.y, z=t.z}; return true end,
                        align = function(b, t) poses[b.index] = {x=t.x, y=t.y, z=t.z}; return true end,
                        record = function() return true end, fault = function() end,
                    })
                end,
            }))
            local c1 = assert(pool:ensure({epoch=EPOCH, index=100, sector_macro='sec_a', actor='1001', code='AAA-100'}))
            local c2 = assert(pool:ensure({epoch=EPOCH, index=200, sector_macro='sec_b', actor='2002', code='BBB-200'}))
            -- Push moving samples to both tracks concurrently
            for seq = 1, 3 do
                now = now + 0.2; gt = gt + 0.2
                assert(c1:push({version=3, epoch=EPOCH, generation=string.rep('a',32), index=100, sequence=seq,
                                sector_macro='sec_a', source_time=gt, capture_time=now,
                                x=seq*2.0, y=0, z=0, real_velocity={x=10,y=0,z=0}}, now))
                assert(c2:push({version=3, epoch=EPOCH, generation=string.rep('b',32), index=200, sequence=seq,
                                sector_macro='sec_b', source_time=gt, capture_time=now,
                                x=10+seq*2.0, y=0, z=0, real_velocity={x=10,y=0,z=0}}, now))
                now = now + 0.05; gt = gt + 0.05
                pool:tick_all()
            end
            -- Unseen sector change on track 100 while withdrawn: old sector sample discarded, new sector rebinds!
            c1:withdraw('offscreen')
            assert(c1:reacquire_view({epoch=EPOCH, index=100, sector_macro='sec_c', actor='1001', code='AAA-100'}))
            local old_ok, old_err = c1:push({version=3, epoch=EPOCH, generation=string.rep('a',32), index=100, sequence=4,
                sector_macro='sec_a', source_time=gt+0.1, capture_time=now+0.1, x=8, y=0, z=0, real_velocity=nil}, now)
            assert(old_ok == false and old_err == 'sector_mismatch')
            now = now + 0.2; gt = gt + 0.2
            local new_ok = c1:push({version=3, epoch=EPOCH, generation=string.rep('a',32), index=100, sequence=5,
                sector_macro='sec_c', source_time=gt, capture_time=now, x=8, y=0, z=0, real_velocity=nil}, now)
            assert(new_ok == true)
            local st = pool:stats()
            assert(st.track_count == 2 and st.total_ticks >= 3)
            pool:stop_all()
            return true
        """)
        self.assertTrue(ok)


if __name__ == '__main__':
    unittest.main()
