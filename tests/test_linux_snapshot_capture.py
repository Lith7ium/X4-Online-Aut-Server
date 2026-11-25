"""Offline checks for the disposable capture runner; these do not launch X4."""
import gzip
from contextlib import ExitStack, nullcontext
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('linux_snapshot_capture', REPO / 'tools/dev/linux_snapshot_capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


@unittest.skipUnless(sys.platform == 'linux', 'Linux-only capture helper')
class CaptureTests(unittest.TestCase):
    def test_save_requires_complete_gzip_and_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / 'save.gz'
            save.write_bytes(gzip.compress(b'<savegame><universe/></savegame>'))
            capture.validate_save(save)
            good = save.read_bytes()
            for bad in (good[:-4], gzip.compress(b'<savegame>'), gzip.compress(b'<other/>')):
                save.write_bytes(bad)
                with self.assertRaises((OSError, EOFError, ValueError, capture.ET.ParseError)):
                    capture.validate_save(save)

    def test_output_is_fresh_and_confined(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / '.local'
            local.mkdir()
            with mock.patch.object(capture, 'REPO', root):
                self.assertEqual(capture.output_path(local / 'new'), local / 'new')
                with self.assertRaises(ValueError):
                    capture.output_path(local)
                with self.assertRaises(ValueError):
                    capture.output_path(root / 'elsewhere')
                (local / 'link').symlink_to('/tmp')
                with self.assertRaises(ValueError):
                    capture.output_path(local / 'link/new')

    def test_command_uses_current_user_and_private_paths(self):
        case = Path('/tmp/private-case')
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory)
            import pwd
            user = pwd.getpwuid(os.geteuid()).pw_name
            cmd = capture.command_for(case, game, [], case / 'x4.lock', ':0', Path('/tmp/auth'), user)
        self.assertEqual(cmd[0], 'bwrap')
        self.assertNotIn('runuser', cmd)
        self.assertIn('--ro-bind', cmd)
        self.assertIn('--clearenv', cmd)
        self.assertIn(str(case / 'game/extensions'), cmd)
        self.assertIn('/tmp/EGOSOFT_X4.lock', cmd)
        self.assertIn(str(case / 'home'), cmd)
        self.assertIn('-personalfolderid', cmd)
        self.assertIn('45841', cmd)
        self.assertIn('-seed:276944566', cmd)
        self.assertNotIn('--share-net', cmd)

    def test_guard_refuses_root_before_any_output(self):
        args = mock.Mock()
        with mock.patch.object(capture.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(ValueError, 'unprivileged'):
                capture.capture(args)

    def test_group_scan_preserves_uid_group_start_filters_and_bad_entry_handling(self):
        with tempfile.TemporaryDirectory() as directory:
            entries = []
            for name, group, started in (('103', 77, 10), ('101', 77, 11),
                                         ('102', 78, 11), ('104', 77, 9),
                                         ('105', 77, 11), ('106', 77, 11),
                                         ('107suffix', 77, 11), ('not-a-pid', 77, 11)):
                path = Path(directory) / name
                path.mkdir()
                fields = ['S', '0', str(group)] + ['0'] * 16 + [str(started)]
                if name != '106':  # A disappearing entry has no readable stat.
                    text = 'bad stat' if name == '105' else name + ' (name with ) parentheses) ' + ' '.join(fields)
                    (path / 'stat').write_text(text)
                entries.append(SimpleNamespace(name=name, path=str(path)))
            actual_stat = os.stat
            observed = []
            def stat(path):
                observed.append(Path(path).name)
                value = actual_stat(path)
                return SimpleNamespace(st_uid=value.st_uid + (Path(path).name == '103'))
            with mock.patch.object(capture.os, 'scandir', return_value=nullcontext(entries)), \
                 mock.patch.object(capture.os, 'stat', side_effect=stat):
                self.assertEqual(capture.owned_group_members(77, 10), [101])
            self.assertEqual(observed, ['103', '101', '102', '104', '107suffix'])

    def test_group_scan_preserves_unreadable_proc_result(self):
        with mock.patch.object(capture.os, 'scandir', side_effect=PermissionError):
            self.assertEqual(capture.owned_group_members(77, 10), [])

    def test_cleanup_signals_only_launched_group(self):
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = None
        with mock.patch.object(capture.os, 'killpg') as kill, \
             mock.patch.object(capture.live, 'start_ticks', return_value=10), \
             mock.patch.object(capture, 'owned_group_members', return_value=[]):
            capture.stop_owned(proc, 10)
        kill.assert_called_once_with(12345, capture.signal.SIGTERM)
        proc.wait.assert_called_once_with(timeout=15)
        proc.poll.return_value = 0
        with mock.patch.object(capture.os, 'killpg') as kill, \
             mock.patch.object(capture, 'owned_group_members', return_value=[]):
            capture.stop_owned(proc, 10)
        kill.assert_not_called()

    def test_reused_pid_is_not_signalled(self):
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = None
        with mock.patch.object(capture.os, 'killpg') as kill, \
             mock.patch.object(capture.live, 'start_ticks', return_value=11), \
             mock.patch.object(capture, 'owned_group_members', return_value=[]):
            capture.stop_owned(proc, 10)
        kill.assert_not_called()

    def test_cleanup_waits_for_group_to_exit(self):
        proc = mock.Mock(pid=12345)
        proc.poll.return_value = 0
        with mock.patch.object(capture, 'owned_group_members', side_effect=[[12346], []]), \
             mock.patch.object(capture.time, 'sleep') as sleep:
            self.assertEqual(capture.stop_owned(proc, 10), [])
        sleep.assert_called_once()

    def test_failed_save_cleans_up_and_preserves_error(self):
        import pwd
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / '.local'
            local.mkdir()
            game = root / 'game'
            (game / 'extensions').mkdir(parents=True)
            auth = root / 'auth'
            auth.touch()
            out = local / 'run'
            profile = out / 'case/home/.config/EgoSoft/X4/45841'
            profile.mkdir(parents=True)
            (profile / 'save').mkdir()
            (profile / 'save' / capture.SLOT).write_bytes(b'not gzip')
            (profile / 'engine-output.log').write_text('NATIVE_SNAPSHOT SAVED')
            # capture requires a fresh output; staging creates this fixture instead.
            import shutil
            shutil.move(out, root / 'staged')
            def stage(destination, game_path, home):
                shutil.move(root / 'staged', destination)
                return (destination / 'case',
                        destination / 'case/home/.config/EgoSoft/X4/45841', [], destination / 'case/x4.lock')
            user = pwd.getpwuid(os.geteuid())
            steam = {'pid': 42, 'start': 1, 'uid': user.pw_uid}
            proc = mock.Mock(pid=12345)
            proc.poll.return_value = None
            args = mock.Mock(out=out, game=game, display=':0', timeout=5, seed=276944566)
            # The runner creates the output before calling staging.
            def stage_existing(destination, game_path, home):
                destination.rmdir()
                return stage(destination, game_path, home)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(capture, 'REPO', root))
                stack.enter_context(mock.patch.object(capture, 'LOCK_TARGET', root / 'lock'))
                stack.enter_context(mock.patch.dict(os.environ, {'XAUTHORITY': str(auth)}))
                stack.enter_context(mock.patch.object(capture.live, 'sha', return_value=capture.live.EXE_SHA))
                stack.enter_context(mock.patch.object(capture.live, 'x4_executables', return_value=[]))
                stack.enter_context(mock.patch.object(capture.live, 'steam_identity', return_value=steam))
                stack.enter_context(mock.patch.object(capture.live, 'start_ticks', return_value=10))
                stack.enter_context(mock.patch.object(capture.shutil, 'which', return_value='/usr/bin/bwrap'))
                stack.enter_context(mock.patch.object(capture, 'prepare_case', side_effect=stage_existing))
                stack.enter_context(mock.patch.object(capture, 'command_for', return_value=['bwrap']))
                stack.enter_context(mock.patch.object(capture.subprocess, 'Popen', return_value=proc))
                stop = stack.enter_context(mock.patch.object(capture, 'stop_owned', return_value=[]))
                result = capture.capture(args)
            self.assertFalse(result['success'])
            self.assertIn('gzip', result['error'].lower())
            self.assertFalse((out / capture.SLOT).exists())
            self.assertFalse((root / 'lock').exists())
            self.assertTrue((out / 'result.json').is_file())
            stop.assert_called_once_with(proc, 10)

    def test_staging_creates_only_allowed_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / 'game'
            (game / 'extensions/ego_dlc_test').mkdir(parents=True)
            (game / 'extensions/untrusted_mod').mkdir()
            home = root / 'steam-user'
            (home / '.steam').mkdir(parents=True)
            out = root / 'output'
            out.mkdir()
            case, profile, binds, _ = capture.prepare_case(out, game, home)
            self.assertEqual(sorted(p.name for p in (case / 'extensions').iterdir()),
                             ['ego_dlc_test', 'x4_lan_lab'])
            self.assertEqual(len(binds), 1)
            self.assertFalse((profile / 'save' / capture.SLOT).exists())
            config = (case / 'extensions/x4_lan_lab/ui/config.lua').read_text()
            self.assertIn('native_gate_snapshot_fixed = false', config)
            self.assertIn('x4ls_autostart = "custom_creative"', config)
            default_graphics = (profile / 'config.xml').read_text()
            self.assertIn('<chromaticaberration>false</chromaticaberration>', default_graphics)
            self.assertNotIn('<gfxquality>', default_graphics)
            _, cpu_profile, _, _ = capture.prepare_case(root / 'cpu-provider', game, home, software_rendering=True)
            graphics = (cpu_profile / 'config.xml').read_text()
            self.assertEqual(graphics, capture.config_xml(640, 360, fast=True))
            self.assertNotIn('<lodfactor>', graphics)
            self.assertNotIn('<effectdistance>', graphics)


if __name__ == '__main__':
    unittest.main()
