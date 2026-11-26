"""Offline checks of the native Company damage transport and install boundary."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree

from authority.company_combat_service import CombatImpactRejected
from authority_company_gun_damage import NativeGunDamageProvider, NativeGunDamageUncertain


ROOT = Path(__file__).resolve().parents[1]
BINDING = ('a' * 32, 'b' * 32, 1073)
RECEIPT = 'applied|0x102|0x103|0|26|30|3600|4|3600|2366|3600|4.000000165|3600|2366|3600'


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
            self.assertEqual(commands[0].get('hullshield'), '$args.$amount')
            self.assertFalse(native.findall('.//set_object_shield'))
            self.assertFalse(native.findall('.//set_object_hull'))
            self.assertNotIn('__PROVIDER_LEASE__', cue.read_text())
            result = self.provider.archive_cues(case, out)
            self.assertFalse(cue.exists())
            self.assertTrue(result.is_file())


class NativeDamageLuaTests(unittest.TestCase):
    def test_paused_start_and_one_dispatch_through_independent_readback(self):
        luajit = shutil.which('luajit')
        self.assertIsNotNone(luajit)
        subprocess.run([luajit, str(ROOT / 'tests/authority_company_gun_damage.lua'),
                        str(ROOT / 'tools/dev/authority_company_gun_damage.lua')], check=True, timeout=5)
