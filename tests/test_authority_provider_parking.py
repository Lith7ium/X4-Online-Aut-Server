"""Exercise the GPU-free provider wrapper without opening X4 or a display."""
from contextlib import nullcontext, redirect_stderr
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dev'))
import authority_provider_server as provider

ARGS = ['--game', '/private/game', '--store', '/private/source/checkpoints',
        '--out', '/private/prep']


class ProviderParkingTest(unittest.TestCase):
    def test_both_modes_keep_the_existing_software_rendered_path(self):
        icd = Path(__file__).resolve()
        for mode in ('prepare', 'verify'):
            with (
                self.subTest(mode=mode),
                patch.object(provider.os, 'geteuid', return_value=1000),
                patch.dict(provider.os.environ),
                patch.object(provider.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name),
                patch.object(provider, 'lavapipe_icd', return_value=icd),
                patch.object(provider, 'private_display', return_value=nullcontext((':99', '/private/auth'))) as display,
                patch.object(provider.supervisor, 'main', return_value=0) as run,
            ):
                self.assertEqual(provider.main([*ARGS, '--park-server-avatar', mode]), 0)
                display.assert_called_once_with('/usr/bin/Xvfb', '/usr/bin/xauth', '/usr/bin/xdpyinfo')
                flags, = run.call_args.args
                self.assertEqual(flags[:3], ['--require-registry-snapshot', '--park-server-avatar', mode])
                self.assertEqual(flags[flags.index('--display') + 1], ':99')
                self.assertEqual(flags[flags.index('--software-vulkan-icd') + 1], str(icd))
                self.assertEqual(provider.os.environ['XAUTHORITY'], '/private/auth')
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


if __name__ == '__main__':
    unittest.main()
