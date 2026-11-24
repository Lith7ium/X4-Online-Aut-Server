"""One owner, real wire/file protocols and simulated X4 observations."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools/dev'))
from authority.experimental_client_owner import CompanyShipChoiceDenied, ExperimentalClientOwner
from authority.experimental_remote_load_client import ExperimentalRemoteLoadClient
from authority.experimental_tcp_control import offer_request
from authority.experimental_tcp_motion import motion_digest
from authority.load_messages import Offer
from authority.trusted_load_messages import decode_load_claim, encode_bound_offer
from authority.trusted_marker_load_session import MarkerBoundOffer
from lockstep.launcher.joinfiles import FileService


class Controller:
    def __init__(self):
        self.calls = []

    def pause(self, handles):
        self.calls.append(('pause', handles))
        return True

    def resume(self, handles):
        self.calls.append(('resume', handles))
        return True


class ClientOwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.download_count = 0
        self.content = b'one isolated offered save'
        source = self.root / 'source'
        source.write_bytes(self.content)
        self.marker = 'b' * 32
        self.offer = Offer(world_epoch='epoch', connection_handle=1, baseline_id=1,
            load_generation=1, attempt_id='c' * 32, checkpoint_id='checkpoint-' + 'd' * 32,
            sha256=hashlib.sha256(self.content).hexdigest(), size=len(self.content), name='j1.xml.gz')
        self.service = FileService('a' * 32, slots=(1,)).start()
        self.addCleanup(self.service.close)
        self.service.offer('j1.xml.gz', source)

    def connect(self, *, accepted_attempt=None, send_command=True, host='127.0.0.1',
                authority_commands=None, company_exchanges=()):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((host, 0))
        listener.listen(1)
        listener.settimeout(5)
        client = socket.create_connection(listener.getsockname(), timeout=5)
        errors, claims, replies = [], [], []

        def server():
            try:
                conn, _ = listener.accept()
                conn.settimeout(5)
                with conn, conn.makefile('rb', buffering=0) as stream:
                    if stream.readline() != offer_request():
                        raise AssertionError('unexpected offer request')
                    conn.sendall(encode_bound_offer(MarkerBoundOffer(self.offer, self.marker)) + b'\n')
                    for expected, response in company_exchanges:
                        request = json.loads(stream.readline())
                        if request != {'version': 1, 'attempt_id': self.offer.attempt_id, **expected}:
                            raise AssertionError(f'unexpected company ship request: {request}')
                        raw = response if isinstance(response, bytes) else json.dumps(response).encode()
                        conn.sendall(raw + b'\n')
                    line = stream.readline()
                    if not line:
                        return
                    claims.append(decode_load_claim(line.rstrip(b'\n')))
                    accepted = {'version': 1, 'kind': 'accepted',
                                'attempt_id': accepted_attempt or self.offer.attempt_id}
                    conn.sendall(json.dumps(accepted).encode() + b'\n')
                    if authority_commands is not None:
                        for command in authority_commands:
                            conn.sendall(json.dumps(command).encode() + b'\n')
                            reply = stream.readline()
                            if not reply:
                                break
                            replies.append(json.loads(reply))
                    elif send_command:
                        command = {'version': 1, 'kind': 'participant_command', 'epoch': 'epoch',
                                   'connection_handle': 1, 'request_id': 1, 'operation': 'resume'}
                        conn.sendall(json.dumps(command).encode() + b'\n')
                        reply = stream.readline()
                        if reply:
                            replies.append(json.loads(reply))
            except BaseException as error:
                errors.append(error)
            finally:
                listener.close()

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        self.addCleanup(lambda: thread.join(6))
        owner = ExperimentalClientOwner(client, enabled=True, file_host='127.0.0.1',
            file_port=self.service.port, file_token='a' * 32, file_slot=1,
            deadline=time.monotonic() + 10, io_timeout=3)
        self.addCleanup(owner.close)
        return owner, client, thread, errors, claims, replies

    def observe(self, owner):
        bound = owner.request_offer()
        self.download_count += 1
        path = owner.download(self.root / ('download-' + str(self.download_count)))
        self.assertEqual(path.read_bytes(), self.content)
        owner.verify_staged(path)
        owner.load_requested(bound.offer.attempt_id)
        owner.loaded_marker(bound.offer.attempt_id, bound.marker)
        owner.paused(bound.offer.attempt_id, True)
        return bound

    def test_real_load_handoff_and_participant_receipt(self):
        owner, client, thread, errors, claims, replies = self.connect()
        bound = self.observe(owner)
        controller = Controller()
        owner.activate(bound.offer.attempt_id, controller, participant_timeout=3)
        deadline = time.monotonic() + 4
        while not owner.poll(.1):
            if time.monotonic() >= deadline:
                self.fail('participant receipt timed out')
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].marker, self.marker)
        self.assertEqual(controller.calls, [('resume', (1,))])
        self.assertEqual(replies[0]['status'], 'confirmed')
        owner.close()
        self.assertEqual(client.fileno(), -1)

    def company_reply(self, kind, **fields):
        return {'version': 1, 'attempt_id': self.offer.attempt_id, 'kind': kind, **fields}

    @staticmethod
    def company_ship(index):
        return {'ship_index': index, 'sector_macro': 'cluster_14_sector001_macro',
                'name': None, 'code': 'WOO-831'}

    def test_company_ship_pages_and_recoverable_choice_denials(self):
        first, second = self.company_ship(1970), self.company_ship(1980)
        owner, client, thread, errors, _, _ = self.connect(send_command=False, company_exchanges=(
            ({'kind': 'list_company_ships', 'after_index': 0},
             self.company_reply('company_ship_catalog_unavailable')),
            ({'kind': 'list_company_ships', 'after_index': 0},
             self.company_reply('company_ships', ships=[first], next_index=1970)),
            ({'kind': 'list_company_ships', 'after_index': 1970},
             self.company_reply('company_ships', ships=[second], next_index=None)),
            ({'kind': 'choose_company_ship', 'ship_index': 1970},
             self.company_reply('company_ship_unavailable')),
            ({'kind': 'choose_company_ship', 'ship_index': 1980},
             self.company_reply('stale_ship_choice')),
            ({'kind': 'choose_company_ship', 'ship_index': 1980},
             self.company_reply('company_ship_chosen', ship=second)),
        ))
        owner.request_offer()
        with self.assertRaises(CompanyShipChoiceDenied) as denied:
            owner.list_company_ships()
        self.assertEqual(denied.exception.reason, 'company_ship_catalog_unavailable')
        self.assertEqual(owner.list_company_ships(), (first, second))
        for index, reason in ((1970, 'company_ship_unavailable'), (1980, 'stale_ship_choice')):
            with self.assertRaises(CompanyShipChoiceDenied) as denied:
                owner.choose_company_ship(index)
            self.assertEqual(denied.exception.reason, reason)
            self.assertNotEqual(client.fileno(), -1)
        self.assertEqual(owner.choose_company_ship(1980), second)
        path = owner.download(self.root / 'company-download')
        self.assertEqual(path.read_bytes(), self.content)
        owner.close()
        thread.join(3)
        self.assertEqual(errors, [])

    def test_invalid_company_ship_pages_close_connection(self):
        ship = self.company_ship(10)
        cases = (
            self.company_reply('company_ships', ships=[dict(ship, component_ref='[0x1]')], next_index=None),
            self.company_reply('company_ships', ships=[ship, self.company_ship(7)], next_index=None),
            self.company_reply('company_ships', ships=[ship], next_index=9),
            self.company_reply('company_ships', ships=[], next_index=10),
            dict(self.company_reply('company_ships', ships=[], next_index=None), attempt_id='a' * 32),
            b'x' * 4097,
        )
        for response in cases:
            with self.subTest(response=str(response)[:120]):
                owner, client, thread, _, _, _ = self.connect(send_command=False, company_exchanges=(
                    ({'kind': 'list_company_ships', 'after_index': 0}, response),
                ))
                owner.request_offer()
                with self.assertRaises(ValueError):
                    owner.list_company_ships()
                self.assertEqual(client.fileno(), -1)
                thread.join(3)

    def test_wrong_company_ship_choice_closes_connection(self):
        owner, client, thread, _, _, _ = self.connect(send_command=False, company_exchanges=(
            ({'kind': 'choose_company_ship', 'ship_index': 10},
             self.company_reply('company_ship_chosen', ship=self.company_ship(11))),
        ))
        owner.request_offer()
        with self.assertRaisesRegex(ValueError, 'differs from requested index'):
            owner.choose_company_ship(10)
        self.assertEqual(client.fileno(), -1)
        thread.join(3)

    @staticmethod
    def motion_grant():
        return {'session_epoch': 'epoch', 'epoch': '1'*32, 'generation': '2'*32,
                'index': 3, 'sector_macro': 'sector_a', 'token': '3'*32, 'incarnation': '4'*32}

    @staticmethod
    def pull_command(grant, request_id):
        return {'version': 1, 'kind': 'authority_motion_pull', 'connection_handle': 1,
                'session_epoch': 'epoch', 'incarnation': grant['incarnation'],
                'request_id': request_id, 'grant': copy.deepcopy(grant)}

    @staticmethod
    def motion_command(sequence):
        sample = {'version': 1, 'epoch': '1'*32, 'generation': '2'*32,
                  'index': 3, 'sequence': sequence, 'sector_macro': 'sector_a',
                  'source_time': 1000. + sequence / 10,
                  'x': 500. + sequence, 'y': -20., 'z': 30.,
                  'velocity': None if sequence == 1 else {'x': 10., 'y': 0., 'z': 0.}}
        return {'version': 1, 'kind': 'motion_snapshot', 'connection_handle': 1,
                'session_epoch': 'epoch', 'incarnation': '4'*32,
                'request_id': sequence, 'sample': sample}

    def test_motion_consumer_survives_load_handoff_with_original_samples(self):
        commands = [self.motion_command(sequence) for sequence in (1, 2)]
        owner, client, thread, errors, claims, replies = self.connect(authority_commands=commands)
        bound = self.observe(owner)
        consumed, consumer_threads = [], []

        def consume(sample, despawn):
            consumed.append((copy.deepcopy(sample), despawn))
            consumer_threads.append(threading.get_ident())
            return True

        owner.activate(bound.offer.attempt_id, Controller(), apply_motion=consume)
        deadline = time.monotonic() + 4
        receipts = 0
        while receipts < len(commands):
            if owner.poll(.1):
                receipts += 1
            if time.monotonic() >= deadline:
                self.fail('motion receipts timed out')
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(claims), 1)
        self.assertEqual(consumed, [(command['sample'], False) for command in commands])
        self.assertEqual(consumer_threads, [threading.get_ident()] * len(commands))
        self.assertEqual([reply['sample_sha256'] for reply in replies],
                         [motion_digest(command['sample']) for command in commands])
        self.assertTrue(all(reply['kind'] == 'motion_receipt' and reply['status'] == 'accepted'
                            for reply in replies))
        owner.close()
        self.assertEqual(client.fileno(), -1)

    def test_invalid_motion_consumer_closes_before_claim(self):
        owner, client, thread, errors, claims, _ = self.connect(send_command=False)
        bound = self.observe(owner)
        with self.assertRaisesRegex(ValueError, 'motion consumer must be callable'):
            owner.activate(bound.offer.attempt_id, Controller(), apply_motion=object())
        self.assertEqual(client.fileno(), -1)
        thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(claims, [])

    def test_motion_consumer_failure_fences_connection_without_receipt(self):
        for mode in ('exception', 'rejected', 'mutated', 'reentry'):
            with self.subTest(mode=mode):
                owner, client, thread, errors, _, replies = self.connect(
                    authority_commands=[self.motion_command(1)])
                bound = self.observe(owner)

                def consume(sample, despawn):
                    if mode == 'exception':
                        raise RuntimeError('injected consumer failure')
                    if mode == 'mutated':
                        sample['x'] += 1
                    if mode == 'reentry':
                        try:
                            owner.poll()
                        except RuntimeError:
                            pass
                    return mode != 'rejected'

                owner.activate(bound.offer.attempt_id, Controller(), apply_motion=consume)
                with self.assertRaises((RuntimeError, ValueError)):
                    while True:
                        owner.poll(.1)
                self.assertEqual(client.fileno(), -1)
                thread.join(4)
                self.assertEqual(errors, [])
                self.assertEqual(replies, [])

    def test_real_motion_protocol_feeds_bounded_stream_without_rewriting_sample(self):
        from authority_native_client_stream import LiveSourceInput

        commands = [self.motion_command(sequence) for sequence in (1, 2)]
        source = LiveSourceInput(epoch='1'*32, index=3, sector_macro='sector_a', receipt_max_age=5)
        self.addCleanup(source._close)
        # The native INITIAL boundary is simulated; only TCP and the queue are real.
        source._ready({'x': 500., 'y': -20., 'z': 30., 'game_time': 20.})
        owner, _, thread, errors, _, replies = self.connect(authority_commands=commands)
        bound = self.observe(owner)
        owner.activate(bound.offer.attempt_id, Controller(), apply_motion=source.accept_motion)
        receipts = 0
        receipt_deadline = time.monotonic() + 4
        while receipts < 2:
            receipts += bool(owner.poll(.1))
            if time.monotonic() >= receipt_deadline:
                self.fail('stream receipts timed out')
        thread.join(4)
        source.finish()
        deadline = time.monotonic() + 1
        abort = threading.Event()
        for command in commands:
            sample, received_at = source._next(guard=lambda: True, abort=abort, deadline=deadline)
            self.assertEqual(sample, command['sample'])
            self.assertLessEqual(received_at, time.monotonic())
        self.assertIsNone(source._next(guard=lambda: True, abort=abort, deadline=deadline))
        self.assertEqual(errors, [])
        self.assertEqual(len(replies), 2)

    def test_unready_closed_or_full_stream_never_acknowledges_rejected_motion(self):
        from authority_native_client_stream import LiveSourceInput

        for state in ('unready', 'closed', 'full'):
            with self.subTest(state=state):
                source = LiveSourceInput(epoch='1'*32, index=3, sector_macro='sector_a', capacity=1)
                self.addCleanup(source._close)
                if state != 'unready':
                    source._ready({'x': 500., 'y': -20., 'z': 30., 'game_time': 20.})
                if state == 'closed':
                    source._close()
                commands = [self.motion_command(sequence) for sequence in (1, 2)]
                owner, client, thread, errors, _, replies = self.connect(authority_commands=commands)
                bound = self.observe(owner)
                owner.activate(bound.offer.attempt_id, Controller(), apply_motion=source.accept_motion)
                with self.assertRaises(ValueError):
                    while True:
                        owner.poll(.1)
                self.assertEqual(client.fileno(), -1)
                thread.join(4)
                self.assertEqual(errors, [])
                self.assertEqual(len(replies), 1 if state == 'full' else 0)

    def test_authority_callbacks_survive_load_handoff_and_copy_grant(self):
        grant = self.motion_grant()
        expected_grant = copy.deepcopy(grant)
        commands = [self.pull_command(grant, request_id) for request_id in (1, 2)]
        owner, client, thread, errors, claims, replies = self.connect(authority_commands=commands)
        bound = self.observe(owner)
        sequence = 0
        def pull():
            nonlocal sequence
            sequence += 1
            rate = None if sequence == 1 else {'x': 1., 'y': 0., 'z': 0.}
            angular_rate = None if sequence == 1 else {'yaw': 0., 'pitch': 0., 'roll': 0.}
            return {'version': 2, 'epoch': expected_grant['epoch'], 'generation': expected_grant['generation'],
                    'index': 3, 'sequence': sequence, 'sector_macro': 'sector_a', 'source_time': float(sequence),
                    'x': float(sequence), 'y': 0., 'z': 0., 'velocity': rate,
                    'orientation': {'yaw': 0., 'pitch': 0., 'roll': 0.}, 'angular_rate': angular_rate}
        owner.activate(bound.offer.attempt_id, Controller(), authority_grant=grant,
                       authority_guard=lambda value: value == expected_grant, pull_authority_motion=pull)
        grant['sector_macro'] = 'mutated_after_activation'
        deadline = time.monotonic() + 4
        sent = 0
        while sent < 2:
            if owner.poll(.1):
                sent += 1
            if time.monotonic() >= deadline:
                self.fail('authority motion replies timed out')
        thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(len(claims), 1)
        self.assertEqual([reply['sample']['sequence'] for reply in replies], [1, 2])
        self.assertIsNone(replies[0]['sample']['velocity'])
        self.assertEqual(replies[1]['sample']['velocity'], {'x': 1., 'y': 0., 'z': 0.})
        owner.close(); self.assertEqual(client.fileno(), -1)

    def test_invalid_authority_configuration_closes_before_claim(self):
        good = self.motion_grant()
        variants = [
            {'authority_grant': good, 'authority_guard': lambda value: True},
            {'authority_grant': good, 'pull_authority_motion': lambda: None},
            {'authority_guard': lambda value: True, 'pull_authority_motion': lambda: None},
            {'authority_grant': {**good, 'session_epoch': 'foreign'},
             'authority_guard': lambda value: True, 'pull_authority_motion': lambda: None},
            {'authority_grant': {**good, 'token': 'invalid'},
             'authority_guard': lambda value: True, 'pull_authority_motion': lambda: None},
        ]
        for options in variants:
            with self.subTest(options=tuple(options)):
                owner, client, thread, errors, claims, _ = self.connect(send_command=False)
                bound = self.observe(owner)
                with self.assertRaises(ValueError):
                    owner.activate(bound.offer.attempt_id, Controller(), **options)
                self.assertEqual(client.fileno(), -1)
                thread.join(4)
                self.assertEqual(errors, []); self.assertEqual(claims, [])

    def test_authority_callback_exception_or_reentry_fences_without_reply(self):
        for mode in ('exception', 'reentry', 'close', 'guard_revoked'):
            with self.subTest(mode=mode):
                grant = self.motion_grant()
                owner, client, thread, errors, _, replies = self.connect(
                    authority_commands=[self.pull_command(grant, 1)])
                bound = self.observe(owner)
                def pull():
                    if mode == 'exception':
                        raise RuntimeError('injected source failure')
                    try:
                        if mode == 'close':
                            owner.close()
                        else:
                            owner.poll()
                    except RuntimeError:
                        pass
                    return None
                owner.activate(bound.offer.attempt_id, Controller(), authority_grant=grant,
                               authority_guard=lambda value: mode != 'guard_revoked' and value == grant,
                               pull_authority_motion=pull)
                with self.assertRaises(RuntimeError):
                    while True:
                        owner.poll(.1)
                self.assertEqual(client.fileno(), -1)
                thread.join(4)
                self.assertEqual(errors, []); self.assertEqual(replies, [])

    def test_reentrant_controller_fences_owner_before_reply(self):
        owner, client, thread, errors, _, replies = self.connect()
        bound = self.observe(owner)
        class ReentrantController(Controller):
            def resume(self, handles):
                try:
                    owner.poll()
                except RuntimeError:
                    pass
                return True
        owner.activate(bound.offer.attempt_id, ReentrantController(), participant_timeout=3)
        with self.assertRaises(RuntimeError):
            while True:
                owner.poll(.1)
        self.assertEqual(client.fileno(), -1)
        thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(replies, [])

    def test_wrong_admission_closes_without_running_controller(self):
        owner, client, thread, errors, _, _ = self.connect(accepted_attempt='f' * 32, send_command=False)
        bound = self.observe(owner)
        controller = Controller()
        with self.assertRaises(ValueError):
            owner.activate(bound.offer.attempt_id, controller)
        self.assertEqual(client.fileno(), -1)
        self.assertEqual(controller.calls, [])
        thread.join(4)
        self.assertEqual(errors, [])

    def test_interruption_after_transfer_closes_original_socket(self):
        owner, client, thread, errors, _, _ = self.connect(send_command=False)
        bound = self.observe(owner)
        original_transfer = ExperimentalRemoteLoadClient.take_connection
        def interrupted_transfer(load):
            original_transfer(load)
            raise InterruptedError('interrupted immediately after transfer')
        with patch.object(ExperimentalRemoteLoadClient, 'take_connection', interrupted_transfer):
            with self.assertRaises(InterruptedError):
                owner.activate(bound.offer.attempt_id, Controller())
        self.assertEqual(client.fileno(), -1)
        thread.join(4)
        self.assertEqual(errors, [])

    def test_invalid_controller_closes_without_claim(self):
        owner, client, thread, errors, claims, _ = self.connect(send_command=False)
        bound = self.observe(owner)
        with self.assertRaises(ValueError):
            owner.activate(bound.offer.attempt_id, object())
        self.assertEqual(client.fileno(), -1)
        thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(claims, [])

    def test_reentrant_controller_property_does_not_send_claim(self):
        owner, client, thread, errors, claims, _ = self.connect(send_command=False)
        bound = self.observe(owner)
        class ReentrantController:
            @property
            def pause(self):
                try:
                    owner.close()
                except RuntimeError:
                    pass
                return lambda handles: True
            def resume(self, handles):
                return True
        with self.assertRaises(RuntimeError):
            owner.activate(bound.offer.attempt_id, ReentrantController())
        self.assertEqual(client.fileno(), -1)
        thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(claims, [])

    def test_invalid_participant_timeout_closes_without_claim(self):
        owner, client, thread, errors, claims, _ = self.connect(send_command=False)
        bound = self.observe(owner)
        with self.assertRaises(ValueError):
            owner.activate(bound.offer.attempt_id, Controller(), participant_timeout=0)
        self.assertEqual(client.fileno(), -1)
        thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(claims, [])

    @unittest.skipUnless(sys.platform == 'linux', 'requires Linux loopback aliases')
    def test_nonmatching_loopback_peer_closes_without_claim(self):
        owner, client, thread, errors, claims, _ = self.connect(send_command=False, host='127.0.0.2')
        bound = self.observe(owner)
        with self.assertRaises(ValueError):
            owner.activate(bound.offer.attempt_id, Controller())
        self.assertEqual(client.fileno(), -1)
        thread.join(4)
        self.assertEqual(errors, [])
        self.assertEqual(claims, [])

    def test_foreign_thread_rejected_without_losing_connection(self):
        owner, client, thread, errors, _, _ = self.connect(send_command=False)
        caught = []
        def misuse():
            try:
                owner.close()
            except RuntimeError as error:
                caught.append(str(error))
        foreign = threading.Thread(target=misuse)
        foreign.start()
        foreign.join(3)
        self.assertEqual(len(caught), 1)
        self.assertNotEqual(client.fileno(), -1)
        self.assertEqual(owner.request_offer().offer, self.offer)
        owner.close()
        thread.join(4)
        self.assertEqual(errors, [])

    def test_rejected_constructor_preserves_caller_socket(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        client = socket.create_connection(listener.getsockname(), timeout=3)
        peer, _ = listener.accept()
        try:
            with self.assertRaises(ValueError):
                ExperimentalClientOwner(client, enabled=False, file_host='127.0.0.1',
                    file_port=self.service.port, file_token='a' * 32, file_slot=1,
                    deadline=time.monotonic() + 10)
            self.assertNotEqual(client.fileno(), -1)
        finally:
            client.close()
            peer.close()
            listener.close()
