"""Lifecycle composition with test doubles; no Windows, X4 or network run."""
from contextlib import contextmanager
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from authority import windows_fixture_session as subject
from authority.load_messages import Offer
from authority.load_observations import BoundReceiptStream
from authority.trusted_marker_load_session import MarkerBoundOffer


class FixtureSessionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.stop_confirmed = True
        self.start_error = self.poll_error = self.registration_error = self.close_error = None
        self.controller_error = self.redact_error = self.workspace_close_error = None
        self.construct_error = None
        self.inspection_error = self.keepalive_error = None
        self.inspection_hook = lambda: None
        self.registry_status = ('ready', '8'*32, 2, '9'*64)
        self.registry_nonce = '7'*32
        self.registry_log = ('[X4LAN] AUTH_RECOVERY_REGISTRY ' + self.registry_nonce +
                             '|startup|' + self.registry_status[1] + '|2\n').encode('ascii')
        self.poll_hook = lambda: None
        self.poll_result = True
        self.final_log_extra = b''
        self.company_seat_phase = 'controlled'
        self.offer = Offer(world_epoch='a'*32, connection_handle=1, baseline_id=2, load_generation=3,
            checkpoint_id='checkpoint-'+'b'*32, sha256='c'*64, attempt_id='d'*32, size=10, name='j1.xml.gz')
        self.bound = MarkerBoundOffer(self.offer, 'e'*32)
        fields = '|'.join(str(v) for v in ('a'*32, 1, 2, 3, self.offer.checkpoint_id, self.offer.sha256,
                                           self.offer.attempt_id, 'save_009')).encode('ascii')
        self.log = (b'[X4LAN] AUTH_DOWNLOAD_LOAD load_requested|' + fields + b'\n'
                    b'[X4LAN] AUTH_MARKER load INSPECTED ' + b'e'*32 + b'\n' + self.registry_log +
                    b'[X4LAN] AUTH_DOWNLOAD_LOAD pause_observed|' + fields + b'\n')
        test = self

        class Workspace:
            view = Path('/private/view')
            save_dir = Path('/private/45841/save')
            download_dir = Path('/private/download')
            def prepare(self): test.calls.append('workspace_prepare')
            def guard(self): test.calls.append('workspace_guard'); return True
            def register_overlay(self, hashes):
                test.calls.append(('register_overlay', hashes))
                if test.registration_error: raise test.registration_error
            def read_log(self): test.calls.append('read_log'); return test.log + test.final_log_extra
            def redact_config(self):
                test.calls.append('redact')
                if test.redact_error: raise test.redact_error
            def close(self):
                test.calls.append('workspace_close')
                if test.workspace_close_error: raise test.workspace_close_error
        self.workspace = Workspace()

        class Owner:
            def request_offer(self): test.calls.append('offer'); return test.bound
            def download(self, directory):
                test.calls.append(('download', directory)); return Path('/private/download/input')
            def copy_to_new_slot(self, source, target):
                test.calls.append(('stage', source, target)); return target
            def poll(self):
                test.calls.append('load_keepalive')
                if test.keepalive_error: raise test.keepalive_error
        self.owner = Owner()

        def inspect_registry(checkpoint, *, pump):
            test.calls.append(('inspect_registry', checkpoint))
            test.assertEqual(checkpoint.path, test.workspace.save_dir / 'save_009.xml.gz')
            test.assertEqual((checkpoint.checkpoint_id, checkpoint.sha256, checkpoint.size, checkpoint.marker),
                             (test.offer.checkpoint_id, test.offer.sha256, test.offer.size, test.bound.marker))
            pump()
            test.inspection_hook()
            if test.inspection_error: raise test.inspection_error
            return test.registry_status
        self.inspect_registry = inspect_registry

        @contextmanager
        def connection(**options):
            test.connection_options = options
            test.calls.append('tls_enter')
            try: yield test.owner
            finally: test.calls.append('tls_exit')
        self.connection = connection

        class Controller:
            def __init__(self, *, enabled, timeout):
                test.calls.append(('controller_create', enabled, timeout))
                self.lease = 'f'*32
                self.sock = SimpleNamespace(getsockname=lambda: ('127.0.0.1', 12345))
            def close(self):
                test.calls.append('controller_close')
                if test.controller_error: raise test.controller_error
        self.controller = Controller

        class Process:
            def __init__(self, *, enabled):
                test.calls.append(('process_create', enabled))
                if test.construct_error: raise test.construct_error
                self.cleanup_confirmed = False
            def start(self, view, *, profile_id, guard):
                test.calls.append(('process_start', view, profile_id, guard()))
                if test.start_error: raise test.start_error
            def is_running(self): test.calls.append('process_running'); return True
            def close(self):
                test.calls.append('process_close')
                if test.close_error: raise test.close_error
                self.cleanup_confirmed = test.stop_confirmed
                return test.stop_confirmed
        self.process = Process

        class Session:
            @property
            def company_seat_at_admission(self): return test.company_seat_phase
            @property
            def company_seat_status(self): return test.company_seat_phase
            def __init__(self, owner, bound, controller, *, slot, read_log, guard, deadline,
                         registry_status, registry_nonce, require_full_inspection, apply_motion, motion_control, seat_index=None, on_stage=None):
                test.assertEqual(seat_index, test.installed_seat)
                test.assertEqual(require_full_inspection, test.installed_registry_snapshot is not None)
                test.assertIsNone(apply_motion)
                test.assertIsNone(motion_control)
                test.assertEqual(registry_status, test.registry_status)
                test.assertEqual(registry_nonce, test.registry_nonce)
                test.calls.append(('session_create', owner is test.owner, bound == test.bound, slot, guard()))
                self.records = BoundReceiptStream(bound, slot=slot, registry_status=registry_status,
                    registry_nonce=registry_nonce, require_full_inspection=require_full_inspection,
                    seat_index=seat_index)
            def start(self): test.calls.append('session_start'); return True
            def poll(self, timeout):
                test.calls.append(('session_poll', timeout))
                if test.poll_error: raise test.poll_error
                test.poll_hook()
                return test.poll_result
            def close(self): test.calls.append('session_close')
            def verify_final_log(self, snapshot): return self.records.feed(snapshot)
        self.session = Session

        def install(view, **options):
            test.assertEqual(options['registry_status'], test.registry_status)
            test.assertEqual(options['registry_nonce'], test.registry_nonce)
            test.installed_company_source = options.get('company_motion')
            test.installed_seat = options.get('seat_index')
            test.installed_registry_snapshot = options.get('registry_snapshot')
            test.calls.append(('install', view, options['bound_offer'] == test.bound,
                               options['participant_port'], options['participant_lease']))
            return {'ui/config.lua':'0'*64}
        self.install = install

    def run_session(self, **changes):
        stops = iter((False, True))
        options = dict(enabled=True, workspace=self.workspace, server_host='example.test', server_port=443,
            server_fingerprint='1'*64, shared_key=b'2'*32, file_token='3'*32, file_slot=1,
            windows_dll=Path('/private/net.dll'), dll_sha256='4'*64, deadline=time.monotonic()+10,
            keepalive_interval=1, stop_requested=lambda: next(stops))
        options.update(changes)
        with patch.object(subject, 'client_connection', self.connection), \
             patch.object(subject, 'LocalParticipantControl', self.controller), \
             patch.object(subject, 'WindowsX4Process', self.process), \
             patch.object(subject, 'ObservedClientSession', self.session), \
             patch.object(subject, 'saved_status', self.inspect_registry), \
             patch.object(subject.secrets, 'token_hex', return_value=self.registry_nonce), \
             patch.object(subject, 'install_fixture_overlay', self.install):
            return subject.run_fixture_session(**options)

    def test_snapshot_join_passes_verified_staged_observations_to_load_gate(self):
        inspection = f'[X4LAN] AUTH_REGISTRY_FULL_INSPECT {self.bound.marker} 2\n'.encode('ascii')
        self.log = self.log.replace(self.registry_log, inspection + self.registry_log)
        snapshot = {'epoch': self.registry_status[1], 'issued_count': 2,
                    'mapping_sha256': self.registry_status[3], 'operational_bits': '10'}
        def inspect(checkpoint, *, pump):
            self.assertEqual(checkpoint.path, self.workspace.save_dir / 'save_009.xml.gz')
            self.assertEqual(checkpoint.marker, self.bound.marker)
            pump()
            return snapshot
        with patch.object(subject, 'verify_join_snapshot', side_effect=inspect) as reader:
            result = self.run_session(require_registry_snapshot=True)
        self.assertTrue(result['client_admitted'])
        reader.assert_called_once()
        self.assertIs(self.installed_registry_snapshot, snapshot)

    def prepare_company_seat(self):
        ship = {'ship_index': 2, 'sector_macro': 'sector_b', 'code': 'ABC-123', 'name': None}
        snapshot = {'epoch': self.registry_status[1], 'issued_count': 2,
                    'mapping_sha256': self.registry_status[3], 'operational_bits': '11'}
        inspection = f'[X4LAN] AUTH_REGISTRY_FULL_INSPECT {self.bound.marker} 2\n'.encode()
        seat = f'[X4LAN] AUTH_CLIENT_SEAT {self.registry_nonce}|{snapshot["epoch"]}|2|controlled\n'.encode()
        self.log = self.log.replace(self.registry_log, inspection + self.registry_log + seat)
        self.owner.list_company_ships = lambda: (ship,)
        self.owner.choose_company_ship = lambda index: ship
        return snapshot

    def test_open_ended_pilot_stays_after_join_deadline_until_clean_x4_exit(self):
        snapshot = self.prepare_company_seat()
        clock = [100.0]
        game_running = [True]
        def stop():
            if clock[0] > 101:
                game_running[0] = False
            clock[0] = 7200.0
            return False
        with patch.object(subject, 'verify_join_snapshot', return_value=snapshot), \
             patch.object(subject.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.process, 'is_running', side_effect=lambda: game_running[0]), \
             patch.object(self.process, 'exit_code', create=True, return_value=0), \
             patch.object(self.session, 'continue_until_exit', create=True) as admitted:
            result = self.run_session(require_registry_snapshot=True, select_ship=lambda ships, pump: 2,
                                      play_until_exit=True, deadline=101, stop_requested=stop)
        admitted.assert_called_once()
        self.assertTrue(result['owned_cleanup_confirmed'])
        self.assertIn(('session_poll', .1), self.calls)

    def test_selected_ship_reaches_overlay_live_gate_and_final_audit(self):
        snapshot = self.prepare_company_seat()
        with patch.object(subject, 'verify_join_snapshot', return_value=snapshot):
            result = self.run_session(require_registry_snapshot=True, select_ship=lambda ships, pump: 2)
        self.assertEqual(result['controlled_company_ship_index'], 2)
        self.assertEqual(self.installed_seat, 2)

    def test_passenger_audit_keeps_the_reservation_without_claiming_control(self):
        snapshot = self.prepare_company_seat()
        self.company_seat_phase = 'aboard'
        self.log = self.log.replace(b'|2|controlled\n', b'|2|aboard\n')
        with patch.object(subject, 'verify_join_snapshot', return_value=snapshot):
            result = self.run_session(require_registry_snapshot=True, select_ship=lambda ships, pump: 2)
        self.assertEqual(result['reserved_company_ship_index'], 2)
        self.assertEqual(result['company_seat_at_admission'], 'aboard')
        self.assertNotIn('controlled_company_ship_index', result)

    def test_company_motion_starts_only_after_admission_and_cleans_up_before_x4(self):
        ship = {'ship_index': 2, 'sector_macro': 'sector_b', 'code': 'ABC-123', 'name': None}
        snapshot = {'epoch': self.registry_status[1], 'issued_count': 2,
                    'mapping_sha256': self.registry_status[3], 'operational_bits': '11'}
        inspection = f'[X4LAN] AUTH_REGISTRY_FULL_INSPECT {self.bound.marker} 2\n'.encode()
        seat = f'[X4LAN] AUTH_CLIENT_SEAT {self.registry_nonce}|{snapshot["epoch"]}|2|controlled\n'.encode()
        self.log = self.log.replace(self.registry_log, inspection + self.registry_log + seat)
        self.owner.list_company_ships = lambda: (ship,)
        self.owner.choose_company_ship = lambda index: ship
        self.owner.request_ship_motion = lambda: self.calls.append('request_ship_motion')
        self.owner.motion_port = 4567
        self.owner.ship_motion_credential = {'ticket': 'synthetic in-memory token'}
        observation = {'samples_confirmed': 1}
        source = SimpleNamespace(socket=object(), close=lambda: self.calls.append('source_close'),
                                 observer_stats=lambda: observation)
        validate_peer = object()
        counts = dict(samples_acknowledged=0, velocity_samples_acknowledged=0,
                      snapshots_received=0, visibility_ends=0)
        progress = []
        def poll():
            self.calls.append('motion_poll')
            n = self.calls.count('motion_poll')
            if n == 2:
                counts['samples_acknowledged'] = 1
            if n == 3:
                counts['snapshots_received'] = 1

        def note_stage(*event):
            progress.append((self.calls.count('motion_poll'), event))

        def start_motion(port, credential, source_for_client, validator):
            self.assertIn('session_start', self.calls)
            self.assertEqual(port, self.owner.motion_port)
            self.assertIs(credential, self.owner.ship_motion_credential)
            self.assertIs(source_for_client, source)
            self.assertIs(validator, validate_peer)
            self.calls.append('motion_start')
            return SimpleNamespace(poll=poll, close=lambda: self.calls.append('motion_close'),
                                   stats=counts.copy, failure=None)

        motion_module = SimpleNamespace(ReconnectingCompanyMotionClient=SimpleNamespace(observing=start_motion))
        with patch.object(subject, 'verify_join_snapshot', return_value=snapshot), \
             patch.object(subject, 'WindowsMotionPeer', return_value=validate_peer) as peer_factory, \
             patch('authority.company_motion_source.CompanyMotionSource', return_value=source) as source_factory, \
             patch.dict(sys.modules, {'authority.company_motion_reconnect': motion_module}):
            result = self.run_session(require_registry_snapshot=True,
                                      select_ship=lambda ships, pump: 2, company_motion=True,
                                      stop_requested=lambda: self.calls.count('motion_poll') == 4,
                                      on_stage=note_stage)
        self.assertEqual(progress, [
            (0, ('company_ship_reserved', 2)), (2, ('first_own_sample_acknowledged',)),
            (3, ('first_remote_pose_received',)),
        ])
        source_factory.assert_called_once_with(playback_index=None, cockpit_radar_playback=False,
                                               on_stage=note_stage)
        self.assertTrue(self.connection_options['motion_enabled'])
        self.assertIs(self.installed_company_source, source)
        self.assertEqual(result['company_motion'], counts)
        self.assertEqual(result['company_observer'], observation)
        self.assertNotIn(self.owner.ship_motion_credential['ticket'], str(result))
        self.assertIs(peer_factory.call_args.args[1], source.socket)
        first_download = self.calls.index(('download', self.workspace.download_dir))
        self.assertLess(self.calls.index('request_ship_motion'), first_download)
        order = (['session_start', 'motion_start'] + [('session_poll', .1), 'motion_poll'] * 4 +
                 ['motion_close', 'source_close', 'process_close', 'tls_exit'])
        self.assertEqual([call for call in self.calls if call in order], order)

    def test_race_refreshes_available_company_ships_without_choosing_for_the_player(self):
        first = {'ship_index': 1}
        second = {'ship_index': 2}
        catalogues = iter(((first, second), (second,)))
        self.owner.list_company_ships = lambda: next(catalogues)
        def choose(index):
            if index == 1:
                raise subject.CompanyShipChoiceDenied('company_ship_unavailable')
            return second
        self.owner.choose_company_ship = choose
        prompts = []
        def select(choices, keepalive):
            prompts.append(choices)
            keepalive()
            return choices[0]['ship_index']
        self.assertEqual(subject.reserve_company_ship(self.owner, select), 2)
        self.assertEqual(prompts, [(first, second), (second,)])

    def test_snapshot_refusal_or_ineligible_motion_prevents_launch(self):
        snapshot = {'epoch': self.registry_status[1], 'issued_count': 2,
                    'mapping_sha256': self.registry_status[3], 'operational_bits': '10'}
        for error, index in ((ValueError('corrupt snapshot'), 1), (None, 2), (None, 3)):
            with self.subTest(error=error, index=index), patch.object(
                    subject, 'verify_join_snapshot', side_effect=error, return_value=snapshot):
                self.calls.clear()
                motion = {'generation': '1' * 32, 'indices': [index], 'timeout': 1}
                with self.assertRaisesRegex(ValueError, 'corrupt snapshot|unavailable for motion'):
                    self.run_session(require_registry_snapshot=True, motion_request=motion)
                self.assertFalse(any(isinstance(call, tuple) and call[0] == 'install' for call in self.calls))
                self.assertIn('workspace_close', self.calls)

    def test_stages_starts_polls_then_stops_before_tls_and_redaction(self):
        result = self.run_session()
        self.assertEqual(result, {'client_admitted':True, 'participant_receipts':1,
                                  'owned_cleanup_confirmed':True, 'configuration_redacted':True})
        self.assertIn(('stage', Path('/private/download/input'), Path('/private/45841/save/save_009.xml.gz')), self.calls)
        self.assertIn(('controller_create', True, 30), self.calls)
        self.assertIn(('install', self.workspace.view, True, 12345, 'f'*32), self.calls)
        self.assertIn(('process_start', self.workspace.view, 45841, True), self.calls)
        self.assertLess(self.calls.index('load_keepalive'), self.calls.index(('controller_create', True, 30)))
        self.assertLess(self.calls.index('session_start'), self.calls.index(('session_poll', .1)))
        for before, after in (('process_close', 'read_log'), ('read_log', 'session_close'),
                              ('session_close', 'redact'), ('redact', 'tls_exit'),
                              ('tls_exit', 'workspace_close')):
            self.assertLess(self.calls.index(before), self.calls.index(after))

    def test_registry_or_keepalive_failure_prevents_installation_and_launch(self):
        for field in ('inspection_error', 'keepalive_error'):
            with self.subTest(field=field):
                self.calls.clear()
                setattr(self, field, RuntimeError('registry inspection aborted'))
                try:
                    with self.assertRaisesRegex(RuntimeError, 'registry inspection aborted'):
                        self.run_session()
                    self.assertFalse(any(isinstance(call, tuple) and call[0] in
                        ('install', 'controller_create', 'process_create', 'session_create') for call in self.calls))
                    self.assertIn('tls_exit', self.calls)
                    self.assertIn('workspace_close', self.calls)
                finally:
                    setattr(self, field, None)

    def test_inspection_cannot_outlive_deadline_and_then_launch(self):
        clock = [100.0]
        self.inspection_hook = lambda: clock.__setitem__(0, 102.0)
        with patch.object(subject.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RuntimeError, 'after registry inspection'):
                self.run_session(deadline=101.0)
        self.assertFalse(any(isinstance(call, tuple) and call[0] == 'install' for call in self.calls))

    def test_uncertain_stop_retains_process_and_never_redacts(self):
        self.stop_confirmed = False
        with self.assertRaises(subject.FixtureCleanupError) as caught:
            self.run_session()
        self.assertIsInstance(caught.exception.process_owner, self.process)
        self.assertIs(caught.exception.workspace, self.workspace)
        self.assertNotIn('redact', self.calls)
        self.assertLess(self.calls.index('process_close'), self.calls.index('tls_exit'))
        self.assertIn('workspace_close', self.calls)

    def test_launch_error_preserved_and_uncertain_owner_available(self):
        self.start_error = RuntimeError('launch failed')
        self.stop_confirmed = False
        with self.assertRaisesRegex(RuntimeError, 'launch failed') as caught:
            self.run_session()
        self.assertIsInstance(caught.exception.fixture_cleanup.process_owner, self.process)
        self.assertNotIn('redact', self.calls)
        self.assertIn('controller_close', self.calls)

    def test_failed_registration_leaves_unregistered_files_untouched(self):
        self.registration_error = ValueError('register failed')
        with self.assertRaisesRegex(ValueError, 'register failed'):
            self.run_session()
        self.assertNotIn('redact', self.calls)
        self.assertNotIn('process_close', self.calls)
        self.assertIn('controller_close', self.calls)

    def test_constructor_failure_before_launch_allows_owned_redaction(self):
        self.construct_error = RuntimeError('process unavailable')
        with self.assertRaisesRegex(RuntimeError, 'process unavailable'):
            self.run_session()
        self.assertIn('redact', self.calls)
        self.assertNotIn('process_close', self.calls)

    def test_final_duplicate_prevents_success_but_still_redacts(self):
        self.final_log_extra = b'[X4LAN] AUTH_MARKER load INSPECTED ' + b'e'*32 + b'\n'
        with self.assertRaisesRegex(subject.FixtureCleanupError, 'observations unconfirmed'):
            self.run_session()
        self.assertIn('redact', self.calls)
        self.assertLess(self.calls.index('process_close'), self.calls.index('read_log'))

    def test_final_registry_missing_changed_or_duplicated_prevents_success(self):
        original_log = self.log
        for replacement in (b'', self.registry_log.replace(b'|2\n', b'|3\n'), self.registry_log*2):
            with self.subTest(replacement=replacement):
                self.calls.clear()
                self.log = original_log.replace(self.registry_log, replacement)
                with self.assertRaisesRegex(subject.FixtureCleanupError, 'observations unconfirmed'):
                    self.run_session()
                self.assertIn('redact', self.calls)

    def test_primary_error_is_not_masked_by_workspace_cleanup(self):
        self.poll_error = ValueError('poll failed')
        self.workspace_close_error = OSError('workspace failed')
        with self.assertRaisesRegex(ValueError, 'poll failed') as caught:
            self.run_session()
        self.assertTrue(any('fixture workspace cleanup failed' in note for note in caught.exception.__notes__))
        self.assertIn('redact', self.calls)
        self.assertLess(self.calls.index('process_close'), self.calls.index('tls_exit'))

    def test_stop_callback_after_deadline_never_reports_success(self):
        clock = [100.0]
        def late_stop():
            clock[0] = 102.0
            return True
        with patch.object(subject.time, 'monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                self.run_session(deadline=101.0, stop_requested=late_stop)
        self.assertIn('process_close', self.calls)
        self.assertIn('redact', self.calls)

    def test_fast_polls_are_paced_but_slow_polls_are_not(self):
        for poll_duration, expected_sleep in ((0, .05), (.06, 0)):
            with self.subTest(poll_duration=poll_duration):
                clock = [100.0]
                sleeps = []
                self.poll_result = False
                self.poll_hook = lambda: clock.__setitem__(0, clock[0] + poll_duration)
                def sleep(duration):
                    sleeps.append(duration)
                    clock[0] += duration
                with patch.object(subject.time, 'monotonic', side_effect=lambda: clock[0]), \
                     patch.object(subject.time, 'sleep', side_effect=sleep):
                    result = self.run_session(deadline=101.0)
                self.assertEqual(result['participant_receipts'], 0)
                if expected_sleep:
                    self.assertEqual(len(sleeps), 1)
                    self.assertAlmostEqual(sleeps[0], expected_sleep)
                else:
                    self.assertEqual(sleeps, [])

    def test_idle_sleep_cannot_extend_deadline_into_success(self):
        clock = [100.0]
        sleeps = []
        self.poll_result = False
        def sleep(duration):
            sleeps.append(duration)
            clock[0] += duration
        with patch.object(subject.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(subject.time, 'sleep', side_effect=sleep):
            with self.assertRaises(TimeoutError):
                self.run_session(deadline=100.03)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], .03)
        self.assertIn('process_close', self.calls)
        self.assertIn('redact', self.calls)

    def test_invalid_credentials_do_not_prepare(self):
        cases = ({'shared_key':'2'*32}, {'shared_key':b'short'},
                 {'server_fingerprint':'A'*64}, {'file_token':'3'*31})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_session(**changes)
        self.assertEqual(self.calls, [])

    def test_no_opt_in_does_not_prepare(self):
        with self.assertRaises(ValueError):
            self.run_session(enabled=False)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
