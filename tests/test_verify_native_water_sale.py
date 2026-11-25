from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from verify_native_water_sale import SaleRequest, verify, verify_settlement


def synthetic_recording():
    """Construct test input for the verifier without running X4."""
    passive = ('|ship=0x10|station=0x20|deal=0x30|bay=0x50|identity=1|berth=1'
               '|water=3|station_water_delta=2|company_ct=1400|faction_ct=1400'
               '|credit_ct=400|ship_credit_ct=400|current=Wait|order_exists=0|queue=0|tradeorders=0')
    return {'success': True, 'native_water_sale': {'status': 'CONFIRMED', 'rows': [
        'BERTH_QUOTE|price_ct=205|bay=0x50',
        'ATTEMPTED|ship=0x10|station=0x20|quantity=2|water=5|money_ct=1000|ship_ct=1000|price_ct=205',
        'ORDER|order=0x40|deal=0x30',
        'EVENT|buyer=0x20|seller=0x10|order=0x40|deal=0x30|param=0x30|matches=1',
        'PASSIVE|age_s=1|bound=1|transferred=2|destroyed=0' + passive,
        'PASSIVE|age_s=2' + passive,
        'PASSIVE|age_s=3' + passive,
        'LEDGER|observed|nil|60',
        'JOURNAL|ship|61|60|completed_trade|32|16|water|2|205|400|true',
        'CONFIRMED|60',
    ]}}


class VerifyNativeWaterSaleTests(unittest.TestCase):
    request = SaleRequest(0x10, 0x20, 'water', 2, 205)

    def test_returns_observed_credit_and_post_trade_state(self):
        recording = synthetic_recording()
        settlement = verify_settlement(recording, self.request)
        self.assertEqual(settlement.credited_cents, 400)
        self.assertEqual(settlement.quantity, 2)
        self.assertEqual(settlement.cargo_units, 3)
        self.assertEqual(settlement.company_balance_cents, 1400)
        self.assertEqual(settlement.journal_trade_id, 60)
        self.assertEqual(verify(recording),
                         'Verified 2 water from ship 0x10 to station 0x20. '
                         'Order 0x40, deal 0x30, journal trade 60, ship entry 61. '
                         'Company credit +400 cents. Quoted total 410 cents.')

    def test_rejects_recording_for_another_request(self):
        recording = synthetic_recording()
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
        recording = synthetic_recording()
        rows = recording['native_water_sale']['rows']
        rows[-2] = rows[-2].replace('|400|', '|410|')
        with self.assertRaisesRegex(ValueError, 'signed credit differs'):
            verify_settlement(recording, self.request)

    @unittest.skipUnless(os.environ.get('X4LAN_RECORDED_WATER_SALE'),
                         'set X4LAN_RECORDED_WATER_SALE to a copy of water-sale-native-04/result.json')
    def test_recorded_native_diagnostic(self):
        path = Path(os.environ['X4LAN_RECORDED_WATER_SALE'])
        recording = json.loads(path.read_text(encoding='utf-8'))
        settlement = verify_settlement(recording, SaleRequest(0x373bf, 0x26f3, 'water', 2, 3473))
        self.assertEqual((settlement.credited_cents, settlement.cargo_units,
                          settlement.company_balance_cents, settlement.journal_trade_id),
                         (6900, 10, 937000, 5))
