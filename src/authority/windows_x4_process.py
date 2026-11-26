"""Experimental direct Windows X4 child in a kill-on-close job.

This owns a process, not a profile, save, game view, Steam client or session.
The caller must establish and guard those resources before and during use.
There is no admission, event observation, installer, or multiplayer integration.
The Win32 path has not yet been validated with native Windows X4.
"""
import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import warnings

from lockstep.launcher import game, profile, steam


def _record_cleanup_issue(error, message):
    """Annotate the original error without requiring Python 3.11."""
    add_note = getattr(error, 'add_note', None)
    if callable(add_note):
        try:
            add_note(message)
            return
        except BaseException:
            pass
    try:
        error.x4_cleanup_warning = message
    except BaseException:
        pass
    try:
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    except BaseException:
        pass


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]


class _BasicLimit(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD)]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', _BasicLimit), ('IoInfo', _IoCounters),
                ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR),
                ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR),
                *[(name, wintypes.DWORD) for name in ('dwX', 'dwY', 'dwXSize', 'dwYSize',
                  'dwXCountChars', 'dwYCountChars', 'dwFillAttribute', 'dwFlags')],
                ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                ('lpReserved2', ctypes.POINTER(ctypes.c_byte)), ('hStdInput', wintypes.HANDLE),
                ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [('StartupInfo', _StartupInfo), ('lpAttributeList', ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
                ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]


class _JobAccounting(ctypes.Structure):
    _fields_ = [('TotalUserTime', ctypes.c_longlong), ('TotalKernelTime', ctypes.c_longlong),
                ('ThisPeriodTotalUserTime', ctypes.c_longlong), ('ThisPeriodTotalKernelTime', ctypes.c_longlong),
                ('TotalPageFaultCount', wintypes.DWORD), ('TotalProcesses', wintypes.DWORD),
                ('ActiveProcesses', wintypes.DWORD), ('TotalTerminatedProcesses', wintypes.DWORD)]


class _Win32Job:
    """One native job and its directly-created suspended root process."""
    def __init__(self):
        if os.name != 'nt':
            raise ValueError('Windows required')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.job = None
        self.info = _ProcessInfo()
        self.closed = False
        self._configure()

    def _open_job(self):
        self.job = self.create_job(None, None)
        self._require(self.job, 'CreateJobObjectW')
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        self._require(self.set_job(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                      'SetInformationJobObject')

    def _api(self, name, result, *arguments):
        function = getattr(self.kernel, name)
        function.restype, function.argtypes = result, arguments
        return function

    def _configure(self):
        handle, dword, size = wintypes.HANDLE, wintypes.DWORD, ctypes.c_size_t
        self.create_job = self._api('CreateJobObjectW', handle, ctypes.c_void_p, wintypes.LPCWSTR)
        self.set_job = self._api('SetInformationJobObject', wintypes.BOOL, handle, ctypes.c_int, ctypes.c_void_p, dword)
        self.initialize = self._api('InitializeProcThreadAttributeList', wintypes.BOOL, ctypes.c_void_p, dword, dword, ctypes.POINTER(size))
        self.update = self._api('UpdateProcThreadAttribute', wintypes.BOOL, ctypes.c_void_p, dword, size, ctypes.c_void_p, size, ctypes.c_void_p, ctypes.c_void_p)
        self.delete = self._api('DeleteProcThreadAttributeList', None, ctypes.c_void_p)
        self.create = self._api('CreateProcessW', wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPWSTR,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, dword, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInfo))
        self.in_job = self._api('IsProcessInJob', wintypes.BOOL, handle, handle, ctypes.POINTER(wintypes.BOOL))
        self.get_process_id = self._api('GetProcessId', dword, handle)
        self.resume = self._api('ResumeThread', dword, handle)
        self.wait = self._api('WaitForSingleObject', dword, handle, dword)
        self.get_exit_code = self._api('GetExitCodeProcess', wintypes.BOOL, handle, ctypes.POINTER(dword))
        self.terminate = self._api('TerminateJobObject', wintypes.BOOL, handle, wintypes.UINT)
        self.query = self._api('QueryInformationJobObject', wintypes.BOOL, handle, ctypes.c_int,
                               ctypes.c_void_p, dword, ctypes.POINTER(dword))
        self.close_handle = self._api('CloseHandle', wintypes.BOOL, handle)

    @staticmethod
    def _require(result, action):
        if not result:
            raise OSError(ctypes.get_last_error(), action)

    def start(self, command, cwd, environment):
        self._open_job()
        size = ctypes.c_size_t()
        self.initialize(None, 1, 0, ctypes.byref(size))
        if not size.value:
            raise OSError(ctypes.get_last_error(), 'attribute list size')
        memory = ctypes.create_string_buffer(size.value)
        attributes = ctypes.cast(memory, ctypes.c_void_p)
        self._require(self.initialize(attributes, 1, 0, ctypes.byref(size)), 'InitializeProcThreadAttributeList')
        try:
            jobs = (wintypes.HANDLE * 1)(self.job)
            self._require(self.update(attributes, 0, 0x0002000d, ctypes.cast(jobs, ctypes.c_void_p),
                                      ctypes.sizeof(jobs), None, None), 'UpdateProcThreadAttribute')
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.lpAttributeList = attributes
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            # Windows requires a sorted double-NUL terminated Unicode environment block.
            block = '\0'.join(key + '=' + value for key, value in sorted(environment.items(), key=lambda pair: pair[0].upper())) + '\0\0'
            env = ctypes.create_unicode_buffer(block)
            flags = 0x00080000 | 0x00000004 | 0x00000400  # EXTENDED, SUSPENDED, UNICODE_ENVIRONMENT
            self._require(self.create(command[0], command_line, None, None, False, flags,
                                      ctypes.cast(env, ctypes.c_void_p), cwd,
                                      ctypes.byref(startup), ctypes.byref(self.info)), 'CreateProcessW')
        finally:
            self.delete(attributes)
        if not self.info.hProcess or not self.info.hThread or not self.info.dwProcessId:
            raise RuntimeError('X4 process handles unavailable')
        member = wintypes.BOOL()
        self._require(self.in_job(self.info.hProcess, self.job, ctypes.byref(member)), 'IsProcessInJob')
        if not member.value:
            raise RuntimeError('X4 is not in the owned job')
        if self.resume(self.info.hThread) != 1:
            raise RuntimeError('X4 initial suspend count differs')
        return int(self.info.dwProcessId)

    def exit_code(self):
        if self.running():
            return None
        code = wintypes.DWORD()
        self._require(self.get_exit_code(self.info.hProcess, ctypes.byref(code)), 'GetExitCodeProcess')
        return code.value

    def running(self):
        result = self.wait(self.info.hProcess, 0)
        if result == 0:
            return False
        if result == 258:  # WAIT_TIMEOUT
            return True
        raise OSError(ctypes.get_last_error(), 'WaitForSingleObject')

    def owns_running_pid(self, pid):
        """Check a PID against the retained root handle and this specific job."""
        if self.closed or not self.info.hProcess or not self.job:
            return False
        process_handle = self.info.hProcess
        actual_pid = self.get_process_id(process_handle)
        self._require(actual_pid, 'GetProcessId')
        if actual_pid != pid or not self.running():
            return False
        member = wintypes.BOOL()
        self._require(self.in_job(process_handle, self.job, ctypes.byref(member)), 'IsProcessInJob')
        if not member.value or not self.running():
            return False
        actual_pid = self.get_process_id(process_handle)
        self._require(actual_pid, 'GetProcessId')
        return actual_pid == pid

    def stop(self, timeout):
        self._require(self.terminate(self.job, 1), 'TerminateJobObject')
        return self.wait_cleanup(timeout)

    def wait_cleanup(self, timeout):
        deadline = time.monotonic() + timeout
        result = self.wait(self.info.hProcess, int(timeout * 1000))
        if result == 258:
            return False
        if result != 0:
            raise OSError(ctypes.get_last_error(), 'WaitForSingleObject')
        while True:
            accounting = _JobAccounting()
            self._require(self.query(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None),
                          'QueryInformationJobObject')
            if accounting.ActiveProcesses == 0:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(.05, remaining))

    def close(self):
        if self.closed:
            return
        failures = []
        # Close the job first, so an interrupted start is also fenced. A
        # failed CloseHandle remains recorded for a subsequent explicit retry.
        for name, handle in (('job', self.job), ('thread', self.info.hThread),
                             ('process', self.info.hProcess)):
            if handle:
                try:
                    if not self.close_handle(handle):
                        failures.append((name, ctypes.get_last_error()))
                        continue
                except BaseException as error:
                    failures.append((name, type(error).__name__))
                    continue
                if name == 'job':
                    self.job = None
                elif name == 'thread':
                    self.info.hThread = None
                else:
                    self.info.hProcess = None
        self.closed = not failures
        if failures:
            raise OSError('CloseHandle failed: ' + ', '.join(f'{name} ({code})' for name, code in failures))


