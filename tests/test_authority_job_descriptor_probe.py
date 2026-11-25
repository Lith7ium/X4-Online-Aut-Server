"""Read-only job descriptor diagnostics with synthetic callbacks, no X4."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]

class JobDescriptorTest(unittest.TestCase):
    def test_md_is_read_only_and_emits_one_correlated_result(self):
        root=ET.parse(ROOT/'tools/dev/authority_job_descriptor_probe.xml').getroot()
        self.assertEqual({n.tag for n in root.iter()}, {'mdscript','cues','cue','conditions','event_ui_triggered',
            'actions','set_value','do_if','do_elseif','do_else','debug_text','raise_lua_event'})
        self.assertTrue(all(n.get('name','').startswith('$') for n in root.iter('set_value')))
        guards=' '.join(n.get('value','') for n in root.iter('do_if'))
        for term in ('not md.X4AuthorityShipRegistry.State.$faulted',
                     '@md.X4AuthorityShipRegistry.State.$byship.{$ship} == $index',
                     '@$ship.sector.isclass.sector'):
            self.assertIn(term,guards)
        result=root.find('.//raise_lua_event').get('param')
        for term in ('$v.$request_id','$v.$epoch','$index','$ship.macro.id','$ship.owner.id','$ship.sector.macro.id'):
            self.assertIn(term,result)

    def test_unavailable_is_the_else_for_an_inoperational_ship(self):
        root = ET.parse(ROOT/'tools/dev/authority_job_descriptor_probe.xml').getroot()
        registry_gate = root.find('./cues/cue/actions/do_if')
        ship_gate = registry_gate.find('do_if')
        # Static XML check: the operational predicate is a positive conjunct.
        predicates = ship_gate.get('value').split(' and ')
        self.assertIn('@$ship.isoperational', predicates)
        self.assertIn('|ok|', ship_gate.find('raise_lua_event').get('param'))
        branches = list(registry_gate)
        transit, unavailable = branches[branches.index(ship_gate) + 1:]
        self.assertEqual(transit.tag, 'do_elseif')
        for term in ('@$v.$allow_superhighway_gap == true', '@$ship.isoperational',
                     '$ship.isclass.ship', '@md.X4AuthorityShipRegistry.State.$byship.{$ship} == $index',
                     '$ship == player.controlled', '$ship.pilot == player.entity',
                     'not @$ship.sector.isclass.sector', '@$ship.macro',
                     '$ship.owner == faction.player', '$ship.trueowner == faction.player',
                     '@$ship.zone.issuperhighway'):
            self.assertIn(term, transit.get('value').split(' and '))
        self.assertEqual(transit.find('raise_lua_event').get('param'),
                         "'%s|%s|%s|transit|%s'.[$v.$request_id,$v.$epoch,$index,$ship]")
        self.assertEqual(unavailable.tag, 'do_else')
        self.assertEqual(unavailable.find('raise_lua_event').get('param'),
            "'%s|%s|%s|unavailable'.[$v.$request_id,$v.$epoch,$index]")

    def test_unavailable_diagnostics_are_static_rechecks_after_unchanged_responses(self):
        root = ET.parse(ROOT/'tools/dev/authority_job_descriptor_probe.xml').getroot()
        responses = list(root.iter('raise_lua_event'))
        self.assertEqual(len(responses), 4)
        self.assertEqual([node.get('param') for node in responses[2:]],
            ["'%s|%s|%s|unavailable'.[$v.$request_id,$v.$epoch,$index]"] * 2)
        expected = {
            'ship_missing', 'ship_not_operational', 'not_ship', 'reverse_mismatch', 'sector_invalid',
            'ship_macro_missing', 'owner_missing', 'sector_macro_missing', 'registry_faulted_field_missing',
            'registry_ships_missing', 'registry_byship_missing', 'registry_epoch_missing', 'registry_last_missing',
            'registry_faulted', 'epoch_mismatch', 'index_below_range', 'index_above_range',
            'index_noninteger', 'all_pass_on_recheck',
        }
        labels = [node.get('text') for node in root.iter('debug_text')]
        self.assertEqual(set(labels), {"'[X4LAN] AUTH_DESCRIPTOR_RECHECK " + label + "'" for label in expected})
        self.assertEqual(len(labels), len(expected) + 1)  # each branch has its own all-pass label
        unavailable_branches = [node for node in root.iter('do_else')
                                if node.find('raise_lua_event') is not None]
        self.assertEqual(len(unavailable_branches), 2)
        for branch in unavailable_branches:
            self.assertEqual(branch[0].tag, 'raise_lua_event')
            self.assertTrue(all(node.tag == 'debug_text' for condition in branch[1:]
                                for node in condition))

    @unittest.skipUnless(shutil.which('luajit'),'LuaJIT required')
    def test_lua_correlates_original_actor_and_fails_closed(self):
        subprocess.run(['luajit','tests/authority_job_descriptor_probe.lua'],cwd=ROOT,
                       check=True,capture_output=True,text=True,timeout=5)

    @unittest.skipUnless(shutil.which('cc') and shutil.which('luajit'),'C compiler and LuaJIT required')
    def test_fake_x4_exact_large_actor_and_sector_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            library=Path(directory)/'fake-x4.so'
            subprocess.run(['cc','-shared','-fPIC','tests/lua/authority_job_descriptor_fake_x4.c',
                            '-o',str(library)],cwd=ROOT,check=True,capture_output=True,timeout=10)
            subprocess.run(['luajit','tests/authority_job_descriptor_fake_x4.lua',str(library)],cwd=ROOT,
                           check=True,capture_output=True,text=True,timeout=5)

if __name__=='__main__': unittest.main()
