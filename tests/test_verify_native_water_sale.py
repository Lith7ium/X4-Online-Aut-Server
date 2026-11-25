from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from verify_native_water_sale import SaleRequest, verify, verify_settlement


def recorded_sale():
    path = Path(__file__).with_name('fixtures') / 'native_water_sale_settlement.json'
    return json.loads(path.read_text(encoding='utf-8'))


class VerifyNativeWaterSaleTests(unittest.TestCase):
    request = SaleRequest(0x373bf, 0x26f3, 'water', 2, 3473)

    def test_returns_observed_credit_and_post_trade_state(self):
        recording = recorded_sale()
        settlement = verify_settlement(recording, self.request)
        self.assertEqual(settlement.credited_cents, 6900)
        self.assertEqual(settlement.quantity, 2)
        self.assertEqual(settlement.cargo_units, 10)
        self.assertEqual(settlement.company_balance_cents, 937000)
        self.assertEqual(settlement.journal_trade_id, 5)
        self.assertEqual(verify(recording),
                         'Verified 2 water from ship 0x373bf to station 0x26f3. '
                         'Order 0x9a6d, deal 0x9a6c, journal trade 5, ship entry 14. '
                         'Company credit +6900 cents. Quoted total 6946 cents.')

    def test_rejects_recording_for_another_request(self):
        recording = recorded_sale()
        for field, value, message in (
            ('ship_id', 0x11, 'recorded ship'),
            ('station_id', 0x21, 'recorded station'),
            ('quantity', 1, 'recorded quantity'),
            ('unit_price_cents', 204, 'recorded unit price'),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                verify_settlement(recording, replace(self.request, **{field: value}))
        with self.assertRaisesRegex(ValueError, 'only supports water'):
            replace(self.request, ware='ore')

    def test_rejects_journal_credit_equal_to_quote_but_not_balance_delta(self):
        recording = recorded_sale()
        rows = recording['native_water_sale']['rows']
        for index, row in enumerate(rows):
            if row.startswith('JOURNAL|'):
                rows[index] = row.replace('|6900|true', '|6946|true')
        with self.assertRaisesRegex(ValueError, 'signed credit differs'):
            verify_settlement(recording, self.request)
