#!/usr/bin/env python3
"""Native Windows 11 X4 9.00 verification of the packaged Company motion sender & DLL clock.

Exercises the EXACT packaged network/x4lan_net_64.dll (6ad97e04...) installed at the
production path extensions/x4_lan_lab/native/x4lan_net_64.dll and loaded through the
unmodified production modules:
  X4LAN_LOCAL_UDP_MODULE.load -> X4LAN_COMPANY_MOTION_SOURCE.new_x4 ->
  X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER.new_x4 -> X4LAN_AUTHORITY_JOB_DESCRIPTOR_PROBE
paired on the Windows host with the unmodified production Python modules:
  WindowsMotionPeer -> CompanyMotionSource -> ReconnectingCompanyMotionClient ->
  CompanyMotionService (private local loopback peer).

Verifies and records:
  1. Loaded DLL in live X4.exe process memory (CreateToolhelp32Snapshot TH32CS_SNAPMODULE),
     confirming exact path, image size, on-disk SHA-256 (6ad97e04...), and lua51_64.dll.
  2. Live udp.monotonic() clock inside native Windows X4 9.00 correlated against Python's
     time.perf_counter() across every received SAMPLE3 packet.
  3. Exact WindowsMotionPeer GetExtendedUdpTable binding (0.0.0.0/127.0.0.1, port, PID)
     and CompanyMotionSource stage progression.
  4. Real locally controlled ship flight via stock MD <set_playership_throttle value="0.35"/>
     producing two+ distinct positions and nonzero real_velocity, correlated end-to-end with
     CompanyMotionService acceptance and ReconnectingCompanyMotionClient acknowledgements
     (last_acknowledged_sample and last_nonzero_velocity_acknowledged).

Safety:
  - Enforces shared lockfile C:\\Users\\Observer\\Documents\\Gemini Projects\\.x4-native-test.lock
    and refuses if X4.exe is already running.
  - Disposable profile 45850 and isolated x4ls view only; asserts protected_unchanged,
    view_removed, x4_running_after == False, and project_error_count == 0.
  - 800x600 muted fast window started with SW_SHOWNOACTIVATE and kept at HWND_BOTTOM.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT / 'tools/dev')]

from authority_company_cockpit_windows import (
    EPOCH, NONCE, SESSION, SW_SHOWNOACTIVATE, STARTF_USESHOWWINDOW,
    _dist3, attach_kill_on_close_job, find_window, remove_view_patiently,
    tree_metadata, tuck_window_bottom_corner, x4_running,
)
from authority.windows_motion_peer import WindowsMotionPeer, _AF_INET, _get_extended_udp_table, _load_udp_table_function
from authority.windows_x4_process import WindowsX4Process

EXTENSION_ID = 'x4_lan_lab'
LOGFILE = 'neon-windows-sender.log'
PROFILE_DEFAULT = 45850
EXPECTED_PACKAGED_DLL_SHA256 = '6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0'
SHARED_LOCKFILE = ROOT.parent / '.x4-native-test.lock'


def _pid_alive(pid):
    try:
        out = subprocess.run(
            ['tasklist', '/FI', f'PID eq {int(pid)}', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, check=True,
        ).stdout
        return f'"{int(pid)}"' in out
    except Exception:
        return False


def acquire_shared_lock(profile):
    deadline = time.monotonic() + 300.0
    while True:
        try:
            with open(SHARED_LOCKFILE, 'x', encoding='utf-8') as handle:
                json.dump({
                    'agent': 'Neon',
                    'pid': os.getpid(),
                    'profile': int(profile),
                    'started_at': datetime.now(timezone.utc).isoformat(),
                }, handle)
            return
        except FileExistsError:
            try:
                info = json.loads(SHARED_LOCKFILE.read_text(encoding='utf-8'))
            except Exception:
                info = {}
            owner_pid = info.get('pid')
            owner_alive = isinstance(owner_pid, int) and _pid_alive(owner_pid)
            if not owner_alive and not x4_running():
                try:
                    SHARED_LOCKFILE.unlink()
                    continue
                except OSError:
                    pass
            if info.get('agent') == 'Quartz' and owner_alive and time.monotonic() < deadline:
                time.sleep(15.0)
                continue
            raise SystemExit(
                f'REFUSED: shared lockfile {SHARED_LOCKFILE} held by {info!r}; '
                'will not disturb concurrent native X4 session.'
            )


def release_shared_lock():
    try:
        if SHARED_LOCKFILE.is_file():
            info = json.loads(SHARED_LOCKFILE.read_text(encoding='utf-8'))
            if info.get('pid') == os.getpid():
                SHARED_LOCKFILE.unlink()
    except Exception:
        pass

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('th32ModuleID', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('GlblcntUsage', wintypes.DWORD),
        ('ProccntUsage', wintypes.DWORD),
        ('modBaseAddr', ctypes.c_void_p),
        ('modBaseSize', wintypes.DWORD),
        ('hModule', wintypes.HMODULE),
        ('szModule', wintypes.WCHAR * 256),
        ('szExePath', wintypes.WCHAR * 260),
    ]


def snapshot_process_modules(pid, *, targets=('x4lan_net_64.dll', 'lua51_64.dll')):
    """Enumerate live loaded DLL modules inside X4.exe via Win32 Toolhelp32."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Module32FirstW.restype = wintypes.BOOL
    kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MODULEENTRY32W)]
    kernel32.Module32NextW.restype = wintypes.BOOL
    kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MODULEENTRY32W)]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, int(pid))
    if not snap or snap == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), 'CreateToolhelp32Snapshot')
    wanted = {name.casefold() for name in targets}
    found = {}
    total = 0
    try:
        entry = _MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            total += 1
            mod_name = entry.szModule
            if mod_name.casefold() in wanted:
                exe_path = Path(entry.szExePath)
                sha256 = hashlib.sha256(exe_path.read_bytes()).hexdigest() if exe_path.is_file() else None
                found[mod_name.casefold()] = {
                    'module': mod_name,
                    'path': str(exe_path),
                    'base_addr': hex(entry.modBaseAddr or 0),
                    'image_size': int(entry.modBaseSize),
                    'file_size': exe_path.stat().st_size if exe_path.is_file() else None,
                    'sha256': sha256,
                }
            ok = kernel32.Module32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return {'pid': int(pid), 'total_modules': total, 'matched': found}


