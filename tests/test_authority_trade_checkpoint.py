"""Trade checkpoint evidence follows the registered ship's own cargo node."""
from copy import deepcopy
import unittest

from authority_trade_checkpoint import TradeSaveEvidence, compare_verified_trade


class TradeSaveEvidenceTest(unittest.TestCase):
    def observe_ship(self, *, dock=False):
        evidence = TradeSaveEvidence()
        evidence.observe('component', 3, {'class': 'sector', 'macro': 'cluster_01_sector001_macro'}, None)
        if dock:
            evidence.observe('component', 6, {'class': 'station', 'code': 'BIM-169', 'owner': 'teladi'}, None)
            evidence.observe('component', 9, {'class': 'dockingbay'}, None)
        else:
            evidence.observe('component', 9, {'class': 'zone'}, None)
        evidence.observe('component', 12, {
            'class': 'ship_s', 'macro': 'ship_arg_s_fighter_02_a_macro', 'owner': 'player',
            'code': 'WRN-302', 'connection': 'dock' if dock else 'space',
        }, 7354)
        return evidence

    def test_nested_cargo_is_ignored_and_dock_parent_is_recorded(self):
        evidence = self.observe_ship(dock=True)
        evidence.observe('connections', 13, {}, None)
        evidence.observe('component', 15, {'class': 'ship_s'}, None)
        evidence.observe('cargo', 16, {}, None)
        evidence.observe('ware', 17, {'ware': 'water', 'amount': '99'}, None)
        self.assertEqual(evidence.ships[7354]['cargo_encoding'], 'no_cargo_node')
        self.assertEqual(evidence.ships[7354]['dock']['station_code'], 'BIM-169')
        evidence.observe('cargo', 13, {}, None)
        self.assertEqual(evidence.ships[7354]['cargo_encoding'], 'no_ware_row')
        evidence.observe('ware', 14, {'ware': 'water', 'amount': '0'}, None)
        self.assertEqual(evidence.ships[7354]['cargo_encoding'], 'explicit_ware_row')
        self.assertEqual(evidence.ships[7354]['cargo_units'], 0)
        with self.assertRaisesRegex(ValueError, 'duplicate water'):
            evidence.observe('ware', 14, {'ware': 'water', 'amount': '2'}, None)

    def test_malformed_relevant_ware_does_not_become_zero(self):
        evidence = self.observe_ship()
        evidence.observe('cargo', 13, {}, None)
        with self.assertRaisesRegex(ValueError, 'invalid saved cargo amount'):
            evidence.observe('ware', 14, {'ware': 'water'}, None)

    def test_player_account_distinguishes_absent_and_explicit_zero(self):
        for attrs, expected in [({'id': '[0x14c]'}, None), ({'id': '[0x14c]', 'amount': '0'}, 0)]:
            with self.subTest(attrs=attrs):
                evidence = TradeSaveEvidence()
                evidence.observe('factions', 3, {}, None)
                evidence.observe('faction', 4, {'id': 'player'}, None)
                evidence.observe('account', 5, attrs, None)
                self.assertEqual(evidence.account['amount'], attrs.get('amount'))
                self.assertEqual(evidence.account['amount_cents'], expected)

    def test_player_account_only_accepts_exact_cents(self):
        for raw, cents in [('10000', 1000000), ('9941.23', 994123), ('3.1', 310), ('3.1200', 312)]:
            with self.subTest(amount=raw):
                evidence = TradeSaveEvidence()
                evidence.observe('factions', 3, {}, None)
                evidence.observe('faction', 4, {'id': 'player'}, None)
                evidence.observe('account', 5, {'id': '[0x14c]', 'amount': raw}, None)
                self.assertEqual(evidence.account['amount'], raw)
                self.assertEqual(evidence.account['amount_cents'], cents)
        for attrs in ({'amount': '1.001'}, {'amount': '1e2'}, {'amount': '1', 'subamount': '5'}):
            with self.subTest(attrs=attrs):
                evidence = TradeSaveEvidence()
                evidence.observe('factions', 3, {}, None)
                evidence.observe('faction', 4, {'id': 'player'}, None)
                with self.assertRaises(ValueError):
                    evidence.observe('account', 5, {'id': '[0x14c]', **attrs}, None)

    def test_compares_docked_buy_against_native_receipt(self):
        before = {
            'sha256': 'before', 'marker': 'old', 'registry_epoch': 'shared', 'ware': 'water',
            'account': {'amount_cents': 1000000}, 'avatar': {'station': 'XPJ-169'},
            'seller': {'sector': 'Grand Exchange I', 'code': 'BIM-169'},
            'ships': [{'index': 1970, 'cargo_units': 0},
                      {'index': 7354, 'cargo_units': 0, 'sector': 'Grand Exchange I', 'dock': None}],
        }
        after = deepcopy(before)
        after.update(sha256='after', marker='new', account={'amount_cents': 994123})
        after['ships'][1].update(cargo_units=1, dock={'station_code': 'BIM-169'})
        self.assertEqual(compare_verified_trade(before, after, quantity=1, debit_cents=5877)['added_units'], 1)
        with self.assertRaisesRegex(ValueError, 'debit'):
            compare_verified_trade(before, after, quantity=1, debit_cents=5876)
        after['ships'][0]['cargo_units'] = 1
        with self.assertRaisesRegex(ValueError, 'cargo delta'):
            compare_verified_trade(before, after, quantity=1, debit_cents=5877)
