"""Real local TLS and owned subprocess staging; simulated host, no X4/WAN."""
import asyncio
import contextlib
import io
import tempfile
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import ssl
import sys
import time
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests')]
from authority import experimental_client_connection as subject
from authority.experimental_tls_tunnel import create_server_context, start_server
import test_authority_experimental_local_host as host_fixture
from test_authority_tls_tunnel_integration import TEST_CERTIFICATE, TEST_PRIVATE_KEY, listening_socket

# tools/dev/authority_client_native.py is the Linux-only client and imports pwd.
HAS_PWD = importlib.util.find_spec('pwd') is not None


class IndependentStageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = host_fixture.HostTest(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = Path(self.fixture.temp.name)
        certificate, key = self.root/'certificate.pem', self.root/'key.pem'
        certificate.write_text(TEST_CERTIFICATE)
        key.write_text(TEST_PRIVATE_KEY)
        context = create_server_context(certificate, key)
        self.fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest()
        listener = listening_socket()
        self.server_port = listener.getsockname()[1]
        self.server = await start_server(listener=listener, ssl_context=context, shared_key=b'k'*32,
            control_port=self.fixture.listener.getsockname()[1], file_port=self.fixture.service.port)
        self.addAsyncCleanup(self.server.close)
        self.fixture.listener.setblocking(False)
        self.fixture.host.tick()
        self.advance_host = True
        self.adopted = asyncio.Event()
        self.pump = asyncio.create_task(self._pump())
        self.addAsyncCleanup(self._stop_pump)
        self.processes, self.listeners = [], []
        original_spawn, original_listener = asyncio.create_subprocess_exec, subject._listener
        self.original_spawn = original_spawn
        async def record_spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            self.processes.append(process)
            return process
        def record_listener():
            local = original_listener()
            self.listeners.append(local)
            return local
        self.spawn_patch = patch.object(subject.asyncio, 'create_subprocess_exec', side_effect=record_spawn)
        self.listener_patch = patch.object(subject, '_listener', side_effect=record_listener)
        self.spawn_patch.start(); self.addCleanup(self.spawn_patch.stop)
        self.listener_patch.start(); self.addCleanup(self.listener_patch.stop)

    async def _pump(self):
        while True:
            if select.select([self.fixture.listener], [], [], 0)[0]:
                connection, _ = self.fixture.listener.accept()
                self.fixture.host.adopt(connection)
                self.adopted.set()
            if self.advance_host:
                self.fixture.host.tick(0)
            await asyncio.sleep(.005)

    async def _stop_pump(self):
        self.pump.cancel()
        await asyncio.gather(self.pump, return_exceptions=True)

    def options(self, **changes):
        options = dict(server_host='127.0.0.1', server_port=self.server_port,
            server_fingerprint=self.fingerprint, shared_key=b'k'*32,
            file_token='a'*32, file_slot=1, new_directory=self.root/'download',
            deadline=time.monotonic()+8, io_timeout=2, keepalive_interval=.2)
        options.update(changes)
        return options

    async def assert_resources_closed(self):
        self.assertTrue(self.processes)
        self.assertTrue(all(process.returncode is not None for process in self.processes))
        self.assertTrue(all(listener.fileno() == -1 for listener in self.listeners))
        await asyncio.sleep(.08)  # Let the actual host observe EOF, without claiming.
        self.assertEqual(self.fixture.world.session.players, ())
        self.assertEqual(self.fixture.world.session.ready_players, ())

    @unittest.skipUnless(HAS_PWD, 'authority_client_native is the Linux-only client (needs pwd)')
    async def test_native_staging_real_tls_simulated_load_activation_and_cleanup(self):
        from authority import experimental_client_session as session
        sys.path.insert(0, str(ROOT/'tools/dev'))
        import authority_client_native as native
        options = self.options()
        directory = options.pop('new_directory')
        target = self.root/'native-profile/save_009.xml.gz'
        target.parent.mkdir()
        def operation():
            with session.client_connection(**options) as owner:
                bound = owner.request_offer()
                path = native.stage_offered_save(owner, directory, target)
                self.assertEqual(path, target)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), bound.offer.sha256)
                self.assertEqual(path.stat().st_size, bound.offer.size)
                # X4 observations/controllers are simulated; TLS, owner and host are real.
                attempt = bound.offer.attempt_id
                owner.load_requested(attempt)
                owner.loaded_marker(attempt, bound.marker)
                owner.paused(attempt, True)
                class SimulatedController:
                    def pause(self, handles): return True
                    def resume(self, handles): return True
                owner.activate(attempt, SimulatedController())
                owner.poll()
                self.assertTrue(any(thread.name == 'authority-client-tls' for thread in threading.enumerate()))
            self.assertFalse(any(thread.name == 'authority-client-tls' for thread in threading.enumerate()))
        with patch.object(session, '_listener', side_effect=subject._listener):
            await asyncio.to_thread(operation)
        self.assertIn(('resume', (1,)), self.fixture.participants.calls)
        self.assertTrue(all(listener.fileno() == -1 for listener in self.listeners))
        await asyncio.sleep(.08)
        self.assertEqual(self.fixture.world.session.ready_players, ())
        self.assertEqual(self.fixture.world.session.players, ())

    @unittest.skipUnless(HAS_PWD, 'authority_client_native is the Linux-only client (needs pwd)')
    async def test_native_staging_metadata_change_rejected_at_real_owner_claim(self):
        from authority import experimental_client_session as session
        sys.path.insert(0, str(ROOT/'tools/dev'))
        import authority_client_native as native
        options = self.options()
        directory = options.pop('new_directory')
        target = self.root/'changed-profile/save_009.xml.gz'
        target.parent.mkdir()
        def operation():
            with session.client_connection(**options) as owner:
                bound = owner.request_offer()
                native.stage_offered_save(owner, directory, target)
                target.chmod(0o400)  # Deliberate simulated mutation, never part of native staging.
                attempt = bound.offer.attempt_id
                owner.load_requested(attempt)
                owner.loaded_marker(attempt, bound.marker)
                owner.paused(attempt, True)
                class SimulatedController:
                    def pause(self, handles): return True
                    def resume(self, handles): return True
                with self.assertRaisesRegex(ValueError, '^staged save changed during load attempt$') as raised:
                    owner.activate(attempt, SimulatedController())
                self.assertEqual(native.error_code(raised.exception), 'staged_save_changed_during_load')
        await asyncio.to_thread(operation)
        await asyncio.sleep(.08)
        self.assertEqual(self.fixture.world.session.ready_players, ())
        self.assertEqual(self.fixture.world.session.players, ())

    async def test_sync_session_startup_failure_closes_listeners_without_cleanup_failure(self):
        from authority import experimental_client_session as session
        options = self.options()
        options.pop('new_directory')
        async def refuse_start(**options):
            raise ValueError('private startup details')
        def operation():
            with self.assertRaisesRegex(session.ClientConnectionError, '^tls_startup_failed$') as raised:
                with session.client_connection(**options):
                    self.fail('startup must fail')
            self.assertFalse(getattr(raised.exception, '__notes__', ()))
            self.assertFalse(any(thread.name == 'authority-client-tls' for thread in threading.enumerate()))
        with patch.object(session, '_listener', side_effect=subject._listener), \
                patch.object(session, 'start_client', side_effect=refuse_start):
            await asyncio.to_thread(operation)
        self.assertTrue(all(listener.fileno() == -1 for listener in self.listeners))

    async def test_sync_session_cleanup_failure_preserves_original_and_joins_thread(self):
        from authority import experimental_client_session as session
        options = self.options()
        options.pop('new_directory')
        original_close = session._TLSBridgeThread.close
        def fail_after_close(bridge):
            original_close(bridge)
            raise RuntimeError('private cleanup details')
        def operation():
            with self.assertRaisesRegex(ValueError, '^original$') as raised:
                with session.client_connection(**options) as owner:
                    owner.request_offer()
                    raise ValueError('original')
            self.assertIn('tls_cleanup_failed', raised.exception.__notes__)
            self.assertFalse(any(thread.name == 'authority-client-tls' for thread in threading.enumerate()))
        with patch.object(session._TLSBridgeThread, 'close', fail_after_close):
            await asyncio.to_thread(operation)

    async def test_tls_stop_before_loop_ready_does_not_leave_listeners_or_thread(self):
        from authority import experimental_client_session as session
        closed = threading.Event()
        class Tunnel:
            async def close(self): closed.set()
        async def start(**options): return Tunnel()
        def operation():
            bridge = session._TLSBridgeThread({})
            bridge.close()
            bridge.thread.start()
            bridge.thread.join(3)
            self.assertFalse(bridge.thread.is_alive())
            self.assertFalse(bridge.failed)
            self.assertTrue(closed.is_set())
        with patch.object(session, '_listener', side_effect=subject._listener), \
                patch.object(session, 'start_client', side_effect=start):
            await asyncio.to_thread(operation)
        self.assertTrue(all(listener.fileno() == -1 for listener in self.listeners))

    async def test_verified_download_without_admission(self):
        result = await subject.stage_client_connection(**self.options())
        path = self.root/'download/save.xml.gz'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result['sha256'])
        self.assertEqual(path.stat().st_size, result['size'])
        self.assertEqual(result['status'], 'staged')
        self.assertIs(result['admitted'], False)
        await self.assert_resources_closed()

    async def test_wrong_certificate_pin_never_downloads(self):
        with self.assertRaises(subject.ClientStageError):
            await subject.stage_client_connection(**self.options(server_fingerprint='0'*64))
        self.assertFalse((self.root/'download').exists())
        await self.assert_resources_closed()

    async def test_wrong_shared_key_never_downloads(self):
        with self.assertRaises(subject.ClientStageError):
            await subject.stage_client_connection(**self.options(shared_key=b'x'*32))
        self.assertFalse((self.root/'download').exists())
        await self.assert_resources_closed()

    async def test_wrong_file_token_preserves_owned_failed_directory(self):
        with self.assertRaises(subject.ClientStageError) as caught:
            await subject.stage_client_connection(**self.options(file_token='b'*32))
        self.assertEqual(str(caught.exception), 'staging_failed')
        self.assertTrue((self.root/'download').is_dir())
        self.assertFalse((self.root/'download/save.xml.gz').exists())
        await self.assert_resources_closed()

    async def test_cancellation_reaps_worker_before_releasing_listeners(self):
        self.advance_host = False
        task = asyncio.create_task(subject.stage_client_connection(**self.options()))
        await asyncio.wait_for(self.adopted.wait(), 3)
        original_reap = subject._reap
        async def checked_reap(process):
            self.assertTrue(all(listener.fileno() >= 0 for listener in self.listeners))
            await original_reap(process)
        with patch.object(subject, '_reap', side_effect=checked_reap):
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.advance_host = True
        await self.assert_resources_closed()

    async def test_success_frame_followed_by_nonzero_exit_is_failure(self):
        original_spawn = self.original_spawn
        result = dict(version=1, status='staged', sha256='a'*64, size=1,
            checkpoint_id='checkpoint-'+'b'*32, admitted=False)
        async def failed_worker(*args, **kwargs):
            process = await original_spawn(sys.executable, '-c',
                'import sys;sys.stdin.buffer.read();print('+repr(json.dumps(result))+');sys.exit(1)', **kwargs)
            self.processes.append(process)
            return process
        with patch.object(subject.asyncio, 'create_subprocess_exec', side_effect=failed_worker):
            with self.assertRaises(subject.ClientStageError):
                await subject.stage_client_connection(**self.options())
        await self.assert_resources_closed()

    async def test_malformed_success_output_closes_all_resources(self):
        async def malformed_worker(*args, **kwargs):
            process = await self.original_spawn(sys.executable, '-c',
                'import sys;sys.stdin.buffer.read();print("{invalid json")', **kwargs)
            self.processes.append(process)
            return process
        with patch.object(subject.asyncio, 'create_subprocess_exec', side_effect=malformed_worker):
            with self.assertRaisesRegex(subject.ClientStageError, '^invalid_worker_result$'):
                await subject.stage_client_connection(**self.options())
        await self.assert_resources_closed()

    async def test_failed_spawn_closes_listeners_without_claiming_cleanup_failure(self):
        with patch.object(subject.asyncio, 'create_subprocess_exec', side_effect=OSError('private spawn failure')):
            with self.assertRaises(OSError) as caught:
                await subject.stage_client_connection(**self.options())
        self.assertFalse(getattr(caught.exception, '__notes__', ()))
        self.assertFalse(self.processes)
        self.assertTrue(all(listener.fileno() == -1 for listener in self.listeners))

    async def test_fragmented_result_is_read_through_eof(self):
        original_spawn = self.original_spawn
        result = dict(version=1, status='staged', sha256='a'*64, size=1,
            checkpoint_id='checkpoint-'+'b'*32, admitted=False)
        payload = json.dumps(result)
        async def fragmented_worker(*args, **kwargs):
            script = ('import sys,time;sys.stdin.buffer.read();'
                'sys.stdout.write('+repr(payload[:25])+');sys.stdout.flush();time.sleep(.05);'
                'sys.stdout.write('+repr(payload[25:])+');sys.stdout.flush()')
            process = await original_spawn(sys.executable, '-c', script, **kwargs)
            self.processes.append(process)
            return process
        with patch.object(subject.asyncio, 'create_subprocess_exec', side_effect=fragmented_worker):
            observed = await subject.stage_client_connection(**self.options())
        self.assertEqual(observed, result)
        await self.assert_resources_closed()

    async def test_cancellation_during_spawn_still_reaps_returned_process(self):
        original_spawn = self.original_spawn
        created, release_spawn = asyncio.Event(), asyncio.Event()
        async def delayed_spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            self.processes.append(process)
            created.set()
            await release_spawn.wait()
            return process
        with patch.object(subject.asyncio, 'create_subprocess_exec', side_effect=delayed_spawn):
            task = asyncio.create_task(subject.stage_client_connection(**self.options()))
            await asyncio.wait_for(created.wait(), 3)
            task.cancel()
            release_spawn.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await self.assert_resources_closed()

    async def test_oversized_result_is_bounded_and_worker_reaped(self):
        original_spawn = self.original_spawn
        async def noisy_worker(*args, **kwargs):
            process = await original_spawn(sys.executable, '-c',
                'import os,signal,sys,time;'
                'signal.signal(signal.SIGTERM,signal.SIG_IGN) if os.name=="posix" else None;'
                'sys.stdin.buffer.read();sys.stdout.write("x"*2000000);sys.stdout.flush();time.sleep(30)', **kwargs)
            self.processes.append(process)
            return process
        with patch.object(subject.asyncio, 'create_subprocess_exec', side_effect=noisy_worker):
            with self.assertRaisesRegex(subject.ClientStageError, '^worker_result_too_large$'):
                await subject.stage_client_connection(**self.options())
        await self.assert_resources_closed()
        if os.name == 'posix':
            self.assertEqual(self.processes[-1].returncode, -signal.SIGKILL)