class _LiveChildProcessOwner(WindowsX4Process):
    """Adapts our SW_SHOWNOACTIVATE child Popen to exact WindowsMotionPeer ownership checks."""

    def __init__(self, popen):
        super().__init__(enabled=True)
        self._popen = popen
        self.pid = int(popen.pid)
        self._phase = 'running'

    def owns_running_pid(self, pid):
        return int(pid) == self.pid and self._popen.poll() is None


def compose_extension(extensions_dir, *, dll_path, profile, gamestart, epoch, save_path, motion_cfg):
    ext = Path(extensions_dir) / EXTENSION_ID
    if ext.exists():
        shutil.rmtree(ext)
    for sub in ('ui', 'md', 'native'):
        (ext / sub).mkdir(parents=True)

    (ext / 'content.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<content id="{EXTENSION_ID}" name="X4 Neon Packaged Windows Sender Trial" '
        'description="Disposable native Windows packaged sender and DLL clock verification" '
        'author="Neon" version="100" date="2026-11-25" save="false">\n'
        '  <dependency version="900" />\n'
        '</content>\n',
        encoding='utf-8',
    )

    ui_sources = [
        ('authority_registry_resolve_string.lua', 'tools/dev/authority_registry_resolve_string.lua'),
        ('local_udp_module.lua', 'src/authority/lua/local_udp_module.lua'),
        ('replica_motion_binding.lua', 'src/authority/lua/replica_motion_binding.lua'),
        ('authority_job_descriptor_probe.lua', 'tools/dev/authority_job_descriptor_probe.lua'),
        ('source_motion_sampler.lua', 'src/authority/lua/source_motion_sampler.lua'),
        ('map_render_observation.lua', 'src/authority/lua/map_render_observation.lua'),
        ('company_observer_target.lua', 'src/authority/lua/company_observer_target.lua'),
        ('company_observer_map.lua', 'src/authority/lua/company_observer_map.lua'),
        ('company_observer_stream.lua', 'src/authority/lua/company_observer_stream.lua'),
        ('company_motion_source.lua', 'src/authority/lua/company_motion_source.lua'),
        ('authority_windows_sender_x4.lua', 'tools/dev/authority_windows_sender_x4.lua'),
    ]
    md_sources = [
        ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
        ('authority_job_descriptor_probe.xml', 'tools/dev/authority_job_descriptor_probe.xml'),
        ('company_observer_target.xml', 'src/authority/md/company_observer_target.xml'),
        ('authority_windows_sender_probe.xml', 'tools/dev/authority_windows_sender_probe.xml'),
    ]

    for dst_name, rel_src in ui_sources:
        src = ROOT / rel_src
        if dst_name == 'local_udp_module.lua':
            # Exact export transformation used by src/authority/experimental_windows_fixture_overlay.py:213
            text = src.read_text(encoding='utf-8').replace('\r\n', '\n')
            assert text.endswith('return M\n')
            text = text[:-len('return M\n')] + 'X4LAN_LOCAL_UDP_MODULE=M\nreturn M\n'
            (ext / 'ui' / dst_name).write_text(text, encoding='utf-8')
        else:
            shutil.copyfile(src, ext / 'ui' / dst_name)

    for dst_name, rel_src in md_sources:
        shutil.copyfile(ROOT / rel_src, ext / 'md' / dst_name)

    dst_dll = ext / 'native' / 'x4lan_net_64.dll'
    shutil.copyfile(dll_path, dst_dll)
    installed_dll_sha256 = hashlib.sha256(dst_dll.read_bytes()).hexdigest()

    (ext / 'ui/config.lua').write_text(
        'X4NEON_WINDOWS_SENDER = {\n'
        '  enabled = true,\n'
        f'  profile = {json.dumps(str(profile))},\n'
        f'  gamestart = {json.dumps(gamestart)},\n'
        f'  epoch = {json.dumps(epoch)},\n'
        f'  nonce = {json.dumps(NONCE)},\n'
        f'  save_path = {json.dumps(str(save_path))},\n'
        '  company_motion = {\n'
        f'    port = {int(motion_cfg["port"])},\n'
        f'    lease = {json.dumps(motion_cfg["lease"])},\n'
        f'    generation = {json.dumps(motion_cfg["generation"])},\n'
        f'    nonce = {json.dumps(motion_cfg["nonce"])},\n'
        f'    index = {int(motion_cfg["index"])},\n'
        '  },\n'
        '}\n',
        encoding='ascii',
    )
    ui_entries = ''.join(
        ['    <file name="ui/config.lua" />\n']
        + [f'    <file name="ui/{dst}" />\n' for dst, _ in ui_sources]
    )
    (ext / 'ui.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<addon name="{EXTENSION_ID}">\n'
        '  <environment type="menus">\n'
        '    <dependency name="ego_detailmonitor" />\n'
        f'{ui_entries}'
        '  </environment>\n'
        '</addon>\n',
        encoding='utf-8',
    )
    return ext, installed_dll_sha256


