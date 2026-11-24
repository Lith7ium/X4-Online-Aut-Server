"""CLI/config checks only; no Windows, X4 or network connection."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'fixture_cli', ROOT / 'tools/dev/authority_windows_fixture_client.py')
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


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

    def test_final_failure_preserves_live_confirmations(self):
        arguments = ['--connection-file', str(self.connection), '--game', str(self.root / 'game'),
                     '--view-container', str(self.root / 'view'), '--out', str(self.root / 'out'),
                     '--network-dll', str(self.root / 'net.dll'), '--network-dll-sha256', 'a' * 64]
        def late_failure(args, *, on_stage):
            on_stage('cockpit_control_confirmed', 18)
            on_stage('participant_admitted')
            raise RuntimeError('PRIVATE_CREDENTIAL')

        output, errors = io.StringIO(), io.StringIO()
        with (patch.object(cli, 'run', side_effect=late_failure), contextlib.redirect_stdout(output),
              contextlib.redirect_stderr(errors)):
            self.assertEqual(cli.main(arguments), 1)
        progress = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(progress, [
            {'status': 'pilot_progress', 'stage': 'cockpit_control_confirmed', 'ship_index': 18},
            {'status': 'pilot_progress', 'stage': 'participant_admitted'}])
        report = json.loads(errors.getvalue())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['confirmed_stages'], {'cockpit_control_confirmed': 18, 'participant_admitted': True})
        self.assertNotIn('PRIVATE_CREDENTIAL', errors.getvalue())

    def test_missing_opt_in_refuses_before_reading_credentials(self):
        args = type('Arguments', (), {'isolated_fixture_test': False})()
        with patch.object(cli, 'read_connection_settings') as reader:
            with self.assertRaises(ValueError):
                cli.run(args)
        reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
