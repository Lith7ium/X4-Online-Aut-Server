import unittest
from unittest.mock import Mock

from authority.company_trade_view import CompanyTradeViews, PAGE_SIZE, view_page


def request(token='a', offset=0):
    return dict(view_id=token * 32, sector='cluster_01_sector001_macro', station_code='BIM-169', offset=offset)


def market_offer(ware):
    return dict(side='buy', tradeable=True, ware=ware, price_cents=5848, amount=100, min_amount=1,
                cargo_units=4, free_units=21)


class CompanyTradeViewTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.provider.begin.return_value = None
        self.provider.poll.return_value = None
        self.binding = Mock(handle=1)
        self.binding.native_value.return_value = dict(registry_epoch='c' * 32, ship_index=7354, ship_code='WRN-302')
        self.bindings = {1: self.binding}
        self.views = CompanyTradeViews(self.provider)

    def step(self):
        self.views.step(self.bindings, can_start=True)

    def test_pages_come_from_one_observation_and_reopen_reads_fresh_account_and_cargo(self):
        self.views.request(self.binding, request())
        self.step()
        self.step()
        self.assertIsNone(self.views.page(self.binding))
        self.provider.begin.assert_called_once_with(dict(self.binding.native_value(), sector=request()['sector'],
                                                       station_code='BIM-169'))
        offers = [market_offer(f'ware_{index}') for index in range(PAGE_SIZE + 1)]
        self.provider.poll.return_value = dict(outcome='observed', company_balance_cents=988400, offers=offers)
        self.step()
        first = view_page(self.views.page(self.binding))
        self.assertEqual(first['offers'], offers[:PAGE_SIZE])
        self.assertEqual(first['total_offers'], len(offers))
        self.views.request(self.binding, request(offset=PAGE_SIZE))
        last = view_page(self.views.page(self.binding))
        self.assertEqual(last['offers'], offers[PAGE_SIZE:])
        self.assertEqual(self.provider.begin.call_count, 1)

        self.views.request(self.binding, request('b'))
        self.assertIsNone(self.views.page(self.binding))
        self.provider.begin.return_value = dict(outcome='observed', company_balance_cents=976800,
            offers=[dict(offers[0], cargo_units=6, free_units=19)])
        self.step()
        fresh = view_page(self.views.page(self.binding))
        self.assertEqual((fresh['view_id'], fresh['company_balance_cents'], fresh['offers'][0]['free_units']),
                         ('b' * 32, 976800, 19))
        self.assertEqual(self.provider.begin.call_count, 2)

    def test_cancel_and_late_pages_cannot_satisfy_a_new_seat(self):
        self.views.request(self.binding, request())
        self.step()
        self.bindings.clear()
        self.step()
        self.provider.cancel.assert_called_once_with()
        self.assertIsNone(self.views.page(self.binding))
        self.bindings[self.binding.handle] = self.binding
        self.views.request(self.binding, request(offset=PAGE_SIZE))
        self.step()
        page = view_page(self.views.page(self.binding))
        self.assertEqual((page['outcome'], page['reason']), ('unavailable', 'reopen_trade_window'))
        self.views.reset()
        self.assertIsNone(self.views.page(self.binding))

    def test_empty_market_is_valid_but_incomplete_or_malformed_pages_are_refused(self):
        empty = dict(request(), outcome='observed', company_balance_cents=0, offers=[], total_offers=0)
        self.assertEqual(view_page(empty), empty)
        for changes in (dict(company_balance_cents=True), dict(offset=8), dict(total_offers=2),
                        dict(total_offers=1, offers=[dict(market_offer('water'), price_cents=1.5)])):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                view_page(dict(empty, **changes))
