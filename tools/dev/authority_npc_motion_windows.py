"""Isolated native Windows X4 9.00 end-to-end NPC motion, visibility, playback, and profiling runner.

Demonstrates on a real Windows X4 9.00 instance (disposable profile 45850,
isolated x4ls view, 800x600 fast=True HWND_BOTTOM):
  1. Full universe registry population via X4AuthorityShipRegistry (reporting exact
     live universe ship count + populate time).
  2. Real live moving-NPC V3 capture (game time, monotonic capture time, finite-
     difference real_velocity) routed through CompanyMotionService to TWO admitted
     CompanyMotionClient + NpcMotionObserver peers (two tracks on Pilot A across
     high-attention + map views, plus Pilot B concurrently).
  3. Offscreen interest withdrawal on Pilot A (0 deliveries while offscreen; Pilot A's
     second track and Pilot B's stream continue uninterrupted) + reacquisition when
     visible again, retaining the last nonzero-velocity sample.
  4. Guarded local X4 hull playback (SetObjectSectorPos + constantvel, <= 50 m limit,
     same-zone check, >50 m refusal) and independent later X4 readback.
  5. Pause/save/resume clock transition (set_running False -> True) keeping both
     pilots admitted and resuming live NPC delivery.
  6. Native X4 multi-ship FFI capture profiling (N = 1, 5, 16, 32) plus a separate
     explicitly labeled synthetic 10,082-entry loopback transport scalability sweep
     (N = 1, 16, 64, 256 visible ships).
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
LOGFILE = 'neon-npc-motion.log'
REGISTRY_XML = ROOT / 'tools/dev/authority_ship_registry.xml'
MOTION_XML = ROOT / 'tools/dev/authority_npc_motion.xml'
MOTION_LUA = ROOT / 'tools/dev/authority_npc_motion_x4.lua'

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
    'description="Disposable opt-in NPC motion capture, playback, and profiling harness" '
    'author="Neon" version="100" date="2026-11-24" save="false">\n'
    '  <dependency version="900" />\n'
    '</content>\n'
)

UI_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<addon name="{EXTENSION_ID}">\n'
    '  <environment type="menus">\n'
    '    <dependency name="ego_detailmonitor" />\n'
    '    <file name="ui/config.lua" />\n'
    '    <file name="ui/authority_npc_motion_x4.lua" />\n'
    '  </environment>\n'
    '</addon>\n'
)


def config_lua(profile, gamestart, epoch):
    return (
        'X4NEON_NPC_MOTION = {\n'
        '  enabled = true,\n'
        f'  extension_id = "{EXTENSION_ID}",\n'
        f'  profile = "{int(profile)}",\n'
        f'  gamestart = "{gamestart}",\n'
        f'  epoch = "{epoch}",\n'
        '}\n'
    )


def compose_extension(extensions_dir, *, profile, gamestart, epoch):
    if not gamestart.replace('_', '').isalnum():
        raise SystemExit('invalid gamestart id')
    target = Path(extensions_dir) / EXTENSION_ID
    target.mkdir()
    (target / 'ui').mkdir()
    (target / 'md').mkdir()
    shutil.copyfile(REGISTRY_XML, target / 'md' / 'authority_ship_registry.xml')
    shutil.copyfile(MOTION_XML, target / 'md' / 'authority_npc_motion.xml')
    shutil.copyfile(MOTION_LUA, target / 'ui' / 'authority_npc_motion_x4.lua')
    (target / 'ui' / 'config.lua').write_text(config_lua(profile, gamestart, epoch), encoding='ascii')
    (target / 'content.xml').write_text(CONTENT_XML, encoding='utf-8')
    (target / 'ui.xml').write_text(UI_XML, encoding='utf-8')
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
                ('ActiveProcessLimit', wintypes.DWORD),
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
            time.sleep(0.05)

    def wait_ready(self, timeout=180.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.ready_info is None and self.error is None:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError('timed out waiting for X4 NPC motion READY')
                self._cond.wait(min(0.25, rem))
            if self.error is not None:
                raise RuntimeError(f'X4 error before READY: {self.error}')
            return dict(self.ready_info)

    def _lua_value(self, value):
        if isinstance(value, bool):
            return 'true' if value else 'false'
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, (list, tuple)):
            return '{ ' + ', '.join(self._lua_value(item) for item in value) + ' }'
        return json.dumps(str(value))

    def request(self, op, **params):
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

        deadline = time.monotonic() + self.timeout
        with self._cond:
            while seq not in self.responses and self.error is None:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError(f'timed out waiting for X4 response to seq={seq} op={op}')
                self._cond.wait(min(0.2, rem))
            if self.error is not None:
                raise RuntimeError(f'X4 error during seq={seq} op={op}: {self.error}')
            return dict(self.responses[seq])

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
        dt = gt - float(prev['game_time'])
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
                # Spread N indices evenly across 1..10,082 (avoiding player seat 1970) across 2 sectors.
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


def run_live_x4_session(bridge, ready_info):
    """Drive real CompanyMotionService + 2 CompanyMotionClients against live X4 9.00 captures & playback."""
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.npc_motion_observer import NpcMotionObserver

    primary_idx = int(ready_info['primary_index'])
    secondary_idx = int(ready_info['secondary_index'])
    playback_idx = int(ready_info['playback_index'])
    primary_sector = ready_info['primary_sector']
    secondary_sector = ready_info['secondary_sector']
    playback_sector = ready_info['playback_sector']
    # Use synthetic admitted pilot seat indices guaranteed distinct from enrolled NPCs
    pilot_a_seat = max(199001, int(ready_info['total_registry']) + 10)
    pilot_b_seat = pilot_a_seat + 1

    service = CompanyMotionService()
    client_a = client_b = None
    try:
        grant_a = service.admit(1, 'a1' * 16, pilot_a_seat, primary_sector, SESSION, EPOCH)
        grant_b = service.admit(2, 'b2' * 16, pilot_b_seat, primary_sector, SESSION, EPOCH)
        service.set_running(True)

        obs_a = NpcMotionObserver(EPOCH, pilot_a_seat)
        obs_b = NpcMotionObserver(EPOCH, pilot_b_seat)
        client_a = CompanyMotionClient(
            service.port, grant_a, _IdlePilotSource(EPOCH, pilot_a_seat), lambda: True,
            on_snapshot=obs_a.on_snapshot, on_end=obs_a.on_end, on_view_reset=obs_a.on_view_reset,
        )
        client_b = CompanyMotionClient(
            service.port, grant_b, _IdlePilotSource(EPOCH, pilot_b_seat), lambda: True,
            on_snapshot=obs_b.on_snapshot, on_end=obs_b.on_end, on_view_reset=obs_b.on_view_reset,
        )
        obs_a.bind_views(client_a.set_views)
        obs_b.bind_views(client_b.set_views)

        def pump_until(pred, timeout=10.0, label='condition'):
            deadline = time.monotonic() + timeout
            while not pred():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'timed out waiting for {label}')
                service.poll()
                client_a.poll()
                client_b.poll()
                time.sleep(0.002)

        pump_until(lambda: client_a._running and client_b._running, label='clients_running')

        # Stage 1: Pilot A subscribes to BOTH Primary (high_attention) and Secondary (map); Pilot B subscribes to Primary.
        obs_a.set_slot_view('high_attention', primary_sector, [primary_idx])
        obs_a.set_slot_view('map', secondary_sector, [secondary_idx])
        obs_b.set_slot_view('high_attention', primary_sector, [primary_idx])
        pump_until(lambda: (
            primary_idx in service.subscribed_npc_targets().get(primary_sector, ())
            and secondary_idx in service.subscribed_npc_targets().get(secondary_sector, ())
        ), label='initial_views')

        prev_p = prev_s = None
        seq_p = seq_s = 0
        captured_primary = []
        captured_secondary = []

        def capture_and_publish(reset_baseline=False, delay=0.25):
            nonlocal prev_p, prev_s, seq_p, seq_s
            raw_p = bridge.request('capture', index=primary_idx, min_game_delay=delay if prev_p else 0.0)
            raw_s = bridge.request('capture', index=secondary_idx, min_game_delay=0.0)
            seq_p += 1
            seq_s += 1
            sp = compute_v3_sample(prev_p, raw_p, index=primary_idx, generation=GEN_PRIMARY,
                                   sequence=seq_p, reset_baseline=reset_baseline)
            ss = compute_v3_sample(prev_s, raw_s, index=secondary_idx, generation=GEN_SECONDARY,
                                   sequence=seq_s, reset_baseline=reset_baseline)
            prev_p, prev_s = raw_p, raw_s
            captured_primary.append(sp)
            captured_secondary.append(ss)
            assert service.publish_npc_sample(sp) is True
            assert service.publish_npc_sample(ss) is True
            return sp, ss

        for i in range(4):
            capture_and_publish(reset_baseline=(i == 0), delay=0.25)
            pump_until(lambda q=seq_p, qs=seq_s: (
                obs_a.stats()['tracks'].get(primary_idx, {}).get('last_sequence') == q
                and obs_a.stats()['tracks'].get(secondary_idx, {}).get('last_sequence') == qs
                and obs_b.stats()['tracks'].get(primary_idx, {}).get('last_sequence') == q
            ), label=f'stage1_seq_{seq_p}')

        stage1_primary = obs_a.stats()['tracks'][primary_idx]
        assert stage1_primary['last_nonzero_velocity_confirmed'] is not None, 'expected retained moving sample'

        # Stage 2: Offscreen interest withdrawal on Pilot A (Secondary on Pilot A + Primary on Pilot B continue).
        obs_a.clear_slot('high_attention')
        pump_until(lambda: primary_idx not in (client_a._sent_view or {}).get(primary_sector, ()),
                   label='stage2_withdraw')
        seq_before_withdraw = obs_a.stats()['tracks'][primary_idx]['last_sequence']
        for _ in range(2):
            capture_and_publish(reset_baseline=False, delay=0.25)
            pump_until(lambda q=seq_p, qs=seq_s: (
                obs_a.stats()['tracks'][secondary_idx]['last_sequence'] == qs
                and obs_b.stats()['tracks'][primary_idx]['last_sequence'] == q
            ), label=f'stage2_offscreen_seq_{seq_p}')
        # Verify Pilot A received ZERO deliveries of Primary while offscreen!
        assert obs_a.stats()['tracks'][primary_idx]['last_sequence'] == seq_before_withdraw
        assert obs_a.stats()['tracks'][primary_idx]['withdrawal_count'] == 1

        # Stage 3: Reacquisition when Primary becomes visible again on Pilot A + local X4 hull playback & readback.
        obs_a.set_slot_view('high_attention', primary_sector, [primary_idx])
        pump_until(lambda: primary_idx in (client_a._sent_view or {}).get(primary_sector, ()),
                   label='stage3_reacquire_view')
        capture_and_publish(reset_baseline=False, delay=0.25)
        pump_until(lambda q=seq_p: obs_a.stats()['tracks'][primary_idx]['last_sequence'] == q,
                   label='stage3_reacquired_sample')
        reacquired_track = obs_a.stats()['tracks'][primary_idx]
        assert reacquired_track['reacquisition_count'] == 1
        assert reacquired_track['reacquired_after_withdrawal'] is True

        # Local X4 playback on registered playback_idx driven by the retained real moving velocity (clamped to <=25 m/s
        # along its real direction so it stays inside the same zone and <=50 m step limit), then later X4 readback.
        retained_vel = reacquired_track['last_nonzero_velocity_confirmed']['real_velocity']
        speed = math.sqrt(retained_vel['x'] ** 2 + retained_vel['y'] ** 2 + retained_vel['z'] ** 2)
        scale = min(1.0, 20.0 / speed) if speed > 1e-6 else 0.0
        play_vx = round(retained_vel['x'] * scale, 4)
        play_vy = round(retained_vel['y'] * scale, 4)
        play_vz = round(retained_vel['z'] * scale, 4)
        play_speed = math.sqrt(play_vx ** 2 + play_vy ** 2 + play_vz ** 2)

        pb_before = bridge.request('capture', index=playback_idx)
        target_x = float(pb_before['x']) + (play_vx * 0.5)
        target_y = float(pb_before['y']) + (play_vy * 0.5)
        target_z = float(pb_before['z']) + (play_vz * 0.5)
        pb_apply = bridge.request(
            'playback_step', index=playback_idx, expected_sector=playback_sector,
            x=target_x, y=target_y, z=target_z, vx=play_vx, vy=play_vy, vz=play_vz,
        )
        assert pb_apply.get('status') == 'playback_applied', f'playback failed: {pb_apply}'

        pb_over = bridge.request(
            'playback_step', index=playback_idx, expected_sector=playback_sector,
            x=target_x + 120.0, y=target_y, z=target_z,
        )
        assert pb_over.get('status') == 'refused_over_50m', f'expected 50m refusal: {pb_over}'

        pb_readback = bridge.request('readback', index=playback_idx, min_game_delay=1.2)
        assert pb_readback.get('status') == 'readback'
        dt_pb = float(pb_readback['game_time']) - float(pb_apply['game_time'])
        disp_m = math.sqrt(
            (float(pb_readback['x']) - float(pb_apply['x'])) ** 2 +
            (float(pb_readback['y']) - float(pb_apply['y'])) ** 2 +
            (float(pb_readback['z']) - float(pb_apply['z'])) ** 2
        )
        expected_disp_m = play_speed * dt_pb

        # Stage 4: Pause / save / resume window transition keeping both admitted observers alive.
        service.set_running(False)
        pump_until(lambda: not client_a._running and not client_b._running, label='stage4_paused')
        service.set_running(True)
        pump_until(lambda: (
            client_a._running and client_b._running
            and primary_idx in service.subscribed_npc_targets().get(primary_sector, ())
            and secondary_idx in service.subscribed_npc_targets().get(secondary_sector, ())
        ), label='stage4_resumed')
        capture_and_publish(reset_baseline=True, delay=0.2)
        capture_and_publish(reset_baseline=False, delay=0.25)
        pump_until(lambda q=seq_p, qs=seq_s: (
            obs_a.stats()['tracks'][primary_idx]['last_sequence'] == q
            and obs_a.stats()['tracks'][secondary_idx]['last_sequence'] == qs
            and obs_b.stats()['tracks'][primary_idx]['last_sequence'] == q
        ), label='stage4_post_resume_delivery')

        # Stage 5: Native X4 multi-ship FFI capture profile across N = 1, 5, 16, 32 enrolled ships.
        native_profile = []
        max_cand = int(ready_info['candidates'])
        for n in (1, 5, 16, 32):
            if n <= max_cand:
                native_profile.append(bridge.request('profile_capture', count=n, iters=20))

        done_resp = bridge.request('done')
        assert done_resp.get('status') == 'done'

        return {
            'accepted': True,
            'universe_registry': {
                'total_enrolled_ships': int(ready_info['total_registry']),
                'populate_ms': float(ready_info['populate_ms']),
                'candidates_sampled': max_cand,
            },
            'ships': {
                'primary': {
                    'index': primary_idx, 'idcode': ready_info['primary_idcode'],
                    'actor_key': ready_info['primary_actor'], 'sector': primary_sector,
                    'scout_speed_mps': float(ready_info['primary_speed']),
                },
                'secondary': {
                    'index': secondary_idx, 'idcode': ready_info['secondary_idcode'],
                    'actor_key': ready_info['secondary_actor'], 'sector': secondary_sector,
                    'scout_speed_mps': float(ready_info['secondary_speed']),
                },
                'playback': {
                    'index': playback_idx, 'idcode': ready_info['playback_idcode'],
                    'actor_key': ready_info['playback_actor'], 'sector': playback_sector,
                },
            },
            'stage1_retained_moving_sample': stage1_primary['last_nonzero_velocity_confirmed'],
            'stage2_withdrawal': {
                'pilot_a_seq_before_and_during_offscreen': seq_before_withdraw,
                'pilot_a_withdrawal_count': obs_a.stats()['tracks'][primary_idx]['withdrawal_count'],
                'pilot_a_secondary_continued': True,
                'pilot_b_primary_continued': True,
            },
            'stage3_reacquisition_and_playback': {
                'pilot_a_reacquisition_count': obs_a.stats()['tracks'][primary_idx]['reacquisition_count'],
                'pilot_a_reacquired_after_withdrawal': obs_a.stats()['tracks'][primary_idx]['reacquired_after_withdrawal'],
                'requested_playback_velocity_mps': {'x': play_vx, 'y': play_vy, 'z': play_vz, 'speed': round(play_speed, 4)},
                'playback_apply': pb_apply,
                'over_50m_refusal': pb_over,
                'later_readback': pb_readback,
                'readback_dt_game_s': round(dt_pb, 4),
                'measured_displacement_m': round(disp_m, 3),
                'expected_displacement_m': round(expected_disp_m, 3),
            },
            'stage4_pause_save_resume': {
                'pilot_a_world_resets': obs_a.stats()['world_resets'],
                'pilot_b_world_resets': obs_b.stats()['world_resets'],
                'post_resume_primary_seq': obs_a.stats()['tracks'][primary_idx]['last_sequence'],
                'post_resume_secondary_seq': obs_a.stats()['tracks'][secondary_idx]['last_sequence'],
            },
            'stage5_native_x4_capture_profile': native_profile,
            'observer_a_stats': obs_a.stats(),
            'observer_b_stats': obs_b.stats(),
            'service_totals': {
                'accepted_npc_samples': service.accepted_npc_samples,
                'npc_samples_with_velocity': service.npc_samples_with_velocity,
            },
        }
    finally:
        if client_a is not None:
            client_a.close()
        if client_b is not None:
            client_b.close()
        service.close()


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
    process = log_path = bridge = job_handle = None
    ctypes.windll.user32.SetProcessDPIAware()
    try:
        game_mod.create_view(game, view)
        ext_dir = compose_extension(
            view / 'extensions', profile=args.profile, gamestart=args.gamestart, epoch=EPOCH,
        )
        profile_dir = profile_mod.prepare(
            args.profile,
            [*game_mod.dlc_ids(view), EXTENSION_ID],
            width=args.width,
            height=args.height,
            fast=True,
        )
        log_path = profile_dir / LOGFILE
        command = [
            str(view / 'X4.exe'),
            '-personalfolderid', str(args.profile),
            '-skipintro', '-noworkshopsync', '-nocputhrottle',
            '-debug', 'scripts', '-logfile', LOGFILE,
        ]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWNOACTIVATE
        process = subprocess.Popen(
            command, cwd=view, env=steam.client_environment(), startupinfo=si,
        )
        job_handle = attach_kill_on_close_job(process.pid)
        result['pid'] = process.pid
        bridge = X4MotionBridge(process, log_path, ext_dir, timeout=60.0)
        ready_info = bridge.wait_ready(timeout=args.timeout)
        result['native_run'] = run_live_x4_session(bridge, ready_info)
        result['synthetic_10082_scalability'] = benchmark_10082_scalability()
        result['outcome'] = 'done'
    except BaseException as exc:
        result['outcome'] = 'error'
        result['error'] = f'{type(exc).__name__}: {exc}'
        if bridge is not None:
            result['x4_events'] = list(bridge.events)
    finally:
        if bridge is not None:
            bridge.stop()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(25)
        if job_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(job_handle)
            except BaseException:
                pass
        if process is not None:
            result['exit_code'] = process.returncode
        time.sleep(1.5)
        if log_path is not None and log_path.is_file():
            shutil.copyfile(log_path, out / LOGFILE)
        try:
            remove_view_patiently(game_mod, view)
        except OSError as exc:
            result['cleanup_error'] = f'{type(exc).__name__}: {exc}'
        protected_after = {
            'other_profiles': {
                entry.name: tree_metadata(entry)
                for entry in sorted(documents.iterdir())
                if entry.is_dir() and entry.name != str(args.profile)
            },
            'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
        }
        (out / 'protected-after.json').write_text(json.dumps(protected_after, indent=1, sort_keys=True))
        result['protected_unchanged'] = protected_after == protected_before
        result['view_removed'] = not view.exists()
        result['x4_running_after'] = x4_running()
        (out / 'result.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
        print(json.dumps(result, indent=2, default=str))
    return 0 if (result['outcome'] == 'done' and result['protected_unchanged']
                 and result['view_removed'] and not result['x4_running_after']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
