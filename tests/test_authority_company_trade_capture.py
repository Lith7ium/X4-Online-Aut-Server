"""Exercise the stock MapMenu adapter through its opt-in callback."""
from contextlib import chdir
from pathlib import Path
import unittest


if __package__:
    from .run_fixture_lua import run_fixture
else:
    from run_fixture_lua import run_fixture


class CompanyTradeCaptureTests(unittest.TestCase):
    def test_single_native_menu_confirmation_dispatches_the_pinned_purchase(self):
        root = Path(__file__).resolve().parents[1]
        with chdir(root):
            run_fixture(root / 'tests/authority_company_trade_capture.lua')

    def test_server_stock_window_uses_stock_rounded_total_and_disables_stale_offers(self):
        root = Path(__file__).resolve().parents[1]
        with chdir(root):
            run_fixture(root / 'tests/authority_company_trade_view_ui.lua')

    def test_opt_in_stock_sell_is_bounded_and_correlates_sale_receipts(self):
        root = Path(__file__).resolve().parents[1]
        with chdir(root):
            for final_order in ('wrong_id', 'wrong_price', 'negative_cargo', 'price_changed', 'buy'):
                with self.subTest(final_order=final_order):
                    run_fixture(root / 'tests/authority_company_trade_view_ui.lua', 'sale', final_order)
