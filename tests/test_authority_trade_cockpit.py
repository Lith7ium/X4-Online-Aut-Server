"""A quote read in X4 and a player confirmation are distinct observations."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from authority_trade_cockpit import result_from_rows


class TradeCockpitEvidenceTests(unittest.TestCase):
    binding = 'INSPECT|READY|0x11|0x22|0x33|0x44|0|0|0x55'
    quote = 'OFFER|5820|32241|1|1'
    controlled = 'VERIFY|CONTROLLED|0x11|0x22|0x33|0x44|0x11|0x11|0x11'
    intent = 'INTENT|cluster_01_sector001_macro|BIM-169|water|6|5820'

    def test_reads_actual_ship_quote_after_binding(self):
        read = result_from_rows([self.binding, self.quote, 'DONE|offer_read'], 'offer')
        self.assertEqual(read['unit_price_cents'], 5820)
        self.assertFalse(read['client_intent_captured'])
        with self.assertRaises(ValueError):
            result_from_rows([self.quote, self.binding, 'DONE|offer_read'], 'offer')

    def test_confirm_requires_control_and_the_intended_purchase(self):
        steps = [self.binding, self.quote, 'PERMISSION|research|granted',
                 'TAKE|DISPATCHED|0x11|0x22|0x33|0x44|0|0|0x55']
        rows = [*steps, self.controlled, self.intent, 'DONE|intent_captured']
        self.assertTrue(result_from_rows(rows, 'cockpit')['client_intent_captured'])
        for mutation in (
            [*steps, self.intent, rows[-1]],
            [*steps, self.controlled.replace('0x44', '0x78'), self.intent, rows[-1]],
            [*steps, self.controlled, self.intent.replace('BIM-169', 'XPJ-169'), rows[-1]],
        ):
            with self.subTest(rows=mutation), self.assertRaises(ValueError):
                result_from_rows(mutation, 'cockpit')
