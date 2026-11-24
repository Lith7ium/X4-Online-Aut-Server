"""Synthetic boundary checks for the preparation and cold-read receipts."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import authority_company_shuttle_pair as pair


class ShuttleSeedTest(unittest.TestCase):
    def test_only_exact_parked_seed_can_create(self):
        self.assertIsNone(pair.source_expected('prepare', SimpleNamespace(sha256=pair.SOURCE), {}))
        with self.assertRaises(ValueError):
            pair.source_expected('prepare', SimpleNamespace(sha256='0'*64), {})

    def test_cold_overlay_contains_only_inspection(self):
        nonce = 'a'*32
        with tempfile.TemporaryDirectory() as folder:
            lab = Path(folder) / 'extensions/x4_lan_lab'
            (lab/'ui').mkdir(parents=True)
            (lab/'ui/authority_world_bridge_probe.lua').write_text(
                pair.DECLARATION + 'registry_checkpoint = X4LAN_AUTHORITY_REGISTRY_CHECKPOINT_GATE.new({\n'
                + pair.ACTIVATION)
            (lab/'ui.xml').write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
            pair.install(folder, nonce, 'verify', pair._pair(['AAA-111','BBB-222']))
            self.assertEqual([p.name for p in (lab/'md').iterdir()], [pair.INSPECT])
            self.assertFalse((lab/'libraries/loadouts.xml').exists())
            self.assertNotIn('accept_company_shuttle_pair', (lab/'ui/authority_world_bridge_probe.lua').read_text())
            self.assertNotIn('create_ship', (lab/'md'/pair.INSPECT).read_text())

    def test_native_pair_requires_one_receipt_two_distinct_codes(self):
        nonce = 'a'*32
        row = pair.TAG + f'{nonce}|PAIR|AAA-111|BBB-222\n'.encode()
        self.assertEqual(pair.verify_log(None, nonce, lambda *_: row)['ships'][0]['index'], 10083)
        for invalid in (row + row, row.replace(b'BBB-222', b'AAA-111'), row.replace(b'PAIR', b'ERROR')):
            with self.assertRaises(ValueError):
                pair.verify_log(None, nonce, lambda *_: invalid)

    def test_saved_mounts_and_software_required_on_both_ships(self):
        proof = pair._pair(['AAA-111','BBB-222'])
        loadout = pair.ET.parse(pair.LOADOUT).find('./add/loadout')

        def snapshot(_, observe_universe):
            emit = observe_universe
            emit('component', 3, {'class':'sector', 'macro':pair.SECTOR}, None)
            emit('component', 6, {'class':'station', 'code':'XPJ-169'}, None)
            emit('component', 9, {'class':'dockingbay', 'macro':'dockingbay_arg_s_01_macro'}, None)
            emit('component', 12, {'class':'player', 'owner':'player'}, None)
            for desc in proof['ships']:
                emit('component', 6, dict(macro=pair.HULL, code=desc['code'], owner='player',
                     connection='space', thruster='thruster_gen_s_combat_01_mk3_macro',
                     **{'class':'ship_s'}), desc['index'])
                wares = [node.get('ware') for node in loadout.find('software')]
                if desc['index'] == 10084 and missing == 'software':
                    wares.pop()
                emit('software', 7, {'wares':' '.join(wares)}, None)
                for node in loadout.find('macros'):
                    name = node.get('path').removeprefix('../')
                    if desc['index'] == 10083 and missing == name:
                        continue
                    emit('connection', 8, {'connection':name}, None)
                    emit('component', 9, {'class':node.tag, 'macro':node.get('macro')}, None)
            return {'epoch':pair.EPOCH, 'issued_count':10084}

        with patch.object(pair,'verify_join_snapshot', side_effect=snapshot):
            missing = None
            self.assertEqual(len(pair.verify_saved(None, proof)['ships']), 2)
            for missing in ['software', 'con_weapon_06','con_engine_02','con_shield_01']:
                with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'equipment'):
                    pair.verify_saved(None, proof)
