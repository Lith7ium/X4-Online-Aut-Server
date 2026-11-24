"""Isolated native Windows X4 9.00 verification of opt-in receiving-cockpit Company ship playback.

Exercises the REAL production Lua & MD modules inside native Windows X4 9.00 (disposable profile 45850,
isolated x4ls view, 800x600 fast=True HWND_BOTTOM) paired with real loopback CompanyMotionService,
CompanyMotionClient, CompanyMotionSource, and CompanyMotionObserver (with a recorded/loopback remote
Company pilot trajectory, explicitly labeled as such):
  1. Stage 1 (Selected HUD target): Real SetSofttarget() locks the nearby unoccupied Company Pulsar ->
     X4LAN_COMPANY_COCKPIT_OBSERVER resolves mode='hud' -> real CompanyMotionService -> admitted client
     -> X4LAN_COMPANY_OBSERVER_STREAM -> X4LAN_COMPANY_COCKPIT_PLAYBACK -> X4LAN_COMPANY_HULL_PLAYBACK_X4
     drives the hull with velocity and records delayed later-update readbacks while own_disp_m == 0.0.
  2. Stage 2 (Nearby radar-visible pilot WITHOUT a HUD target lock + independent map slot): Real
     RemoveSofttarget() clears HUD lock (has_target=false) and opens independent map observation on a
     3rd Company ship in another sector -> cockpit observer seamlessly runs in mode='radar' and keeps
     moving the remote Pulsar with velocity while map slot independently confirms remote samples.
  3. Stage 3 (Five-second connection-loss limit): Remote pilot stops publishing + transport_lost ->
     remote Pulsar extrapolates smoothly up to 5.0 s, hits stale_5s cutoff, and holds 0.0000 m drift
     with 0 extra writes across post-cutoff probes.
  4. Stage 4 (Strict generation checks + reacquisition): Revokes gen1 via END, proves wrong & revoked
     generations are rejected, re-admits remote pilot on fresh gen2 >100 m ahead -> verifies single
     authorized post-loss alignment (confirmed by later alignment_readback), refuses >50 m guard probe,
     and resumes ordinary <=50 m continuous playback in untargeted radar mode.
  5. Stage 5 (Independent final MD + FFI readback): Confirms identity, sector, total displacement,
     delayed readback error bounds, and own_disp_m == 0.0000 m.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tools' / 'dev')]

EXTENSION_ID = 'x4_neon_company_cockpit'
LOGFILE = 'neon-company-cockpit.log'
EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
SESSION = 'a' * 32
NONCE = 'b' * 32

SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
STARTF_USESHOWWINDOW = 0x00000001
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

UI_FILES = [
    ('authority_registry_resolve_string.lua', 'tools/dev/authority_registry_resolve_string.lua'),
    ('motion_buffer.lua', 'src/authority/lua/motion_buffer.lua'),
    ('motion_playback.lua', 'src/authority/lua/motion_playback.lua'),
    ('company_hull_playback.lua', 'src/authority/lua/company_hull_playback.lua'),
    ('company_hull_playback_x4.lua', 'src/authority/lua/company_hull_playback_x4.lua'),
    ('company_cockpit_observer.lua', 'src/authority/lua/company_cockpit_observer.lua'),
    ('company_cockpit_playback.lua', 'src/authority/lua/company_cockpit_playback.lua'),
    ('company_observer_stream.lua', 'src/authority/lua/company_observer_stream.lua'),
    ('authority_company_cockpit_x4.lua', 'tools/dev/authority_company_cockpit_x4.lua'),
]
MD_FILES = [
    ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
    ('company_cockpit_observer.xml', 'src/authority/md/company_cockpit_observer.xml'),
    ('company_observer_target.xml', 'src/authority/md/company_observer_target.xml'),
    ('authority_company_cockpit_probe.xml', 'tools/dev/authority_company_cockpit_probe.xml'),
]
X4LAN_NET_DLL = ROOT / 'src/lockstep/base/native/x4lan_net_64.dll'

CONTENT_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<content id="{EXTENSION_ID}" name="Company cockpit playback probe" '
    'description="Disposable opt-in Company cockpit playback verification" '
    'author="Neon" version="100" date="2026-11-24" save="false">\n'
    '  <dependency version="900" />\n'
    '</content>\n'
)


def compose_extension(extensions_dir, *, profile, gamestart, epoch, udp_port, udp_lease, udp_generation):
    target = Path(extensions_dir) / EXTENSION_ID
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    (target / 'ui').mkdir()
    (target / 'md').mkdir()
    (target / 'native').mkdir()
    shutil.copyfile(X4LAN_NET_DLL, target / 'native' / 'x4lan_net_64.dll')
    for dst_name, rel_src in MD_FILES:
        shutil.copyfile(ROOT / rel_src, target / 'md' / dst_name)
    for dst_name, rel_src in UI_FILES:
        src = ROOT / rel_src
        if dst_name == 'motion_buffer.lua':
            text = src.read_text(encoding='utf-8').replace('\r\n', '\n')
            idx = text.rfind('return M')
            assert idx >= 0
            text = text[:idx] + 'X4LAN_MOTION_BUFFER = M\n' + text[idx:]
            (target / 'ui' / dst_name).write_text(text, encoding='utf-8')
        else:
            shutil.copyfile(src, target / 'ui' / dst_name)
    cfg_text = (
        'X4NEON_COMPANY_COCKPIT = {\n'
        '  enabled = true,\n'
        f'  extension_id = "{EXTENSION_ID}",\n'
        f'  profile = "{int(profile)}",\n'
        f'  gamestart = "{gamestart}",\n'
        f'  epoch = "{epoch}",\n'
        f'  nonce = "{NONCE}",\n'
        f'  udp_port = {int(udp_port)},\n'
        f'  udp_lease = "{udp_lease}",\n'
        f'  udp_generation = "{udp_generation}",\n'
        '}\n'
    )
    (target / 'ui' / 'config.lua').write_text(cfg_text, encoding='ascii')
    ui_entries = ''.join(
        ['    <file name="ui/config.lua" />\n']
        + [f'    <file name="ui/{dst}" />\n' for dst, _ in UI_FILES]
    )
    ui_doc = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<addon name="{EXTENSION_ID}">\n'
        '  <environment type="menus">\n'
        '    <dependency name="ego_detailmonitor" />\n'
        f'{ui_entries}'
        '  </environment>\n'
        '</addon>\n'
    )
    (target / 'ui.xml').write_text(ui_doc, encoding='utf-8')
    (target / 'content.xml').write_text(CONTENT_XML, encoding='utf-8')
    return target


def tree_metadata(path):
    path = Path(path)
    result = {}
    if not path.exists():
        return result
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            full = Path(dirpath) / name
            info = full.lstat()
            result[str(full.relative_to(path))] = [info.st_size, info.st_mtime_ns]
    return result


def parse_kv(text):
    fields = {}
    for token in text.split():
        if '=' in token:
            k, v = token.split('=', 1)
            fields[k] = v
    return fields


def x4_running():
    out = subprocess.run(
        ['tasklist', '/FI', 'IMAGENAME eq X4.exe', '/FO', 'CSV', '/NH'],
        capture_output=True, text=True, check=True,
    ).stdout
    return 'x4.exe' in out.lower()


def make_windows_pid_udp_validator(process):
    from authority.windows_motion_peer import _AF_INET, _get_extended_udp_table, _load_udp_table_function

    fn = _load_udp_table_function()
    pid = int(process.pid)
    bound_addr = {}

    def validate(peer):
        if process.poll() is not None:
            return False
        if not (isinstance(peer, tuple) and len(peer) == 2 and peer[0] == '127.0.0.1'
                and isinstance(peer[1], int) and 1 <= peer[1] <= 65535):
            return False
        try:
            rows = _get_extended_udp_table(_AF_INET, fn)
        except OSError:
            return False
        matches = [r for r in rows if r[1] == peer[1] and r[2] == pid and r[0] in ('127.0.0.1', '0.0.0.0')]
        if len(matches) == 1:
            bound_addr.update(local_ip=matches[0][0], port=matches[0][1], pid=pid)
            return True
        return False

    validate.info = bound_addr
    return validate


def attach_kill_on_close_job(pid):
    if os.name != 'nt':
        return None
    try:
        kernel32 = ctypes.windll.kernel32

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ('r', 'w', 'o', 'rt', 'wt', 'ot')]

        class _BL(ctypes.Structure):
            _fields_ = [
                ('a', ctypes.c_int64), ('b', ctypes.c_int64), ('LimitFlags', wintypes.DWORD),
                ('c', ctypes.c_size_t), ('d', ctypes.c_size_t), ('e', ctypes.c_uint32),
                ('f', ctypes.c_size_t), ('g', wintypes.DWORD), ('h', wintypes.DWORD),
            ]

        class _EL(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', _BL), ('IoInfo', _IO),
                ('i', ctypes.c_size_t), ('j', ctypes.c_size_t),
                ('k', ctypes.c_size_t), ('l', ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EL()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
        proc = kernel32.OpenProcess(0x001F0FFF, False, int(pid))
        if proc:
            kernel32.AssignProcessToJobObject(job, proc)
            kernel32.CloseHandle(proc)
        return job
    except BaseException:
        return None


def find_window(pid):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            if area > 0:
                found.append((area, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return max(found)[1] if found else None


def tuck_window_bottom_corner(hwnd):
    user32 = ctypes.windll.user32
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    win_w = max(100, rect.right - rect.left)
    win_h = max(100, rect.bottom - rect.top)
    user32.SetWindowPos(
        hwnd, HWND_BOTTOM, max(0, screen_w - win_w - 8), max(0, screen_h - win_h - 48), 0, 0,
        SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def remove_view_patiently(game_mod, view, attempts=20, delay=1.5):
    if not view.exists():
        return
    try:
        game_mod.remove_view(view)
        return
    except PermissionError:
        pass
    for _ in range(attempts):
        time.sleep(delay)
        if game_mod.is_link_or_reparse(view) or any(view.iterdir()):
            raise OSError(f'{view} is not empty')
        try:
            os.rmdir(view)
            return
        except PermissionError:
            continue
    raise OSError(f'{view} stayed in use')


class X4CockpitBridge:
    def __init__(self, process, log_path, ext_dir, *, timeout=60.0):
        self.process = process
        self.log_path = Path(log_path)
        self.ext_dir = Path(ext_dir)
        self.timeout = float(timeout)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._next_seq = 1
        self.ready_info = None
        self.responses = {}
        self.events = []
        self.error = None
        self.done = False
        self._hwnd = None
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self):
        position = 0
        pending = ''
        last_tuck = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if self._hwnd is None:
                self._hwnd = find_window(self.process.pid)
                if self._hwnd is not None:
                    tuck_window_bottom_corner(self._hwnd)
                    last_tuck = now
            elif now - last_tuck >= 0.75:
                tuck_window_bottom_corner(self._hwnd)
                last_tuck = now

            if self.log_path.is_file():
                try:
                    with self.log_path.open('rb') as handle:
                        handle.seek(position)
                        chunk = handle.read()
                        position += len(chunk)
                except OSError:
                    chunk = b''
                if chunk:
                    pending += chunk.decode('utf-8', errors='replace')
                    lines, _, pending = pending.rpartition('\n')
                    with self._cond:
                        for line in lines.splitlines():
                            if '[X4LAN] AUTH_COMPANY_COCKPIT ' not in line:
                                continue
                            payload = line.split('[X4LAN] AUTH_COMPANY_COCKPIT ', 1)[1].strip()
                            self.events.append(payload)
                            if payload.startswith('READY '):
                                self.ready_info = parse_kv(payload[len('READY '):])
                                self._cond.notify_all()
                            elif payload.startswith('RESP '):
                                kv = parse_kv(payload[len('RESP '):])
                                self.responses[int(kv['seq'])] = kv
                                self._cond.notify_all()
                            elif payload.startswith('ERROR '):
                                self.error = payload[len('ERROR '):]
                                self._cond.notify_all()
                            elif payload == 'DONE':
                                self.done = True
                                self._cond.notify_all()
            if self.process.poll() is not None:
                with self._cond:
                    if not self.done and self.error is None:
                        self.error = f'X4 exited unexpectedly with code {self.process.returncode}'
                    self._cond.notify_all()
                return
            time.sleep(0.04)

    def wait_ready(self, timeout=180.0, *, pump=None):
        deadline = time.monotonic() + timeout
        while True:
            if pump is not None:
                pump()
            with self._cond:
                if self.ready_info is not None:
                    return dict(self.ready_info)
                if self.error is not None:
                    raise RuntimeError(f'X4 error before READY: {self.error}')
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError('timed out waiting for X4 READY')
                self._cond.wait(min(0.05 if pump else 0.25, rem))

    def _lua_val(self, v):
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


class _QueuedPilotSource:
    def __init__(self, epoch, index, local_generation):
        self.registry = (epoch, index)
        self.local_generation = local_generation
        self.items = []

    def poll(self, _validate):
        return self.items.pop(0) if self.items else None


def _dist3(a, b):
    return math.sqrt((a['x'] - b['x']) ** 2 + (a['y'] - b['y']) ** 2 + (a['z'] - b['z']) ** 2)


def run_live_cockpit_trial(view, profile_dir, out, args):
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.company_motion_source import CompanyMotionSource
    from lockstep.launcher import steam

    log_path = profile_dir / LOGFILE
    if log_path.exists():
        log_path.unlink()

    # Real production CompanyMotionSource (opt-in cockpit_radar_playback=True!) for receiving Pilot A (index 1).
    recv_source = CompanyMotionSource(playback_index=2, cockpit_radar_playback=True)
    recv_cfg = recv_source.configure(EPOCH, 1, operational_bits='111')
    service = CompanyMotionService()
    client_recv = client_remote = client_map = None
    process = job_handle = bridge = None
    try:
        ext_dir = compose_extension(
            view / 'extensions',
            profile=args.profile,
            gamestart=args.gamestart,
            epoch=EPOCH,
            udp_port=recv_cfg['port'],
            udp_lease=recv_cfg['lease'],
            udp_generation=recv_cfg['generation'],
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
        validator = make_windows_pid_udp_validator(process)

        bridge = X4CockpitBridge(process, log_path, ext_dir, timeout=60.0)
        ready = bridge.wait_ready(timeout=args.timeout)
        remote_sector = ready['remote_sector']
        map_sector = ready['map_sector']
        r0 = {'x': float(ready['remote_x']), 'y': float(ready['remote_y']), 'z': float(ready['remote_z'])}

        # Admit Receiving Pilot A (1), Remote Company Pilot B (2), and Map Company Pilot C (3) on real CompanyMotionService!
        grant_a = service.admit(1, 'a1' * 16, 1, remote_sector, SESSION, EPOCH)
        grant_b1 = service.admit(2, 'b1' * 16, 2, remote_sector, SESSION, EPOCH)
        grant_c = service.admit(3, 'c1' * 16, 3, map_sector, SESSION, EPOCH)
        service.set_running(True)

        observer_a = recv_source.observe(validator)
        client_recv = CompanyMotionClient(
            service.port, grant_a, recv_source, validator,
            on_snapshot=observer_a.snapshot, on_end=observer_a.end,
            on_transport_lost=observer_a.transport_lost, on_view_reset=observer_a.reset_world,
            trial_index=2,
        )
        observer_a.bind_views(client_recv.set_views)

        src_b1 = _QueuedPilotSource(EPOCH, 2, '1' * 32)
        src_c = _QueuedPilotSource(EPOCH, 3, '3' * 32)
        client_remote = CompanyMotionClient(service.port, grant_b1, src_b1, lambda _: True)
        client_map = CompanyMotionClient(service.port, grant_c, src_c, lambda _: True)

        def pump_all():
            service.poll()
            client_recv.poll()
            if client_remote is not None:
                client_remote.poll()
            if client_map is not None:
                client_map.poll()

        def pump_until(pred, timeout=15.0, label='condition'):
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

        pump_until(lambda: client_recv._running and client_remote._running and client_map._running, label='clients_running')

        # The playback IPC timestamp and the native UDP module use QPC.
        r_start = bridge.request('start_stream', pump=pump_all, clock_base=time.perf_counter())
        assert r_start.get('status') == 'stream_started'

        # ==================== STAGE 1: Selected HUD Target Playback (mode='hud') ====================
        r_lock = bridge.request('lock_hud', pump=pump_all)
        assert r_lock.get('status') == 'hud_locked' and r_lock.get('locked') == 'true', f'HUD lock failed: {r_lock}'
        pump_until(lambda: 2 in (client_recv._sent_view or {}).get(remote_sector, ()), label='stage1_hud_view_subscribed')

        # Recorded/loopback remote pilot trajectory: constant velocity (18, 4, 12) m/s (speed 22.0 m/s) at 10 Hz!
        vx, vy, vz = 18.0, 4.0, 12.0
        dt = 0.10
        sim_t = 0.0
        seq_b = 0

        def push_remote(source_obj, *, with_vel=True, offset_override=None):
            nonlocal sim_t, seq_b
            seq_b += 1
            sim_t += dt
            if offset_override is not None:
                px, py, pz = offset_override
            else:
                px = r0['x'] + vx * sim_t
                py = r0['y'] + vy * sim_t
                pz = r0['z'] + vz * sim_t
            source_obj.items.append({
                'version': 3, 'epoch': EPOCH, 'generation': source_obj.local_generation,
                'index': 2, 'sequence': seq_b, 'sector_macro': remote_sector,
                'source_time': 100.0 + sim_t, 'capture_time': 500.0 + sim_t,
                'x': px, 'y': py, 'z': pz,
                'real_velocity': {'x': vx, 'y': vy, 'z': vz} if with_vel else None,
            })
            return {'x': px, 'y': py, 'z': pz, 'sequence': seq_b}

        for i in range(25):
            push_remote(src_b1, with_vel=(i > 0))
            pump_for(dt)
        pump_for(0.25)

        stage1_probe = bridge.request('probe_state', pump=pump_all)
        stage1_pose = {k: float(stage1_probe[k]) for k in ('x', 'y', 'z')}
        stage1_disp_m = _dist3(r0, stage1_pose)
        assert stage1_probe.get('mode') == 'hud', f'expected hud mode: {stage1_probe}'
        assert stage1_probe.get('has_target') == 'true'
        assert int(stage1_probe['writes']) > 10 and int(stage1_probe['readbacks']) > 10
        assert stage1_disp_m > 30.0, f'expected HUD target movement >30 m: {stage1_disp_m}'
        assert float(stage1_probe['own_disp_m']) < 0.01

        # ==================== STAGE 2: Nearby Radar-Visible Pilot WITHOUT Target Lock + Independent Map ====================
        r_unlock = bridge.request('unlock_hud', pump=pump_all)
        assert r_unlock.get('status') == 'hud_unlocked' and r_unlock.get('has_target') == 'false'
        r_map = bridge.request('set_map_enabled', pump=pump_all, enabled=True)
        assert r_map.get('status') == 'map_set'

        pump_until(lambda: (
            2 in (client_recv._sent_view or {}).get(remote_sector, ())
            and 3 in (client_recv._sent_view or {}).get(map_sector, ())
        ), label='stage2_radar_and_map_views')

        # Stream 20 more samples to remote Pulsar (untargeted radar mode!) AND 2 samples to independent map ship!
        for seq_c in (1, 2):
            src_c.items.append({
                'version': 3, 'epoch': EPOCH, 'generation': src_c.local_generation,
                'index': 3, 'sequence': seq_c, 'sector_macro': map_sector,
                'source_time': 100.0 + seq_c * 0.2, 'capture_time': 500.0 + seq_c * 0.2,
                'x': 10000.0 + seq_c * 5.0, 'y': 0.0, 'z': 10000.0,
                'real_velocity': None if seq_c == 1 else {'x': 25.0, 'y': 0.0, 'z': 0.0},
            })
        for _ in range(20):
            push_remote(src_b1, with_vel=True)
            pump_for(dt)
        pump_for(0.25)

        stage2_probe = bridge.request('probe_state', pump=pump_all)
        stage2_pose = {k: float(stage2_probe[k]) for k in ('x', 'y', 'z')}
        stage2_extra_disp_m = _dist3(stage1_pose, stage2_pose)
        obs_stats_stage2 = recv_source.observer_stats()
        assert stage2_probe.get('mode') == 'radar', f'expected untargeted radar mode: {stage2_probe}'
        assert stage2_probe.get('has_target') == 'false', 'HUD target must be cleared in stage 2!'
        assert stage2_extra_disp_m > 30.0, f'expected radar-mode movement >30 m: {stage2_extra_disp_m}'
        assert obs_stats_stage2['slots']['map']['samples_confirmed'] >= 1, f'expected independent map confirmation: {obs_stats_stage2}'
        assert float(stage2_probe['own_disp_m']) < 0.01

        # Reject mismatched generation while track is active!
        cockpit_serial_now = obs_stats_stage2['slots']['cockpit']['last_confirmed']['serial']
        r_bad_gen = bridge.request('inject_generation', pump=pump_all, generation='9' * 32,
                                   serial=cockpit_serial_now, sample_seq=999)
        assert r_bad_gen.get('ok') == 'false' and r_bad_gen.get('reason') == 'generation_mismatch', f'bad gen check failed: {r_bad_gen}'

        # ==================== STAGE 3: Five-Second Connection-Loss Limit ====================
        # Simulate transport loss of remote pilot (keeps local 5.0 s prediction running!).
        client_remote.close()
        client_remote = None
        service.transport_lost(2)
        loss_t0 = time.monotonic()
        loss_probes = []
        for label, target_s in (('1.0s_predicting', 1.0), ('4.2s_predicting', 4.2),
                                ('5.5s_post_cutoff', 5.5), ('6.3s_post_cutoff_hold', 6.3)):
            while time.monotonic() - loss_t0 < target_s:
                pump_all()
                time.sleep(0.01)
            p = bridge.request('probe_state', pump=pump_all)
            loss_probes.append({
                'label': label,
                'elapsed_s': round(time.monotonic() - loss_t0, 3),
                'mode': p['mode'],
                'has_target': p['has_target'],
                'x': float(p['x']), 'y': float(p['y']), 'z': float(p['z']),
                'writes': int(p['writes']),
                'readbacks': int(p['readbacks']),
                'stale_cutoffs': int(p['stale_cutoffs']),
                'own_disp_m': float(p['own_disp_m']),
            })
        pred_move_m = _dist3(loss_probes[0], loss_probes[1])
        post_cutoff_drift_m = _dist3(loss_probes[2], loss_probes[3])
        post_cutoff_extra_writes = loss_probes[3]['writes'] - loss_probes[2]['writes']
        assert pred_move_m > 50.0, f'expected 5s extrapolation >50 m: {pred_move_m}'
        assert loss_probes[3]['stale_cutoffs'] >= 1, f'expected 5s cutoff: {loss_probes}'
        assert post_cutoff_extra_writes == 0 and post_cutoff_drift_m < 0.01, (
            f'hull moved after 5s cutoff: writes={post_cutoff_extra_writes}, drift={post_cutoff_drift_m}'
        )

        # ==================== STAGE 4: Revocation + Fresh Generation Reacquisition (>50 m alignment + ordinary playback) ====================
        # Re-admit remote pilot on fresh generation gen2 (admit() revokes old route & sends END for old generation!).
        grant_b2 = service.admit(4, 'b2' * 16, 2, remote_sector, SESSION, EPOCH)
        src_b2 = _QueuedPilotSource(EPOCH, 2, '2' * 32)
        client_remote = CompanyMotionClient(service.port, grant_b2, src_b2, lambda _: True)
        pump_until(lambda: client_remote._running, label='stage4_gen2_running')
        pump_for(0.15)

        # Confirm late packet carrying revoked gen1 is rejected!
        r_revoked = bridge.request('inject_generation', pump=pump_all, generation=grant_b1['generation'],
                                   serial=cockpit_serial_now, sample_seq=998)
        assert r_revoked.get('ok') == 'false' and r_revoked.get('reason') == 'revoked_generation', f'revoked gen check failed: {r_revoked}'

        # Fresh gen2 starts 110 m ahead of frozen 5s pose (single authorized alignment + later readback, then ordinary <=50 m playback!)
        frozen = {'x': loss_probes[3]['x'], 'y': loss_probes[3]['y'], 'z': loss_probes[3]['z']}
        align_origin = (frozen['x'] + 90.0, frozen['y'] + 20.0, frozen['z'] + 60.0)  # ~110 m > 50 m
        seq_b = 0
        sim_t = 0.0
        # Sample 1: baseline at align_origin (triggers single authorized alignment!)
        push_remote(src_b2, with_vel=False, offset_override=align_origin)
        pump_for(0.25)

        # Samples 2..20: ordinary <=50 m continuous playback at 22 m/s from align_origin (still untargeted radar mode!)
        last_sent = None
        r_50m = None
        for k in range(1, 20):
            t_k = k * dt
            pos_k = (align_origin[0] + vx * t_k, align_origin[1] + vy * t_k, align_origin[2] + vz * t_k)
            last_sent = push_remote(src_b2, with_vel=True, offset_override=pos_k)
            pump_for(dt)
            if k == 6:
                # Probe active >50 m guard refusal (read-only) while resumed ordinary playback is active!
                r_50m = bridge.request('probe_50m', pump=pump_all, offset_m=120.0)
                assert r_50m.get('ok') == 'false' and r_50m.get('reason') == 'correction_out_of_bounds', f'50m refusal failed: {r_50m}'
        pump_for(0.25)

        # ==================== STAGE 5: Independent Final MD + FFI Readback ====================
        final_rb = bridge.request('readback', pump=pump_all, min_game_delay=0.20)
        assert final_rb.get('status') == 'readback', f'final readback failed: {final_rb}'
        final_pose = {k: float(final_rb[k]) for k in ('x', 'y', 'z')}
        total_disp_m = _dist3(r0, final_pose)
        post_align_disp_m = _dist3({'x': align_origin[0], 'y': align_origin[1], 'z': align_origin[2]}, final_pose)
        assert post_align_disp_m > 30.0, f'expected resumed ordinary playback >30 m after alignment: {post_align_disp_m}'
        assert int(final_rb['alignments']) == 1
        assert int(final_rb['gen_rejects']) >= 2
        assert int(final_rb['refusals_50m']) >= 1
        assert float(final_rb['own_disp_m']) < 0.01

        done_resp = bridge.request('done', pump=pump_all)
        assert done_resp.get('status') == 'done'

        return {
            'accepted': True,
            'evidence_source': 'recorded_loopback_remote_pilot_driving_real_native_windows_x4',
            'ships': {
                'own': {'index': 1, 'code': ready['own_code'], 'sector': remote_sector},
                'remote': {'index': 2, 'code': ready['remote_code'], 'hex': ready['remote_hex'],
                           'sector': remote_sector, 'initial_pose': r0},
                'map': {'index': 3, 'code': ready['map_code'], 'sector': map_sector},
            },
            'udp_peer_validation': dict(validator.info),
            'stage1_hud_target': {
                'lock_resp': r_lock,
                'probe': stage1_probe,
                'displacement_m': round(stage1_disp_m, 3),
            },
            'stage2_untargeted_radar_and_independent_map': {
                'unlock_resp': r_unlock,
                'probe': stage2_probe,
                'additional_displacement_m': round(stage2_extra_disp_m, 3),
                'map_slot_confirmed': obs_stats_stage2['slots']['map'],
                'mismatched_gen_check': r_bad_gen,
            },
            'stage3_5s_loss_limit': {
                'predicted_displacement_1s_to_4_2s_m': round(pred_move_m, 3),
                'post_cutoff_drift_5_5s_to_6_3s_m': round(post_cutoff_drift_m, 4),
                'post_cutoff_extra_writes': post_cutoff_extra_writes,
                'probes': loss_probes,
            },
            'stage4_generation_and_reacquisition': {
                'revoked_gen1_check': r_revoked,
                'over_50m_guard_check': r_50m,
                'reacquire_gap_m': round(_dist3(frozen, {'x': align_origin[0], 'y': align_origin[1], 'z': align_origin[2]}), 3),
                'post_align_resumed_displacement_m': round(post_align_disp_m, 3),
                'last_sent_sample': last_sent,
            },
            'stage5_independent_final_readback': {
                'md_ffi_readback': final_rb,
                'total_hull_displacement_m': round(total_disp_m, 3),
                'own_ship_displacement_m': float(final_rb['own_disp_m']),
            },
            'observer_final_stats': recv_source.observer_stats(),
        }
    finally:
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
        for client in (client_recv, client_remote, client_map):
            if client is not None:
                client.close()
        service.close()
        recv_source.close()
        if log_path.is_file():
            shutil.copyfile(log_path, out / LOGFILE)


def main(argv=None):
    from lockstep.launcher import game as game_mod, profile as profile_mod, steam

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--game')
    parser.add_argument('--profile', type=int, default=45850)
    parser.add_argument('--gamestart', default='custom_creative')
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--timeout', type=float, default=240.0)
    args = parser.parse_args(argv)

    if args.profile not in profile_mod.PROFILES:
        raise SystemExit(f'Profile {args.profile} is outside disposable launcher lanes')
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f'{out} already exists; use a fresh output directory')
    if x4_running():
        raise SystemExit('X4.exe is running; deferring to avoid disturbing user session')
    ok, msg = steam.status()
    if not ok:
        raise SystemExit(msg)
    game = Path(args.game) if args.game else game_mod.find_install()
    game_mod.verify_exe(game)
    view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-cockpit-{out.name}'
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
    result = {'options': vars(args), 'game': str(game), 'view': str(view), 'outcome': 'started'}
    ctypes.windll.user32.SetProcessDPIAware()
    try:
        game_mod.create_view(game, view)
        profile_dir = profile_mod.prepare(
            args.profile,
            [*game_mod.dlc_ids(view), EXTENSION_ID],
            width=args.width, height=args.height, fast=True,
        )
        result['native_cockpit_trial'] = run_live_cockpit_trial(view, profile_dir, out, args)
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
        result['protected_unchanged'] = (protected_after == protected_before)
        result['view_removed'] = not view.exists()
        result['x4_running_after'] = x4_running()
        (out / 'result.json').write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps({
        'outcome': result['outcome'],
        'accepted': result.get('native_cockpit_trial', {}).get('accepted'),
        'protected_unchanged': result['protected_unchanged'],
        'view_removed': result['view_removed'],
        'x4_running_after': result['x4_running_after'],
        'out': str(out),
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
