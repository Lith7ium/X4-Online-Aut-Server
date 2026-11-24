"""Offline checks for the map visibility probe, its Windows runner and its evidence table.

These are synthetic: they check parsing, the evidence rules, the composed extension and the PNG
writer. Native map behaviour is only established by a real X4 run.
"""
import importlib.util
import re
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'dev'))
import authority_map_visibility_evidence as evidence  # noqa: E402

_spec = importlib.util.spec_from_file_location('mapvis_windows', ROOT / 'tools/dev/authority_map_visibility_windows.py')
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

NPC, PLAYER = '18446744073709551557', '379304'


def camera(prefix, x, distance=20000):
    return (f'{prefix}_x={x} {prefix}_y=0 {prefix}_z=5 {prefix}_yaw=0 {prefix}_pitch=0.5 {prefix}_roll=0 '
            f'{prefix}_distance={distance}')


def view_rows(name, listed, x, reached=True, seq=1, control='set_map_state', target_x=None, ids=None):
    target_x = x if target_x is None else target_x
    ids = ids if ids is not None else ([PLAYER, NPC] if listed else [PLAYER])
    rows = []
    for phase in ('before_shot', 'after_shot'):
        rows.append(f'[X4LAN] AUTH_MAPVIS VIEW view={name} phase={phase} npc={NPC} npc_listed={str(listed).lower()} '
                    f'player={PLAYER} player_listed=true raw_count={len(ids)} focus={PLAYER} selected=0 paused=true '
                    f'game_time=12.5 npc_known=true npc_radar=true npc_player_m=2500.25 reached={str(reached).lower()} '
                    f'control={control} {camera("camera", x)} {camera("target", target_x)}')
        rows.append(f'[X4LAN] AUTH_MAPVIS LIST view={name} phase={phase} ids={",".join(ids)}')
        if phase == 'before_shot':
            rows.append(f'[X4LAN] AUTH_MAPVIS SHOT view={name} seq={seq}')
    return rows


def log(on=True, off=False, back=True, off_reached=True):
    rows = [f'[X4LAN] AUTH_MAPVIS PICK player={PLAYER} sector=370749 npc={NPC} npc_player_m=2500.25 candidates=4']
    rows += view_rows('onscreen', on, -3600000, seq=1)
    rows += view_rows('offscreen', off, -3520000, reached=off_reached, seq=2)
    rows += view_rows('back', back, -3600000, seq=3)
    rows.append('[X4LAN] AUTH_MAPVIS DONE views=3')
    return '\n'.join('12.5 ' + row for row in rows)


