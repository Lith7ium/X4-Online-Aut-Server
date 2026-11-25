"""Exercise the provider survey without launching X4."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools/dev')]
from authority_native_market_survey import NativeMarketSurveyProvider
from authority_recovered_loopback_server import redact_recovery_config
from linux_gate_live import start_ticks

BINDING = dict(registry_epoch='e' * 32, ship_index=7354, ship_code='WRN-302',
               station_code='BIM-169', sector='cluster_01_sector001_macro')


class NativeMarketSurveyTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.guard = True
        self.provider = NativeMarketSurveyProvider(timeout=2, clock=lambda: self.now)
        self.addCleanup(self.provider.close)
        pid = os.getpid()
        exe = Path(f'/proc/{pid}/exe').stat()
        self.provider.bind(dict(pid=pid, start_ticks=start_ticks(pid), exe_dev=exe.st_dev,
                                exe_ino=exe.st_ino), lambda: self.guard)
        self.lease, port = self.provider.lua_settings
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.peer.close)
        self.peer.connect(('127.0.0.1', port))
        self.peer.settimeout(1)
        self.peer.send(f'MARKET_HELLO|{self.lease}'.encode())

    def begin(self):
        self.assertIsNone(self.provider.begin(BINDING))
        open_kind, lease, nonce, serial, *binding = self.peer.recv(768).decode().split('|')
        self.assertEqual((open_kind, lease), ('MARKET_OPEN', self.lease))
        self.assertEqual(binding, [BINDING[key] if key != 'ship_index' else '7354' for key in (
            'registry_epoch', 'ship_index', 'ship_code', 'sector', 'station_code')])
        return nonce, int(serial)

    def reply(self, kind, nonce, *fields):
        packet = '|'.join((f'MARKET_{kind}', self.lease, nonce, *fields)).encode()
        self.peer.send(packet)
        return self.provider.poll()

    def read(self, nonce, offset):
        self.assertEqual(self.peer.recv(768).decode(),
                         f'MARKET_READ|{self.lease}|{nonce}|{offset}')

    def test_paged_market_preserves_same_ware_offers_and_exact_cents(self):
        nonce, _ = self.begin()
        self.assertIsNone(self.reply('HEADER', nonce, '976800', '3'))
        self.read(nonce, 0)
        self.assertIsNone(self.reply('PAGE', nonce, '0',
                                     'buy,water,5848,80,1,4,21,1', 'buy,water,5901,120,1,4,21,0'))
        self.read(nonce, 2)
        self.assertIsNone(self.reply('PAGE', nonce, '0', 'buy,water,5848,80,1,4,21,1'))
        self.now += .25
        self.assertIsNone(self.provider.poll())
        self.read(nonce, 2)
        value = self.reply('PAGE', nonce, '2', 'sell,water,4450,10,2,4,21,1')
        self.assertEqual(value, {'outcome': 'observed', 'company_balance_cents': 976800, 'offers': [
            {'side': 'buy', 'ware': 'water', 'price_cents': 5848,
             'amount': 80, 'min_amount': 1, 'cargo_units': 4, 'free_units': 21, 'tradeable': True},
            {'side': 'buy', 'ware': 'water', 'price_cents': 5901,
             'amount': 120, 'min_amount': 1, 'cargo_units': 4, 'free_units': 21, 'tradeable': False},
            {'side': 'sell', 'ware': 'water', 'price_cents': 4450,
             'amount': 10, 'min_amount': 2, 'cargo_units': 4, 'free_units': 21, 'tradeable': True},
        ]})

    def test_cancel_fences_partial_snapshot_and_late_replies(self):
        first, serial = self.begin()
        self.assertIsNone(self.reply('HEADER', first, '976800', '2'))
        self.read(first, 0)
        self.provider.cancel()
        self.assertIsNone(self.reply('PAGE', first, '0', 'buy,water,5848,80,1,4,21,1'))
        second, new_serial = self.begin()
        self.assertNotEqual(second, first)
        self.assertEqual(new_serial, serial + 1)
        self.assertIsNone(self.reply('HEADER', first, '976800', '0'))
        self.assertEqual(self.reply('HEADER', second, '960000', '0'),
                         {'outcome': 'observed', 'company_balance_cents': 960000, 'offers': []})

    def test_refusal_malformed_page_limits_and_expiry_are_terminal(self):
        nonce, _ = self.begin()
        self.assertEqual(self.reply('FAIL', nonce, 'dock_mismatch'),
                         {'outcome': 'refused', 'reason': 'dock_mismatch'})
        nonce, _ = self.begin()
        self.assertEqual(self.reply('HEADER', nonce, '976800', '513'),
                         {'outcome': 'unavailable', 'reason': 'invalid_observation'})
        nonce, _ = self.begin()
        self.assertIsNone(self.reply('HEADER', nonce, '976800', '1'))
        self.read(nonce, 0)
        self.assertEqual(self.reply('PAGE', nonce, '0', 'buy,water,5848.0,80,1,4,21,1'),
                         {'outcome': 'unavailable', 'reason': 'invalid_observation'})
        nonce, _ = self.begin()
        self.now += 2
        self.assertEqual(self.reply('HEADER', nonce, '976800', '0'),
                         {'outcome': 'unavailable', 'reason': 'timeout'})

    def test_loss_of_owned_process_abandons_partial_observation(self):
        nonce, _ = self.begin()
        self.guard = False
        self.assertEqual(self.reply('HEADER', nonce, '976800', '0'),
                         {'outcome': 'unavailable', 'reason': 'provider_unavailable'})


class MarketSurveyCueTests(unittest.TestCase):
    def test_overlay_stages_reused_resolver_and_archives_cue_for_cold_restore(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = root / 'case'
            extension = case / 'extensions/x4_lan_lab'
            ui, md = extension / 'ui', extension / 'md'
            ui.mkdir(parents=True)
            md.mkdir()
            bridge = ui / 'authority_world_bridge_probe.lua'
            bridge.write_text('local registry_checkpoint\n local ok,reason=bridge:update();assert(ok,reason)\n')
            config = ui / 'config.lua'
            config.write_text('return {authority_control_lease="' + 'e' * 32 + '"}\n')
            manifest = extension / 'ui.xml'
            resolver = ui / 'authority_registry_resolve_string.lua'
            resolver.write_bytes((ROOT / 'tools/dev' / resolver.name).read_bytes())
            resolver_entry = '    <file name="ui/authority_registry_resolve_string.lua" />\n'
            manifest.write_text(resolver_entry + '    <file name="ui/authority_world_bridge_probe.lua" />\n')
            provider = NativeMarketSurveyProvider()
            self.addCleanup(provider.close)
            protected = provider.install(case)
            driver, cue = ui / 'authority_native_market_survey.lua', md / 'authority_native_market_survey.xml'
            self.assertCountEqual(protected, [resolver, driver, cue, bridge, manifest, config])
            entries = manifest.read_text()
            self.assertEqual(entries.count(resolver_entry), 1)
            self.assertLess(entries.index(resolver.name), entries.index(driver.name))
            self.assertLess(entries.index(driver.name), entries.index(bridge.name))
            self.assertIn('native_market_tick(C.IsGamePaused())', bridge.read_text())
            redacted = redact_recovery_config(config.read_text())
            self.assertIn('authority_market_survey_lease="redacted"', redacted)
            self.assertNotIn(provider.lua_settings[0], redacted)
            with self.assertRaisesRegex(ValueError, 'new owned market overlay'):
                provider.install(case)
            archive = provider.archive_cue(case, root)
            self.assertFalse(cue.exists())
            self.assertEqual(archive.read_bytes(), (ROOT / 'tools/dev' / cue.name).read_bytes())

    def test_cue_only_reads_registry_berth_and_account(self):
        cue = ElementTree.parse(ROOT / 'tools/dev/authority_native_market_survey.xml').find('.//cue')
        allowed = {'cue', 'conditions', 'event_ui_triggered', 'actions', 'set_value',
                   'do_if', 'do_else', 'raise_lua_event'}
        self.assertEqual({entry.tag for entry in cue.iter()}, allowed)
        self.assertTrue(all(entry.attrib['name'].startswith('$') for entry in cue.iter('set_value')))
        expressions = '\n'.join(value for entry in cue.iter() for value in entry.attrib.values())
        for binding in ('$ships.{$request.$index}', '$byship.{$ship}', '$bay.docked.indexof.{$ship}',
                        '$station.idcode == @$request.$station_code', '$ship.idcode == @$request.$ship_code',
                        '$station.sector.macro.id == @$request.$sector', 'faction.player.money == player.money'):
            self.assertIn(binding, expressions)

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_provider_stock_reads_with_simulated_native_functions(self):
        subprocess.run(['luajit', 'tests/authority_native_market_survey.lua'],
                       cwd=ROOT, check=True, timeout=15)
