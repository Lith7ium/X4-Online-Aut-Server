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
