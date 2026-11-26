"""Synthetic Lua state machine and structural checks for cockpit health replay."""
from pathlib import Path
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / 'src/authority/lua/company_cockpit_health.lua'
MD = ROOT / 'src/authority/md/company_cockpit_health.xml'


class CompanyCockpitHealthTests(unittest.TestCase):
    def test_lua_fences_health_requests_and_independently_verifies_the_same_actor(self):
        luajit = shutil.which('luajit')
        self.assertIsNotNone(luajit)
        subprocess.run([luajit, str(ROOT / 'tests/authority_company_cockpit_health.lua'), str(LUA)],
                       timeout=5, check=True)

    def test_md_consumes_the_current_prepared_request_before_guarded_percent_setters(self):
        source = MD.read_text(encoding='utf-8')
        self.assertEqual(source.count('__COCKPIT_HEALTH_LEASE__'), 1)
        root = ET.fromstring(source.replace('__COCKPIT_HEALTH_LEASE__', 'a' * 32))
        self.assertEqual(root.get('name'), 'X4AuthorityCompanyCockpitHealth')
        self.assertEqual({node.tag for node in root.iter()}, {
            'mdscript', 'cues', 'cue', 'library', 'actions', 'set_value', 'do_if', 'do_elseif',
            'conditions', 'event_ui_triggered', 'include_actions', 'remove_value', 'set_object_shield',
            'set_object_hull', 'raise_lua_event', 'cancel_cue',
        })
        resolve = root.find('.//library[@name="ResolveSeat"]')
        expressions = ' '.join(node.get('value') or node.get('exact', '') for node in resolve.iter())
        for guard in ('not md.X4AuthorityShipRegistry.State.$faulted',
                      '$proposal.$epoch == md.X4AuthorityShipRegistry.State.$epoch',
                      'State.$ships.{$proposal.$index}', 'State.$byship.{$ship} == $proposal.$index',
                      '$ship.owner == faction.player and $ship.trueowner == faction.player',
                      "'%s'.[$ship] == $proposal.$actor", '$ship == player.controlled',
                      '$ship == player.occupiedship', '$ship == player.container', '$ship.hull gt 0',
                      '$ship.maxshield == $proposal.$maxshield', '$ship.maxhull == $proposal.$maxhull'):
            self.assertIn(guard, expressions)
        commands = root.find('.//cue[@name="Command"]')
        commit = next(node for node in commands.iter('do_elseif') if "event.param2 == 'Apply'" in node.get('value'))
        for field in ('request', 'generation', 'window', 'sequence'):
            self.assertIn(f'$args.${field} == State.$proposal.${field}', commit.get('value'))
        self.assertEqual([node.tag for node in list(commit)[:4]],
                         ['set_value', 'remove_value', 'set_value', 'include_actions'])
        self.assertEqual(commit.find('remove_value').get('name'), 'State.$proposal')
        self.assertIn('player.age lt State.$prepared + $args.$validity * 1s', commit.find('do_if').get('value'))
        for name in ('shield', 'hull'):
            setters = root.findall(f'.//set_object_{name}')
            self.assertEqual(len(setters), 1)
            self.assertIn(setters[0], list(commit.iter()))
            self.assertEqual(setters[0].attrib,
                             {'object': '$ship', 'exact': f'100.0 * $proposal.${name} / $proposal.$max{name}'})


if __name__ == '__main__':
    unittest.main()
