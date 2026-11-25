"""CLI/config checks only; no Windows, X4 or network connection."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'fixture_cli', ROOT / 'tools/dev/authority_windows_fixture_client.py')
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)

from authority.company_ship_selection import CompanyShipChoiceDenied, reserve_company_ship


class FixtureCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.connection = self.root / 'connection.json'
        self.settings = dict(server_host='example.invalid', server_port=47050,
                             server_fingerprint='a' * 64, shared_key='b' * 64,
                             file_token='c' * 32, file_slot=1)
        self.connection.write_text(json.dumps(self.settings))

    def test_find_game_uses_packaged_steam_libraries_with_spaces(self):
        from lockstep.launcher import game

        steamapps = self.root / 'Steam root' / 'steamapps'
        library = self.root / 'Alternate Steam Libräry'
        installed = library / 'steamapps/common/X4 Foundations'
        steamapps.mkdir(parents=True)
        installed.mkdir(parents=True)
        (steamapps / 'libraryfolders.vdf').write_text(
            f'"libraryfolders" {{ "1" {{ "path" "{library}" }} }}', encoding='utf-8')
        (steamapps / 'appmanifest_392160.acf').touch()
        (installed / 'X4.exe').touch()
        output, errors = io.StringIO(), io.StringIO()
        with (patch.object(game, 'steam_root', return_value=steamapps.parent),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
            self.assertEqual(cli.main(['--find-game']), 1)
            self.assertEqual(output.getvalue(), '')
            self.assertIn('Pass -Game', errors.getvalue())

            (library / 'steamapps/appmanifest_392160.acf').touch()
            self.assertEqual(cli.main(['--find-game']), 0)
        self.assertEqual(json.loads(output.getvalue()), {'game': str(installed.resolve())})

    def test_explicit_game_bypasses_steam_discovery(self):
        from lockstep.launcher import game

        installed = self.root / 'Explicit X4'
        arguments = ['--connection-file', str(self.connection), '--game', str(installed),
                     '--view-container', str(self.root / 'view'), '--out', str(self.root / 'out'),
                     '--network-dll', str(self.root / 'net.dll'), '--network-dll-sha256', 'a' * 64]
        with (patch.object(game, 'find_install') as discover, patch.object(cli, 'run', return_value={}) as run,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(cli.main(arguments), 0)
        discover.assert_not_called()
        self.assertEqual(run.call_args.args[0].game, installed)

    def test_connection_decodes_only_expected_fields(self):
        settings = cli.read_connection_settings(self.connection)
        self.assertEqual(settings, self.settings | {'shared_key': bytes.fromhex('b' * 64)})
        for changes in ({'file_slot': True}, {'extra': 'secret'}, {'shared_key': 'short'}):
            with self.subTest(changes=changes):
                self.connection.write_text(json.dumps(self.settings | changes))
                with self.assertRaises(ValueError):
                    cli.read_connection_settings(self.connection)
        self.connection.write_text('{"shared_key":"one","shared_key":"two"}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            cli.read_connection_settings(self.connection)

    def test_connection_bound_and_reparse_rejection(self):
        self.connection.write_bytes(b' ' * 4097)
        with self.assertRaisesRegex(ValueError, 'bounded'):
            cli.read_connection_settings(self.connection)
        from types import SimpleNamespace
        import stat
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(
                st_mode=stat.S_IFREG, st_size=100, st_file_attributes=0x400)):
            with self.assertRaises(ValueError):
                cli.read_connection_settings(self.connection)

    def test_observation_window_starts_at_first_admitted_poll(self):
        window = cli.ObservationWindow(30)
        with patch.object(cli.time, 'monotonic', side_effect=(100, 129, 130)):
            self.assertFalse(window())
            self.assertFalse(window())
            self.assertTrue(window())

    def test_motion_config_is_explicit_bounded_and_fixture_checked(self):
        path = self.root / 'motion.json'
        request = {'generation': '1' * 32, 'indices': [1318], 'timeout': 5,
                   'saved_playback_fixture': 'recovery_enh_870'}
        path.write_text(json.dumps(request))
        self.assertEqual(cli.read_motion_request(path), request)
        for invalid in (None, {**request, 'indices': [1]}, {**request, 'timeout': 30},
                        {**request, 'generation': 'bad'}, {**request, 'extra': True}):
            with self.subTest(invalid=invalid):
                path.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    cli.read_motion_request(path)

    def test_motion_request_counts_both_ipc_deadlines_in_overall_limit(self):
        from authority.windows_fixture_session import validate_motion_request
        request = {'generation': '1' * 32, 'indices': [1318], 'timeout': 30}
        validate_motion_request(request, participant_timeout=239)
        with self.assertRaisesRegex(ValueError, 'bounded'):
            validate_motion_request(request, participant_timeout=240)

    def test_failure_does_not_print_exception_text_or_credentials(self):
        arguments = ['--connection-file', str(self.connection), '--game', str(self.root / 'game'),
                     '--view-container', str(self.root / 'view'), '--out', str(self.root / 'out'),
                     '--network-dll', str(self.root / 'net.dll'), '--network-dll-sha256', 'a' * 64]
        output = io.StringIO()
        with patch.object(cli, 'run', side_effect=RuntimeError('PRIVATE_CREDENTIAL')):
            with contextlib.redirect_stderr(output):
                self.assertEqual(cli.main(arguments), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report['error_type'], 'RuntimeError')
        self.assertEqual(report['status'], 'failed')
        self.assertNotIn('PRIVATE_CREDENTIAL', output.getvalue())

    def test_cli_reports_session_full_from_remote_client(self):
        arguments = ['--connection-file', str(self.connection), '--game', str(self.root / 'game'),
                     '--view-container', str(self.root / 'view'), '--out', str(self.root / 'out'),
                     '--network-dll', str(self.root / 'net.dll'), '--network-dll-sha256', 'a' * 64]
        with (patch.object(cli, 'run', side_effect=cli.SessionFull('private detail')),
              contextlib.redirect_stdout(io.StringIO()) as stdout,
              contextlib.redirect_stderr(io.StringIO()) as stderr):
            self.assertEqual(cli.main(arguments), 1)
        self.assertEqual(stderr.getvalue(), 'Session Full\n')
        self.assertEqual(stdout.getvalue(), '')

    def test_terminal_selector_explains_lost_choice_and_prompts_again(self):
        first = dict(ship_index=7, code='ABC-123', name='Scout', sector_macro='sector_01_macro')
        second = dict(ship_index=8, code='DEF-456', name='Courier', sector_macro='sector_01_macro')
        owner = Mock()
        owner.list_company_ships.side_effect = [(first, second), (second,)]
        owner.choose_company_ship.side_effect = [CompanyShipChoiceDenied('company_ship_unavailable'), second]
        with (patch('builtins.input', side_effect=['7', '8']),
              contextlib.redirect_stderr(io.StringIO()) as stderr):
            self.assertEqual(reserve_company_ship(owner, cli.terminal_company_ship_selector), 8)
        listing = stderr.getvalue()
        first_prompt = listing.index('Available company ships')
        rejection = listing.index('Company ship 7 is no longer available.')
        second_prompt = listing.index('Available company ships', first_prompt + 1)
        self.assertLess(rejection, second_prompt)
        self.assertNotIn('ABC-123', listing[second_prompt:])
        self.assertIn('DEF-456', listing[second_prompt:])

    def test_terminal_selector_explains_empty_catalogue_without_prompting(self):
        owner = Mock()
        owner.list_company_ships.return_value = ()
        with (patch('builtins.input') as read,
              contextlib.redirect_stderr(io.StringIO()) as stderr):
            with self.assertRaisesRegex(CompanyShipChoiceDenied, '^no_free_company_ships$'):
                reserve_company_ship(owner, cli.terminal_company_ship_selector)
        self.assertEqual(stderr.getvalue(),
                         'No company ships are free. Try joining again when one is available.\n')
        read.assert_not_called()
        owner.choose_company_ship.assert_not_called()

    def test_final_failure_preserves_live_confirmations(self):
        arguments = ['--connection-file', str(self.connection), '--game', str(self.root / 'game'),
                     '--view-container', str(self.root / 'view'), '--out', str(self.root / 'out'),
                     '--network-dll', str(self.root / 'net.dll'), '--network-dll-sha256', 'a' * 64]
        def late_failure(args, *, on_stage):
            on_stage('cockpit_control_confirmed', 18)
            on_stage('participant_admitted')
            on_stage('first_local_resume_confirmed')
            on_stage('first_local_motion_datagram_matched')
            on_stage('first_local_motion_peer_verified')
            on_stage('first_local_own_pose_validated')
            raise RuntimeError('PRIVATE_CREDENTIAL')

        output, errors = io.StringIO(), io.StringIO()
        with (patch.object(cli, 'run', side_effect=late_failure), contextlib.redirect_stdout(output),
              contextlib.redirect_stderr(errors)):
            self.assertEqual(cli.main(arguments), 1)
        progress = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(progress, [
            {'status': 'pilot_progress', 'stage': 'cockpit_control_confirmed', 'ship_index': 18},
            {'status': 'pilot_progress', 'stage': 'participant_admitted'},
            {'status': 'pilot_progress', 'stage': 'first_local_resume_confirmed'},
            {'status': 'pilot_progress', 'stage': 'first_local_motion_datagram_matched'},
            {'status': 'pilot_progress', 'stage': 'first_local_motion_peer_verified'},
            {'status': 'pilot_progress', 'stage': 'first_local_own_pose_validated'}])
        report = json.loads(errors.getvalue())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['confirmed_stages'], {
            'cockpit_control_confirmed': 18, 'participant_admitted': True,
            'first_local_resume_confirmed': True, 'first_local_motion_datagram_matched': True,
            'first_local_motion_peer_verified': True, 'first_local_own_pose_validated': True,
        })
        self.assertNotIn('PRIVATE_CREDENTIAL', errors.getvalue())

    def test_missing_opt_in_refuses_before_reading_credentials(self):
        args = type('Arguments', (), {'isolated_fixture_test': False})()
        with patch.object(cli, 'read_connection_settings') as reader:
            with self.assertRaises(ValueError):
                cli.run(args)
        reader.assert_not_called()

    def test_cockpit_playback_cli_options_require_company_motion_and_are_exclusive(self):
        base = ['--isolated-fixture-test', '--connection-file', str(self.connection),
                '--game', str(self.root / 'game'), '--view-container', str(self.root / 'view'),
                '--out', str(self.root / 'out'), '--network-dll', str(self.root / 'net.dll'),
                '--network-dll-sha256', 'a' * 64]
        parsed = cli.parser().parse_args([*base, '--company-motion', '--company-cockpit-playback'])
        self.assertTrue(parsed.company_cockpit_playback)
        self.assertIsNone(parsed.company_cockpit_playback_index)
        with patch.object(cli, 'os') as platform, \
                patch('authority.windows_fixture_session.run_fixture_session', return_value={}) as run_session, \
                patch('authority.windows_fixture_workspace.WindowsFixtureWorkspace'):
            platform.name = 'nt'
            observe = ['--observe-registered-npc', '42', 'NPC-042']
            cli.run(cli.parser().parse_args([*base, '--company-motion', *observe]))
            self.assertEqual(run_session.call_args.kwargs['observed_npc'], (42, 'NPC-042'))
            measure = ['--company-motion', '--company-ship-index', '1073', '--observe-registered-npc',
                       '943', 'MFQ-419', '--trial-registered-npc-measurement']
            cli.run(cli.parser().parse_args([*base, '--base-game-only', *measure]))
            self.assertTrue(run_session.call_args.kwargs['registered_npc_local_measurement'])
            with self.assertRaisesRegex(ValueError, 'vanilla pilot'):
                cli.run(cli.parser().parse_args([*base, *measure]))
            cli.run(parsed)
            with self.assertRaisesRegex(ValueError, 'requires company motion'):
                cli.run(cli.parser().parse_args([*base, '--company-cockpit-playback']))
        self.assertTrue(run_session.call_args.kwargs['company_motion'])
        self.assertTrue(run_session.call_args.kwargs['cockpit_radar_playback'])
        self.assertNotIn('company_hull_playback_index', run_session.call_args.kwargs)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args([*base, '--company-motion', '--company-cockpit-playback',
                                     '--company-cockpit-playback-index', '1074'])

    def test_fresh_company_profile_requires_explicit_seat_join_and_no_legacy_motion(self):
        base = ['--isolated-fixture-test', '--connection-file', str(self.connection),
                '--game', str(self.root / 'game'), '--view-container', str(self.root / 'view'),
                '--out', str(self.root / 'out'), '--network-dll', str(self.root / 'net.dll'),
                '--network-dll-sha256', 'a' * 64]
        parse = cli.parser().parse_args
        with (patch.object(cli, 'os') as platform,
              patch('authority.windows_fixture_session.run_fixture_session', return_value={}) as session,
              patch('authority.windows_fixture_workspace.WindowsFixtureWorkspace') as workspace,
              patch.object(cli, 'read_connection_settings', wraps=cli.read_connection_settings) as read_settings):
            platform.name = 'nt'
            for flags in ([], ['--play-until-exit'],
                          ['--choose-company-ship', '--motion-config', str(self.root / 'motion.json')]):
                with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, 'Company seat join'):
                    cli.run(parse([*base, '--fresh-company-profile', *flags]))
            read_settings.assert_not_called()
            workspace.assert_not_called()
            session.assert_not_called()
            for flags in (['--choose-company-ship'], ['--company-ship-index', '2'],
                          ['--company-motion'], ['--company-purchases']):
                with self.subTest(flags=flags):
                    cli.run(parse([*base, '--fresh-company-profile', *flags]))
                    self.assertTrue(workspace.call_args.kwargs['fresh_company_profile'])
                    self.assertTrue(session.call_args.kwargs['require_registry_snapshot'])
            cli.run(parse([*base, '--company-ship-index', '2']))
            self.assertNotIn('fresh_company_profile', workspace.call_args.kwargs)

    def test_fleet_orders_require_purchase_and_reach_the_fixture_session(self):
        args = ['--isolated-fixture-test', '--connection-file', str(self.connection),
                '--game', str(self.root / 'game'), '--view-container', str(self.root / 'view'),
                '--out', str(self.root / 'out'), '--network-dll', str(self.root / 'net.dll'),
                '--network-dll-sha256', 'a' * 64, '--company-fleet-orders']
        parse = cli.parser().parse_args
        with (patch.object(cli, 'os') as platform,
              patch('authority.windows_fixture_session.run_fixture_session', return_value={}) as session,
              patch('authority.windows_fixture_workspace.WindowsFixtureWorkspace') as workspace):
            platform.name = 'nt'
            with self.assertRaisesRegex(ValueError, '--company-fleet-orders requires --company-purchases'):
                cli.run(parse(args))
            session.assert_not_called()
            workspace.assert_not_called()
            cli.run(parse([*args, '--company-purchases']))
        self.assertIs(session.call_args.kwargs['company_fleet_orders'], True)
        self.assertIs(session.call_args.kwargs['company_purchases'], True)
        self.assertNotIn('company_trade_view', session.call_args.kwargs)

    def test_sales_require_purchase_and_market_view_and_reach_session(self):
        base = ['--isolated-fixture-test', '--connection-file', str(self.connection),
                '--game', str(self.root / 'game'), '--view-container', str(self.root / 'view'),
                '--out', str(self.root / 'out'), '--network-dll', str(self.root / 'net.dll'),
                '--network-dll-sha256', 'a' * 64]
        parse = cli.parser().parse_args
        with (patch.object(cli, 'os') as platform,
              patch('authority.windows_fixture_session.run_fixture_session', return_value={}) as session,
              patch('authority.windows_fixture_workspace.WindowsFixtureWorkspace') as workspace):
            platform.name = 'nt'
            for prerequisite in ([], ['--company-purchases'], ['--company-trade-view']):
                with self.subTest(prerequisite=prerequisite), self.assertRaisesRegex(ValueError, '--company-sales requires'):
                    cli.run(parse([*base, '--company-sales', *prerequisite]))
            session.assert_not_called()
            workspace.assert_not_called()
            normal = [*base, '--company-purchases', '--company-trade-view']
            cli.run(parse(normal))
            self.assertNotIn('company_sales', session.call_args.kwargs)
            cli.run(parse([*normal, '--company-sales']))
            self.assertIs(session.call_args.kwargs['company_sales'], True)


if __name__ == '__main__':
    unittest.main()
