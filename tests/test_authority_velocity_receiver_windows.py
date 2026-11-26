"""Offline checks for the native Windows velocity receiver runner and its trace hook."""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tools' / 'dev')]

import authority_velocity_receiver_windows as runner  # noqa: E402

try:
    from lupa import luajit21
except ImportError:  # pragma: no cover
    luajit21 = None

FINAL = {'refusals_50m': '0', 'own_disp_m': '0.0000'}


class VelocityReceiverRunnerTests(unittest.TestCase):
    def test_trajectory_must_increase_and_accepts_list_velocity(self):
        rows = [dict(sequence=143, source_time=1.0, capture_time=2.0, x=0, y=0, z=0, real_velocity=[1, 2, 3]),
                dict(sequence=144, source_time=1.1, capture_time=2.1, x=1, y=0, z=0, real_velocity=None)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'samples.json'
            path.write_text(json.dumps({'samples': rows}), encoding='utf-8')
            samples = runner.load_trajectory(path)
            self.assertEqual(samples[0]['real_velocity'], {'x': 1.0, 'y': 2.0, 'z': 3.0})
            rows[1]['capture_time'] = 2.0
            path.write_text(json.dumps(rows), encoding='utf-8')
            with self.assertRaises(ValueError):
                runner.load_trajectory(path)

    def test_placement_starts_on_the_remote_preserves_steps_and_clears_the_own_ship(self):
        samples = runner.synthetic_trajectory()
        start = {'x': 1000.0, 'y': 50.0, 'z': -400.0}
        own = {'x': 1000.0 + 600.0, 'y': 50.0, 'z': -400.0}  # straight down the unflipped path
        placed, info = runner.place(samples, start, own)
        self.assertEqual([placed[0][a] for a in 'xyz'], [start[a] for a in 'xyz'])
        self.assertGreaterEqual(info['nearest_own_m'], runner.CLEARANCE)

        def steps(rows):
            return [math.dist([a[k] for k in 'xyz'], [b[k] for k in 'xyz']) for a, b in zip(rows, rows[1:])]
        for before, after in zip(steps(samples), steps(placed)):
            self.assertAlmostEqual(before, after, places=6)
        flip = dict(zip('xyz', info['flip']))
        self.assertEqual(placed[5]['real_velocity'], {a: flip[a] * samples[5]['real_velocity'][a] for a in 'xyz'})

    def test_schedule_keeps_capture_spacing_and_uses_the_receiver_sector(self):
        samples = runner.synthetic_trajectory()
        rows = runner.schedule(samples, 'e' * 32, '1' * 32, 'cluster_x_sector001_macro')
        self.assertEqual(rows[0][0], 0.0)
        self.assertAlmostEqual(rows[-1][0], samples[-1]['capture_time'] - samples[0]['capture_time'])
        self.assertEqual({row['sector_macro'] for _, row in rows}, {'cluster_x_sector001_macro'})
        self.assertEqual([row['sequence'] for _, row in rows], [s['sequence'] for s in samples])

    def test_evaluate_reports_steps_over_50m_and_rejects_refusals(self):
        events = [
            'TRACE BIND index=2 actor=0x20 code=ABC-002 sector=s slot=cockpit velocity_steps=true t=1.0',
            'TRACE OBS seq=143 phase=extrapolated t=1.00 step=10.0 actual=0,0,0 target=10,0,0',
            'TRACE WRITE ok=true t=1.00 step=10.0000 recheck=false target=10,0,0',
            'TRACE OBS seq=144 phase=interpolated t=1.05 step=76.2 actual=10,0,0 target=86.2,0,0',
            'TRACE READBACK seq=143 t=1.05 elapsed=0.05 error=0.0000 ffi_vs_actual=0.0000 ffi_error=0.0 ffi=10,0,0',
            'TRACE WRITE ok=true t=1.05 step=76.2000 recheck=true target=86.2,0,0',
            'TRACE READBACK seq=144 t=1.10 elapsed=0.05 error=0.0010 ffi_vs_actual=0.0000 ffi_error=0.0 ffi=86,0,0',
        ]
        report = runner.evaluate(events, FINAL, [{}] * 2)
        self.assertTrue(report['accepted'], report)
        self.assertEqual(report['steps_over_50m'], [{'t': 1.05, 'step_m': 76.2, 'ok': 'true',
                                                      'velocity_authorized': 'true'}])
        refused = events + ['PLAYBACK_EVENT kind=refused_over_50m index=2 reason=correction_out_of_bounds']
        self.assertFalse(runner.evaluate(refused, FINAL, [])['accepted'])
        self.assertFalse(runner.evaluate(events + events[:1], FINAL, [])['accepted'])

    def test_only_the_baseline_before_a_later_bind_sample_may_be_refused(self):
        baseline = ['PLAYBACK_EVENT kind=refused_over_50m dist=170.688 index=2 max_step=50 slot=cockpit stage=reacquire']
        events = baseline + [
            'TRACE BIND index=2 actor=0x20 code=ABC-002 sector=s slot=cockpit velocity_steps=true t=1.0',
            'TRACE OBS seq=144 phase=extrapolated t=1.00 step=1.0 actual=0,0,0 target=1,0,0',
            'TRACE WRITE ok=true t=1.00 step=1.0000 recheck=false target=1,0,0',
            'TRACE READBACK seq=144 t=1.02 elapsed=0.02 error=0.0000 ffi_vs_actual=0.0000 ffi_error=0.0 ffi=1,0,0',
        ]
        one = dict(FINAL, refusals_50m='1')
        report = runner.evaluate(events, one, [], bind_sample=1)
        self.assertTrue(report['accepted'], report)
        self.assertEqual(report['pre_bind_refusals'], baseline)
        self.assertFalse(runner.evaluate(events, one, [], bind_sample=0)['accepted'])
        self.assertFalse(runner.evaluate(baseline * 2 + events[1:], dict(FINAL, refusals_50m='2'), [],
                                         bind_sample=1)['accepted'])

    def test_extension_loads_the_trace_last_with_the_supplied_module(self):
        class Args:
            profile, gamestart = 45856, 'custom_creative'
        with tempfile.TemporaryDirectory() as tmp:
            view = Path(tmp)
            (view / 'extensions').mkdir()
            ext = runner.compose(view, Args, {'port': 47000, 'lease': 'd' * 32, 'generation': 'e' * 32},
                                 b'dll monotonic')
            self.assertEqual((ext / 'native' / 'x4lan_net_64.dll').read_bytes(), b'dll monotonic')
            ui = (ext / 'ui.xml').read_text(encoding='utf-8')
            self.assertGreater(ui.index('ui/velocity_trace.lua'), ui.index('ui/authority_company_cockpit_x4.lua'))
            self.assertIn('velocity_trace = true', (ext / 'ui' / 'config.lua').read_text(encoding='ascii'))


@unittest.skipIf(luajit21 is None, 'lupa.luajit21 required')
class VelocityTraceLuaTests(unittest.TestCase):
    def test_trace_wraps_real_cockpit_playback_at_20hz_without_changing_results(self):
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        lua.globals()._ROOT = str(ROOT).replace('\\', '/')
        samples = runner.synthetic_trajectory()
        lua.globals()._SAMPLES = lua.table_from([lua.table_from(
            {'sequence': s['sequence'], 'capture_time': s['capture_time'], 'x': s['x'], 'y': s['y'], 'z': s['z'],
             'vx': s['real_velocity']['x'], 'vz': s['real_velocity']['z']}) for s in samples])
        lines = lua.execute(r"""
            local root = _ROOT .. '/src/authority/lua/'
            X4LAN_MOTION_BUFFER = dofile(root .. 'motion_buffer.lua')
            dofile(root .. 'motion_playback.lua')
            dofile(root .. 'company_hull_playback.lua')
            local module = dofile(root .. 'company_cockpit_playback.lua')
            local lines, pose = {}, {x = 0, y = 0, z = 0}
            DebugError = function(text) lines[#lines + 1] = text end
            X4NEON_COMPANY_COCKPIT = {enabled = true, velocity_trace = true,
                trace_occupied = function() return 7 end,
                trace_position = function() return {x = pose.x, y = pose.y, z = pose.z} end}
            dofile(_ROOT .. '/tools/dev/authority_velocity_receiver_trace.lua')

            local now, writes = 10, 0
            local binding = {epoch = ('a'):rep(32), sector_macro = 'sector_a', index = 2,
                             actor = '0x20', code = 'ABC-002'}
            local adapter = {bind = function()
                return {
                    guard = function() return true end,
                    read = function() return {x = pose.x, y = pose.y, z = pose.z} end,
                    write = function(_, target)
                        pose = {x = target.x, y = target.y, z = target.z}; return true
                    end,
                    align = function() return false end,
                    close = function() end,
                }
            end}
            local ctrl = module.new({
                enabled = true, own_index = 1, adapter = adapter,
                clock = function() return now end, game = function() return now end,
                paused = function() return false end,
                fault = function(reason) error('fault ' .. tostring(reason)) end,
                record = function(event)
                    if event.kind == 'readback' then assert(event.error < 1e-3) end
                    return true
                end,
            })
            local start, index = now, 1
            while index <= #_SAMPLES do
                local s = _SAMPLES[index]
                if start + s.capture_time <= now then
                    local ok, reason = ctrl:sample(binding, 1, {
                        version = 3, epoch = binding.epoch, generation = ('b'):rep(32),
                        sector_macro = binding.sector_macro, index = 2, sequence = s.sequence,
                        x = s.x, y = s.y, z = s.z, source_time = 100 + s.capture_time,
                        capture_time = 500 + s.capture_time, real_velocity = {x = s.vx, y = 0, z = s.vz},
                    }, now)
                    assert(ok, tostring(reason))
                    index = index + 1
                else
                    assert(ctrl:tick(now) ~= false)
                    now = now + 0.05
                end
            end
            for _ = 1, 3 do now = now + 0.05; ctrl:tick(now) end
            ctrl:close()
            return lines
        """)
        prefix = '[X4LAN] AUTH_COMPANY_COCKPIT '
        events = [lines[i][len(prefix):] for i in range(1, len(lines) + 1)]
        self.assertTrue(all(lines[i].startswith(prefix) for i in range(1, len(lines) + 1)))
        report = runner.evaluate(events, FINAL, samples)
        self.assertTrue(report['accepted'], report)
        self.assertEqual(report['binds'], 1)
        self.assertGreater(report['writes'], 40)
        # 20 Hz frames at 1.5 km/s exceed 50 m; each is velocity authorized and within the 250 m limit.
        self.assertTrue(report['steps_over_50m'])
        self.assertTrue(all(s['velocity_authorized'] == 'true' and s['step_m'] <= 250
                            for s in report['steps_over_50m']))


if __name__ == '__main__':
    unittest.main()