class CLIValidationTests(unittest.TestCase):
    def test_ctrl_c_preserves_cleanup_failure_and_ordinary_cancellation(self):
        sys.path.insert(0, str(ROOT/'tools/dev'))
        import authority_client_stage as cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_file, token_file = root/'key', root/'token'
            key_file.write_bytes(b'a'*64)
            token_file.write_bytes(b'b'*32)
            arguments = ['authority_client_stage.py', '--host', '127.0.0.1', '--port', '1',
                '--fingerprint', '0'*64, '--shared-key-file', str(key_file),
                '--file-token-file', str(token_file), '--slot', '1', '--out', str(root/'new')]
            for cleanup_note in (None, 'worker_cleanup_failed', 'tunnel_cleanup_failed'):
                async def interrupted_stage(**options):
                    asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGINT)
                    try:
                        await asyncio.sleep(30)
                    except asyncio.CancelledError as error:
                        if cleanup_note is not None:
                            error.add_note(cleanup_note)
                        raise
                output = io.StringIO()
                with self.subTest(cleanup_note=cleanup_note), patch.object(sys, 'argv', arguments), \
                        patch.object(cli, 'stage_client_connection', side_effect=interrupted_stage), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 1)
                expected = 'cleanup_failed' if cleanup_note else 'cancelled'
                self.assertEqual(json.loads(output.getvalue()), {'status': 'failed', 'error_class': expected})

    def test_trailing_or_oversized_credentials_never_start_staging(self):
        sys.path.insert(0, str(ROOT/'tools/dev'))
        import authority_client_stage as cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_file, token_file = root/'key', root/'token'
            for invalid in ('key', 'token'):
                key_file.write_bytes(b'a'*64+b'\n')
                token_file.write_bytes(b'b'*32+b'\n')
                target = key_file if invalid == 'key' else token_file
                target.write_bytes(target.read_bytes()+b'\nPRIVATE_TRAILING_CONTENT'*100)
                arguments = ['authority_client_stage.py', '--host', '127.0.0.1', '--port', '1',
                    '--fingerprint', '0'*64, '--shared-key-file', str(key_file),
                    '--file-token-file', str(token_file), '--slot', '1', '--out', str(root/'new')]
                output = io.StringIO()
                with self.subTest(invalid=invalid), patch.object(sys, 'argv', arguments), \
                        patch.object(cli, 'stage_client_connection') as staging, contextlib.redirect_stdout(output):
                    self.assertEqual(cli.main(), 1)
                    staging.assert_not_called()
                self.assertEqual(json.loads(output.getvalue()), {'status': 'failed', 'error_class': 'staging_failed'})
                self.assertNotIn('PRIVATE', output.getvalue())


if __name__ == '__main__':
    unittest.main()
