import gzip
from pathlib import Path
import tempfile
import unittest

from authority.save_dlc import active_dlc_ids


class SaveDlcTests(unittest.TestCase):
    def read_header(self, xml):
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / 'save.xml.gz'
            save.write_bytes(gzip.compress(xml.encode('utf-8')))
            return active_dlc_ids(save)

    def test_only_active_official_patches_are_required(self):
        xml = '''<savegame><info><patches>
            <patch extension="ego_dlc_terran"/><patch extension="x4_lan_lab"/>
            <patch extension="ego_dlc_boron"/><patch extension="ego_dlc_terran"/>
            <history><patch extension="ego_dlc_ventures"/></history>
            </patches></info><universe/></savegame>'''
        self.assertEqual(self.read_header(xml), ('ego_dlc_boron', 'ego_dlc_terran'))

    def test_base_game_and_missing_header_are_distinct(self):
        self.assertEqual(self.read_header('<savegame><info><save/></info></savegame>'), ())
        with self.assertRaises(ValueError):
            self.read_header('<savegame><universe/></savegame>')

    def test_rejects_a_document_type(self):
        with self.assertRaises(ValueError):
            self.read_header('<!DOCTYPE savegame><savegame><info/></savegame>')
