import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from authority_company_trade_trial import verify_settlement


class StockTradeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.request = 'test-request'
        self.rows = [
            'START', 'DOCK|test-request|READY|ship|station|bay|22',
            'RESULT|test-request|quote|5877', 'RESULT|test-request|submitted|5877',
            'OBSERVED|test-request|5877|1|-1|-5877|0|-5877|true|true',
            'RESULT|test-request|already_attempted|0', 'DONE',
        ]

    def test_requires_correlated_stock_cargo_and_company_debit(self):
        result = verify_settlement(self.rows, self.request)
        self.assertEqual((result['ship_cargo_delta'], result['station_cargo_delta'],
                          result['company_money_delta_cents']), (1, -1, -5877))
        self.assertEqual(result['station_money_delta_cents'], 0)
        original = self.rows[4].split('|')
        for field, invalid in [(1, 'other'), (2, '6000'), (3, '0'), (4, '0'),
                               (7, '0'), (8, 'false'), (9, 'false')]:
            with self.subTest(field=field):
                receipt = original.copy()
                receipt[field] = invalid
                rows = self.rows.copy()
                rows[4] = '|'.join(receipt)
                with self.assertRaises(ValueError):
                    verify_settlement(rows, self.request)

    def test_rejects_submission_without_settlement_or_duplicate_refusal(self):
        for index in (4, 5):
            with self.subTest(index=index), self.assertRaises(ValueError):
                verify_settlement(self.rows[:index] + self.rows[index + 1:], self.request)
