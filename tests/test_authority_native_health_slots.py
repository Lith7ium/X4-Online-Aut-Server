"""Bounded read slots on the owned native damage adapter."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import unittest

from authority_company_gun_damage import NativeGunDamageProvider


ROOT = Path(__file__).resolve().parents[1]
EPOCH = 'b' * 32
BINDINGS = tuple((str(i) * 32, EPOCH, 1072 + i) for i in range(1, 6))


class NativeHealthSlotTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.owned = True
        self.admitted = set(BINDINGS)
        self.helms = set(BINDINGS)
        provider = NativeGunDamageProvider(self.admitted.__contains__, can_fire=self.helms.__contains__,
                                           world_current=lambda epoch: epoch == EPOCH,
                                           clock=lambda: self.now)
        self.provider = provider
        self.addCleanup(provider.close)
        executable = Path('/proc/self/exe').stat()
        start = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
        provider.bind(dict(pid=os.getpid(), start_ticks=int(start),
                           exe_dev=executable.st_dev, exe_ino=executable.st_ino), lambda: self.owned)
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(peer.close)
        self.peer = peer
        peer.connect(provider._socket.getsockname())
        peer.settimeout(1)
        peer.send(f'GUN_HELLO|{provider._lease}'.encode('ascii'))

    def start(self, binding):
        operation = self.provider.begin_health_read(binding)
        kind, lease, serial, generation, epoch, index, deadline, slot = self.peer.recv(512).decode('ascii').split('|')
        self.assertEqual((kind, lease, generation, epoch, int(index)),
                         ('GUN_HEALTH', self.provider._lease, *binding))
        self.assertEqual(float(deadline), self.now + 2)
        prefix = f'GUN_HEALTH_RESULT|{lease}|{serial}|{generation}|{epoch}|{index}|'.encode('ascii')
        return operation, prefix, int(slot)

    def send(self, prefix, shield):
        self.peer.send(prefix + f'observed|0x102|{shield}|2366|3600|3600'.encode('ascii'))

    def test_out_of_order_replies_map_once_to_each_slot(self):
        self.assertEqual(self.provider.max_health_reads, 4)
        reads = [self.start(binding) for binding in BINDINGS[:4]]
        self.assertEqual([slot for _, _, slot in reads], [1, 2, 3, 4])
        with self.assertRaisesRegex(ValueError, 'capacity'):
            self.provider.begin_health_read(BINDINGS[4])
        with self.assertRaisesRegex(ValueError, 'already pending'):
            self.provider.begin_health_read(BINDINGS[0])
        for number in (3, 1, 2, 0):
            self.send(reads[number][1], 50 + number)
        self.send(reads[3][1], 3)
        self.assertEqual([operation.poll()['shield'] for operation, _, _ in reads], [50, 51, 52, 53])

    def test_cancelled_or_expired_identity_cannot_fill_a_reused_slot(self):
        cancelled, old_prefix, slot = self.start(BINDINGS[0])
        other, other_prefix, _ = self.start(BINDINGS[1])
        cancelled.cancel()
        fresh, new_prefix, new_slot = self.start(BINDINGS[2])
        self.assertEqual(new_slot, slot)
        self.send(old_prefix, 1)
        self.send(new_prefix.replace(EPOCH.encode(), b'c' * 32), 2)
        self.assertIsNone(fresh.poll())
        self.send(other_prefix, 30)
        self.send(new_prefix, 40)
        self.assertEqual(fresh.poll()['shield'], 40)
        self.now += 2
        with self.assertRaisesRegex(ValueError, 'expired'):
            other.poll()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            cancelled.poll()

    def test_buffered_replies_revalidate_process_and_helm_at_delivery(self):
        first, first_prefix, _ = self.start(BINDINGS[0])
        second, second_prefix, _ = self.start(BINDINGS[1])
        third, third_prefix, _ = self.start(BINDINGS[2])
        for prefix in (third_prefix, second_prefix, first_prefix):
            self.send(prefix, 50)
        self.assertEqual(first.poll()['shield'], 50)
        self.helms.remove(BINDINGS[1])
        with self.assertRaisesRegex(ValueError, 'lost its Company helm'):
            second.poll()
        self.owned = False
        with self.assertRaisesRegex(ValueError, 'process fence'):
            third.poll()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.provider.begin_health_read(BINDINGS[4])

    def test_damage_preempts_pending_and_buffered_reads(self):
        first, first_prefix, _ = self.start(BINDINGS[0])
        second, second_prefix, _ = self.start(BINDINGS[1])
        third, third_prefix, _ = self.start(BINDINGS[2])
        self.send(second_prefix, 50)
        self.send(first_prefix, 30)
        self.assertEqual(first.poll()['shield'], 30)
        impact = self.provider.begin_impact(BINDINGS[0], 1074, 26)
        command = self.peer.recv(512).decode('ascii').split('|')
        self.assertEqual(command[0], 'GUN_IMPACT')
        self.send(third_prefix, 30)
        for operation in (second, third):
            with self.assertRaisesRegex(ValueError, 'superseded'):
                operation.poll()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.provider.begin_health_read(BINDINGS[4])
        identity = '|'.join(command[1:7])
        self.peer.send(f'GUN_RESULT|{identity}|applied|0x102|0x103|0|26|30|3600|4|3600|2366|3600|'
                       '4|3600|2366|3600'.encode('ascii'))
        self.assertEqual(impact.poll(), (4, 3600))
        replacement, prefix, slot = self.start(BINDINGS[4])
        self.assertEqual(slot, 1)
        self.send(second_prefix, 50)
        self.send(prefix, 70)
        self.assertEqual(replacement.poll()['shield'], 70)


class NativeHealthLuaTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT not installed')
    def test_four_reads_in_stock_native_update(self):
        subprocess.run(['luajit', str(ROOT / 'tests/authority_native_health_slots.lua'),
                        str(ROOT / 'tools/dev/authority_company_gun_damage.lua')], check=True, timeout=5)
