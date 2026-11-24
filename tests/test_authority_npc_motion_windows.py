"""Offline synthetic checks for the Windows X4 NPC motion harness and helpers.

Validates opt-in MD XML structure (including ResolveSavedShip for cold reload),
LuaJIT syntax & opt-in guards, both capture and observe extension composition,
finite-difference velocity calculation, and trace conversion without launching X4.
"""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'npc_motion_windows', ROOT / 'tools/dev/authority_npc_motion_windows.py',
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


class NpcMotionMdScriptTests(unittest.TestCase):
    def setUp(self):
        self.root = ET.parse(runner.MOTION_XML).getroot()

    def test_cues_and_guard_library_exist(self):
        self.assertEqual(self.root.tag, 'mdscript')
        self.assertEqual(self.root.attrib.get('name'), 'X4AuthorityNpcMotion')
        cues = {el.attrib['name']: el for el in self.root.findall('./cues/cue')}
        self.assertEqual(
            set(cues),
            {'State', 'PopulateUniverse', 'ResolveSavedShip', 'PreparePlayback', 'Readback', 'Release'},
        )
        for name in ('PopulateUniverse', 'ResolveSavedShip', 'PreparePlayback', 'Readback', 'Release'):
            trigger = cues[name].find('./conditions/event_ui_triggered')
            self.assertIsNotNone(trigger)
            self.assertEqual(trigger.attrib.get('screen'), "'X4AuthorityNpcMotion'")
            self.assertEqual(trigger.attrib.get('control'), f"'{name}'")
        libs = {el.attrib['name']: el for el in self.root.findall('./cues/library')}
        self.assertEqual(libs['GuardShip'].attrib.get('purpose'), 'include_actions')


class NpcMotionLuaHarnessTests(unittest.TestCase):
    SOURCE = runner.MOTION_LUA.read_text(encoding='utf-8')

    def test_compiles_cleanly_under_luajit_and_is_opt_in(self):
        for cfg_expr in ('nil', '{ enabled = false }', '{ enabled = "true" }'):
            with self.subTest(cfg=cfg_expr):
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.execute(f'X4NEON_NPC_MOTION = {cfg_expr}')
                lua.execute('function SetScript() error("must not register onUpdate when disabled") end')
                lua.execute('function RegisterEvent() error("must not register events when disabled") end')
                lua.execute('function _compile(src) local fn, err = loadstring(src, "@authority_npc_motion_x4.lua"); return { fn = fn, err = err } end')
                compiled = lua.globals()._compile(self.SOURCE)
                self.assertIsNone(compiled.err, f'LuaJIT syntax error: {compiled.err}')
                self.assertIsNotNone(compiled.fn)
                compiled.fn()

    def test_safety_guards_and_disposable_profile_checks_in_source(self):
        self.assertIn("if type(cfg) ~= 'table' or cfg.enabled ~= true then return end", self.SOURCE)
        self.assertIn('not disposable profile', self.SOURCE)
        self.assertIn('X4LAN_NPC_HULL_PLAYBACK', self.SOURCE)
        self.assertIn('GetCurRealTime', self.SOURCE)
        for forbidden in (r'\bio\.', r'\bos\.'):
            self.assertIsNone(re.search(forbidden, self.SOURCE))


class NpcMotionRunnerHelpersTests(unittest.TestCase):
    def test_compose_extension_capture_and_observe_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            cap = runner.compose_extension(
                folder, profile=45850, gamestart='custom_creative', epoch=runner.EPOCH, mode='capture',
            )
            ui_cap = (cap / 'ui.xml').read_text(encoding='utf-8')
            self.assertEqual(
                re.findall(r'<file name="([^"]+)"', ui_cap),
                ['ui/config.lua', 'ui/authority_npc_motion_x4.lua'],
            )
            self.assertEqual((cap / 'md/authority_npc_motion.xml').read_bytes(), runner.MOTION_XML.read_bytes())

            obs = runner.compose_extension(
                folder, profile=45850, gamestart='custom_creative', epoch=runner.EPOCH, mode='observe',
                extra={'primary_index': 7417, 'secondary_index': 3915, 'own_index': 199001,
                       'udp_port': 45000, 'udp_lease': 'a' * 32},
            )
            ui_obs = (obs / 'ui.xml').read_text(encoding='utf-8')
            self.assertEqual(
                re.findall(r'<file name="([^"]+)"', ui_obs),
                ['ui/config.lua', 'ui/motion_buffer.lua', 'ui/motion_playback.lua',
                 'ui/npc_hull_playback.lua', 'ui/authority_npc_motion_x4.lua'],
            )
            self.assertTrue((obs / 'native/x4lan_net_64.dll').is_file())
            self.assertIn('X4LAN_MOTION_BUFFER = M', (obs / 'ui/motion_buffer.lua').read_text(encoding='utf-8'))

    def test_compute_v3_sample_and_build_trace(self):
        p1 = {'game_time': '10.0', 'real_time': '40.0', 'sector_macro': 'sec_a', 'x': '100', 'y': '0', 'z': '0'}
        p2 = {'game_time': '10.5', 'real_time': '40.5', 'sector_macro': 'sec_a', 'x': '120', 'y': '5', 'z': '-10'}
        p3 = {'game_time': '11.0', 'real_time': '41.0', 'sector_macro': 'sec_b', 'x': '500', 'y': '0', 'z': '0'}
        s1 = runner.compute_v3_sample(None, p1, index=1318, generation='1' * 32, sequence=1)
        self.assertIsNone(s1['real_velocity'])
        s2 = runner.compute_v3_sample(p1, p2, index=1318, generation='1' * 32, sequence=2)
        self.assertEqual(s2['real_velocity'], {'x': 40.0, 'y': 10.0, 'z': -20.0})
        s3 = runner.compute_v3_sample(p2, p3, index=1318, generation='1' * 32, sequence=3)
        self.assertIsNone(s3['real_velocity'])
        s2_reset = runner.compute_v3_sample(p1, p2, index=1318, generation='1' * 32, sequence=2, reset_baseline=True)
        self.assertIsNone(s2_reset['real_velocity'])

        trace = runner.build_trace_v3_samples([p1, p2], index=1318, generation='1' * 32)
        self.assertEqual(len(trace), 2)
        self.assertIsNone(trace[0]['real_velocity'])
        self.assertEqual(trace[1]['real_velocity'], {'x': 40.0, 'y': 10.0, 'z': -20.0})


if __name__ == '__main__':
    unittest.main()
