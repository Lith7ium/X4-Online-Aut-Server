import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from authority_trade_settlement import (
    live_purchase_before, purchase_settlement, reconcile_observed_purchase, six_unit_settlement,
)


class SixUnitSettlementTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            'START', 'DOCK|request|READY|0x10|0x20|0x30|447.644',
            'RESULT|request|quote|5819', 'RESULT|request|submitted|5819',
            'DEAL|request|epoch|six_unit|6|5819|448.030|455.660|0x10|0x20|water|6|0'
            '|5819|34914|1000000|965200|1|1|1000000|965200',
            'OBSERVED|request|5819|6|-646|-1000000|-1611661312|-34800|1|1',
            'RESULT|request|already_attempted|0', 'LEDGER|observed|-|75|448.4|455.7',
            'ENTRY|ship|455.65|16|32|32|water|6|1|73|75|trade|0|completed_trade'
            '|1|1|-34800|5819|1',
            'DONE',
        ]

    def test_correlated_native_debit_can_differ_from_quote(self):
        self.rows.insert(-1, self.rows[-2].replace('ENTRY|ship|', 'ENTRY|player|'))
        result = six_unit_settlement(self.rows, 'request', 'epoch')
        self.assertTrue(result['verified'])
        self.assertEqual(result['quoted_total_cents'], 34914)
        self.assertEqual(result['nominal_total_cents'], 34914)
        self.assertEqual(result['settled_total_cents'], 34800)
        self.assertEqual(result['settled_minus_nominal_cents'], -114)
        self.assertEqual(len(result['matched_purchase_entries']), 2)
        self.assertEqual(result['records'], self.rows)
        json.dumps(result)

    def test_only_journal_start_may_round_to_the_md_dock_instant(self):
        def settle(start, end='455.7', entry='455.65', dock='447.644'):
            rows = self.rows.copy()
            rows[1] = rows[1].removesuffix('447.644') + dock
            rows[7] = f'LEDGER|observed|-|75|{start}|{end}'
            rows[8] = rows[8].replace('|455.65|', f'|{entry}|')
            return six_unit_settlement(rows, 'request', 'epoch')['reason']

        self.assertEqual(settle('447.64399286'), 'verified')
        self.assertEqual(settle('447.6435'), 'verified')
        self.assertEqual(settle('447.6434999'), 'ledger_window_disagrees')
        self.assertEqual(settle('447.6441', dock='447.6442'), 'ledger_window_disagrees')
        self.assertEqual(settle('447.64399286', end='455.65999999'), 'ledger_window_disagrees')
        self.assertEqual(settle('447.64399286', end='477.64399287'), 'ledger_window_disagrees')
        self.assertEqual(settle('447.64399286', entry='447.64399285'), 'ledger_entry_outside_window')
        self.assertEqual(settle('447.64399286', entry='455.70000001'), 'ledger_entry_outside_window')

    def test_ambiguous_monetary_result_keeps_raw_evidence(self):
        negative = self.rows[-2]
        cases = (
            (self.rows[:7] + ['LEDGER|unknown|unavailable|-|-|-', 'DONE'], 'ledger_unavailable'),
            (self.rows[:-2] + [negative.replace('|455.65|', '|448.4|'), 'DONE'],
             'ledger_entry_outside_window'),
            (self.rows[:4] + [self.rows[4].replace('|5819|34914|', '|5820|34920|')] + self.rows[5:],
             'accepted_unit_price_disagrees'),
            (self.rows[:-2] + [negative.replace('|5819|1', '|5820|1'), 'DONE'],
             'ledger_unit_price_disagrees'),
            (self.rows[:-2] + [negative.replace('|-34800|', '|-34914|'), 'DONE'],
             'ledger_debit_disagrees'),
            (self.rows[:-1] + [negative.replace('|73|', '|74|').replace('ENTRY|ship|', 'ENTRY|player|'),
                              'DONE'], 'ledger_purchase_ambiguous'),
            (self.rows[:4] + [self.rows[4].removesuffix('|965200') + '|965199'] + self.rows[5:],
             'account_balances_disagree'),
        )
        for rows, reason in cases:
            with self.subTest(reason=reason):
                result = six_unit_settlement(rows, 'request', 'epoch')
                self.assertFalse(result['verified'])
                self.assertEqual(result['reason'], reason)
                self.assertIsNone(result['settled_total_cents'])
                self.assertEqual(result['observed_player_debit_cents'], 34800)
                self.assertEqual(result['records'], rows)

    def test_requires_identity_and_order(self):
        cases = (
            self.rows[:4] + self.rows[5:],
            self.rows[:4] + [self.rows[4].replace('|0x10|', '|0x11|')] + self.rows[5:],
            self.rows[:4] + [self.rows[4].replace('|epoch|', '|other|')] + self.rows[5:],
            self.rows[:4] + [self.rows[4].replace('|water|', '|energycells|')] + self.rows[5:],
            self.rows[:6] + self.rows[7:],
        )
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                six_unit_settlement(rows, 'request', 'epoch')


    def test_one_unit_requires_same_native_account_ledger_and_absolute_cargo(self):
        rows = self.rows.copy()
        rows[4] = ('DEAL|request|epoch|one_unit|1|5819|448.030|455.660|0x10|0x20|water|1|0'
                   '|5819|5819|1000000|994200|1|1|1000000|994200|12|13')
        rows[5] = 'OBSERVED|request|5819|1|-646|-1000000|-1611661312|-5800|1|1'
        rows[-2] = 'ENTRY|ship|455.65|16|32|32|water|1|1|73|75|trade|0|completed_trade|1|1|-5800|5819|1'
        result = purchase_settlement(rows, 'request', 'epoch', 'buy', require_absolute_cargo=True)
        self.assertTrue(result['verified'])
        self.assertEqual(result['settled_total_cents'], 5800)
        self.assertEqual(result['settled_minus_nominal_cents'], -19)
        self.assertEqual(result['ship_ware_units_after'], 13)
        self.assertEqual(result['deal']['player_after_ct'], 994200)

        no_ledger = rows[:7] + ['LEDGER|unknown|unavailable|-|-|-', 'DONE']
        self.assertEqual(purchase_settlement(no_ledger, 'request', 'epoch', 'buy')['reason'],
                         'ledger_unavailable')
        incorrect_cargo = rows.copy()
        incorrect_cargo[4] = rows[4].removesuffix('|13') + '|14'
        result = purchase_settlement(incorrect_cargo, 'request', 'epoch', 'buy')
        self.assertEqual(result['reason'], 'ship_cargo_counts_disagree')
        self.assertIsNone(result['ship_ware_units_after'])
        legacy = rows.copy()
        legacy[4] = rows[4].removesuffix('|12|13')
        with self.assertRaisesRegex(ValueError, 'missing native ship cargo'):
            purchase_settlement(legacy, 'request', 'epoch', 'buy', require_absolute_cargo=True)
        with self.assertRaisesRegex(ValueError, 'unbound native deal'):
            six_unit_settlement(rows, 'request', 'epoch')


class LivePurchaseSettlementTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            'DOCK|request|READY|0x10|0x20|0x30|11.000',
            'RESULT|request|quote|2719',
            'BEFORE|request|epoch|0x10|0x20|0x30|energycells|3|2719|12|1000000|1000000|11.000',
            'RESULT|request|submitted|2719',
            'DEAL|request|epoch|live_purchase|3|2719|11.000|13.000|0x10|0x20|energycells|3|0'
            '|2719|8157|1000000|991900|1|1|1000000|991900|12|15',
            'OBSERVED|request|2719|3|0|0|0|-8100|1|1',
            'LEDGER|observed|-|75|10|14',
            'ENTRY|ship|12|16|32|32|energycells|3|1|73|75|trade|0|completed_trade|1|1|-8100|2719|1',
            'DONE',
        ]

    def settle(self, rows):
        return purchase_settlement(rows, 'request', 'epoch', 'live_purchase', ware='energycells', quantity=3)

    def test_verifies_requested_ware_and_quantity_with_stock_debit(self):
        result = self.settle(self.rows)
        self.assertTrue(result['verified'])
        self.assertEqual(result['before']['ware'], 'energycells')
        self.assertEqual(result['before']['quantity'], 3)
        self.assertEqual(result['ship_ware_units_after'], 15)
        self.assertEqual(result['quoted_total_cents'], 8157)
        self.assertEqual(result['settled_total_cents'], 8100)

    def test_rejects_binding_mismatches(self):
        for index, old, new in ((2, '|energycells|', '|water|'), (2, '|3|', '|6|'),
                                (4, '|live_purchase|', '|six_unit|'), (4, '|energycells|', '|water|'),
                                (4, '|live_purchase|3|', '|live_purchase|6|'), (4, '|12|15', '')):
            with self.subTest(index=index, new=new), self.assertRaises(ValueError):
                rows = self.rows.copy()
                rows[index] = rows[index].replace(old, new)
                self.settle(rows)
        parts = [row.split('|') for row in self.rows[:3]]
        with self.assertRaisesRegex(ValueError, 'invalid native purchase request'):
            live_purchase_before(parts, 'request', 'epoch', ware='energycells', quantity=True)

    def test_withholds_confirmation_for_unmatched_cargo_or_ledger(self):
        for index, old, new, reason in (
            (4, '|12|15', '|11|14', 'pre_purchase_snapshot_disagrees'),
            (5, '|3|', '|6|', 'cargo_or_transfer_disagrees'),
            (7, '|energycells|', '|water|', 'ledger_completed_trade_unconfirmed'),
            (7, '|3|', '|6|', 'ledger_completed_trade_unconfirmed'),
            (7, '|16|32|', '|17|32|', 'ledger_completed_trade_unconfirmed'),
            (7, '|-8100|', '|-8157|', 'ledger_debit_disagrees'),
        ):
            with self.subTest(reason=reason, old=old):
                rows = self.rows.copy()
                rows[index] = rows[index].replace(old, new)
                self.assertEqual(self.settle(rows)['reason'], reason)


