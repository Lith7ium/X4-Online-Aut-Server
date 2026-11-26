"""Exercise the GPU-free provider wrapper without opening X4 or a display."""
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dev'))
import authority_provider_server as provider

ARGS = ['--game', '/private/game', '--store', '/private/source/checkpoints',
        '--out', '/private/prep']


class ProviderParkingTest(unittest.TestCase):
    def test_display_shutdown_if_log_close_fails_after_spawn(self):
        log = Mock()

        @contextmanager
        def failing_log():
            yield log
            raise OSError('log close failed')

        ready, notify = provider.os.pipe()
        process = Mock()
        process.poll.return_value = None
        with (
            patch.object(provider, 'authorize'),
            patch.object(provider.os, 'pipe', return_value=(ready, notify)),
            patch.object(Path, 'open', return_value=failing_log()),
            patch.object(provider.subprocess, 'Popen', return_value=process),
            patch.object(provider.select, 'select') as select,
        ):
            with self.assertRaisesRegex(OSError, 'log close failed'):
                with provider.private_display('Xvfb', 'xauth', 'xdpyinfo'):
                    self.fail('a partially initialized display was published')
        select.assert_not_called()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)
        for descriptor in (ready, notify):
            with self.assertRaises(OSError):
                provider.os.fstat(descriptor)

    def test_display_environment_restored_even_on_interrupt(self):
        with patch.dict(provider.os.environ, DISPLAY=':1'):
            provider.os.environ.pop('XAUTHORITY', None)
            with self.assertRaises(KeyboardInterrupt):
                with provider._display_environment(':99', Path('/private/auth')):
                    self.assertEqual(provider.os.environ['DISPLAY'], ':99')
                    self.assertEqual(provider.os.environ['XAUTHORITY'], '/private/auth')
                    raise KeyboardInterrupt
            self.assertEqual(provider.os.environ['DISPLAY'], ':1')
            self.assertNotIn('XAUTHORITY', provider.os.environ)

    def test_both_modes_keep_the_existing_software_rendered_path(self):
        icd = Path(__file__).resolve()

        def supervise(*_args, **_options):
            self.assertEqual(provider.os.environ['DISPLAY'], ':99')
            self.assertEqual(provider.os.environ['XAUTHORITY'], '/private/auth')
            return 0

        for mode in ('prepare', 'verify'):
            with (
                self.subTest(mode=mode),
                patch.object(provider.os, 'geteuid', return_value=1000),
                patch.dict(provider.os.environ, DISPLAY=':1', XAUTHORITY='/initial/auth'),
                patch.object(provider.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name),
                patch.object(provider, 'lavapipe_icd', return_value=icd),
                patch.object(provider, 'private_display', return_value=nullcontext((':99', '/private/auth', 'x4-private-aabbccddeeff0011'))) as display,
                patch.object(provider.supervisor, 'main', side_effect=supervise) as run,
            ):
                self.assertEqual(provider.main([*ARGS, '--park-server-avatar', mode]), 0)
                display.assert_called_once_with('/usr/bin/Xvfb', '/usr/bin/xauth', '/usr/bin/xdpyinfo')
                flags, = run.call_args.args
                self.assertEqual(flags[:3], ['--require-registry-snapshot', '--park-server-avatar', mode])
                self.assertEqual(flags[flags.index('--display') + 1], ':99')
                self.assertEqual(flags[flags.index('--software-vulkan-icd') + 1], str(icd))
                self.assertEqual(provider.os.environ['DISPLAY'], ':1')
                self.assertEqual(provider.os.environ['XAUTHORITY'], '/initial/auth')
                self.assertEqual(run.call_args.kwargs, {'display_hostname': 'x4-private-aabbccddeeff0011'})
                self.assertFalse(any(flag.startswith('--tls-') for flag in flags))

    def test_parking_rejects_hosting_options_before_starting_the_display(self):
        for extra in (['--tls-port', '0'], ['--tls-bind', '127.0.0.1'], ['--company-motion'],
                      ['--require-company-ship']):
            with (
                self.subTest(extra=extra),
                redirect_stderr(StringIO()) as reported,
                patch.object(provider, 'private_display') as display,
                patch.object(provider.supervisor, 'main') as run,
            ):
                with self.assertRaises(SystemExit) as refused:
                    provider.main([*ARGS, '--park-server-avatar', 'prepare', *extra])
                self.assertEqual(refused.exception.code, 2)
                self.assertIn('no TLS or Company hosting options', reported.getvalue())
                display.assert_not_called()
                run.assert_not_called()

    def test_company_pose_modes_forward_only_their_expected_options(self):
        cases = (
            (['--company-shuttle-pair', 'prepare'], ['--require-registry-snapshot', '--company-shuttle-pair', 'prepare']),
            (['--company-shuttle-pair', 'verify'], ['--require-registry-snapshot', '--company-shuttle-pair', 'verify']),
            (['--company-pose-trial', '--tls-port', '0'],
             ['--tls-port', '0', '--require-registry-snapshot', '--require-company-ship',
              '--company-motion', '--company-pose-trial']),
            (['--verify-company-pose'], ['--require-registry-snapshot', '--verify-company-pose']),
        )
        for options, expected in cases:
            with (
                self.subTest(options=options),
                patch.object(provider.os, 'geteuid', return_value=1000),
                patch.dict(provider.os.environ),
                patch.object(provider.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name),
                patch.object(provider, 'lavapipe_icd', return_value=Path(__file__)),
                patch.object(provider, 'private_display', return_value=nullcontext((':99', '/private/auth', 'x4-private-aabbccddeeff0011'))),
                patch.object(provider.supervisor, 'main', return_value=0) as run,
            ):
                self.assertEqual(provider.main([*ARGS, *options]), 0)
                flags, = run.call_args.args
                self.assertEqual(flags[:flags.index('--game')], expected)

    def test_company_purchase_opt_ins_reach_the_raw_supervisor_parser(self):
        tls = ['--tls-port', '0']
        for enabled, undock, fleet, sales in ((False, False, False, False), (True, False, False, False),
                                              (True, True, False, False), (True, False, True, False),
                                              (True, False, False, True)):
            with (
                self.subTest(enabled=enabled, undock=undock, fleet=fleet, sales=sales),
                patch.object(provider.os, 'geteuid', return_value=1000),
                patch.dict(provider.os.environ),
                patch.object(provider.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name),
                patch.object(provider, 'lavapipe_icd', return_value=Path(__file__)),
                patch.object(provider, 'private_display', return_value=nullcontext((':99', '/private/auth', 'x4-private-aabbccddeeff0011'))),
                patch.object(provider.supervisor, 'run', return_value={'success': True}) as run,
                redirect_stdout(StringIO()),
            ):
                options = ['--company-purchases'] if enabled else []
                if undock:
                    options.append('--company-undock-test')
                if fleet:
                    options.append('--company-fleet-orders')
                if sales:
                    options.append('--company-sales')
                self.assertEqual(provider.main([*ARGS, *tls, *options]), 0)
                args, = run.call_args.args
                self.assertIs(args.company_purchases, enabled)
                self.assertIs(args.company_sales, sales)
                self.assertIs(args.company_undock_test, undock)
                self.assertIs(args.company_fleet_orders, fleet)
                self.assertIs(args.require_company_ship, enabled)
                self.assertIs(args.require_registry_snapshot, enabled)
                self.assertFalse(args.company_motion)

    def test_undock_test_requires_purchase_and_tls_before_display(self):
        for options in (['--tls-port', '0'], ['--company-purchases']):
            with (
                self.subTest(options=options),
                redirect_stderr(StringIO()) as reported,
                patch.object(provider, 'private_display') as display,
                patch.object(provider.supervisor, 'main') as run,
            ):
                with self.assertRaises(SystemExit) as refused:
                    provider.main([*ARGS, '--company-undock-test', *options])
                self.assertEqual(refused.exception.code, 2)
                self.assertIn('requires --company-purchases and pinned TLS', reported.getvalue())
                display.assert_not_called()
                run.assert_not_called()

    def test_conflicting_company_pose_modes_and_verify_hosting_flags_fail_before_display(self):
        cases = (
            ['--company-shuttle-pair', 'prepare', '--tls-port', '0'],
            ['--company-shuttle-pair', 'verify', '--company-motion'],
            ['--company-shuttle-pair', 'prepare', '--company-trade-trial', 'buy'],
            ['--company-pose-trial', '--verify-company-pose'],
            ['--company-pose-trial', '--park-server-avatar', 'prepare'],
            ['--verify-company-pose', '--park-server-avatar', 'verify'],
            ['--verify-company-pose', '--tls-port', '0'],
            ['--verify-company-pose', '--company-motion'],
            ['--verify-company-pose', '--require-company-ship'],
        )
        for options in cases:
            with (
                self.subTest(options=options),
                redirect_stderr(StringIO()),
                patch.object(provider, 'private_display') as display,
                patch.object(provider.supervisor, 'main') as run,
            ):
                with self.assertRaises(SystemExit) as refused:
                    provider.main([*ARGS, *options])
                self.assertEqual(refused.exception.code, 2)
                display.assert_not_called()
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
