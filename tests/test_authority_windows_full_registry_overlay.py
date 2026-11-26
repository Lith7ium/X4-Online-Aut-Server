"""Synthetic install and actual Lua-owner checks; no native X4 run."""
import hashlib
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from authority.client_registry_overlay import extend_full_inspect
from authority.company_motion_source import CompanyMotionSource
from authority.experimental_windows_fixture_overlay import install_fixture_overlay
from authority.load_messages import Offer
from authority.trusted_marker_load_session import MarkerBoundOffer

if __package__:
    from .run_fixture_lua import run_fixture
else:
    from run_fixture_lua import run_fixture


class FullRegistryOverlayTest(unittest.TestCase):
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
        self.status = ('ready', '1' * 32, 3, '2' * 64)
        self.snapshot = dict(format=3, source_registry_checked=True,
                             evidence_scope='saved checkpoint bytes only',
                             run_id='e' * 32, epoch='1' * 32, issued_count=3,
                             mapping_sha256='2' * 64, availability_bits='110',
                             operational_bits='100', available_count=2,
                             unavailable_count=1, operational_count=1)
        self.options = dict(new_test_view=True, bound_offer=MarkerBoundOffer(offer, 'e' * 32),
                            save_path=r'C:\private\45841\save', participant_port=20123,
                            participant_lease='f' * 32, windows_dll=self.dll,
                            dll_sha256=hashlib.sha256(self.dll.read_bytes()).hexdigest(),
                            registry_status=self.status, registry_nonce='3' * 32,
                            registry_snapshot=self.snapshot)

    def test_install_runs_full_inspect_before_header_and_pause(self):
        manifest = install_fixture_overlay(self.view, **self.options)
        target = self.view / 'extensions/x4_lan_lab'
        self.assertIn('md/authority_registry_availability_snapshot.xml', manifest)
        self.assertIn('ui/registry_checkpoint_gate.lua', manifest)
        self.assertIn('ui/client_registry_inspect.lua', manifest)
        engine = (target / 'ui/x4_client_load_engine.lua').read_text()
        self.assertIn('double GetCurrentGameTime(void)', engine)
        self.assertIn('game_time=function() return tonumber(C.GetCurrentGameTime()) end', engine)
        probe = (target / 'ui/authority_download_load_probe.lua').read_text()
        self.assertNotIn('end,300))', probe)
        self.assertLess(probe.index('full_registry_inspect:tick()'),
                        probe.index('X4LAN_RECOVERY_REGISTRY_STATUS.new'))
        self.assertLess(probe.index('X4LAN_RECOVERY_REGISTRY_STATUS.new'),
                        probe.index('barrier.update()'))
        root = Path(__file__).resolve().parents[1]
        for mode in ('ready', 'wrong_bits', 'time_changed', 'time_changed_header'):
            with self.subTest(mode=mode):
                run_fixture(root / 'tests/authority_windows_full_registry_overlay.lua', target / 'ui', mode)

    def test_alternate_profile_binds_one_save_directory_for_load_control_and_optional_flight(self):
        for flight in (False, True):
            with self.subTest(flight=flight):
                view = self.view.parent / ('flight' if flight else 'seat')
                (view / 'extensions').mkdir(parents=True)
                (view / '.x4ls-view').write_text('synthetic view')
                source = CompanyMotionSource() if flight else None
                if source is not None:
                    self.addCleanup(source.close)
                options = dict(self.options, profile_id=45850, save_path=r'D:\Test\45850\save',
                               seat_index=1, company_motion=source)
                install_fixture_overlay(view, **options)
                target = view / 'extensions/x4_lan_lab'
                config = (target / 'ui/config.lua').read_text()
                self.assertIn('test_profile="45850"', config)
                self.assertIn('authority_windows_company_profile=true', config)
                self.assertIn(r'authority_load_save_path="D:\\Test\\45850\\save"', config)
                scripts = [entry.get('name') for entry in ElementTree.parse(target / 'ui.xml').iter('file')]
                normalizer = 'ui/replica_motion_binding.lua'
                self.assertEqual(scripts.count(normalizer), 1)
                for consumer in ('ui/authority_download_load_probe.lua', 'ui/authority_two_x4_participant_probe.lua'):
                    self.assertLess(scripts.index(normalizer), scripts.index(consumer))

    def test_alternate_profile_requires_its_own_company_save_path(self):
        options = dict(self.options, profile_id=45850, save_path=r'D:\Test\45850\save', seat_index=1)
        for change in ({'profile_id': 45852}, {'profile_id': 45850.0}, {'seat_index': None},
                       {'save_path': r'D:\Test\45841\save'}, {'motion_settings': {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                install_fixture_overlay(self.view, **(options | change))
            self.assertFalse((self.view / 'extensions/x4_lan_lab').exists())

    def test_combat_mounts_health_receiver_with_the_owned_motion_lease(self):
        source = CompanyMotionSource()
        self.addCleanup(source.close)
        assets = install_fixture_overlay(self.view, **self.options, seat_index=1,
                                         company_motion=source, company_combat=True, combat_capture=True)
        target = self.view / 'extensions/x4_lan_lab'
        config = (target / 'ui/config.lua').read_text()
        motion = config.split('authority_company_motion={', 1)[1].split('}', 1)[0]
        self.assertIn('cockpit_health=true', motion)
        lease = re.search(r'\blease="([0-9a-f]{32})"', motion).group(1)
        cue = (target / 'md/company_cockpit_health.xml').read_text()
        self.assertIn(f"@$args.$lease == '{lease}'", cue)
        self.assertIn('md/company_cockpit_health.xml', assets)
        self.assertIn('md/client_combat_capture.xml', assets)
        scripts = [entry.get('name') for entry in ElementTree.parse(target / 'ui.xml').iter('file')]
        self.assertEqual(scripts.count('ui/combat_observer.lua'), 1)
        self.assertLess(scripts.index('ui/combat_observer.lua'), scripts.index('ui/company_cockpit_health.lua'))
        self.assertLess(scripts.index('ui/company_cockpit_health.lua'),
                        scripts.index('ui/authority_two_x4_participant_probe.lua'))

    def test_software_overlay_selects_bounded_inspection_budget(self):
        with patch('authority.experimental_windows_fixture_overlay.extend_full_inspect',
                   side_effect=lambda text: extend_full_inspect(text, software_rendered=True)):
            install_fixture_overlay(self.view, **self.options)
        target = self.view / 'extensions/x4_lan_lab/ui'
        root = Path(__file__).resolve().parents[1]
        run_fixture(root / 'tests/authority_windows_full_registry_overlay.lua', target, 'ready', '300')

    def test_invalid_snapshot_rejected_before_install(self):
        for change in ({'format': 2}, {'source_registry_checked': False},
                       {'run_id': 'f' * 32}, {'mapping_sha256': '9' * 64},
                       {'operational_bits': '101'}, {'availability_bits': '11'},
                       {'available_count': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                install_fixture_overlay(self.view, **(self.options | {
                    'registry_snapshot': self.snapshot | change}))
            self.assertFalse((self.view / 'extensions/x4_lan_lab').exists())


if __name__ == '__main__':
    unittest.main()
