#!/usr/bin/env python3
"""Two-phase native Windows 11 X4 9.00 check of ordinary Windows NPC receive/playback.

Exercises the EXACT packaged network/x4lan_net_64.dll (6ad97e04..., QPC udp.monotonic)
and unmodified b418a409 Windows receive/playback modules:
  CompanyMotionService -> CompanyMotionClient -> CompanyMotionSource ->
  WindowsMotionPeer (GetExtendedUdpTable) -> CompanyMotionObserver ->
  X4LAN_LOCAL_UDP_MODULE -> X4LAN_COMPANY_OBSERVER_STREAM ->
  X4LAN_COMPANY_COCKPIT_PLAYBACK -> X4LAN_COMPANY_HULL_PLAYBACK ->
  X4LAN_MOTION_BUFFER + X4LAN_MOTION_PLAYBACK
paired with a thin opt-in NPC observer/pose adapter in tools/dev/ (zero edits to
src/authority/) while empirically confirming that unmodified
X4LAN_COMPANY_HULL_PLAYBACK_X4 refuses the NPC target.

Two phases (separate native Windows X4 9.00 processes, disposable profile 45850):
  1. Phase 1 (Authoritative Capture X4): populates X4AuthorityShipRegistry across
     player.galaxy, spawns & enrolls one unrelated visible uncrewed ship 450 m ahead
     of the player in player.sector, selects a real moving non-player AI-piloted NPC
     in player.sector, saves save_009.xml.gz, and records ~9-10 s of genuine AI
     flight with packaged udp.monotonic().
  2. Phase 2 (Observing Windows Client X4): cold-reloads save_009.xml.gz, locks the
     unrelated visible ship on the stock HUD, streams the recorded Phase 1 authority
     NPC trajectory (EXPLICITLY LABELED as recorded from Phase 1 native X4) through
     real loopback CompanyMotionService -> CompanyMotionObserver -> Windows UDP ->
     ordinary playback onto that exact registered NPC, and compares sender identity,
     velocity, observer SEEN confirmation, delayed playback readbacks, and independent
     final readbacks on the moving NPC, unrelated visible ship, and local own ship.
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
import secrets
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT / 'tools/dev')]

from authority_company_cockpit_windows import (
    EPOCH, NONCE, SESSION, SW_SHOWNOACTIVATE, STARTF_USESHOWWINDOW,
    _dist3, attach_kill_on_close_job, find_window, remove_view_patiently,
    tree_metadata, tuck_window_bottom_corner, x4_running,
)
from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_service import CompanyMotionService
from authority.company_motion_source import CompanyMotionSource
from authority.windows_motion_peer import WindowsMotionPeer, _AF_INET, _get_extended_udp_table, _load_udp_table_function
from authority.windows_x4_process import WindowsX4Process
from lockstep.launcher import game as launcher_game
from lockstep.launcher import profile as launcher_profile
from lockstep.launcher import steam as launcher_steam

EXTENSION_ID = 'x4_lan_lab'
PROFILE_DEFAULT = 45850
EXPECTED_PACKAGED_DLL_SHA256 = '6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0'
SHARED_LOCKFILE = ROOT.parent / '.x4-native-test.lock'

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
    except OSError:
        pass


class _LiveChildProcessOwner(WindowsX4Process):
    def __init__(self, popen):
        super().__init__(enabled=True)
        self._popen = popen
        self.pid = int(popen.pid)
        self._phase = 'running'

    def owns_running_pid(self, pid):
        return int(pid) == self.pid and self._popen.poll() is None


class RecordedAuthorityNpcSource:
    """Replays a Phase-1 recorded native X4 NPC trajectory into CompanyMotionClient.

    Explicitly labeled as a recorded native authority trace: positions, velocities,
    and inter-sample dt come directly from Phase 1 native Windows X4 9.00 capture,
    while capture_time is monotonically re-anchored to Phase 2's Windows QPC
    (time.perf_counter()) timeline so live freshness checks hold without fabricating motion.
    """

    def __init__(self, epoch, npc_index, samples, *, local_generation=None):
        self.registry = (epoch, int(npc_index))
        self.local_generation = local_generation or secrets.token_hex(16)
        self._samples = list(samples)
        self._cursor = 0
        self._armed = False
        self._start_qpc = None
        self._rec_t0 = float(self._samples[0]['capture_time']) if self._samples else 0.0
        self.emitted = []

    def arm(self):
        if not self._armed:
            self._armed = True
            self._start_qpc = time.perf_counter()

    @property
    def done(self):
        return self._cursor >= len(self._samples)

    def poll(self, _validate_peer):
        if not self._armed or self._cursor >= len(self._samples):
            return None
        row = self._samples[self._cursor]
        rel_dt = float(row['capture_time']) - self._rec_t0
        now_qpc = time.perf_counter()
        if now_qpc - self._start_qpc < rel_dt:
            return None
        self._cursor += 1
        # Keep exact recorded velocity for seq>=2; CompanyMotionClient strips seq=1 velocity cleanly.
        vel = row['real_velocity']
        reanchored_capture = self._start_qpc + rel_dt
        sample = {
            'version': 3,
            'epoch': self.registry[0],
            'generation': self.local_generation,
            'index': self.registry[1],
            'sequence': int(row['seq']),
            'sector_macro': row['sector_macro'],
            'source_time': float(row['game_time']),
            'capture_time': reanchored_capture,
            'x': float(row['x']),
            'y': float(row['y']),
            'z': float(row['z']),
            'real_velocity': (
                {'x': float(vel['x']), 'y': float(vel['y']), 'z': float(vel['z'])}
                if vel is not None else None
            ),
        }
        self.emitted.append({
            'sequence': sample['sequence'],
            'index': sample['index'],
            'sector_macro': sample['sector_macro'],
            'source_time': sample['source_time'],
            'capture_time': sample['capture_time'],
            'original_phase1_capture_time': float(row['capture_time']),
            'emitted_at_qpc': now_qpc,
            'position': {'x': sample['x'], 'y': sample['y'], 'z': sample['z']},
            'real_velocity': sample['real_velocity'],
            'speed_mps': (
                math.hypot(vel['x'], vel['y'], vel['z']) if vel is not None else 0.0
            ),
        })
        return sample


def parse_kv(text):
    out = {}
    for tok in text.split():
        if '=' in tok:
            k, v = tok.split('=', 1)
            out[k] = v
    return out


def parse_xyz(text):
    a, b, c = (float(x) for x in text.split(','))
    return {'x': a, 'y': b, 'z': c}


def count_project_errors(log_text):
    errors = []
    for line in log_text.splitlines():
        if '[X4LAN]' in line:
            if 'AUTH_NPC_ERROR' in line or 'AUTH_NPC_PLAYBACK_STOP' in line:
                errors.append(line.strip())
            continue
        if 'Could not find signature file' in line:
            continue
        if '[=ERROR=]' in line and ('x4_lan_lab' in line or 'X4Authority' in line or 'X4LAN' in line or 'authority_' in line):
            errors.append(line.strip())
    return errors


def compose_extension(extensions_dir, *, dll_path, phase, profile, gamestart, epoch, extra_cfg=None):
    ext = Path(extensions_dir) / EXTENSION_ID
    if ext.exists():
        shutil.rmtree(ext)
    for sub in ('ui', 'md', 'native'):
        (ext / sub).mkdir(parents=True)

    (ext / 'content.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<content id="{EXTENSION_ID}" name="X4 Neon Windows NPC Playback Check" '
        'description="Disposable two-phase Windows registered-NPC receive/playback check" '
        'author="Neon" version="100" date="2026-11-25" save="false">\n'
        '  <dependency version="900" />\n'
        '</content>\n',
        encoding='utf-8',
    )

    ui_sources = [
        ('authority_registry_resolve_string.lua', 'tools/dev/authority_registry_resolve_string.lua'),
        ('local_udp_module.lua', 'src/authority/lua/local_udp_module.lua'),
    ]
    if phase == 'playback':
        ui_sources.extend([
            ('motion_buffer.lua', 'src/authority/lua/motion_buffer.lua'),
            ('motion_playback.lua', 'src/authority/lua/motion_playback.lua'),
            ('company_hull_playback.lua', 'src/authority/lua/company_hull_playback.lua'),
            ('company_hull_playback_x4.lua', 'src/authority/lua/company_hull_playback_x4.lua'),
            ('company_cockpit_playback.lua', 'src/authority/lua/company_cockpit_playback.lua'),
            ('company_observer_stream.lua', 'src/authority/lua/company_observer_stream.lua'),
        ])
    ui_sources.append(('authority_windows_npc_playback_x4.lua', 'tools/dev/authority_windows_npc_playback_x4.lua'))

    md_sources = [
        ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
        ('authority_windows_npc_playback_probe.xml', 'tools/dev/authority_windows_npc_playback_probe.xml'),
    ]

    for dst_name, rel_src in ui_sources:
        src = ROOT / rel_src
        if dst_name == 'local_udp_module.lua':
            text = src.read_text(encoding='utf-8').replace('\r\n', '\n')
            assert text.endswith('return M\n')
            text = text[:-len('return M\n')] + 'X4LAN_LOCAL_UDP_MODULE=M\nreturn M\n'
            (ext / 'ui' / dst_name).write_text(text, encoding='utf-8')
        elif dst_name == 'motion_buffer.lua':
            text = src.read_text(encoding='utf-8').replace('\r\n', '\n')
            idx = text.rfind('return M')
            assert idx >= 0
            text = text[:idx] + 'X4LAN_MOTION_BUFFER = M\n' + text[idx:]
            (ext / 'ui' / dst_name).write_text(text, encoding='utf-8')
        else:
            shutil.copyfile(src, ext / 'ui' / dst_name)

    for dst_name, rel_src in md_sources:
        shutil.copyfile(ROOT / rel_src, ext / 'md' / dst_name)

    dst_dll = ext / 'native' / 'x4lan_net_64.dll'
    shutil.copyfile(dll_path, dst_dll)
    installed_dll_sha256 = hashlib.sha256(dst_dll.read_bytes()).hexdigest()
    assert installed_dll_sha256 == EXPECTED_PACKAGED_DLL_SHA256

    cfg_lines = [
        'X4NEON_WINDOWS_NPC_PLAYBACK = {',
        '  enabled = true,',
        f'  phase = {json.dumps(phase)},',
        f'  profile = {json.dumps(str(profile))},',
        f'  gamestart = {json.dumps(gamestart)},',
        f'  epoch = {json.dumps(epoch)},',
        f'  nonce = {json.dumps(NONCE)},',
        '  save_slot = "save_009",',
    ]
    for k, v in (extra_cfg or {}).items():
        if isinstance(v, bool):
            cfg_lines.append(f'  {k} = {"true" if v else "false"},')
        elif isinstance(v, int):
            cfg_lines.append(f'  {k} = {v},')
        else:
            cfg_lines.append(f'  {k} = {json.dumps(str(v))},')
    cfg_lines.append('}\n')
    (ext / 'ui/config.lua').write_text('\n'.join(cfg_lines), encoding='ascii')

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


def launch_unobtrusive_x4(exe_path, view_root, profile_id, logfile_name):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = SW_SHOWNOACTIVATE
    cmd = [
        str(exe_path),
        '-personalfolderid', str(int(profile_id)),
        '-skipintro',
        '-noworkshopsync',
        '-nocputhrottle',
        '-debug', 'scripts',
        '-logfile', logfile_name,
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(view_root), env=launcher_steam.client_environment(), startupinfo=startupinfo,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    job = attach_kill_on_close_job(proc.pid)
    return proc, job


def terminate_x4(proc, job):
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
    finally:
        if job:
            ctypes.windll.kernel32.CloseHandle(job)


def run_phase1_capture(*, game_dir, dll_path, profile_id, out_dir, width, height, timeout_s):
    lane_root = Path(os.path.splitdrive(str(game_dir.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-npc-p1-{out_dir.name}'
    if lane_root.exists():
        remove_view_patiently(launcher_game, lane_root)

    launcher_game.create_view(game_dir, lane_root)
    profile_dir = launcher_profile.prepare(
        profile_id, [*launcher_game.dlc_ids(lane_root), EXTENSION_ID],
        width=width, height=height, fast=True,
    )
    log_path = profile_dir / 'neon-npc-phase1-capture.log'
    if log_path.exists():
        log_path.unlink()
    save_file = profile_dir / 'save' / 'save_009.xml.gz'
    if save_file.exists():
        save_file.unlink()

    ext_dir, installed_sha = compose_extension(
        lane_root / 'extensions', dll_path=dll_path, phase='capture',
        profile=profile_id, gamestart='custom_creative', epoch=EPOCH,
    )
    assert installed_sha == EXPECTED_PACKAGED_DLL_SHA256

    proc, job = launch_unobtrusive_x4(
        lane_root / 'X4.exe', lane_root, profile_id, 'neon-npc-phase1-capture.log'
    )
    loaded_modules = None
    done = False
    tucked = False
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            if not tucked:
                hwnd = find_window(proc.pid)
                if hwnd:
                    tuck_window_bottom_corner(hwnd)
                    tucked = True
            if log_path.is_file():
                text = log_path.read_text(encoding='utf-8', errors='replace')
                if loaded_modules is None and 'AUTH_NPC_DLL_CLOCK' in text:
                    loaded_modules = snapshot_process_modules(proc.pid)
                if 'AUTH_NPC_ERROR' in text:
                    raise RuntimeError('Phase 1 Lua error in log')
                if 'AUTH_NPC_CAPTURE_DONE' in text:
                    done = True
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.15)
        if not done:
            raise TimeoutError('Phase 1 capture timed out')
    finally:
        terminate_x4(proc, job)
        remove_view_patiently(launcher_game, lane_root)

    log_text = log_path.read_text(encoding='utf-8', errors='replace')
    shutil.copyfile(log_path, out_dir / 'neon-npc-phase1-capture.log')
    errors = count_project_errors(log_text)

    setup_kv = {}
    selected_kv = {}
    done_kv = {}
    dll_clock_kv = {}
    samples = []
    for line in log_text.splitlines():
        if '[X4LAN] AUTH_NPC_DLL_CLOCK ' in line:
            dll_clock_kv = parse_kv(line.split('AUTH_NPC_DLL_CLOCK ', 1)[1])
        elif '[X4LAN] AUTH_NPC_CAPTURE_SETUP ' in line:
            setup_kv = parse_kv(line.split('AUTH_NPC_CAPTURE_SETUP ', 1)[1])
        elif '[X4LAN] AUTH_NPC_CAPTURE_SELECTED ' in line:
            selected_kv = parse_kv(line.split('AUTH_NPC_CAPTURE_SELECTED ', 1)[1])
        elif '[X4LAN] AUTH_NPC_CAPTURE_SAMPLE ' in line:
            kv = parse_kv(line.split('AUTH_NPC_CAPTURE_SAMPLE ', 1)[1])
            vel = None
            if kv.get('vx') != '-' and kv.get('vy') != '-' and kv.get('vz') != '-':
                vel = {'x': float(kv['vx']), 'y': float(kv['vy']), 'z': float(kv['vz'])}
            samples.append({
                'seq': int(kv['seq']),
                'index': int(kv['index']),
                'actor': kv['actor'],
                'code': kv['code'],
                'sector_macro': kv['sector'],
                'zone': kv['zone'],
                'game_time': float(kv['game_time']),
                'capture_time': float(kv['capture_time']),
                'x': float(kv['x']),
                'y': float(kv['y']),
                'z': float(kv['z']),
                'yaw': float(kv['yaw']),
                'pitch': float(kv['pitch']),
                'roll': float(kv['roll']),
                'real_velocity': vel,
                'speed_mps': float(kv['speed']),
            })
        elif '[X4LAN] AUTH_NPC_CAPTURE_DONE ' in line:
            done_kv = parse_kv(line.split('AUTH_NPC_CAPTURE_DONE ', 1)[1])

    assert save_file.is_file() and save_file.stat().st_size > 1024, 'save_009.xml.gz missing after Phase 1'
    save_sha256 = hashlib.sha256(save_file.read_bytes()).hexdigest()
    shutil.copyfile(save_file, out_dir / 'save_009.xml.gz')
    assert len(samples) >= 20, f'too few captured NPC samples: {len(samples)}'

    trace = {
        'source_label': 'recorded_phase1_native_x4_ai_flight',
        'epoch': EPOCH,
        'save_slot': 'save_009',
        'save_bytes': save_file.stat().st_size,
        'save_sha256': save_sha256,
        'total_registry': int(setup_kv['total_registry']),
        'own': {
            'index': int(setup_kv['own_index']),
            'code': setup_kv['own_code'],
            'sector_macro': setup_kv['sector'],
            'pre_save_pose': parse_xyz(selected_kv['pre_save_own']),
        },
        'unrelated': {
            'index': int(setup_kv['unrelated_index']),
            'code': setup_kv['unrelated_code'],
            'sector_macro': setup_kv['sector'],
            'dist_m': float(setup_kv['unrelated_dist_m']),
            'pre_save_pose': parse_xyz(selected_kv['pre_save_unrelated']),
        },
        'npc': {
            'index': int(selected_kv['index']),
            'actor': selected_kv['actor'],
            'code': selected_kv['code'],
            'owner': selected_kv['owner'],
            'macro': selected_kv['macro'],
            'sector_macro': selected_kv['sector'],
            'dist_m': float(selected_kv['dist_m']),
            'within_radar': selected_kv.get('within_radar') == 'true',
            'scout_speed_mps': float(selected_kv['scout_speed_mps']),
            'pre_save_pose': parse_xyz(selected_kv['pre_save_npc']),
            'pre_save_att_deg': selected_kv['pre_save_npc_att_deg'],
        },
        'dll_clock': dll_clock_kv,
        'loaded_modules': loaded_modules,
        'summary': {
            'samples': int(done_kv['samples']),
            'displacement_m': float(done_kv['displacement_m']),
            'peak_speed_mps': float(done_kv['peak_speed_mps']),
            'unrelated_capture_disp_m': float(done_kv['unrelated_capture_disp_m']),
            'own_capture_disp_m': float(done_kv['own_capture_disp_m']),
        },
        'project_errors': errors,
        'view_removed': not lane_root.exists(),
        'samples': samples,
    }
    (out_dir / 'captured-npc-trace.json').write_text(json.dumps(trace, indent=2), encoding='utf-8')
    return trace


def run_phase2_playback(*, game_dir, dll_path, profile_id, out_dir, trace, width, height, timeout_s):
    lane_root = Path(os.path.splitdrive(str(game_dir.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-npc-p2-{out_dir.name}'
    if lane_root.exists():
        remove_view_patiently(launcher_game, lane_root)

    npc_index = int(trace['npc']['index'])
    own_index = int(trace['own']['index'])
    unrelated_index = int(trace['unrelated']['index'])
    sector_macro = trace['npc']['sector_macro']
    total_registry = int(trace['total_registry'])

    # Start unmodified CompanyMotionService and admit both Grant 1 (Authority NPC Sender)
    # and Grant 2 (Observing Windows Client).
    service = CompanyMotionService()
    service.set_running(True)
    authority_cred = service.admit(
        handle=1, attempt_id=secrets.token_hex(16), ship_index=npc_index,
        sector_macro=sector_macro, session_epoch=SESSION, registry_epoch=EPOCH,
    )
    observer_cred = service.admit(
        handle=2, attempt_id=secrets.token_hex(16), ship_index=own_index,
        sector_macro=sector_macro, session_epoch=SESSION, registry_epoch=EPOCH,
    )

    stage_events = []
    def on_stage(stage_name):
        stage_events.append({'stage': stage_name, 'qpc': time.perf_counter()})

    observer_source = CompanyMotionSource(
        playback_index=npc_index, cockpit_radar_playback=True, on_stage=on_stage,
    )
    motion_cfg = observer_source.configure(
        EPOCH, own_index, operational_bits='1' * max(total_registry, npc_index, own_index, unrelated_index),
    )

    proc = job = None
    authority_client = observer_client = observer = None
    authority_stats = observer_client_stats = observer_stats = service_traffic = None
    observer_active_binding = None
    loaded_modules = None
    udp_table_evidence = None
    tucked = False
    playback_done = False
    stop_flagged_at = None
    last_sample_emitted_at = None
    py_clock_before_dll = time.perf_counter()
    py_clock_after_dll = None
    deadline = time.monotonic() + timeout_s

    try:
        launcher_game.create_view(game_dir, lane_root)
        profile_dir = launcher_profile.prepare(
            profile_id, [*launcher_game.dlc_ids(lane_root), EXTENSION_ID],
            width=width, height=height, fast=True,
        )
        staged_save = out_dir / 'save_009.xml.gz'
        assert staged_save.is_file(), f'archived Phase 1 save missing: {staged_save}'
        assert hashlib.sha256(staged_save.read_bytes()).hexdigest() == trace['save_sha256'], 'archived save SHA mismatch'
        save_dir = profile_dir / 'save'
        save_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staged_save, save_dir / 'save_009.xml.gz')
        log_path = profile_dir / 'neon-npc-phase2-playback.log'
        if log_path.exists():
            log_path.unlink()
        ext_dir, installed_sha = compose_extension(
            lane_root / 'extensions', dll_path=dll_path, phase='playback',
            profile=profile_id, gamestart='custom_creative', epoch=EPOCH,
            extra_cfg={
                'udp_port': int(motion_cfg['port']),
                'udp_lease': motion_cfg['lease'],
                'udp_generation': motion_cfg['generation'],
                'own_index': own_index,
                'npc_index': npc_index,
                'unrelated_index': unrelated_index,
                'authority_generation': authority_cred['generation'],
            },
        )
        assert installed_sha == EXPECTED_PACKAGED_DLL_SHA256

        authority_source = RecordedAuthorityNpcSource(EPOCH, npc_index, trace['samples'])
        authority_client = CompanyMotionClient(
            service.port, authority_cred, authority_source, lambda _peer: True,
        )

        proc, job = launch_unobtrusive_x4(
            lane_root / 'X4.exe', lane_root, profile_id, 'neon-npc-phase2-playback.log'
        )
        proc_owner = _LiveChildProcessOwner(proc)
        peer_validator = WindowsMotionPeer(proc_owner, observer_source.socket)
        observer = observer_source.observe(peer_validator)
        observer_client = CompanyMotionClient(
            service.port, observer_cred, observer_source, peer_validator,
            on_snapshot=observer.snapshot,
            on_end=observer.end,
            on_view_reset=observer.reset_world,
            on_transport_lost=observer.transport_lost,
            trial_index=npc_index,
        )
        observer.bind_views(observer_client.set_views)

        while time.monotonic() < deadline:
            if not tucked:
                hwnd = find_window(proc.pid)
                if hwnd:
                    tuck_window_bottom_corner(hwnd)
                    tucked = True

            # Poll unmodified service + authority sender + observing Windows client tightly.
            service.poll()
            authority_client.poll()
            observer_client.poll()
            service.poll()

            # Arm authority replay only once X4's VIEW subscription for npc_index is sent to the service.
            if not authority_source._armed:
                if npc_index in (observer_client._sent_view or {}).get(sector_macro, ()):
                    slot_active = observer._slots['cockpit'].active
                    if slot_active is not None:
                        observer_active_binding = {
                            'sector_macro': slot_active[0],
                            'index': slot_active[1],
                            'actor': slot_active[2],
                            'code': slot_active[3],
                        }
                    # Capture Win32 GetExtendedUdpTable proof for the live X4 UDP socket.
                    if udp_table_evidence is None and observer_source._peer is not None:
                        _, peer_port = observer_source._peer
                        rows = _get_extended_udp_table(_AF_INET, _load_udp_table_function())
                        udp_table_evidence = [
                            {'local_ip': ip, 'local_port': port, 'pid': pid}
                            for ip, port, pid in rows
                            if port == peer_port and pid == proc.pid
                        ]
                    authority_source.arm()

            if authority_source.done and last_sample_emitted_at is None:
                last_sample_emitted_at = time.monotonic()

            # Allow ~0.8 s after final emitted sample for ACK, SEEN, and delayed readback settlement.
            if (last_sample_emitted_at is not None and stop_flagged_at is None
                    and time.monotonic() - last_sample_emitted_at >= 0.8):
                (ext_dir / 'stop_playback.lua').write_text('return true\n', encoding='ascii')
                stop_flagged_at = time.monotonic()

            if log_path.is_file():
                text = log_path.read_text(encoding='utf-8', errors='replace')
                if loaded_modules is None and 'AUTH_NPC_DLL_CLOCK' in text:
                    py_clock_after_dll = time.perf_counter()
                    loaded_modules = snapshot_process_modules(proc.pid)
                if 'AUTH_NPC_ERROR' in text:
                    raise RuntimeError('Phase 2 Lua error in log')
                if 'AUTH_NPC_PLAYBACK_DONE' in text:
                    playback_done = True
                    break

            if proc.poll() is not None:
                break
            time.sleep(0.01)

        if not playback_done:
            raise TimeoutError('Phase 2 playback timed out')
    finally:
        if authority_client is not None:
            authority_stats = authority_client.stats()
        if observer_client is not None:
            observer_client_stats = observer_client.stats()
        if observer_source._observer is not None:
            observer_stats = observer_source.observer_stats()
        service_traffic = service.participant_traffic()
        for obj in (authority_client, observer_client, observer_source, service):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if proc is not None:
            terminate_x4(proc, job)
        if 'log_path' in locals() and log_path.is_file():
            shutil.copyfile(log_path, out_dir / 'neon-npc-phase2-playback.log')
        if lane_root.exists():
            remove_view_patiently(launcher_game, lane_root)

    log_text = log_path.read_text(encoding='utf-8', errors='replace')
    shutil.copyfile(log_path, out_dir / 'neon-npc-phase2-playback.log')
    errors = count_project_errors(log_text)

    dll_clock_kv = {}
    gate_check_kv = {}
    resolved_kv = {}
    ready_kv = {}
    stats_kv = {}
    final_rb_kv = {}
    readbacks = []
    final_delayed_rb = None
    events = []
    stops = []

    for line in log_text.splitlines():
        if '[X4LAN] AUTH_NPC_DLL_CLOCK ' in line:
            dll_clock_kv = parse_kv(line.split('AUTH_NPC_DLL_CLOCK ', 1)[1])
        elif '[X4LAN] AUTH_NPC_COMPANY_GATE_CHECK ' in line:
            gate_check_kv = parse_kv(line.split('AUTH_NPC_COMPANY_GATE_CHECK ', 1)[1])
        elif '[X4LAN] AUTH_NPC_PLAYBACK_RESOLVED ' in line:
            resolved_kv = parse_kv(line.split('AUTH_NPC_PLAYBACK_RESOLVED ', 1)[1])
        elif '[X4LAN] AUTH_NPC_PLAYBACK_READY ' in line:
            ready_kv = parse_kv(line.split('AUTH_NPC_PLAYBACK_READY ', 1)[1])
        elif '[X4LAN] AUTH_NPC_PLAYBACK_READBACK ' in line:
            kv = parse_kv(line.split('AUTH_NPC_PLAYBACK_READBACK ', 1)[1])
            readbacks.append({
                'n': int(kv['n']),
                'kind': kv['kind'],
                'slot': kv['slot'],
                'serial': int(kv['serial']),
                'epoch': kv['epoch'],
                'index': int(kv['index']),
                'code': kv['ship'],
                'sector_macro': kv['sector'],
                'generation': kv['generation'],
                'sequence': int(kv['sequence']),
                'source_time': float(kv['source_time']),
                'capture_time': float(kv['capture_time']),
                'elapsed_s': float(kv['elapsed']),
                'elapsed_ms': float(kv['elapsed']) * 1000.0,
                'target': parse_xyz(kv['target']),
                'actual': parse_xyz(kv['actual']),
                'error_m': float(kv['error']),
                'displacement_m': float(kv['displacement']),
                'att_delta_deg': float(kv['att_delta_deg']),
                'att_deg': kv['att_deg'],
            })
        elif '[X4LAN] AUTH_NPC_PLAYBACK_READBACK_FINAL ' in line:
            kv = parse_kv(line.split('AUTH_NPC_PLAYBACK_READBACK_FINAL ', 1)[1])
            final_delayed_rb = {
                'n': int(kv['n']),
                'kind': kv['kind'],
                'slot': kv['slot'],
                'serial': int(kv['serial']),
                'epoch': kv['epoch'],
                'index': int(kv['index']),
                'code': kv['ship'],
                'sector_macro': kv['sector'],
                'generation': kv['generation'],
                'sequence': int(kv['sequence']),
                'source_time': float(kv['source_time']),
                'capture_time': float(kv['capture_time']),
                'elapsed_s': float(kv['elapsed']),
                'elapsed_ms': float(kv['elapsed']) * 1000.0,
                'target': parse_xyz(kv['target']),
                'actual': parse_xyz(kv['actual']),
                'error_m': float(kv['error']),
                'displacement_m': float(kv['displacement']),
                'att_delta_deg': float(kv['att_delta_deg']),
                'att_deg': kv['att_deg'],
            }
        elif '[X4LAN] AUTH_NPC_PLAYBACK_STATS ' in line:
            stats_kv = parse_kv(line.split('AUTH_NPC_PLAYBACK_STATS ', 1)[1])
        elif '[X4LAN] AUTH_NPC_FINAL_READBACK ' in line:
            final_rb_kv = parse_kv(line.split('AUTH_NPC_FINAL_READBACK ', 1)[1])
        elif '[X4LAN] AUTH_NPC_PLAYBACK_EVENT ' in line:
            events.append(parse_kv(line.split('AUTH_NPC_PLAYBACK_EVENT ', 1)[1]))
        elif '[X4LAN] AUTH_NPC_PLAYBACK_STOP ' in line:
            stops.append(parse_kv(line.split('AUTH_NPC_PLAYBACK_STOP ', 1)[1]))

    # Build direct comparison of sender identity, velocity, observer SEEN, and native readback.
    emitted_by_seq = {item['sequence']: item for item in authority_source.emitted}
    last_ack = authority_stats.get('last_acknowledged_sample')
    last_nz_ack = authority_stats.get('last_nonzero_velocity_acknowledged')
    cockpit_obs = observer_stats['slots']['cockpit']
    last_seen = cockpit_obs.get('last_confirmed')
    last_nz_seen = cockpit_obs.get('last_nonzero_velocity_confirmed')

    matched_seq = None
    if final_delayed_rb is not None:
        matched_seq = final_delayed_rb['sequence']
    elif last_seen is not None:
        matched_seq = last_seen['sequence']
    matched_sender = emitted_by_seq.get(matched_seq) if matched_seq is not None else None

    npc_loaded_pose = parse_xyz(resolved_kv['npc_init'])
    cold_reload_offset_m = _dist3(npc_loaded_pose, trace['npc']['pre_save_pose'])
    npc_post_hold_pose = parse_xyz(ready_kv['npc_post_hold'])
    npc_final_pose = parse_xyz(final_rb_kv['npc_final'])
    unrelated_final_pose = parse_xyz(final_rb_kv['unrelated_final'])
    own_final_pose = parse_xyz(final_rb_kv['own_final'])

    def _speed(vel):
        if not vel:
            return None
        return math.hypot(vel['x'], vel['y'], vel['z'])

    comparison = {
        'authority_input_source': 'recorded_phase1_native_x4_ai_flight',
        'authority_input_explicit_note': (
            'Recorded from Phase 1 native Windows X4 9.00 live AI flight on the exact same '
            'registered NPC in save_009.xml.gz; replayed into live CompanyMotionService with '
            'exact recorded positions/velocities/dt and QPC capture_time re-anchored to Phase 2.'
        ),
        'identity_match': {
            'epoch': {
                'authority_sender': last_ack['epoch'] if last_ack else None,
                'observer_seen': last_seen['epoch'] if last_seen else None,
                'x4_readback': final_delayed_rb['epoch'] if final_delayed_rb else None,
                'all_equal': (
                    last_ack is not None and last_seen is not None and final_delayed_rb is not None
                    and last_ack['epoch'] == last_seen['epoch'] == final_delayed_rb['epoch'] == EPOCH
                ),
            },
            'ship_index': {
                'authority_sender': last_ack['index'] if last_ack else None,
                'observer_seen': last_seen['index'] if last_seen else None,
                'x4_readback': final_delayed_rb['index'] if final_delayed_rb else None,
                'all_equal': (
                    last_ack is not None and last_seen is not None and final_delayed_rb is not None
                    and last_ack['index'] == last_seen['index'] == final_delayed_rb['index'] == npc_index
                ),
            },
            'generation': {
                'authority_grant': authority_cred['generation'],
                'authority_sender_ack': last_ack['generation'] if last_ack else None,
                'observer_seen': last_seen['generation'] if last_seen else None,
                'x4_readback': final_delayed_rb['generation'] if final_delayed_rb else None,
                'all_equal': (
                    last_ack is not None and last_seen is not None and final_delayed_rb is not None
                    and authority_cred['generation'] == last_ack['generation']
                    == last_seen['generation'] == final_delayed_rb['generation']
                ),
            },
            'sector_macro': {
                'authority_sender': last_ack['sector_macro'] if last_ack else None,
                'observer_seen': last_seen['sector_macro'] if last_seen else None,
                'x4_readback': final_delayed_rb['sector_macro'] if final_delayed_rb else None,
                'all_equal': (
                    last_ack is not None and last_seen is not None and final_delayed_rb is not None
                    and last_ack['sector_macro'] == last_seen['sector_macro']
                    == final_delayed_rb['sector_macro'] == sector_macro
                ),
            },
            'ship_code': {
                'phase1_npc': trace['npc']['code'],
                'phase2_resolved_npc': resolved_kv.get('npc_code'),
                'observer_seen': last_seen['code'] if last_seen else None,
                'x4_readback': final_delayed_rb['code'] if final_delayed_rb else None,
            },
            'actor_hex': {
                'phase1_npc': trace['npc']['actor'],
                'phase2_resolved_npc': resolved_kv.get('npc_actor'),
                'observer_active': observer_active_binding['actor'] if observer_active_binding else None,
            },
            'sequence': {
                'authority_last_acked': last_ack['sequence'] if last_ack else None,
                'observer_last_seen': last_seen['sequence'] if last_seen else None,
                'x4_final_delayed_readback': final_delayed_rb['sequence'] if final_delayed_rb else None,
            },
        },
        'velocity_comparison': {
            'authority_last_nonzero_ack': {
                'sequence': last_nz_ack['sequence'] if last_nz_ack else None,
                'real_velocity': last_nz_ack['real_velocity'] if last_nz_ack else None,
                'speed_mps': _speed(last_nz_ack['real_velocity']) if last_nz_ack else None,
            },
            'observer_last_nonzero_seen': {
                'sequence': last_nz_seen['sequence'] if last_nz_seen else None,
                'real_velocity': last_nz_seen['real_velocity'] if last_nz_seen else None,
                'speed_mps': _speed(last_nz_seen['real_velocity']) if last_nz_seen else None,
            },
            'matched_final_readback_sender': {
                'sequence': matched_sender['sequence'] if matched_sender else None,
                'real_velocity': matched_sender['real_velocity'] if matched_sender else None,
                'speed_mps': matched_sender['speed_mps'] if matched_sender else None,
                'position': matched_sender['position'] if matched_sender else None,
            },
            'service_max_reported_speed_mps': next(
                (row['max_reported_speed_mps'] for row in service_traffic['active'] if row['ship_index'] == npc_index),
                None,
            ),
            'phase1_peak_speed_mps': trace['summary']['peak_speed_mps'],
        },
        'observer_readback': {
            'cold_reload_offset_m': cold_reload_offset_m,
            'npc_total_displacement_m': float(final_rb_kv['npc_disp_m']),
            'npc_final_pose': npc_final_pose,
            'npc_post_hold_pose': npc_post_hold_pose,
            'npc_final_vs_matched_sender_m': (
                _dist3(npc_final_pose, matched_sender['position']) if matched_sender else None
            ),
            'npc_final_vs_final_readback_target_m': (
                _dist3(npc_final_pose, final_delayed_rb['target']) if final_delayed_rb else None
            ),
            'npc_att_delta_deg': float(final_rb_kv['npc_att_delta_deg']),
            'final_delayed_readback': final_delayed_rb,
            'unrelated_visible_ship': {
                'index': unrelated_index,
                'code': final_rb_kv['unrelated_code'],
                'actor': resolved_kv.get('unrelated_actor'),
                'dist_m': float(resolved_kv.get('unrelated_dist_m', 0)),
                'hud_locked_before': (
                    ready_kv.get('unrelated_hud_locked') == 'true'
                    or resolved_kv.get('unrelated_hud_locked') == 'true'
                ),
                'hud_still_locked_after': final_rb_kv.get('unrelated_hud_still_locked') == 'true',
                'onscreen_initial': resolved_kv.get('unrelated_onscreen') == 'true',
                'initial_pose': parse_xyz(ready_kv['unrelated_post_hold']),
                'final_pose': unrelated_final_pose,
                'displacement_m': float(final_rb_kv['unrelated_disp_m']),
                'writes_targeted': 0,
                'samples_targeted': 0,
            },
            'local_own_ship': {
                'index': own_index,
                'code': final_rb_kv['own_code'],
                'initial_pose': parse_xyz(ready_kv['own_post_hold']),
                'final_pose': own_final_pose,
                'displacement_m': float(final_rb_kv['own_disp_m']),
            },
        },
    }

    return {
        'dll_clock': dll_clock_kv,
        'py_clock_bracket': {
            'before_dll_qpc': py_clock_before_dll,
            'after_dll_qpc': py_clock_after_dll,
        },
        'loaded_modules': loaded_modules,
        'udp_table_evidence': udp_table_evidence,
        'stages': stage_events,
        'company_gate_check': gate_check_kv,
        'resolved': resolved_kv,
        'playback_stats': stats_kv,
        'authority_sender_stats': authority_stats,
        'observer_client_stats': observer_client_stats,
        'observer_stats': observer_stats,
        'service_traffic': service_traffic,
        'readbacks_logged': readbacks,
        'final_delayed_readback': final_delayed_rb,
        'events': events,
        'stops': stops,
        'comparison': comparison,
        'project_errors': errors,
        'view_removed': not lane_root.exists(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--dll', type=Path,
                        default=ROOT / '.local/pkg-d0756d51/x4-authority-windows-diagnostic/network/x4lan_net_64.dll')
    parser.add_argument('--profile', type=int, default=PROFILE_DEFAULT)
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--timeout', type=float, default=180.0)
    parser.add_argument('--reuse-phase1', type=Path, default=None,
                        help='reuse an already-captured Phase 1 captured-npc-trace.json with save_009.xml.gz')
    args = parser.parse_args()

    if not args.dll.is_file():
        raise SystemExit(f'packaged DLL not found: {args.dll}')
    dll_sha = hashlib.sha256(args.dll.read_bytes()).hexdigest()
    if dll_sha != EXPECTED_PACKAGED_DLL_SHA256:
        raise SystemExit(f'unexpected DLL SHA-256: {dll_sha}')

    if x4_running():
        raise SystemExit('REFUSED: X4.exe is already running; will not disturb user or peer run.')
    ok, msg = launcher_steam.status()
    if not ok:
        raise SystemExit(msg)

    args.out.mkdir(parents=True, exist_ok=True)
    game_dir = launcher_game.find_install().resolve(strict=True)
    launcher_game.verify_exe(game_dir)
    x4_exe_sha = hashlib.sha256((game_dir / 'X4.exe').read_bytes()).hexdigest()
    ctypes.windll.user32.SetProcessDPIAware()

    acquire_shared_lock(args.profile)
    try:
        profile_root = launcher_profile.documents()
        protected_before = {
            'other_profiles': {
                child.name: tree_metadata(child)
                for child in sorted(profile_root.iterdir())
                if child.is_dir() and child.name != str(args.profile)
            },
            'game_extensions': sorted(entry.name for entry in (game_dir / 'extensions').iterdir()),
        }

        if args.reuse_phase1 is not None:
            phase1 = json.loads(args.reuse_phase1.read_text(encoding='utf-8'))
            phase1['project_errors'] = [
                e for e in phase1.get('project_errors', [])
                if 'Could not find signature file' not in e
            ]
            src_save = args.reuse_phase1.parent / 'save_009.xml.gz'
            assert src_save.is_file(), f'save_009.xml.gz missing next to trace: {src_save}'
            assert hashlib.sha256(src_save.read_bytes()).hexdigest() == phase1['save_sha256'], 'save_009 SHA mismatch'
            shutil.copyfile(src_save, args.out / 'save_009.xml.gz')
        else:
            phase1 = run_phase1_capture(
                game_dir=game_dir, dll_path=args.dll, profile_id=args.profile,
                out_dir=args.out, width=args.width, height=args.height, timeout_s=args.timeout,
            )
        phase2 = run_phase2_playback(
            game_dir=game_dir, dll_path=args.dll, profile_id=args.profile,
            out_dir=args.out, trace=phase1, width=args.width, height=args.height, timeout_s=args.timeout,
        )

        protected_after = {
            'other_profiles': {
                child.name: tree_metadata(child)
                for child in sorted(profile_root.iterdir())
                if child.is_dir() and child.name != str(args.profile)
            },
            'game_extensions': sorted(entry.name for entry in (game_dir / 'extensions').iterdir()),
        }

        result = {
            'status': 'ok',
            'x4_exe_sha256': x4_exe_sha,
            'packaged_dll_path': str(args.dll),
            'packaged_dll_sha256': dll_sha,
            'profile': args.profile,
            'phase1_capture': {k: v for k, v in phase1.items() if k != 'samples'},
            'phase2_playback': phase2,
            'hygiene': {
                'protected_unchanged': protected_before == protected_after,
                'phase1_view_removed': phase1['view_removed'],
                'phase2_view_removed': phase2['view_removed'],
                'x4_running_after': x4_running(),
                'phase1_project_error_count': len(phase1['project_errors']),
                'phase2_project_error_count': len(phase2['project_errors']),
            },
        }
        (args.out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({
            'status': result['status'],
            'out': str(args.out),
            'hygiene': result['hygiene'],
            'comparison': phase2['comparison'],
        }, indent=2))
        assert result['hygiene']['protected_unchanged']
        assert result['hygiene']['phase1_view_removed']
        assert result['hygiene']['phase2_view_removed']
        assert not result['hygiene']['x4_running_after']
        assert result['hygiene']['phase1_project_error_count'] == 0
        assert result['hygiene']['phase2_project_error_count'] == 0
    finally:
        release_shared_lock()


if __name__ == '__main__':
    main()
