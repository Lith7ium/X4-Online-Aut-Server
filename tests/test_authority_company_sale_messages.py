import unittest

from authority.company_trade_messages import purchase_value, sale_receipt, sale_value


class SaleMessages(unittest.TestCase):
    order = {'sale_id': 'a' * 32, 'sector': 'cluster_43_sector001_macro',
             'station_code': 'CTD-093', 'ware': 'water', 'quantity': 2, 'price_cents': 3473}

    def test_directions_are_distinct(self):
        self.assertEqual(sale_value(self.order), self.order)
        with self.assertRaises(ValueError):
            purchase_value(self.order)
        with self.assertRaises(ValueError):
            sale_value(dict(self.order, purchase_id='b' * 32))

    def test_stock_credit_can_differ_from_quote_and_final_cargo_can_be_empty(self):
        receipt = dict(self.order, outcome='confirmed', ship_code='WRN-302',
                       credited_cents=6900, company_balance_cents=937000, cargo_units=0)
        self.assertEqual(sale_receipt(receipt), receipt)
        with self.assertRaises(ValueError):
            sale_receipt(dict(receipt, credited_cents=-6900))
        with self.assertRaises(ValueError):
            sale_receipt(dict(receipt, settled_total_cents=6946))

    def test_requote_in_either_direction(self):
        refusal = dict(self.order, outcome='refused', reason='price_changed')
        for price in (3472, 3474):
            changed = dict(refusal, current_price_cents=price)
            self.assertEqual(sale_receipt(changed), changed)
        with self.assertRaises(ValueError):
            sale_receipt(dict(refusal, current_price_cents=self.order['price_cents']))
