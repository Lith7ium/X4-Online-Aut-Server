"""Exercise the stock MapMenu adapter through its opt-in callback."""
from pathlib import Path
import shutil
import subprocess
import unittest


class CompanyTradeCaptureTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_single_native_menu_confirmation_dispatches_the_pinned_purchase(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_company_trade_capture.lua'],
                       cwd=root, timeout=10, check=True)

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_server_stock_window_uses_stock_rounded_total_and_disables_stale_offers(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_company_trade_view_ui.lua'],
                       cwd=root, timeout=10, check=True)

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_opt_in_stock_sell_is_bounded_and_correlates_sale_receipts(self):
        root = Path(__file__).resolve().parents[1]
        for final_order in ('wrong_id', 'wrong_price', 'negative_cargo', 'price_changed', 'buy'):
            with self.subTest(final_order=final_order):
                subprocess.run(['luajit', 'tests/authority_company_trade_view_ui.lua', 'sale', final_order],
                               cwd=root, timeout=10, check=True)
