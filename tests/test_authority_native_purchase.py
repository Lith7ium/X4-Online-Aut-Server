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
sys.path.insert(0, str(ROOT / 'tools/dev'))
from authority_native_purchase import NativePurchaseProvider
from authority_trade_settlement import purchase_settlement

BINDING = {'registry_epoch': 'e' * 32, 'ship_index': 7354, 'ship_code': 'WRN-302'}
PURCHASE = {'purchase_id': 'a' * 32, 'sector': 'cluster_01_sector001_macro',
            'station_code': 'BIM-169', 'ware': 'water', 'quantity': 6, 'price_cents': 5819}
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

    def test_mismatched_scope_does_not_dispatch_and_id_cannot_change(self):
        provider = self.provider()
        purchase = dict(PURCHASE, station_code='XYZ-999')
        self.assertEqual(provider.begin(BINDING, purchase)['reason'], 'unsupported_purchase')
        with self.assertRaisesRegex(ValueError, 'already bound'):
            provider.begin(BINDING, PURCHASE)
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

    @unittest.skipUnless(shutil.which('cc') and shutil.which('luajit'), 'LuaJIT and C compiler required')
    def test_real_udp_luajit_requotes_and_confirms_only_native_ledger_proof(self):
        with TemporaryDirectory() as directory:
            native = Path(directory) / 'x4lan_net.so'
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
                result = buy(BINDING, PURCHASE)
                self.assertEqual(result, dict(PURCHASE, outcome='confirmed', ship_code='WRN-302',
                                              settled_total_cents=34800, company_balance_cents=965200,
                                              cargo_units=18))
                evidence = provider.evidence(PURCHASE['purchase_id'])
                self.assertEqual(evidence['settlement']['quoted_total_cents'], 34914)
                self.assertEqual(provider.begin(BINDING, PURCHASE), result)
                self.assertEqual(buy(BINDING, dict(PURCHASE, purchase_id='b' * 32))['reason'],
                                 'provider_limit')
                stale_rows = tuple(row.replace('ENTRY|ship|12|', 'ENTRY|ship|10.5|')
                                   .replace('LEDGER|observed|-|75|11|', 'LEDGER|observed|-|75|10|')
                                   for row in evidence['rows'])
                self.assertEqual(purchase_settlement(stale_rows, PURCHASE['purchase_id'],
                                 BINDING['registry_epoch'], 'six-unit', require_absolute_cargo=True,
                                 live=True)['reason'], 'ledger_entry_predates_native_binding')
                self.assertIsNone(proc.poll())
            finally:
                proc.terminate()
                proc.wait(timeout=3)

    def test_md_stock_order_requires_physical_dock_binding(self):
        root = ET.parse(STOCK).getroot()
        guards = {node.attrib['name']: node.attrib['exact'] for node in root.iter('set_value')
                  if node.attrib['name'] in ('$owned_docked', '$live_dock')}
        for field in ('dockingbay', '$bay.container == $station', 'not $bay.isstorage',
                      'not $ship.isinternallystored', '$ship.orders.count == 0',
                      '$ship.sector == $station.sector', '$ship.trueowner == faction.player'):
            self.assertIn(field, guards['$live_dock'])
        for field in ("'WRN-302'", "'BIM-169'", '$byship.{$ship} == 7354'):
            self.assertIn(field, guards['$owned_docked'])
        buys = list(root.iter('create_trade_order'))
        self.assertEqual(len(buys), 1)
        request = root.find(".//cue[@name='Request']")
        ordered = list(request.iter())
        observation = next(node for node in request.iter('raise_lua_event')
                           if node.attrib.get('name') == "'X4AuthorityCompanyStockTrade.Before'")
        self.assertLess(ordered.index(observation), ordered.index(buys[0]))
        self.assertEqual(buys[0].attrib['immediate'], 'true')
        for action in ('dock', 'spawn_docked', 'teleport_player', 'assign_pilot', 'set_object_position'):
            self.assertEqual(list(root.iter(action)), [])
