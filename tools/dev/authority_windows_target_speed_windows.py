"""Isolated native Windows X4 9.00 target-speed & HUD investigation harness.

Runs unattended in background (800x600 windowed fast=True SW_SHOWNOACTIVATE HWND_BOTTOM,
disposable profile 45850, isolated x4ls view, shared local run lockfile, packaged
x4lan_net_64.dll with udp.monotonic()).

Investigation stages (target ship locked in stock HUD via C.SetSofttarget throughout):
  0. Disposable save (save_009.xml.gz) & stationary HUD baseline (C.GetSofttarget2,
     C.GetComponentDetails with resolved connectionName AND empty "", GetPlayerSpeed, ScreenPos)
  1. Position-only control (SetObjectSectorPos at 60 m/s without native velocity -> speed 0)
  2. Forward motion (+60 m/s along ship forward): LAN constantvel=true AND constantvel=false
  3. Pure lateral (+60 m/s along right), diagonal (+60 fwd + 60 lat = 84.85 m/s), reverse (-60 m/s)
  4. Uncoupled heading turns (+12, +30, +90 deg via SetObjectSectorPos with +1.5 m lateral shift)
     + Snap-back hazard control (release velocity without pose anchor)
  5. Real unmodified loopback CompanyMotionService V4 stream (REMOTE4/SEEN4) driving coupled
     turning (0 -> 30 deg) + speed ramp (60 -> 90 m/s) + real 5.0 s packet gap + safe
     anchor -> zero -> stop -> release_readback via X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY
  6. Final independent MD + FFI readback, DWM window screenshots, and strict hygiene checks.
"""
import argparse
import ctypes
from ctypes import wintypes
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tools' / 'dev')]

import authority_company_cockpit_windows as cockpit  # noqa: E402

EXTENSION_ID = 'x4_neon_target_speed'
LOGFILE = 'neon-target-speed.log'
SAVE_SLOT = 'save_009'
LOCKFILE = Path(r'C:\Users\Observer\Documents\Gemini Projects\.x4-native-test.lock')
EPOCH = '7c5e64178ebb04ec5bb85fdb95b5a499'
SESSION = 'a' * 32
NONCE = 'd' * 32

PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0

UI_FILES = [
    ('authority_registry_resolve_string.lua', 'tools/dev/authority_registry_resolve_string.lua'),
    ('company_cockpit_observer.lua', 'src/authority/lua/company_cockpit_observer.lua'),
    ('company_observer_stream.lua', 'src/authority/lua/company_observer_stream.lua'),
    ('company_cockpit_native_velocity.lua', 'src/authority/lua/company_cockpit_native_velocity.lua'),
    ('authority_windows_target_speed_x4.lua', 'tools/dev/authority_windows_target_speed_x4.lua'),
]
MD_FILES = [
    ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
    ('company_cockpit_observer.xml', 'src/authority/md/company_cockpit_observer.xml'),
    ('company_cockpit_native_velocity.xml', 'src/authority/md/company_cockpit_native_velocity.xml'),
    ('authority_windows_target_speed_probe.xml', 'tools/dev/authority_windows_target_speed_probe.xml'),
]

CONTENT_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<content id="{EXTENSION_ID}" name="Target-speed native velocity investigation" '
    'description="Disposable opt-in Windows X4 target-speed and native velocity verification" '
    'author="Neon" version="100" date="2026-11-27" save="false">\n'
    '  <dependency version="900" />\n'
    '</content>\n'
)


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == 259  # STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)


def acquire_lockfile(profile):
    if LOCKFILE.exists():
        try:
            data = json.loads(LOCKFILE.read_text(encoding='utf-8'))
            other_pid = int(data.get('pid', 0))
        except Exception:
            other_pid = 0
        if cockpit.x4_running() or pid_alive(other_pid):
            raise SystemExit(f'Active lockfile held at {LOCKFILE}; refusing launch')
        LOCKFILE.unlink()
    payload = {
        'agent': 'Neon',
        'pid': os.getpid(),
        'profile': int(profile),
        'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with LOCKFILE.open('x', encoding='utf-8') as handle:
        json.dump(payload, handle)


def release_lockfile():
    try:
        if LOCKFILE.exists():
            data = json.loads(LOCKFILE.read_text(encoding='utf-8'))
            if int(data.get('pid', -1)) == os.getpid():
                LOCKFILE.unlink()
    except Exception:
        pass


def pe_file_version(exe_path):
    """Read Windows VS_FIXEDFILEINFO version string from X4.exe."""
    version = ctypes.WinDLL('version')
    path_str = str(exe_path)
    dummy = wintypes.DWORD(0)
    size = version.GetFileVersionInfoSizeW(path_str, ctypes.byref(dummy))
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path_str, 0, size, buf):
        return None
    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(buf, '\\', ctypes.byref(ptr), ctypes.byref(length)):
        return None

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [
            ('dwSignature', wintypes.DWORD),
            ('dwStrucVersion', wintypes.DWORD),
            ('dwFileVersionMS', wintypes.DWORD),
            ('dwFileVersionLS', wintypes.DWORD),
            ('dwProductVersionMS', wintypes.DWORD),
            ('dwProductVersionLS', wintypes.DWORD),
        ]

    info = VS_FIXEDFILEINFO.from_address(ptr.value)
    return (
        f'{info.dwFileVersionMS >> 16}.{info.dwFileVersionMS & 0xFFFF}.'
        f'{info.dwFileVersionLS >> 16}.{info.dwFileVersionLS & 0xFFFF}'
    )


