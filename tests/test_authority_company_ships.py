from pathlib import Path
import gzip
import hashlib
import tempfile
import unittest

from authority.checkpoints import Checkpoint
from authority.company_ships import CompanyShip, CompanyShipCatalogue, read_company_ship_catalogue, read_company_ships
from authority.registry_availability_evidence import RegistryAvailabilityEvidenceError
from test_authority_registry_availability_evidence import EPOCH, RUN, build_xml


def sector(components, macro='cluster_14_sector001_macro'):
    return (f'<component class="sector" macro="{macro}" id="[0x801]">'
            '<connections><connection connection="zones">'
            f'{components}</connection></connections></component>')


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
