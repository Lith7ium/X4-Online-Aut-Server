"""Select protected station instances by universe entry, equipment and current save code."""
import unittest
from unittest.mock import patch

from authority.server_parking_sites import BLACK_HOLE_SUN_V, protected_fallback_stations


def component(depth, kind, **attributes):
    return 'component', depth, dict(attributes, **{'class': kind})


def station(code, entry, *, owner='argon', dock=True):
    macro = ('station_arg_tradestation_base_01_macro' if entry == 'tradestation_argon_01'
             else 'station_gen_factory_base_01_macro')
    elements = [component(7, 'station', macro=macro, code=code, owner=owner),
                ('source', 8, {'class': 'godstation', 'entry': entry}),
                component(10, 'defencemodule'), component(13, 'turret'), component(13, 'shieldgenerator')]
    if dock:
        elements.append(component(10, 'dockarea'))
    # An attached ship's turrets and dock must never qualify the station.
    elements.extend([component(10, 'ship_l'), component(13, 'turret'), component(13, 'dockarea')])
    return elements


class ParkingSitesTests(unittest.TestCase):
    def test_rank_configured_sites_only_in_correct_sector_with_attached_station_equipment(self):
        defence = 'arg_defence_cluster_06_sector002'
        trade = 'tradestation_argon_01'
        elements = [
            component(3, 'sector', macro=BLACK_HOLE_SUN_V.upper()),
            *station('AAA-000', trade), *station('BBB-000', defence),
            *station('CCC-000', defence, dock=False), *station('DDD-000', trade, owner='xenon'),
            *station('EEE-000', 'other'), component(3, 'sector', macro='cluster_06_sector001_macro'),
            *station('FFF-000', defence),
        ]

        def verify(checkpoint, *, observe_universe):
            for tag, depth, attributes in elements:
                observe_universe(tag, depth, attributes, None)

        with patch('authority.server_parking_sites.verify_join_snapshot', side_effect=verify):
            ranked = protected_fallback_stations(object())
        self.assertEqual([site.code for site in ranked], ['BBB-000', 'AAA-000'])
        self.assertTrue(all(site.turrets == site.docks == 1 for site in ranked))

    def test_unverified_checkpoint_cannot_supply_a_candidate(self):
        with patch('authority.server_parking_sites.verify_join_snapshot', side_effect=ValueError):
            with self.assertRaises(ValueError):
                protected_fallback_stations(object())
