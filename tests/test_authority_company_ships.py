from pathlib import Path
from functools import partial
import gzip
import hashlib
import tempfile
import unittest

from authority.checkpoints import Checkpoint
from authority.company_ships import CompanyShip, CompanyShipCatalogue, read_company_ship_catalogue, read_company_ships
from authority.registry_availability_evidence import RegistryAvailabilityEvidenceError
from test_authority_registry_availability_evidence import EPOCH, RUN, build_xml

read_with_captains = partial(read_company_ship_catalogue, include_captains=True)


def sector(components, macro='cluster_14_sector001_macro'):
    return (f'<component class="sector" macro="{macro}" id="[0x801]">'
            '<connections><connection connection="zones">'
            f'{components}</connection></connections></component>')


def ship(contents='', component='101'):
    return f'<component id="[0x{component}]" class="ship_s" owner="player">{contents}</component>'


def connections(contents):
    return f'<connections><connection>{contents}</connection></connections>'


def npc(component='501', owner='player', post='aipilot', seed='2211373338772438693'):
    return (f'<component id="[0x{component}]" class="npc" owner="{owner}">'
            f'<entity post="{post}"/><npcseed seed="{seed}"/></component>')


PILOT_POST = '<post id="aipilot" component="[0x501]"/>'


def control(posts=PILOT_POST):
    return f'<control>{posts}</control>'


