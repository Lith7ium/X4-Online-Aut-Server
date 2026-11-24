from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


CUE = Path(__file__).resolve().parents[1] / 'tools/dev/authority_company_berth_adoption.xml'


class BerthAdoptionCue(unittest.TestCase):
    def setUp(self):
        self.script = ET.parse(CUE)

    def test_selection_resolves_the_requested_berth_even_if_occupied(self):
        select = self.script.find(".//library[@name='Select']/actions")
        resolve = select.find(".//find_dockingbay[@name='$StationBays']")
        self.assertEqual(resolve.attrib['object'], '$Station')
        self.assertIsNone(resolve.find('match_dock'))
        identify = select.find(".//do_for_each[@in='$StationBays']/do_if").attrib['value']
        for field in ('bay_seed', 'bay_macro', 'dockarea_macro'):
            self.assertIn(f'$Request.${field}', identify)

    def test_fallback_checks_free_external_compatible_bays_on_the_same_station(self):
        select = self.script.find(".//library[@name='ChooseBerth']/actions")
        free = select.find(".//find_dockingbay[@name='$FreeBays']")
        self.assertEqual(free.attrib['object'], '$Station')
        self.assertEqual(free.attrib['checkoperational'], 'true')
        self.assertEqual(free.find('match_dock').attrib, {
            'size': '$Ship.docksize', 'free': 'true', 'trading': 'true',
            'allowplayeronly': 'false', 'showroom': 'false', 'storage': 'false',
        })
        candidate = select.find(".//do_for_each[@in='$FreeBays']/do_if")
        for requirement in ('container == $Station', '.external', 'assignedship.exists', 'docked.count == 0'):
            self.assertIn(requirement, candidate.attrib['value'])
        prefer = candidate.find('do_if').attrib['value']
        self.assertIn('$Candidate == $Requested', prefer)
        self.assertIn('$Candidate.seed lt $Actual.seed', prefer)
        self.assertIn('$RequestedOccupied', prefer)

        eligibility = self.script.find(".//library[@name='SelectEligible']/actions/do_if")
        self.assertIn('$CompatibleBays.indexof.{$Requested}', eligibility.attrib['value'])
        self.assertIn('$Requested.istradingallowed', eligibility.attrib['value'])
        self.assertEqual(eligibility.find('include_actions').attrib['ref'], 'ChooseBerth')

    def test_only_dispatch_can_move_a_ship_and_duplicate_returns_from_selection(self):
        self.assertEqual([element.attrib for element in self.script.iter('spawn_docked')],
                         [{'ship': '$Ship', 'dock': '$Actual'}])
        for mutation in ('create_ship', 'create_order', 'create_cue_actor', 'assign_control_entity',
                         'undock', 'destroy_object'):
            self.assertFalse(list(self.script.iter(mutation)))
        select = self.script.find(".//library[@name='ChooseBerth']/actions")
        duplicate = select.find(".//set_value[@exact=\"'already_docked'\"]")
        self.assertIsNotNone(duplicate)
        report = self.script.find(".//library[@name='Report']//raise_lua_event")
        for datum in ('$Route', '$Request.$bay_seed', '$Actual.seed', '$Actual.dockarea.macro.id'):
            self.assertIn(datum, report.attrib['param'])
