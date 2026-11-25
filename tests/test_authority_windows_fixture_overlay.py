"""Synthetic files only: no X4, Windows installation, or real DLL."""
import hashlib
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

from authority.experimental_windows_fixture_overlay import _ordinary, install_fixture_overlay
from authority.load_messages import Offer
from authority.trusted_marker_load_session import MarkerBoundOffer


class FixtureOverlayTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.view = root / 'view'
        (self.view / 'extensions').mkdir(parents=True)
        (self.view / '.x4ls-view').write_text('synthetic view')
        self.dll = root / 'synthetic.dll'
        self.dll.write_bytes(b'MZ synthetic test library')
        offer = Offer(world_epoch='a' * 32, connection_handle=1, baseline_id=2,
                      load_generation=3, checkpoint_id='checkpoint-' + 'b' * 32,
                      sha256='c' * 64, attempt_id='d' * 32, size=100, name='j1.xml.gz')
        self.options = dict(new_test_view=True, bound_offer=MarkerBoundOffer(offer, 'e' * 32),
                            save_path=r'C:\private\45841\save', participant_port=20123,
                            participant_lease='f' * 32, windows_dll=self.dll,
                            dll_sha256=hashlib.sha256(self.dll.read_bytes()).hexdigest())

    def install(self, **changes):
        return install_fixture_overlay(self.view, **(self.options | changes))

    def test_overlay_contains_ordered_fixture_hooks_and_windows_dll(self):
        manifest = self.install()
        target = self.view / 'extensions/x4_lan_lab'
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((target / 'native/x4lan_net_64.dll').read_bytes(), self.dll.read_bytes())
        ui = ET.parse(target / 'ui.xml').getroot()
        paths = [item.attrib['name'] for item in ui.iter('file')]
        for before, after in [('ui/authority_identity_probe.lua', 'ui/authority_download_load_probe.lua'),
                              ('ui/authority_checkpoint_marker_probe.lua', 'ui/authority_download_load_probe.lua'),
                              ('ui/local_udp_module.lua', 'ui/authority_two_x4_participant_probe.lua')]:
            self.assertLess(paths.index(before), paths.index(after))
        load = (target / 'ui/authority_download_load_probe.lua').read_text()
        self.assertLess(load.index('X4LAN_AUTHORITY_MARKER_READY()'), load.index('X4LAN_IDENTITY_READY()'))
        self.assertLess(load.index('X4LAN_IDENTITY_READY()'), load.index('barrier.update()'))
        config = (target / 'ui/config.lua').read_text()
        self.assertIn('authority_identity_phase="load"', config)
        self.assertIn('authority_marker_phase="load"', config)
        self.assertNotIn('e' * 32, config)  # observed from the saved world, not the expected marker
        self.assertIn('authority_participant_port=20123', config)

    def test_fixture_world_faction_dependencies_match_tracked_base(self):
        manifest = self.install()
        target = self.view / 'extensions/x4_lan_lab'
        base = Path(__file__).resolve().parents[1] / 'src/lockstep/base'
        expected = {'libraries/factions.xml', 'libraries/icons.xml'} | {
            f'assets/textures/ui/x4lan/x4lan_player{index}.dds' for index in range(1, 17)}
        self.assertEqual({name for name in manifest if name.startswith(('libraries/', 'assets/'))}, expected)
        for name in expected:
            with self.subTest(name=name):
                data = (base / name).read_bytes()
                self.assertEqual((target / name).read_bytes(), data)
                self.assertEqual(manifest[name], hashlib.sha256(data).hexdigest())
        self.assertNotIn('native_gate_player_identity_plan', (target / 'ui/config.lua').read_text())

    def test_existing_extension_is_never_modified(self):
        target = self.view / 'extensions/x4_lan_lab'
        target.mkdir()
        keep = target / 'keep'
        keep.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(keep.read_bytes(), b'original')
        self.assertEqual([path.name for path in target.iterdir()], ['keep'])

    def test_installed_marker_restores_persisted_markerstate_before_inspection(self):
        """Recovered checkpoints persist MarkerState, not one-shot Store state."""
        self.install()
        marker = ET.parse(self.view / 'extensions/x4_lan_lab/md/authority_checkpoint_marker.xml').getroot()
        self.assertEqual(marker.attrib['name'], 'X4AuthorityCheckpointMarker')
        cues = {cue.attrib['name']: cue for cue in marker.findall('./cues/cue')}
        state = cues['MarkerState']
        self.assertEqual(state.attrib.get('namespace'), 'this')
        self.assertIsNone(state.find('conditions'))
        # A fresh state initializes only its load flag; it must not overwrite
        # the marker recovered from the checkpoint.
        self.assertEqual([value.attrib for value in state.findall('./actions/set_value')],
                         [{'name': '$loaded', 'exact': 'false'}])
        loaded = cues['Loaded']
        self.assertIsNotNone(loaded.find('./conditions/event_game_loaded'))
        mark_loaded = loaded.find('./actions/do_if/set_value')
        self.assertEqual(mark_loaded.attrib, {'name': 'MarkerState.$loaded', 'exact': 'true'})
        self.assertEqual(loaded.find('./actions/do_if').attrib['value'], 'MarkerState.$marker?')
        inspection = cues['Inspect'].find('./actions/do_if')
        self.assertEqual(inspection.attrib['value'],
                         'MarkerState.$marker? and MarkerState.$loaded? and MarkerState.$loaded')
        event = inspection.find('raise_lua_event')
        self.assertEqual(event.attrib, {'name': "'X4AuthorityCheckpointMarker.Inspected'",
                                       'param': 'MarkerState.$marker'})

    def test_installed_registry_gate_executes_before_pause_receipt(self):
        manifest = self.install(registry_status=('ready', '1' * 32, 3, '2' * 64),
                                registry_nonce='3' * 32)
        target = self.view / 'extensions/x4_lan_lab'
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), digest)
        paths = [item.attrib['name'] for item in ET.parse(target / 'ui.xml').iter('file')]
        self.assertLess(paths.index('ui/recovery_registry_status.lua'),
                        paths.index('ui/authority_download_load_probe.lua'))
        self.assertIn('md/authority_ship_registry.xml', manifest)
        root = Path(__file__).resolve().parents[1]
        for mode in ('ready', 'wrong_count'):
            with self.subTest(mode=mode):
                subprocess.run(['luajit', str(root / 'tests/authority_windows_registry_gate.lua'),
                                str(target / 'ui'), mode], check=True, capture_output=True, cwd=root)

    def test_saved_ship_seat_requires_operational_full_registry_and_loads_before_pause(self):
        status = ('ready', '1' * 32, 8, '2' * 64)
        snapshot = {
            'format': 3, 'source_registry_checked': True, 'epoch': status[1],
            'evidence_scope': 'saved checkpoint bytes only', 'run_id': 'e' * 32,
            'issued_count': 8, 'mapping_sha256': status[3],
            'availability_bits': '11111111', 'operational_bits': '11111101',
            'available_count': 8, 'unavailable_count': 0, 'operational_count': 7,
        }
        options = dict(registry_status=status, registry_nonce='3' * 32)
        with self.assertRaisesRegex(ValueError, 'requires full registry inspection'):
            self.install(**options, seat_index=8)
        with self.assertRaisesRegex(ValueError, 'inoperable'):
            self.install(**options, registry_snapshot=snapshot, seat_index=7)
        from authority.company_purchase_source import CompanyPurchaseSource
        source = CompanyPurchaseSource(expected_epoch=status[1])
        self.addCleanup(source.close)
        manifest = self.install(**options, registry_snapshot=snapshot, seat_index=8, company_purchase=source)
        target = self.view / 'extensions/x4_lan_lab'
        self.assertIn('md/client_seat.xml', manifest)
        # The running session can discover new ships after this seat was chosen.
        # The issued index and actor still have to match the original cockpit.
        seat = ET.parse(target / 'md/client_seat.xml')
        berth = seat.find(".//cue[@name='OccupiedBerth']/actions/do_if")
        self.assertIn('$last ge $args.$count', berth.get('value'))
        self.assertIn('$args.$index le $args.$count', berth.get('value'))
        ship_checks = berth.find('do_if').get('value')
        self.assertIn('$byship.{$ship} == $args.$index', ship_checks)
        self.assertIn("'%s'.[$ship] == @$args.$actor", ship_checks)
        self.assertIn('$ship == player.controlled', ship_checks)
        self.assertIn('md/authority_checkpoint_marker.xml', manifest)
        self.assertNotIn('md/authority_identity_fixture.xml', manifest)
        self.assertNotIn('ui/authority_identity_probe.lua', manifest)
        self.assertNotIn('authority_identity_phase', (target / 'ui/config.lua').read_text())
        self.assertIn('ui/company_trade_capture.lua', manifest)
        self.assertIn('ui/company_purchase_source.lua', manifest)
        self.assertNotIn('ui/company_undock_hold.lua', manifest)
        self.assertNotIn('md/company_client_undock.xml', manifest)
        paths = [item.attrib['name'] for item in ET.parse(target / 'ui.xml').iter('file')]
        self.assertNotIn('ui/authority_identity_probe.lua', paths)
        self.assertLess(paths.index('ui/client_seat.lua'),
                        paths.index('ui/authority_download_load_probe.lua'))
        self.assertLess(paths.index('ui/company_trade_capture.lua'), paths.index('ui/company_purchase_source.lua'))
        self.assertLess(paths.index('ui/company_purchase_source.lua'),
                        paths.index('ui/authority_two_x4_participant_probe.lua'))
        self.assertIn('authority_company_purchase={', (target / 'ui/config.lua').read_text())
        load = (target / 'ui/authority_download_load_probe.lua').read_text()
        self.assertNotIn('X4LAN_IDENTITY_READY', load)
        self.assertLess(load.index('X4LAN_AUTHORITY_MARKER_READY()'), load.index('full_registry_inspect:ready()'))
        self.assertLess(load.index('full_registry_inspect:ready()'), load.index('client_seat:tick()'))
        self.assertLess(load.index('client_seat:admission_ready()'), load.index('X4LAN_AUTHORITY_SELECTED_SEAT=client_seat'))
        self.assertLess(load.index('X4LAN_AUTHORITY_SELECTED_SEAT=client_seat'),
                        load.index('X4LAN_COMPANY_PURCHASE_SOURCE.arm(client_seat)'))
        self.assertLess(load.index('client_seat:tick()'), load.index('barrier.update()'))
        self.assertIn('authority_registry_snapshot,8,full_registry_inspect', load)
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_client_seat.lua'], cwd=root, check=True)

    def test_explicit_undock_overlay_orders_hook_before_participant_and_stages_lock_cue(self):
        from authority.company_purchase_source import CompanyPurchaseSource

        status = ('ready', '1' * 32, 2, '2' * 64)
        snapshot = {
            'format': 3, 'source_registry_checked': True, 'epoch': status[1],
            'evidence_scope': 'saved checkpoint bytes only', 'run_id': 'e' * 32,
            'issued_count': 2, 'mapping_sha256': status[3],
            'availability_bits': '11', 'operational_bits': '11',
            'available_count': 2, 'unavailable_count': 0, 'operational_count': 2,
        }
        source = CompanyPurchaseSource(expected_epoch=status[1], undock=True)
        self.addCleanup(source.close)
        manifest = self.install(registry_status=status, registry_nonce='3' * 32,
                                registry_snapshot=snapshot, seat_index=2, company_purchase=source)
        self.assertIn('md/company_client_undock.xml', manifest)
        target = self.view / 'extensions/x4_lan_lab'
        paths = [item.attrib['name'] for item in ET.parse(target / 'ui.xml').iter('file')]
        self.assertLess(paths.index('ui/company_undock_hold.lua'),
                        paths.index('ui/authority_two_x4_participant_probe.lua'))
        settings = (target / 'ui/config.lua').read_text()
        self.assertIn('undock_nonce=', settings)
        self.assertIn('undock_request=', settings)

    def test_incomplete_registry_options_leave_no_overlay(self):
        for changes in ({'registry_nonce': '3' * 32},
                        {'registry_status': ('ready', '1' * 32, 3, '2' * 64)},
                        {'registry_status': ('ready', '1' * 32, True, '2' * 64),
                         'registry_nonce': '3' * 32}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.install(**changes)
            self.assertEqual(list((self.view / 'extensions').iterdir()), [])

    def test_playback_overlay_reuses_motion_modules_and_stop_gate(self):
        settings = dict(port=23456, lease='1'*32, attempt='2'*32, generation='3'*32,
                        nonce='4'*32, router_generation='5'*32, index=1318, timeout=5,
                        saved_playback_fixture='recovery_enh_870')
        status = ('ready', '4ba7be438ebb04ec5bb85fdb95b5a488', 10082, '9'*64)
        manifest = self.install(registry_status=status, registry_nonce='6'*32, motion_settings=settings)
        target = self.view / 'extensions/x4_lan_lab'
        for name, digest in manifest.items():
            self.assertEqual(hashlib.sha256((target/name).read_bytes()).hexdigest(), digest)
        paths = [item.attrib['name'] for item in ET.parse(target/'ui.xml').iter('file')]
        for before, after in (('local_udp_module.lua', 'authority_recovered_client_motion.lua'),
                              ('replica_motion_binding.lua', 'saved_motion_apply.lua'),
                              ('motion_playback.lua', 'authority_recovered_client_motion.lua'),
                              ('authority_recovered_client_motion.lua', 'authority_two_x4_participant_probe.lua')):
            self.assertLess(paths.index('ui/'+before), paths.index('ui/'+after))
        control = (target/'ui/authority_two_x4_participant_probe.lua').read_text()
        self.assertIn("client_motion:state()=='stopped'", control)
        self.assertIn('saved_playback_fixture=cfg.authority_client_motion_saved_playback_fixture', control)
        self.assertIn('X4LAN_RECOVERED_CLIENT_LOAD_READY', (target/'ui/authority_download_load_probe.lua').read_text())
        self.assertNotIn('md/authority_saved_motion_apply.xml', manifest)  # Playback uses native setters, not the one-shot MD route.
        root = Path(__file__).resolve().parents[1]
        for path in paths:
            subprocess.run(['luajit', '-b', str(target/path), str(target/'compiled.out')],
                           check=True, capture_output=True, cwd=root)

    def test_motion_context_collision_refuses_before_writing(self):
        settings = dict(port=23456, lease='1'*32, attempt='2'*32, generation='3'*32,
                        nonce='4'*32, router_generation='5'*32, index=1, timeout=5)
        status = ('ready', '8'*32, 2, '9'*64)
        for altered in ({**settings, 'port': self.options['participant_port']},
                        {**settings, 'lease': self.options['participant_lease']},
                        {**settings, 'generation': '6'*32}):
            with self.subTest(altered=altered), self.assertRaisesRegex(ValueError, 'overlaps'):
                self.install(registry_status=status, registry_nonce='6'*32, motion_settings=altered)
            self.assertEqual(list((self.view/'extensions').iterdir()), [])

    def test_bad_input_fails_before_creating_any_extension(self):
        for changes in ({'new_test_view': False}, {'participant_port': 0},
                        {'participant_lease': 'bad'}, {'dll_sha256': '0' * 64},
                        {'save_path': r'C:\Users\someone\save'},
                        {'save_path': r'C:\private\45841\..\save'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.install(**changes)
                self.assertEqual(list((self.view / 'extensions').iterdir()), [])

    def test_write_failure_leaves_partial_extension_without_deletion(self):
        original = Path.open
        def interrupted(path, *args, **kwargs):
            if path.name == 'x4_control_engine.lua' and args and args[0] == 'xb':
                raise OSError('simulated failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', interrupted):
            with self.assertRaises(OSError):
                self.install()
        target = self.view / 'extensions/x4_lan_lab'
        self.assertTrue((target / 'ui/client_load_barrier.lua').is_file())
        with self.assertRaises(FileExistsError):
            self.install()

    def test_linked_extension_root_is_refused(self):
        extensions = self.view / 'extensions'
        extensions.rmdir()
        other = self.view.parent / 'other'
        other.mkdir()
        try:
            extensions.symlink_to(other, target_is_directory=True)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(other.iterdir()), [])

    def test_reparse_attributes_are_refused(self):
        for attributes in (0x410, None, -1):
            info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=attributes)
            with self.subTest(attributes=attributes), \
                 patch.object(Path, 'lstat', return_value=info), \
                 patch.object(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400, create=True):
                with self.assertRaises(ValueError):
                    _ordinary(self.view, directory=True)


if __name__ == '__main__':
    unittest.main()
