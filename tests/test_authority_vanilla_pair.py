"""Fresh fighter receipts discover station identity and keep the cold overlay read-only."""
from pathlib import Path
import tempfile
import unittest

import authority_vanilla_pair as pair


class VanillaPairTest(unittest.TestCase):
    nonce = '1'*32

    def proof(self):
        rows = pair.TAG + (self.nonce + '|PAIR|AAA-111|BBB-222|ZZZ-999|dockingbay_arg_s_01_macro').encode()
        return pair.verify_genesis_log(None, self.nonce, lambda *_: rows)

    def test_dynamic_station_and_noncontiguous_issued_indices(self):
        proof = self.proof()
        self.assertEqual(proof['station'], 'ZZZ-999')
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)
            lab = case/'extensions/x4_lan_lab'
            (lab/'ui').mkdir(parents=True)
            (lab/'ui.xml').write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
            (lab/'ui/authority_world_bridge_probe.lua').write_text(pair.DECLARATION + pair.ACTIVATION)
            pair.install_cold(case, None, self.nonce, proof,
                              [{'code':'AAA-111', 'index':147}, {'code':'BBB-222', 'index':36}])
            self.assertEqual([p.name for p in (lab/'md').iterdir()], [pair.INSPECT])
            config = (lab/'ui/authority_vanilla_pair.lua').read_text()
            self.assertIn('indices={147,36}', config)
            self.assertIn('station="ZZZ-999"', config)
            self.assertNotIn('create_ship', (lab/'md'/pair.INSPECT).read_text())

    def test_four_fighter_receipt_requires_group_and_exact_distinct_codes(self):
        codes = ['AAA-111', 'BBB-222', 'CCC-333', 'DDD-444']

        def verify(kind, reported, **options):
            fields = [self.nonce, kind, *reported, 'ZZZ-999', 'dockingbay_arg_s_01_macro']
            return pair.verify_genesis_log(None, self.nonce,
                                          lambda *_: pair.TAG + '|'.join(fields).encode(),
                                          **options)

        self.assertEqual(verify('GROUP', codes, fighter_count=4)['codes'], codes)
        with self.assertRaises(ValueError):
            verify('GROUP', codes)
        for kind, reported in [('PAIR', codes), ('GROUP', codes[:2]), ('GROUP', codes[:3] + codes[:1])]:
            with self.subTest(kind=kind, reported=reported), self.assertRaises(ValueError):
                verify(kind, reported, fighter_count=4)

    def test_four_fighter_installers_pin_genesis_and_cold_indices(self):
        codes = ['AAA-111', 'BBB-222', 'CCC-333', 'DDD-444']
        for mode, indices in [('prepare', [1, 2, 3, 4]), ('verify', [147, 36, 99, 89])]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                case = Path(temp)
                lab = case/'extensions/x4_lan_lab'
                (lab/'ui').mkdir(parents=True)
                (lab/'ui.xml').write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
                (lab/'ui/authority_world_bridge_probe.lua').write_text(pair.DECLARATION + pair.ACTIVATION)
                if mode == 'prepare':
                    pair.install_genesis(case, None, self.nonce, fighter_count=4)
                else:
                    ships = [dict(code=code, index=index) for code, index in zip(codes, indices, strict=True)]
                    pair.install_cold(case, None, self.nonce, self.proof() | {'codes': codes}, ships)
                config = (lab/'ui/authority_vanilla_pair.lua').read_text()
                self.assertIn('indices={' + ','.join(map(str, indices)) + '}', config)
                self.assertEqual((lab/'md'/pair.CREATE).exists(), mode == 'prepare')

    def test_rejects_duplicate_codes_and_invalid_station(self):
        for changes in ({'codes':['AAA-111','AAA-111']}, {'station':None}, {'room':''}, {'sector':'other'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pair._checked(self.proof() | changes)

    def test_all_pulsar_mounts_populated(self):
        loadout = pair.ET.parse(pair.LOADOUT).find('./add/loadout')
        self.assertEqual(loadout.get('macro'), 'ship_arg_s_fighter_03_a_macro')
        mounts = loadout.find('macros')
        self.assertEqual({node.get('path') for node in mounts.findall('weapon')},
                         {'../con_weapon_' + tail for tail in ('01','002','003','004','005','006')})
        self.assertEqual(len(mounts.findall('engine')), 3)
        self.assertEqual(len(mounts.findall('shield')), 1)