class Evidence(unittest.TestCase):
    def evaluate(self, text, missing_shot=None):
        with tempfile.TemporaryDirectory() as directory:
            shots = {}
            for seq, view in enumerate(evidence.VIEWS, start=1):
                if view == missing_shot:
                    continue
                shot = Path(directory) / f'shot-{seq}-{view}.png'
                shot.write_bytes(runner.png_bytes(1, 1, [bytes(3)]))
                shots[seq] = shot
            return evidence.evaluate(evidence.parse_rows(text), shots)

    def test_list_follows_view(self):
        result = self.evaluate(log(on=True, off=False, back=True))
        self.assertEqual(result['problems'], [])
        self.assertTrue(result['accepted'])
        self.assertTrue(result['conclusion'].startswith('listed onscreen, absent offscreen'))
        self.assertEqual([entry['shot_seq'] for entry in result['views']], [1, 2, 3])

    def test_list_not_clipped(self):
        result = self.evaluate(log(on=True, off=True, back=True))
        self.assertTrue(result['conclusion'].startswith('listed in all three views'))

    def test_unreached_pan_is_inconclusive(self):
        result = self.evaluate(log(off=False, off_reached=False))
        self.assertIn('not reached', result['conclusion'])

    def test_npc_missing_onscreen_is_inconclusive(self):
        result = self.evaluate(log(on=False, off=False, back=False))
        self.assertIn('not listed even onscreen', result['conclusion'])

    def test_list_change_during_shot_is_a_problem(self):
        text = log().replace(f'LIST view=offscreen phase=after_shot ids={PLAYER}',
                             f'LIST view=offscreen phase=after_shot ids={PLAYER},{NPC}')
        result = self.evaluate(text)
        self.assertIn('offscreen: raw list changed during the screenshot', result['problems'])
        self.assertTrue(result['conclusion'].startswith('inconclusive'))

    def test_moving_npc_is_a_problem(self):
        text = log().replace('npc_player_m=2500.25 reached=true control=set_map_state camera_x=-3600000',
                             'npc_player_m=2600 reached=true control=set_map_state camera_x=-3600000', 1)
        result = self.evaluate(text)
        self.assertIn('NPC-player distance changed although paused', result['problems'])

    def test_error_row_is_reported(self):
        result = self.evaluate(log() + '\n[X4LAN] AUTH_MAPVIS ERROR no known radar-visible NPC ship in range')
        self.assertIn('no known radar-visible NPC ship in range', result['problems'])

    def test_missing_view_is_reported(self):
        text = '\n'.join(line for line in log().splitlines() if 'view=back' not in line)
        result = self.evaluate(text)
        self.assertIn('back: missing view rows', result['problems'])

    def test_missing_offscreen_list_rows_are_rejected(self):
        text = '\n'.join(line for line in log().splitlines() if 'LIST view=offscreen' not in line)
        result = self.evaluate(text)
        self.assertIn('offscreen: missing raw list rows', result['problems'])
        self.assertFalse(result['accepted'])

    def test_missing_screenshot_or_done_is_rejected(self):
        self.assertFalse(self.evaluate(log(), missing_shot='offscreen')['accepted'])
        self.assertFalse(self.evaluate(log().rsplit('\n', 1)[0])['accepted'])

    def test_radar_change_and_membership_mismatch_are_rejected(self):
        for text in (log().replace('npc_radar=true', 'npc_radar=false', 1),
                     log().replace('npc_listed=false', 'npc_listed=true')):
            with self.subTest(text=text):
                self.assertFalse(self.evaluate(text)['accepted'])

    def test_camera_readback_must_match_the_offscreen_target(self):
        result = self.evaluate(log().replace('target_x=-3520000', 'target_x=-3599999'))
        self.assertIn('did not match the target', result['conclusion'])
        self.assertFalse(result['accepted'])

    def test_focus_change_is_disclosed(self):
        text = log().replace(f'focus={PLAYER}', f'focus={NPC}', 2)
        result = self.evaluate(text)
        self.assertTrue(result['accepted'])
        self.assertIn('Map focus changed', evidence.markdown(result))

    def test_bad_boolean_is_refused(self):
        with self.assertRaises(ValueError):
            self.evaluate(log().replace('paused=true', 'paused=yes', 1))

    def test_markdown_names_conclusion(self):
        text = evidence.markdown(self.evaluate(log()))
        self.assertIn('| offscreen | set_map_state | True | False |', text)
        self.assertIn('Conclusion: listed onscreen, absent offscreen', text)


