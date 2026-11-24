"""Isolated native Windows X4 9.00 two-phase authoritative NPC capture-to-same-ship playback runner.

Phase 1 (Authoritative Capture X4, disposable profile 45850, isolated x4ls view, 800x600 fast=True HWND_BOTTOM):
  1. Enrolls the live universe (~9,900+ ships) into X4AuthorityShipRegistry via Initialize + PopulateUniverse.
  2. Scouts real AI-flying NPCs, picks Primary S (moderate speed) and Secondary S2 (distinct sector if available).
  3. Saves disposable checkpoint save_009 inside disposable profile 45850 (capturing X4AuthorityShipRegistry
     and S's exact baseline pose), unpauses, and captures ~15 s of genuine live AI flight at ~6.7 Hz using
     GetCurrentGameTime() + sub-ms GetCurRealTime().
  4. Profiles sub-ms native X4 FFI capture cost across N = 1, 5, 16, 32, 48 enrolled ships via GetCurRealTime().

Phase 2 (Observing Windows Client X4, fresh process loading save_009 from Phase 1):
  1. Cold-reloads save_009 from the start menu; resolves the EXACT SAME registered ship S (same epoch,
     same index, same idcode, same sector_macro) directly from the saved X4AuthorityShipRegistry without
     re-populating, and verifies S's starting pose matches Phase 1's saved baseline.
  2. Connects X4's real local UDP socket (x4lan_net_64.dll) to Pilot A's admitted CompanyMotionClient
     (trial_index=S.index, on_transport_lost, NpcMotionObserver, NpcLocalPlaybackBridge with Windows
     GetExtendedUdpTable PID validation) + concurrent Pilot B on real CompanyMotionService.
  3. Stage A (Live capture-to-same-ship playback): Streams Phase 1's real captured V3 samples of S and S2
     through CompanyMotionService -> TCP -> admitted CompanyMotionClient -> NpcMotionObserver -> UDP REMOTE
     -> X4LAN_NPC_HULL_PLAYBACK (X4LAN_MOTION_BUFFER + X4LAN_MOTION_PLAYBACK) driving S's own local hull,
     confirming SEEN receipts and delayed later-update readbacks (elapsed > 0).
  4. Stage B (Sample loss & 5.0 s prediction cutoff): Stops publishing S -> CompanyMotionService MAX_SAMPLE_AGE
     expiry fires motion_hidden -> on_transport_lost -> UDP LOSS -> X4 predicts S smoothly up to 5.0 s and
     freezes dead-still at the 5.0 s cutoff (verified by pre-cutoff and post-cutoff readbacks while S2 continues).
  5. Stage C (Offscreen withdrawal & reacquisition): X4 withdraws high_attention view -> 0 deliveries of S to
     Pilot A while Pilot B & Pilot A's S2 continue -> re-subscribes high_attention -> verifies >50 m guard
     refusal (PROBE_50M) -> streams fresh post-loss real captured samples of S -> single authorized reacquisition
     alignment (when >50 m) confirmed by independent later readback -> resumes ordinary <=50 m continuous playback.
  6. Stage D (Independent final MD+FFI readback & pause/save/resume): Independent later-update MD+FFI Readback
     confirming S's identity, sector, zone, exact hull XYZ agreement, player_disp_m == 0.0, plus pause/save/resume.
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

EXTENSION_ID = 'x4_neon_npc_motion'
SAVE_SLOT = 'save_009'
REGISTRY_XML = ROOT / 'tools/dev/authority_ship_registry.xml'
MOTION_XML = ROOT / 'tools/dev/authority_npc_motion.xml'
MOTION_LUA = ROOT / 'tools/dev/authority_npc_motion_x4.lua'
MOTION_BUFFER_LUA = ROOT / 'src/authority/lua/motion_buffer.lua'
MOTION_PLAYBACK_LUA = ROOT / 'src/authority/lua/motion_playback.lua'
NPC_PLAYBACK_LUA = ROOT / 'src/authority/lua/npc_hull_playback.lua'
X4LAN_NET_DLL = ROOT / 'src/lockstep/base/native/x4lan_net_64.dll'

SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
STARTF_USESHOWWINDOW = 0x00000001
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
SESSION = 'a' * 32
GEN_PRIMARY = '1' * 32
GEN_SECONDARY = '2' * 32

CONTENT_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<content id="{EXTENSION_ID}" name="NPC motion milestone probe" '
    'description="Disposable opt-in NPC motion capture, same-ship playback, and profiling harness" '
    'author="Neon" version="100" date="2026-11-24" save="false">\n'
    '  <dependency version="900" />\n'
    '</content>\n'
)


def ui_xml(mode):
    extra = ''
    if mode == 'observe':
        extra = (
            '    <file name="ui/motion_buffer.lua" />\n'
            '    <file name="ui/motion_playback.lua" />\n'
            '    <file name="ui/npc_hull_playback.lua" />\n'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<addon name="{EXTENSION_ID}">\n'
        '  <environment type="menus">\n'
        '    <dependency name="ego_detailmonitor" />\n'
        '    <file name="ui/config.lua" />\n'
        f'{extra}'
        '    <file name="ui/authority_npc_motion_x4.lua" />\n'
        '  </environment>\n'
        '</addon>\n'
    )


def config_lua(profile, gamestart, epoch, *, mode='capture', extra=None):
    lines = [
        'X4NEON_NPC_MOTION = {',
        '  enabled = true,',
        f'  mode = "{mode}",',
        f'  extension_id = "{EXTENSION_ID}",',
        f'  save_slot = "{SAVE_SLOT}",',
        f'  profile = "{int(profile)}",',
        f'  gamestart = "{gamestart}",',
        f'  epoch = "{epoch}",',
    ]
    for key, value in (extra or {}).items():
        if isinstance(value, bool):
            lines.append(f'  {key} = {"true" if value else "false"},')
        elif isinstance(value, (int, float)):
            lines.append(f'  {key} = {value!r},')
        else:
            lines.append(f'  {key} = {json.dumps(str(value))},')
    lines.append('}\n')
    return '\n'.join(lines)


def compose_extension(extensions_dir, *, profile, gamestart, epoch, mode='capture', extra=None):
    if not gamestart.replace('_', '').isalnum():
        raise SystemExit('invalid gamestart id')
    if mode not in ('capture', 'observe'):
        raise ValueError(f'unsupported mode: {mode}')
    target = Path(extensions_dir) / EXTENSION_ID
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()
    (target / 'ui').mkdir()
    (target / 'md').mkdir()
    shutil.copyfile(REGISTRY_XML, target / 'md' / 'authority_ship_registry.xml')
    shutil.copyfile(MOTION_XML, target / 'md' / 'authority_npc_motion.xml')
    shutil.copyfile(MOTION_LUA, target / 'ui' / 'authority_npc_motion_x4.lua')
    if mode == 'observe':
        (target / 'native').mkdir()
        shutil.copyfile(X4LAN_NET_DLL, target / 'native' / 'x4lan_net_64.dll')
        buf_text = MOTION_BUFFER_LUA.read_text(encoding='utf-8').replace('\r\n', '\n')
        if 'X4LAN_MOTION_BUFFER' not in buf_text:
            assert buf_text.rstrip().endswith('return M'), 'unexpected motion_buffer.lua ending'
            idx = buf_text.rfind('return M')
            buf_text = buf_text[:idx] + 'X4LAN_MOTION_BUFFER = M\n' + buf_text[idx:]
        (target / 'ui' / 'motion_buffer.lua').write_text(buf_text, encoding='utf-8')
        shutil.copyfile(MOTION_PLAYBACK_LUA, target / 'ui' / 'motion_playback.lua')
        shutil.copyfile(NPC_PLAYBACK_LUA, target / 'ui' / 'npc_hull_playback.lua')
    (target / 'ui' / 'config.lua').write_text(
        config_lua(profile, gamestart, epoch, mode=mode, extra=extra), encoding='ascii',
    )
    (target / 'content.xml').write_text(CONTENT_XML, encoding='utf-8')
    (target / 'ui.xml').write_text(ui_xml(mode), encoding='utf-8')
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
    """Validate that a local loopback UDP peer port belongs strictly to our spawned X4 child PID."""
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
            bound_addr['local_ip'] = matches[0][0]
            bound_addr['port'] = matches[0][1]
            bound_addr['pid'] = pid
            return True
        return False

    validate.info = bound_addr
    return validate


def attach_kill_on_close_job(pid):
    """Attach child process to a Windows Job Object so even a hard Python exit kills X4."""
    if os.name != 'nt':
        return None
    try:
        kernel32 = ctypes.windll.kernel32

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount',
            )]

        class _BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', ctypes.c_uint32),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class _EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', _BASIC_LIMIT),
                ('IoInfo', _IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryUsed', ctypes.c_size_t),
                ('PeakJobMemoryUsed', ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info),
        )
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
    x = max(0, screen_w - win_w - 8)
    y = max(0, screen_h - win_h - 48)
    user32.SetWindowPos(
        hwnd, HWND_BOTTOM, x, y, 0, 0,
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
            raise OSError(f'{view} is not an empty view root; left in place')
        try:
            os.rmdir(view)
            return
        except PermissionError:
            continue
    raise OSError(f'{view} stayed in use')


class X4MotionBridge:
    """Thread-safe req_N.lua + log bridge to the live X4 NPC motion harness."""

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
        self.traces = {'primary': [], 'secondary': []}
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
                            if '[X4LAN] AUTH_NPC_MOTION ' not in line:
                                continue
                            payload = line.split('[X4LAN] AUTH_NPC_MOTION ', 1)[1].strip()
                            self.events.append(payload)
                            if payload.startswith('READY '):
                                self.ready_info = parse_kv(payload[len('READY '):])
                                self._cond.notify_all()
                            elif payload.startswith('TRACE '):
                                kv = parse_kv(payload[len('TRACE '):])
                                ship = kv.get('ship')
                                if ship in self.traces:
                                    self.traces[ship].append({
                                        'seq': int(kv['seq']),
                                        'index': int(kv['index']),
                                        'idcode': kv['idcode'],
                                        'sector_macro': kv['sector'],
                                        'zone_key': kv['zone'],
                                        'game_time': float(kv['gt']),
                                        'real_time': float(kv['rt']),
                                        'x': float(kv['x']),
                                        'y': float(kv['y']),
                                        'z': float(kv['z']),
                                    })
                            elif payload.startswith('RESP '):
                                kv = parse_kv(payload[len('RESP '):])
                                seq = int(kv['seq'])
                                self.responses[seq] = kv
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
                    raise TimeoutError('timed out waiting for X4 NPC motion READY')
                self._cond.wait(min(0.05 if pump else 0.25, rem))

    def _lua_value(self, value):
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, (list, tuple)):
            return '{ ' + ', '.join(self._lua_value(item) for item in value) + ' }'
        return json.dumps(str(value))

    def request(self, op, *, pump=None, timeout=None, **params):
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
        fields = [f'seq = {seq}', f'op = {json.dumps(op)}', f'request_id = {json.dumps(f"req-{seq}")}']
        for key, value in params.items():
            if value is not None:
                fields.append(f'{key} = {self._lua_value(value)}')
        body = 'return { ' + ', '.join(fields) + ' }\n'
        partial = self.ext_dir / f'req_{seq}.partial'
        partial.write_text(body, encoding='ascii')
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
                    raise TimeoutError(f'timed out waiting for X4 response to seq={seq} op={op}')
                self._cond.wait(min(0.02 if pump else 0.2, rem))

    def stop(self):
        self._stop.set()
        self._thread.join(3)


class _IdlePilotSource:
    def __init__(self, epoch, index):
        self.registry = (epoch, index)
        self.local_generation = 'f' * 32

    def poll(self, _validate_peer):
        return None


def compute_v3_sample(prev, curr, *, index, generation, sequence, epoch=EPOCH, reset_baseline=False):
    """Build a validated V3 sample with real finite-difference velocity from live X4 captures."""
    gt = float(curr['game_time'])
    rt = float(curr['real_time'])
    x, y, z = float(curr['x']), float(curr['y']), float(curr['z'])
    sector = curr['sector_macro']
    vel = None
    if not reset_baseline and prev is not None and prev['sector_macro'] == sector:
        dt_rt = rt - float(prev['real_time'])
        dt_gt = gt - float(prev['game_time'])
        dt = dt_rt if dt_rt >= 0.02 else dt_gt
        if 0.02 <= dt <= 2.0:
            vx = (x - float(prev['x'])) / dt
            vy = (y - float(prev['y'])) / dt
            vz = (z - float(prev['z'])) / dt
            if all(math.isfinite(v) and abs(v) <= 5000.0 for v in (vx, vy, vz)):
                vel = {'x': round(vx, 6), 'y': round(vy, 6), 'z': round(vz, 6)}
    return {
        'version': 3,
        'epoch': epoch,
        'generation': generation,
        'index': int(index),
        'sequence': int(sequence),
        'sector_macro': sector,
        'source_time': gt,
        'capture_time': rt,
        'x': x,
        'y': y,
        'z': z,
        'real_velocity': vel,
    }


def build_trace_v3_samples(raw_rows, *, index, generation, epoch=EPOCH):
    """Convert raw live X4 trace rows into a monotone V3 sample sequence with real velocities."""
    samples = []
    prev = None
    for seq, row in enumerate(raw_rows, start=1):
        sample = compute_v3_sample(
            prev, row, index=index, generation=generation, sequence=seq,
            epoch=epoch, reset_baseline=(prev is None),
        )
        samples.append(sample)
        prev = row
    return samples


def benchmark_10082_scalability(counts=(1, 16, 64, 256), rounds=10):
    """Synthetic loopback scalability sweep over a 10,082-entry registry index space."""
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.npc_motion_observer import NpcMotionObserver

    rows = []
    registry_size = 10082
    sector_a = 'cluster_01_sector001_macro'
    sector_b = 'cluster_14_sector001_macro'
    for n in counts:
        service = CompanyMotionService()
        try:
            grant_a = service.admit(1, 'a1' * 16, 1970, sector_a, SESSION, EPOCH)
            service.set_running(True)
            obs = NpcMotionObserver(EPOCH, 1970)
            client = CompanyMotionClient(
                service.port, grant_a, _IdlePilotSource(EPOCH, 1970), lambda: True,
                on_snapshot=obs.on_snapshot, on_end=obs.on_end, on_view_reset=obs.on_view_reset,
            )
            try:
                obs.bind_views(client.set_views)
                while not client._running:
                    service.poll()
                    client.poll()
                stride = max(1, (registry_size - 10) // max(n, 1))
                picked = []
                cur = 10
                while len(picked) < n:
                    if cur != 1970:
                        picked.append(cur)
                    cur += stride
                half = (n + 1) // 2
                idx_a, idx_b = picked[:half], picked[half:]
                sector_by_idx = {idx: sector_a for idx in idx_a}
                sector_by_idx.update({idx: sector_b for idx in idx_b})
                obs.set_slot_view('high_attention', sector_a, idx_a)
                if idx_b:
                    obs.set_slot_view('map', sector_b, idx_b)
                while sum(len(v) for v in service.subscribed_npc_targets().values()) < n:
                    service.poll()
                    client.poll()

                total_bytes = 0
                delivered_before = obs.snapshots_total
                t0 = time.perf_counter()
                for r in range(1, rounds + 1):
                    for idx in picked:
                        sec = sector_by_idx[idx]
                        vel = None if r == 1 else {'x': 25.0, 'y': 0.0, 'z': 0.0}
                        sample = {
                            'version': 3, 'epoch': EPOCH, 'generation': f'{idx:032x}',
                            'index': idx, 'sequence': r, 'sector_macro': sec,
                            'source_time': float(r), 'capture_time': float(r) * 1.1,
                            'x': float(idx) + r * 25.0, 'y': 100.0, 'z': -50.0, 'real_velocity': vel,
                        }
                        service.publish_npc_sample(sample)
                        total_bytes += len(json.dumps(sample, separators=(',', ':'))) + 56
                    target_total = delivered_before + r * n
                    while obs.snapshots_total < target_total:
                        service.poll()
                        client.poll()
                elapsed = time.perf_counter() - t0
                delivered = obs.snapshots_total - delivered_before
                rows.append({
                    'registry_size': registry_size,
                    'visible_tracks': n,
                    'rounds': rounds,
                    'snapshots_delivered': delivered,
                    'elapsed_s': round(elapsed, 4),
                    'ms_per_round': round((elapsed / rounds) * 1000.0, 3),
                    'effective_hz_all_tracks': round(rounds / elapsed, 2) if elapsed > 0 else 0.0,
                    'snapshots_per_sec': round(delivered / elapsed, 1) if elapsed > 0 else 0.0,
                    'est_payload_bytes_per_sec': round(total_bytes / elapsed, 1) if elapsed > 0 else 0.0,
                })
            finally:
                client.close()
        finally:
            service.close()
    return rows


def _spawn_x4_hidden(view, profile, log_path):
    from lockstep.launcher import steam

    command = [
        str(view / 'X4.exe'),
        '-personalfolderid', str(profile),
        '-skipintro', '-noworkshopsync', '-nocputhrottle',
        '-debug', 'scripts', '-logfile', log_path.name,
    ]
    si = subprocess.STARTUPINFO()
    si.dwFlags |= STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWNOACTIVATE
    process = subprocess.Popen(
        command, cwd=str(view), env=steam.client_environment(), startupinfo=si,
    )
    job = attach_kill_on_close_job(process.pid)
    return process, job


def _terminate_x4(process, job_handle):
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


def _dist3(a, b):
    return math.sqrt((a['x'] - b['x']) ** 2 + (a['y'] - b['y']) ** 2 + (a['z'] - b['z']) ** 2)


def run_phase1_capture(view, profile_dir, out, args):
    """Launch X4 #1 (Authoritative Capture): populate registry, save save_009, capture ~15 s real AI flight."""
    save_dir = profile_dir / 'save'
    if save_dir.is_dir():
        for old in save_dir.glob(f'{SAVE_SLOT}*'):
            old.unlink()
    log_path = profile_dir / 'neon-npc-phase1-capture.log'
    if log_path.exists():
        log_path.unlink()

    ext_dir = compose_extension(
        view / 'extensions', profile=args.profile, gamestart=args.gamestart, epoch=EPOCH, mode='capture',
    )
    process, job_handle = _spawn_x4_hidden(view, args.profile, log_path)
    bridge = X4MotionBridge(process, log_path, ext_dir, timeout=90.0)
    try:
        ready = bridge.wait_ready(timeout=args.timeout)
        trace_resp = bridge.request('capture_trace', interval=0.10, duration_s=16.0, target_samples=160, timeout=55.0)
        assert trace_resp.get('status') == 'trace_captured', f'trace capture failed: {trace_resp}'

        native_profile = []
        max_cand = int(ready['candidates'])
        for n in (1, 5, 16, 32, 48):
            if n <= max_cand:
                native_profile.append(bridge.request('profile_capture', count=n, iters=40))

        done_resp = bridge.request('done')
        assert done_resp.get('status') == 'done'

        primary_rows = list(bridge.traces['primary'])
        secondary_rows = list(bridge.traces['secondary'])
        assert len(primary_rows) >= 50, f'expected >=50 captured primary samples, got {len(primary_rows)}'
        assert len(secondary_rows) >= 50, f'expected >=50 captured secondary samples, got {len(secondary_rows)}'

        saved_files = sorted(p.name for p in save_dir.glob(f'{SAVE_SLOT}*')) if save_dir.is_dir() else []
        assert saved_files, f'expected {SAVE_SLOT} in disposable profile {save_dir}'

        return {
            'ready': ready,
            'trace_resp': trace_resp,
            'primary_rows': primary_rows,
            'secondary_rows': secondary_rows,
            'native_profile': native_profile,
            'saved_files': saved_files,
            'log_path': str(log_path),
            'events_count': len(bridge.events),
        }
    finally:
        bridge.stop()
        _terminate_x4(process, job_handle)
        if log_path.is_file():
            shutil.copyfile(log_path, out / log_path.name)


