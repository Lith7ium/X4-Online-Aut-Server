from pathlib import Path
import sys
import unittest

from test_authority_company_ships import connections, control, npc, sector
import test_authority_registry_availability_evidence as registry_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dev'))
from authority_boarding_transport_evidence import SHIP_MACRO, verify_boarding_transport


CODE = 'ABC-123'
SECTOR = 'cluster_14_sector001_macro'


def crew(count=8, role='marine'):
    return '<people>' + f'<person role="{role}"/>' * count + '</people>'


def ship(contents, *, owner='player', macro=SHIP_MACRO, component='101', code=CODE):
    return (f'<component id="[0x{component}]" class="ship_m" owner="{owner}" '
            f'macro="{macro}" code="{code}">{contents}</component>')


class BoardingTransportEvidenceTests(unittest.TestCase):
    def verify_saved(self, definitions, *, expected_sector=SECTOR, **options):
        xml = registry_evidence.build_xml((1,), definitions=sector(definitions), **options)
        with registry_evidence.RegistryAvailabilityEvidenceTests.checkpoint(
                self, xml, registry_evidence.RUN) as checkpoint:
            return verify_boarding_transport(checkpoint, code=CODE, sector_macro=expected_sector)

    def test_saved_registered_company_transport(self):
        docked = ship(crew(3) + connections(npc('502')), component='102', code='XYZ-456')
        captain = npc(seed='18446744073709551615').replace('</component>', crew(2) + '</component>')
        contents = control() + connections(captain + docked) + crew()
        self.assertEqual(self.verify_saved(ship(contents)), {
            'ship_index': 1, 'code': CODE, 'sector_macro': SECTOR,
            'captain_seed': '-1', 'marines': 8,
        })

    def test_physical_ship_and_registry_must_match(self):
        contents = control() + connections(npc()) + crew()
        cases = [
            ('wrong owner', ship(contents, owner='argon'), {}),
            ('wrong macro', ship(contents, macro='ship_arg_m_frigate_01_a_macro'), {}),
            ('wrong sector', ship(contents), {'expected_sector': 'wrong_sector'}),
            ('wrong code', ship(contents, code='XYZ-456'), {}),
            ('duplicate code', ship(contents) + ship('', component='102'), {}),
            ('nonoperational', ship(contents), {'operational': (0,)}),
            ('wrong source forward', ship(contents), {'source_ships': {1: '102'}}),
            ('wrong source reverse', ship(contents), {'source_reverse': {'102': 1}}),
            ('old snapshot', ship(contents), {'format_version': 2}),
        ]
        for case, definitions, options in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.verify_saved(definitions, **options)

    def test_placeholder_or_out_of_scope_captain_is_rejected(self):
        nested_npc = npc('502').replace('</component>', connections(npc()) + '</component>')
        cases = [
            control() + connections('<component class="npc" owner="player" id="[0x501]"/>'),
            control() + connections(npc(seed='0')),
            control() + connections(npc(owner='argon')),
            control() + connections(npc(post='service')),
            control() + connections(nested_npc),
            control() + connections(ship(connections(npc()), component='102', code='XYZ-456')),
            control() + control('') + connections(npc()),
            connections(npc()),
        ]
        for contents in cases:
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, 'NPC captain'):
                self.verify_saved(ship(contents + crew()))

    def test_exact_marine_crew_comes_from_transport_people(self):
        captain = control() + connections(npc())
        cases = [
            ship(captain + crew(7)),
            ship(captain + crew(9)),
            ship(captain + crew(7) + crew(1, 'service')),
            ship(captain + connections(ship(crew(), component='102', code='XYZ-456'))),
            ship(captain) + ship(crew(), component='102', code='XYZ-456'),
        ]
        for definitions in cases:
            with self.subTest(definitions=definitions), self.assertRaisesRegex(ValueError, 'eight saved crew'):
                self.verify_saved(definitions)