class WindowsX4Process:
    """One explicitly started X4 child; no Steam or profile files are changed.

    Use as a context manager or call ``close`` in a finally block. This owner
    does not establish or clean up the caller's view, profile or save files.
    """
    def __init__(self, *, enabled=False):
        if enabled is not True or not _windows_runtime():
            raise ValueError('explicit Windows experiment opt-in required')
        self._thread = threading.current_thread()
        self._phase = 'new'
        self._job = None
        self.pid = None

    def _check_thread(self):
        if threading.current_thread() is not self._thread:
            raise RuntimeError('exclusive process owner thread required')

    def __enter__(self):
        self._check_thread()
        if self._phase != 'new':
            raise RuntimeError('a fresh process owner is required')
        return self

    def __exit__(self, exception_type, exception, traceback):
        try:
            confirmed = self.close()
        except BaseException as cleanup_error:
            if exception is None:
                raise
            _record_cleanup_issue(exception, 'Windows X4 job cleanup failed: ' + type(cleanup_error).__name__)
            return False
        if not confirmed:
            if exception is None:
                raise RuntimeError('Windows X4 job cleanup could not be confirmed')
            _record_cleanup_issue(exception, 'Windows X4 job cleanup could not be confirmed')
        return False

    def start(self, view, *, profile_id, guard):
        """Start only a pinned X4 in the caller's exclusively controlled view.

        Guard must verify the view/profile and other launch prerequisites. A
        raised start is indeterminate; never reuse a profile until the caller
        has independently established that the created process has stopped.
        """
        self._check_thread()
        if (self._phase != 'new' or type(profile_id) is not int or
                profile_id not in (profile.CAPTURE_PROFILE, *profile.PROFILES) or not callable(guard)):
            raise ValueError('new owner, fixture profile and guard required')
        view = Path(view)
        if not view.is_absolute() or not view.is_dir() or guard() is not True:
            raise ValueError('owned absolute view and successful guard required')
        game.verify_exe(view)
        ready, _ = steam.status()
        if not ready or guard() is not True:
            raise RuntimeError('direct Steam startup or guard unavailable')
        environment = {key: value for key, value in steam.client_environment().items()
                       if key.casefold() not in ('steamappid', 'steamgameid')}
        environment.update(SteamAppId='392160', SteamGameId='392160')
        command = [str(view / 'X4.exe'), '-personalfolderid', str(profile_id), '-skipintro',
                   '-noworkshopsync', '-nocputhrottle', '-debug', 'scripts', '-logfile', 'x4authority.log']
        self._phase = 'starting'
        try:
            self._job = _Win32Job()
            self.pid = self._job.start(command, str(view), environment)
            if guard() is not True or not self._job.running():
                raise RuntimeError('X4 stopped or launch guard failed')
            self._phase = 'running'
            return self.pid
        except BaseException as error:
            self._phase = 'unknown'
            if self._job is not None:
                try:
                    self._job.close()
                except BaseException as cleanup_error:
                    _record_cleanup_issue(error, 'Windows X4 job handle close failed: ' + type(cleanup_error).__name__)
            raise

    def stop(self, timeout=10):
        """Terminate only the owned job; observe root exit and no active job members."""
        self._check_thread()
        if self._phase != 'running' or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 300:
            raise ValueError('running owner and bounded timeout required')
        self._phase = 'stopping'
        try:
            if not self._job.stop(timeout):
                return False
        except BaseException as error:
            self._phase = 'unknown'
            try:
                self._job.close()
            except BaseException as cleanup_error:
                _record_cleanup_issue(error, 'Windows X4 job handle close failed: ' + type(cleanup_error).__name__)
            raise
        self._phase = 'close_pending'
        self._job.close()
        self._phase = 'stopped'
        return True

    def close(self, timeout=10):
        """Stop or finish observing the owned job; return false if unconfirmed.

        A fresh owner has started nothing. A failed/uncertain start or stop
        cannot be reclassified as clean merely because its job was closed.
        On timeout, job and process handles remain owned for a later ``close``;
        callers must retain this owner and must not reuse its profile meanwhile.
        """
        self._check_thread()
        if self._phase == 'running':
            return self.stop(timeout)
        if self._phase == 'new':
            self._phase = 'closed'
            return True
        if self._phase in ('stopped', 'closed'):
            return True
        if self._phase == 'close_pending':
            self._job.close()
            self._phase = 'stopped'
            return True
        if self._phase == 'stopping':
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 300:
                raise ValueError('bounded timeout required')
            try:
                if not self._job.wait_cleanup(timeout):
                    return False
            except BaseException as error:
                self._phase = 'unknown'
                try:
                    self._job.close()
                except BaseException as cleanup_error:
                    _record_cleanup_issue(error, 'Windows X4 job handle close failed: ' + type(cleanup_error).__name__)
                raise
            self._phase = 'close_pending'
            self._job.close()
            self._phase = 'stopped'
            return True
        if self._job is not None:
            self._job.close()
        return False

    def is_running(self):
        """Observe the retained root handle; it does not prove session health."""
        self._check_thread()
        if self._phase != 'running':
            raise RuntimeError('X4 process is not running under this owner')
        return self._job.running()

    def exit_code(self):
        """Read the retained root process handle. A running process returns None."""
        self._check_thread()
        if self._phase != 'running':
            raise RuntimeError('X4 process has no live owner')
        return self._job.exit_code()

    def owns_running_pid(self, pid):
        """Sample whether a PID still names our live root in the owned job.

        Uses the retained handle rather than reopening a process by PID. This
        does not prove socket ownership or prevent exit after the observation.
        The owner thread must keep this object alive across the caller's work.
        """
        self._check_thread()
        if type(pid) is not int or not 0 < pid <= 0xffffffff:
            raise ValueError('positive Windows process identifier required')
        if self._phase != 'running':
            raise RuntimeError('X4 process is not running under this owner')
        return self.pid == pid and self._job.owns_running_pid(pid)

    @property
    def cleanup_confirmed(self):
        self._check_thread()
        return self._phase == 'stopped'


def _windows_runtime():
    return os.name == 'nt'
