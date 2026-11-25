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
        readback = self.script.find(".//cue[@name='Readback']")
        self.assertEqual(readback.attrib['checkinterval'], '1s')
        select = self.script.find(".//library[@name='ChooseBerth']/actions")
        duplicate = select.find(".//set_value[@exact=\"'already_docked'\"]")
        self.assertIsNotNone(duplicate)
        report = self.script.find(".//library[@name='Report']//raise_lua_event")
        for datum in ('$Route', '$Request.$bay_seed', '$Actual.seed', '$Actual.dockarea.macro.id'):
            self.assertIn(datum, report.attrib['param'])

    def test_selecting_a_berth_requires_an_empty_or_idle_captained_ship(self):
        choose = self.script.find(".//library[@name='ChooseBerth']/actions")
        self.assertEqual(list(choose)[0].attrib, {'name': '$Status', 'exact': "'ship_busy'"})
        self.assertEqual(list(choose)[1].attrib, {'name': '$Captain', 'exact': '@$Ship.assignedaipilot'})
        self.assertEqual(list(choose)[2].attrib, {'ref': 'CheckIdleShip'})
        self.assertEqual(list(choose)[3].attrib, {'value': '$IdleShip'})

        idle = self.script.find(".//library[@name='CheckIdleShip']/actions/set_value")
        terms = ' '.join(idle.attrib['exact'].split())
        for requirement in (
            'not $Ship.isinternallystored', 'not $Ship.orders.count',
            'not $Ship.tradeorders.count', 'not @$Ship.tradecomputer.exists',
            'not $Ship.commander', 'not $Ship.subordinates.count',
            "(not $Ship.defaultorder or $Ship.defaultorder.id == 'Wait')",
            '(not $Captain and not $Ship.pilot and not $Ship.assignedpilot and not $Ship.assignedaipilot)',
            '@$Captain.isclass.npc and $Captain.owner == faction.player and not $Captain.isbusy',
            '$Ship.assignedaipilot == $Captain and $Ship.pilot == $Captain',
            '$Captain.assignedcontrolled == $Ship and $Captain.ship == $Ship',
            '$Captain.hascontext.{$Ship}',
        ):
            self.assertIn(requirement, terms)

    def test_changed_or_busy_captain_leaves_deferred_readback_unconfirmed(self):
        self.assertEqual(len(self.script.findall(".//set_value[@name='$Captain']")), 1)
        readback = self.script.find(".//cue[@name='Readback']/actions")
        self.assertEqual(list(readback)[0].attrib, {'name': '$Status', 'exact': "'readback_failed'"})
        self.assertEqual(list(readback)[1].attrib, {'ref': 'CheckIdleShip'})
        adopted = readback.find("do_if/set_value[@exact=\"'adopted'\"]/..")
        self.assertIn('and $IdleShip', adopted.attrib['value'])
        self.assertIn('@$Ship.dock == $Actual', adopted.attrib['value'])
        self.assertIn('$Actual.docked.count == 1 and $Actual.docked.{1} == $Ship', adopted.attrib['value'])
        failed = readback.find("do_if[@value=\"$Status != 'adopted'\"]")
        self.assertEqual(failed.find("set_value[@name='$Actual']").attrib['exact'], 'null')
        self.assertEqual(failed.find("set_value[@name='$Route']").attrib['exact'], "'none'")
