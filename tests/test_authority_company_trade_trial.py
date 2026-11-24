import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from authority_company_trade_trial import install


class StockTradeInstallTests(unittest.TestCase):
    def test_both_purchase_modes_stage_the_native_ledger_and_driver(self):
        for mode, stock_mode in (('buy', 'one_unit'), ('six-unit', 'six_unit')):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                case = Path(directory)
                lab = case / 'extensions/x4_lan_lab'
                (lab / 'md').mkdir(parents=True)
                (lab / 'ui').mkdir()
                bridge = lab / 'ui/authority_world_bridge_probe.lua'
                bridge.write_text(' local ok,reason=bridge:update();assert(ok,reason)\n')
                manifest = lab / 'ui.xml'
                manifest.write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
                files = install(case, mode, 'epoch', 'request')
                self.assertIn(lab / 'ui/authority_trade_ledger.lua', files)
                self.assertIn(lab / 'ui/authority_company_trade_driver.lua', files)
                declaration = manifest.read_text()
                self.assertLess(declaration.index('authority_trade_ledger'),
                                declaration.index('authority_company_trade_driver'))
                self.assertIn(f'new("epoch", "request", "{stock_mode}")', bridge.read_text())


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
        self.assertEqual(writes['$after_ship_cargo'], 'State.$ship.cargo.{State.$ware}.count')
        self.assertEqual(events["'X4AuthorityCompanyStockTrade.Deal'"][-2:],
                         ['State.$before_ship_cargo', '$after_ship_cargo'])


class FixedTradeDriverTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_native_refusal_and_success_sequences_use_original_price(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_company_fixed_trade.lua'],
                       cwd=root, check=True, timeout=10)

    def test_native_refusal_branch_cannot_book_an_order(self):
        source = Path(__file__).resolve().parents[1] / 'tools/dev/authority_company_stock_trade.xml'
        document = ET.parse(source)
        branches = [node for node in document.iter('do_elseif')
                    if any(event.attrib['name'] == "'X4AuthorityCompanyStockTrade.Refused'"
                           for event in node.iter('raise_lua_event'))]
        refusal, = branches
        self.assertIsNone(refusal.find('.//create_trade_order'))
        proof = refusal.find('do_if').attrib['value']
        self.assertIn('$ship.orders.count == $refusal_orders_before', proof)
        self.assertIn('$refusal_water_before == $refusal_water_after', proof)
        self.assertIn('$refusal_cash_before == $refusal_cash_after', proof)
