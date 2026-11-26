"""Test the dedicated cockpit combat source without launching X4."""
from contextlib import nullcontext
import json
from pathlib import Path
import select
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
from authority.company_cockpit_health import CompanyCockpitHealthBridge


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
        return [dict(impact, order=impact['id'], status='confirmed', shield=0, hull=1000)
                for impact in impacts]

    def close(self):
        self.closed = True


class CompanyCombatSourceTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.log = Path(temp.name) / 'engine.log'

    def sender(self, text, client=None, *, identity=None, port=1234, health_bridge=None):
        self.log.write_bytes(text.encode('ascii'))
        info = self.log.lstat()
        context = patch('authority.company_combat_source.CompanyCombatClient', return_value=client) if client else nullcontext()
        with context:
            sender = CompanyCombatSender(self.log, port, GRANT, epoch=EPOCH, index=1073,
                                         log_identity=identity or (info.st_dev, info.st_ino),
                                         health_bridge=health_bridge)
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
                    READY + contact(1) * 2, READY + contact(3),
                    READY + contact(1).rstrip() + '|36\n',
                    READY + contact(1).rstrip() + '|weapon_gen_s_laser_01_mk2_macro\n'):
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

    def ready_health_bridge(self, clock=lambda: 10):
        bridge = CompanyCockpitHealthBridge((GRANT['generation'], EPOCH, 1073), clock=clock)
        bridge.on_clock(3, True)
        bridge.on_sample({'sequence': 1, 'capture_time': clock()})
        return bridge

    def test_completed_contact_preempts_outstanding_health_without_delivering_stale_reply(self):
        bridge = self.ready_health_bridge()
        with listening_socket() as listener:
            sender = self.sender(READY, port=listener.getsockname()[1], health_bridge=bridge)
            old, _ = listener.accept()
            with old, old.makefile('rb') as stream:
                old.settimeout(1)
                health = json.loads(stream.readline())
                self.assertEqual((health['kind'], health['window'], health['sequence']), ('combat_health_read', 3, 1))
                stale = encode_frame({key: health[key] for key in (*BINDING_FIELDS, 'window', 'sequence')} | {
                    'kind': 'combat_health', 'revision': 0,
                    'health': {'shield': 100, 'maxshield': 100, 'hull': 1000, 'maxhull': 1000},
                })
                old.sendall(stale[:-1])
                with self.log.open('ab') as log:
                    log.write(b'[=ERROR=] 5.01 unrelated native log noise\n')
                    log.write(contact(1).encode('ascii')[:-1])
                self.assertFalse(select.select([old, listener], [], [], .2)[0],
                                 'chatter or a partial contact preempted health')
                self.assertEqual(sender._counts['observed'], 0)

                with self.log.open('ab') as log:
                    log.write(b'\n')
                listener.settimeout(.8)
                fresh, _ = listener.accept()
                with fresh, fresh.makefile('rb') as hits:
                    fresh.settimeout(1)
                    request = json.loads(hits.readline())
                    self.assertEqual(request['kind'], 'combat_impacts')
                    for key in (*BINDING_FIELDS, 'ticket'):
                        self.assertEqual(request[key], health[key])
                    self.assertEqual(request['impacts'], [{'id': 1, 'target_index': 1074, 'amount': 26}])
                    with self.assertRaises(ConnectionResetError):
                        old.recv(1)
                    fresh.sendall(receipt(request))
                    self.wait_for_confirmed(sender, 1)
                    result = sender.close()
        sent = []
        bridge.deliver(lambda message: sent.append(message) or True)
        self.assertFalse(any(message.startswith('HEALTH|') for message in sent))
        self.assertEqual((bridge.stats()['delivered'], bridge.stats()['confirmed']), (0, 0))
        self.assertEqual((result['confirmed'], result['retries'], result['failure']), (1, 0, None))
        self.assertEqual(result['last_confirmed_receipt']['order'], 1)

    def test_next_health_read_finishes_despite_another_contact_after_preemption(self):
        now = 10
        bridge = self.ready_health_bridge(clock=lambda: now)
        with listening_socket() as listener:
            sender = self.sender(READY, port=listener.getsockname()[1], health_bridge=bridge)
            old, _ = listener.accept()
            with old, old.makefile('rb') as stream:
                old.settimeout(1)
                first_read = json.loads(stream.readline())
                self.assertEqual((first_read['kind'], first_read['sequence']), ('combat_health_read', 1))
                with self.log.open('a', encoding='ascii') as log:
                    log.write('unrelated native chatter\n')
                self.assertFalse(select.select([old, listener], [], [], .15)[0],
                                 'unrelated chatter cancelled an outstanding health read')
                with self.log.open('a', encoding='ascii') as log:
                    log.write(contact(1))

                fresh, _ = listener.accept()
                with fresh, fresh.makefile('rb') as stream:
                    fresh.settimeout(1)
                    hit = json.loads(stream.readline())
                    self.assertEqual([row['id'] for row in hit['impacts']], [1])
                    with self.assertRaises(ConnectionResetError):
                        old.recv(1)
                    now = 11
                    bridge.on_sample({'sequence': 2, 'capture_time': now})
                    fresh.sendall(receipt(hit))
                    protected = json.loads(stream.readline())
                    self.assertEqual((protected['kind'], protected['sequence']), ('combat_health_read', 2))
                    with self.log.open('a', encoding='ascii') as log:
                        log.write(contact(2))
                    self.assertFalse(select.select([fresh, listener], [], [], .15)[0],
                                     'a second contact interrupted the protected health read')
                    fresh.sendall(encode_frame({key: protected[key] for key in
                                                (*BINDING_FIELDS, 'window', 'sequence')} | {
                        'kind': 'combat_health', 'revision': 1,
                        'health': {'shield': 75, 'maxshield': 100, 'hull': 1000, 'maxhull': 1000},
                    }))
                    queued_hit = json.loads(stream.readline())
                    self.assertEqual(queued_hit['impacts'], [{'id': 2, 'target_index': 1074, 'amount': 26}])
                    fresh.sendall(receipt(queued_hit))
                    self.wait_for_confirmed(sender, 2)
                    delivered = []
                    bridge.deliver(lambda message: delivered.append(message) or True)
                    self.assertEqual([message.split('|')[5:7] for message in delivered
                                      if message.startswith('HEALTH|')], [['3', '2']])
                    self.assertEqual(bridge.stats()['delivered'], 1)
                    self.assertFalse(select.select([fresh, listener], [], [], .15)[0],
                                     'the waiting hit was sent more than once')
                    result = sender.close()
        self.assertEqual((result['observed'], result['confirmed'], result['retries']), (2, 2, 0))
        self.assertEqual(result['last_confirmed_receipt']['order'], 2)

    def test_already_read_contact_batches_precede_idle_health(self):
        count = MAX_BATCH + 1
        bridge = self.ready_health_bridge()
        with listening_socket() as listener:
            sender = self.sender(READY + ''.join(contact(n) for n in range(1, count + 1)),
                                 port=listener.getsockname()[1], health_bridge=bridge)
            connection, _ = listener.accept()
            with connection, connection.makefile('rb') as stream:
                connection.settimeout(1)
                for ids in (list(range(1, MAX_BATCH + 1)), [count]):
                    request = json.loads(stream.readline())
                    self.assertEqual(request['kind'], 'combat_impacts')
                    self.assertEqual([impact['id'] for impact in request['impacts']], ids)
                    connection.sendall(receipt(request))
                health = json.loads(stream.readline())
                self.assertEqual((health['kind'], health['window'], health['sequence']), ('combat_health_read', 3, 1))
                self.assertEqual(sender.close()['confirmed'], count)

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
            self.assertEqual(result['last_confirmed_receipt'], dict(
                id=count, target_index=1074, amount=26, order=count, status='confirmed', shield=0, hull=1000))

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
            self.assertNotIn('last_confirmed_receipt', result)

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
        try:
            replacement.replace(self.log)
        except PermissionError as error:
            if sys.platform != 'win32' or error.winerror not in (5, 32):
                raise
            # Windows can deny replacement while the collector holds the log open.
            # Confirm it is still tailing the admitted file and ignored the other one.
            replacement.write_text(READY + contact(2) + contact(3), encoding='ascii')
            with self.log.open('a', encoding='ascii') as original:
                original.write(contact(2))
            self.wait_for_confirmed(sender, 2)
            result = sender.close()
            self.assertIsNone(result['failure'])
            self.assertEqual(result['observed'], 2)
            return
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
        contact = md.find(".//cue[@name='Contact']")
        self.assertEqual(contact.find(".//set_value[@name='$weaponmacro']").attrib['exact'], '@$weapon.macro.id')
        self.assertTrue(contact.find('.//raise_lua_event').attrib['param'].endswith(',$weapon,$weaponmacro]'))
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
            GetNumUpgradeSlots=function() return 2 end,
            GetUpgradeSlotCurrentComponent=function(_, _, slot) return 199 + slot end,
            GetInstalledWeaponMod=function() return modified end,
            GetWeaponLaunchedMacro=function(gun)
                return gun == 200 and 'bullet_gen_s_gatling_01_mk2_macro' or 'bullet_gen_s_laser_01_mk1_macro'
            end,
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
        function GetLibraryEntry(_, macro)
            if macro == 'weapon_gen_s_laser_01_mk1_macro' then
                return {hullshielddpshot=pulse_damage or 36, isbeamweapon=beam, shieldonlydpshot=extra_channel}
            end
            return {hullshielddpshot=26}
        end
        seat={controlled_actor=function() return '0x64' end}
        """)
        lua.execute((ROOT / COMPANY_GUN_UI).read_text())
        result = lua.execute("""
        local ready=listeners['X4AuthorityCompanyGunSource.Ready']
        local contact=listeners['X4AuthorityCompanyGunSource.Contact']
        local epoch=X4LAN_CONFIG.authority_company_gun.epoch
        local bolt='|weapon_gen_s_gatling_01_mk2_macro'
        local pulse='|weapon_gen_s_laser_01_mk1_macro'
        local stem=epoch..'|1073|1074|0x64|'
        local exact=stem..'0xc8'..bolt
        local module=X4LAN_COMPANY_GUN_SOURCE
        module.tick(seat)
        contact(nil, exact)
        ready(nil, '1|'..epoch..'|1073')
        modified=true
        contact(nil, exact)
        modified=false
        contact(nil, stem..'0xca'..bolt)  -- not mounted
        contact(nil, stem..'0xc8'..pulse) -- wrong bullet for the reported gun
        contact(nil, stem..'0xc9'..bolt)
        contact(nil, stem..'0xc9') -- legacy rows require the verified Bolt projectile
        contact(nil, stem..'0xc8|')
        contact(nil, stem..'0xc9|weapon_gen_s_laser_01_mk2_macro')
        pulse_damage=35
        contact(nil, stem..'0xc9'..pulse)
        pulse_damage=nil
        beam=true
        contact(nil, stem..'0xc9'..pulse)
        beam=false
        extra_channel=1
        contact(nil, stem..'0xc9'..pulse)
        extra_channel=nil
        paused=true
        contact(nil, exact)
        paused=false
        contact(nil, exact)
        contact(nil, stem..'0xc9'..pulse)
        contact(nil, stem..'0xc8') -- old MD callback for the stock Bolt
        module.tick(nil)
        contact(nil, exact)
        module.tick({controlled_actor=function() return '0x65' end})
        return table.concat(logs,string.char(10)), table.concat(calls,',')
        """)
        logs, calls = result
        self.assertEqual(logs.splitlines(), [
            f'[X4LAN] AUTH_COMPANY_GUN_STATUS armed|{EPOCH}|1073',
            f'[X4LAN] AUTH_COMPANY_GUN {EPOCH}|1073|1074|1',
            f'[X4LAN] AUTH_COMPANY_GUN {EPOCH}|1073|1074|2|weapon_gen_s_laser_01_mk1_macro',
            f'[X4LAN] AUTH_COMPANY_GUN {EPOCH}|1073|1074|3',
            '[X4LAN] AUTH_COMPANY_GUN_STATUS unavailable',
        ])
        self.assertEqual(calls, 'Arm,Disarm')
