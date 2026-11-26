"""Test the dedicated cockpit combat source without launching X4."""
from contextlib import nullcontext
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from authority.company_combat_source import COMPANY_GUN_MD, COMPANY_GUN_UI, CompanyCombatSender
from authority.company_combat_frames import BINDING_FIELDS, MAX_BATCH, encode_frame


EPOCH = 'a' * 32
READY = f'[=ERROR=] 5.00 [X4LAN] AUTH_COMPANY_GUN_STATUS armed|{EPOCH}|1073\n'
GRANT = {'epoch': EPOCH, 'index': 1073, 'ticket': 'b' * 64,
         'session_epoch': 'c' * 32, 'generation': 'd' * 32}


def contact(sequence, *, epoch=EPOCH, target=1074):
    return f'[=ERROR=] 5.01 [X4LAN] AUTH_COMPANY_GUN {epoch}|1073|{target}|{sequence}\n'


def listening_socket():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(4)
    sock.settimeout(2)
    return sock


def receipt(request):
    return encode_frame({key: request[key] for key in BINDING_FIELDS} | {
        'kind': 'combat_receipts',
        'receipts': [dict(impact, order=impact['id'], status='confirmed', shield=0, hull=1000)
                     for impact in request['impacts']],
    })


class Client:
    def __init__(self, retry=False):
        self.retry = retry
        self.batches = []
        self.deadlines = []
        self.received = threading.Event()
        self.closed = False

    def exchange(self, impacts, *, deadline, cancelled):
        self.batches.append(impacts)
        self.deadlines.append(deadline)
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

    def sender(self, text, client=None, *, identity=None, port=1234):
        self.log.write_bytes(text.encode('ascii'))
        info = self.log.lstat()
        context = patch('authority.company_combat_source.CompanyCombatClient', return_value=client) if client else nullcontext()
        with context:
            sender = CompanyCombatSender(self.log, port, GRANT, epoch=EPOCH, index=1073,
                                         log_identity=identity or (info.st_dev, info.st_ino))
        self.addCleanup(sender.close)
        return sender

    def wait_for_confirmed(self, sender, count):
        deadline = time.monotonic() + 2
        while sender._counts['confirmed'] < count and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(sender._counts['confirmed'], count)

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
        self.assertEqual(client.deadlines[0], client.deadlines[1])
        self.assertEqual((result['observed'], result['confirmed'], result['retries'], result['failure']), (1, 1, 1, None))
        self.assertTrue(client.closed)

    def test_stale_replayed_and_malformed_rows_fail_closed(self):
        for log in (contact(1), READY + contact(1, epoch='b' * 32), READY + contact(1, target=1073),
                    READY + contact(1) * 2, READY + contact(3)):
            with self.subTest(log=log[:50]):
                client = Client()
                sender = self.sender(log, client)
                sender._thread.join(2)
                self.assertEqual(sender.close()['failure'], 'source_unavailable')
                self.assertEqual(client.batches, [])

    def test_one_read_flushes_more_than_two_batches_and_preserves_partial_row(self):
        client = Client()
        partial = contact(70)
        sender = self.sender(READY + ''.join(contact(n) for n in range(1, 70)) + partial[:-1], client)
        self.wait_for_confirmed(sender, 69)
        self.assertEqual([len(batch) for batch in client.batches], [32, 32, 5])
        with self.log.open('ab') as stream:
            stream.write(b'\n')
        self.wait_for_confirmed(sender, 70)
        self.assertEqual(sender.close()['confirmed'], 70)
        self.assertEqual([impact['id'] for batch in client.batches for impact in batch], list(range(1, 71)))

    def test_slow_fragmented_acknowledgement_keeps_the_healthy_connection(self):
        with listening_socket() as listener:
            count = MAX_BATCH + 1
            sender = self.sender(READY + ''.join(contact(n) for n in range(1, count + 1)),
                                 port=listener.getsockname()[1])
            connection, _ = listener.accept()
            with connection, connection.makefile('rb') as stream:
                connection.settimeout(2)
                first = json.loads(stream.readline())
                self.assertEqual([item['id'] for item in first['impacts']], list(range(1, MAX_BATCH + 1)))
                response = receipt(first)
                mid = len(response) // 2
                connection.sendall(response[:mid])
                time.sleep(4.05)  # Longer than all former one-second attempts combined.
                connection.sendall(response[mid:])
                next_request = json.loads(stream.readline())
                self.assertEqual([item['id'] for item in next_request['impacts']], [count])
                connection.sendall(receipt(next_request))
                self.wait_for_confirmed(sender, count)
                result = sender.close()
            self.assertEqual((result['confirmed'], result['retries'], result['failure']), (count, 0, None))

    def test_disconnected_reply_retries_the_exact_request_on_a_new_socket(self):
        with listening_socket() as listener:
            sender = self.sender(READY + contact(1), port=listener.getsockname()[1])
            first, _ = listener.accept()
            with first, first.makefile('rb') as stream:
                first.settimeout(2)
                request = stream.readline()
                response = receipt(json.loads(request))
                first.sendall(response[:20])
            second, _ = listener.accept()
            with second, second.makefile('rb') as stream:
                second.settimeout(2)
                self.assertEqual(stream.readline(), request)
                second.sendall(response)
                self.wait_for_confirmed(sender, 1)
                result = sender.close()
            self.assertEqual((result['confirmed'], result['retries'], result['failure']), (1, 1, None))

    def test_close_interrupts_an_outstanding_acknowledgement(self):
        with listening_socket() as listener:
            sender = self.sender(READY + contact(1), port=listener.getsockname()[1])
            connection, _ = listener.accept()
            with connection, connection.makefile('rb') as stream:
                connection.settimeout(2)
                self.assertEqual(json.loads(stream.readline())['impacts'][0]['id'], 1)
                started = time.monotonic()
                result = sender.close()
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(connection.recv(1024), b'')
            self.assertEqual((result['confirmed'], result['retries'], result['failure']), (0, 0, None))

    def test_receipt_deadline_expires_without_idle_poll_reconnect(self):
        with listening_socket() as listener, patch('authority.company_combat_source._ACK_TIMEOUT', .25):
            sender = self.sender(READY + contact(1), port=listener.getsockname()[1])
            connection, _ = listener.accept()
            with connection, connection.makefile('rb') as stream:
                connection.settimeout(2)
                self.assertEqual(json.loads(stream.readline())['impacts'][0]['id'], 1)
                self.assertEqual(connection.recv(1024), b'')
            sender._thread.join(1)
            result = sender.close()
            self.assertEqual((result['confirmed'], result['retries'], result['failure']), (0, 0, 'receipt_timeout'))

    def test_log_replacement_never_rebinds_current_admission(self):
        client = Client()
        sender = self.sender(READY + contact(1), client)
        self.assertTrue(client.received.wait(2))
        replacement = self.log.with_suffix('.new')
        replacement.write_text(READY + contact(2), encoding='ascii')
        if sys.platform == 'win32':
            # Windows refuses to replace a file that the sender's handle holds open, so
            # the bound log cannot change underneath the current admission at all.
            with self.assertRaises(PermissionError):
                replacement.replace(self.log)
            time.sleep(.3)  # Give the sender time to read anything it could still see.
            self.assertIsNone(sender.close()['failure'])
            self.assertEqual(len(client.batches), 1)
            return
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
