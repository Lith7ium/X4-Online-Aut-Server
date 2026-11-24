"""Private parked fixture checks with synthetic save and UI boundary data."""
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import authority_station_parking as parking


class StationParkingTest(unittest.TestCase):
    def test_pinned_source_and_exact_cold_location(self):
        source = SimpleNamespace(sha256=parking.SEED)
        self.assertIsNone(parking.source_location('prepare', source, {}))
        with self.assertRaisesRegex(ValueError, 'pinned two-company seed'):
            parking.source_location('prepare', SimpleNamespace(sha256='0'*64), {})
        old = {'station_parking': {'mode': 'prepare', 'source_sha256': parking.SEED,
                                  'native_parked': True, 'park_cue_sha256': '1'*64}}
        location = {'kind': 'FALLBACK', 'code': 'XPJ-169', 'sector': parking.FALLBACK_SECTOR,
                    'room': 'room_gen_arg_manager_macro'}
        with self.assertRaises(ValueError):
            parking.source_location('verify', source, old)
        old['station_parking']['location'] = location
        self.assertEqual(parking.source_location('verify', source, old), location)
        with self.assertRaises(ValueError):
            parking.source_location('verify', source, {'station_parking': old['station_parking'] | {
                'location': location | {'code': 'ABS-ENT'}}})

    def test_saved_avatar_has_station_ancestry_and_two_available_registered_hulls(self):
        location = {'kind': 'FALLBACK', 'code': 'XPJ-169', 'sector': parking.FALLBACK_SECTOR,
                    'room': 'room_gen_arg_manager_macro'}

        def snapshot(_checkpoint, observe_universe):
            for ship in parking.SHIPS:
                observe_universe('component', 3, {'class': 'sector', 'macro': ship['sector']}, None)
                observe_universe('component', 4, {'class': 'ship_s', 'owner': 'player',
                    'macro': ship['macro'], 'code': ship['code']}, ship['index'])
            observe_universe('component', 3, {'class': 'sector', 'macro': location['sector']}, None)
            observe_universe('component', 4, {'class': 'station', 'macro': parking.FALLBACK_MACROS[0],
                'code': location['code'], 'owner': 'argon'}, None)
            observe_universe('component', 5, {'class': 'room', 'macro': location['room']}, None)
            observe_universe('component', 6, {'class': 'player', 'owner': 'player'}, None)
            return {'epoch': parking.EPOCH, 'issued_count': parking.COUNT}

        with patch.object(parking, 'verify_join_snapshot', side_effect=snapshot):
            proof = parking.verify_saved(object(), location)
            self.assertEqual(proof['company_ships'], list(parking.SHIPS))
            self.assertEqual(proof['station']['owner'], 'argon')
            with self.assertRaisesRegex(ValueError, 'saved player room'):
                parking.verify_saved(object(), location | {'room': 'different_room'})

    def test_lua_prepare_and_cold_readback_are_distinct(self):
        subprocess.run(['luajit', 'tests/authority_station_parking.lua'], cwd=parking.ROOT, check=True)
