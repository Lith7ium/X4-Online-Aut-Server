"""Test the dedicated cockpit combat source without launching X4."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from authority.company_combat_source import COMPANY_GUN_MD, COMPANY_GUN_UI, CompanyCombatSender


EPOCH = 'a' * 32
READY = f'[=ERROR=] 5.00 [X4LAN] AUTH_COMPANY_GUN_STATUS armed|{EPOCH}|1073\n'


def contact(sequence, *, epoch=EPOCH, target=1074):
    return f'[=ERROR=] 5.01 [X4LAN] AUTH_COMPANY_GUN {epoch}|1073|{target}|{sequence}\n'


class Client:
    def __init__(self, retry=False):
        self.retry = retry
        self.batches = []
        self.received = threading.Event()
        self.closed = False

    def exchange(self, impacts, *, timeout):
        self.batches.append(impacts)
        if len(self.batches) == 1 and self.retry:
            raise TimeoutError
        self.received.set()
        return [{'status': 'confirmed'} for _ in impacts]

    def close(self):
        self.closed = True


class CompanyCombatSourceTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.log = Path(temp.name) / 'engine.log'

    def sender(self, text, client, *, identity=None):
        self.log.write_bytes(text.encode('ascii'))
        info = self.log.lstat()
        with patch('authority.company_combat_source.CompanyCombatClient', return_value=client):
            sender = CompanyCombatSender(self.log, 1234, {'epoch': EPOCH, 'index': 1073}, epoch=EPOCH, index=1073,
                                         log_identity=identity or (info.st_dev, info.st_ino))
        self.addCleanup(sender.close)
        return sender

    def test_incremental_log_and_exact_retry_on_dedicated_worker(self):
        client = Client(retry=True)
        row = contact(1)
        sender = self.sender(READY + 'weapon_fired and lastattacker are unrelated\n' + row[:-1], client)
        with self.log.open('ab') as stream:
            stream.write(b'\n')
        self.assertTrue(client.received.wait(3))
        result = sender.close()
        self.assertEqual(client.batches, [[{'id': 1, 'target_index': 1074, 'amount': 26}]] * 2)
        self.assertIs(client.batches[0], client.batches[1])
        self.assertEqual((result['observed'], result['confirmed'], result['retries'], result['failure']), (1, 1, 1, None))
        self.assertTrue(client.closed)

    def test_stale_replayed_malformed_and_backlogged_rows_fail_closed(self):
        for log in (contact(1), READY + contact(1, epoch='b' * 32), READY + contact(1, target=1073),
                    READY + contact(1) * 2, READY + contact(3),
                    READY + ''.join(contact(n) for n in range(1, 66))):
            with self.subTest(log=log[:50]):
                client = Client()
                sender = self.sender(log, client)
                sender._thread.join(2)
                self.assertEqual(sender.close()['failure'], 'source_unavailable')
                self.assertEqual(client.batches, [])

    def test_log_replacement_never_rebinds_current_admission(self):
        client = Client()
        sender = self.sender(READY + contact(1), client)
        self.assertTrue(client.received.wait(2))
        replacement = self.log.with_suffix('.new')
        replacement.write_text(READY + contact(2), encoding='ascii')
        replacement.replace(self.log)
        sender._thread.join(2)
        self.assertEqual(sender.close()['failure'], 'source_unavailable')
        self.assertEqual(len(client.batches), 1)

    def test_refuses_log_replaced_before_sender_started(self):
        other = self.log.with_suffix('.old')
        other.touch()
        info = other.lstat()
        client = Client()
        sender = self.sender(READY + contact(1), client, identity=(info.st_dev, info.st_ino))
        sender._thread.join(2)
        self.assertEqual(sender.close()['failure'], 'source_unavailable')
        self.assertEqual(client.batches, [])

    def test_md_has_only_the_direct_attacker_side_stock_callback(self):
        md = ElementTree.parse(ROOT / COMPANY_GUN_MD).getroot()
        events = [node for node in md.iter() if node.tag.startswith('event_object')]
        self.assertEqual([(node.tag, node.get('method')) for node in events],
                         [('event_object_attacked_object', 'killmethod.hitbybullet')])
        for field in ('[X4LAN]', 'lastattacker', 'event_weapon_fired', 'set_object_hull', 'set_object_shield'):
            self.assertNotIn(field, (ROOT / COMPANY_GUN_MD).read_text())

    def test_lua_reports_only_live_unmodified_stock_primary_and_never_logs_handles(self):
        from lupa.luajit21 import LuaRuntime
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute("""
        local C = {
            IsGamePaused=function() return paused end,
            GetPlayerControlledShipID=function() return 100 end,
            GetPlayerOccupiedShipID=function() return 100 end,
            GetNumUpgradeSlots=function() return 1 end,
            GetUpgradeSlotCurrentComponent=function() return 200 end,
            GetInstalledWeaponMod=function() return modified end,
            GetWeaponLaunchedMacro=function() return 'bullet_gen_s_gatling_01_mk2_macro' end,
        }
        package.loaded.ffi = {
            C=C, cdef=function() end, new=function() return {} end, string=function(x) return x end
        }
        listeners, logs, calls = {}, {}, {}
        X4LAN_CONFIG = {authority_company_gun={epoch=string.rep('a',32), index=1073}}
        function RegisterEvent(event, callback) listeners[event] = callback end
        function AddUITriggeredEvent(_, control) calls[#calls+1]=control return true end
        function DebugError(log) logs[#logs+1]=log end
        ConvertIDTo64Bit = tonumber
        function GetMacroData() return 'weapons' end
        function GetLibraryEntry() return {hullshielddpshot=26} end
        seat={controlled_actor=function() return '0x64' end}
        """)
        lua.execute((ROOT / COMPANY_GUN_UI).read_text())
        result = lua.execute("""
        local ready=listeners['X4AuthorityCompanyGunSource.Ready']
        local contact=listeners['X4AuthorityCompanyGunSource.Contact']
        local epoch=X4LAN_CONFIG.authority_company_gun.epoch
        local exact=epoch..'|1073|1074|0x64|0xc8'
        local module=X4LAN_COMPANY_GUN_SOURCE
        module.tick(seat)
        contact(nil, exact)
        ready(nil, '1|'..epoch..'|1073')
        modified=true
        contact(nil, exact)
        modified=false
        contact(nil, epoch..'|1073|1074|0x64|0xc9')
        paused=true
        contact(nil, exact)
        paused=false
        contact(nil, exact)
        module.tick(nil)
        contact(nil, exact)
        module.tick({controlled_actor=function() return '0x65' end})
        return table.concat(logs,string.char(10)), table.concat(calls,',')
        """)
        logs, calls = result
        self.assertEqual(logs.splitlines(), [
            f'[X4LAN] AUTH_COMPANY_GUN_STATUS armed|{EPOCH}|1073',
            f'[X4LAN] AUTH_COMPANY_GUN {EPOCH}|1073|1074|1',
            '[X4LAN] AUTH_COMPANY_GUN_STATUS unavailable',
        ])
        self.assertEqual(calls, 'Arm,Disarm')
