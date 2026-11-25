"""Offline native-client orchestration/receipts and real UDP; never launch X4."""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools/dev')]
import authority_client_native as native


class NativeClientTests(unittest.TestCase):
    def ticket(self):
        return dict(world_epoch='a'*32, connection_handle=1, baseline_id=1, load_generation=1,
            checkpoint_id='checkpoint-'+'b'*32, sha256=hashlib.sha256(b'save').hexdigest(),
            attempt_id='c'*32, slot='save_009')

    def log_bytes(self):
        watch = native.download.ReceiptWatch(None, self.ticket())
        return (native.download.TAG+watch.expected[0]+b'\nAUTH_MARKER load INSPECTED '+b'd'*32+b'\n'+
                native.download.TAG+watch.expected[1]+b'\n')

    def test_rejected_native_evidence_never_claims_or_enrolls(self):
        ticket = self.ticket()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)/'engine-output.log'
            good = self.log_bytes()
            for bad in (good.replace(b'd'*32, b'e'*32), good+b'AUTH_MARKER load ERROR\n',
                        good.replace(b'AUTH_MARKER load INSPECTED '+b'd'*32+b'\n', b'')):
                log.write_bytes(bad)
                owner = SimpleNamespace(poll=lambda: None, load_requested=lambda attempt: self.fail('false load assertion'))
                participant = SimpleNamespace(poll_load_keepalive=Mock(),
                    enroll=lambda *args: self.fail('false enrollment'))
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    native.observe_and_activate(owner, participant, native.download.ReceiptWatch(log, ticket),
                        ticket, 'd'*32, lambda **kwargs: True, lambda: None, {})

    def test_real_udp_receipts_count_only_server_control_and_do_not_reenter_owner(self):
        owner = SimpleNamespace(poll=lambda: self.fail('active controller must not poll owner'))
        participant = native.NativeParticipant(owner, time.monotonic()+5, lambda **kwargs: True)
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            participant.loading = False
            participant.server_control = True
            participant.epoch, participant.handle = 'a'*32, 1
            peer.connect(participant.sock.getsockname())
            participant.peer = peer.getsockname()
            executable = Path('/proc/self/exe').stat()
            participant.process = dict(pid=os.getpid(), start_ticks=native.live.start_ticks(os.getpid()),
                exe_dev=executable.st_dev, exe_ino=executable.st_ino)
            def respond():
                data = peer.recv(769)
                peer.send(b'RECEIPT|'+data.split(b'|', 1)[1]+b'|confirmed')
            thread = threading.Thread(target=respond)
            thread.start()
            self.assertTrue(participant.resume((1,)))
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(participant.confirmations, {'pause': 0, 'resume': 1})
        finally:
            participant.close()
            peer.close()

    def test_marker_registry_failure_prevents_load_assertions_and_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)/'log'; log.write_bytes(self.log_bytes())
            owner = SimpleNamespace(poll=lambda: None, load_requested=Mock(), activate=Mock())
            participant = SimpleNamespace(enroll=Mock(), poll_load_keepalive=Mock())
            with self.assertRaisesRegex(ValueError, 'registry rejected'):
                native.observe_and_activate(owner, participant,
                    native.download.ReceiptWatch(log, self.ticket()), self.ticket(), 'd'*32,
                    lambda **kwargs: True, lambda: None, {},
                    registry_check=Mock(side_effect=ValueError('registry rejected')))
            owner.load_requested.assert_not_called()
            owner.activate.assert_not_called()
            participant.enroll.assert_not_called()

    def test_active_pause_requires_stopped_but_initial_local_pause_is_allowed(self):
        participant = native.NativeParticipant(SimpleNamespace(), time.monotonic()+5, lambda **kwargs: True)
        participant.playback = SimpleNamespace(before_pause=Mock(side_effect=RuntimeError('not stopped')))
        try:
            with patch.object(participant, '_peer_guard'), patch.object(native.Participant, 'request', return_value=True) as request:
                participant.request('pause')
                self.assertEqual(request.call_count, 1)
                participant.playback.before_pause.assert_not_called()
                participant.server_control = True
                with self.assertRaisesRegex(RuntimeError, 'not stopped'):
                    participant.request('pause')
                self.assertEqual(request.call_count, 1)
                participant.playback.before_pause.side_effect = None
                participant.request('pause')
                self.assertEqual(participant.confirmations['pause'], 1)
        finally:
            participant.close()

    def test_motion_consumer_attached_after_checked_native_load(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)/'log'
            log.write_bytes(self.log_bytes())
            events = []
            owner = SimpleNamespace(poll=Mock(),
                load_requested=lambda attempt: events.append('load'),
                loaded_marker=lambda *args: events.append('marker'),
                paused=lambda *args: events.append('paused'),
                activate=Mock(side_effect=lambda *args, **kwargs: events.append('activate')))
            participant = SimpleNamespace(enroll=Mock(), request=Mock(), poll_load_keepalive=Mock(),
                                          deadline=time.monotonic()+10)
            consumer, sale_exchange = Mock(), Mock()
            native.observe_and_activate(owner, participant,
                native.download.ReceiptWatch(log, self.ticket()), self.ticket(), 'd'*32,
                lambda **kwargs: True, lambda: events.append('verified'), {},
                registry_check=lambda: events.append('registry'), motion_consumer=consumer,
                company_sale_exchange=sale_exchange)
            self.assertEqual(events, ['verified', 'registry', 'load', 'marker', 'paused',
                                      'verified', 'registry', 'activate'])
            self.assertIs(owner.activate.call_args.kwargs['apply_motion'], consumer)
            self.assertIs(owner.activate.call_args.kwargs['company_sale_exchange'], sale_exchange)
            consumer.assert_not_called()
            sale_exchange.assert_not_called()
            self.assertFalse(participant.loading)
            self.assertTrue(participant.server_control)

    def test_load_polling_requires_configured_keepalive(self):
        for interval in (None, 1.0):
            with self.subTest(interval=interval):
                owner = Mock()
                if interval is None:
                    owner.poll.side_effect = RuntimeError('keepalive not configured')
                guard = Mock()
                participant = native.NativeParticipant(owner, time.monotonic()+5, guard,
                    keepalive_interval=interval)
                self.addCleanup(participant.close)
                watch = SimpleNamespace(poll=Mock(return_value=True), stage=2, path=None)
                result = {}
                with patch.object(native, 'verify_receipts'), \
                     patch.object(participant, 'enroll', side_effect=lambda *args: participant._wait()), \
                     patch.object(participant, 'request'):
                    native.observe_and_activate(owner, participant, watch, self.ticket(), 'd'*32,
                        guard, Mock(), result)
                self.assertEqual(result['phase'], 'active')
                participant._wait()
                self.assertEqual(owner.poll.call_count, 0 if interval is None else 2)

    def test_main_forwards_argv_and_python_only_driver(self):
        from contextlib import redirect_stdout
        import io
        callback = lambda initial: []
        argv = ['--internal-native-client']
        for name in ('game', 'out', 'shared-key-file', 'file-token-file', 'native-library'):
            argv.extend(['--'+name, '/private/'+name])
        for name, value in (('display', ':9'), ('host', '127.0.0.1'), ('fingerprint', 'a'*64),
                            ('native-library-sha256', 'b'*64), ('port', '1'), ('slot', '1'),
                            ('internal-saved-playback-config', '/private/playback.json')):
            argv.extend(['--'+name, value])
        with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
            self.assertEqual(native.main(argv, playback_driver=callback), 0)
        self.assertIs(run.call_args.kwargs['playback_driver'], callback)
        self.assertEqual(run.call_args.args[0].internal_saved_playback_config, Path('/private/playback.json'))

    def test_launch_failure_stops_owned_process_before_connection_and_lock_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            library = root/'net.so'; library.write_bytes(b'library')
            ticket = self.ticket()
            bound = SimpleNamespace(offer=SimpleNamespace(**{k: v for k, v in ticket.items() if k != 'slot'}), marker='d'*32)
            class Owner:
                def request_offer(self): return bound
                def download(self, directory):
                    directory.mkdir(); path = directory/'save.xml.gz'; path.write_bytes(b'save'); return path
                def verify_staged(self, path): pass
                def copy_to_new_slot(self, source, target): target.write_bytes(source.read_bytes())
            @contextmanager
            def connection(**options):
                try: yield Owner()
                finally: events.append('connection_closed')
            def prepare(out, game, home):
                case = out/'case'; profile = case/'home/.config/EgoSoft/X4/45841'
                (profile/'save').mkdir(parents=True)
                (profile/'config.xml').write_text('config'); (profile/'content.xml').write_text('content')
                lab = case/'extensions/x4_lan_lab'
                for name in ('ui', 'md', 'native'): (lab/name).mkdir(parents=True)
                (lab/'ui/config.lua').write_text('X4LAN_CONFIG = {}\n')
                (lab/'ui/native_gate_snapshot.lua').write_text('old')
                (lab/'md/native_gate_battle.xml').write_text('old')
                return case, profile, [], case/'lock'
            def spawn(command, log, processes):
                processes.append([SimpleNamespace(), 1]); events.append('launch_registered')
                raise OSError('PRIVATE launch detail')
            def stop(services, processes, result, errors):
                events.append('native_stopped'); result['owned_cleanup_confirmed'] = True
                return True
            def finish(fd, st, stopped, errors):
                self.assertTrue(stopped); events.append('lock_released')
            args = SimpleNamespace(timeout=10, active_seconds=1, host='host', port=1, fingerprint='0'*64,
                slot=1, keepalive_interval=None, display=':1', seed=1, native_library_sha256=hashlib.sha256(b'library').hexdigest(),
                shared_key_file=root/'key', file_token_file=root/'token', archived_identity_fixture=None,
                archived_identity_fixture_sha256=None, expected_content_sha256=None)
            args.shared_key_file.write_text('a'*64); args.file_token_file.write_text('b'*32)
            out = root/'new'
            environment = (out, root, root/'auth', library, SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_name='test'), root, 'steam')
            with ExitStack() as stack:
                for target, name, replacement in (
                    (native, 'validate_environment', lambda args: environment),
                    (native, 'client_connection', connection),
                    (native.snapshot, 'prepare_case', prepare),
                    (native.load, 'readonly_save_command', lambda *args: ['bwrap', '--die-with-parent']),
                    (native.load, 'spawn_owned_gated', spawn),
                    (native.download, 'create_owned_lock', lambda uid, gid, locks: locks.append((1, 2))),
                    (native.download, 'stop_owned_resources', stop),
                    (native.download, 'finish_owned_lock', finish),
                    (native.live, 'steam_identity', lambda *args: 'steam')):
                    stack.enter_context(patch.object(target, name, side_effect=replacement))
                previous = signal.getsignal(signal.SIGINT)
                result = native.run(args)
                self.assertIs(signal.getsignal(signal.SIGINT), previous)
                self.assertFalse(result['success'])
                self.assertEqual(result['failure_phase'], 'launch')
                self.assertEqual(result['error_type'], 'OSError')
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertEqual(events, ['launch_registered', 'native_stopped', 'connection_closed', 'lock_released'])
                events.clear()
                @contextmanager
                def failed_connection(**options):
                    raise RuntimeError('tls_startup_failed')
                    yield  # Context entry must fail before the native body starts.
                startup_environment = (root/'startup-failure', *environment[1:])
                with patch.object(native, 'validate_environment', return_value=startup_environment), \
                        patch.object(native, 'client_connection', failed_connection), \
                        patch.object(native.download, 'stop_owned_resources', side_effect=stop) as stop_empty:
                    failed_result = native.run(args)
                self.assertEqual(stop_empty.call_args.args[1], [])
                self.assertEqual(events, ['native_stopped', 'lock_released'])
                self.assertEqual(failed_result['failure_phase'], 'connect')
                self.assertEqual(failed_result['error_code'], 'tls_startup_failed')
                self.assertTrue(failed_result['owned_cleanup_confirmed'])
                self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_failed_final_evidence_releases_only_fully_fenced_original_lock(self):
        # Use real lock creation/release against a fresh temporary path. All X4,
        # connection and worker behavior is simulated; never touch the host lock.
        for scenario in ('clean', 'late_failure', 'worker_uncertain', 'service_uncertain', 'native_uncertain',
                         'replaced_lock', 'changed_input', 'steam_changed'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); out = root/'new'
                library = root/'net.so'; library.write_bytes(b'library')
                lock_path = root/'owned.lock'
                ticket = self.ticket()
                bound = SimpleNamespace(offer=SimpleNamespace(**{k: v for k, v in ticket.items() if k != 'slot'}), marker='d'*32)
                events = []
                class Owner:
                    def request_offer(self): return bound
                    def download(self, destination):
                        destination.mkdir(); path = destination/'save.xml.gz'; path.write_bytes(b'save'); return path
                    def copy_to_new_slot(self, source, target): target.write_bytes(source.read_bytes())
                    def poll(self, *args): pass
                @contextmanager
                def connection(**options):
                    try:
                        yield Owner()
                    finally:
                        events.append('connection_closed')
                        if scenario == 'replaced_lock':
                            lock_path.unlink(); lock_path.write_bytes(b'replacement')
                        if scenario == 'service_uncertain':
                            raise RuntimeError('tls_cleanup_failed')
                def prepare(out, game, home):
                    case = out/'case'; profile = case/'home/.config/EgoSoft/X4/45841'
                    (profile/'save').mkdir(parents=True)
                    for name in ('config.xml', 'content.xml'): (profile/name).write_text(name)
                    lab = case/'extensions/x4_lan_lab'
                    for name in ('ui', 'md', 'native'): (lab/name).mkdir(parents=True)
                    (lab/'ui/config.lua').write_text('X4LAN_CONFIG = {}\n')
                    (lab/'ui/native_gate_snapshot.lua').write_text('old')
                    (lab/'md/native_gate_battle.xml').write_text('old')
                    return case, profile, [], case/'lock'
                def spawn(command, log, processes):
                    processes.append((SimpleNamespace(poll=lambda: None), 1))
                def stop(services, processes, result, errors):
                    events.append('native_fenced')
                    confirmed = scenario != 'native_uncertain'
                    result.update(owned_cleanup_confirmed=confirmed, owned_processes_remaining=[] if confirmed else None)
                    if not confirmed: errors.append('process_cleanup_unconfirmed')
                    return confirmed
                class Playback:
                    paths = []
                    thread = object()
                    evidence = dict(worker_joined=False)
                    def verify_registry(self): return True
                    def poll(self): pass
                    def start(self, *args): raise AssertionError('already started')
                    def finish(self): return dict(self.evidence)
                    def records(self):
                        if scenario == 'late_failure':
                            return None
                        if scenario == 'changed_input':
                            (out/'case/home/.config/EgoSoft/X4/45841/config.xml').write_text('changed')
                        raise ValueError('native_playback_faulted')
                    def close(self, *, native_stopped):
                        if not native_stopped or scenario == 'worker_uncertain':
                            raise RuntimeError('native_playback_worker_not_joined')
                        self.evidence = dict(worker_joined=True, configuration_redacted=True, cleanup_confirmed=True,
                                             failure_code='native_playback_faulted')
                        if scenario == 'late_failure':
                            self.failure = RuntimeError('native_playback_worker_failed')
                        events.append('playback_redacted')
                playback = Playback()
                def activate(owner, participant, watch, ticket, marker, guard, verify_loaded, result, **kwargs):
                    participant.confirmations['resume'] = 1
                    result.update(phase='active', admission_state='accepted')
                args = SimpleNamespace(timeout=10, active_seconds=.001, host='host', port=1, fingerprint='0'*64,
                    slot=1, keepalive_interval=None, display=':1', seed=1,
                    native_library_sha256=hashlib.sha256(b'library').hexdigest(),
                    shared_key_file=root/'key', file_token_file=root/'token', archived_identity_fixture=None,
                    archived_identity_fixture_sha256=None, expected_content_sha256=None)
                args.shared_key_file.write_text('a'*64); args.file_token_file.write_text('b'*32)
                user = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(), pw_name='test')
                with ExitStack() as stack:
                    for target, name, replacement in (
                        (native, 'validate_environment', lambda args: (out, root, root/'auth', library, user, root, 'steam')),
                        (native, 'client_connection', connection),
                        (native, 'observe_and_activate', activate),
                        (native, 'verify_receipts', lambda *args: True),
                        (native.native_playback, 'read_config', lambda *args: {}),
                        (native.native_playback, 'install', lambda *args: playback),
                        (native.snapshot, 'prepare_case', prepare),
                        (native.load, 'readonly_save_command', lambda *args: ['bwrap', '--die-with-parent']),
                        (native.load, 'spawn_owned_gated', spawn),
                        (native.download, 'game_identity', lambda *args: (os.getpid(), 1)),
                        (native.download, 'stop_owned_resources', stop),
                        (native.live, 'steam_identity', lambda *args: 'changed' if scenario == 'steam_changed' and 'connection_closed' in events else 'steam')):
                        stack.enter_context(patch.object(target, name, side_effect=replacement))
                    stack.enter_context(patch.object(native.snapshot, 'LOCK_TARGET', lock_path))
                    result = native.run(args, playback_driver=lambda initial: [])
                self.assertFalse(result['success'])
                self.assertEqual(result['final_native_evidence_valid'], scenario == 'late_failure')
                self.assertEqual(result['evidence_errors'],
                                 ['playback_failed' if scenario == 'late_failure' else 'final_native_evidence_invalid'])
                if scenario in ('clean', 'late_failure'):
                    self.assertNotIn('cleanup_errors', result)
                    self.assertFalse(lock_path.exists())
                    self.assertTrue(result['owned_cleanup_confirmed'])
                    self.assertTrue(result['playback']['cleanup_confirmed'])
                else:
                    self.assertTrue(lock_path.exists())
                    self.assertTrue(result['cleanup_errors'])
                    if scenario == 'replaced_lock':
                        self.assertEqual(lock_path.read_bytes(), b'replacement')
                    if scenario == 'changed_input':
                        self.assertIn('final_native_inputs_invalid', result['cleanup_errors'])
                if 'playback_redacted' in events:
                    self.assertLess(events.index('native_fenced'), events.index('playback_redacted'))


if __name__ == '__main__':
    unittest.main()
