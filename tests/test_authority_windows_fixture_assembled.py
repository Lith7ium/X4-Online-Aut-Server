"""Loopback fixture-session composition; Windows process and X4 are simulated.

Real TLS, file transfer, workspace, overlay, log parsing, TCP and UDP are used.
This is not a Windows, X4, Internet or gameplay test.
"""
import asyncio
import gzip
import hashlib
import json
from pathlib import Path
import re
import socket
import ssl
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]

from authority import experimental_windows_fixture_overlay as overlay
from authority import windows_fixture_session as fixture_session
from authority.experimental_tcp_control import offer_request
from authority.experimental_tcp_motion import motion_digest
from authority.experimental_tls_tunnel import create_server_context, start_server
from authority.load_messages import Offer
from authority.trusted_load_messages import decode_load_claim, encode_bound_offer
from authority.trusted_marker_load_session import MarkerBoundOffer
from lockstep.launcher.joinfiles import FileService
from test_authority_tls_tunnel_integration import TEST_CERTIFICATE, TEST_PRIVATE_KEY, listening_socket
from test_authority_registry_save_evidence import xml as registry_xml, EPOCH as REGISTRY_EPOCH
import test_authority_windows_fixture_workspace as workspace_fixture


class AssembledFixtureSessionTest(unittest.TestCase):
    # The existing fixture doubles Windows APIs and view creation, but uses the
    # real workspace and temporary on-disk profile, save and overlay.
    setUp = workspace_fixture.WindowsFixtureWorkspaceTests.setUp
    _command = workspace_fixture.WindowsFixtureWorkspaceTests._command
    _create_view = workspace_fixture.WindowsFixtureWorkspaceTests._create_view

    def test_download_admit_command_and_cleanup_over_real_local_transports(self):
        self.run_assembled()

    def test_motion_waits_for_resume_then_stops_before_pause(self):
        self.run_assembled(motion_enabled=True)

    def test_failed_motion_stop_prevents_pause_receipt_and_still_closes_process(self):
        self.run_assembled(motion_enabled=True, bad_stop=True)

    def test_v3_clocks_rates_and_stop_survive_assembled_windows_path(self):
        self.run_assembled(motion_enabled=True, sample_version=3)

    def test_v3_duplicate_capture_never_dispatches_another_bind(self):
        self.run_assembled(motion_enabled=True, sample_version=3, invalid_motion='capture_duplicate')

    def test_v3_regressed_capture_never_dispatches_another_bind(self):
        self.run_assembled(motion_enabled=True, sample_version=3, invalid_motion='capture_regression')

    def test_v3_version_switch_never_dispatches_another_bind(self):
        self.run_assembled(motion_enabled=True, sample_version=3, invalid_motion='version_switch')

    def run_assembled(self, *, motion_enabled=False, bad_stop=False,
                      sample_version=1, invalid_motion=None):
        content = gzip.compress(registry_xml().encode('ascii'))
        source = self.root / 'source.save'
        source.write_bytes(content)
        offer = Offer(world_epoch='9' * 32, connection_handle=1, baseline_id=1, load_generation=1,
                      attempt_id='c' * 32, checkpoint_id='checkpoint-' + 'd' * 32,
                      sha256=hashlib.sha256(content).hexdigest(), size=len(content), name='j1.xml.gz')
        bound = MarkerBoundOffer(offer, 'b' * 32)
        service = FileService('f' * 32, slots=(1,)).start()
        self.addCleanup(service.close)
        service.offer(offer.name, source)
        dll = self.root / 'fixture.dll'
        dll.write_bytes(b'MZ fixture DLL')
        dll_digest = hashlib.sha256(dll.read_bytes()).hexdigest()

        # Existing public test-only certificate; the production tunnel still
        # performs its real certificate fingerprint and shared-key checks.
        cert = self.root / 'certificate.pem'
        key = self.root / 'key.pem'
        cert.write_text(TEST_CERTIFICATE)
        key.write_text(TEST_PRIVATE_KEY)
        context = create_server_context(str(cert), str(key))
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest()
        control_listener = listening_socket()
        self.addCleanup(control_listener.close)
        control_listener.settimeout(.2)
        tls_listener = listening_socket()
        self.addCleanup(tls_listener.close)
        port = tls_listener.getsockname()[1]
        stop = threading.Event()
        receipt_ready = threading.Event()
        inspecting_registry = threading.Event()
        registry_keepalives = []
        errors, claims, receipts, commands = [], [], [], []
        motion_messages, motion_peers = [], []
        source_sample = dict(version=1, epoch=REGISTRY_EPOCH, generation='8'*32,
                             index=1, sequence=1, sector_macro='sector_a', source_time=1,
                             x=10, y=20, z=30, velocity={'x': 1, 'y': 2, 'z': 3})
        source_samples = [source_sample]
        if sample_version == 3:
            # Capture seconds advance at a different rate from game seconds,
            # including a game-clock stall. Neither clock is rewritten.
            source_samples = []
            for sequence, game_time, capture_time in ((1, 10, 100), (2, 10, 100.2), (3, 10.01, 100.4)):
                value = {key: item for key, item in source_sample.items() if key != 'velocity'}
                value.update(version=3, sequence=sequence, source_time=game_time,
                             capture_time=capture_time, x=9+sequence,
                             real_velocity=None if sequence == 1 else {'x': 1, 'y': 2, 'z': 3})
                source_samples.append(value)
            if invalid_motion:
                source_samples = source_samples[:2]
                if invalid_motion == 'version_switch':
                    source_samples[1] = {**source_sample, 'sequence': 2, 'source_time': 11}
                else:
                    source_samples[1]['capture_time'] = 100 if invalid_motion == 'capture_duplicate' else 99
        expected_bind_count = 1 if invalid_motion else len(source_samples)
        bind_kind = 'BIND3' if sample_version == 3 else 'BIND'

        def control_server():
            try:
                while not stop.is_set():
                    try:
                        connection, _ = control_listener.accept()
                        break
                    except socket.timeout:
                        continue
                else:
                    return
                connection.settimeout(3)
                with connection, connection.makefile('rb', buffering=0) as stream:
                    if stream.readline() != offer_request():
                        raise AssertionError('unexpected offer request')
                    connection.sendall(encode_bound_offer(bound) + b'\n')
                    while True:
                        line = stream.readline()
                        if not line:
                            raise AssertionError('missing claim')
                        frame = json.loads(line)
                        if frame.get('kind') != 'keepalive':
                            break
                        if inspecting_registry.is_set():
                            registry_keepalives.append(frame['sequence'])
                        connection.sendall(json.dumps({'version': 1, 'kind': 'alive',
                            'attempt_id': frame['attempt_id'], 'sequence': frame['sequence']}).encode() + b'\n')
                    claims.append(decode_load_claim(line.rstrip(b'\n')))
                    connection.sendall(json.dumps({'version': 1, 'kind': 'accepted',
                                                   'attempt_id': offer.attempt_id}).encode() + b'\n')
                    command = {'version': 1, 'kind': 'participant_command', 'epoch': offer.world_epoch,
                               'connection_handle': 1, 'request_id': 1, 'operation': 'resume'}
                    outgoing = [command]
                    if motion_enabled:
                        outgoing += [dict(version=1, kind='motion_snapshot', connection_handle=1,
                            session_epoch=offer.world_epoch, incarnation='7'*32, request_id=value['sequence'],
                            sample=value) for value in source_samples]
                        outgoing.append({**command, 'request_id': 3, 'operation': 'pause'})
                    for command in outgoing:
                        connection.sendall(json.dumps(command).encode() + b'\n')
                        reply = stream.readline()
                        if not reply and bad_stop and command.get('operation') == 'pause':
                            break
                        if not reply and invalid_motion and command.get('kind') == 'motion_snapshot':
                            break
                        receipts.append(json.loads(reply))
                    receipt_ready.set()
                    stop.wait(10)
            except BaseException as error:
                errors.append(error)
                receipt_ready.set()

        server_thread = threading.Thread(target=control_server, name='fixture-control-server')
        server_thread.start()

        def cleanup_control_server():
            stop.set()
            server_thread.join(5)
            self.assertFalse(server_thread.is_alive())

        self.addCleanup(cleanup_control_server)
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, name='fixture-tls-server')
        loop_thread.start()

        def cleanup_loop():
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(5)
            self.assertFalse(loop_thread.is_alive())
            loop.close()

        self.addCleanup(cleanup_loop)
        tunnel_start = asyncio.run_coroutine_threadsafe(start_server(listener=tls_listener, ssl_context=context,
            shared_key=b'k' * 32, control_port=control_listener.getsockname()[1],
            file_port=service.port), loop)

        def cleanup_tunnel():
            if not tunnel_start.done():
                tunnel_start.cancel()
                return
            if tunnel_start.cancelled() or tunnel_start.exception() is not None:
                return
            tunnel = tunnel_start.result()
            asyncio.run_coroutine_threadsafe(tunnel.close(), loop).result(timeout=5)

        self.addCleanup(cleanup_tunnel)
        tunnel_start.result(timeout=5)

        workspace = self.workspace
        test = self
        processes = []

        class FakeX4Process:
            def __init__(self, *, enabled):
                if enabled is not True:
                    raise AssertionError('process not enabled')
                self.done = threading.Event()
                self.cleanup_confirmed = False
                self.thread = None
                self.motion_thread = None
                processes.append(self)

            def start(self, view, *, profile_id, guard):
                test.assertEqual(profile_id, 45841)
                test.assertTrue(guard())
                installed = view / 'extensions' / 'x4_lan_lab'
                config = (installed / 'ui' / 'config.lua').read_text('ascii')
                for name in overlay._FIELDS:
                    value = 'save_009' if name == 'slot' else getattr(offer, name)
                    literal = str(value) if type(value) is int else json.dumps(value)
                    test.assertIn(name + '=' + literal, config)
                test.assertIn('authority_load_save_path=' + json.dumps(r'C:\Private\45841\save'), config)
                port_match = re.search(r'authority_participant_port=(\d+)', config)
                lease_match = re.search(r'authority_participant_lease="([0-9a-f]{32})"', config)
                test.assertIsNotNone(port_match)
                test.assertIsNotNone(lease_match)
                test.assertEqual((installed / 'native' / 'x4lan_net_64.dll').read_bytes(), dll.read_bytes())
                test.assertEqual((workspace.save_dir / 'save_009.xml.gz').read_bytes(), content)
                load_probe = (installed / 'ui' / 'authority_download_load_probe.lua').read_text('ascii')
                registry_context = re.search(r"nonce='([0-9a-f]{32})',expected='([^']+)'", load_probe)
                test.assertIsNotNone(registry_context)
                test.assertEqual(registry_context.group(2), REGISTRY_EPOCH + '|2')
                destination = ('127.0.0.1', int(port_match.group(1)))
                lease = lease_match.group(1)
                motion_context = {}
                if motion_enabled:
                    # V3 selection is carried by the packet, without enabling
                    # an unrequested saved fixture or changing installer flags.
                    for option in ('saved_playback_fixture', 'saved_replica_control_fixture',
                                   'saved_initial_alignment_fixture'):
                        test.assertNotIn('authority_client_motion_' + option + '=', config)
                    for name in ('lease', 'attempt', 'epoch', 'generation'):
                        match = re.search('authority_client_motion_' + name + r'="([0-9a-f]{32})"', config)
                        test.assertIsNotNone(match)
                        motion_context[name] = match.group(1)
                    match = re.search(r'authority_client_motion_port=(\d+)', config)
                    test.assertIsNotNone(match)
                    motion_context['destination'] = ('127.0.0.1', int(match.group(1)))
                fields = '|'.join(str(value) for value in (offer.world_epoch, 1, 1, 1,
                                  offer.checkpoint_id, offer.sha256, offer.attempt_id, 'save_009'))
                workspace.log_path.write_text('[X4LAN] AUTH_DOWNLOAD_LOAD load_requested|' + fields + '\n' +
                    '[X4LAN] AUTH_MARKER load INSPECTED ' + bound.marker + '\n' +
                    '[X4LAN] AUTH_RECOVERY_REGISTRY ' + registry_context.group(1) +
                    '|startup|' + registry_context.group(2) + '\n' +
                    '[X4LAN] AUTH_DOWNLOAD_LOAD pause_observed|' + fields + '\n')

                def motion_participant():
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                            udp.bind(('127.0.0.1', 0))
                            udp.settimeout(.05)
                            motion_peers.append(udp.getsockname())
                            prefix = [motion_context['lease'], motion_context['attempt']]
                            identity = [motion_context['epoch'], motion_context['generation']]
                            udp.sendto('|'.join(['R', *prefix, 'READY', *identity]).encode(),
                                       motion_context['destination'])
                            while not self.done.is_set():
                                try:
                                    data, peer = udp.recvfrom(1024)
                                except socket.timeout:
                                    continue
                                parts = data.decode('ascii').split('|')
                                test.assertEqual(parts[:3], ['M', *prefix])
                                motion_messages.append(parts[3])
                                if parts[3] == bind_kind:
                                    value = source_samples[len(motion_messages)-1]
                                    test.assertEqual(parts[5:11], [*identity, '1', str(value['sequence']),
                                                                'sector_a', motion_digest(value)])
                                    numeric_keys = ('source_time', 'capture_time', 'x', 'y', 'z') if sample_version == 3 else ('source_time', 'x', 'y', 'z')
                                    numeric = [format(value[key], '.17g') for key in numeric_keys]
                                    velocity = value['real_velocity'] if sample_version == 3 else value['velocity']
                                    rate = ['-', '-', '-'] if velocity is None else [format(velocity[key], '.17g') for key in ('x', 'y', 'z')]
                                    test.assertEqual(len(parts), 19 if sample_version == 3 else 18)
                                    test.assertEqual(parts[11:], numeric + rate)
                                    reply = ['R', *prefix, 'BOUND', *parts[4:11]]
                                elif parts[3] == 'STOP':
                                    reply = ['R', *prefix, 'WRONG' if bad_stop else 'STOPPED', *parts[4:]]
                                else:
                                    raise AssertionError('unexpected motion command')
                                udp.sendto('|'.join(reply).encode(), peer)
                    except BaseException as error:
                        errors.append(error)

                def participant():
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                            udp.bind(('127.0.0.1', 0))
                            udp.settimeout(.05)
                            while not self.done.is_set():
                                udp.sendto(('HELLO|' + lease + '|' + offer.world_epoch).encode(), destination)
                                try:
                                    data, peer = udp.recvfrom(1024)
                                except socket.timeout:
                                    continue
                                parts = data.decode('ascii').split('|')
                                if len(parts) != 5 or parts[:3] != ['CMD', lease, offer.world_epoch]:
                                    raise AssertionError('unexpected UDP command')
                                commands.append(parts[4])
                                if motion_enabled and parts[4] == 'pause':
                                    test.assertEqual(motion_messages, [bind_kind]*expected_bind_count + ['STOP'])
                                udp.sendto('|'.join(('RECEIPT', *parts[1:], 'confirmed')).encode(), peer)
                                if motion_enabled and parts[4] == 'resume':
                                    test.assertIsNone(self.motion_thread)
                                    self.motion_thread = threading.Thread(target=motion_participant, name='fake-x4-motion')
                                    self.motion_thread.start()
                    except BaseException as error:
                        errors.append(error)
                self.thread = threading.Thread(target=participant, name='fake-x4-participant')
                self.thread.start()

            def is_running(self):
                return self.thread is not None and self.thread.is_alive()

            def close(self):
                if motion_enabled and not self.cleanup_confirmed:
                    test.assertEqual(motion_messages, [bind_kind]*expected_bind_count + ['STOP'])
                self.done.set()
                if self.motion_thread is not None:
                    self.motion_thread.join(3)
                    test.assertFalse(self.motion_thread.is_alive())
                if self.thread is not None:
                    self.thread.join(3)
                    if self.thread.is_alive():
                        raise RuntimeError('fake X4 thread did not stop')
                    if not self.cleanup_confirmed:
                        test.assertTrue((workspace.view / 'extensions' / 'x4_lan_lab' /
                                         'ui' / 'config.lua').is_file())
                self.cleanup_confirmed = True
                return True

        original_saved_status = fixture_session.saved_status
        calling_thread = threading.get_ident()

        def inspect_with_slow_first_chunk(checkpoint, *, pump):
            # A delayed first chunk forces a real keepalive during inspection,
            # without a large save or substituting the parser/digest checks.
            first_chunk = True
            def delayed_pump():
                nonlocal first_chunk
                test.assertEqual(threading.get_ident(), calling_thread)
                if first_chunk:
                    first_chunk = False
                    time.sleep(.25)
                pump()
            inspecting_registry.set()
            try:
                return original_saved_status(checkpoint, pump=delayed_pump)
            finally:
                inspecting_registry.clear()

        try:
            # Only Windows path rendering and the OS/X4 process boundary are
            # substituted; installation and every transport remain real.
            with patch.object(overlay, '_windows_save_path', return_value=r'C:\Private\45841\save'), \
                 patch.object(fixture_session, 'saved_status', side_effect=inspect_with_slow_first_chunk), \
                 patch.object(fixture_session, 'WindowsMotionPeer',
                              side_effect=lambda process, sock: lambda peer: peer in motion_peers), \
                 patch.object(fixture_session, 'WindowsX4Process', FakeX4Process):
                options = dict(enabled=True, workspace=workspace,
                    server_host='127.0.0.1', server_port=port, server_fingerprint=fingerprint,
                    shared_key=b'k' * 32, file_token='f' * 32, file_slot=1, windows_dll=dll,
                    dll_sha256=dll_digest, deadline=time.monotonic() + 15, keepalive_interval=.2,
                    stop_requested=receipt_ready.is_set, io_timeout=3, participant_timeout=3,
                    motion_request={'generation': '8'*32, 'indices': [1], 'timeout': 1} if motion_enabled else None)
                if invalid_motion:
                    expected_error = 'version changed' if invalid_motion == 'version_switch' else 'capture time'
                    with self.assertRaisesRegex(ValueError, expected_error):
                        fixture_session.run_fixture_session(**options)
                elif bad_stop:
                    with self.assertRaisesRegex(ValueError, 'foreign motion stop'):
                        fixture_session.run_fixture_session(**options)
                else:
                    result = fixture_session.run_fixture_session(**options)
            if not bad_stop and not invalid_motion:
                expected = {'client_admitted': True, 'participant_receipts': 2+len(source_samples) if motion_enabled else 1,
                            'profile_id': 45841,
                            'owned_cleanup_confirmed': True, 'configuration_redacted': True}
                if motion_enabled:
                    expected.update(motion_receipts=len(source_samples), motion_stop_confirmed=True)
                self.assertEqual(result, expected)
            self.assertEqual(len(claims), 1)
            self.assertTrue(registry_keepalives)
            self.assertEqual(claims[0].marker, bound.marker)
            self.assertEqual(claims[0].receipt.attempt_id, offer.attempt_id)
            self.assertTrue(claims[0].load_event_observed)
            self.assertTrue(claims[0].paused_observed)
            self.assertEqual(len(receipts), 2 if bad_stop or invalid_motion else 2+len(source_samples) if motion_enabled else 1)
            self.assertEqual(receipts[0]['status'], 'confirmed')
            self.assertEqual(receipts[0]['operation'], 'resume')
            self.assertEqual(receipts[0]['request_id'], 1)
            if motion_enabled:
                self.assertEqual(motion_messages, [bind_kind]*expected_bind_count + ['STOP'])
                self.assertEqual(receipts[1]['kind'], 'motion_receipt')
                for value, receipt in zip(source_samples[:expected_bind_count], receipts[1:]):
                    self.assertEqual(receipt['request_id'], value['sequence'])
                    self.assertEqual(receipt['sample_sha256'], motion_digest(value))
            self.assertEqual(commands, ['resume', 'pause'] if motion_enabled and not bad_stop and not invalid_motion else ['resume'])
            self.assertEqual(errors, [])
            self.assertTrue(processes[0].cleanup_confirmed)
            self.assertFalse((workspace.view / 'extensions' / 'x4_lan_lab' / 'ui' / 'config.lua').exists())
            self.assertEqual((workspace.save_dir / 'save_009.xml.gz').read_bytes(), content)
        finally:
            for process in processes:
                process.close()


if __name__ == '__main__':
    unittest.main()
