import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
import re

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
        snapshot = request.find(".//set_value[@name='State.$purchase']")
        self.assertIn('$before_player_money=player.money', snapshot.attrib['exact'])
        snapshot_names = {name for name in re.findall(r'(\$before_[a-z_]+)=', snapshot.attrib['exact'])}

        writes = {
            node.attrib['name']: node.attrib['exact']
            for node in completed.iter('set_value')
        }
        self.assertFalse(snapshot_names & {name.rsplit('.', 1)[-1] for name in writes})
        self.assertEqual(writes['$after_player_money'], '(player.money / 1ct)L')
        self.assertEqual(writes['$delta_player_money'], '((player.money - $entry.$before_player_money) / 1ct)L')

        events = {
            node.attrib['name']: node.attrib['param'].split('.[', 1)[1].rstrip(']').split(',')
            for node in completed.iter('raise_lua_event')
        }
        self.assertEqual(events["'X4AuthorityCompanyStockTrade.Deal'"][14:16],
                         ['($entry.$before_player_money / 1ct)L', '$after_player_money'])
        self.assertEqual(events["'X4AuthorityCompanyStockTrade.Observed'"][6], '$delta_player_money')
        self.assertEqual(writes['$after_ship_cargo'], '$entry.$ship.cargo.{$entry.$ware}.count')
        self.assertEqual(events["'X4AuthorityCompanyStockTrade.Deal'"][-2:],
                         ['$entry.$before_ship_cargo', '$after_ship_cargo'])


class FixedTradeDriverTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_live_bridge_preserves_dynamic_native_binding_and_journal(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_native_purchase_binding.lua'],
                       cwd=root, check=True, timeout=10)

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
                           for event in node.findall('./do_if/raise_lua_event'))]
        refusal, = branches
        self.assertIsNone(refusal.find('.//create_trade_order'))
        proof = next(node.attrib['value'] for node in refusal.findall('do_if')
                     if node.find('raise_lua_event') is not None)
        self.assertIn('$ship.orders.count == $refusal_orders_before', proof)
        self.assertIn('$refusal_cargo_before == $refusal_cargo_after', proof)
        self.assertIn('$refusal_cash_before == $refusal_cash_after', proof)


    def test_fixed_native_quote_and_order_share_the_post_duplicate_lookup(self):
        source = Path(__file__).resolve().parents[1] / 'tools/dev/authority_company_stock_trade.xml'
        cue = ET.parse(source).find(".//cue[@name='Request']")
        actions = cue.find('actions')
        declarations = {node.attrib['name']: node.attrib['exact'] for node in actions.findall('set_value')}
        self.assertEqual(declarations['$sell'], "event.param2 == 'Sell'")
        self.assertEqual(declarations['$submit'], "event.param2 == 'Buy' or $sell")
        live_valid = declarations['$live_request_valid']
        self.assertIn('(not $live and not $sell) or ($live and $fixed', live_valid)
        self.assertIn("($trial == 'live_purchase' and event.param2 == 'Buy')", live_valid)
        self.assertIn("($trial == 'live_sale' and $sell)", live_valid)

        fixture = cue.find(".//do_if[@value='$live_dock or $fixture_dock']")
        branches = fixture.findall('do_elseif')
        live_gate = next(node for node in branches if '@$v.$previous_confirmed' in node.attrib['value'])
        self.assertIn('@State.$live_completed != true', live_gate.attrib['value'])
        self.assertIn('$request == @State.$live_request', live_gate.attrib['value'])
        diagnostic_gate = next(node for node in branches if '@State.$attempted' in node.attrib['value'])
        self.assertEqual(diagnostic_gate.attrib['value'],
                         "event.param2 == 'Buy' and not $live and (@State.$attempted or @State.$refused)")
        lookup = fixture.find('do_else')
        for gate in (live_gate, diagnostic_gate):
            self.assertLess(list(fixture).index(gate), list(fixture).index(lookup))
            for tag in ('find_sell_offer', 'find_buy_offer', 'raise_lua_event', 'create_trade_order'):
                self.assertIsNone(gate.find(f'.//{tag}'))

        bid, = cue.iter('find_buy_offer')
        ask, = cue.iter('find_sell_offer')
        self.assertIs(bid, lookup.find("./do_if[@value='$sell']/find_buy_offer"))
        self.assertIs(ask, lookup.find('./do_else/find_sell_offer'))
        for offer in (bid, ask):
            self.assertEqual(offer.attrib['result'], '$offers')
            self.assertEqual(offer.attrib['tradepartner'], '$ship')
            self.assertEqual(offer.attrib['wares'], '$ware')
        self.assertEqual(bid.attrib['buyer'], '$station')
        self.assertEqual(ask.attrib['seller'], '$station')

        post_lookup = next(node for node in lookup.findall('do_if')
                           if node.attrib['value'].startswith('$offers.count == 1 and '))
        self.assertLess(list(lookup).index(lookup.find('./do_else')), list(lookup).index(post_lookup))
        self.assertIn('$offers.{1}.isbuyoffer', post_lookup.attrib['value'])
        read_offer = post_lookup.find("./set_value[@name='$offer']")
        read_price = post_lookup.find("./set_value[@name='$price']")
        self.assertEqual(read_offer.attrib['exact'], '$offers.{1}')
        self.assertEqual(read_price.attrib['exact'], '$offer.unitprice')
        fixed_quote = post_lookup.find("./do_if[@value='$submit and $fixed and $quote gt 0']")
        self.assertEqual(fixed_quote.find('raise_lua_event').attrib['param'],
                         "'%s|quote|%s'.[$request,$quote]")

        price_recheck = post_lookup.find('do_else')
        self.assertLess(list(post_lookup).index(read_offer), list(post_lookup).index(read_price))
        self.assertLess(list(post_lookup).index(read_price), list(post_lookup).index(fixed_quote))
        self.assertLess(list(post_lookup).index(fixed_quote), list(post_lookup).index(price_recheck))
        quote_only = next(node for node in price_recheck.findall('do_if')
                          if node.attrib['value'] == "event.param2 == 'Quote' and not $fixed and $tradeable")
        self.assertEqual(quote_only.find("./set_value[@name='State.$quoted_price']").attrib['exact'], '$price')
        self.assertIsNone(quote_only.find('.//create_trade_order'))
        submit = price_recheck.find("./do_elseif[@value='$submit and $tradeable']")
        changed = submit.find('do_if')
        self.assertIn('$v.$price_cents * 1ct != $price', changed.attrib['value'])
        self.assertIsNone(changed.find('.//create_trade_order'))
        accepted = submit.find('do_else')
        self.assertLess(list(submit).index(changed), list(submit).index(accepted))
        order, = cue.iter('create_trade_order')
        self.assertIs(order, accepted.find("./do_if[@value='@State.$monitor_ready == $request']/create_trade_order"))
        self.assertEqual(order.attrib['tradeoffer'], '$offer')
        self.assertEqual(order.attrib['price'], '$price')