class CompanyShipTests(unittest.TestCase):
    def read(self, data, *, marker=RUN, digest=None, reader=read_company_ships):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'checkpoint.gz'
            compressed = gzip.compress(data)
            path.write_bytes(compressed)
            checkpoint = Checkpoint('checkpoint-' + 'a' * 32, path,
                                    digest or hashlib.sha256(compressed).hexdigest(), len(compressed), marker)
            return reader(checkpoint)

    def test_only_verified_operational_company_ships_are_listed(self):
        components = (
            '<component id="[0x104]" class="ship_m" owner="player" code="XYZ-246"/>'
            '<component id="[0x101]" class="ship_s" owner="player" name="Survey Ship" code="ABC-123"/>'
            '<component id="[0x102]" class="ship_s" owner="argon" knownto="player"/>'
            '<component id="[0x103]" class="ship_s" owner="player"/>'
            '<component id="[0x900]" class="ship_s" owner="player"/>'
            '<component id="[0x901]" class="computer" owner="player"/>')
        xml = build_xml((1, 1, 1, 1, 0), operational=(1, 1, 0, 1, 0), definitions=sector(components))
        self.assertEqual(self.read(xml), (
            CompanyShip(1, '[0x101]', 'cluster_14_sector001_macro', 'Survey Ship', 'ABC-123'),
            CompanyShip(4, '[0x104]', 'cluster_14_sector001_macro', None, 'XYZ-246'),
        ))

    def test_catalogue_reads_verified_epoch_and_ships_from_same_snapshot(self):
        xml = build_xml((1,), definitions=sector(
            '<component id="[0x101]" class="ship_s" owner="player" code="ABC-123"/>'))
        self.assertEqual(self.read(xml, reader=read_company_ship_catalogue), CompanyShipCatalogue(
            EPOCH, (CompanyShip(1, '[0x101]', 'cluster_14_sector001_macro', None, 'ABC-123'),)))

    def test_operational_npc_crewed_company_ship_remains_a_catalogue_candidate(self):
        xml = build_xml((1,), definitions=sector(
            '<component id="[0x101]" class="ship_s" owner="player" code="ABC-123">'
            '<connections><connection connection="controlentity">'
            '<component id="[0x501]" class="npc" owner="player"/>'
            '</connection></connections></component>'))
        self.assertEqual(self.read(xml), (CompanyShip(1, '[0x101]', 'cluster_14_sector001_macro', None, 'ABC-123'),))

    def test_opt_in_reads_only_the_assigned_captain_on_a_nested_docked_ship(self):
        docked = ('<component class="dockingbay"><connections><connection connection="dock">'
                  + ship(connections(npc('502') + npc()) + control()) + ship(component='102')
                  + '</connection></connections></component>')
        xml = build_xml((1, 1), definitions=sector(f'<component class="station">{connections(docked)}</component>'))
        result = self.read(xml, reader=read_with_captains)
        self.assertEqual([row.captain_seed for row in result.ships], ['2211373338772438693', None])
        self.assertEqual(self.read(xml, reader=read_company_ship_catalogue).ships, (
            CompanyShip(1, '[0x101]', 'cluster_14_sector001_macro', None, None),
            CompanyShip(2, '[0x102]', 'cluster_14_sector001_macro', None, None),
        ))

    def test_ambiguous_foreign_or_out_of_scope_captain_is_not_inferred(self):
        cases = {
            'duplicate controls': control() + control('') + connections(npc()),
            'duplicate posts': control(PILOT_POST * 2) + connections(npc()),
            'duplicate component ID': control() + connections(npc() + npc('0501', owner='argon')),
            'foreign assignment': control() + connections(npc(owner='argon') + npc('502')),
            'matching NPC is not a pilot': control() + connections(npc(post='service')),
            'duplicate NPC seeds': control() + connections(npc().replace(
                '</component>', '<npcseed seed="1"/></component>')),
            'indirect control': connections(control() + npc()),
            'nested ship boundary': control() + connections(ship(connections(npc()), component='102')),
            'nested station boundary': control() + connections(
                f'<component class="station">{connections(npc())}</component>'),
        }
        for condition, contents in cases.items():
            with self.subTest(condition=condition):
                xml = build_xml((1,), definitions=sector(ship(contents)))
                result = self.read(xml, reader=read_with_captains)
                self.assertIsNone(result.ships[0].captain_seed)

    def test_saved_captain_seed_uses_signed_native_text_without_rounding(self):
        for saved, expected in (
            ('9223372036854775807', '9223372036854775807'),
            ('9223372036854775808', '-9223372036854775808'),
            ('18446744073709551615', '-1'),
            ('-9223372036854775808', '-9223372036854775808'),
            ('-9223372036854775809', None),
            ('18446744073709551616', None),
            ('2.211373338772438693e18', None),
        ):
            with self.subTest(saved=saved):
                xml = build_xml((1,), definitions=sector(ship(control() + connections(npc(seed=saved)))))
                self.assertEqual(self.read(xml, reader=read_with_captains).ships[0].captain_seed, expected)

    def test_unusable_labels_are_omitted(self):
        xml = build_xml((1,), definitions=sector(
            '<component id="[0x101]" class="ship_s" owner="player" name="{123,456}" code="invalid"/>'))
        self.assertEqual(self.read(xml), (
            CompanyShip(1, '[0x101]', 'cluster_14_sector001_macro', None, None),
        ))

    def test_sector_must_be_an_unambiguous_ancestor(self):
        ship = '<component id="[0x101]" class="ship_s" owner="player"/>'
        for definitions in (sector('') + '<connections><connection>' + ship + '</connection></connections>',
                            sector(sector(ship)), sector(ship, macro='')):
            with self.subTest(definitions=definitions), self.assertRaisesRegex(
                    RegistryAvailabilityEvidenceError, 'unambiguous sector macro'):
                self.read(build_xml((1,), definitions=definitions))

    def test_unverified_bytes_and_ambiguous_bindings_are_rejected(self):
        ship = '<component id="[0x101]" class="ship_s" owner="player"/>'
        good = build_xml((1,), definitions=sector(ship))
        for xml, options in ((good, {'marker': 'd' * 32}), (good, {'digest': '0' * 64}),
                             (build_xml((1,), definitions=sector(ship + ship)), {})):
            with self.subTest(options=options), self.assertRaises(RegistryAvailabilityEvidenceError):
                self.read(xml, **options)


if __name__ == '__main__':
    unittest.main()
