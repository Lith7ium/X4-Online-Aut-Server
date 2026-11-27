"""Isolated native Windows 11 X4 9.00 trial connecting the one-shot native velocity path
(constantvel="false") to the genuine opt-in receiving cockpit on a disposable world.

Exercises the regular receiving cockpit stack:
  CompanyMotionService -> ReconnectingCompanyMotionClient.observing -> CompanyMotionObserver
  -> OS PID-validated WindowsMotionPeer -> local UDP -> company_observer_stream.lua
  -> company_cockpit_observer.lua (stock target HUD locked) -> company_cockpit_playback.lua
  -> company_hull_playback.lua -> company_hull_playback_x4.lua
  -> company_cockpit_velocity.lua -> company_cockpit_velocity.xml (constantvel="false").

Test input provenance is explicit:
  - Disposable local custom_creative save (save_009.xml.gz) in profile 45850 inside an
    isolated x4ls view (800x600, fast=True, SW_SHOWNOACTIVATE, HWND_BOTTOM, shared lockfile).
  - Authenticated local loopback V4 scripted trajectory published by an admitted peer
    CompanyMotionClient on the same machine (not a live two-human WAN flight).

Stages verified with independent MD + FFI readbacks and live DWM PrintWindow HUD PNGs:
  0. Disposable save + stock HUD target locked (mode='hud', active pilot roster confirmed)
  1. Forward flight (+60 m/s for 3.0 s) — check HUD speed (~59 m/s), 1x displacement (no
     double movement), genuine receiver readbacks, zero position_deviation
  2. Coupled turn (0 -> 35 deg yaw + 60 -> 90 m/s ramp over 2.5 s + 1.0 s steady) — check
     XYZ, yaw, HUD speed (~89 m/s), readbacks, no double movement
  3. Silent motion-loss (5-second packet gap while remote stays admitted) — check 5 s coast
     (~440 m, 1x, HUD ~89 m/s), stale_5s stop via anchor + constantvel=false (0,0,0),
     <1 mm anchor drift, 0.000 m post-cutoff drift, HUD 0 m/s, slot stays reusable
  4. Actual control-loss roster (gen2 +60 m/s active velocity, then service.transport_lost(3)
     drops pilot from ROSTER with NO END packet) — check immediate pre-dispatch refusal,
     guarded anchor + constantvel=false (0,0,0), confirmed zero, 0.000 m post-stop drift,
     HUD 0 m/s, and slot stays REUSABLE (closed=false, velocity_cleanup_uncertain=false)
  5. Reacquisition after control-loss roster (gen3 +60 m/s for 2.0 s, HUD ~59 m/s) +
     explicit pilot withdrawal (service.revoke(4) -> END revocation) — check immediate anchor
     + constantvel=false (0,0,0), confirmed zero, 0.000 m post-withdrawal drift, HUD 0 m/s,
     final own-ship invariance (0.000 m), and strict teardown hygiene.
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
from authority.client_seat_overlay import company_motion_md, company_motion_ui, export_motion_buffer  # noqa: E402

EXTENSION_ID = 'x4_neon_cockpit_vel'
LOGFILE = 'neon-cockpit-vel.log'
SAVE_SLOT = 'save_009'
LOCKFILE = Path(r'C:\Users\Observer\Documents\Gemini Projects\.x4-native-test.lock')
EPOCH = '6a9cc6cc8ebb04ec5bb85fdb95b5a499'
SESSION = 'a' * 32
NONCE = 'd' * 32

PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
DIB_RGB_COLORS = 0

CONTENT_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<content id="{EXTENSION_ID}" name="Receiving cockpit native velocity trial" '
    'description="Disposable opt-in Windows X4 receiving-cockpit constantvel-false velocity trial" '
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
            return code.value == 259
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
        bmi.biHeight = -wh
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
        stride = ww * 4
        for y in range(wh):
            row = raw[y * stride:(y + 1) * stride]
            rgb = bytearray(ww * 3)
            rgb[0::3] = row[2::4]
            rgb[1::3] = row[1::4]
            rgb[2::3] = row[0::4]
            rgb_rows.append(bytes(rgb))
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


def compose_extension(extensions_dir, *, recv_source, profile, gamestart, epoch, udp_port,
                      udp_lease, udp_generation, network_dll_bytes, width, height):
    """Compose the disposable extension directly from production client_seat_overlay sources."""
    target = Path(extensions_dir) / EXTENSION_ID
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    (target / 'ui').mkdir()
    (target / 'md').mkdir()
    (target / 'native').mkdir()
    (target / 'native' / 'x4lan_net_64.dll').write_bytes(network_dll_bytes)

    prod_ui = company_motion_ui(recv_source)
    prod_md = company_motion_md(recv_source)

    md_sources = {
        'authority_ship_registry.xml': 'tools/dev/authority_ship_registry.xml',
        'company_cockpit_observer.xml': prod_md['company_cockpit_observer.xml'],
        'company_cockpit_velocity.xml': prod_md['company_cockpit_velocity.xml'],
        'authority_windows_cockpit_velocity_probe.xml': 'tools/dev/authority_windows_cockpit_velocity_probe.xml',
    }
    for dst_name, rel_src in md_sources.items():
        shutil.copyfile(ROOT / rel_src, target / 'md' / dst_name)

    ui_order = [
        ('authority_registry_resolve_string.lua', 'tools/dev/authority_registry_resolve_string.lua'),
        ('motion_buffer.lua', prod_ui['motion_buffer.lua']),
        ('motion_playback.lua', prod_ui['motion_playback.lua']),
        ('company_hull_playback.lua', prod_ui['company_hull_playback.lua']),
        ('company_hull_playback_x4.lua', prod_ui['company_hull_playback_x4.lua']),
        ('company_cockpit_velocity.lua', prod_ui['company_cockpit_velocity.lua']),
        ('company_cockpit_observer.lua', prod_ui['company_cockpit_observer.lua']),
        ('company_cockpit_playback.lua', prod_ui['company_cockpit_playback.lua']),
        ('company_observer_stream.lua', prod_ui['company_observer_stream.lua']),
        ('authority_windows_cockpit_velocity_x4.lua', 'tools/dev/authority_windows_cockpit_velocity_x4.lua'),
    ]
    for dst_name, rel_src in ui_order:
        data = (ROOT / rel_src).read_bytes()
        if dst_name == 'motion_buffer.lua':
            data = export_motion_buffer(data)
        (target / 'ui' / dst_name).write_bytes(data)

    cfg_text = (
        'X4NEON_COCKPIT_VEL = {\n'
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
        + [f'    <file name="ui/{dst}" />\n' for dst, _ in ui_order]
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


class TrialBridge:
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
        self.cockpit_readbacks = []
        self.cockpit_events = []
        self.cockpit_stops = []
        self.vel_sets = []
        self.vel_stops = []
        self.vel_refusals = []
        self.vel_fenced = []
        self.vel_uncertain = []
        self.error = None
        self.hwnd = None
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    @staticmethod
    def _parse_kv(text):
        out = {}
        for token in text.strip().split():
            if '=' in token:
                k, v = token.split('=', 1)
                out[k] = v
        return out

    def _watch(self):
        position = 0
        pending = ''
        last_tuck = 0.0
        tag = '[X4LAN] AUTH_COCKPIT_VEL '
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
                    with self.log_path.open('r', encoding='utf-8', errors='replace') as handle:
                        handle.seek(position)
                        chunk = handle.read()
                        position = handle.tell()
                except OSError:
                    chunk = ''
                if chunk:
                    pending += chunk
                    while '\n' in pending:
                        line, pending = pending.split('\n', 1)
                        self._handle_line(line, tag)
            if self.process.poll() is not None:
                with self._cond:
                    if self.error is None:
                        self.error = f'X4 exited unexpectedly with rc={self.process.returncode}'
                    self._cond.notify_all()
                return
            time.sleep(0.02)

    def _handle_line(self, line, tag):
        with self._cond:
            if tag in line:
                kv = self._parse_kv(line.split(tag, 1)[1])
                kind = kv.get('kind')
                if kind == 'ready':
                    self.ready_info = kv
                    self._cond.notify_all()
                elif kind == 'resp' and 'seq' in kv:
                    self.responses[int(kv['seq'])] = kv
                    self._cond.notify_all()
                elif kind == 'error':
                    self.error = kv
                    self._cond.notify_all()
            elif '[X4LAN] AUTH_COMPANY_COCKPIT_READBACK ' in line:
                self.cockpit_readbacks.append(self._parse_kv(line.split('[X4LAN] AUTH_COMPANY_COCKPIT_READBACK ', 1)[1]))
            elif '[X4LAN] AUTH_COMPANY_COCKPIT_EVENT ' in line:
                self.cockpit_events.append(self._parse_kv(line.split('[X4LAN] AUTH_COMPANY_COCKPIT_EVENT ', 1)[1]))
                self._cond.notify_all()
            elif '[X4LAN] AUTH_COMPANY_COCKPIT_STOP ' in line:
                self.cockpit_stops.append(line.split('[X4LAN] AUTH_COMPANY_COCKPIT_STOP ', 1)[1].strip())
                self._cond.notify_all()
            elif '[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_SET ' in line:
                self.vel_sets.append(self._parse_kv(line.split('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_SET ', 1)[1]))
            elif '[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_STOP ' in line:
                self.vel_stops.append(self._parse_kv(line.split('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_STOP ', 1)[1]))
                self._cond.notify_all()
            elif '[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_REFUSED ' in line:
                self.vel_refusals.append(self._parse_kv(line.split('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_REFUSED ', 1)[1]))
            elif '[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_FENCED ' in line:
                self.vel_fenced.append(self._parse_kv(line.split('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_FENCED ', 1)[1]))
            elif '[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_UNCERTAIN ' in line:
                self.vel_uncertain.append(self._parse_kv(line.split('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_UNCERTAIN ', 1)[1]))
                self._cond.notify_all()

    def wait_ready(self, timeout=180.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.ready_info is None:
                if self.error is not None:
                    raise RuntimeError(f'X4 error before ready: {self.error}')
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError('timed out waiting for X4 ready')
                self._cond.wait(min(0.25, rem))
            return dict(self.ready_info)

    def request(self, op, *, pump=None, timeout=30.0):
        with self._cond:
            seq = self._next_seq
            self._next_seq += 1
        cmd_path = self.ext_dir / 'ui' / 'command.lua'
        tmp_path = self.ext_dir / 'ui' / 'command.lua.tmp'
        tmp_path.write_text(f'return {{seq={seq},op="{op}"}}\n', encoding='ascii')
        os.replace(tmp_path, cmd_path)
        deadline = time.monotonic() + timeout
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


def forward_from_yaw(yaw):
    # Confirmed in X4 9.00: +yaw rotates +Z forward toward +X right.
    return {'x': math.sin(yaw), 'y': 0.0, 'z': math.cos(yaw)}


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
        if any(k in line for k in (EXTENSION_ID, 'X4Authority', 'X4NEON', 'company_cockpit', 'company_hull')):
            errors.append(line.strip())
    return errors


class _QueueSource:
    def __init__(self, epoch, index, local_gen):
        self.registry = (epoch, index)
        self.local_generation = local_gen
        self.items = []

    def poll(self, _):
        return self.items.pop(0) if self.items else None


def run_trial(view, profile_dir, out, args, network_dll_bytes):
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_reconnect import ReconnectingCompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.company_motion_source import CompanyMotionSource
    from lockstep.launcher import steam

    log_path = profile_dir / LOGFILE
    if log_path.exists():
        log_path.unlink()
    frames_dir = out / 'frames'
    frames_dir.mkdir(parents=True, exist_ok=True)

    recv_source = CompanyMotionSource(
        cockpit_radar_playback=True,
        cockpit_continuous_playback=True,
        cockpit_native_velocity=True,
    )
    recv_cfg = recv_source.configure(EPOCH, 1, operational_bits='111')
    service = CompanyMotionService()
    client_recv = client_remote = client_remote_gen2 = client_remote_gen3 = None
    process = job_handle = bridge = None

    def snap(name):
        return capture_window_png(bridge.hwnd if bridge else None, frames_dir / f'{name}.png')

    try:
        ext_dir = compose_extension(
            view / 'extensions',
            recv_source=recv_source,
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
        bridge = TrialBridge(process, log_path, ext_dir, timeout=60.0)

        ready = bridge.wait_ready(timeout=args.timeout)
        save_path = profile_dir / 'save' / f'{SAVE_SLOT}.xml.gz'
        save_sha = hashlib.sha256(save_path.read_bytes()).hexdigest() if save_path.is_file() else None
        if save_path.is_file():
            shutil.copyfile(save_path, out / f'{SAVE_SLOT}.xml.gz')

        sector = ready['remote_sector']
        r0 = parse_vec(ready['remote_xyz'])
        own0 = parse_vec(ready['own_xyz'])
        rot0 = parse_vec(ready['ffi_rot'])
        yaw0 = rot0['x']

        g_recv = service.admit(1, '1' * 32, 1, sector, SESSION, EPOCH)
        g_remote1 = service.admit(2, '2' * 32, 2, sector, SESSION, EPOCH)
        service.set_running(True)

        client_recv = ReconnectingCompanyMotionClient.observing(
            service.port, g_recv, recv_source, validator,
        )
        src_remote1 = _QueueSource(EPOCH, 2, 'c' * 32)
        client_remote = CompanyMotionClient(service.port, g_remote1, src_remote1, lambda _: True)
        remote_alive = [True]
        active_remote_client = [client_remote]

        def pump():
            service.poll()
            client_recv.poll()
            if remote_alive[0] and active_remote_client[0] is not None:
                active_remote_client[0].poll()

        def pump_sleep(duration):
            end = time.monotonic() + duration
            while time.monotonic() < end:
                pump()
                time.sleep(0.01)

        deadline = time.monotonic() + 10.0
        while not (client_recv._client is not None and client_recv._client._running and client_remote._running):
            if time.monotonic() >= deadline:
                raise TimeoutError('motion clients failed to bind')
            pump()
            time.sleep(0.01)

        # Stage 0: Lock observed ship in stock HUD and start genuine receiving cockpit!
        lock_resp = bridge.request('lock_hud_and_start', pump=pump, timeout=20.0)
        if lock_resp.get('status') != 'ok':
            raise RuntimeError(f'lock_hud_and_start failed: {lock_resp}')
        deadline = time.monotonic() + 10.0
        while not (client_recv._client and client_recv._client._sent_view == {sector: frozenset({2})}):
            if time.monotonic() >= deadline:
                raise TimeoutError('receiving client did not subscribe VIEW|cockpit for index 2')
            pump()
            time.sleep(0.01)

        base_probe = bridge.request('probe', pump=pump)
        frame_00 = snap('00_baseline_stationary')

        sim_pos = dict(r0)
        sim_yaw = yaw0

        def make_v4_sample(local_gen, seq, pos, vel, yaw, gt_s, cap_s):
            return {
                'version': 4, 'epoch': EPOCH, 'generation': local_gen, 'index': 2,
                'sequence': seq, 'sector_macro': sector,
                'source_time': gt_s, 'capture_time': cap_s,
                'x': pos['x'], 'y': pos['y'], 'z': pos['z'],
                'real_velocity': vel,
                'orientation': {'yaw': yaw, 'pitch': 0.0, 'roll': 0.0},
            }

        # Stage 1: Forward flight (+60 m/s along yaw0 for 3.0 s, 10 Hz = 30 samples).
        stage1_start_pos = dict(sim_pos)
        fwd0 = forward_from_yaw(sim_yaw)
        vel_fwd = {'x': fwd0['x'] * 60.0, 'y': 0.0, 'z': fwd0['z'] * 60.0}
        seq1 = 1
        t0 = time.monotonic()
        src_remote1.items.append(make_v4_sample('c' * 32, seq1, sim_pos, None, sim_yaw, 100.0, 200.0))
        pump_sleep(0.10)
        stage1_mid_probe = frame_01 = None
        for step_i in range(1, 31):
            dt = 0.10
            sim_pos = {
                'x': sim_pos['x'] + vel_fwd['x'] * dt,
                'y': sim_pos['y'] + vel_fwd['y'] * dt,
                'z': sim_pos['z'] + vel_fwd['z'] * dt,
            }
            seq1 += 1
            elapsed = step_i * dt
            src_remote1.items.append(make_v4_sample(
                'c' * 32, seq1, sim_pos, vel_fwd, sim_yaw, 100.0 + elapsed, 200.0 + elapsed))
            target_t = t0 + (step_i + 1) * dt
            while time.monotonic() < target_t:
                pump()
                time.sleep(0.01)
            if step_i == 22:
                stage1_mid_probe = bridge.request('probe', pump=pump)
                frame_01 = snap('01_forward_60mps')
        stage1_end_probe = bridge.request('probe', pump=pump)
        stage1_requested_disp = vec_dist(sim_pos, stage1_start_pos)
        stage1_actual_disp = vec_dist(parse_vec(stage1_end_probe['md_xyz']), stage1_start_pos)

        # Stage 2: Coupled turn (yaw0 -> yaw0 + 35 deg) + speed ramp (60 -> 90 m/s) over 2.5 s + 1.0 s steady.
        stage2_start_pos = dict(sim_pos)
        turn_steps = 25
        steady_steps = 10
        elapsed_base = 3.0
        stage2_mid_probe = frame_02a = None
        vel_turn = dict(vel_fwd)
        t_stage2 = time.monotonic()
        for step_i in range(1, turn_steps + steady_steps + 1):
            dt = 0.10
            frac = min(1.0, step_i / float(turn_steps))
            sim_yaw = yaw0 + math.radians(35.0) * frac
            spd = 60.0 + 30.0 * frac
            fvec = forward_from_yaw(sim_yaw)
            vel_turn = {'x': fvec['x'] * spd, 'y': 0.0, 'z': fvec['z'] * spd}
            sim_pos = {
                'x': sim_pos['x'] + vel_turn['x'] * dt,
                'y': sim_pos['y'] + vel_turn['y'] * dt,
                'z': sim_pos['z'] + vel_turn['z'] * dt,
            }
            seq1 += 1
            elapsed = elapsed_base + step_i * dt
            src_remote1.items.append(make_v4_sample(
                'c' * 32, seq1, sim_pos, vel_turn, sim_yaw, 100.0 + elapsed, 200.0 + elapsed))
            target_t = t_stage2 + step_i * dt
            while time.monotonic() < target_t:
                pump()
                time.sleep(0.01)
            if step_i == 16:
                stage2_mid_probe = bridge.request('probe', pump=pump)
                frame_02a = snap('02a_turn_mid')
        stage2_end_probe = bridge.request('probe', pump=pump)
        frame_02b = snap('02b_turn_90mps')
        stage2_requested_disp = vec_dist(sim_pos, stage2_start_pos)
        stage2_actual_disp = vec_dist(parse_vec(stage2_end_probe['md_xyz']), stage2_start_pos)
        total_pre_gap_requested = vec_dist(sim_pos, stage1_start_pos)
        total_pre_gap_actual = vec_dist(parse_vec(stage2_end_probe['md_xyz']), stage1_start_pos)

        # Stage 3: Silent motion-loss (5-second packet gap while pilot remains admitted in ROSTER).
        pos_at_gap_start = parse_vec(stage2_end_probe['md_xyz'])
        gap_started = time.monotonic()
        remote_alive[0] = False
        pump_sleep(2.4)
        stage3_coast_probe = bridge.request('probe', pump=pump)
        frame_03a = snap('03a_coasting_5s_gap')

        rem_to_5_65 = (gap_started + 5.65) - time.monotonic()
        if rem_to_5_65 > 0:
            pump_sleep(rem_to_5_65)
        stage3_stop_probe_1 = bridge.request('probe', pump=pump)
        frame_03b = snap('03b_stopped_after_5s_cutoff')
        pump_sleep(1.0)
        stage3_stop_probe_2 = bridge.request('probe', pump=pump)
        pump_sleep(1.0)
        stage3_stop_probe_3 = bridge.request('probe', pump=pump)

        p3_1 = parse_vec(stage3_stop_probe_1['md_xyz'])
        p3_2 = parse_vec(stage3_stop_probe_2['md_xyz'])
        p3_3 = parse_vec(stage3_stop_probe_3['md_xyz'])
        stage3_coast_dist = vec_dist(p3_1, pos_at_gap_start)
        stage3_post_drift_1_2 = vec_dist(p3_2, p3_1)
        stage3_post_drift_2_3 = vec_dist(p3_3, p3_2)
        stage3_vel_stops = list(bridge.vel_stops)

        # Stage 4: Fresh gen2 + active +60 m/s auxiliary velocity -> ACTUAL CONTROL-LOSS ROSTER
        # via service.transport_lost(3) (drops pilot from ROSTER, NO END packet!).
        service.revoke(2)
        pump_sleep(0.20)
        g_remote2 = service.admit(3, '3' * 32, 2, sector, SESSION, EPOCH)
        client_remote.close()
        client_remote = None
        src_remote2 = _QueueSource(EPOCH, 2, 'e' * 32)
        client_remote_gen2 = CompanyMotionClient(service.port, g_remote2, src_remote2, lambda _: True)
        active_remote_client[0] = client_remote_gen2
        remote_alive[0] = True
        deadline = time.monotonic() + 10.0
        while not client_remote_gen2._running:
            if time.monotonic() >= deadline:
                raise TimeoutError('gen2 remote client failed to bind')
            pump()
            time.sleep(0.01)
        pump_sleep(0.30)

        pre_s4_probe = bridge.request('probe', pump=pump)
        sim4_pos = parse_vec(pre_s4_probe['md_xyz'])
        stage4_start_pos = dict(sim4_pos)
        fvec4 = forward_from_yaw(sim_yaw)
        vel4 = {'x': fvec4['x'] * 60.0, 'y': 0.0, 'z': fvec4['z'] * 60.0}
        seq2 = 1
        t_s4 = time.monotonic()
        src_remote2.items.append(make_v4_sample('e' * 32, seq2, sim4_pos, None, sim_yaw, 200.0, 300.0))
        pump_sleep(0.10)
        stage4_moving_probe = frame_04a = None
        for step_i in range(1, 21):
            dt = 0.10
            sim4_pos = {
                'x': sim4_pos['x'] + vel4['x'] * dt,
                'y': sim4_pos['y'] + vel4['y'] * dt,
                'z': sim4_pos['z'] + vel4['z'] * dt,
            }
            seq2 += 1
            elapsed = step_i * dt
            src_remote2.items.append(make_v4_sample(
                'e' * 32, seq2, sim4_pos, vel4, sim_yaw, 200.0 + elapsed, 300.0 + elapsed))
            target_t = t_s4 + (step_i + 1) * dt
            while time.monotonic() < target_t:
                pump()
                time.sleep(0.01)
            if step_i == 15:
                stage4_moving_probe = bridge.request('probe', pump=pump)
                frame_04a = snap('04a_gen2_moving_60mps')

        stage4_before_closs_probe = bridge.request('probe', pump=pump)
        stage4_requested_disp = vec_dist(sim4_pos, stage4_start_pos)
        stage4_actual_disp = vec_dist(parse_vec(stage4_before_closs_probe['md_xyz']), stage4_start_pos)

        # Trigger actual control-loss roster (service.transport_lost(3) -> motion_hidden + ROSTER drop, NO END!).
        closs_triggered_at = time.monotonic()
        service.transport_lost(3)
        remote_alive[0] = False
        pump_sleep(0.60)
        stage4_closs_probe_1 = bridge.request('probe', pump=pump)
        closs_settle_elapsed_s = round(time.monotonic() - closs_triggered_at, 3)
        frame_04b = snap('04b_stopped_after_control_loss_roster')
        pump_sleep(1.0)
        stage4_closs_probe_2 = bridge.request('probe', pump=pump)
        pump_sleep(1.0)
        stage4_closs_probe_3 = bridge.request('probe', pump=pump)

        p4_1 = parse_vec(stage4_closs_probe_1['md_xyz'])
        p4_2 = parse_vec(stage4_closs_probe_2['md_xyz'])
        p4_3 = parse_vec(stage4_closs_probe_3['md_xyz'])
        stage4_post_drift_1_2 = vec_dist(p4_2, p4_1)
        stage4_post_drift_2_3 = vec_dist(p4_3, p4_2)
        stage4_vel_stops = list(bridge.vel_stops)

        # Stage 5: Reacquisition after control-loss roster (proves slot stays REUSABLE!) -> gen3 +60 m/s
        # -> explicit END withdrawal via service.revoke(4)!
        g_remote3 = service.admit(4, '4' * 32, 2, sector, SESSION, EPOCH)
        client_remote_gen2.close()
        client_remote_gen2 = None
        src_remote3 = _QueueSource(EPOCH, 2, 'f' * 32)
        client_remote_gen3 = CompanyMotionClient(service.port, g_remote3, src_remote3, lambda _: True)
        active_remote_client[0] = client_remote_gen3
        remote_alive[0] = True
        deadline = time.monotonic() + 10.0
        while not client_remote_gen3._running:
            if time.monotonic() >= deadline:
                raise TimeoutError('gen3 remote client failed to bind')
            pump()
            time.sleep(0.01)
        pump_sleep(0.30)

        pre_s5_probe = bridge.request('probe', pump=pump)
        sim5_pos = parse_vec(pre_s5_probe['md_xyz'])
        stage5_start_pos = dict(sim5_pos)
        vel5 = dict(vel4)
        seq3 = 1
        t_s5 = time.monotonic()
        src_remote3.items.append(make_v4_sample('f' * 32, seq3, sim5_pos, None, sim_yaw, 300.0, 400.0))
        pump_sleep(0.10)
        stage5_moving_probe = frame_05a = None
        for step_i in range(1, 21):
            dt = 0.10
            sim5_pos = {
                'x': sim5_pos['x'] + vel5['x'] * dt,
                'y': sim5_pos['y'] + vel5['y'] * dt,
                'z': sim5_pos['z'] + vel5['z'] * dt,
            }
            seq3 += 1
            elapsed = step_i * dt
            src_remote3.items.append(make_v4_sample(
                'f' * 32, seq3, sim5_pos, vel5, sim_yaw, 300.0 + elapsed, 400.0 + elapsed))
            target_t = t_s5 + (step_i + 1) * dt
            while time.monotonic() < target_t:
                pump()
                time.sleep(0.01)
            if step_i == 15:
                stage5_moving_probe = bridge.request('probe', pump=pump)
                frame_05a = snap('05a_gen3_reacquired_60mps')

        stage5_before_withdraw_probe = bridge.request('probe', pump=pump)
        stage5_requested_disp = vec_dist(sim5_pos, stage5_start_pos)
        stage5_actual_disp = vec_dist(parse_vec(stage5_before_withdraw_probe['md_xyz']), stage5_start_pos)

        # Trigger explicit END withdrawal (service.revoke(4) -> sends END|cockpit|...|gen3).
        service.revoke(4)
        remote_alive[0] = False
        pump_sleep(0.60)
        stage5_withdraw_probe_1 = bridge.request('probe', pump=pump)
        frame_05b = snap('05b_stopped_after_end_withdrawal')
        pump_sleep(1.0)
        stage5_withdraw_probe_2 = bridge.request('probe', pump=pump)
        pump_sleep(1.0)
        stage5_withdraw_probe_3 = bridge.request('probe', pump=pump)

        p5_1 = parse_vec(stage5_withdraw_probe_1['md_xyz'])
        p5_2 = parse_vec(stage5_withdraw_probe_2['md_xyz'])
        p5_3 = parse_vec(stage5_withdraw_probe_3['md_xyz'])
        stage5_post_drift_1_2 = vec_dist(p5_2, p5_1)
        stage5_post_drift_2_3 = vec_dist(p5_3, p5_2)

        own_final = parse_vec(stage5_withdraw_probe_3['md_own_xyz'])
        own_disp = vec_dist(own_final, own0)

        bridge.request('finish', pump=pump)
        pump_sleep(0.25)

        rb_errors = [float(r['error']) for r in bridge.cockpit_readbacks if 'error' in r]
        rb_deviations = [r for r in bridge.cockpit_readbacks if r.get('kind') == 'position_deviation']
        return {
            'x4_version_lua': ready.get('x4_version'),
            'input_provenance': (
                'Single-machine Windows 11 X4 9.00 isolated trial on disposable custom_creative '
                'save_009.xml.gz (profile 45850); authenticated loopback CompanyMotionService '
                'streaming scripted V4 forward/turn/silent-5s-gap/control-loss-roster/reacquire/'
                'explicit-END-withdrawal trajectory through unmodified '
                'ReconnectingCompanyMotionClient.observing + OS PID-validated WindowsMotionPeer '
                '+ genuine opt-in receiving cockpit (cockpit_continuous_playback=true, '
                'cockpit_native_velocity=true, constantvel=false). Not a two-human WAN flight.'
            ),
            'disposable_save': {'slot': SAVE_SLOT, 'sha256': save_sha},
            'world': {
                'own_actor': ready['own_actor'], 'own_code': ready['own_code'],
                'remote_actor': ready['remote_actor'], 'remote_code': ready['remote_code'],
                'sector': sector, 'zone': ready['remote_zone'],
                'basis_error': float(ready['basis_error']),
                'initial_remote_xyz': r0, 'initial_own_xyz': own0, 'initial_yaw_rad': yaw0,
            },
            'stage0_baseline': {'lock': lock_resp, 'probe': base_probe, 'frame': frame_00},
            'stage1_forward_60mps': {
                'mid_probe': stage1_mid_probe, 'end_probe': stage1_end_probe,
                'frame': frame_01,
                'requested_disp_m': stage1_requested_disp,
                'actual_disp_m': stage1_actual_disp,
                'ratio_actual_to_requested': round(stage1_actual_disp / max(1e-6, stage1_requested_disp), 4),
            },
            'stage2_turn_35deg_90mps': {
                'mid_probe': stage2_mid_probe, 'end_probe': stage2_end_probe,
                'frame_mid': frame_02a, 'frame_end': frame_02b,
                'requested_disp_m': stage2_requested_disp,
                'actual_disp_m': stage2_actual_disp,
                'total_pre_gap_requested_m': total_pre_gap_requested,
                'total_pre_gap_actual_m': total_pre_gap_actual,
                'ratio_total_actual_to_requested': round(total_pre_gap_actual / max(1e-6, total_pre_gap_requested), 4),
                'target_yaw_rad': sim_yaw,
            },
            'stage3_silent_five_second_gap': {
                'coast_probe': stage3_coast_probe,
                'frame_coast': frame_03a,
                'stop_probe_1': stage3_stop_probe_1,
                'stop_probe_2': stage3_stop_probe_2,
                'stop_probe_3': stage3_stop_probe_3,
                'frame_stopped': frame_03b,
                'coast_dist_m': stage3_coast_dist,
                'post_cutoff_drift_1_2_m': stage3_post_drift_1_2,
                'post_cutoff_drift_2_3_m': stage3_post_drift_2_3,
                'vel_stops_after_stage3': stage3_vel_stops,
            },
            'stage4_control_loss_roster': {
                'pre_probe': pre_s4_probe,
                'moving_probe': stage4_moving_probe,
                'before_closs_probe': stage4_before_closs_probe,
                'frame_moving': frame_04a,
                'requested_disp_m': stage4_requested_disp,
                'actual_disp_m': stage4_actual_disp,
                'ratio_actual_to_requested': round(stage4_actual_disp / max(1e-6, stage4_requested_disp), 4),
                'closs_settle_elapsed_s': closs_settle_elapsed_s,
                'closs_probe_1': stage4_closs_probe_1,
                'closs_probe_2': stage4_closs_probe_2,
                'closs_probe_3': stage4_closs_probe_3,
                'frame_stopped': frame_04b,
                'post_closs_drift_1_2_m': stage4_post_drift_1_2,
                'post_closs_drift_2_3_m': stage4_post_drift_2_3,
                'vel_stops_after_stage4': stage4_vel_stops,
            },
            'stage5_reacquire_and_end_withdrawal': {
                'pre_probe': pre_s5_probe,
                'moving_probe': stage5_moving_probe,
                'before_withdraw_probe': stage5_before_withdraw_probe,
                'frame_moving': frame_05a,
                'requested_disp_m': stage5_requested_disp,
                'actual_disp_m': stage5_actual_disp,
                'ratio_actual_to_requested': round(stage5_actual_disp / max(1e-6, stage5_requested_disp), 4),
                'withdraw_probe_1': stage5_withdraw_probe_1,
                'withdraw_probe_2': stage5_withdraw_probe_2,
                'withdraw_probe_3': stage5_withdraw_probe_3,
                'frame_withdrawn': frame_05b,
                'post_withdraw_drift_1_2_m': stage5_post_drift_1_2,
                'post_withdraw_drift_2_3_m': stage5_post_drift_2_3,
            },
            'telemetry_summary': {
                'own_ship_displacement_m': own_disp,
                'cockpit_readback_log_count': len(bridge.cockpit_readbacks),
                'cockpit_readback_max_error_m': max(rb_errors) if rb_errors else None,
                'cockpit_position_deviation_count': len(rb_deviations),
                'cockpit_events': bridge.cockpit_events,
                'cockpit_stops': bridge.cockpit_stops,
                'vel_set_log_count': len(bridge.vel_sets),
                'vel_stops': bridge.vel_stops,
                'vel_refusals': bridge.vel_refusals,
                'vel_fenced': bridge.vel_fenced,
                'vel_uncertain': bridge.vel_uncertain,
                'observer_stats': recv_source.observer_stats(),
                'recv_client_stats': client_recv.stats(),
            },
        }
    finally:
        if bridge is not None:
            bridge.stop()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=10)
            except BaseException:
                try:
                    process.kill()
                    process.wait(timeout=10)
                except BaseException:
                    pass
        if job_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(job_handle)
            except BaseException:
                pass
        for client in (client_recv, client_remote, client_remote_gen2, client_remote_gen3):
            if client is not None:
                try:
                    client.close()
                except BaseException:
                    pass
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
    view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-cv-{out.name}'
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
        result['trial'] = run_trial(view, profile_dir, out, args, network_dll_bytes)
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

    tr = result.get('trial', {})
    print(json.dumps({
        'outcome': result['outcome'],
        'x4_exe_version': result['x4_exe_version'],
        'x4_version_lua': tr.get('x4_version_lua'),
        'save_sha256': tr.get('disposable_save', {}).get('sha256'),
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
