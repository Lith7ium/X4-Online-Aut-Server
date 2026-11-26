"""Real pinned TLS and Company admissions with a synthetic damage adapter."""
import asyncio
import hashlib
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import Mock

from authority.company_combat_client import CompanyCombatClient
from authority.company_combat_frames import encode_frame
from authority.company_combat_service import CompanyCombatService, derive_combat_ticket
from authority.company_motion_service import CompanyMotionService
from authority.experimental_tls_tunnel import create_server_context, start_client, start_server
from test_authority_tls_tunnel_integration import TEST_CERTIFICATE, TEST_PRIVATE_KEY, listening_socket

_KEY = b'c' * 32
_EPOCH = 'b' * 32


class CompanyCombatTLSTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.motion = CompanyMotionService()
        self.addCleanup(self.motion.close)
        self.combat = CompanyCombatService(self.motion.current_combat_grant, self.apply)
        self.addCleanup(self.combat.close)
        self.health = {1074: (30, 3600), 1700: (80, 2000)}
        self.calls = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        certificate, private_key = (Path(directory.name) / name for name in ('cert.pem', 'key.pem'))
        certificate.write_text(TEST_CERTIFICATE, encoding='ascii')
        private_key.write_text(TEST_PRIVATE_KEY, encoding='ascii')
        self.context = create_server_context(certificate, private_key)
        self.fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest()

    def apply(self, binding, target, amount):
        self.calls.append((binding[2], target, amount))
        shield, hull = self.health[target]
        self.health[target] = max(shield - amount, 0), hull - max(amount - shield, 0)
        return self.health[target]

    @staticmethod
    async def echo(reader, writer):
        try:
            writer.write(b'control:' + await reader.readline())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def listener(self):
        result = listening_socket()
        self.addCleanup(result.close)
        return result

    async def start(self, *, combat_enabled):
        backend = await asyncio.start_server(self.echo, '127.0.0.1', 0)
        self.addAsyncCleanup(backend.wait_closed)
        self.addAsyncCleanup(backend.close)
        public = self.listener()
        self.public_port = public.getsockname()[1]
        gateway = await start_server(
            listener=public, ssl_context=self.context, shared_key=_KEY,
            control_port=backend.sockets[0].getsockname()[1], file_port=self.listener().getsockname()[1],
            motion_port=self.motion.port, combat_port=self.combat.port if combat_enabled else None,
            max_connections=1, idle_timeout=15,
        )
        self.addAsyncCleanup(gateway.close)

    async def link(self):
        control, files, combat = (self.listener() for _ in range(3))
        ports = control.getsockname()[1], combat.getsockname()[1]
        tunnel = await start_client(
            control_listener=control, file_listener=files, combat_listener=combat,
            server_host='127.0.0.1', server_port=self.public_port, server_fingerprint=self.fingerprint,
            shared_key=_KEY, max_combat_connections=2, idle_timeout=15,
        )
        self.addAsyncCleanup(tunnel.close)
        return ports

    def grant(self, handle, index):
        return self.motion.admit(handle, f'{handle:032x}', index, 'sector_a', 'a' * 32, _EPOCH)

    async def drive(self, exchange):
        task = asyncio.create_task(exchange)
        try:
            async with asyncio.timeout(4):
                while not task.done():
                    self.motion.poll()
                    self.combat.poll()
                    await asyncio.sleep(.003)
                return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    async def raw_exchange(port, message):
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            writer.write(message)
            await writer.drain()
            return await reader.readline()
        finally:
            writer.close()
            await writer.wait_closed()

    async def hit(self, client, sequence, target=1074, amount=26):
        result = await self.drive(asyncio.to_thread(
            client.exchange, [dict(id=sequence, target_index=target, amount=amount)], timeout=2,
        ))
        self.assertEqual(result[0]['status'], 'confirmed')
        return result[0]

    async def test_two_reporters_over_one_public_port_with_retry_and_revocation(self):
        await self.start(combat_enabled=True)
        control, first_port = await self.link()
        _, second_port = await self.link()
        self.assertNotIn(self.public_port, (self.motion.port, self.combat.port))
        first_grant, second_grant = self.grant(1, 1073), self.grant(2, 943)
        first = CompanyCombatClient(first_port, first_grant)
        second = CompanyCombatClient(second_port, second_grant)
        self.addCleanup(first.close)
        self.addCleanup(second.close)

        self.assertEqual(await self.drive(self.raw_exchange(control, b'ready\n')), b'control:ready\n')
        self.assertIsNone(self.motion.current_combat_grant(first_grant['ticket']))
        self.assertEqual(self.motion.current_combat_grant(derive_combat_ticket(first_grant['ticket'])),
                         (first_grant['generation'], _EPOCH, 1073))
        wrong_domain = encode_frame(dict(kind='combat_impacts', ticket=first_grant['ticket'],
                                         generation=first_grant['generation'], registry_epoch=_EPOCH,
                                         shooter_index=1073, impacts=[dict(id=1, target_index=1074, amount=26)]))
        self.assertEqual(await self.drive(self.raw_exchange(first_port, wrong_domain)), b'')
        self.assertFalse(self.calls)

        original = await self.hit(first, 1)
        following = await self.hit(second, 1)
        self.assertEqual((original['order'], original['shield'], original['hull']), (1, 4, 3600))
        self.assertEqual((following['order'], following['shield'], following['hull']), (2, 0, 3578))
        first.close()
        self.assertEqual(await self.hit(first, 1), original)
        self.assertEqual(self.health[1074], (0, 3578))

        self.motion.revoke(1)
        replacement = self.grant(3, 1073)
        self.assertNotEqual(replacement['generation'], first_grant['generation'])
        with self.assertRaises(ConnectionError):
            await self.hit(first, 1)
        remaining = await self.hit(second, 2, target=1700, amount=10)
        self.assertEqual((remaining['order'], remaining['shield'], remaining['hull']), (3, 70, 2000))
        self.assertEqual(self.calls, [(1073, 1074, 26), (943, 1074, 26), (943, 1700, 10)])
        self.assertEqual(await self.drive(self.raw_exchange(control, b'alive\n')), b'control:alive\n')

    async def test_control_remains_available_when_combat_is_disabled(self):
        await self.start(combat_enabled=False)
        control, combat = await self.link()
        client = CompanyCombatClient(combat, self.grant(1, 1073))
        self.addCleanup(client.close)
        with self.assertRaises(ConnectionError):
            await self.hit(client, 1)
        self.assertFalse(self.calls)
        self.assertEqual(await self.drive(self.raw_exchange(control, b'ordinary\n')), b'control:ordinary\n')

    async def test_victim_reads_fresh_health_before_and_after_reconnect(self):
        self.combat.close()
        reads = []

        def begin_read(binding):
            reads.append(binding[2])
            shield, hull = self.health[binding[2]]
            result = dict(shield=shield, maxshield=2366, hull=hull, maxhull=3600)
            return Mock(poll=Mock(side_effect=[None, result]))

        self.combat = CompanyCombatService(
            self.motion.current_combat_grant, self.apply, begin_health_read=begin_read,
            health_window=lambda binding: self.motion.window if self.motion.running else None)
        self.addCleanup(self.combat.close)
        await self.start(combat_enabled=True)
        _, attacker_port = await self.link()
        _, victim_port = await self.link()
        attacker = CompanyCombatClient(attacker_port, self.grant(1, 1073))
        victim_grant = self.grant(2, 1074)
        victim = CompanyCombatClient(victim_port, victim_grant)
        self.addCleanup(attacker.close)
        self.addCleanup(victim.close)
        self.motion.set_running(True)
        window = self.motion.window
        await self.hit(attacker, 1)
        first = await self.drive(asyncio.to_thread(victim.read_health, window, 1))
        self.assertEqual((first['shield'], first['hull'], first['revision']), (4, 3600, 1))
        self.assertIsNone(await self.drive(asyncio.to_thread(victim.read_health, window, 2, first['revision'])))
        await self.hit(attacker, 1)
        self.assertIsNone(await self.drive(asyncio.to_thread(victim.read_health, window, 3, first['revision'])))
        await self.hit(attacker, 2)
        hit_again = await self.drive(asyncio.to_thread(victim.read_health, window, 4, first['revision']))
        self.assertEqual((hit_again['shield'], hit_again['hull'], hit_again['revision']), (0, 3578, 2))

        victim.close()
        self.health[1074] = (120, 3550)  # Simulate regeneration and other game activity during a gap.
        resumed = await self.drive(asyncio.to_thread(victim.read_health, window, 5, hit_again['revision']))
        self.assertEqual(resumed, dict(shield=120, maxshield=2366, hull=3550, maxhull=3600, revision=2))
        self.motion.set_running(False)
        self.motion.set_running(True)
        self.assertIsNone(await self.drive(asyncio.to_thread(victim.read_health, window, 6)))
        current = self.motion.window
        second = await self.drive(asyncio.to_thread(victim.read_health, current, 7, resumed['revision']))
        self.assertEqual(second, resumed)
        self.motion.revoke(2)
        self.grant(3, 1074)
        with self.assertRaises(ConnectionError):
            await self.drive(asyncio.to_thread(victim.read_health, current, 8))
        self.assertEqual(reads, [1074] * 4)
        self.assertEqual(self.calls, [(1073, 1074, 26)] * 2)


if __name__ == '__main__':
    unittest.main()
