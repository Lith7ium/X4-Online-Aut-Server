"""Player CLI, isolated command and owned seat-log binding. No X4 launches."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from authority.client_seat_overlay import COMPANY_PLAYBACK_UI
from authority.company_motion_source import CompanyMotionSource
from authority.load_messages import Offer
from authority.trusted_marker_load_session import MarkerBoundOffer
import authority_client_native as native
import authority_linux_player_client as cli
from authority_client_options import read_connection_settings
import authority_linux_company_seat as company_seat
from authority_linux_company_seat import SeatWatch
from authority_linux_player_namespace import command_with_private_lock

MARKER = '0' * 32
NONCE = 'e' * 32
STATUS = ('ready', 'd' * 32, 2000, 'f' * 64)
INDEX = 1970
OFFER = Offer(world_epoch='a' * 32, connection_handle=1, baseline_id=2, load_generation=3,
              attempt_id='b' * 32, checkpoint_id='checkpoint-' + 'c' * 32,
              sha256='a' * 64, size=128, name='j1.xml.gz')


def receipt_lines(offer=OFFER):
    ticket = native.ticket_for(MarkerBoundOffer(offer, MARKER))
    load = native.download.ReceiptWatch(None, ticket)
    return [
        native.download.TAG + load.expected[0],
        b'AUTH_MARKER load INSPECTED ' + MARKER.encode(),
        b'[X4LAN] AUTH_REGISTRY_FULL_INSPECT ' + MARKER.encode() + b' 2000',
        f'[X4LAN] AUTH_RECOVERY_REGISTRY {NONCE}|startup|{STATUS[1]}|2000'.encode(),
        f'[X4LAN] AUTH_CLIENT_SEAT {NONCE}|{STATUS[1]}|{INDEX}|controlled'.encode(),
        native.download.TAG + load.expected[1],
    ]


class SeatLogTests(unittest.TestCase):
    def test_installs_real_seat_overlay_in_disposable_case(self):
        bound = MarkerBoundOffer(OFFER, MARKER)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game, home = root / 'game', root / 'home'
            (game / 'extensions').mkdir(parents=True)
            home.mkdir()
            case, profile, _, _ = native.snapshot.prepare_case(root / 'run', game, home)
            native.download.install_probe(case, profile, native.ticket_for(bound))
            native.marked.install_marked_load_probe(case)
            snapshot = {'format': 3, 'source_registry_checked': True,
                        'evidence_scope': 'saved checkpoint bytes only', 'run_id': MARKER,
                        'epoch': STATUS[1], 'issued_count': STATUS[2], 'mapping_sha256': STATUS[3],
                        'availability_bits': '1' * STATUS[2], 'operational_bits': '1' * STATUS[2],
                        'available_count': STATUS[2], 'unavailable_count': 0, 'operational_count': STATUS[2]}
            target = profile / 'save/save_009.xml.gz'
            keepalive = Mock()
            with patch.object(company_seat, 'verify_join_snapshot', return_value=snapshot) as read, \
                    patch.object(company_seat.secrets, 'token_hex', return_value=NONCE):
                paths, watch = company_seat.install(case, profile, bound, target, INDEX, keepalive)
            self.assertEqual(read.call_args.args[0].path, target)
            self.assertIs(read.call_args.kwargs['pump'], keepalive)
            lab = case / 'extensions/x4_lan_lab'
            manifest = (lab / 'ui.xml').read_text()
            self.assertLess(manifest.index('ui/client_seat.lua'), manifest.index('ui/authority_download_load_probe.lua'))
            self.assertIn(lab / 'md/client_seat.xml', paths)
            self.assertIn('cfg.authority_registry_snapshot,1970', (lab / 'ui/authority_download_load_probe.lua').read_text())
            self.assertIn('authority_registry_snapshot={', (lab / 'ui/config.lua').read_text())
            watch.path.write_bytes(b'\n'.join(receipt_lines()) + b'\n')
            watch.verify()

    def test_company_playback_modules_only_in_opted_in_private_overlay(self):
        bound = MarkerBoundOffer(OFFER, MARKER)
        snapshot = {'format': 3, 'source_registry_checked': True,
                    'evidence_scope': 'saved checkpoint bytes only', 'run_id': MARKER,
                    'epoch': STATUS[1], 'issued_count': STATUS[2], 'mapping_sha256': STATUS[3],
                    'availability_bits': '1' * STATUS[2], 'operational_bits': '1' * STATUS[2],
                    'available_count': STATUS[2], 'unavailable_count': 0, 'operational_count': STATUS[2]}
        for target in (None, INDEX + 1):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'game/extensions').mkdir(parents=True)
                (root / 'home').mkdir()
                case, profile, _, _ = native.snapshot.prepare_case(root / 'run', root / 'game', root / 'home')
                native.download.install_probe(case, profile, native.ticket_for(bound))
                native.marked.install_marked_load_probe(case)
                source = CompanyMotionSource(playback_index=target)
                self.addCleanup(source.close)
                with patch.object(company_seat, 'verify_join_snapshot', return_value=snapshot):
                    paths, _ = company_seat.install(case, profile, bound, profile / 'save/save_009.xml.gz',
                                                    INDEX, Mock(), company_motion=source)
                lab = case / 'extensions/x4_lan_lab'
                config = (lab / 'ui/config.lua').read_text()
                manifest = (lab / 'ui.xml').read_text()
                self.assertEqual('playback_index=' in config, target is not None)
                self.assertIn('ui/company_observer_stream.lua', manifest)
                for name in COMPANY_PLAYBACK_UI:
                    asset = lab / 'ui' / name
                    self.assertEqual(asset.exists(), target is not None)
                    self.assertEqual(asset in paths, target is not None)
                    if target is not None:
                        self.assertLess(manifest.index('ui/' + name), manifest.index('ui/company_motion_source.lua'))
                if target is not None:
                    self.assertIn(f'playback_index={target}', config)
                    self.assertIn('X4LAN_MOTION_BUFFER=M', (lab / 'ui/motion_buffer.lua').read_text())

    def test_playback_target_must_be_operational_and_distinct_in_verified_register(self):
        for target, bits in ((INDEX, '1' * STATUS[2]), (INDEX + 1, '1' * INDEX),
                             (INDEX + 1, '1' * INDEX + '0')):
            with self.subTest(target=target, count=len(bits)):
                source = CompanyMotionSource(playback_index=target)
                self.addCleanup(source.close)
                with self.assertRaises(ValueError):
                    source.configure(STATUS[1], INDEX, operational_bits=bits)
                self.assertIsNone(source.registry)

    def test_no_admission_receipt_until_exact_same_offer_seat_and_pause(self):
        lines = receipt_lines()
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / 'engine-output.log'
            log = SeatWatch(path, MarkerBoundOffer(OFFER, MARKER), STATUS, NONCE, INDEX)
            self.assertFalse(log.poll())
            for line in lines[:-1]:
                with path.open('ab') as output:
                    output.write(line + b'\n')
                self.assertFalse(log.poll())
            with path.open('ab') as output:
                output.write(lines[-1])
            self.assertFalse(log.poll())
            with path.open('ab') as output:
                output.write(b'\n')
            self.assertTrue(log.poll())
            self.assertEqual(log.stage, 2)
            with path.open('ab') as output:
                output.write(lines[-2] + b'\n')
            with self.assertRaises(ValueError):
                log.verify()

    def test_refusal_and_cross_attempt_fail_before_a_claim(self):
        for replacement in (receipt_lines(replace(OFFER, attempt_id='2' * 32)),
                [*receipt_lines()[:4], receipt_lines()[4].replace(b'1970', b'1971')],
                [*receipt_lines()[:4], receipt_lines()[4].replace(b'AUTH_CLIENT_SEAT ', b'AUTH_CLIENT_SEAT_REFUSED ')
                 .replace(b'controlled', b'PILOT_PRESENT')]):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as out:
                path = Path(out) / 'engine-output.log'
                path.write_bytes(b'\n'.join(replacement) + b'\n')
                gate = SeatWatch(path, MarkerBoundOffer(OFFER, MARKER), STATUS, NONCE, INDEX)
                owner, participant = Mock(), Mock()
                with self.assertRaises(ValueError):
                    native.observe_and_activate(owner, participant, gate, native.ticket_for(
                        MarkerBoundOffer(OFFER, MARKER)), MARKER, Mock(), Mock(), {})
                owner.activate.assert_not_called()
                participant.enroll.assert_not_called()

    def test_replaced_engine_log_with_same_contents_is_rejected(self):
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / 'engine-output.log'
            bound = MarkerBoundOffer(OFFER, MARKER)
            path.write_bytes(b'\n'.join(receipt_lines()) + b'\n')
            gate = SeatWatch(path, bound, STATUS, NONCE, INDEX)
            gate.verify()
            other = path.with_suffix('.new')
            other.write_bytes(path.read_bytes())
            other.replace(path)
            with self.assertRaisesRegex(ValueError, 'log changed'):
                gate.verify()


class PlayerClientTests(unittest.TestCase):
    def test_local_namespace_keeps_only_client_lock_and_explicit_x_socket(self):
        lock = '/tmp/EGOSOFT_X4.lock'
        command = ['bwrap', '--ro-bind', '/', '/', '--bind', '/tmp/private-case', '/tmp/private-case',
                   '--bind', '/tmp/private-case/x4.lock', lock, '--die-with-parent', '--', 'X4']
        with patch.object(Path, 'is_socket', return_value=True):
            local = command_with_private_lock(command, display=':12.0', authorization=Path('/tmp/test-auth'))
        self.assertEqual(local[:7], ['bwrap', '--ro-bind', '/', '/', '--tmpfs', '/tmp', '--ro-bind'])
        self.assertEqual(local[7:12], ['/tmp/.X11-unix/X12', '/tmp/.X11-unix/X12',
                                      '--ro-bind', '/tmp/test-auth', '/tmp/test-auth'])
        position = local.index(lock)
        self.assertEqual(local[position - 2:position + 1], ['--bind', '/tmp/private-case/x4.lock', lock])
        self.assertEqual(command[4:7], ['--bind', '/tmp/private-case', '/tmp/private-case'])
        with patch.object(Path, 'is_socket', return_value=False), self.assertRaisesRegex(ValueError, 'socket'):
            command_with_private_lock(command, display=':12', authorization=Path('/tmp/test-auth'))

    def test_cli_uses_private_pinned_settings_and_selected_index(self):
        with tempfile.TemporaryDirectory() as temp:
            connection = Path(temp) / 'connection.json'
            connection.write_text(json.dumps(dict(server_host='example.invalid', server_port=4433,
                server_fingerprint='a' * 64, shared_key='b' * 64, file_token='c' * 32, file_slot=1)))
            connection.chmod(0o600)
            args = ['--connection-file', str(connection), '--display', ':1', '--out', '.local/run',
                    '--game', '/licensed/X4 Foundations', '--company-ship-index', str(INDEX)]
            with patch.object(native, 'run', return_value={'success': True}) as run, \
                    redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(args), 0)
            options = run.call_args.kwargs
            self.assertEqual(options['player_settings']['shared_key'], bytes.fromhex('b' * 64))
            self.assertEqual(options['player_settings']['server_fingerprint'], 'a' * 64)
            self.assertEqual(options['select_ship']([], None), INDEX)
            self.assertNotIn('b' * 64, stdout.getvalue())
            self.assertNotIn('company_motion', options)
            with patch.object(native, 'run', return_value={'success': True}) as run, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion']), 0)
            self.assertTrue(run.call_args.kwargs['company_motion'])
            self.assertNotIn('company_hull_playback_index', run.call_args.kwargs)
            option = ['--company-hull-playback-index', str(INDEX + 1)]
            with patch.object(native, 'run', return_value={'success': True}) as run, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([*args, '--company-motion', *option]), 0)
            self.assertEqual(run.call_args.kwargs['company_hull_playback_index'], INDEX + 1)
            with patch.object(native, 'run') as run, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main([*args, *option])
                with self.assertRaises(SystemExit):
                    cli.main([*args, '--company-motion', '--company-hull-playback-index', str(INDEX)])
            run.assert_not_called()

            connection.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'owner-only'):
                read_connection_settings(connection, require_private=True)
            with patch.object(native, 'run') as run, redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(cli.main(args), 1)
            run.assert_not_called()
            self.assertNotIn('b' * 64, stdout.getvalue())

    def test_cli_reports_session_full_from_native_client(self):
        self.assertNotEqual(native.error_code(RuntimeError('session_full')), 'session_full')
        error_code = native.error_code(cli.SessionFull('private detail'))
        self.assertEqual(error_code, 'session_full')
        result = {'success': False, 'error_code': error_code}
        arguments = ['--connection-file', 'private.json', '--display', ':1', '--out', '.local/run',
                     '--game', '/licensed/X4 Foundations', '--company-ship-index', str(INDEX)]
        with (patch.object(cli, 'read_connection_settings', return_value={}),
              patch.object(native, 'run', return_value=result),
              redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr):
            self.assertEqual(cli.main(arguments), 1)
        self.assertEqual(stderr.getvalue(), 'Session Full\n')
        self.assertEqual(stdout.getvalue(), '')

    def test_player_polls_company_client_only_during_the_active_run(self):
        calls = []
        owner = Mock()
        owner.poll.side_effect = lambda _: calls.append('control')
        process = Mock()
        process.poll.side_effect = [None, 0]
        company_client = Mock()
        company_client.poll.side_effect = lambda: calls.append('motion')
        self.assertEqual(native.poll_player(owner, process, Mock(), float('inf'), lambda: False,
                                             company_client=company_client), 'game_closed')
        self.assertEqual(calls, ['control', 'motion'])

    def test_player_loop_closes_only_its_confirmed_process_and_propagates_unrelated_errors(self):
        owner = Mock()
        process = Mock()
        guard = Mock()
        process.poll.side_effect = [None, 0]
        self.assertEqual(native.poll_player(owner, process, guard, float('inf'), lambda: False), 'game_closed')
        owner.poll.assert_called_once_with(.1)
        process.reset_mock()
        self.assertEqual(native.poll_player(owner, process, guard, float('inf'), lambda: True), 'user_left')
        process.poll.assert_not_called()
        process.poll.side_effect = None
        process.poll.return_value = None
        guard.side_effect = RuntimeError('protected_configuration_changed')
        with self.assertRaisesRegex(RuntimeError, 'protected_configuration_changed'):
            native.poll_player(owner, process, guard, float('inf'), lambda: False)
        process.wait.assert_not_called()


if __name__ == '__main__':
    unittest.main()
