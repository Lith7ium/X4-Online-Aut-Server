"""Offline checks for the fresh, private base-game authority world."""
import gzip
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/dev'))
import authority_vanilla_genesis as genesis


class VanillaGenesisTest(unittest.TestCase):
    def test_header_rejects_any_current_or_past_dlc_and_other_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'save.xml.gz'
            for start, patches, valid in (
                ('custom_creative', '', True),
                ('custom_creative', '<patches><history/></patches>', True),
                ('custom_creative', '<patches><patch extension="ego_dlc_terran"/></patches>', False),
                ('custom_creative', '<patches><history><patch extension="ego_dlc_split"/></history></patches>', False),
                ('different', '', False),
            ):
                with self.subTest(start=start, patches=patches):
                    xml = f'<savegame><info><game start="{start}"/>{patches}</info><universe/></savegame>'
                    path.write_bytes(gzip.compress(xml.encode()))
                    if valid:
                        genesis.verify_base_save(SimpleNamespace(path=path))
                    else:
                        with self.assertRaises(ValueError):
                            genesis.verify_base_save(SimpleNamespace(path=path))

    def test_owned_views_still_select_installed_dlc_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = root/'game'
            (game/'extensions/ego_dlc_terran').mkdir(parents=True)
            (game/'extensions/foreign_mod').mkdir()
            for base_only in (False, True):
                out = root / str(base_only)
                out.mkdir()
                case, profile, binds, _ = genesis.snapshot.prepare_case(
                    out, game, root/'home', base_game_only=base_only)
                self.assertEqual({path.name for path, _ in binds}, set() if base_only else {'ego_dlc_terran'})
                self.assertEqual({p.name for p in (case/'extensions').iterdir()},
                                 {'x4_lan_lab'} if base_only else {'ego_dlc_terran','x4_lan_lab'})
                self.assertEqual('ego_dlc_terran' in (profile/'content.xml').read_text(), not base_only)
