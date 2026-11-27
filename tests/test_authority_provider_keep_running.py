"""The provider forwards the opt-in dedicated-server flags without opening X4 or a display."""
from contextlib import nullcontext, redirect_stderr
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dev'))
import authority_provider_server as provider

ARGS = ['--game', '/private/game', '--store', '/private/source/checkpoints', '--out', '/private/run']


class ProviderKeepRunningTest(unittest.TestCase):
    def forwarded(self, options):
        with (
            patch.object(provider.sys, 'platform', 'linux'),
            patch.object(provider.os, 'geteuid', return_value=1000, create=True),
            patch.dict(provider.os.environ),
            patch.object(provider.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name),
            patch.object(provider, 'lavapipe_icd', return_value=Path(__file__)),
            patch.object(provider, 'private_display',
                         return_value=nullcontext((':99', '/private/auth', 'x4-private-aabbccddeeff0011'))),
            patch.object(provider.supervisor, 'main', return_value=0) as run,
        ):
            self.assertEqual(provider.main([*ARGS, *options]), 0)
        flags, = run.call_args.args
        return flags[:flags.index('--game')]

    def test_flags_reach_the_supervisor_only_when_requested(self):
        self.assertEqual(self.forwarded(['--persistent']), ['--persistent'])
        self.assertEqual(self.forwarded(['--persistent', '--keep-world-running']),
                         ['--persistent', '--keep-world-running'])
        self.assertEqual(self.forwarded(['--persistent', '--keep-world-running', '--empty-checkpoint-seconds', '900']),
                         ['--persistent', '--keep-world-running', '--empty-checkpoint-seconds', '900.0'])

    def test_host_admin_flags_are_forwarded_with_the_registry_snapshot(self):
        with (
            patch.object(provider.sys, 'platform', 'linux'),
            patch.object(provider.os, 'geteuid', return_value=1000, create=True),
            patch.dict(provider.os.environ),
            patch.object(provider.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name),
            patch.object(provider, 'lavapipe_icd', return_value=Path(__file__)),
            patch.object(provider, 'private_display',
                         return_value=nullcontext((':99', '/private/auth', 'x4-private-aabbccddeeff0011'))),
            patch.object(provider.supervisor, 'main', return_value=0) as run,
        ):
            self.assertEqual(provider.main([*ARGS, '--require-registry-snapshot', '--host-admin-port', '8765',
                                            '--host-admin-token-file', '/private/token']), 0)
        flags, = run.call_args.args
        start = flags.index('--host-admin-port')
        self.assertEqual(flags[start:start + 4],
                         ['--host-admin-port', '8765', '--host-admin-token-file', str(Path('/private/token'))])

    def test_invalid_combinations_are_refused_before_the_display_starts(self):
        for options, message in (
                (['--keep-world-running'], '--keep-world-running requires --persistent'),
                (['--persistent', '--empty-checkpoint-seconds', '900'], 'requires --keep-world-running'),
                (['--persistent', '--keep-world-running', '--empty-checkpoint-seconds', '10'], '60 to 86400'),
                (['--persistent', '--keep-world-running', '--empty-checkpoint-seconds', 'nan'], '60 to 86400'),
                (['--host-admin-port', '8765'], 'requires --require-registry-snapshot'),
                (['--require-registry-snapshot', '--host-admin-port', '80'], '1024 to 65535'),
                (['--host-admin-token-file', '/private/token'], 'requires --host-admin-port')):
            with (
                self.subTest(options=options),
                redirect_stderr(StringIO()) as reported,
                patch.object(provider, 'private_display') as display,
                patch.object(provider.supervisor, 'main') as run,
            ):
                with self.assertRaises(SystemExit) as refused:
                    provider.main([*ARGS, *options])
                self.assertEqual(refused.exception.code, 2)
                self.assertIn(message, reported.getvalue())
                display.assert_not_called()
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
