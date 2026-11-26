"""Offline checks of the native Company damage transport and install boundary."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree

from authority.company_combat_frames import MAX_BATCH
from authority.company_combat_service import CombatImpactRejected
from authority_company_gun_damage import NativeGunDamageProvider, NativeGunDamageUncertain


ROOT = Path(__file__).resolve().parents[1]
BINDING = ('a' * 32, 'b' * 32, 1073)
RECEIPT = 'applied|0x102|0x103|0|26|30|3600|4|3600|2366|3600|4.000000165|3600|2366|3600'
HEALTH_RECEIPT = b'observed|0x102|100.25|2366|3600|3600'
HEALTH = dict(shield=100.25, maxshield=2366, hull=3600, maxhull=3600)


class NativeDamageTests(unittest.TestCase):
    def setUp(self):
        self.current, self.helm, self.world = True, True, True
        self.now = 100
        self.provider = NativeGunDamageProvider(self.is_current, can_fire=self.can_fire,
                                                world_current=self.world_current, timeout=1,
                                                clock=lambda: self.now)
        self.addCleanup(self.provider.close)
        executable = Path('/proc/self/exe').stat()
        stat = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
        self.provider.bind(dict(pid=os.getpid(), start_ticks=int(stat[19]),
                                exe_dev=executable.st_dev, exe_ino=executable.st_ino), lambda: True)
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.peer.close)
        self.peer.connect(self.provider._socket.getsockname())
        self.peer.settimeout(2)

    def is_current(self, binding):
        self.assertEqual(binding, BINDING)
        return self.current

    def can_fire(self, binding):
        self.assertEqual(binding, BINDING)
        return self.helm

    def world_current(self, epoch):
        self.assertEqual(epoch, BINDING[1])
        return self.world

    def hello(self):
        self.peer.send(f'GUN_HELLO|{self.provider._lease}'.encode('ascii'))

    def engine(self, receipt):
        fields = self.peer.recv(512).decode('ascii').split('|')
        self.assertEqual(fields[0], 'GUN_IMPACT')
        self.assertEqual(fields[1], self.provider._lease)
        self.assertEqual(fields[3:8], [*BINDING[:2], '1073', '1074', '26'])
        if receipt is not None:
            self.peer.send('|'.join(('GUN_RESULT', *fields[1:7], receipt)).encode('ascii'))
        return int(fields[2])

    def begin(self):
        return self.provider.begin_impact(BINDING, 1074, 26)

    def begin_health(self):
        return self.provider.begin_health_read(BINDING)

    def health_reply_prefix(self):
        fields = self.peer.recv(512).decode('ascii').split('|')
        self.assertEqual(fields[0], 'GUN_HEALTH')
        self.assertEqual(fields[1], self.provider._lease)
        self.assertEqual(fields[3:6], [*BINDING[:2], '1073'])
        self.assertEqual(float(fields[6]), self.now + 2)
        return ('|'.join(('GUN_HEALTH_RESULT', *fields[1:6])) + '|').encode('ascii')

    def assert_no_impact(self):
        self.peer.setblocking(False)
        try:
            with self.assertRaises(BlockingIOError):
                self.peer.recv(512)
        finally:
            self.peer.settimeout(2)

    def test_missing_hello_and_stale_source_do_not_send(self):
        with self.assertRaises(CombatImpactRejected):
            self.begin()
        self.hello()
        self.current = False
        with self.assertRaises(CombatImpactRejected):
            self.begin()
        self.current, self.helm = True, False
        with self.assertRaises(CombatImpactRejected):
            self.begin()
        self.assert_no_impact()

    def test_authoritative_readbacks_and_serial_refusal(self):
        self.hello()
        refused = self.begin()
        self.assertIsNone(refused.poll())
        first = self.engine('refused')
        with self.assertRaises(CombatImpactRejected):
            refused.poll()
        confirmed = self.begin()
        self.assertGreater(self.engine(RECEIPT), first)
        self.assertEqual(confirmed.poll(), (4.000000165, 3600))
        self.assertEqual(confirmed.poll(), (4.000000165, 3600))
        self.assert_no_impact()

    def test_revocation_after_dispatch_can_still_settle(self):
        self.hello()
        impact = self.begin()
        self.current, self.helm = False, False
        self.engine(RECEIPT)
        self.assertEqual(impact.poll(), (4.000000165, 3600))
        with self.assertRaises(CombatImpactRejected):
            self.begin()
        self.assert_no_impact()

    def test_lost_world_after_single_send_fences(self):
        self.hello()
        impact = self.begin()
        self.world = False
        self.engine(RECEIPT)
        with self.assertRaisesRegex(NativeGunDamageUncertain, 'lost its world'):
            impact.poll()
        with self.assertRaisesRegex(NativeGunDamageUncertain, 'fenced'):
            self.begin()
        self.assert_no_impact()

    def test_timeout_never_resends(self):
        self.hello()
        pending = self.begin()
        self.engine(None)
        self.assertIsNone(pending.poll())
        with self.assertRaises(CombatImpactRejected):
            self.begin()
        self.assert_no_impact()
        self.now += 1
        with self.assertRaisesRegex(NativeGunDamageUncertain, 'authoritative receipt'):
            pending.poll()
        with self.assertRaisesRegex(NativeGunDamageUncertain, 'fenced'):
            self.begin()
        self.assert_no_impact()

    def test_unrelated_peer_and_stale_serial_cannot_settle(self):
        self.hello()
        pending = self.begin()
        fields = self.peer.recv(512).decode('ascii').split('|')
        reply = '|'.join(('GUN_RESULT', *fields[1:7], RECEIPT)).encode('ascii')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
            other.sendto(reply, self.provider._socket.getsockname())
        fields[2] = str(int(fields[2]) + 1)
        self.peer.send('|'.join(('GUN_RESULT', *fields[1:7], RECEIPT)).encode('ascii'))
        self.assertIsNone(pending.poll())
        self.peer.send(reply)
        self.assertEqual(pending.poll(), (4.000000165, 3600))

    def test_close_does_not_reissue_pending_work(self):
        self.hello()
        pending = self.begin()
        self.engine(None)
        self.provider.close()
        with self.assertRaisesRegex(NativeGunDamageUncertain, 'closed during impact'):
            pending.poll()
        self.assert_no_impact()

    def test_disagreeing_health_never_returns_hp(self):
        self.hello()
        pending = self.begin()
        self.engine(RECEIPT.replace('4.000000165', '18'))
        with self.assertRaisesRegex(NativeGunDamageUncertain, 'readbacks disagree'):
            pending.poll()
        self.assert_no_impact()

    def test_lethal_root_readbacks_must_independently_agree(self):
        self.hello()
        pending = self.begin()
        lethal = 'applied|0x102|0x103|1|26|4|22|0|0|2366|3600|0|0|2366|3600'
        self.engine(lethal)
        self.assertEqual(pending.poll(), (0, 0))

    def test_health_uses_fresh_read_with_exact_registry_identity(self):
        self.hello()
        read = self.begin_health()
        prefix = self.health_reply_prefix()
        self.assertIsNone(read.poll())
        with self.assertRaisesRegex(ValueError, 'already pending'):
            self.begin_health()
        self.peer.send(prefix + HEALTH_RECEIPT)
        self.assertEqual(read.poll(), HEALTH)
        read.poll()['shield'] = 0
        self.assertEqual(read.poll(), HEALTH)
        current = self.begin_health()
        current_prefix = self.health_reply_prefix()
        self.assertNotEqual(current_prefix, prefix)
        self.peer.send(prefix + HEALTH_RECEIPT)
        self.assertIsNone(current.poll())
        self.peer.send(current_prefix + HEALTH_RECEIPT.replace(b'100.25', b'50.5'))
        self.assertEqual(current.poll(), dict(HEALTH, shield=50.5))

    def test_cancelling_health_read_releases_slot_and_discards_stale_reply(self):
        self.hello()
        old = self.begin_health()
        old_prefix = self.health_reply_prefix()
        old.cancel()
        old.cancel()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            old.poll()
        new = self.begin_health()
        new_prefix = self.health_reply_prefix()
        self.peer.send(old_prefix + HEALTH_RECEIPT)
        self.assertIsNone(new.poll())
        self.peer.send(new_prefix + HEALTH_RECEIPT)
        self.assertEqual(new.poll(), HEALTH)
        new.cancel()
        self.assertEqual(new.poll(), HEALTH)

    def test_impact_supersedes_read_and_read_cannot_steal_impact_receipt(self):
        self.hello()
        read = self.begin_health()
        prefix = self.health_reply_prefix()
        impact = self.begin()
        self.engine(RECEIPT)
        self.peer.send(prefix + HEALTH_RECEIPT)
        with self.assertRaisesRegex(ValueError, 'superseded'):
            read.poll()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.begin_health()
        self.assertEqual(impact.poll(), (4.000000165, 3600))
        new_read = self.begin_health()
        new_prefix = self.health_reply_prefix()
        self.assertIsNone(new_read.poll())
        self.peer.send(new_prefix + HEALTH_RECEIPT)
        self.assertEqual(new_read.poll(), HEALTH)

    def test_invalid_expired_and_revoked_health_reads_do_not_fence_impacts(self):
        self.hello()
        for field, replacement in ((b'0x102', b'0x0'), (b'100.25', b'nan'), (b'100.25', b'2367'),
                                   (b'|3600|3600', b'|0|3600'), (b'|3600|3600', b'|3601|3600')):
            with self.subTest(field=field, replacement=replacement):
                read = self.begin_health()
                prefix = self.health_reply_prefix()
                self.peer.send(prefix + HEALTH_RECEIPT.replace(field, replacement))
                with self.assertRaises(ValueError):
                    read.poll()
        expired = self.begin_health()
        expired_prefix = self.health_reply_prefix()
        self.now += 2
        self.peer.send(expired_prefix + HEALTH_RECEIPT)
        with self.assertRaisesRegex(ValueError, 'expired'):
            expired.poll()
        revoked = self.begin_health()
        revoked_prefix = self.health_reply_prefix()
        self.helm = False
        self.peer.send(revoked_prefix + HEALTH_RECEIPT)
        with self.assertRaisesRegex(ValueError, 'lost its Company helm'):
            revoked.poll()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.begin_health()
        self.helm = True
        impact = self.begin()
        self.engine(RECEIPT)
        self.assertEqual(impact.poll(), (4.000000165, 3600))

    def group_prefix(self, count):
        rows = []
        identity = None
        while len(rows) < count:
            packet = self.peer.recv(768)
            self.assertLessEqual(len(packet), 512)
            tag, lease, serial, generation, epoch, source, offset, total, deadline, *chunk = (
                packet.decode('ascii').split('|'))
            self.assertEqual((tag, lease, generation, epoch, source),
                             ('GUN_GROUP', self.provider._lease, *BINDING[:2], '1073'))
            self.assertEqual((int(offset), int(total), float(deadline)), (len(rows) + 1, count, self.now + 1))
            rows.extend(chunk)
            prefix = f'GUN_GROUP_RESULT|{lease}|{serial}|{generation}|{epoch}|{source}|'.encode('ascii')
            self.assertIn(identity, (None, prefix))
            identity = prefix
        self.assertEqual(len(rows), count)
        return identity, rows

    def send_group(self, *impacts):
        self.hello()
        self.provider._ordered_hits = True
        group = self.provider.begin_impacts(BINDING, impacts)
        prefix, rows = self.group_prefix(len(impacts))
        self.assertEqual(rows, [f'{target},{amount:.17g}' for target, amount in impacts])
        return group, prefix

    def test_group_opt_in_and_maximum_packet_bound(self):
        self.hello()
        with self.assertRaises(CombatImpactRejected):
            self.provider.begin_impacts(BINDING, ((1074, 26),))
        self.assert_no_impact()
        self.provider._ordered_hits = True
        with self.assertRaises(CombatImpactRejected):
            self.provider.begin_impacts(BINDING, ((200000, 26),) * (MAX_BATCH + 1))
        self.assert_no_impact()
        request, prefix = self.send_group(*((200000, 1.234567890123456e-300),) * MAX_BATCH)
        self.peer.send(prefix + b'refused')
        with self.assertRaises(CombatImpactRejected):
            request.poll()
        self.assertFalse(self.provider._fenced)

    def test_group_confirms_interleaved_distinct_targets_and_repeated_target(self):
        request, prefix = self.send_group((1074, 26), (943, 26), (1074, 26))
        self.peer.send(prefix + b'row|1|1074|8.01344|applied|0x102|0x103|0|26|30|3600|4|3600|2366|3600|0|3578|2366|3600')
        self.assertIsNone(request.poll())
        with self.assertRaises(CombatImpactRejected):
            self.begin()
        self.peer.send(prefix + b'row|2|943|8.01344|applied|0x102|0x104|0|26|2366|3600|2340|3600|2366|3600|2340|3600|2366|3600')
        self.peer.send(prefix + b'row|3|1074|8.01344|applied|0x102|0x103|0|26|4|3600|0|3578|2366|3600|0|3578|2366|3600')
        self.peer.send(prefix + b'complete')
        self.assertEqual(request.poll(), ((4, 3600), (2340, 3600), (0, 3578)))
        self.assert_no_impact()

    def test_partial_group_never_reissues_or_accepts_a_prewrite_refusal(self):
        request, prefix = self.send_group((1074, 26), (943, 26))
        self.peer.send(prefix + b'row|1|1074|8.01344|' + RECEIPT.encode('ascii'))
        self.assertIsNone(request.poll())
        self.peer.send(prefix + b'refused')
        with self.assertRaises(NativeGunDamageUncertain):
            request.poll()
        with self.assertRaises(NativeGunDamageUncertain):
            self.begin()
        self.assert_no_impact()

    def test_group_completion_requires_every_row(self):
        request, prefix = self.send_group((1074, 26), (943, 26))
        self.peer.send(prefix + b'row|1|1074|8.01344|' + RECEIPT.encode('ascii'))
        self.peer.send(prefix + b'complete')
        with self.assertRaises(NativeGunDamageUncertain):
            request.poll()
        self.assertTrue(self.provider._fenced)
        self.assert_no_impact()

    def test_group_missing_terminal_receipt_fences_and_never_retransmits(self):
        request, prefix = self.send_group((1074, 26),)
        self.peer.send(prefix + b'row|1|1074|8.01344|' + RECEIPT.encode('ascii'))
        self.assertIsNone(request.poll())
        self.now += 1
        with self.assertRaises(NativeGunDamageUncertain):
            request.poll()
        with self.assertRaises(NativeGunDamageUncertain):
            self.provider.begin_impacts(BINDING, ((1074, 26),))
        self.assert_no_impact()

    def test_opt_in_overlay_contains_one_stock_write_and_archives_cue(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            case = out / 'case'
            lab = case / 'extensions/x4_lan_lab'
            ui, md = lab / 'ui', lab / 'md'
            ui.mkdir(parents=True)
            md.mkdir()
            manifest = lab / 'ui.xml'
            manifest.write_text('<ui>\n    <file name="ui/authority_world_bridge_probe.lua" />\n</ui>\n')
            bridge = ui / 'authority_world_bridge_probe.lua'
            bridge.write_text('local registry_checkpoint\n local ok,reason=bridge:update();assert(ok,reason)\n')
            self.assertNotIn('company_gun_damage', bridge.read_text())
            paths = self.provider.install(case)
            self.assertIn('if registry_checkpoint:ready()', bridge.read_text())
            self.assertIn('C.IsGamePaused, X4LAN_COMBAT_OBSERVER.read_lua_health', bridge.read_text())
            cue = md / 'authority_company_gun_damage.xml'
            self.assertIn(cue, paths)
            native = ElementTree.parse(cue)
            commands = native.findall('.//apply_attackstrength')
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0].get('hullshield'), '$amount')
            for name in ('Impact', 'Group'):
                self.assertIsNotNone(native.find(f"./cues/cue[@name='{name}']//include_actions[@ref='ApplyStock']"))
            self.assertIn('engine.game_time, false)', bridge.read_text())
            self.assertFalse(native.findall('.//set_object_shield'))
            self.assertFalse(native.findall('.//set_object_hull'))
            self.assertNotIn('__PROVIDER_LEASE__', cue.read_text())
            health = native.find("./cues/cue[@name='Health']")
            self.assertIsNotNone(health)
            self.assertFalse(health.findall('.//apply_attackstrength'))
            self.assertNotIn('__PROVIDER_HEALTH_LEASE__', cue.read_text())
            self.assertNotIn('__PROVIDER_GROUP_LEASE__', cue.read_text())
            self.assertIn("@$args.$lease == '-'", cue.read_text())
            result = self.provider.archive_cues(case, out)
            self.assertFalse(cue.exists())
            self.assertTrue(result.is_file())


class NativeDamageLuaTests(unittest.TestCase):
    def test_paused_start_and_one_dispatch_through_independent_readback(self):
        luajit = shutil.which('luajit')
        self.assertIsNotNone(luajit)
        subprocess.run([luajit, str(ROOT / 'tests/authority_company_gun_damage.lua'),
                        str(ROOT / 'tools/dev/authority_company_gun_damage.lua')], check=True, timeout=5)
