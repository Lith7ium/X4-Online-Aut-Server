"""Owner behavior with fake Win32 calls; no Windows or X4 process is started."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import warnings
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import authority.windows_x4_process as subject
NATIVE_JOB = subject._Win32Job


class FakeJob:
    def __init__(self):
        self.calls = []
        self.live = True
        self.in_owned_job = True
        self.pid = 123
        self.stop_result = True

    def start(self, command, cwd, environment):
        self.calls.append(('start', command, cwd, environment))
        return 123

    def running(self):
        return self.live

    def owns_running_pid(self, pid):
        self.calls.append(('owns_running_pid', pid))
        return self.live and self.in_owned_job and pid == self.pid

    def stop(self, timeout):
        self.calls.append(('stop', timeout))
        return self.stop_result

    def wait_cleanup(self, timeout):
        self.calls.append(('wait_cleanup', timeout))
        return self.stop_result

    def close(self):
        self.calls.append(('close',))


class WindowsX4ProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.view = Path(self.directory.name)
        self.job = FakeJob()
        self.patches = [patch.object(subject, '_windows_runtime', return_value=True),
                        patch.object(subject, '_Win32Job', return_value=self.job),
                        patch.object(subject.game, 'verify_exe'),
                        patch.object(subject.steam, 'status', return_value=(True, 'ready')),
                        patch.object(subject.steam, 'client_environment',
                                     return_value={'SteamAppId': '392160', 'SteamGameId': '392160'})]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_direct_start_retains_one_owner_and_stops_only_its_job(self):
        owner = subject.WindowsX4Process(enabled=True)
        self.assertEqual(owner.start(self.view, profile_id=45841, guard=lambda: True), 123)
        self.assertTrue(owner.is_running())
        _, command, cwd, environment = self.job.calls[0]
        self.assertEqual(command[0], str(self.view / 'X4.exe'))
        self.assertEqual(command[1:3], ['-personalfolderid', '45841'])
        self.assertEqual(cwd, str(self.view))
        self.assertEqual(environment['SteamAppId'], '392160')
        self.assertEqual(environment['SteamGameId'], '392160')
        self.assertTrue(owner.stop(timeout=2))
        self.assertTrue(owner.cleanup_confirmed)
        self.assertEqual(self.job.calls[-2:], [('stop', 2), ('close',)])
        with self.assertRaises(ValueError):
            owner.stop()

    def test_owns_running_pid_requires_the_same_live_owned_process(self):
        owner = subject.WindowsX4Process(enabled=True)
        owner.start(self.view, profile_id=45841, guard=lambda: True)
        self.assertTrue(owner.owns_running_pid(123))
        self.assertEqual(self.job.calls[-1], ('owns_running_pid', 123))

        self.assertFalse(owner.owns_running_pid(124))
        self.assertEqual(self.job.calls[-1], ('owns_running_pid', 123))

        owner.pid = 124
        calls_before = list(self.job.calls)
        self.assertFalse(owner.owns_running_pid(123))
        self.assertEqual(self.job.calls, calls_before)

        owner.pid = 123
        self.job.live = False
        self.assertFalse(owner.owns_running_pid(123))
        self.job.live = True
        self.job.in_owned_job = False
        self.assertFalse(owner.owns_running_pid(123))
        owner.close()

    def test_owns_running_pid_rejects_invalid_pid_thread_and_closed_phase(self):
        owner = subject.WindowsX4Process(enabled=True)
        owner.start(self.view, profile_id=45841, guard=lambda: True)
        for pid in (0, -1, 0x100000000, True, 123.0, '123'):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                owner.owns_running_pid(pid)

        failures = []
        thread = threading.Thread(target=lambda: self._capture_error(
            failures, owner.owns_running_pid, 123))
        thread.start(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeError)
        self.assertTrue(owner.stop())
        with self.assertRaisesRegex(RuntimeError, 'not running'):
            owner.owns_running_pid(123)

    @staticmethod
    def _capture_error(errors, callback, *arguments):
        try:
            callback(*arguments)
        except BaseException as error:
            errors.append(error)

    def test_native_job_rejects_pid_not_matching_held_process_handle(self):
        native = NATIVE_JOB.__new__(NATIVE_JOB)
        native.closed = False
        native.job = 11
        native.info = subject._ProcessInfo()
        native.info.hProcess = 33
        native.get_process_id = lambda process: 124 if process == 33 else 0
        native.running = lambda: True
        job_queries = []

        def is_process_in_job(process, job, result):
            job_queries.append((process, job))
            result._obj.value = True
            return True

        native.in_job = is_process_in_job
        self.assertFalse(native.owns_running_pid(123))
        self.assertEqual(job_queries, [])

    def test_native_job_rejects_process_outside_owned_job_or_not_running(self):
        for mode in ('foreign_job', 'stopped'):
            with self.subTest(mode=mode):
                native = NATIVE_JOB.__new__(NATIVE_JOB)
                native.closed = False
                native.job = 11
                native.info = subject._ProcessInfo()
                native.info.hProcess = 33
                native.get_process_id = lambda process: 123
                native.running = lambda: mode != 'stopped'
                job_queries = []

                def is_process_in_job(process, job, result):
                    job_queries.append((process, job))
                    result._obj.value = mode != 'foreign_job'
                    return True

                native.in_job = is_process_in_job
                self.assertFalse(native.owns_running_pid(123))
                if mode == 'stopped':
                    self.assertEqual(job_queries, [])
                else:
                    self.assertEqual(job_queries, [(33, 11)])

    def test_native_job_rechecks_process_after_job_membership_query(self):
        cases = (
            ('still_owned', [123, 123], [True, True], True),
            ('exits_during_check', [123], [True, False], False),
            ('pid_changes_during_check', [123, 124], [True, True], False),
            ('invalid_retained_handle_pid', [0], [], OSError),
        )
        for name, process_ids, running_values, expected in cases:
            with self.subTest(name=name):
                native = NATIVE_JOB.__new__(NATIVE_JOB)
                native.closed = False
                native.job = 11
                native.info = subject._ProcessInfo()
                native.info.hProcess = 33
                process_id_reads = iter(process_ids)
                running_reads = iter(running_values)
                native.get_process_id = lambda process: next(process_id_reads)
                native.running = lambda: next(running_reads)
                job_queries = []

                def is_process_in_job(process, job, result):
                    job_queries.append((process, job))
                    result._obj.value = True
                    return True

                native.in_job = is_process_in_job
                if expected is OSError:
                    with patch.object(subject.ctypes, 'get_last_error', return_value=6, create=True):
                        with self.assertRaises(OSError):
                            native.owns_running_pid(123)
                else:
                    self.assertIs(native.owns_running_pid(123), expected)
                expected_queries = [] if expected is OSError else [(33, 11)]
                self.assertEqual(job_queries, expected_queries)

    def test_failed_or_incomplete_stop_never_claims_cleanup(self):
        owner = subject.WindowsX4Process(enabled=True)
        owner.start(self.view, profile_id=45841, guard=lambda: True)
        self.job.stop_result = False
        self.assertFalse(owner.stop())
        self.assertFalse(owner.cleanup_confirmed)
        self.assertNotIn(('close',), self.job.calls)
        self.job.stop_result = True
        self.assertTrue(owner.close(timeout=1))
        self.assertEqual(self.job.calls[-2:], [('wait_cleanup', 1), ('close',)])
        self.assertTrue(owner.cleanup_confirmed)

    def test_guard_failure_after_start_closes_job_and_fences_owner(self):
        outcomes = iter((True, True, False))
        owner = subject.WindowsX4Process(enabled=True)
        with self.assertRaisesRegex(RuntimeError, 'guard failed'):
            owner.start(self.view, profile_id=45841, guard=lambda: next(outcomes))
        self.assertEqual(self.job.calls[-1], ('close',))
        self.assertFalse(owner.cleanup_confirmed)
        with self.assertRaises(ValueError):
            owner.start(self.view, profile_id=45841, guard=lambda: True)

    def test_unavailable_steam_and_unsafe_profile_do_not_start_job(self):
        owner = subject.WindowsX4Process(enabled=True)
        for invalid in (45841.0, 45852, 99999):
            with self.subTest(profile_id=invalid), self.assertRaises(ValueError):
                owner.start(self.view, profile_id=invalid, guard=lambda: True)
        with patch.object(subject.steam, 'status', return_value=(False, 'not ready')):
            with self.assertRaisesRegex(RuntimeError, 'Steam'):
                owner.start(self.view, profile_id=45841, guard=lambda: True)
        self.assertEqual(self.job.calls, [])

    def test_direct_start_uses_reserved_company_profile_id(self):
        owner = subject.WindowsX4Process(enabled=True)
        self.assertEqual(owner.start(self.view, profile_id=45850, guard=lambda: True), 123)
        self.assertEqual(self.job.calls[0][1][1:3], ['-personalfolderid', '45850'])
        self.assertTrue(owner.close())

    def test_context_closes_owned_job_on_normal_exit_and_exception(self):
        for error in (False, True):
            with self.subTest(error=error):
                self.job = FakeJob()
                with patch.object(subject, '_Win32Job', return_value=self.job):
                    owner = subject.WindowsX4Process(enabled=True)
                    try:
                        with owner:
                            owner.start(self.view, profile_id=45841, guard=lambda: True)
                            if error:
                                raise RuntimeError('original failure')
                    except RuntimeError as exc:
                        self.assertTrue(error)
                        self.assertEqual(str(exc), 'original failure')
                    self.assertTrue(owner.cleanup_confirmed)
                    self.assertEqual(self.job.calls[-2:], [('stop', 10), ('close',)])
                    self.assertTrue(owner.close())

    def test_context_never_hides_original_error_or_claims_unconfirmed_cleanup(self):
        for error in (False, True):
            with self.subTest(error=error):
                self.job = FakeJob()
                self.job.stop_result = False
                with patch.object(subject, '_Win32Job', return_value=self.job):
                    owner = subject.WindowsX4Process(enabled=True)
                    with self.assertRaises(RuntimeError) as caught:
                        with owner:
                            owner.start(self.view, profile_id=45841, guard=lambda: True)
                            if error:
                                raise RuntimeError('original failure')
                    if error:
                        self.assertEqual(str(caught.exception), 'original failure')
                        notes = getattr(caught.exception, '__notes__', None)
                        detail = notes[0] if notes else caught.exception.x4_cleanup_warning
                        self.assertIn('cleanup could not be confirmed', detail)
                    else:
                        self.assertIn('cleanup could not be confirmed', str(caught.exception))
                    self.assertFalse(owner.cleanup_confirmed)
                    self.assertFalse(owner.close())
                    self.assertNotIn(('close',), self.job.calls)

    def test_existing_case_variants_of_steam_ids_are_replaced(self):
        with patch.object(subject.steam, 'client_environment', return_value={
                'STEAMAPPID': 'wrong', 'steamgameid': 'wrong', 'Other': 'kept'}):
            owner = subject.WindowsX4Process(enabled=True)
            owner.start(self.view, profile_id=45841, guard=lambda: True)
        environment = self.job.calls[0][3]
        self.assertEqual(environment, {'Other': 'kept', 'SteamAppId': '392160', 'SteamGameId': '392160'})
        owner.close()

    def test_fresh_context_starts_nothing_and_rejects_later_start(self):
        owner = subject.WindowsX4Process(enabled=True)
        with owner:
            pass
        self.assertEqual(self.job.calls, [])
        with self.assertRaises(ValueError):
            owner.start(self.view, profile_id=45841, guard=lambda: True)

    def test_failed_handle_close_remains_retryable_and_unconfirmed(self):
        owner = subject.WindowsX4Process(enabled=True)
        owner.start(self.view, profile_id=45841, guard=lambda: True)
        calls = []
        def close_once():
            calls.append('close')
            if len(calls) == 1:
                raise OSError('CloseHandle failed')
        self.job.close = close_once
        with self.assertRaisesRegex(OSError, 'CloseHandle'):
            owner.stop()
        self.assertFalse(owner.cleanup_confirmed)
        self.assertTrue(owner.close())
        self.assertTrue(owner.cleanup_confirmed)
        self.assertEqual(calls, ['close', 'close'])
        self.assertEqual([c for c in self.job.calls if c[0] == 'stop'], [('stop', 10)])

    def test_python310_exception_preserved_with_cleanup_warning(self):
        class LegacyError(Exception):
            add_note = None
        owner = subject.WindowsX4Process(enabled=True)
        self.job.stop_result = False
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter('always')
            with self.assertRaises(LegacyError) as caught:
                with owner:
                    owner.start(self.view, profile_id=45841, guard=lambda: True)
                    raise LegacyError('original failure')
        self.assertEqual(str(caught.exception), 'original failure')
        self.assertIn('cleanup could not be confirmed', caught.exception.x4_cleanup_warning)
        self.assertEqual(len(captured), 1)
        self.assertFalse(owner.cleanup_confirmed)

    def test_partial_close_retries_only_failed_native_handles(self):
        native = NATIVE_JOB.__new__(NATIVE_JOB)
        native.closed = False
        native.job = 11
        native.info = subject._ProcessInfo()
        native.info.hThread, native.info.hProcess = 22, 33
        closed = []
        def close_handle(handle):
            closed.append(handle)
            return handle != 22 or closed.count(22) > 1
        native.close_handle = close_handle
        with patch.object(subject.ctypes, 'get_last_error', return_value=6, create=True):
            with self.assertRaisesRegex(OSError, 'thread'):
                native.close()
            self.assertFalse(native.closed)
            self.assertIsNone(native.job)
            self.assertEqual(native.info.hThread, 22)
            self.assertIsNone(native.info.hProcess)
            native.close()
        self.assertTrue(native.closed)
        self.assertEqual(closed, [11, 22, 33, 22])


if __name__ == '__main__':
    unittest.main()
