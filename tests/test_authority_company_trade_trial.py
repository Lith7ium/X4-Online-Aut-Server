import sys
import unittest
import xml.etree.ElementTree as ET
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


class StockTradeReceiptSourceTests(unittest.TestCase):
    def test_player_balance_snapshot_cannot_alias_completion_delta(self):
        source = Path(__file__).resolve().parents[1] / 'tools/dev/authority_company_stock_trade.xml'
        document = ET.parse(source)
        request = document.find(".//cue[@name='Request']")
        completed = document.find(".//cue[@name='Completed']")
        snapshot = request.find(".//set_value[@name='State.$before_player_money']")
        self.assertEqual(snapshot.attrib['exact'], 'player.money')

        snapshot_names = {
            node.attrib['name'].removeprefix('State.')
            for node in request.iter('set_value')
            if node.attrib['name'].startswith('State.$before_')
        }
        writes = {
            node.attrib['name'].removeprefix('State.'): node.attrib['exact']
            for node in completed.iter('set_value')
        }
        self.assertFalse(snapshot_names & writes.keys())
        self.assertEqual(writes['$after_player_money'], '(player.money / 1ct)L')
        self.assertEqual(writes['$delta_player_money'], '((player.money - State.$before_player_money) / 1ct)L')

        events = {
            node.attrib['name']: node.attrib['param'].split('.[', 1)[1].rstrip(']').split(',')
            for node in completed.iter('raise_lua_event')
        }
        self.assertEqual(events["'X4AuthorityCompanyStockTrade.Deal'"][14:16],
                         ['(State.$before_player_money / 1ct)L', '$after_player_money'])
        self.assertEqual(events["'X4AuthorityCompanyStockTrade.Observed'"][6], '$delta_player_money')
