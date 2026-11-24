"""Real local TCP/UDP; synthetic log, process guard and X4 participant."""
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from authority.experimental_client_owner import ExperimentalClientOwner
from authority.experimental_tcp_control import offer_request
from authority.load_messages import Offer
from authority.local_participant_control import LocalParticipantControl
from authority.observed_client_session import ObservedClientSession
from authority.trusted_load_messages import decode_load_claim, encode_bound_offer
from authority.trusted_marker_load_session import MarkerBoundOffer
from lockstep.launcher.joinfiles import FileService


class ObservedSessionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        content = b'isolated synthetic checkpoint'
        source = self.root / 'source'
        source.write_bytes(content)
        self.offer = Offer(world_epoch='a' * 32, connection_handle=1, baseline_id=1, load_generation=1,
            attempt_id='c' * 32, checkpoint_id='checkpoint-' + 'd' * 32,
            sha256=hashlib.sha256(content).hexdigest(), size=len(content), name='j1.xml.gz')
        self.bound = MarkerBoundOffer(self.offer, 'b' * 32)
        self.service = FileService('f' * 32, slots=(1,)).start()
        self.addCleanup(self.service.close)
        self.service.offer(self.offer.name, source)
        self.stop = threading.Event()
        self.server_done = threading.Event()
        self.errors, self.claims, self.replies, self.commands, self.keepalives = [], [], [], [], []
        self.log = b''
        self.partial_during_command = False

    def connect(self, *, disconnect=False, keepalive_interval=2, participant_command=None):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        client = socket.create_connection(listener.getsockname(), timeout=2)
        def server():
            try:
                conn, _ = listener.accept()
                conn.settimeout(4)
                with conn, conn.makefile('rb', buffering=0) as stream:
                    if stream.readline() != offer_request():
                        raise AssertionError('unexpected offer request')
                    conn.sendall(encode_bound_offer(self.bound) + b'\n')
                    while True:
                        line = stream.readline()
                        if not line:
                            return
                        frame = json.loads(line)
                        if frame.get('kind') != 'keepalive':
                            break
                        self.keepalives.append(frame['sequence'])
                        conn.sendall(json.dumps({'version': 1, 'kind': 'alive',
                            'attempt_id': frame['attempt_id'], 'sequence': frame['sequence']}).encode() + b'\n')
                    self.claims.append(decode_load_claim(line.rstrip(b'\n')))
                    conn.sendall(json.dumps({'version': 1, 'kind': 'accepted',
                                             'attempt_id': self.offer.attempt_id}).encode() + b'\n')
                    if disconnect:
                        return
                    command = participant_command or {
                        'version': 1, 'kind': 'participant_command', 'epoch': self.offer.world_epoch,
                        'connection_handle': 1, 'request_id': 1, 'operation': 'resume'}
                    commands = command if type(command) is tuple else (command,)
                    for request in commands:
                        conn.sendall(json.dumps(request).encode() + b'\n')
                        reply = stream.readline()
                        if reply:
                            self.replies.append(json.loads(reply))
                        else:
                            return
                    self.stop.wait(4)
            except BaseException as error:
                self.errors.append(error)
            finally:
                self.server_done.set()
        thread = threading.Thread(target=server)
        thread.start()
        self.addCleanup(self.join_thread, thread)
        deadline = time.monotonic() + 8
        owner = ExperimentalClientOwner(client, enabled=True, file_host='127.0.0.1',
            file_port=self.service.port, file_token='f' * 32, file_slot=1,
            deadline=deadline, io_timeout=2, keepalive_interval=keepalive_interval)
        self.addCleanup(owner.close)
        bound = owner.request_offer()
        downloaded = owner.download(self.root/'download')
        slot_dir = self.root/'save'; slot_dir.mkdir()
        owner.copy_to_new_slot(downloaded, slot_dir/'save_009.xml.gz')
        return owner, client, bound, deadline

    def join_thread(self, thread):
        self.stop.set()
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.errors, [])

    def start_fake_participant(self, controller):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0)); udp.settimeout(.05)
        destination = controller.sock.getsockname()
        def game():
            try:
                while not self.stop.is_set():
                    udp.sendto(('HELLO|' + controller.lease + '|' + self.offer.world_epoch).encode(), destination)
                    try:
                        data, peer = udp.recvfrom(1024)
                    except socket.timeout:
                        continue
                    fields = data.decode('ascii').split('|')
                    if len(fields) != 5 or fields[:3] != ['CMD', controller.lease, self.offer.world_epoch]:
                        raise AssertionError('unexpected participant command')
                    self.commands.append(fields[4])
                    if self.partial_during_command:
                        self.log += b'[X4LAN] AUTH_PARTICIPANT ERR'
                    udp.sendto('|'.join(('RECEIPT', *fields[1:], 'confirmed')).encode(), peer)
            except BaseException as error:
                self.errors.append(error)
            finally:
                udp.close()
        thread = threading.Thread(target=game)
        thread.start()
        self.addCleanup(self.join_thread, thread)

    def log_parts(self):
        fields = '|'.join(str(value) for value in (self.offer.world_epoch, 1, 1, 1, self.offer.checkpoint_id,
                          self.offer.sha256, self.offer.attempt_id, 'save_009')).encode()
        return (b'[X4LAN] AUTH_DOWNLOAD_LOAD load_requested|' + fields + b'\n',
                b'[X4LAN] AUTH_MARKER load INSPECTED ' + self.bound.marker.encode() + b'\n',
                b'[X4LAN] AUTH_DOWNLOAD_LOAD pause_observed|' + fields + b'\n')

    def motion_command(self):
        sample = {'version': 1, 'epoch': self.offer.world_epoch, 'generation': 'd' * 32,
                  'index': 7, 'sequence': 1, 'sector_macro': 'sector_a', 'source_time': 1.0,
                  'x': 10.0, 'y': 20.0, 'z': 30.0, 'velocity': None}
        return {'version': 1, 'kind': 'motion_snapshot', 'connection_handle': 1,
                'session_epoch': self.offer.world_epoch, 'incarnation': '4' * 32,
                'request_id': 1, 'sample': sample}

    def start_motion_peer(self, ipc):
        from authority.experimental_tcp_motion import motion_digest

        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        peer.bind(('127.0.0.1', 0)); peer.settimeout(3)
        lease, attempt = ipc._lease, ipc._attempt
        epoch, generation = self.offer.world_epoch, 'd' * 32
        def engine_owner():
            try:
                peer.sendto(f'R|{lease}|{attempt}|READY|{epoch}|{generation}'.encode('ascii'),
                            ipc._sock.getsockname())
                data, address = peer.recvfrom(1024)
                fields = data.decode('ascii').split('|')
                self.motion_ipc_requests.append(fields)
                if fields[:4] != ['M', lease, attempt, 'BIND']:
                    raise AssertionError('unexpected motion IPC request')
                if fields[7:11] != ['7', '1', 'sector_a', motion_digest(self.motion_command()['sample'])]:
                    raise AssertionError('motion sample identity changed in IPC')
                if fields[11:18] != ['1', '10', '20', '30', '-', '-', '-']:
                    raise AssertionError('motion pose or rate changed in IPC')
                peer.sendto('|'.join(['R', lease, attempt, 'BOUND', *fields[4:11]]).encode('ascii'), address)
            except BaseException as error:
                self.errors.append(error)
            finally:
                peer.close()
        self.motion_ipc_requests = []
        thread = threading.Thread(target=engine_owner)
        thread.start(); self.addCleanup(self.join_thread, thread)
        return thread

    def test_real_tcp_udp_handoff_and_late_duplicate(self):
        owner, client, bound, deadline = self.connect(keepalive_interval=.02)
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        request, marker, pause = self.log_parts()
        self.log = request + marker + pause[:-1]
        def complete_log():
            time.sleep(.1)
            self.log += b'\n'
        writer = threading.Thread(target=complete_log)
        writer.start(); self.addCleanup(writer.join)
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        self.assertIsNone(owner._participant._apply_motion)
        self.assertEqual(owner._participant._timeout, controller.timeout + 1)
        while not self.replies:
            session.poll(.1)
        self.assertEqual(self.replies[0]['status'], 'confirmed')
        self.assertEqual(len(self.claims), 1)
        self.assertGreater(len(self.keepalives), 0)
        self.log += request
        with self.assertRaisesRegex(ValueError, 'out of order'):
            session.poll()
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)

    def test_motion_budget_reserves_ready_stop_and_native_control_deadlines(self):
        class MotionControl:
            timeout = .75

            def __init__(self):
                self.events = []

            def __call__(self, sample, despawn):
                return True

            def before_control(self, operation):
                self.events.append(('before', operation))
                return True

            def after_control(self, operation):
                self.events.append(('after', operation))
                return True

        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        self.log = b''.join(self.log_parts())
        motion = MotionControl()
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline,
            apply_motion=motion, motion_control=motion)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        # READY and STOP each get .75s, native control 2s, framing 1s.
        self.assertEqual(owner._participant._timeout, 4.5)
        while not self.replies:
            session.poll(.1)
        self.assertEqual(self.replies[0]['status'], 'confirmed')
        self.assertEqual(motion.events, [('before', 'resume'), ('after', 'resume')])

    def test_registry_gate_admits_then_refuses_a_late_registry_fault(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        request, marker, pause = self.log_parts()
        nonce, epoch = '1' * 32, '2' * 32
        registry = f'[X4LAN] AUTH_RECOVERY_REGISTRY {nonce}|startup|{epoch}|3\n'.encode()
        self.log = request + marker + registry + pause
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline,
            registry_status=('ready', epoch, 3, '3' * 64), registry_nonce=nonce)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        while not self.replies:
            session.poll(.1)
        self.assertEqual(self.replies[0]['status'], 'confirmed')
        self.assertEqual(len(self.claims), 1)
        self.log += f'[X4LAN] AUTH_RECOVERY_REGISTRY_ERROR {nonce}\n'.encode()
        with self.assertRaisesRegex(ValueError, 'registry reported'):
            session.poll()
        self.assertEqual(client.fileno(), -1)

    def test_missing_registry_receipt_prevents_admission(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.log = b''.join(self.log_parts())
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline,
            registry_status=('ready', '2' * 32, 3, '3' * 64), registry_nonce='1' * 32)
        self.addCleanup(session.close)
        with self.assertRaisesRegex(ValueError, 'out of order'):
            session.start()
        self.assertEqual(self.claims, [])
        self.assertEqual(client.fileno(), -1)

    def make_motion_ipc(self):
        from authority.experimental_participant_motion_ipc import ExperimentalParticipantMotionIPC
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 0))
        return ExperimentalParticipantMotionIPC(sock, enabled=True, lease='e' * 32,
            attempt=self.offer.attempt_id, epoch=self.offer.world_epoch, generation='d' * 32,
            guard=lambda: True, validate_peer=lambda peer: peer[0] == '127.0.0.1', timeout=2)

    @staticmethod
    def close_motion_ipc(ipc):
        if ipc.state in ('new', 'ready', 'remote_stopped'):
            ipc.close()

    def test_motion_callback_passes_through_real_ipc_before_tcp_receipt(self):
        command = self.motion_command()
        owner, client, bound, deadline = self.connect(participant_command=command)
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        ipc = self.make_motion_ipc()
        self.addCleanup(self.close_motion_ipc, ipc)
        peer = self.start_motion_peer(ipc)
        self.assertTrue(ipc.wait_ready())
        self.log = b''.join(self.log_parts())
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline, apply_motion=ipc)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        poll_deadline = time.monotonic() + 4
        while not self.replies and time.monotonic() < poll_deadline:
            session.poll(.1)
        peer.join(3)
        self.assertFalse(peer.is_alive())
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.motion_ipc_requests), 1)
        self.assertEqual(self.motion_ipc_requests[0][3], 'BIND')
        self.assertEqual(len(self.replies), 1)
        self.assertEqual(self.replies[0]['kind'], 'motion_receipt')
        self.assertEqual(self.replies[0]['status'], 'accepted')

    def test_reentry_alignment_is_guarded_through_the_observed_session(self):
        original = self.motion_command()
        first_target = dict(original['sample'], sequence=2, source_time=2.0, sector_macro='sector_b')
        prepared = dict(original, kind='motion_reentry_prepare', request_id=2,
                        previous_sector='sector_a', sample=first_target)
        latest_target = dict(first_target, sequence=3, source_time=3.0, x=40.0)
        aligned = dict(prepared, kind='motion_reentry_align', request_id=3,
                       prepared_sequence=2, sample=latest_target)
        owner, client, bound, deadline = self.connect(participant_command=(original, prepared, aligned))
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        self.log = b''.join(self.log_parts())
        events = []

        class LocalReentry:
            def prepare(_, previous_sector, sample):
                events.append(('prepare', previous_sector, sample))
                return True

            def align(_, previous_sector, prepared_sample, sample):
                events.append(('align', previous_sector, prepared_sample, sample))
                self.log += b'[X4LAN] AUTH_PARTICIPANT ERR'
                return True

            def cancel(_, previous_sector, sample):
                raise AssertionError('unexpected cancel')

        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline,
            apply_motion=lambda sample, despawn: events.append(('snapshot', sample, despawn)) or True,
            motion_reentry_controller=LocalReentry())
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        with self.assertRaisesRegex(RuntimeError, 'incomplete during motion'):
            while True:
                session.poll(.1)
        self.assertTrue(self.server_done.wait(2))
        self.assertEqual([receipt['kind'] for receipt in self.replies],
                         ['motion_receipt', 'motion_reentry_prepared_receipt'])
        self.assertEqual(events, [('snapshot', original['sample'], False),
                                  ('prepare', 'sector_a', first_target),
                                  ('align', 'sector_a', first_target, latest_target)])
        self.assertEqual(client.fileno(), -1)

    def test_log_failure_after_motion_ipc_dispatch_blocks_tcp_receipt(self):
        command = self.motion_command()
        owner, client, bound, deadline = self.connect(participant_command=command)
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        ipc = self.make_motion_ipc()
        self.addCleanup(self.close_motion_ipc, ipc)
        peer = self.start_motion_peer(ipc)
        self.assertTrue(ipc.wait_ready())
        self.log = b''.join(self.log_parts())

        def dispatch_then_break_log(sample, despawn):
            accepted = ipc(sample, despawn)
            self.log += b'[X4LAN] AUTH_PARTICIPANT ERR'
            return accepted

        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline,
            apply_motion=dispatch_then_break_log)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        with self.assertRaisesRegex(RuntimeError, 'incomplete during motion'):
            while True:
                session.poll(.1)
        peer.join(3)
        self.assertFalse(peer.is_alive())
        self.assertEqual(self.errors, [])
        self.assertEqual(self.motion_ipc_requests[0][3], 'BIND')
        self.assertEqual(self.replies, [])
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)

    def test_controller_fault_after_motion_dispatch_blocks_tcp_receipt(self):
        command = self.motion_command()
        owner, client, bound, deadline = self.connect(participant_command=command)
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        ipc = self.make_motion_ipc()
        self.addCleanup(self.close_motion_ipc, ipc)
        peer = self.start_motion_peer(ipc)
        self.assertTrue(ipc.wait_ready())
        self.log = b''.join(self.log_parts())

        def dispatch_then_fault_controller(sample, despawn):
            accepted = ipc(sample, despawn)
            controller.fault = True
            return accepted

        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline,
            apply_motion=dispatch_then_fault_controller)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        with self.assertRaisesRegex(RuntimeError, 'observed session ownership changed'):
            while True:
                session.poll(.1)
        peer.join(3)
        self.assertFalse(peer.is_alive())
        self.assertEqual(self.errors, [])
        self.assertEqual(self.motion_ipc_requests[0][3], 'BIND')
        self.assertEqual(self.replies, [])
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)

    def test_invalid_motion_callback_is_rejected_during_session_construction(self):
        owner, client, bound, deadline = self.connect(disconnect=True)
        controller = LocalParticipantControl(enabled=True, timeout=2)
        with self.assertRaisesRegex(ValueError, 'motion consumer must be callable'):
            ObservedClientSession(owner, bound, controller, slot='save_009',
                read_log=lambda: b'', guard=lambda: True, deadline=deadline, apply_motion=object())
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)

    def test_wrong_ticket_rejected_before_claim(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        request, marker, pause = self.log_parts()
        self.log = request.replace(self.offer.attempt_id.encode(), b'e' * 32) + marker + pause
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline)
        with self.assertRaisesRegex(ValueError, 'out of order'):
            session.start()
        self.assertTrue(self.server_done.wait(2))
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)
        self.assertEqual(self.claims, [])

    def test_disconnect_after_claim_fences_owners(self):
        owner, client, bound, deadline = self.connect(disconnect=True)
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        self.log = b''.join(self.log_parts())
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        with self.assertRaises((ConnectionError, RuntimeError)):
            while True:
                session.poll(.1)
        self.assertEqual(len(self.claims), 1)
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)

    def test_partial_late_error_defers_pending_command_then_faults(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        self.log = b''.join(self.log_parts())
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        self.log += b'[X4LAN] AUTH_PARTICIPANT ERR'
        self.assertFalse(session.poll(.1))
        self.assertEqual(self.commands, [])
        self.log += b'OR state\n'
        with self.assertRaisesRegex(ValueError, 'client script reported'):
            session.poll(.1)
        self.assertEqual(self.commands, [])
        self.assertEqual(client.fileno(), -1)

    def test_replaced_log_is_rejected_before_queued_command(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        self.log = b''.join(self.log_parts())
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        self.log = b'other complete snapshot\n'
        with self.assertRaisesRegex(ValueError, 'changed or shrank'):
            session.poll(.1)
        self.assertEqual(self.commands, [])
        self.assertEqual(client.fileno(), -1)

    def test_partial_log_during_command_prevents_receipt(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.start_fake_participant(controller)
        self.log = b''.join(self.log_parts())
        self.partial_during_command = True
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=lambda: True, deadline=deadline)
        self.addCleanup(session.close)
        self.assertTrue(session.start())
        with self.assertRaisesRegex(RuntimeError, 'incomplete during command'):
            while True:
                session.poll(.1)
        self.assertEqual(self.commands, ['resume'])
        self.assertTrue(self.server_done.wait(2))
        self.assertEqual(self.replies, [])
        self.assertEqual(client.fileno(), -1)

    def test_rejects_mismatched_offer_and_shorter_session_deadline(self):
        from dataclasses import replace
        for mode in ('offer', 'deadline'):
            with self.subTest(mode=mode):
                owner, client, bound, deadline = self.connect()
                controller = LocalParticipantControl(enabled=True, timeout=2)
                self.addCleanup(controller.close)
                wrong = MarkerBoundOffer(replace(bound.offer, attempt_id='e' * 32), bound.marker)
                with self.assertRaisesRegex(ValueError, 'matching staged offer'):
                    ObservedClientSession(owner, wrong if mode == 'offer' else bound, controller,
                        slot='save_009', read_log=lambda: b'', guard=lambda: True,
                        deadline=deadline if mode == 'offer' else deadline - 1)
                self.assertEqual(client.fileno(), -1)
                self.assertEqual(controller.sock.fileno(), -1)
                # Each iteration needs a fresh download and slot path.
                for path in (self.root/'download', self.root/'save'):
                    shutil.rmtree(path)

    def test_reentrant_guard_faults_without_claim(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.log = b''.join(self.log_parts())
        session = None
        def guard():
            try:
                session.poll()
            except RuntimeError:
                pass
            return True
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=guard, deadline=deadline)
        with self.assertRaises((TimeoutError, RuntimeError)):
            session.start()
        self.assertTrue(self.server_done.wait(2))
        self.assertEqual(self.claims, [])
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)

    def test_changed_internal_offer_fails_before_claim(self):
        from dataclasses import replace
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        self.log = b''.join(self.log_parts())
        def guard():
            owner._load._bound = MarkerBoundOffer(replace(bound.offer, attempt_id='e' * 32), bound.marker)
            return True
        session = ObservedClientSession(owner, bound, controller, slot='save_009',
            read_log=lambda: self.log, guard=guard, deadline=deadline)
        with self.assertRaisesRegex(RuntimeError, 'staged load owner changed'):
            session.start()
        self.assertTrue(self.server_done.wait(2))
        self.assertEqual(self.claims, [])
        self.assertEqual(client.fileno(), -1)

    def test_other_thread_rejected_without_taking_owner(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        failures = []
        def construct():
            try:
                ObservedClientSession(owner, bound, controller, slot='save_009',
                    read_log=lambda: b'', guard=lambda: True, deadline=deadline)
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=construct)
        thread.start(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeError)
        self.assertGreaterEqual(client.fileno(), 0)
        self.assertGreaterEqual(controller.sock.fileno(), 0)

    def test_controller_timeout_must_fit_participant_budget(self):
        owner, client, bound, deadline = self.connect()
        controller = LocalParticipantControl(enabled=True, timeout=300)
        self.addCleanup(controller.close)
        with self.assertRaisesRegex(ValueError, 'fresh local participant'):
            ObservedClientSession(owner, bound, controller, slot='save_009',
                read_log=lambda: b'', guard=lambda: True, deadline=deadline)
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)

    def test_missing_keepalive_rejected_before_claim(self):
        owner, client, bound, deadline = self.connect(keepalive_interval=None)
        controller = LocalParticipantControl(enabled=True, timeout=2)
        self.addCleanup(controller.close)
        with self.assertRaisesRegex(ValueError, 'keepalive'):
            ObservedClientSession(owner, bound, controller, slot='save_009',
                read_log=lambda: b'', guard=lambda: True, deadline=deadline)
        self.assertEqual(self.claims, [])
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.sock.fileno(), -1)


if __name__ == '__main__':
    unittest.main()
