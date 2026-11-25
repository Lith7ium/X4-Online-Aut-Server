"""Read-only NPC observation on the regular pilot motion channel."""
from pathlib import Path
import shutil
import subprocess
import unittest

from authority.client_seat_overlay import company_motion_md, company_motion_ui
from authority.company_motion_source import CompanyMotionSource, validate_npc_observation


ROOT = Path(__file__).resolve().parents[1]


class NpcPilotObservationTests(unittest.TestCase):
    def test_observed_actor_is_bound_to_the_offered_registry_without_playback(self):
        target = validate_npc_observation(['2', 'NPC-021'], company_motion=True)
        source = CompanyMotionSource(observed_npc=target)
        self.addCleanup(source.close)
        for own_index, bits in ((2, '11'), (1, '10')):
            with self.assertRaises(ValueError):
                source.configure('a' * 32, own_index, operational_bits=bits)
        config = source.configure('a' * 32, 1, operational_bits='11')
        self.assertEqual((config['observed_npc_index'], config['observed_npc_code']), target)
        ui = company_motion_ui(source)
        self.assertIn('npc_cockpit_observer.lua', ui)
        self.assertIn('npc_cockpit_observer.xml', company_motion_md(source))
        self.assertNotIn('company_hull_playback_x4.lua', ui)
        for target, options in (
            (['2', 'NPC-021'], {'company_motion': False}),
            (['2', 'NPC-021'], {'company_motion': True, 'cockpit_radar_playback': True}),
            (['2', 'NPC-021'], {'company_motion': True, 'playback_index': 3}),
            (['2', 'NPC-021'], {'company_motion': True, 'own_index': 2}),
            ([1.5, 'NPC-021'], {'company_motion': True}),
            (['2', 'incorrect'], {'company_motion': True}),
        ):
            with self.subTest(target=target, options=options), self.assertRaises(ValueError):
                validate_npc_observation(target, **options)

    @unittest.skipUnless(shutil.which('luajit'), 'requires LuaJIT')
    def test_registered_radar_and_map_require_current_native_visibility(self):
        subprocess.run(['luajit', 'tests/authority_npc_map_interest.lua'], cwd=ROOT, check=True)
