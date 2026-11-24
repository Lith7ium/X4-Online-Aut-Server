import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from authority_trade_settlement import purchase_settlement, six_unit_settlement


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