class Runner(unittest.TestCase):
    OPTIONS = {'profile': 45850, 'gamestart': 'custom_creative', 'distance': 20000.0, 'pan_factor': 4.0,
               'min_m': 1000.0, 'max_m': 8000.0, 'warmup_s': 20.0, 'map_settle_s': 3.0, 'hint_wait_s': 120.0}

    def test_compose_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            target = runner.compose_extension(folder, self.OPTIONS)
            ui = (target / 'ui.xml').read_text()
            order = re.findall(r'<file name="([^"]+)"', ui)
            self.assertEqual(order, ['ui/config.lua', 'ui/map_render_observation.lua', 'ui/map_visibility_probe.lua'])
            self.assertIn('<dependency name="ego_detailmonitor" />', ui)
            self.assertIn('id="x4_cobalt_mapvis"', (target / 'content.xml').read_text())
            config = (target / 'ui/config.lua').read_text()
            self.assertIn('profile = "45850"', config)
            self.assertIn('gamestart = "custom_creative"', config)
            self.assertEqual((target / 'ui/map_visibility_probe.lua').read_bytes(), runner.PROBE.read_bytes())
            with self.assertRaises(FileExistsError):
                runner.compose_extension(folder, self.OPTIONS)

    def test_gamestart_is_validated(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(SystemExit):
            runner.compose_extension(folder, dict(self.OPTIONS, gamestart='x"); os.exit() --'))

    def test_png_writer(self):
        rows = [bytes([255, 0, 0, 0, 255, 0]), bytes([0, 0, 255, 10, 20, 30])]
        data = runner.png_bytes(2, 2, rows)
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        width, height, depth, color = struct.unpack('>IIBB', data[16:26])
        self.assertEqual((width, height, depth, color), (2, 2, 8, 2))
        idat = data.index(b'IDAT')
        length = struct.unpack('>I', data[idat - 4:idat])[0]
        self.assertEqual(zlib.decompress(data[idat + 4:idat + 4 + length]), b'\x00' + rows[0] + b'\x00' + rows[1])

    def test_done_is_not_sufficient_for_runner_success(self):
        result = {'outcome': 'done', 'protected_unchanged': True, 'view_removed': True,
                  'x4_running_after': False, 'evidence': {'accepted': True}}
        self.assertTrue(runner.successful_run(result))
        for key, value in (('protected_unchanged', False), ('view_removed', False),
                           ('x4_running_after', True), ('evidence', {'accepted': False})):
            with self.subTest(key=key):
                self.assertFalse(runner.successful_run(dict(result, **{key: value})))

    def test_tree_metadata_lists_files_only(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'a').mkdir()
            (Path(folder) / 'a' / 'b.txt').write_text('x')
            meta = runner.tree_metadata(folder)
            self.assertEqual(list(meta), [str(Path('a') / 'b.txt')])
            self.assertEqual(meta[str(Path('a') / 'b.txt')][0], 1)


class ProbeSource(unittest.TestCase):
    SOURCE = (ROOT / 'tools/dev/authority_map_visibility_probe.lua').read_text()

    def test_opt_in_and_profile_bound(self):
        self.assertIn("if type(cfg) ~= 'table' or cfg.enabled ~= true then return end", self.SOURCE)
        self.assertIn('not the disposable profile', self.SOURCE)

    def test_no_writes_beyond_map_state(self):
        for name in ('SaveGame', 'LoadGame', 'SetObjectPosition', 'CreateOrder', 'io.', 'os.'):
            self.assertNotIn(name, self.SOURCE)
        # The only non-map write: the map's own one-time popup answer, guarded by HasConfigSetting.
        self.assertEqual(re.findall(r'SetOrder\w*', self.SOURCE), ['SetOrderQueueOption', 'SetOrderQueueOption'])
        self.assertIn("if not C.HasConfigSetting('orderqueue') then", self.SOURCE)

    def test_rows_match_evidence_parser(self):
        for kind in ('PICK', 'MAP_OPEN', 'VIEW', 'LIST', 'SHOT', 'DONE', 'ERROR', 'FALLBACK'):
            self.assertRegex(self.SOURCE, rf"log\(\(?'{kind}[ =]")
        for field in ('npc_listed', 'player_listed', 'raw_count', 'focus', 'selected', 'paused', 'npc_player_m',
                      'reached', 'control', 'npc_known', 'npc_radar'):
            self.assertIn(f'{field}=%s' if field not in ('raw_count', 'selected') else f'{field}=%d', self.SOURCE)


if __name__ == '__main__':
    unittest.main()