def _png_chunk(chunk_type, data):
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack('>I', len(data)) + chunk_type + data + struct.pack('>I', crc)


def write_png_rgb(path, width, height, rgb_rows):
    raw = b''.join(b'\x00' + row for row in rgb_rows)
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    png = (
        b'\x89PNG\r\n\x1a\n'
        + _png_chunk(b'IHDR', ihdr)
        + _png_chunk(b'IDAT', zlib.compress(raw, level=6))
        + _png_chunk(b'IEND', b'')
    )
    Path(path).write_bytes(png)
    return hashlib.sha256(png).hexdigest()


def capture_window_png(hwnd, dest_path):
    """Capture a window's composited content via DWM PrintWindow without raising/focusing it."""
    if not hwnd:
        return {'captured': False, 'reason': 'no_hwnd'}
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return {'captured': False, 'reason': 'get_client_rect_failed'}
    w = max(1, rect.right - rect.left)
    h = max(1, rect.bottom - rect.top)
    wrect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wrect))
    ww = max(w, wrect.right - wrect.left)
    wh = max(h, wrect.bottom - wrect.top)

    hdc_win = user32.GetWindowDC(hwnd)
    if not hdc_win:
        return {'captured': False, 'reason': 'get_window_dc_failed'}
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, ww, wh)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        if not ok:
            ok = user32.PrintWindow(hwnd, hdc_mem, 0)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ('biSize', wintypes.DWORD),
                ('biWidth', wintypes.LONG),
                ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD),
                ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD),
                ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG),
                ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = ww
        bmi.biHeight = -wh  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(ww * wh * 4)
        lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, wh, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        if not lines:
            return {'captured': False, 'reason': 'get_dibits_failed'}
        raw = buf.raw
        rgb_rows = []
        non_black = 0
        luma_sum = 0
        total_px = ww * wh
        stride = ww * 4
        for y in range(wh):
            row = raw[y * stride:(y + 1) * stride]
            rgb = bytearray(ww * 3)
            # BGRA -> RGB
            rgb[0::3] = row[2::4]
            rgb[1::3] = row[1::4]
            rgb[2::3] = row[0::4]
            rgb_rows.append(bytes(rgb))
            # Sample every 16th pixel for quick brightness stats
            for x in range(0, stride, 64):
                b, g, r = row[x], row[x + 1], row[x + 2]
                if r or g or b:
                    non_black += 1
                luma_sum += (r + g + b)
        sampled = max(1, wh * ((stride + 63) // 64))
        sha = write_png_rgb(dest_path, ww, wh, rgb_rows)
        return {
            'captured': True,
            'printwindow_ok': bool(ok),
            'path': str(dest_path),
            'width': ww,
            'height': wh,
            'sha256': sha,
            'non_black_ratio': round(non_black / sampled, 4),
            'mean_rgb': round(luma_sum / (3.0 * sampled), 2),
        }
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)


def compose_extension(extensions_dir, *, profile, gamestart, epoch, udp_port, udp_lease, udp_generation,
                      network_dll_bytes, width, height):
    target = Path(extensions_dir) / EXTENSION_ID
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    (target / 'ui').mkdir()
    (target / 'md').mkdir()
    (target / 'native').mkdir()
    (target / 'native' / 'x4lan_net_64.dll').write_bytes(network_dll_bytes)
    for dst_name, rel_src in MD_FILES:
        shutil.copyfile(ROOT / rel_src, target / 'md' / dst_name)
    for dst_name, rel_src in UI_FILES:
        shutil.copyfile(ROOT / rel_src, target / 'ui' / dst_name)
    cfg_text = (
        'X4NEON_TARGET_SPEED = {\n'
        '  enabled = true,\n'
        f'  extension_id = "{EXTENSION_ID}",\n'
        f'  profile = "{int(profile)}",\n'
        f'  gamestart = "{gamestart}",\n'
        f'  save_slot = "{SAVE_SLOT}",\n'
        f'  width = {int(width)},\n'
        f'  height = {int(height)},\n'
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


class TargetSpeedBridge:
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
        self.vel_events = []
        self.error = None
        self.done = False
        self.hwnd = None
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self):
        position = 0
        pending = ''
        last_tuck = 0.0
        tag = '[X4LAN] AUTH_TARGET_SPEED '
        while not self._stop.is_set():
            now = time.monotonic()
            if self.hwnd is None:
                self.hwnd = cockpit.find_window(self.process.pid)
                if self.hwnd is not None:
                    cockpit.tuck_window_bottom_corner(self.hwnd)
                    last_tuck = now
            elif now - last_tuck >= 0.75:
                cockpit.tuck_window_bottom_corner(self.hwnd)
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
                            if tag not in line:
                                continue
                            payload = line.split(tag, 1)[1].strip()
                            self.events.append(payload)
                            if payload.startswith('READY '):
                                self.ready_info = cockpit.parse_kv(payload[len('READY '):])
                                self._cond.notify_all()
                            elif payload.startswith('RESP '):
                                kv = cockpit.parse_kv(payload[len('RESP '):])
                                self.responses[int(kv['seq'])] = kv
                                self._cond.notify_all()
                            elif payload.startswith('VEL_EVENT '):
                                self.vel_events.append(cockpit.parse_kv(payload[len('VEL_EVENT '):]))
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
            time.sleep(0.03)

    def wait_ready(self, timeout=240.0, *, pump=None):
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
                self._cond.wait(min(0.02 if pump else 0.15, rem))

    def stop(self):
        self._stop.set()
        self._thread.join(3)


def parse_vec(text):
    parts = [float(x) for x in text.split(',')]
    return {'x': parts[0], 'y': parts[1], 'z': parts[2]}


def vec_dist(a, b):
    return math.sqrt((a['x'] - b['x']) ** 2 + (a['y'] - b['y']) ** 2 + (a['z'] - b['z']) ** 2)


def scale_vec(v, s):
    return {'x': v['x'] * s, 'y': v['y'] * s, 'z': v['z'] * s}


def add_vec(a, b):
    return {'x': a['x'] + b['x'], 'y': a['y'] + b['y'], 'z': a['z'] + b['z']}


def count_project_errors(log_path):
    errors = []
    if not Path(log_path).is_file():
        return errors
    for line in Path(log_path).read_text(encoding='utf-8', errors='replace').splitlines():
        if '[=ERROR=]' not in line:
            continue
        if '[X4LAN]' in line:
            continue
        if EXTENSION_ID in line and 'Could not find signature file' in line:
            continue
        if any(k in line for k in (EXTENSION_ID, 'X4Authority', 'X4NEON', 'company_cockpit')):
            errors.append(line.strip())
    return errors


def run_investigation(view, profile_dir, out, args, network_dll_bytes):
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.company_motion_source import CompanyMotionSource
    from lockstep.launcher import steam

    log_path = profile_dir / LOGFILE
    if log_path.exists():
        log_path.unlink()
    frames_dir = out / 'frames'
    frames_dir.mkdir(parents=True, exist_ok=True)

    recv_source = CompanyMotionSource(playback_index=2, cockpit_radar_playback=True)
    recv_cfg = recv_source.configure(EPOCH, 1, operational_bits='111')
    service = CompanyMotionService()
    client_recv = client_remote = None
    process = job_handle = bridge = None

    def snap(name):
        return capture_window_png(bridge.hwnd if bridge else None, frames_dir / f'{name}.png')

    try:
        ext_dir = compose_extension(
            view / 'extensions',
            profile=args.profile,
            gamestart=args.gamestart,
            epoch=EPOCH,
            udp_port=recv_cfg['port'],
            udp_lease=recv_cfg['lease'],
            udp_generation=recv_cfg['generation'],
            network_dll_bytes=network_dll_bytes,
            width=args.width,
            height=args.height,
        )
        command = [
            str(view / 'X4.exe'),
            '-personalfolderid', str(args.profile),
            '-skipintro', '-noworkshopsync', '-nocputhrottle',
            '-debug', 'scripts', '-logfile', LOGFILE,
        ]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= cockpit.STARTF_USESHOWWINDOW
        si.wShowWindow = cockpit.SW_SHOWNOACTIVATE
        process = subprocess.Popen(command, cwd=str(view), env=steam.client_environment(), startupinfo=si)
        job_handle = cockpit.attach_kill_on_close_job(process.pid)
        validator = cockpit.make_windows_pid_udp_validator(process)
        bridge = TargetSpeedBridge(process, log_path, ext_dir, timeout=60.0)

        ready = bridge.wait_ready(timeout=args.timeout)
        save_path = profile_dir / 'save' / f'{SAVE_SLOT}.xml.gz'
        save_sha = hashlib.sha256(save_path.read_bytes()).hexdigest() if save_path.is_file() else None
        if save_path.is_file():
            shutil.copyfile(save_path, out / f'{SAVE_SLOT}.xml.gz')

        sector = ready['remote_sector']
        r0 = parse_vec(ready['remote_xyz'])
        basis_r = parse_vec(ready['right'])
        basis_u = parse_vec(ready['up'])
        basis_f = parse_vec(ready['forward'])

        def sample_md_vel(vel_op, *, vx=None, vy=None, vz=None, constantvel=None, min_game_delay=None, min_real_delay=None):
            return bridge.request(
                'direct_vel', vel_op=vel_op, vx=vx, vy=vy, vz=vz,
                constantvel=constantvel, min_game_delay=min_game_delay, min_real_delay=min_real_delay,
            )

        def safe_stop_direct():
            anc = bridge.request('anchor_current_pose')
            z = sample_md_vel('zero')
            s = sample_md_vel('stop', min_real_delay=0.08)
            rb = sample_md_vel('release_readback', min_real_delay=0.10)
            return {'anchor': anc, 'zero': z, 'stop': s, 'release_readback': rb}

        # ==================== STAGE 0: Stock HUD Lock & Stationary Baseline ====================
        lock0 = bridge.request('lock_hud')
        time.sleep(4.0)  # Let the 'Game successfully saved' banner fade so the HUD target bracket is unobstructed.
        hud0 = bridge.request('read_hud')
        md0 = bridge.request('md_readback')
        frame0 = snap('00_baseline_stationary')

        # ==================== STAGE 1: Position-Only Control (60 m/s without native velocity) ====================
        pos_only_samples = []
        p_start = parse_vec(md0['pos'])
        for k in range(1, 13):
            step_pos = add_vec(p_start, scale_vec(basis_f, 3.0 * k))
            bridge.request('set_pose', x=step_pos['x'], y=step_pos['y'], z=step_pos['z'],
                           yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0)
            time.sleep(0.05)
            if k in (4, 8, 12):
                pos_only_samples.append(bridge.request('md_readback'))
        frame1 = snap('01_position_only_control')
        pos_only_disp = vec_dist(p_start, parse_vec(pos_only_samples[-1]['pos']))

        # ==================== STAGE 2: Forward Motion (+60 m/s) constantvel=true vs constantvel=false ====================
        v_fwd60 = scale_vec(basis_f, 60.0)
        s2_base = sample_md_vel('baseline')
        s2_apply_true = sample_md_vel('apply', vx=v_fwd60['x'], vy=v_fwd60['y'], vz=v_fwd60['z'], constantvel=True)
        s2_true_reads = []
        for i in range(20):
            r = sample_md_vel('readback', min_real_delay=0.08)
            s2_true_reads.append(r)
            if i == 10:
                frame2a = snap('02a_forward_60mps_constvel_true')
        s2_true_disp = vec_dist(parse_vec(s2_apply_true['pos']), parse_vec(s2_true_reads[-1]['pos']))
        s2_true_dt = float(s2_true_reads[-1]['md_age']) - float(s2_apply_true['md_age'])
        s2_stop1 = safe_stop_direct()

        # Compare constantvel=false impulse on the same unoccupied ship
        s2_base2 = sample_md_vel('baseline')
        s2_apply_false = sample_md_vel('apply', vx=v_fwd60['x'], vy=v_fwd60['y'], vz=v_fwd60['z'], constantvel=False)
        s2_false_reads = []
        for i in range(16):
            r = sample_md_vel('readback', min_real_delay=0.08)
            s2_false_reads.append(r)
            if i == 8:
                frame2b = snap('02b_forward_60mps_constvel_false')
        s2_false_disp = vec_dist(parse_vec(s2_apply_false['pos']), parse_vec(s2_false_reads[-1]['pos']))
        s2_false_dt = float(s2_false_reads[-1]['md_age']) - float(s2_apply_false['md_age'])
        s2_stop2 = safe_stop_direct()

        # ==================== STAGE 3: Pure Lateral (+60), Diagonal (+60/+60), Reverse (-60) ====================
        v_lat60 = scale_vec(basis_r, 60.0)
        v_diag = add_vec(v_fwd60, v_lat60)
        v_rev60 = scale_vec(basis_f, -60.0)

        s3_base = sample_md_vel('baseline')
        # 3a: Pure lateral +60 m/s
        s3a_apply = sample_md_vel('apply', vx=v_lat60['x'], vy=v_lat60['y'], vz=v_lat60['z'], constantvel=True)
        s3a_reads = []
        for i in range(20):
            r = sample_md_vel('readback', min_real_delay=0.08)
            s3a_reads.append(r)
            if i == 10:
                frame3a = snap('03a_lateral_60mps')
        s3a_disp = vec_dist(parse_vec(s3a_apply['pos']), parse_vec(s3a_reads[-1]['pos']))
        s3a_dt = float(s3a_reads[-1]['md_age']) - float(s3a_apply['md_age'])

        # 3b: Diagonal (+60 fwd, +60 lat -> |v| = 84.85 m/s)
        s3b_apply = sample_md_vel('apply', vx=v_diag['x'], vy=v_diag['y'], vz=v_diag['z'], constantvel=True)
        s3b_reads = []
        for i in range(12):
            r = sample_md_vel('readback', min_real_delay=0.08)
            s3b_reads.append(r)
            if i == 6:
                frame3b = snap('03b_diagonal_60fwd_60lat')

        # 3c: Reverse (-60 fwd)
        s3c_apply = sample_md_vel('apply', vx=v_rev60['x'], vy=v_rev60['y'], vz=v_rev60['z'], constantvel=True)
        s3c_reads = []
        for i in range(12):
            r = sample_md_vel('readback', min_real_delay=0.08)
            s3c_reads.append(r)
            if i == 6:
                frame3c = snap('03c_reverse_minus60mps')
        s3_stop = safe_stop_direct()

        # ==================== STAGE 4: Uncoupled Heading Turns (+12, +30, +90 deg) + Snap-Back Control ====================
        # Re-center cleanly at r0, yaw=0 before Stage 4
        bridge.request('set_pose', x=r0['x'], y=r0['y'], z=r0['z'], yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0)
        time.sleep(0.15)
        s4_base = sample_md_vel('baseline')
        s4_apply = sample_md_vel('apply', vx=v_fwd60['x'], vy=v_fwd60['y'], vz=v_fwd60['z'], constantvel=True)
        s4_before_turn = sample_md_vel('readback', min_real_delay=0.08)
        turn_results = {}
        frames_s4 = {}
        for deg in (12.0, 30.0, 90.0):
            # Mirror Linux's test: move +1.5 m sideways and rotate yaw while keeping original sector velocity!
            bridge.request('set_pose', dx=1.5 * basis_r['x'], dy=1.5 * basis_r['y'], dz=1.5 * basis_r['z'],
                           yaw_deg=deg, pitch_deg=0.0, roll_deg=0.0)
            reads = [sample_md_vel('readback', min_real_delay=0.08) for _ in range(6)]
            turn_results[f'yaw_{int(deg)}deg'] = reads
            if deg in (12.0, 30.0):
                frames_s4[f'yaw_{int(deg)}deg'] = snap(f'04a_turn_uncoupled_{int(deg)}deg')

        # Measure X4's native yaw sign from the verified +30 deg basis so Stage 5 aligns velocity with nose!
        fwd_at_30 = parse_vec(turn_results['yaw_30deg'][-1]['forward'])
        yaw_sign_dot = (fwd_at_30['x'] * basis_r['x'] + fwd_at_30['y'] * basis_r['y'] + fwd_at_30['z'] * basis_r['z'])
        yaw_sign = 1.0 if yaw_sign_dot >= 0 else -1.0

        # 4b: Snap-back hazard control — coast ~1.5 s (~90 m past last SetObjectSectorPos), then stop WITHOUT anchor!
        s4_pre_snap_reads = [sample_md_vel('readback', min_real_delay=0.08) for _ in range(16)]
        s4_unsafe_stop = sample_md_vel('stop')
        s4_unsafe_after = sample_md_vel('release_readback', min_real_delay=0.10)

        # ==================== STAGE 5: Real Unmodified Loopback V4 Stream + Coupled Turn + 5s Packet Gap ====================
        # Re-center ship at r0, yaw=0 ahead of player before starting real loopback V4 stream
        bridge.request('set_pose', x=r0['x'], y=r0['y'], z=r0['z'], yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0)
        time.sleep(0.20)
        lock5 = bridge.request('lock_hud')

        grant_a = service.admit(1, 'a1' * 16, 1, sector, SESSION, EPOCH)
        grant_b = service.admit(2, 'b1' * 16, 2, sector, SESSION, EPOCH)
        service.set_running(True)
        observer_a = recv_source.observe(validator)
        client_recv = CompanyMotionClient(
            service.port, grant_a, recv_source, validator,
            on_snapshot=observer_a.snapshot, on_end=observer_a.end,
            on_transport_lost=observer_a.transport_lost, on_view_reset=observer_a.reset_world,
            trial_index=2,
        )
        observer_a.bind_views(client_recv.set_views)
        pilot_b = cockpit._QueuedPilotSource(EPOCH, 2, '4' * 32)
        client_remote = CompanyMotionClient(service.port, grant_b, pilot_b, lambda _: True)

        def pump_all():
            service.poll()
            client_recv.poll()
            if client_remote is not None:
                client_remote.poll()

        def pump_until(pred, label, timeout=15.0):
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

        pump_until(lambda: client_recv._running and client_remote._running, 'v4_clients_running')
        r_v4_start = bridge.request('start_v4_stream', pump=pump_all, clock_base=time.perf_counter())
        assert r_v4_start.get('status') == 'v4_stream_started', r_v4_start
        pump_until(lambda: 2 in (client_recv._sent_view or {}).get(sector, ()), 'v4_hud_view_subscribed')

        # Stream V4 samples at 5 Hz (dt=0.20s): 0..1.0 s forward 60 m/s (yaw 0), 1.0..2.6 s coupled turn 0 -> 30 deg & 60 -> 90 m/s
        dt = 0.20
        cur_p = dict(r0)
        sim_t = 0.0
        seq_v4 = 0
        v4_sent = []
        frame5a = None
        for step in range(14):
            seq_v4 += 1
            if step <= 5:
                yaw_rad = 0.0
                spd = 60.0
            else:
                frac = min(1.0, (step - 5) / 8.0)
                yaw_rad = math.radians(30.0 * frac)
                spd = 60.0 + 30.0 * frac
            # Align velocity strictly with X4's measured native yaw sign from Stage 4a!
            c, s = math.cos(yaw_rad), math.sin(yaw_rad) * yaw_sign
            fwd_dir = add_vec(scale_vec(basis_f, c), scale_vec(basis_r, s))
            vel = scale_vec(fwd_dir, spd)
            if step > 0:
                cur_p = add_vec(cur_p, scale_vec(vel, dt))
                sim_t += dt
            pilot_b.items.append({
                'version': 4, 'epoch': EPOCH, 'generation': pilot_b.local_generation,
                'index': 2, 'sequence': seq_v4, 'sector_macro': sector,
                'source_time': 200.0 + sim_t, 'capture_time': 600.0 + sim_t,
                'x': cur_p['x'], 'y': cur_p['y'], 'z': cur_p['z'],
                'real_velocity': None if step == 0 else vel,
                'orientation': {'yaw': yaw_rad, 'pitch': 0.0, 'roll': 0.0},
            })
            v4_sent.append({'sequence': seq_v4, 'yaw_deg': round(math.degrees(yaw_rad), 2), 'speed_mps': round(spd, 2)})
            pump_for(dt)
            if step == 12:
                frame5a = snap('05a_v4_turn_90mps')

        s5_before_gap = bridge.request('probe_v4_state', pump=pump_all)

        # Cease packets -> real 5.0-second packet gap!
        client_remote.close()
        client_remote = None
        service.transport_lost(2)
        gap_t0 = time.monotonic()
        gap_probes = []
        frame5b = None
        for label, target_s in (('1.5s_coasting', 1.5), ('3.0s_coasting', 3.0), ('4.4s_coasting', 4.4),
                                ('5.6s_post_cutoff', 5.6), ('6.4s_post_hold', 6.4), ('7.2s_post_hold2', 7.2)):
            while time.monotonic() - gap_t0 < target_s:
                pump_all()
                time.sleep(0.01)
            pr = bridge.request('probe_v4_state', pump=pump_all)
            pr['probe_label'] = label
            pr['gap_elapsed_s'] = round(time.monotonic() - gap_t0, 3)
            gap_probes.append(pr)
            if label == '3.0s_coasting':
                frame5b = snap('05b_coasting_in_5s_gap')
        frame5c = snap('05c_stopped_after_5s_gap')

        # ==================== STAGE 6: Final Independent MD + FFI Readback ====================
        final_md = bridge.request('md_readback', pump=pump_all, min_game_delay=0.20)
        done = bridge.request('done', pump=pump_all)
        assert done.get('status') == 'done', done

        vel_events = list(bridge.vel_events)
        zeroed_ev = [e for e in vel_events if e.get('kind') == 'zeroed']
        released_ev = [e for e in vel_events if e.get('kind') == 'released']
        rel_rb_ev = [e for e in vel_events if e.get('kind') == 'release_readback']
        coast_disp = vec_dist(parse_vec(s5_before_gap['pos']), parse_vec(gap_probes[3]['pos']))
        post_stop_drift = vec_dist(parse_vec(gap_probes[3]['pos']), parse_vec(gap_probes[5]['pos']))

        return {
            'x4_version_lua': ready.get('x4_ver'),
            'disposable_save': {'slot': f'{SAVE_SLOT}.xml.gz', 'sha256': save_sha},
            'ships': {
                'own_code': ready['own_code'], 'remote_code': ready['remote_code'],
                'remote_hex': ready['remote_hex'], 'sector': sector,
                'basis_error': float(ready['basis_error']),
                'initial_remote_xyz': r0, 'basis': {'right': basis_r, 'up': basis_u, 'forward': basis_f},
            },
            'udp_peer_validation': dict(validator.info),
            'stage0_baseline': {'lock': lock0, 'hud': hud0, 'md': md0, 'frame': frame0},
            'stage1_position_only_control': {
                'displacement_m': round(pos_only_disp, 3),
                'samples': pos_only_samples,
                'frame': frame1,
            },
            'stage2_forward_60mps': {
                'constvel_true': {
                    'baseline': s2_base, 'apply': s2_apply_true,
                    'displacement_m': round(s2_true_disp, 3), 'game_dt_s': round(s2_true_dt, 4),
                    'sample_count': len(s2_true_reads),
                    'first_read': s2_true_reads[0], 'mid_read': s2_true_reads[len(s2_true_reads) // 2],
                    'last_read': s2_true_reads[-1],
                    'hud_speeds_resolved': [r['spd_resolved'] for r in s2_true_reads],
                    'hud_speeds_empty': [r['spd_empty'] for r in s2_true_reads],
                    'stop': s2_stop1, 'frame': frame2a,
                },
                'constvel_false': {
                    'baseline': s2_base2, 'apply': s2_apply_false,
                    'displacement_m': round(s2_false_disp, 3), 'game_dt_s': round(s2_false_dt, 4),
                    'sample_count': len(s2_false_reads),
                    'first_read': s2_false_reads[0], 'mid_read': s2_false_reads[len(s2_false_reads) // 2],
                    'last_read': s2_false_reads[-1],
                    'hud_speeds_resolved': [r['spd_resolved'] for r in s2_false_reads],
                    'hud_speeds_empty': [r['spd_empty'] for r in s2_false_reads],
                    'stop': s2_stop2, 'frame': frame2b,
                },
            },
            'stage3_lateral_diagonal_reverse': {
                'baseline': s3_base,
                'lateral_60mps': {
                    'apply': s3a_apply, 'displacement_m': round(s3a_disp, 3), 'game_dt_s': round(s3a_dt, 4),
                    'first_read': s3a_reads[0], 'mid_read': s3a_reads[len(s3a_reads) // 2],
                    'last_read': s3a_reads[-1],
                    'hud_speeds_resolved': [r['spd_resolved'] for r in s3a_reads],
                    'hud_speeds_empty': [r['spd_empty'] for r in s3a_reads],
                    'frame': frame3a,
                },
                'diagonal_60fwd_60lat': {
                    'apply': s3b_apply, 'first_read': s3b_reads[0], 'last_read': s3b_reads[-1],
                    'hud_speeds_resolved': [r['spd_resolved'] for r in s3b_reads],
                    'hud_speeds_empty': [r['spd_empty'] for r in s3b_reads],
                    'frame': frame3b,
                },
                'reverse_minus60mps': {
                    'apply': s3c_apply, 'first_read': s3c_reads[0], 'last_read': s3c_reads[-1],
                    'hud_speeds_resolved': [r['spd_resolved'] for r in s3c_reads],
                    'hud_speeds_empty': [r['spd_empty'] for r in s3c_reads],
                    'frame': frame3c,
                },
                'stop': s3_stop,
            },
            'stage4_turns_and_snapback_control': {
                'before_turn': s4_before_turn,
                'uncoupled_turns': {
                    k: {
                        'first': v[0], 'last': v[-1],
                        'hud_speeds_resolved': [r['spd_resolved'] for r in v],
                        'hud_speeds_empty': [r['spd_empty'] for r in v],
                        'fwd_mps_series': [r['fwd_mps'] for r in v],
                    }
                    for k, v in turn_results.items()
                },
                'frames': frames_s4,
                'snapback_control_without_anchor': {
                    'pre_stop_last': s4_pre_snap_reads[-1],
                    'unsafe_stop': s4_unsafe_stop,
                    'post_unsafe_readback': s4_unsafe_after,
                },
            },
            'stage5_v4_loopback_turn_and_5s_gap': {
                'lock': lock5,
                'v4_samples_sent': len(v4_sent),
                'before_gap': s5_before_gap,
                'gap_probes': gap_probes,
                'coast_displacement_m': round(coast_disp, 3),
                'post_stop_drift_m': round(post_stop_drift, 5),
                'vel_event_count': len(vel_events),
                'zeroed_events': zeroed_ev,
                'released_events': released_ev,
                'release_readback_events': rel_rb_ev,
                'vel_events_tail': vel_events[-12:],
                'frames': {'turn_90mps': frame5a, 'coasting_gap': frame5b, 'stopped_5s': frame5c},
                'observer_stats': recv_source.observer_stats(),
            },
            'stage6_final_md_readback': final_md,
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
        for client in (client_recv, client_remote):
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
    parser.add_argument('--network-dll', required=True, help='x4lan_net_64.dll exporting monotonic()')
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
    network_dll_bytes = Path(args.network_dll).read_bytes()
    if b'monotonic' not in network_dll_bytes:
        raise SystemExit('network DLL lacks monotonic(); use packaged x4lan_net_64.dll')
    if cockpit.x4_running():
        raise SystemExit('X4.exe is running; deferring to avoid disturbing user session')
    ok, msg = steam.status()
    if not ok:
        raise SystemExit(msg)

    game = Path(args.game) if args.game else game_mod.find_install()
    game_mod.verify_exe(game)
    exe_path = game / 'X4.exe'
    exe_sha = hashlib.sha256(exe_path.read_bytes()).hexdigest()
    exe_ver = pe_file_version(exe_path)
    view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-ts-{out.name}'
    if view.exists():
        raise SystemExit(f'{view} already exists')

    documents = profile_mod.documents()

    def protected():
        return {
            'other_profiles': {
                entry.name: cockpit.tree_metadata(entry)
                for entry in sorted(documents.iterdir())
                if entry.is_dir() and entry.name != str(args.profile)
            },
            'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
        }

    acquire_lockfile(args.profile)
    before = protected()
    out.mkdir(parents=True)
    result = {
        'options': vars(args),
        'game': str(game),
        'x4_exe_version': exe_ver,
        'x4_exe_sha256': exe_sha,
        'network_dll_sha256': hashlib.sha256(network_dll_bytes).hexdigest(),
        'view': str(view),
        'outcome': 'started',
    }
    ctypes.windll.user32.SetProcessDPIAware()
    try:
        game_mod.create_view(game, view)
        profile_dir = profile_mod.prepare(
            args.profile,
            [*game_mod.dlc_ids(view), EXTENSION_ID],
            width=args.width, height=args.height, fast=True,
        )
        result['investigation'] = run_investigation(view, profile_dir, out, args, network_dll_bytes)
        result['outcome'] = 'done'
    except BaseException as exc:
        result['outcome'] = 'error'
        result['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        try:
            if view.exists():
                cockpit.remove_view_patiently(game_mod, view)
        finally:
            release_lockfile()
        proj_errs = count_project_errors(out / LOGFILE)
        result['project_errors'] = proj_errs
        result['project_error_count'] = len(proj_errs)
        result['protected_unchanged'] = protected() == before
        result['view_removed'] = not view.exists()
        result['x4_running_after'] = cockpit.x4_running()
        result['lockfile_removed'] = not LOCKFILE.exists()
        (out / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')

    inv = result.get('investigation', {})
    print(json.dumps({
        'outcome': result['outcome'],
        'x4_exe_version': result['x4_exe_version'],
        'x4_version_lua': inv.get('x4_version_lua'),
        'save_sha256': inv.get('disposable_save', {}).get('sha256'),
        'protected_unchanged': result['protected_unchanged'],
        'view_removed': result['view_removed'],
        'x4_running_after': result['x4_running_after'],
        'lockfile_removed': result['lockfile_removed'],
        'project_error_count': result['project_error_count'],
        'out': str(out),
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
