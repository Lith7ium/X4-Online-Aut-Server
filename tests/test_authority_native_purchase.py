"""Offline provider verification with real UDP and simulated X4 receipts."""
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools/dev')]
from authority.company_purchase_ledger import CompanyPurchaseLedger
from authority_native_purchase import NativePurchaseProvider
from authority_trade_settlement import purchase_settlement, sale_settlement

BINDING = {'registry_epoch': 'e' * 32, 'ship_index': 7354, 'ship_code': 'WRN-302'}
PURCHASE = {'purchase_id': 'a' * 32, 'sector': 'cluster_01_sector001_macro',
            'station_code': 'BIM-169', 'ware': 'water', 'quantity': 6, 'price_cents': 5819}
SALE = {'sale_id': 'a' * 32, 'sector': 'cluster_43_sector001_macro',
        'station_code': 'CTD-093', 'ware': 'water', 'quantity': 2, 'price_cents': 3473}
STOCK = ROOT / 'tools/dev/authority_company_stock_trade.xml'


class NativePurchaseProviderTests(unittest.TestCase):
    def provider(self, **kwargs):
        provider = NativePurchaseProvider(**kwargs)
        self.addCleanup(provider.close)
        return provider

    def test_disposable_overlay_fences_registry_and_loads_ledger_first(self):
        with TemporaryDirectory() as directory:
            provider = self.provider()
            case = Path(directory) / 'case'
            extension = case / 'extensions/x4_lan_lab'
            for folder in ('md', 'ui'):
                (extension / folder).mkdir(parents=True)
            bridge = extension / 'ui/authority_world_bridge_probe.lua'
            manifest = extension / 'ui.xml'
            bridge.write_text(' local ok,reason=bridge:update();assert(ok,reason)\n')
            manifest.write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
            with self.assertRaisesRegex(ValueError, 'registry-aware'):
                provider.install(case)
            bridge.write_text('local registry_checkpoint\n' + bridge.read_text())
            files = provider.install(case)
            self.assertCountEqual(set(files), {
                bridge, manifest, extension / 'md/authority_company_stock_trade.xml',
                extension / 'ui/authority_native_purchase.lua', extension / 'ui/authority_trade_ledger.lua',
            })
            self.assertIn('if registry_checkpoint:ready() then', bridge.read_text())
            self.assertIn('native_purchase_tick(C.IsGamePaused())', bridge.read_text())
            self.assertLess(manifest.read_text().index('authority_trade_ledger'),
                            manifest.read_text().index('authority_native_purchase'))
            cue = extension / 'md/authority_company_stock_trade.xml'
            other = extension / 'md/authority_ship_registry.xml'
            other.write_bytes(b'owned by the registry')
            original = cue.read_bytes()
            cue.write_bytes(original + b'\n')
            with self.assertRaisesRegex(ValueError, 'cue changed'):
                provider.archive_cues(case, case.parent)
            cue.write_bytes(original)
            archive = case.parent / cue.name
            archive.write_bytes(b'pre-existing archive')
            with self.assertRaises(FileExistsError):
                provider.archive_cues(case, case.parent)
            self.assertEqual(cue.read_bytes(), original)
            self.assertEqual(archive.read_bytes(), b'pre-existing archive')
            archive.unlink()
            self.assertEqual(provider.archive_cues(case, case.parent), archive)
            self.assertEqual(archive.read_bytes(), original)
            self.assertFalse(cue.exists())
            self.assertEqual(other.read_bytes(), b'owned by the registry')

    def test_valid_but_unrepresentable_basket_is_refused_before_dispatch(self):
        provider = self.provider()
        oversized_orders = [dict(PURCHASE, quantity=65536), dict(PURCHASE, purchase_id='b' * 32, price_cents=2**31)]
        for purchase in oversized_orders:
            self.assertEqual(provider.begin(BINDING, purchase)['reason'], 'unsupported_purchase')
            self.assertIsNone(provider.poll())

    def test_sale_cannot_be_dispatched_as_a_purchase(self):
        provider = self.provider()
        with self.assertRaisesRegex(ValueError, 'invalid company purchase'):
            provider.begin(BINDING, dict(PURCHASE, side='sell'))
        with self.assertRaisesRegex(ValueError, 'invalid company purchase'):
            provider.begin(BINDING, SALE)
        with self.assertRaisesRegex(ValueError, 'invalid company sale'):
            provider.begin_sale(BINDING, PURCHASE)
        self.assertIsNone(provider.poll())
        self.assertIsNone(provider.begin(BINDING, PURCHASE))

    def test_second_company_ship_can_request_other_stock_and_id_binds_the_basket(self):
        provider = self.provider()
        binding = dict(BINDING, ship_index=1074, ship_code='KHI-630')
        purchase = dict(PURCHASE, sector='cluster_06_sector002_macro', station_code='ABC-X1Z',
                        ware='energycells', quantity=13)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            peer.settimeout(1)
            peer.sendto(f'PURCHASE_HELLO|{provider._lease}'.encode(), provider._socket.getsockname())
            self.assertIsNone(provider.begin(binding, purchase))
            packet, _ = peer.recvfrom(768)
        self.assertEqual(packet.decode().split('|')[3:], [binding['registry_epoch'], '1074', 'KHI-630',
                         'cluster_06_sector002_macro', 'ABC-X1Z', 'energycells', '13', '5819', '-'])
        with self.assertRaisesRegex(ValueError, 'already bound'):
            provider.begin(binding, dict(purchase, quantity=12))
        self.assertIsNone(provider.poll())

    def test_lost_receipt_is_unknown_and_poisoned_without_resend(self):
        clock = [1]
        provider = self.provider(timeout=3, clock=lambda: clock[0])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            peer.settimeout(.05)
            address = provider._socket.getsockname()
            lease = provider._lease
            peer.sendto(f'PURCHASE_HELLO|{lease}'.encode(), address)
            self.assertIsNone(provider.begin(BINDING, PURCHASE))
            sent, _ = peer.recvfrom(768)
            self.assertTrue(sent.startswith(f'PURCHASE|{lease}|{PURCHASE["purchase_id"]}|'.encode()))
            self.assertIsNone(provider.begin(BINDING, PURCHASE))
            with self.assertRaises(TimeoutError):
                peer.recvfrom(768)
            clock[0] = 4
            result = provider.poll()
            self.assertEqual(result, dict(PURCHASE, outcome='unknown'))
            self.assertEqual(provider.evidence(PURCHASE['purchase_id'])['reason'], 'native_receipt_timeout')
            self.assertEqual(provider.begin(BINDING, PURCHASE), result)
            with self.assertRaisesRegex(ValueError, 'awaiting ledger commit'):
                provider.confirm_persisted(result)
            self.assertEqual(provider.begin(BINDING, dict(PURCHASE, purchase_id='b' * 32))['reason'],
                             'provider_limit')
            with self.assertRaises(TimeoutError):
                peer.recvfrom(768)

    def test_verified_native_prestate_survives_timeout(self):
        clock = [1]
        provider = self.provider(timeout=3, clock=lambda: clock[0])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            address = provider._socket.getsockname()
            lease = provider._lease
            purchase_id = PURCHASE['purchase_id']
            peer.sendto(f'PURCHASE_HELLO|{lease}'.encode(), address)
            self.assertIsNone(provider.begin(BINDING, PURCHASE))
            rows = (f'DOCK|{purchase_id}|READY|0x10|0x20|0x30|11.000',
                    f'RESULT|{purchase_id}|quote|5819',
                    f'BEFORE|{purchase_id}|{BINDING["registry_epoch"]}'
                    '|0x10|0x20|0x30|water|6|5819|12|1000000|1000000|11.000')
            for sequence, row in enumerate(rows, 1):
                peer.sendto(f'RECEIPT|{lease}|{purchase_id}|{sequence}|{row}'.encode(), address)
            self.assertIsNone(provider.poll())
            before = provider.evidence(purchase_id)['before']
            self.assertEqual(before['cargo_units'], 12)
            self.assertEqual(before['company_balance_cents'], 1000000)
            clock[0] = 4
            self.assertEqual(provider.poll()['outcome'], 'unknown')
            self.assertEqual(provider.evidence(purchase_id)['before'], before)

    def test_sequence_hole_never_confirms_refusal_or_settlement(self):
        provider = self.provider(timeout=3)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            address = provider._socket.getsockname()
            lease = provider._lease
            id = PURCHASE['purchase_id']
            peer.sendto(f'PURCHASE_HELLO|{lease}'.encode(), address)
            self.assertIsNone(provider.begin(BINDING, PURCHASE))
            peer.sendto(f'RECEIPT|{lease}|{id}|2|DONE'.encode(), address)
            self.assertEqual(provider.poll(), dict(PURCHASE, outcome='unknown'))
            self.assertEqual(provider.evidence(id)['reason'], 'native_receipt_sequence_gap')

    def test_native_captain_required_is_a_clean_refusal(self):
        provider = self.provider()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            address = provider._socket.getsockname()
            lease = provider._lease
            purchase_id = PURCHASE['purchase_id']
            prefix = f'RECEIPT|{lease}|{purchase_id}|'
            peer.sendto(f'PURCHASE_HELLO|{lease}'.encode(), address)
            self.assertIsNone(provider.begin(BINDING, PURCHASE))
            peer.sendto(f'{prefix}1|RESULT|{purchase_id}|captain_required|0'.encode(), address)
            peer.sendto(f'{prefix}2|DONE'.encode(), address)
            self.assertEqual(provider.poll(), dict(PURCHASE, outcome='refused', reason='captain_required'))
            self.assertIsNone(provider.begin(BINDING, dict(PURCHASE, purchase_id='b' * 32)))

    def test_unarmed_monitor_refuses_after_bound_quote_without_locking_provider(self):
        provider = self.provider()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            address = provider._socket.getsockname()
            lease = provider._lease
            purchase_id = PURCHASE['purchase_id']
            peer.sendto(f'PURCHASE_HELLO|{lease}'.encode(), address)
            self.assertIsNone(provider.begin(BINDING, PURCHASE))
            rows = (f'DOCK|{purchase_id}|READY|0x10|0x20|0x30|11.000',
                    f'RESULT|{purchase_id}|quote|5819',
                    f'RESULT|{purchase_id}|provider_limit|5819', 'DONE')
            for sequence, row in enumerate(rows, 1):
                peer.sendto(f'RECEIPT|{lease}|{purchase_id}|{sequence}|{row}'.encode(), address)
            self.assertEqual(provider.poll(), dict(PURCHASE, outcome='refused', reason='provider_limit'))
            self.assertIsNone(provider.begin(BINDING, dict(PURCHASE, purchase_id='b' * 32)))

    @unittest.skipUnless(shutil.which('cc') and shutil.which('luajit'), 'LuaJIT and C compiler required')
    def test_real_udp_luajit_requotes_and_confirms_two_serial_purchases(self):
        with TemporaryDirectory() as directory:
            native = Path(directory) / 'x4lan_net.so'
            ledger = CompanyPurchaseLedger(BINDING['registry_epoch'], Path(directory) / 'ledger.sqlite3')
            self.addCleanup(ledger.close)
            subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', str(ROOT / 'src/native/net.c'),
                            '-ldl', '-o', str(native)], check=True, timeout=10)
            provider = self.provider(timeout=3)
            proc = subprocess.Popen(
                ['luajit', 'tests/authority_native_purchase_peer.lua',
                 str(provider._socket.getsockname()[1]), provider._lease, str(native)], cwd=ROOT)
            try:
                def buy(binding, purchase):
                    now = time.monotonic()
                    result = provider.begin(binding, purchase)
                    while result is None and time.monotonic() - now < 3:
                        time.sleep(.005)
                        result = provider.poll()
                    self.assertIsNotNone(result)
                    return result

                wrong_epoch = dict(BINDING, registry_epoch='f' * 32)
                refused = dict(PURCHASE, purchase_id='c' * 32)
                self.assertEqual(buy(wrong_epoch, refused)['reason'], 'invalid_registry')
                stale = dict(PURCHASE, purchase_id='d' * 32, price_cents=5848)
                self.assertEqual(buy(BINDING, stale),
                                 dict(stale, outcome='refused', reason='price_changed', current_price_cents=5819),
                                 provider.evidence(stale['purchase_id']))
                self.assertTrue(ledger.begin(7354, 'WRN-302', PURCHASE))
                result = buy(BINDING, PURCHASE)
                self.assertEqual(result, dict(PURCHASE, outcome='confirmed', ship_code='WRN-302',
                                              settled_total_cents=34800, company_balance_cents=965200,
                                              cargo_units=18))
                evidence = provider.evidence(PURCHASE['purchase_id'])
                self.assertEqual(evidence['settlement']['quoted_total_cents'], 34914)
                self.assertEqual(provider.begin(BINDING, PURCHASE), result)
                early = dict(PURCHASE, purchase_id='f' * 32)
                self.assertEqual(buy(BINDING, early)['reason'], 'provider_busy')
                with self.assertRaisesRegex(ValueError, 'awaiting ledger commit'):
                    provider.confirm_persisted(dict(result, cargo_units=19))
                provider.confirm_persisted(ledger.finish(PURCHASE['purchase_id'], result))
                second = dict(PURCHASE, purchase_id='b' * 32, ware='energycells', quantity=3, price_cents=2719)
                self.assertTrue(ledger.begin(7354, 'WRN-302', second))
                second_result = buy(BINDING, second)
                self.assertEqual(second_result, dict(second, outcome='confirmed', ship_code='WRN-302',
                                                     settled_total_cents=8100, company_balance_cents=957100,
                                                     cargo_units=8))
                provider.confirm_persisted(ledger.finish(second['purchase_id'], second_result))
                self.assertEqual(provider.begin(BINDING, PURCHASE), result)
                self.assertFalse(ledger.begin(7354, 'WRN-302', PURCHASE))
                self.assertEqual(provider.evidence(second['purchase_id'])['settlement']['ledger']['trade_id'], 76)

                stale_rows = list(evidence['rows'])
                stale_rows[6] = 'LEDGER|observed|-|75|10|' + stale_rows[6].rsplit('|', 1)[1]
                entry = stale_rows[7].split('|')
                entry[2] = '10.5'
                stale_rows[7] = '|'.join(entry)
                self.assertEqual(purchase_settlement(stale_rows, PURCHASE['purchase_id'],
                                 BINDING['registry_epoch'], 'live_purchase', ware=PURCHASE['ware'],
                                 quantity=PURCHASE['quantity'])['reason'], 'ledger_entry_predates_native_binding')
                self.assertIsNone(proc.poll())
            finally:
                proc.terminate()
                proc.wait(timeout=3)

    def test_md_stock_order_requires_physical_dock_binding(self):
        root = ET.parse(STOCK).getroot()
        guards = {node.attrib['name']: node.attrib['exact'] for node in root.iter('set_value')
                  if node.attrib['name'] in ('$owned_docked', '$live_dock', '$fixture_dock')}
        for field in ('dockingbay', '$bay.external', '$bay.container == $station', 'not $bay.isstorage',
                      'not $ship.isinternallystored', '$bay.docked.indexof.{$ship}',
                      '$ship.sector == $station.sector', '$ship.trueowner == faction.player'):
            self.assertIn(field, guards['$live_dock'])
        self.assertIn('$byship.{$ship} == $index', guards['$owned_docked'])
        for fixture_code in ("'WRN-302'", "'BIM-169'"):
            self.assertIn(fixture_code, guards['$fixture_dock'])
            self.assertNotIn(fixture_code, guards['$owned_docked'] + guards['$live_dock'])
        buys = list(root.iter('create_trade_order'))
        self.assertEqual(len(buys), 1)
        request = root.find(".//cue[@name='Request']")
        ordered = list(request.iter())
        observation = next(node for node in request.iter('raise_lua_event')
                           if node.attrib.get('name') == "'X4AuthorityCompanyStockTrade.Before'")
        self.assertLess(ordered.index(observation), ordered.index(buys[0]))
        self.assertEqual(buys[0].attrib['immediate'], 'true')
        self.assertEqual(request.attrib['namespace'], 'this')
        monitor = root.find("./cues/cue[@name='State']/cues/cue[@name='Monitor']")
        completed = monitor.find("./cues/cue[@name='Completed']")
        self.assertIsNone(completed.get('instantiate'))
        completion_guard = completed.find('./conditions/check_value').attrib['value']
        self.assertIn('event.param == State.$purchase.$deal', completion_guard)
        self.assertIn('event.param2 == State.$purchase.$order', completion_guard)
        self.assertIn('@event.param2.trade == State.$purchase.$deal', completion_guard)
        self.assertIn('$provider_lease == @State.$live_lease', completion_guard)
        self.assertIn('$request == @State.$live_request', completion_guard)

        arm = next(node for node in request.iter('do_if')
                   if node.get('value') == '@State.$monitor_ready == $request')
        self.assertEqual(list(arm.iter('create_trade_order')), buys)
        self.assertIsNone(request.find('.//reset_cue'))

        stage = root.find("./cues/cue[@name='State']/cues/cue[@name='PreparePurchase']")
        reset = stage.find('./actions//reset_cue')
        self.assertEqual((reset.tag, reset.get('cue')), ('reset_cue', 'Monitor'))
        self.assertIsNone(stage.find('./actions//signal_cue_instantly'))
        continuation = stage.find("./cues/cue[@name='ArmPreparedMonitor']")
        self.assertIn('@Monitor.state == cuestate.waiting',
                      continuation.find('./conditions/check_value').get('value'))
        self.assertEqual(continuation.find('./actions//signal_cue_instantly').attrib,
                         {'cue': 'Monitor', 'param': '$request'})
        self.assertEqual(list(stage.iter('create_trade_order')), [])

        acknowledged = monitor.find('./actions/do_if')
        self.assertEqual(acknowledged.get('value'), '@State.$purchase.$request == event.param')
        self.assertEqual(acknowledged.find('./set_value').attrib,
                         {'name': 'State.$monitor_ready', 'exact': 'event.param'})
        self.assertEqual(acknowledged.find('.//raise_lua_event').attrib,
                         {'name': "'X4AuthorityCompanyStockTrade.Armed'", 'param': 'event.param'})

        self.assertEqual(request.find('.//event_ui_triggered[@control="\'Sell\'"]').tag, 'event_ui_triggered')
        bid = request.find('.//find_buy_offer')
        self.assertEqual({field: bid.get(field) for field in ('buyer', 'tradepartner', 'space', 'excludemissions')},
                         {'buyer': '$station', 'tradepartner': '$ship', 'space': '$station.sector',
                          'excludemissions': 'true'})
        price_guard = next(node for node in request.iter('do_elseif')
                           if '$submit and $fixed and $quote gt 0' in node.get('value', ''))
        self.assertIn('$v.$price_cents * 1ct != $price', price_guard.get('value'))
        self.assertLess(ordered.index(price_guard), ordered.index(buys[0]))
        sale_completed = monitor.find("./cues/cue[@name='CompletedSale']")
        self.assertEqual(sale_completed.find('./conditions/event_trade_completed').attrib,
                         {'buyer': '@State.$purchase.$station', 'seller': '@State.$purchase.$ship',
                          'ware': '@State.$purchase.$ware'})
        self.assertIn('event.param == State.$purchase.$deal',
                      sale_completed.find('./conditions/check_value').get('value'))
        readback = root.find(".//cue[@name='SaleReadback']")
        self.assertIsNone(readback.find('.//create_trade_order'))

    @unittest.skipUnless(shutil.which('cc') and shutil.which('luajit'), 'LuaJIT and C compiler required')
    def test_real_udp_luajit_sale_requotes_both_ways_and_verifies_actual_credit(self):
        with TemporaryDirectory() as directory:
            native = Path(directory) / 'x4lan_net.so'
            subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', str(ROOT / 'src/native/net.c'),
                            '-ldl', '-o', str(native)], check=True, timeout=10)
            provider = self.provider(timeout=3)
            proc = subprocess.Popen(
                ['luajit', 'tests/authority_native_purchase_peer.lua',
                 str(provider._socket.getsockname()[1]), provider._lease, str(native), 'sale'], cwd=ROOT)
            try:
                def sell(sale):
                    deadline = time.monotonic() + 3
                    result = provider.begin_sale(BINDING, sale)
                    while result is None and time.monotonic() < deadline:
                        time.sleep(.005)
                        result = provider.poll()
                    self.assertIsNotNone(result)
                    return result

                for price, ident in ((3472, 'b' * 32), (3474, 'c' * 32)):
                    stale = dict(SALE, sale_id=ident, price_cents=price)
                    self.assertEqual(sell(stale), dict(stale, outcome='refused', reason='price_changed',
                                                      current_price_cents=3473))
                    rows = provider.evidence(ident)['rows']
                    self.assertFalse(any(row.startswith(('BEFORE|', 'SALE_COMPLETE|')) for row in rows))
                receipt = sell(SALE)
                self.assertEqual(receipt, dict(SALE, outcome='confirmed', ship_code='WRN-302',
                                               credited_cents=6900, company_balance_cents=937000, cargo_units=10))
                self.assertEqual(sell(SALE), receipt)
                self.assertEqual(provider.begin(BINDING, dict(PURCHASE, purchase_id='d' * 32))['reason'],
                                 'provider_busy')
                with self.assertRaisesRegex(ValueError, 'awaiting ledger commit'):
                    provider.confirm_persisted(receipt)
                with self.assertRaisesRegex(ValueError, 'awaiting ledger commit'):
                    provider.confirm_sale_persisted(dict(receipt, credited_cents=6946))
                provider.confirm_sale_persisted(receipt)
                self.assertEqual(sell(SALE), receipt)
                self.assertIsNone(proc.poll())
            finally:
                proc.terminate()
                proc.wait(timeout=3)

            rows = list(provider.evidence(SALE['sale_id'])['rows'])
            def settlement(rows):
                return sale_settlement(rows, SALE['sale_id'], BINDING['registry_epoch'],
                                       ware=SALE['ware'], quantity=SALE['quantity'])

            self.assertEqual(settlement(rows)['matched_sale_entries'][0]['source'], 'ship')
            for index, original, replacement, expected in (
                (4, '|0x20|0x10|', '|0x10|0x20|', None),
                (5, '|2|0|', '|2|1|', None),
                (7, '|3310|', '|3308|', 'sale_cargo_readback_disagrees'),
                (9, '|32|16|', '|16|32|', 'ledger_completed_trade_unconfirmed'),
                (9, '|6900|', '|-6900|', 'ledger_credit_disagrees'),
            ):
                with self.subTest(index=index, replacement=replacement):
                    altered = rows.copy()
                    self.assertIn(original, altered[index])
                    altered[index] = altered[index].replace(original, replacement, 1)
                    if expected is None:
                        with self.assertRaises(ValueError):
                            settlement(altered)
                    else:
                        self.assertEqual(settlement(altered)['reason'], expected)
            altered = rows.copy()
            altered[9] = altered[9].replace('ENTRY|ship|', 'ENTRY|station|', 1)
            self.assertEqual(settlement(altered)['reason'], 'ledger_ship_sale_missing')
            altered = rows.copy()
            altered[2] = altered[2].replace('|3473|12|', '|3473|2|')
            for index in (6, 7):
                altered[index] = altered[index].replace('|10|3310|', '|0|3310|')
            self.assertEqual(settlement(altered)['cargo_units'], 0)
            self.assertTrue(settlement(altered)['verified'])

    def test_dispatched_sale_loss_is_retained_without_resending(self):
        now = [0]
        provider = self.provider(timeout=1, clock=lambda: now[0])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.bind(('127.0.0.1', 0))
            peer.settimeout(1)
            peer.sendto(f'PURCHASE_HELLO|{provider._lease}'.encode(), provider._socket.getsockname())
            self.assertIsNone(provider.begin_sale(BINDING, SALE))
            packet, _ = peer.recvfrom(768)
            self.assertEqual(packet.decode().split('|')[:3], ['SALE', provider._lease, SALE['sale_id']])
            now[0] = 2
            self.assertEqual(provider.poll(), dict(SALE, outcome='unknown'))
            self.assertEqual(provider.begin_sale(BINDING, SALE), dict(SALE, outcome='unknown'))
            with self.assertRaisesRegex(ValueError, 'already bound'):
                provider.begin(BINDING, PURCHASE)
            self.assertEqual(provider.begin_sale(BINDING, dict(SALE, sale_id='b' * 32))['reason'],
                             'provider_limit')
            peer.setblocking(False)
            with self.assertRaises(BlockingIOError):
                peer.recvfrom(768)