class NativeEconomicReadbackTests(unittest.TestCase):
    def setUp(self):
        self.before = {
            'registry_epoch': 'e' * 32, 'buyer': '0x10', 'seller': '0x20',
            'ware': 'water', 'quantity': 6, 'price_cents': 5819, 'game_time': '11.000',
            'cargo_units': 12, 'company_balance_cents': 1000000, 'player_balance_cents': 1000000,
        }
        self.after = dict(self.before, game_time='15.000', cargo_units=18,
                          company_balance_cents=965200, player_balance_cents=965200)
        self.rows = [
            'LEDGER|observed|-|75|10|14',
            'ENTRY|ship|12|16|32|32|water|6|1|73|75|trade|0|completed_trade|1|1|-34800|5819|1',
        ]

    def test_already_applied_is_proven_without_completion_receipt(self):
        result = reconcile_observed_purchase(self.before, self.after, self.rows)
        self.assertEqual(result['status'], 'already_applied')
        self.assertEqual(result['settled_total_cents'], 34800)
        self.assertEqual(result['trade_id'], 75)

    def test_reconciles_requested_ware_and_quantity(self):
        before = dict(self.before, ware='energycells', quantity=3, price_cents=2719)
        after = dict(self.after, ware='energycells', cargo_units=15,
                     company_balance_cents=991900, player_balance_cents=991900)
        rows = [self.rows[0], self.rows[1].replace('|water|6|', '|energycells|3|')
                .replace('|-34800|5819|', '|-8100|2719|')]
        self.assertEqual(reconcile_observed_purchase(before, after, rows)['settled_total_cents'], 8100)
        self.assertEqual(reconcile_observed_purchase(before, dict(after, ware='water'), rows)['reason'],
                         'native_binding_disagrees')

    def test_ambiguity_does_not_assert_no_effect(self):
        other_debit = self.rows[1].replace('|73|', '|74|').replace('|16|32|32|', '|0|32|32|')
        other_debit = other_debit.replace('completed_trade|1|1|', 'matching_account_or_order_event|0|1|')
        cases = [
            (None, self.after, self.rows, 'native_pre_purchase_snapshot_unavailable'),
            (self.before, self.after, [], 'native_journal_unavailable'),
            (self.before, self.after, self.rows[:1], 'ledger_completed_trade_unconfirmed'),
            (self.before, dict(self.after, buyer='0x11'), self.rows, 'native_binding_disagrees'),
            (self.before, dict(self.after, cargo_units=12), self.rows, 'native_cargo_delta_disagrees'),
            (self.before, dict(self.after, game_time='13'), self.rows, 'native_ledger_after_observation'),
            (self.before, self.after, [*self.rows, other_debit], 'ledger_purchase_ambiguous'),
        ]
        for before, after, rows, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(reconcile_observed_purchase(before, after, rows),
                                 {'status': 'no_proven_economic_delta', 'reason': reason})