class X4SenderBridge:
    _TAG = '[X4LAN] AUTH_WIN_SENDER '
    _KV = re.compile(r'([A-Za-z0-9_]+)=([^ ]+)')

    def __init__(self, process, log_path, ext_dir, *, timeout=90.0):
        self.process = process
        self.log_path = log_path
        self.ext_dir = ext_dir
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self.ready = None
        self.error = None
        self.done = False
        self.responses = {}
        self._next_seq = 1
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    @staticmethod
    def _parse_kv(text):
        return {m.group(1): m.group(2) for m in X4SenderBridge._KV.finditer(text)}

    def _watch(self):
        pos = 0
        hwnd = None
        last_tuck = 0.0
        while not self._stop.is_set():
            now_t = time.monotonic()
            if now_t - last_tuck >= 0.4:
                last_tuck = now_t
                if hwnd is None:
                    hwnd = find_window(self.process.pid)
                if hwnd is not None:
                    tuck_window_bottom_corner(hwnd)
            if self.log_path.exists():
                try:
                    data = self.log_path.read_bytes()
                except OSError:
                    data = b''
                if len(data) > pos:
                    chunk = data[pos:].decode('utf-8', errors='replace')
                    pos = len(data)
                    for raw_line in chunk.splitlines():
                        idx = raw_line.find(self._TAG)
                        if idx < 0:
                            continue
                        body = raw_line[idx + len(self._TAG):].strip()
                        with self._cond:
                            if body.startswith('READY '):
                                self.ready = self._parse_kv(body[6:])
                                self._cond.notify_all()
                            elif body.startswith('RESP '):
                                kv = self._parse_kv(body[5:])
                                if 'seq' in kv:
                                    self.responses[int(kv['seq'])] = kv
                                    self._cond.notify_all()
                            elif body.startswith('ERROR '):
                                self.error = body[6:]
                                self._cond.notify_all()
                            elif body == 'DONE':
                                self.done = True
                                self._cond.notify_all()
            if self.process.poll() is not None and not self.done:
                with self._cond:
                    if self.error is None:
                        self.error = f'X4 exited early with code {self.process.returncode}'
                    self._cond.notify_all()
                return
            time.sleep(0.05)

    def wait_ready(self, timeout=180.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.ready is None and self.error is None:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError('timed out waiting for X4 READY')
                self._cond.wait(min(0.25, rem))
            if self.error is not None:
                raise RuntimeError(f'X4 error before READY: {self.error}')
            return dict(self.ready)

    @staticmethod
    def _lua_val(v):
        if isinstance(v, bool):
            return 'true' if v else 'false'
        if isinstance(v, (int, float)):
            return repr(v)
        return json.dumps(str(v))

    def request(self, op, *, pump=None, timeout=None, **params):
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
        fields = [f'seq = {seq}', f'op = {json.dumps(op)}', f'request_id = {json.dumps(f"req-{seq}")}']
        for k, v in params.items():
            if v is not None:
                fields.append(f'{k} = {self._lua_val(v)}')
        partial = self.ext_dir / f'req_{seq}.partial'
        partial.write_text('return { ' + ', '.join(fields) + ' }\n', encoding='ascii')
        os.replace(partial, self.ext_dir / f'req_{seq}.lua')
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            if pump is not None:
                pump()
            with self._cond:
                if seq in self.responses:
                    return dict(self.responses[seq])
                if self.error is not None:
                    raise RuntimeError(f'X4 error during seq={seq} op={op}: {self.error}')
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError(f'timed out waiting for seq={seq} op={op}')
                self._cond.wait(min(0.02 if pump else 0.2, rem))

    def stop(self):
        self._stop.set()
        self._thread.join(3)


class _RecordingMotionSource:
    """Thin read-only tap around production CompanyMotionSource.poll() to record QPC correlation."""

    def __init__(self, inner):
        self._inner = inner
        self.samples = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def poll(self, validate_peer):
        sample = self._inner.poll(validate_peer)
        if sample is not None:
            py_qpc = time.perf_counter()
            self.samples.append({
                'py_perf_counter': py_qpc,
                'delta_py_minus_capture_ms': round((py_qpc - sample['capture_time']) * 1000.0, 3),
                'sample': dict(sample),
            })
        return sample


def run_windows_sender_trial(view, profile_dir, out, args, dll_path):
    from authority.company_motion_reconnect import ReconnectingCompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.company_motion_source import CompanyMotionSource
    from lockstep.launcher import steam

    log_path = profile_dir / LOGFILE
    if log_path.exists():
        log_path.unlink()

    stages = []

    def on_stage(name):
        stages.append({'stage': name, 'py_perf_counter': time.perf_counter()})

    # Exact production CompanyMotionSource!
    inner_source = CompanyMotionSource(on_stage=on_stage)
    motion_cfg = inner_source.configure(EPOCH, 1)
    source_tap = _RecordingMotionSource(inner_source)
    service = CompanyMotionService()
    client = None
    process = job_handle = bridge = None
    try:
        ext_dir, installed_dll_sha256 = compose_extension(
            view / 'extensions',
            dll_path=dll_path,
            profile=args.profile,
            gamestart=args.gamestart,
            epoch=EPOCH,
            save_path=profile_dir / 'save',
            motion_cfg=motion_cfg,
        )
        command = [
            str(view / 'X4.exe'),
            '-personalfolderid', str(args.profile),
            '-skipintro', '-noworkshopsync', '-nocputhrottle',
            '-debug', 'scripts', '-logfile', LOGFILE,
        ]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWNOACTIVATE
        process = subprocess.Popen(command, cwd=str(view), env=steam.client_environment(), startupinfo=si)
        job_handle = attach_kill_on_close_job(process.pid)

        # Exact unmodified production WindowsMotionPeer validator!
        proc_owner = _LiveChildProcessOwner(process)
        peer_validator = WindowsMotionPeer(proc_owner, inner_source.socket)
        udp_table_fn = _load_udp_table_function()
        validated_peer_info = {}

        def logging_validator(peer):
            ok = peer_validator(peer)
            if ok and not validated_peer_info:
                rows = _get_extended_udp_table(_AF_INET, udp_table_fn)
                matches = [r for r in rows if r[1] == peer[1] and r[2] == process.pid]
                validated_peer_info.update(
                    peer_ip=peer[0], peer_port=peer[1], pid=process.pid,
                    udp_table_rows=[{'local_ip': r[0], 'local_port': r[1], 'pid': r[2]} for r in matches],
                )
            return ok

        bridge = X4SenderBridge(process, log_path, ext_dir, timeout=60.0)
        ready = bridge.wait_ready(timeout=args.timeout)
        own_sector = ready['own_sector']
        own_p0 = {a: float(ready[f'own_{a}']) for a in ('x', 'y', 'z')}

        grant = service.admit(1, 'a1' * 16, 1, own_sector, SESSION, EPOCH)
        service.set_running(True)

        client = ReconnectingCompanyMotionClient.observing(
            service.port, grant, source_tap, logging_validator,
        )

        ack_seen = False

        def pump_all():
            nonlocal ack_seen
            service.poll()
            client.poll()
            if not ack_seen and client.stats()['samples_acknowledged'] > 0:
                ack_seen = True
                on_stage('first_own_sample_acknowledged')

        def pump_until(pred, timeout=20.0, label='condition'):
            deadline = time.monotonic() + timeout
            while not pred():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'timed out waiting for {label}')
                pump_all()
                time.sleep(0.005)

        def pump_for(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                pump_all()
                time.sleep(0.005)

        pump_until(lambda: client._client is not None and client._client._running, label='motion_client_running')

        # 1. Start the unmodified production sender inside X4 using packaged x4lan_net_64.dll!
        py_before_start = time.perf_counter()
        r_start = bridge.request('start_sender', pump=pump_all)
        py_after_start = time.perf_counter()
        assert r_start.get('status') == 'sender_started', f'sender start failed: {r_start}'

        # Inspect live X4.exe process memory via Win32 Toolhelp32 to prove exact loaded DLL!
        loaded_modules = snapshot_process_modules(process.pid)

        # 2. Wait for first acknowledged stationary samples from real X4.exe!
        pump_until(lambda: client.stats()['samples_acknowledged'] >= 5, timeout=20.0,
                   label='initial_stationary_samples_acknowledged')
        stats_stationary = client.stats()
        probe_before_flight = bridge.request('probe_pose', pump=pump_all)

        # 3. Real controlled-ship flight via stock MD <set_playership_throttle value="0.35"/>!
        r_thr_on = bridge.request('start_throttle', pump=pump_all)
        assert r_thr_on.get('status') == 'throttle_started', f'start_throttle failed: {r_thr_on}'
        pump_for(2.2)
        probe_mid_flight = bridge.request('probe_pose', pump=pump_all)
        stats_mid_flight = client.stats()

        # 4. Zero throttle and collect decelerating / settled samples!
        r_thr_off = bridge.request('stop_throttle', pump=pump_all)
        assert r_thr_off.get('status') == 'throttle_stopped', f'stop_throttle failed: {r_thr_off}'
        pump_for(1.2)
        probe_post_flight = bridge.request('probe_pose', pump=pump_all)
        stats_final = client.stats()
        service_traffic = service.participant_traffic()
        obs_stats = inner_source.observer_stats()

        r_done = bridge.request('done', pump=pump_all)
        assert r_done.get('status') == 'done'

        # Compute clock correlation & motion summaries across all received SAMPLE3 packets
        samples = source_tap.samples
        deltas_ms = [item['delta_py_minus_capture_ms'] for item in samples]
        capture_times = [item['sample']['capture_time'] for item in samples]
        capture_monotonic = all(capture_times[i] > capture_times[i - 1] for i in range(1, len(capture_times)))
        nonzero_samples = []
        for item in samples:
            rv = item['sample']['real_velocity']
            if rv is not None:
                spd = math.hypot(rv['x'], rv['y'], rv['z'])
                if spd > 0.1:
                    nonzero_samples.append({**item, 'speed_mps': round(spd, 6)})

        own_final = {a: float(probe_post_flight[a]) for a in ('x', 'y', 'z')}
        final_disp_m = _dist3(own_p0, own_final)

        # Correlate client's last_nonzero_velocity_acknowledged and last_acknowledged_sample back to exact X4 SAMPLE3
        last_ack = stats_final.get('last_acknowledged_sample')
        last_nz_ack = stats_final.get('last_nonzero_velocity_acknowledged')
        by_seq = {item['sample']['sequence']: item for item in samples}
        corr_last_ack = by_seq.get(last_ack['sequence']) if last_ack else None
        corr_last_nz = by_seq.get(last_nz_ack['sequence']) if last_nz_ack else None

        return {
            'outcome': 'done',
            'evidence_source': {
                'sender': 'real_native_windows_x4_900_packaged_6ad97e04_dll_and_unmodified_company_motion_source',
                'flight_input': 'real_x4_md_set_playership_throttle_0.35_to_0_on_player_controlled',
                'peer': 'private_local_loopback_CompanyMotionService_and_ReconnectingCompanyMotionClient',
            },
            'installed_dll_sha256': installed_dll_sha256,
            'loaded_modules_in_x4': loaded_modules,
            'sender_start': {
                'response': r_start,
                'py_perf_counter_before': py_before_start,
                'py_perf_counter_after': py_after_start,
            },
            'udp_peer_validation': validated_peer_info,
            'stages': stages,
            'ship': {
                'index': 1, 'hex': ready['own_hex'], 'code': ready['own_code'],
                'macro': ready['own_macro'], 'sector': own_sector,
                'initial_pose': own_p0, 'final_pose': own_final,
                'final_displacement_m': round(final_disp_m, 6),
            },
            'probes': {
                'before_flight': probe_before_flight,
                'throttle_started': r_thr_on,
                'mid_flight': probe_mid_flight,
                'throttle_stopped': r_thr_off,
                'post_flight': probe_post_flight,
            },
            'clock_correlation': {
                'sample_count': len(samples),
                'capture_time_strictly_increasing': capture_monotonic,
                'first_capture_time': capture_times[0] if capture_times else None,
                'last_capture_time': capture_times[-1] if capture_times else None,
                'min_py_minus_capture_ms': min(deltas_ms) if deltas_ms else None,
                'median_py_minus_capture_ms': sorted(deltas_ms)[len(deltas_ms) // 2] if deltas_ms else None,
                'max_py_minus_capture_ms': max(deltas_ms) if deltas_ms else None,
                'first_sample': samples[0] if samples else None,
                'last_sample': samples[-1] if samples else None,
            },
            'flight_motion': {
                'nonzero_velocity_sample_count': len(nonzero_samples),
                'first_nonzero_velocity_sample': nonzero_samples[0] if nonzero_samples else None,
                'peak_speed_sample': max(nonzero_samples, key=lambda x: x['speed_mps']) if nonzero_samples else None,
                'last_nonzero_velocity_sample': nonzero_samples[-1] if nonzero_samples else None,
            },
            'acknowledgement_correlation': {
                'client_stats_stationary': stats_stationary,
                'client_stats_mid_flight': stats_mid_flight,
                'client_stats_final': stats_final,
                'service_traffic': service_traffic,
                'observer_stats': obs_stats,
                'last_acknowledged_matches_x4_sample': (
                    corr_last_ack is not None
                    and corr_last_ack['sample']['sequence'] == last_ack['sequence']
                    and corr_last_ack['sample']['capture_time'] == last_ack['capture_time']
                    and corr_last_ack['sample']['x'] == last_ack['x']
                    and corr_last_ack['sample']['y'] == last_ack['y']
                    and corr_last_ack['sample']['z'] == last_ack['z']
                ),
                'last_nonzero_ack_matches_x4_sample': (
                    corr_last_nz is not None
                    and corr_last_nz['sample']['sequence'] == last_nz_ack['sequence']
                    and corr_last_nz['sample']['capture_time'] == last_nz_ack['capture_time']
                    and corr_last_nz['sample']['real_velocity'] == last_nz_ack['real_velocity']
                ),
                'last_nonzero_ack_delta_py_minus_capture_ms': (
                    corr_last_nz['delta_py_minus_capture_ms'] if corr_last_nz else None
                ),
            },
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        try:
            inner_source.close()
        except Exception:
            pass
        try:
            service.close()
        except Exception:
            pass
        if bridge is not None:
            bridge.stop()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if job_handle is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(job_handle)
            except BaseException:
                pass
        if log_path.is_file():
            shutil.copyfile(log_path, out / LOGFILE)


def main(argv=None):
    from lockstep.launcher import game as game_mod, profile as profile_mod, steam

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--dll', required=True, help='Path to packaged network/x4lan_net_64.dll')
    parser.add_argument('--expected-dll-sha256', default=EXPECTED_PACKAGED_DLL_SHA256)
    parser.add_argument('--game')
    parser.add_argument('--profile', type=int, default=PROFILE_DEFAULT)
    parser.add_argument('--gamestart', default='custom_creative')
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--timeout', type=float, default=240.0)
    args = parser.parse_args(argv)

    dll_path = Path(args.dll).resolve()
    if not dll_path.is_file():
        raise SystemExit(f'Packaged DLL not found: {dll_path}')
    dll_sha = hashlib.sha256(dll_path.read_bytes()).hexdigest()
    if dll_sha != args.expected_dll_sha256.lower():
        raise SystemExit(f'DLL SHA-256 mismatch: {dll_sha} != {args.expected_dll_sha256}')
    if args.profile not in profile_mod.PROFILES:
        raise SystemExit(f'Profile {args.profile} is outside disposable launcher lanes')
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f'{out} already exists; use a fresh output directory')

    acquire_shared_lock(args.profile)
    try:
        if x4_running():
            raise SystemExit('REFUSED: X4.exe is already running; never disturb a running X4 instance.')
        ok, msg = steam.status()
        if not ok:
            raise SystemExit(msg)
        game = Path(args.game) if args.game else game_mod.find_install()
        game_mod.verify_exe(game)
        exe_sha256 = hashlib.sha256((game / 'X4.exe').read_bytes()).hexdigest()
        view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-sender-{out.name}'
        if view.exists():
            raise SystemExit(f'{view} already exists')

        documents = profile_mod.documents()
        protected_before = {
            'other_profiles': {
                entry.name: tree_metadata(entry)
                for entry in sorted(documents.iterdir())
                if entry.is_dir() and entry.name != str(args.profile)
            },
            'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
        }
        out.mkdir(parents=True)
        result = {
            'options': vars(args), 'game': str(game), 'x4_exe_sha256': exe_sha256,
            'source_dll_path': str(dll_path), 'source_dll_sha256': dll_sha,
            'view': str(view), 'outcome': 'started',
        }
        ctypes.windll.user32.SetProcessDPIAware()
        try:
            game_mod.create_view(game, view)
            prof_dir = profile_mod.prepare(
                args.profile,
                [*game_mod.dlc_ids(view), EXTENSION_ID],
                width=args.width, height=args.height, fast=True,
            )
            trial = run_windows_sender_trial(view, prof_dir, out, args, dll_path)
            result['native_windows_sender_trial'] = trial
            result['outcome'] = 'done'
        except BaseException as exc:
            result['outcome'] = 'error'
            result['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            if view.exists():
                remove_view_patiently(game_mod, view)
            protected_after = {
                'other_profiles': {
                    entry.name: tree_metadata(entry)
                    for entry in sorted(documents.iterdir())
                    if entry.is_dir() and entry.name != str(args.profile)
                },
                'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
            }
            protected_unchanged = (protected_after == protected_before)
            view_removed = not view.exists()
            x4_running_after = x4_running()
            log_file = out / LOGFILE
            project_errors = []
            sender_faults = []
            sender_gaps = []
            renderer_lines = []
            if log_file.is_file():
                for line in log_file.read_text(encoding='utf-8', errors='replace').splitlines():
                    if 'Could not find signature file' in line:
                        continue
                    if 'Vulkan' in line and len(renderer_lines) < 5:
                        renderer_lines.append(line.strip())
                    if '[X4LAN] AUTH_COMPANY_MOTION_SOURCE_FAILED ' in line:
                        sender_faults.append(line)
                        project_errors.append(line)
                        continue
                    if '[X4LAN] AUTH_COMPANY_MOTION_SOURCE_GAP ' in line:
                        sender_gaps.append(line)
                        continue
                    if '[X4LAN] AUTH_COMPANY_OBSERVER_UNAVAILABLE' in line:
                        project_errors.append(line)
                        continue
                    if '[X4LAN] AUTH_WIN_SENDER ' in line:
                        if ' ERROR ' in line:
                            project_errors.append(line)
                        continue
                    if ('[=ERROR=]' in line or 'Error in MD cue' in line) and (
                        EXTENSION_ID in line or 'X4Authority' in line or 'X4LAN' in line
                    ):
                        project_errors.append(line)
            result.update({
                'protected_unchanged': protected_unchanged,
                'view_removed': view_removed,
                'x4_running_after': x4_running_after,
                'project_error_count': len(project_errors),
                'project_errors': project_errors,
                'sender_faults': sender_faults,
                'sender_gaps': sender_gaps,
                'renderer_lines': renderer_lines,
            })
            (out / 'result.json').write_text(json.dumps(result, indent=1, sort_keys=True))

        assert result['protected_unchanged'], 'protected profile/config changed!'
        assert result['view_removed'], 'isolated x4 view was not removed!'
        assert not result['x4_running_after'], 'X4.exe still running after test!'
        assert result['project_error_count'] == 0, f'MD/Lua errors detected: {result["project_errors"]}'

        trial = result['native_windows_sender_trial']
        print(json.dumps({
            'outcome': result['outcome'],
            'loaded_dll': trial['loaded_modules_in_x4']['matched'].get('x4lan_net_64.dll'),
            'udp_peer_validation': trial['udp_peer_validation'],
            'stages': [s['stage'] for s in trial['stages']],
            'ship_disp_m': trial['ship']['final_displacement_m'],
            'clock_correlation': {
                k: v for k, v in trial['clock_correlation'].items()
                if k not in ('first_sample', 'last_sample')
            },
            'nonzero_velocity_samples': trial['flight_motion']['nonzero_velocity_sample_count'],
            'peak_speed_mps': (trial['flight_motion']['peak_speed_sample'] or {}).get('speed_mps'),
            'samples_acknowledged': trial['acknowledgement_correlation']['client_stats_final']['samples_acknowledged'],
            'last_acknowledged_matches': trial['acknowledgement_correlation']['last_acknowledged_matches_x4_sample'],
            'last_nonzero_ack_matches': trial['acknowledgement_correlation']['last_nonzero_ack_matches_x4_sample'],
            'service_traffic': trial['acknowledgement_correlation']['service_traffic'],
            'protected_unchanged': result['protected_unchanged'],
            'view_removed': result['view_removed'],
            'x4_running_after': result['x4_running_after'],
            'project_error_count': result['project_error_count'],
            'out': str(out),
        }, indent=2))
        return 0
    finally:
        release_shared_lock()


if __name__ == '__main__':
    main()
