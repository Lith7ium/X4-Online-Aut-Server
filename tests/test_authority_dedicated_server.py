"""Dedicated-world launcher and its systemd unit, without starting X4, Steam or systemd."""
import configparser
from datetime import datetime, timezone
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dev'))
import authority_dedicated_server as launcher

NOW = datetime(2026, 11, 28, 9, 30, 0, tzinfo=timezone.utc)


def clean_result(**changes):
    result = dict(supervisor_version=1, success=True, final_world_state='paused', owned_processes_remaining=[],
                  final_checkpoint_id='c' * 32, final_checkpoint_sha256='d' * 64)
    result.update(changes)
    return result


def make_run(runs, name, result=None, *, confirmed=True):
    run = runs / name
    (run / 'checkpoints').mkdir(parents=True)
    if confirmed:
        (run / 'checkpoints/CONFIRMED').write_text('x\n')
    if result is not None:
        (run / 'result.json').write_text(json.dumps(result))
    return run


class RestartSourceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.runs = Path(folder.name)
        self.initial = self.runs.parent / 'initial-checkpoints'

    def test_without_runs_the_initial_store_is_used(self):
        self.assertEqual(launcher.restart_source(self.runs, self.initial), (self.initial, []))

    def test_newest_clean_run_wins_and_unclean_newer_runs_are_reported(self):
        make_run(self.runs, 'run-20261127T080000Z-000', clean_result())
        newest_clean = make_run(self.runs, 'run-20261127T090000Z-000', clean_result())
        make_run(self.runs, 'run-20261127T100000Z-000', None)  # crashed: no result.json
        make_run(self.runs, 'run-20261127T110000Z-000', clean_result(success=False, error='X4 exited'))
        make_run(self.runs, 'run-20261127T120000Z-000', clean_result(final_checkpoint_id=None))
        make_run(self.runs, 'run-20261127T130000Z-000', clean_result(final_world_state='running'))
        make_run(self.runs, 'run-20261127T140000Z-000', clean_result(cleanup_errors=['x']))
        make_run(self.runs, 'run-20261127T150000Z-000', clean_result(), confirmed=False)
        (self.runs / 'run-20261127T160000Z-000.launch.json').write_text('{}')
        (self.runs / 'notes').mkdir()
        store, skipped = launcher.restart_source(self.runs, self.initial)
        self.assertEqual(store, newest_clean / 'checkpoints')
        self.assertEqual(skipped, [
            ('run-20261127T150000Z-000', 'no confirmed checkpoint store'),
            ('run-20261127T140000Z-000', 'not a clean paused stop'),
            ('run-20261127T130000Z-000', 'not a clean paused stop'),
            ('run-20261127T120000Z-000', 'no final checkpoint'),
            ('run-20261127T110000Z-000', 'not a clean paused stop'),
            ('run-20261127T100000Z-000', 'no result.json (crashed or still running)'),
        ])

    def test_malformed_results_are_skipped(self):
        run = make_run(self.runs, 'run-20261127T090000Z-000')
        for text, reason in (('{', 'unreadable result.json'), ('[]', 'unsupported result.json'),
                             ('{"supervisor_version": NaN}', 'unreadable result.json'),
                             (json.dumps(clean_result(supervisor_version=2)), 'unsupported result.json')):
            with self.subTest(text=text):
                (run / 'result.json').write_text(text)
                self.assertEqual(launcher.clean_stop(run), reason)

    def test_run_names_are_fresh_and_ordered(self):
        first = launcher.next_run(self.runs, NOW)
        self.assertEqual(first.name, 'run-20261128T093000Z-000')
        first.mkdir()
        self.assertEqual(launcher.next_run(self.runs, NOW).name, 'run-20261128T093000Z-001')


class LauncherTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / '.local/source-run/checkpoints').mkdir(parents=True)
        self.initial = self.root / '.local/source-run/checkpoints'
        self.runs = self.root / '.local/dedicated'

    def argv(self, *extra):
        return ['--game', '/games/X4 Foundations', '--initial-store', str(self.initial),
                '--runs', str(self.runs), *extra]

    def launch(self, argv, *, steam=True):
        calls = []
        with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
            code = launcher.main(argv, root=self.root, now=NOW, execv=lambda *a: calls.append(a),
                                 steam_wait=lambda: steam)
        return code, calls, out.getvalue(), err.getvalue()

    def test_first_start_uses_the_initial_store_and_owns_the_keep_running_flags(self):
        code, calls, out, _ = self.launch(self.argv('--', '--base-game-only', '--tls-port', '0'))
        self.assertEqual(code, 1)  # the test double returns instead of replacing the process
        (program, command), = calls
        self.assertEqual(program, sys.executable)
        self.assertEqual(command[1], str(launcher.PROVIDER))
        self.assertEqual(command[2:5], ['--base-game-only', '--tls-port', '0'])
        run = self.runs.resolve() / 'run-20261128T093000Z-000'
        self.assertEqual(command[command.index('--store') + 1], str(self.initial.resolve()))
        self.assertEqual(command[command.index('--out') + 1], str(run))
        self.assertEqual(command[command.index('--game') + 1], str(Path('/games/X4 Foundations')))
        self.assertIn('--persistent', command)
        self.assertIn('--keep-world-running', command)
        self.assertEqual(command[command.index('--empty-checkpoint-seconds') + 1], '900.0')
        self.assertFalse(run.exists())  # the supervisor creates it
        record = json.loads((self.runs / (run.name + '.launch.json')).read_text())
        self.assertTrue(record['source_is_initial'])
        self.assertIn('"source_is_initial": true', out)

    def test_restart_uses_the_newest_clean_run(self):
        self.runs.mkdir(parents=True)
        clean = make_run(self.runs, 'run-20261127T090000Z-000', clean_result())
        code, calls, _, _ = self.launch(self.argv())
        (_, command), = calls
        self.assertEqual(command[command.index('--store') + 1], str((clean / 'checkpoints').resolve()))

    def test_missing_steam_is_a_temporary_failure_without_starting_the_provider(self):
        code, calls, _, err = self.launch(self.argv(), steam=False)
        self.assertEqual((code, calls), (launcher.EX_TEMPFAIL, []))
        self.assertIn('Steam client', err)
        self.assertEqual(list(self.runs.glob('*.launch.json')), [])

    def test_dry_run_starts_nothing(self):
        code, calls, out, _ = self.launch(self.argv('--dry-run'))
        self.assertEqual((code, calls), (0, []))
        self.assertIn('--keep-world-running', out)

    def test_paths_outside_local_and_owned_provider_flags_are_refused(self):
        outside = self.root / 'elsewhere'
        for argv in (['--game', '/g', '--initial-store', str(self.initial), '--runs', str(outside)],
                     ['--game', '/g', '--initial-store', str(outside), '--runs', str(self.runs)],
                     ['--game', '/g', '--initial-store', str(self.root / '.local'), '--runs', str(self.runs)],
                     self.argv('--', '--store', '/other'),
                     self.argv('--', '--timeout=10'),
                     self.argv('--', '--persistent')):
            with self.subTest(argv=argv):
                code, calls, _, err = self.launch(argv)
                self.assertEqual((code, calls), (2, []))
                self.assertIn('refused', err)

    def test_invalid_numbers_are_refused_by_the_parser(self):
        for extra in (['--empty-checkpoint-seconds', '30'], ['--empty-checkpoint-seconds', 'nan'],
                      ['--startup-timeout', '0'], ['--steam-wait-seconds', '-1']):
            with self.subTest(extra=extra), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                launcher.main(self.argv(*extra), root=self.root, now=NOW, execv=self.fail)