def run_phase2_observe_playback(view, profile_dir, out, args, phase1):
    """Launch X4 #2 (Observing Client): cold-load save_009, stream Phase 1 samples via admitted client to SAME ship."""
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.npc_motion_observer import NpcLocalPlaybackBridge, NpcMotionObserver

    ready1 = phase1['ready']
    primary_idx = int(ready1['primary_index'])
    secondary_idx = int(ready1['secondary_index'])
    primary_sector = ready1['primary_sector']
    secondary_sector = ready1['secondary_sector']
    pilot_a_seat = max(199001, int(ready1['total_registry']) + 10)
    pilot_b_seat = pilot_a_seat + 1

    primary_v3 = build_trace_v3_samples(phase1['primary_rows'], index=primary_idx, generation=GEN_PRIMARY)
    secondary_v3 = build_trace_v3_samples(phase1['secondary_rows'], index=secondary_idx, generation=GEN_SECONDARY)

    log_path = profile_dir / 'neon-npc-phase2-observe.log'
    if log_path.exists():
        log_path.unlink()

    # Bind local UDP bridge first so config.lua knows the exact loopback port and lease.
    holder = {'validator': None}

    def deferred_validate(addr):
        fn = holder['validator']
        return fn(addr) is True if fn is not None else False

    udp_bridge = NpcLocalPlaybackBridge(EPOCH, pilot_a_seat, deferred_validate)
    service = CompanyMotionService()
    client_a = client_b = None
    process = job_handle = bridge = None
    try:
        ext_dir = compose_extension(
            view / 'extensions',
            profile=args.profile,
            gamestart=args.gamestart,
            epoch=EPOCH,
            mode='observe',
            extra={
                'primary_index': primary_idx,
                'secondary_index': secondary_idx,
                'own_index': pilot_a_seat,
                'udp_port': udp_bridge.port,
                'udp_lease': udp_bridge.lease,
            },
        )
        process, job_handle = _spawn_x4_hidden(view, args.profile, log_path)
        validator = make_windows_pid_udp_validator(process)
        holder['validator'] = validator

        grant_a = service.admit(1, 'a1' * 16, pilot_a_seat, primary_sector, SESSION, EPOCH)
        grant_b = service.admit(2, 'b2' * 16, pilot_b_seat, primary_sector, SESSION, EPOCH)
        service.set_running(True)

        obs_a = udp_bridge.observer
        obs_b = NpcMotionObserver(EPOCH, pilot_b_seat)
        client_a = CompanyMotionClient(
            service.port, grant_a, _IdlePilotSource(EPOCH, pilot_a_seat), lambda: True,
            on_snapshot=obs_a.on_snapshot, on_end=obs_a.on_end,
            on_transport_lost=obs_a.on_transport_lost, on_view_reset=obs_a.on_view_reset,
            trial_index=primary_idx,
        )
        client_b = CompanyMotionClient(
            service.port, grant_b, _IdlePilotSource(EPOCH, pilot_b_seat), lambda: True,
            on_snapshot=obs_b.on_snapshot, on_end=obs_b.on_end, on_view_reset=obs_b.on_view_reset,
        )
        obs_a.bind_views(client_a.set_views)
        obs_b.bind_views(client_b.set_views)

        def pump_all():
            udp_bridge.poll()
            service.poll()
            client_a.poll()
            client_b.poll()

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

        bridge = X4MotionBridge(process, log_path, ext_dir, timeout=60.0)
        ready2 = bridge.wait_ready(timeout=args.timeout, pump=pump_all)
        pump_until(lambda: client_a._running and client_b._running and udp_bridge.peer is not None,
                   label='clients_and_udp_bound')

        # Cold-reload identity & saved baseline pose verification!
        assert int(ready2['primary_index']) == primary_idx
        assert ready2['primary_idcode'] == ready1['primary_idcode']
        assert ready2['primary_sector'] == primary_sector
        assert int(ready2['total_registry']) == int(ready1['total_registry'])
        saved_p0 = {
            'x': float(ready1['primary_saved_x']),
            'y': float(ready1['primary_saved_y']),
            'z': float(ready1['primary_saved_z']),
        }
        loaded_p0 = {
            'x': float(ready2['primary_init_x']),
            'y': float(ready2['primary_init_y']),
            'z': float(ready2['primary_init_z']),
        }
        cold_reload_offset_m = _dist3(saved_p0, loaded_p0)

        # Subscribe Pilot B (second admitted observer) to primary; X4 #2 sends authenticated VIEW over UDP for Pilot A!
        obs_b.set_slot_view('high_attention', primary_sector, [primary_idx])
        v1 = bridge.request('set_view', pump=pump_all, slot='high_attention',
                            sector=primary_sector, indices=[primary_idx])
        assert v1.get('status') == 'view_set'
        v2 = bridge.request('set_view', pump=pump_all, slot='map',
                            sector=secondary_sector, indices=[secondary_idx])
        assert v2.get('status') == 'view_set'
        pump_until(lambda: (
            primary_idx in service.subscribed_npc_targets().get(primary_sector, ())
            and secondary_idx in service.subscribed_npc_targets().get(secondary_sector, ())
            and primary_idx in (client_a._sent_view or {}).get(primary_sector, ())
        ), label='initial_udp_views_synced')

        # ==================== STAGE A: Live Capture-to-Same-Ship Playback ====================
        # Pick ~3.0 s segment (~18-20 real captured samples) paced at their real capture_time deltas!
        t_start = primary_v3[0]['capture_time']
        seg1_end = 1
        while seg1_end < len(primary_v3) - 35 and (primary_v3[seg1_end]['capture_time'] - t_start) < 2.7:
            seg1_end += 1
        seg1_end = max(12, seg1_end)

        s2_cursor = 0

        def publish_secondary_next(reset_baseline=False):
            nonlocal s2_cursor
            if s2_cursor >= len(secondary_v3):
                return None
            raw = dict(secondary_v3[s2_cursor])
            s2_cursor += 1
            if reset_baseline or s2_cursor == 1:
                raw['real_velocity'] = None
            service.publish_npc_sample(raw)
            return raw

        for i in range(seg1_end):
            sample_p = dict(primary_v3[i])
            if i == 0:
                sample_p['real_velocity'] = None
            service.publish_npc_sample(sample_p)
            publish_secondary_next(reset_baseline=(i == 0))
            if i + 1 < seg1_end:
                dt = max(0.05, min(0.35, primary_v3[i + 1]['capture_time'] - primary_v3[i]['capture_time']))
                pump_for(dt)
            else:
                pump_for(0.25)

        pump_until(lambda: (
            udp_bridge.stats()['seen_by_x4'].get(primary_idx, {}).get('last_sequence', 0) >= primary_v3[seg1_end - 1]['sequence']
            and udp_bridge.stats()['readback_count'] >= 2
        ), label='stage_a_playback_and_readback')

        stage_a_probe = bridge.request('probe_pose', pump=pump_all, index=primary_idx)
        stage_a_pose = {k: float(stage_a_probe[k]) for k in ('x', 'y', 'z')}
        stage_a_disp_m = _dist3(loaded_p0, stage_a_pose)
        stage_a_target_ref = primary_v3[seg1_end - 1]
        stage_a_dist_to_target_m = _dist3(stage_a_pose, stage_a_target_ref)
        stage_a_writes = int(stage_a_probe['writes'])
        stage_a_readbacks = int(stage_a_probe['readbacks'])
        stage_a_seen_seq = udp_bridge.stats()['seen_by_x4'].get(primary_idx, {}).get('last_sequence')
        assert stage_a_writes > 0 and stage_a_readbacks > 0, f'expected active writes & readbacks: {stage_a_probe}'

        # ==================== STAGE B: Sample Loss & 5.0 s Prediction Cutoff ====================
        loss_started_at = time.monotonic()
        # Probe at ~1.0 s (extrapolating), ~4.2 s (still extrapolating; >3.0 s triggers real motion_hidden!),
        # ~5.4 s (past 5.0 s cutoff -> stale_5s!), and ~6.2 s (confirming 0.000 m post-cutoff drift!).
        loss_probes = []
        s2_loss_pushed = 0
        for label, target_elapsed in (('1.0s_predicting', 1.0), ('4.2s_predicting_after_hidden', 4.2),
                                      ('5.4s_post_cutoff', 5.4), ('6.2s_post_cutoff_hold', 6.2)):
            while time.monotonic() - loss_started_at < target_elapsed:
                if int((time.monotonic() - loss_started_at) / 0.8) > s2_loss_pushed:
                    s2_loss_pushed += 1
                    publish_secondary_next(reset_baseline=False)
                pump_all()
                time.sleep(0.01)
            p = bridge.request('probe_pose', pump=pump_all, index=primary_idx)
            loss_probes.append({
                'label': label,
                'elapsed_s': round(time.monotonic() - loss_started_at, 3),
                'x': float(p['x']), 'y': float(p['y']), 'z': float(p['z']),
                'ctrl_state': p['ctrl_state'],
                'writes': int(p['writes']),
                'readbacks': int(p['readbacks']),
                'stale_cutoffs': int(p['stale_cutoffs']),
                'player_disp_m': float(p['player_disp_m']),
            })

        pred_move_m = _dist3(loss_probes[0], loss_probes[1])
        post_cutoff_drift_m = _dist3(loss_probes[2], loss_probes[3])
        post_cutoff_extra_writes = loss_probes[3]['writes'] - loss_probes[2]['writes']
        assert loss_probes[3]['stale_cutoffs'] >= 1, f'expected 5s stale cutoff: {loss_probes}'
        assert post_cutoff_extra_writes == 0 and post_cutoff_drift_m < 0.01, (
            f'hull moved after 5s cutoff: extra_writes={post_cutoff_extra_writes}, drift={post_cutoff_drift_m}'
        )
        assert obs_a.stats()['tracks'][primary_idx]['transport_loss_count'] >= 1, 'expected motion_hidden transport loss'

        # ==================== STAGE C: Offscreen Withdrawal & Reacquisition ====================
        # Withdraw high_attention view from X4 -> Pilot A drops S from motion_views.
        w_resp = bridge.request('set_view', pump=pump_all, slot='high_attention', sector='-', indices=[])
        assert w_resp.get('status') == 'view_set'
        pump_until(lambda: primary_idx not in (client_a._sent_view or {}).get(primary_sector, ()),
                   label='stage_c_withdraw_synced')

        seq_before_withdraw = obs_a.stats()['tracks'][primary_idx]['last_sequence']
        seen_before_withdraw = udp_bridge.stats()['seen_by_x4'][primary_idx]['last_sequence']

        # Advance the captured authority trace while Pilot A is offscreen so S moves cleanly ahead
        # (>65 m past the 5 s frozen cutoff pose) while Pilot B AND Pilot A's S2 continue receiving!
        frozen_pose = {'x': loss_probes[3]['x'], 'y': loss_probes[3]['y'], 'z': loss_probes[3]['z']}
        seg2_start = seg1_end + 12
        while seg2_start < len(primary_v3) - 18:
            if _dist3(primary_v3[seg2_start - 1], frozen_pose) >= 65.0:
                break
            seg2_start += 1
        seg2_start = min(seg2_start, len(primary_v3) - 18)

        for i in range(seg1_end, seg2_start):
            sample_p = dict(primary_v3[i])
            if i == seg1_end:
                # First sample after MAX_SAMPLE_AGE expiry must re-establish baseline velocity=None.
                sample_p['real_velocity'] = None
            service.publish_npc_sample(sample_p)
            if i % 3 == 0:
                publish_secondary_next(reset_baseline=False)
            pump_for(0.03)

        pump_until(lambda: obs_b.stats()['tracks'][primary_idx]['last_sequence'] == primary_v3[seg2_start - 1]['sequence'],
                   label='stage_c_pilot_b_received_offscreen')
        assert obs_a.stats()['tracks'][primary_idx]['last_sequence'] == seq_before_withdraw, (
            'Pilot A received primary while withdrawn!'
        )
        assert udp_bridge.stats()['seen_by_x4'][primary_idx]['last_sequence'] == seen_before_withdraw

        # Re-subscribe high_attention to S from X4!
        re_resp = bridge.request('set_view', pump=pump_all, slot='high_attention',
                                 sector=primary_sector, indices=[primary_idx])
        assert re_resp.get('status') == 'view_set'
        pump_until(lambda: primary_idx in (client_a._sent_view or {}).get(primary_sector, ()),
                   label='stage_c_reacquire_view_synced')

        # Explicitly probe the >50 m guard on S (read-only refusal check)!
        refusals_before = int(bridge.request('probe_pose', pump=pump_all, index=primary_idx)['refusals_50m'])
        assert udp_bridge.send_command('PROBE_50M', primary_idx, '120.0') is True
        pump_until(lambda: any(e['kind'] == 'probe_50m' for e in udp_bridge.stats()['events']),
                   label='stage_c_probe_50m')
        refusals_after = int(bridge.request('probe_pose', pump=pump_all, index=primary_idx)['refusals_50m'])
        assert refusals_after > refusals_before, 'expected >50 m guard refusal increment'

        # Stream the remaining real captured samples (seg2_start .. len-3) at real capture cadence!
        # Leave 2 samples for post-pause/resume verification in Stage D.
        seg2_end = len(primary_v3) - 2
        reacquire_gap_m = _dist3(frozen_pose, primary_v3[seg2_start - 1])
        for i in range(seg2_start, seg2_end):
            sample_p = dict(primary_v3[i])
            service.publish_npc_sample(sample_p)
            if i + 1 < seg2_end:
                dt = max(0.05, min(0.35, primary_v3[i + 1]['capture_time'] - primary_v3[i]['capture_time']))
                pump_for(dt)
            else:
                pump_for(0.35)

        pump_until(lambda: (
            udp_bridge.stats()['seen_by_x4'].get(primary_idx, {}).get('last_sequence', 0) >= primary_v3[seg2_end - 1]['sequence']
        ), label='stage_c_reacquired_playback_seen')
        stage_c_seen_seq = udp_bridge.stats()['seen_by_x4'].get(primary_idx, {}).get('last_sequence')

        # ==================== STAGE D: Independent Later MD+FFI Readback & Pause/Save/Resume ====================
        final_readback = bridge.request('readback', pump=pump_all, index=primary_idx, min_game_delay=0.25)
        assert final_readback.get('status') == 'readback', f'final readback failed: {final_readback}'
        final_pose = {k: float(final_readback[k]) for k in ('x', 'y', 'z')}
        final_target_sample = primary_v3[seg2_end - 1]
        total_hull_displacement_m = _dist3(loaded_p0, final_pose)
        final_player_disp_m = float(final_readback['player_disp_m'])
        assert final_player_disp_m < 0.01, f'player ship moved during NPC playback: {final_player_disp_m}'

        # Pause / save / resume window transition verifying both admitted clients stay admitted & resume!
        service.set_running(False)
        pump_until(lambda: not client_a._running and not client_b._running, label='stage_d_paused')
        service.set_running(True)
        pump_until(lambda: (
            client_a._running and client_b._running
            and primary_idx in service.subscribed_npc_targets().get(primary_sector, ())
            and secondary_idx in service.subscribed_npc_targets().get(secondary_sector, ())
        ), label='stage_d_resumed')
        for offset, idx_row in enumerate(range(seg2_end, len(primary_v3))):
            sample_p = dict(primary_v3[idx_row])
            if offset == 0:
                sample_p['real_velocity'] = None
            service.publish_npc_sample(sample_p)
            publish_secondary_next(reset_baseline=(offset == 0))
            pump_for(0.15)
        pump_until(lambda: (
            obs_a.stats()['tracks'][primary_idx]['last_sequence'] == primary_v3[-1]['sequence']
            and obs_b.stats()['tracks'][primary_idx]['last_sequence'] == primary_v3[-1]['sequence']
        ), label='stage_d_post_resume_confirmed')

        done_resp = bridge.request('done', pump=pump_all)
        assert done_resp.get('status') == 'done'

        bridge_stats = udp_bridge.stats()
        readbacks = bridge_stats['readbacks']
        max_readback_err_m = max((r['error'] for r in readbacks), default=None)
        min_readback_elapsed_s = min((r['elapsed'] for r in readbacks), default=None)
        alignment_readbacks = [r for r in readbacks if r['kind'] == 'alignment_readback']

        return {
            'accepted': True,
            'cold_reload': {
                'save_slot': SAVE_SLOT,
                'total_registry': int(ready2['total_registry']),
                'primary_index': primary_idx,
                'primary_idcode': ready2['primary_idcode'],
                'primary_sector': primary_sector,
                'phase1_saved_pose': saved_p0,
                'phase2_loaded_pose': loaded_p0,
                'cold_reload_offset_m': round(cold_reload_offset_m, 4),
                'udp_peer_validation': dict(validator.info),
            },
            'stage_a_same_ship_playback': {
                'samples_streamed': seg1_end,
                'x4_seen_seq': stage_a_seen_seq,
                'writes': stage_a_writes,
                'readbacks': stage_a_readbacks,
                'hull_displacement_m': round(stage_a_disp_m, 3),
                'pose_at_stage_a_end': stage_a_pose,
                'captured_sample_at_stage_a_end': {k: stage_a_target_ref[k] for k in ('sequence', 'x', 'y', 'z', 'real_velocity')},
                'dist_to_latest_captured_sample_m': round(stage_a_dist_to_target_m, 3),
            },
            'stage_b_loss_and_5s_cutoff': {
                'transport_loss_notifications': obs_a.stats()['tracks'][primary_idx]['transport_loss_count'],
                'loss_packets_sent_to_x4': bridge_stats['loss_sent'],
                'predicted_displacement_1s_to_4_2s_m': round(pred_move_m, 3),
                'post_cutoff_drift_5_4s_to_6_2s_m': round(post_cutoff_drift_m, 4),
                'post_cutoff_extra_writes': post_cutoff_extra_writes,
                'probes': loss_probes,
            },
            'stage_c_withdrawal_and_reacquisition': {
                'pilot_a_seq_before_and_during_offscreen': seq_before_withdraw,
                'pilot_a_withdrawal_count': obs_a.stats()['tracks'][primary_idx]['withdrawal_count'],
                'pilot_a_reacquisition_count': obs_a.stats()['tracks'][primary_idx]['reacquisition_count'],
                'pilot_b_seq_during_offscreen': primary_v3[seg2_start - 1]['sequence'],
                'reacquire_gap_from_frozen_m': round(reacquire_gap_m, 3),
                'over_50m_refusals': int(final_readback['refusals_50m']),
                'alignments_performed': int(final_readback['alignments']),
                'alignment_readbacks': alignment_readbacks,
                'post_reacquire_samples_streamed': seg2_end - seg2_start,
                'x4_seen_seq_after_reacquire': stage_c_seen_seq,
            },
            'stage_d_independent_readback_and_resume': {
                'independent_md_ffi_readback': final_readback,
                'final_captured_sample_before_pause': {k: final_target_sample[k] for k in ('sequence', 'x', 'y', 'z', 'real_velocity')},
                'total_hull_displacement_m': round(total_hull_displacement_m, 3),
                'player_displacement_m': round(final_player_disp_m, 4),
                'total_x4_writes': int(final_readback['writes']),
                'total_x4_readbacks': int(final_readback['readbacks']),
                'udp_readbacks_logged': len(readbacks),
                'max_udp_readback_error_m': round(max_readback_err_m, 5) if max_readback_err_m is not None else None,
                'min_udp_readback_elapsed_s': round(min_readback_elapsed_s, 5) if min_readback_elapsed_s is not None else None,
                'pause_resume_world_resets_pilot_a': obs_a.stats()['world_resets'],
                'post_resume_primary_seq': obs_a.stats()['tracks'][primary_idx]['last_sequence'],
                'post_resume_secondary_seq': obs_a.stats()['tracks'][secondary_idx]['last_sequence'],
            },
            'udp_events': bridge_stats['events'],
            'udp_readbacks_sample': readbacks[:12] + (readbacks[-6:] if len(readbacks) > 12 else []),
            'service_totals': {
                'accepted_npc_samples': service.accepted_npc_samples,
                'npc_samples_with_velocity': service.npc_samples_with_velocity,
            },
            'log_path': str(log_path),
        }
    finally:
        if bridge is not None:
            bridge.stop()
        _terminate_x4(process, job_handle)
        if client_a is not None:
            client_a.close()
        if client_b is not None:
            client_b.close()
        service.close()
        udp_bridge.close()
        if log_path.is_file():
            shutil.copyfile(log_path, out / log_path.name)


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
    ok, message = steam.status()
    if not ok:
        raise SystemExit(message)
    game = Path(args.game) if args.game else game_mod.find_install()
    game_mod.verify_exe(game)
    view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-npc-motion-{out.name}'
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
    (out / 'protected-before.json').write_text(json.dumps(protected_before, indent=1, sort_keys=True))

    result = {
        'options': vars(args),
        'game': str(game),
        'view': str(view),
        'outcome': 'started',
    }
    ctypes.windll.user32.SetProcessDPIAware()
    try:
        game_mod.create_view(game, view)
        profile_dir = profile_mod.prepare(
            args.profile,
            [*game_mod.dlc_ids(view), EXTENSION_ID],
            width=args.width,
            height=args.height,
            fast=True,
        )

        # Phase 1: Native authoritative X4 universe capture + disposable save_009 + GetCurRealTime profiling.
        phase1 = run_phase1_capture(view, profile_dir, out, args)
        (out / 'captured-trace.json').write_text(json.dumps({
            'ready': phase1['ready'],
            'primary_rows': phase1['primary_rows'],
            'secondary_rows': phase1['secondary_rows'],
        }, indent=1))

        # Wait briefly to confirm Phase 1 X4 process has completely exited before launching Phase 2.
        for _ in range(40):
            if not x4_running():
                break
            time.sleep(0.25)
        if x4_running():
            raise RuntimeError('Phase 1 X4.exe still running before Phase 2 launch')

        # Phase 2: Fresh observing Windows client X4 cold-loading save_009 and playing back SAME ship via admitted client!
        phase2 = run_phase2_observe_playback(view, profile_dir, out, args, phase1)

        result['phase1_capture'] = {
            'universe_registry': {
                'total_enrolled_ships': int(phase1['ready']['total_registry']),
                'populate_ms': float(phase1['ready']['populate_ms']),
                'candidates_sampled': int(phase1['ready']['candidates']),
            },
            'primary': {
                'index': int(phase1['ready']['primary_index']),
                'idcode': phase1['ready']['primary_idcode'],
                'actor_key': phase1['ready']['primary_actor'],
                'sector': phase1['ready']['primary_sector'],
                'scout_speed_mps': float(phase1['ready']['primary_speed']),
                'captured_samples': len(phase1['primary_rows']),
            },
            'secondary': {
                'index': int(phase1['ready']['secondary_index']),
                'idcode': phase1['ready']['secondary_idcode'],
                'actor_key': phase1['ready']['secondary_actor'],
                'sector': phase1['ready']['secondary_sector'],
                'scout_speed_mps': float(phase1['ready']['secondary_speed']),
                'captured_samples': len(phase1['secondary_rows']),
            },
            'saved_files': phase1['saved_files'],
            'native_ffi_capture_profile': phase1['native_profile'],
        }
        result['phase2_same_ship_playback'] = phase2
        result['synthetic_10082_scalability'] = benchmark_10082_scalability(counts=(1, 16, 64, 256), rounds=10)
        result['outcome'] = 'done'
    except BaseException as exc:
        result['outcome'] = 'error'
        result['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        # Remove disposable save_009 after run to leave disposable lane clean.
        try:
            disp_save = profile_mod.profile_dir(args.profile) / 'save'
            if disp_save.is_dir():
                for f in disp_save.glob(f'{SAVE_SLOT}*'):
                    f.unlink()
        except OSError:
            pass
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
        'accepted': result.get('phase2_same_ship_playback', {}).get('accepted'),
        'protected_unchanged': result['protected_unchanged'],
        'view_removed': result['view_removed'],
        'x4_running_after': result['x4_running_after'],
        'out': str(out),
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
