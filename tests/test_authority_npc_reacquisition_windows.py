"""Offline checks for the Windows X4 NPC sector-change reacquisition harness.

These checks are synthetic: they validate the opt-in MD script structure,
LuaJIT syntax and opt-in guards, extension composition, helper utilities,
and X4NpcReentryController callback state transitions against a fake bridge.
Native X4 behaviour is only established by a real X4 9.00 run.
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
    'npc_reacq_windows', ROOT / 'tools/dev/authority_npc_reacquisition_windows.py',
)
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

EPOCH = runner.EPOCH


class FakeBridge:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def request(self, op, **params):
        self.calls.append((op, params))
        return dict(self._responses.pop(0))


class ReacquisitionMdScript(unittest.TestCase):
    def setUp(self):
        self.tree = ET.parse(runner.REACQ_XML)
        self.root = self.tree.getroot()

    def test_expected_cues_and_guard_library_exist(self):
        self.assertEqual(self.root.tag, 'mdscript')
        self.assertEqual(self.root.attrib.get('name'), 'X4AuthorityNpcReacquisition')
        cues = {el.attrib['name']: el for el in self.root.findall('./cues/cue')}
        expected = {
            'State', 'Prepare', 'Cancel', 'Align',
            'Readback', 'InspectInitial', 'StepGuard', 'Release',
        }
        self.assertEqual(set(cues), expected)
        libs = {el.attrib['name']: el for el in self.root.findall('./cues/library')}
        self.assertIn('GuardShip', libs)
        self.assertEqual(libs['GuardShip'].attrib.get('purpose'), 'include_actions')

    def test_action_cues_are_strictly_ui_triggered_opt_in(self):
        cues = {el.attrib['name']: el for el in self.root.findall('./cues/cue')}
        for name in ('Prepare', 'Cancel', 'Align', 'Readback', 'InspectInitial', 'StepGuard', 'Release'):
            cue = cues[name]
            self.assertEqual(cue.attrib.get('instantiate'), 'true')
            trigger = cue.find('./conditions/event_ui_triggered')
            self.assertIsNotNone(trigger, f'{name} must require event_ui_triggered')
            self.assertEqual(trigger.attrib.get('screen'), "'X4AuthorityNpcReacquisition'")
            self.assertEqual(trigger.attrib.get('control'), f"'{name}'")

    def test_align_warps_and_guard_excludes_player_and_docked_ships(self):
        text = runner.REACQ_XML.read_text(encoding='utf-8')
        self.assertIn('<warp object="$ship" sector="$target">', text)
        self.assertIn('AlignDispatched', text)
        self.assertIn('not md.X4AuthorityShipRegistry.State.$faulted', text)
        self.assertIn('$ship != player.controlled', text)
        self.assertIn('not $ship.dock.exists', text)
        self.assertIn('not $ship.isinternallystored', text)


class ReacquisitionLuaHarness(unittest.TestCase):
    SOURCE = runner.REACQ_LUA.read_text(encoding='utf-8')

    def test_compiles_cleanly_under_luajit_and_is_opt_in(self):
        for cfg_expr in ('nil', '{ enabled = false }', '{ enabled = "true" }'):
            with self.subTest(cfg=cfg_expr):
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.execute(f'X4NEON_NPC_REACQ = {cfg_expr}')
                lua.execute('function SetScript() error("must not register onUpdate when disabled") end')
                lua.execute('function RegisterEvent() error("must not register events when disabled") end')
                lua.execute('function _compile(src) local fn, err = loadstring(src, "@authority_npc_reacquisition_x4.lua"); return { fn = fn, err = err } end')
                compiled = lua.globals()._compile(self.SOURCE)
                self.assertIsNone(compiled.err, f'LuaJIT syntax error: {compiled.err}')
                self.assertIsNotNone(compiled.fn)
                compiled.fn()

    def test_safety_guards_and_50m_limit_in_source(self):
        self.assertIn("if type(cfg) ~= 'table' or cfg.enabled ~= true then return end", self.SOURCE)
        self.assertIn('local MAX_STEP_METRES = 50.0', self.SOURCE)
        self.assertIn('not disposable profile', self.SOURCE)
        self.assertIn('refused_over_50m', self.SOURCE)
        for forbidden in ('SaveGame', 'LoadGame', 'io.', 'os.'):
            self.assertNotIn(forbidden, self.SOURCE)


class WindowsRunnerComposeAndHelpers(unittest.TestCase):
    def test_compose_extension_writes_expected_files_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            target = runner.compose_extension(
                folder, profile=45850, gamestart='custom_creative', epoch=EPOCH,
            )
            ui = (target / 'ui.xml').read_text(encoding='utf-8')
            order = re.findall(r'<file name="([^"]+)"', ui)
            self.assertEqual(order, ['ui/config.lua', 'ui/authority_npc_reacquisition_x4.lua'])
            self.assertIn('<dependency name="ego_detailmonitor" />', ui)
            self.assertIn(f'id="{runner.EXTENSION_ID}"', (target / 'content.xml').read_text(encoding='utf-8'))
            cfg = (target / 'ui/config.lua').read_text(encoding='ascii')
            self.assertIn('enabled = true', cfg)
            self.assertIn('profile = "45850"', cfg)
            self.assertIn('gamestart = "custom_creative"', cfg)
            self.assertIn(f'epoch = "{EPOCH}"', cfg)
            self.assertEqual((target / 'md/authority_ship_registry.xml').read_bytes(), runner.REGISTRY_XML.read_bytes())
            self.assertEqual((target / 'md/authority_npc_reacquisition.xml').read_bytes(), runner.REACQ_XML.read_bytes())
            self.assertEqual((target / 'ui/authority_npc_reacquisition_x4.lua').read_bytes(), runner.REACQ_LUA.read_bytes())
            with self.assertRaises(FileExistsError):
                runner.compose_extension(folder, profile=45850, gamestart='custom_creative', epoch=EPOCH)

    def test_invalid_gamestart_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(SystemExit):
            runner.compose_extension(
                folder, profile=45850, gamestart='custom"); os.exit() --', epoch=EPOCH,
            )

    def test_parse_kv_and_tree_metadata(self):
        self.assertEqual(
            runner.parse_kv('seq=5 status=aligned sector_macro=cluster_01_sector001_macro bare_token'),
            {'seq': '5', 'status': 'aligned', 'sector_macro': 'cluster_01_sector001_macro'},
        )
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / 'missing'
            self.assertEqual(runner.tree_metadata(missing), {})
            (Path(folder) / 'sub').mkdir()
            (Path(folder) / 'sub' / 'file.txt').write_text('abc', encoding='ascii')
            meta = runner.tree_metadata(folder)
            self.assertEqual(list(meta), [str(Path('sub') / 'file.txt')])
            self.assertEqual(meta[str(Path('sub') / 'file.txt')][0], 3)


class X4NpcReentryControllerTests(unittest.TestCase):
    def _sample(self, seq, sector, x=100.0, y=200.0, z=300.0, vel=None, index=1, epoch=EPOCH):
        return {
            'index': index,
            'epoch': epoch,
            'sequence': seq,
            'sector_macro': sector,
            'x': x,
            'y': y,
            'z': z,
            'real_velocity': vel,
        }

    def test_prepare_cancel_reprepare_align_and_post_align_step(self):
        bridge = FakeBridge([
            {'seq': '1', 'status': 'bound', 'sector_macro': 'sec_a'},
            {'seq': '2', 'status': 'prepared', 'sector_macro': 'sec_a', 'target_sector_key': '1902'},
            {'seq': '3', 'status': 'canceled', 'sector_macro': 'sec_a'},
            {'seq': '4', 'status': 'prepared', 'sector_macro': 'sec_a', 'target_sector_key': '1902'},
            {'seq': '5', 'status': 'aligned', 'sector_macro': 'sec_b'},
            {'seq': '6', 'status': 'step_applied', 'sector_macro': 'sec_b', 'step_dist': '12.0000'},
            {'seq': '7', 'status': 'refused_over_50m', 'dist': '118.0000'},
        ])
        ctrl = runner.X4NpcReentryController(bridge, expected_index=1, expected_epoch=EPOCH)

        # Foreign index / epoch / despawn are ignored without touching bridge.
        self.assertFalse(ctrl.apply_motion(self._sample(1, 'sec_a'), True))
        self.assertFalse(ctrl.apply_motion(self._sample(1, 'sec_a', index=99), False))
        self.assertFalse(ctrl.apply_motion(self._sample(1, 'sec_a', epoch='0' * 32), False))
        # Prepare rejects non-null real_velocity (must be stripped before prepare).
        self.assertFalse(ctrl.prepare('sec_a', self._sample(2, 'sec_b', vel={'x': 1, 'y': 0, 'z': 0})))
        # Cancel / align refuse when nothing is prepared.
        self.assertFalse(ctrl.cancel('sec_a', self._sample(2, 'sec_b')))
        self.assertFalse(ctrl.align('sec_a', self._sample(2, 'sec_b'), self._sample(3, 'sec_b')))
        self.assertEqual(bridge.calls, [])

        # Initial bind
        self.assertTrue(ctrl.apply_motion(self._sample(1, 'sec_a'), False))
        # Prepare -> Cancel
        prep1 = self._sample(3, 'sec_b', vel=None)
        self.assertTrue(ctrl.prepare('sec_a', prep1))
        self.assertTrue(ctrl.cancel('sec_a', prep1))
        self.assertFalse(ctrl.cancel('sec_a', prep1))
        # Re-prepare -> Align with fresh sample velocity
        prep2 = self._sample(5, 'sec_b', vel=None)
        self.assertTrue(ctrl.prepare('sec_a', prep2))
        fresh = self._sample(6, 'sec_b', x=15000.0, y=2500.0, z=-4000.0, vel={'x': 20.0, 'y': 0.0, 'z': 0.0})
        self.assertTrue(ctrl.align('sec_a', prep2, fresh))
        # Post-align normal snapshot within 50m succeeds; over-50m refuses cleanly
        step_ok = self._sample(7, 'sec_b', x=15012.0, y=2500.0, z=-4000.0, vel={'x': 20.0, 'y': 0.0, 'z': 0.0})
        self.assertTrue(ctrl.apply_motion(step_ok, False))
        step_far = self._sample(8, 'sec_b', x=15130.0, y=2500.0, z=-4000.0, vel={'x': 20.0, 'y': 0.0, 'z': 0.0})
        self.assertFalse(ctrl.apply_motion(step_far, False))

        self.assertEqual(
            [op for op, _ in bridge.calls],
            ['bind_initial', 'prepare', 'cancel', 'prepare', 'align', 'apply_step', 'apply_step'],
        )
        self.assertEqual(bridge.calls[2][1]['prepared_request'], 'req-2')
        self.assertEqual(bridge.calls[4][1]['prepared_request'], 'req-4')
        self.assertEqual(bridge.calls[4][1]['vx'], 20.0)


if __name__ == '__main__':
    unittest.main()
