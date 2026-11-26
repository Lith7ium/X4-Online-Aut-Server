"""Real pinned TLS and Company admissions with a synthetic damage adapter."""
import asyncio
from contextlib import ExitStack
import hashlib
from pathlib import Path
import ssl
import tempfile
import time
import unittest
from unittest.mock import Mock

from authority.company_combat_client import CompanyCombatClient
from authority.company_combat_frames import encode_frame
from authority.company_combat_service import CompanyCombatService, derive_combat_ticket
from authority.company_combat_source import CompanyCombatSender
from authority.company_cockpit_health import CompanyCockpitHealthBridge
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

    async def link(self, *, max_combat_connections=2):
        control, files, combat = (self.listener() for _ in range(3))
        ports = control.getsockname()[1], combat.getsockname()[1]
        tunnel = await start_client(
            control_listener=control, file_listener=files, combat_listener=combat,
            server_host='127.0.0.1', server_port=self.public_port, server_fingerprint=self.fingerprint,
            shared_key=_KEY, max_combat_connections=max_combat_connections, idle_timeout=15,
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

    async def test_idle_victim_sender_delivers_own_health_through_the_combat_stream(self):
        self.combat.close()
        state = dict(shield=4, maxshield=2366, hull=3600, maxhull=3600)
        read = Mock(return_value=Mock(poll=Mock(return_value=state)))
        self.combat = CompanyCombatService(self.motion.current_combat_grant, self.apply,
                                           begin_health_read=read, health_window=lambda binding: 1)
        self.addCleanup(self.combat.close)
        await self.start(combat_enabled=True)
        _, port = await self.link()
        grant = self.grant(1, 1074)
        binding = grant['generation'], grant['epoch'], grant['index']
        bridge = CompanyCockpitHealthBridge(binding, clock=lambda: 100.0)
        bridge.on_clock(1, True)
        bridge.on_sample(dict(sequence=1, capture_time=100))
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'engine.log'
            log.touch()
            info = log.stat()
            sender = CompanyCombatSender(log, port, grant, epoch=_EPOCH, index=1074,
                                         log_identity=(info.st_dev, info.st_ino), health_bridge=bridge)
            sent = []
            try:
                async with asyncio.timeout(3):
                    while not any(frame.startswith('HEALTH|') for frame in sent):
                        self.combat.poll()
                        bridge.deliver(lambda frame: sent.append(frame) is None)
                        await asyncio.sleep(.005)
            finally:
                result = sender.close()
            self.assertIsNone(result['failure'])
            self.assertEqual(result['observed'], 0)
            read.assert_called_once_with(binding)
            self.assertTrue(sent[-1].endswith('|4|2366|3600|3600'))

    async def test_four_pending_health_links_yield_to_ring_hits_at_shipped_limits(self):
        self.combat.close()
        pilots = (1073, 1074, 1075, 1076)
        targets = dict(zip(pilots, pilots[1:] + pilots[:1]))
        self.health = {index: (2366, 3600) for index in pilots}
        first_application = None

        def apply(binding, target, amount):
            nonlocal first_application
            if first_application is None:
                first_application = time.monotonic()
            return self.apply(binding, target, amount)

        def begin_read(binding):
            def poll():
                if not self.calls:
                    return None  # The initial read cannot complete before a real service application.
                shield, hull = self.health[binding[2]]
                return dict(shield=shield, maxshield=2366, hull=hull, maxhull=3600)
            return Mock(poll=poll)

        self.combat = CompanyCombatService(self.motion.current_combat_grant, apply,
                                           begin_health_read=begin_read, health_window=lambda binding: 1)
        self.addCleanup(self.combat.close)
        await self.start(combat_enabled=True)  # The gateway defaults to four combat relays.

        with tempfile.TemporaryDirectory() as directory, ExitStack() as owned:
            senders, logs, bridges = {}, {}, {}
            for handle, index in enumerate(pilots, 1):
                _, port = await self.link(max_combat_connections=1)
                grant = self.grant(handle, index)
                binding = grant['generation'], grant['epoch'], index
                self.assertEqual(self.motion.current_combat_grant(derive_combat_ticket(grant['ticket'])), binding)
                bridges[index] = CompanyCockpitHealthBridge(binding)
                bridges[index].on_clock(1, True)
                log = logs[index] = Path(directory) / f'{index}.log'
                log.write_text(f'[=ERROR=] 1.0 [X4LAN] AUTH_COMPANY_GUN_STATUS armed|{_EPOCH}|{index}\n')
                info = log.stat()
                sender = CompanyCombatSender(log, port, grant, epoch=_EPOCH, index=index,
                                             log_identity=(info.st_dev, info.st_ino), health_bridge=bridges[index])
                senders[index] = sender
                owned.callback(sender.close)

            deadline = None
            sequence = 0
            async with asyncio.timeout(7):
                while True:
                    sequence += 1
                    for bridge in bridges.values():
                        bridge.on_sample(dict(sequence=sequence, capture_time=time.monotonic()))
                    self.motion.poll()
                    self.combat.poll()
                    health = self.combat._health_reads
                    if deadline is None and len(health) == len(pilots):
                        self.assertEqual({query.peer.binding[2] for query in health}, set(pilots))
                        self.assertTrue(all(query.sequence == 1 for query in health))
                        deadline = health[0].deadline
                        for index, target in targets.items():
                            with logs[index].open('ab') as stream:
                                stream.write(f'[=ERROR=] 1.1 [X4LAN] AUTH_COMPANY_GUN {_EPOCH}|{index}|{target}|1\n'.encode())
                    if first_application is not None:
                        self.assertLess(first_application, deadline,
                                        'hit transport waited for the abandoned health query deadline')
                    for bridge in bridges.values():
                        frames = []
                        bridge.deliver(lambda frame: frames.append(frame) is None)
                        for frame in frames:
                            if frame.startswith('HEALTH|'):
                                fields = frame.split('|')
                                bridge.native_result(['HEALTH_RESULT', *fields[2:7], 'confirmed', *fields[9:]])
                    if (all(sender._counts['confirmed'] == 1 for sender in senders.values())
                            and all(bridge.stats().get('last_confirmed_readback', {}).get('server', {})
                                    .get('shield') == 2340 for bridge in bridges.values())):
                        break
                    await asyncio.sleep(.005)
            results = {index: sender.close() for index, sender in senders.items()}

        self.assertCountEqual(self.calls, [(index, target, 26) for index, target in targets.items()])
        self.assertEqual(self.health, {index: (2340, 3600) for index in pilots})
        orders = []
        for index, target in targets.items():
            row = results[index]
            self.assertEqual((row['observed'], row['confirmed'], row['failure']), (1, 1, None))
            receipt = row['last_confirmed_receipt'].copy()
            orders.append(receipt.pop('order'))
            self.assertEqual(receipt, dict(id=1, target_index=target, amount=26, status='confirmed',
                                           shield=2340, hull=3600))
            report = bridges[index].stats()['last_confirmed_readback']
            self.assertGreater(report['sequence'], 1)
            self.assertEqual(report['window'], 1)
            self.assertEqual(report['cockpit'], report['server'])
            self.assertEqual((report['server']['shield'], report['server']['hull']), (2340, 3600))
        self.assertCountEqual(orders, range(1, 5))


if __name__ == '__main__':
    unittest.main()
