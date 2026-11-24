"""Structural contract for the explicitly dispatched, isolated MD experiment."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'tools/dev/authority_station_parking.xml'


class StationParkingMDTests(unittest.TestCase):
    def setUp(self):
        self.script = ET.parse(PATH).getroot()
        self.cues = self.script.find('cues')

    def test_only_park_can_move_just_the_avatar(self):
        self.assertEqual(self.script.get('name'), 'X4AuthorityStationParking')
        park = self.cues.find("cue[@name='Park']")
        events = {(event.get('screen'), event.get('control')) for event in self.script.iter('event_ui_triggered')}
        self.assertEqual(events, {("'X4AuthorityStationParking'", "'Park'"),
                                  ("'X4AuthorityStationParking'", "'Read'")})
        operations = list(self.script.iter('teleport_player'))
        self.assertEqual(operations, list(park.iter('teleport_player')))
        self.assertEqual([node.attrib for node in operations],
                         [{'object': '$Target', 'control': 'false', 'force': 'true', 'instant': 'true'}])
        self.assertFalse({'warp', 'request_docking', 'set_owner', 'set_object_velocity',
                          'create_order', 'cancel_order', 'add_actor_to_room', 'create_ship',
                          'create_station', 'save_game', 'event_game_loaded'} & {e.tag for e in self.script.iter()})
        action_parent = next(el for el in park.iter() if operations[0] in list(el))
        state_pin = action_parent.find("set_value[@name='State.$Issued']")
        self.assertLess(list(action_parent).index(state_pin), list(action_parent).index(operations[0]))
        self.assertIsNotNone(park.find(".//set_value[@exact=\"'DISPATCHED'\"]"))
        self.assertIsNone(park.find(".//set_value[@exact=\"'PARKED'\"]"))

    def test_hq_first_with_operational_walkable_dock_and_verified_fallback(self):
        inspect = self.cues.find("library[@name='Inspect']")
        self.assertIsNotNone(inspect.find(".//set_value[@exact='@player.headquarters']"))
        selector = self.cues.find("library[@name='SelectFallback']")
        candidates = selector.find('.//find_station')
        self.assertEqual(candidates.get('space'), '$PreferredSector')
        self.assertEqual(candidates.get('checkoperational'), 'true')
        for operator in ('match_relation_to', 'match_relation_of'):
            self.assertEqual(candidates.find(operator).attrib,
                             {'faction': 'faction.player', 'relation': 'neutral', 'comparison': 'ge'})
        dock = self.cues.find("library[@name='InspectDock']").find('.//find_dockingbay')
        self.assertEqual(dock.get('checkoperational'), 'true')
        self.assertEqual(dock.get('walkable'), 'true')
        self.assertEqual(dock.find('match_dock').get('storage'), 'false')
        self.assertIsNotNone(selector.find(".//set_value[@exact='[State.$Code]']"))
        self.assertIsNotNone(selector.find(".//set_value[@exact='[State.$StationMacro]']"))
        self.assertIsNotNone(selector.find(".//set_value[@exact='State.$OwnerID']"))

    def test_arrival_observation_and_response_keep_local_and_saved_identity_distinct(self):
        read = self.cues.find("cue[@name='Read']")
        self.assertIsNotNone(read.find(".//include_actions[@ref='Inspect']"))
        self.assertIsNotNone(read.find(".//do_if[@value=\"$Status == 'READY' and $AtTarget\"]"))
        inspect = self.cues.find("library[@name='Inspect']")
        guards = [e.get('value', '') for e in inspect.iter('do_if')]
        self.assertTrue(any(all(expr in guard for expr in (
            'player.station == $Target', 'player.entity.hascontext.{$Target}', 'not player.controlled',
            'not player.occupiedship', '@player.room.exists')) for guard in guards))
        self.assertTrue(any('$Target != @State.$Target' in guard and '$Target.idcode != State.$Code' in guard
                            for guard in guards))
        reply, = self.script.iter('raise_lua_event')
        self.assertEqual(reply.get('name'), "'X4AuthorityStationParking.Result'")
        self.assertEqual(reply.get('param').split('.[', 1)[1].rstrip(']').split(','), [
            '$Nonce', '$Operation', '$Status', '$Mode', '$StationRef', '$StationCode', '$SectorRef',
            '$SelectedMacro', '$RoomRef', '$OriginRef', '$OriginCode', '$RoomMacro'])


if __name__ == '__main__':
    unittest.main()