class SteamCheckTests(unittest.TestCase):
    def test_only_a_steam_process_of_the_same_user_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            home, proc = Path(folder, 'home'), Path(folder, 'proc')
            (home / '.steam').mkdir(parents=True)
            self.assertFalse(launcher.steam_running(home, 1000, proc))
            (home / '.steam/steam.pid').write_text('4242\n')
            (proc / '4242').mkdir(parents=True)
            for status, expected in (('Name:\tsteam\nUid:\t1000\t1000\t1000\t1000\n', True),
                                     ('Name:\tsteam\nUid:\t1001\t1001\t1001\t1001\n', False),
                                     ('Name:\tbash\nUid:\t1000\t1000\t1000\t1000\n', False)):
                with self.subTest(status=status):
                    (proc / '4242/status').write_text(status)
                    self.assertIs(launcher.steam_running(home, 1000, proc), expected)

    def test_wait_gives_up_at_its_deadline(self):
        now = [0.0]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(launcher.wait_for_steam(12, home=Path(folder), uid=1000,
                                                     clock=lambda: now[0], sleep=sleep))
        self.assertEqual(slept, [5, 5, 5])


class UnitFileTests(unittest.TestCase):
    def test_unit_stops_only_the_supervisor_and_restarts_on_failure(self):
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.optionxform = str
        unit.read(ROOT / 'tools/systemd/x4-authority-server.service', encoding='utf-8')
        service = unit['Service']
        self.assertEqual(service['KillMode'], 'mixed')
        self.assertEqual(service['Restart'], 'on-failure')
        self.assertGreaterEqual(int(service['TimeoutStopSec']), 300)
        self.assertNotIn('User', service)
        self.assertIn('tools/dev/authority_dedicated_server.py', service['ExecStart'])
        self.assertIn('${X4_GAME}', service['ExecStart'])
        self.assertEqual(unit['Install']['WantedBy'], 'default.target')
        for name in ('Wants', 'After'):
            self.assertIn('x4-authority-steam.service', unit['Unit'][name].split())
        example = (ROOT / 'tools/systemd/x4-authority-server.env.example').read_text(encoding='utf-8')
        for name in ('X4_GAME=', 'X4_INITIAL_STORE=', 'X4_RUNS=', 'X4_LAUNCHER_OPTIONS='):
            self.assertIn(name, example)


    def test_steam_unit_runs_a_headless_client_and_restarts_it(self):
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.optionxform = str
        unit.read(ROOT / 'tools/systemd/x4-authority-steam.service', encoding='utf-8')
        service = unit['Service']
        self.assertTrue(service['ExecStart'].startswith('/usr/bin/xvfb-run '))
        self.assertIn('/usr/bin/steam $STEAM_OPTIONS', service['ExecStart'])
        self.assertEqual(service['Restart'], 'on-failure')
        self.assertTrue(service['EnvironmentFile'].startswith('-'))  # the settings file is optional
        self.assertNotIn('User', service)
        self.assertEqual(unit['Install']['WantedBy'], 'default.target')


class PruneTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.initial = self.root / '.local/source-run/checkpoints'
        self.initial.mkdir(parents=True)
        self.runs = (self.root / '.local/dedicated').resolve()
        self.runs.mkdir(parents=True)

    def name(self, hour):
        return f'run-20261127T{hour:02d}0000Z-000'

    def test_only_runs_older_than_the_source_beyond_the_newest_n_are_prunable(self):
        for hour in range(1, 7):
            make_run(self.runs, self.name(hour), clean_result() if hour <= 4 else None)
        source = self.runs / self.name(4) / 'checkpoints'

        def prunable(keep):
            return [run.name for run in launcher.prunable_runs(self.runs, source, keep)]

        self.assertEqual(prunable(2), [self.name(1), self.name(2), self.name(3)])
        self.assertEqual(prunable(5), [self.name(1)])
        self.assertEqual(prunable(6), [])
        self.assertEqual(launcher.prunable_runs(self.runs, self.initial, 1), [])

    def test_remove_run_deletes_the_run_and_its_record_but_not_link_targets(self):
        run = make_run(self.runs, self.name(1), clean_result())
        (self.runs / (run.name + '.launch.json')).write_text('{}')
        outside = self.root / 'outside.txt'
        outside.write_text('keep')
        nested = run / 'case/home'
        nested.mkdir(parents=True)
        try:
            (nested / 'link').symlink_to(outside)
        except OSError:
            pass  # symlinks need extra rights on Windows
        if sys.platform != 'win32':
            nested.chmod(0o500)  # saved cases can hold read-only directories
        launcher.remove_run(run)
        self.assertFalse(run.exists())
        self.assertFalse((self.runs / (run.name + '.launch.json')).exists())
        self.assertEqual(outside.read_text(), 'keep')

    def launch(self, *extra, clear_lock=lambda: False):
        argv = ['--game', '/g', '--initial-store', str(self.initial), '--runs', str(self.runs), *extra]
        calls = []
        with redirect_stdout(StringIO()) as out, redirect_stderr(StringIO()) as err:
            code = launcher.main(argv, root=self.root, now=NOW, execv=lambda *a: calls.append(a),
                                 steam_wait=lambda: True, clear_lock=clear_lock)
        return code, calls, out.getvalue(), err.getvalue()

    def test_start_prunes_old_clean_runs_and_keeps_crashed_newer_ones(self):
        for hour in (1, 2, 3):
            make_run(self.runs, self.name(hour), clean_result())
        make_run(self.runs, self.name(4), None)  # crashed after the last clean stop
        code, calls, out, _ = self.launch('--dry-run', '--keep-runs', '1')
        self.assertEqual((code, calls), (0, []))
        preview = json.loads(out[:out.rindex('}') + 1])
        self.assertEqual(preview['would_prune_runs'], [self.name(1), self.name(2)])
        self.assertTrue((self.runs / self.name(1)).exists())
        code, calls, _, _ = self.launch('--keep-runs', '1')
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(p.name for p in self.runs.iterdir() if p.is_dir()), [self.name(3), self.name(4)])
        record = json.loads(next(self.runs.glob('*.launch.json')).read_text())
        self.assertEqual(record['pruned_runs'], [self.name(1), self.name(2)])

    def test_an_unsafe_lock_stops_the_start_before_anything_is_pruned(self):
        for hour in (1, 2):
            make_run(self.runs, self.name(hour), clean_result())

        def refuse():
            raise ValueError('X4 is still running (PID 7)')

        code, calls, _, err = self.launch('--keep-runs', '1', '--clear-stale-x4-lock', clear_lock=refuse)
        self.assertEqual((code, calls), (launcher.EX_TEMPFAIL, []))
        self.assertIn('X4 is still running', err)
        self.assertTrue((self.runs / self.name(1)).exists())

    def test_keep_runs_is_bounded(self):
        for value in ('0', '1001'):
            with self.subTest(value=value), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                launcher.parse(['--game', '/g', '--initial-store', '/s', '--runs', '/r', '--keep-runs', value])


class StaleLockTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lock = Path(folder.name, 'EGOSOFT_X4.lock')
        self.proc = Path(folder.name, 'proc')
        self.proc.mkdir()

    def process(self, pid, name, uid):
        (self.proc / str(pid)).mkdir()
        (self.proc / str(pid) / 'status').write_text(f'Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n')

    def clear(self, uid):
        return launcher.clear_stale_x4_lock(uid, lock=self.lock, proc=self.proc)

    def test_missing_lock_needs_nothing(self):
        self.assertFalse(self.clear(1000))

    def test_stale_lock_of_this_user_is_removed_when_no_x4_runs(self):
        self.lock.touch()
        uid = self.lock.lstat().st_uid
        self.process(10, 'steam', uid)
        self.process(11, 'X4', uid + 1)  # another user's game does not hold this user's lock
        self.assertTrue(self.clear(uid))
        self.assertFalse(self.lock.exists())

    def test_lock_is_kept_while_x4_runs_or_when_it_is_not_a_plain_empty_file(self):
        self.lock.touch()
        uid = self.lock.lstat().st_uid
        self.process(12, 'X4', uid)
        with self.assertRaisesRegex(ValueError, 'PID 12'):
            self.clear(uid)
        self.assertTrue(self.lock.exists())
        (self.proc / '12/status').unlink()
        (self.proc / '12').rmdir()
        with self.assertRaisesRegex(ValueError, 'remove it by hand'):
            self.clear(uid + 1)
        self.lock.write_text('x')
        with self.assertRaisesRegex(ValueError, 'remove it by hand'):
            self.clear(uid)
        self.assertTrue(self.lock.exists())


if __name__ == '__main__':
    unittest.main()
