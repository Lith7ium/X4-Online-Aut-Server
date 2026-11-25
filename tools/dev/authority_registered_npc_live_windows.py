#!/usr/bin/env python3
"""Single-session live native Windows 11 X4 9.00 trial of the opt-in registered-NPC path.

Exercises ALL four new opt-in registered-NPC product modules end to end inside ONE live
native Windows X4 9.00 session with packaged x4lan_net_64.dll (6ad97e04..., QPC
udp.monotonic) and unmodified Company transport/playback infrastructure:
  Live AI NPC S (real X4 engine flight, zero pre-recording)
    -> UDP #1 (WindowsMotionPeer) -> NpcMotionSource -> CompanyMotionClient (Grant 1)
    -> CompanyMotionService
    -> CompanyMotionClient (Grant 2) -> CompanyMotionSource + CompanyMotionObserver
    -> UDP #2 (WindowsMotionPeer) -> X4LAN_COMPANY_OBSERVER_STREAM
    -> X4LAN_NPC_COCKPIT_OBSERVER (verified radar & map interest)
    -> X4LAN_COMPANY_COCKPIT_PLAYBACK (allow_reacquire_align=true)
    -> X4LAN_COMPANY_HULL_PLAYBACK -> X4LAN_MOTION_BUFFER + X4LAN_MOTION_PLAYBACK
    -> X4LAN_NPC_HULL_PLAYBACK_X4 -> SetObjectSectorPos on in-radar Argon NPC R

Explicit single-session fixture links (labeled transparently in result.json and report):
  1. Single-host local-origin translation: live flying natural NPC S's real-time
     displacement/velocity is translated onto in-radar registered Argon NPC replica R's
     initial anchor so one Windows X4.exe instance can act as both live AI authority
     source and receiving cockpit simultaneously.
  2. Private local loopback CompanyMotionService on one Windows 11 PC.
  3. Harness-driven interest withdrawal/reacquisition and map-interest toggles.
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
import secrets
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT / 'tools/dev')]

from authority_company_cockpit_windows import (
    EPOCH, NONCE, SESSION, SW_SHOWNOACTIVATE, STARTF_USESHOWWINDOW,
    attach_kill_on_close_job, find_window, remove_view_patiently,
    tree_metadata, tuck_window_bottom_corner, x4_running,
)
from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_service import CompanyMotionService
from authority.company_motion_source import CompanyMotionSource
from authority.npc_motion_source import NpcMotionSource
from authority.windows_motion_peer import WindowsMotionPeer, _AF_INET, _get_extended_udp_table, _load_udp_table_function
from authority.windows_x4_process import WindowsX4Process
from lockstep.launcher import game as launcher_game
from lockstep.launcher import profile as launcher_profile
from lockstep.launcher import steam as launcher_steam

EXTENSION_ID = 'x4_lan_lab'
LOGFILE = 'neon-registered-npc-live.log'
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
    found, total = {}, 0
    try:
        entry = _MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Module32FirstW(snap, ctypes.byref(entry))
        while ok:
            total += 1
            if entry.szModule.casefold() in wanted:
                exe_path = Path(entry.szExePath)
                found[entry.szModule.casefold()] = {
                    'module': entry.szModule,
                    'path': str(exe_path),
                    'base_addr': hex(entry.modBaseAddr or 0),
                    'image_size': int(entry.modBaseSize),
                    'file_size': exe_path.stat().st_size if exe_path.is_file() else None,
                    'sha256': hashlib.sha256(exe_path.read_bytes()).hexdigest() if exe_path.is_file() else None,
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
            raise SystemExit(f'REFUSED: shared lockfile {SHARED_LOCKFILE} held by {info!r}')


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
            if 'AUTH_LIVE_NPC_ERROR' in line or 'AUTH_LIVE_NPC_PLAYBACK_STOP' in line:
                errors.append(line.strip())
            continue
        if 'Could not find signature file' in line:
            continue
        if '[=ERROR=]' in line and ('x4_lan_lab' in line or 'X4Authority' in line or 'X4LAN' in line or 'authority_' in line):
            errors.append(line.strip())
    return errors


def compose_extension(extensions_dir, *, dll_path, profile, gamestart, epoch):
    ext = Path(extensions_dir) / EXTENSION_ID
    if ext.exists():
        shutil.rmtree(ext)
    for sub in ('ui', 'md', 'native'):
        (ext / sub).mkdir(parents=True)

    (ext / 'content.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<content id="{EXTENSION_ID}" name="X4 Neon Registered NPC Live Path" '
        'description="Disposable single-session live registered-NPC end-to-end check" '
        'author="Neon" version="100" date="2026-11-25" save="false">\n'
        '  <dependency version="900" />\n'
        '</content>\n',
        encoding='utf-8',
    )

    ui_sources = [
        ('authority_registry_resolve_string.lua', 'tools/dev/authority_registry_resolve_string.lua'),
        ('local_udp_module.lua', 'src/authority/lua/local_udp_module.lua'),
        ('motion_buffer.lua', 'src/authority/lua/motion_buffer.lua'),
        ('motion_playback.lua', 'src/authority/lua/motion_playback.lua'),
        ('company_hull_playback.lua', 'src/authority/lua/company_hull_playback.lua'),
        ('company_hull_playback_x4.lua', 'src/authority/lua/company_hull_playback_x4.lua'),
        ('company_cockpit_playback.lua', 'src/authority/lua/company_cockpit_playback.lua'),
        ('company_observer_stream.lua', 'src/authority/lua/company_observer_stream.lua'),
        ('npc_cockpit_observer.lua', 'src/authority/lua/npc_cockpit_observer.lua'),
        ('npc_hull_playback_x4.lua', 'src/authority/lua/npc_hull_playback_x4.lua'),
        ('authority_registered_npc_live_x4.lua', 'tools/dev/authority_registered_npc_live_x4.lua'),
    ]
    md_sources = [
        ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
        ('npc_cockpit_observer.xml', 'src/authority/md/npc_cockpit_observer.xml'),
        ('authority_registered_npc_live_probe.xml', 'tools/dev/authority_registered_npc_live_probe.xml'),
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
    installed_sha = hashlib.sha256(dst_dll.read_bytes()).hexdigest()
    assert installed_sha == EXPECTED_PACKAGED_DLL_SHA256

    (ext / 'ui/config.lua').write_text(
        'X4NEON_REGISTERED_NPC_LIVE = {\n'
        '  enabled = true,\n'
        f'  profile = {json.dumps(str(profile))},\n'
        f'  gamestart = {json.dumps(gamestart)},\n'
        f'  epoch = {json.dumps(epoch)},\n'
        f'  nonce = {json.dumps(NONCE)},\n'
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
    return ext, installed_sha


def _lua_val(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--dll', type=Path,
                        default=ROOT / '.local/pkg-d0756d51/x4-authority-windows-diagnostic/network/x4lan_net_64.dll')
    parser.add_argument('--profile', type=int, default=PROFILE_DEFAULT)
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--timeout', type=float, default=180.0)
    args = parser.parse_args()

    if not args.dll.is_file():
        raise SystemExit(f'packaged DLL not found: {args.dll}')
    dll_sha = hashlib.sha256(args.dll.read_bytes()).hexdigest()
    if dll_sha != EXPECTED_PACKAGED_DLL_SHA256:
        raise SystemExit(f'unexpected DLL SHA-256: {dll_sha}')
    if x4_running():
        raise SystemExit('REFUSED: X4.exe is already running.')
    ok, msg = launcher_steam.status()
    if not ok:
        raise SystemExit(msg)

    args.out.mkdir(parents=True, exist_ok=True)
    game_dir = launcher_game.find_install().resolve(strict=True)
    launcher_game.verify_exe(game_dir)
    x4_exe_sha = hashlib.sha256((game_dir / 'X4.exe').read_bytes()).hexdigest()
    ctypes.windll.user32.SetProcessDPIAware()

    acquire_shared_lock(args.profile)
    lane_root = Path(os.path.splitdrive(str(game_dir.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-live-npc-{args.out.name}'
    proc = job = None
    service = npc_source = obs_source = auth_client = obs_client = None
    log_path = None
    try:
        profile_root = launcher_profile.documents()
        protected_before = {
            'other_profiles': {
                c.name: tree_metadata(c)
                for c in sorted(profile_root.iterdir())
                if c.is_dir() and c.name != str(args.profile)
            },
            'game_extensions': sorted(e.name for e in (game_dir / 'extensions').iterdir()),
        }

        if lane_root.exists():
            remove_view_patiently(launcher_game, lane_root)
        launcher_game.create_view(game_dir, lane_root)
        profile_dir = launcher_profile.prepare(
            args.profile, [*launcher_game.dlc_ids(lane_root), EXTENSION_ID],
            width=args.width, height=args.height, fast=True,
        )
        log_path = profile_dir / LOGFILE
        if log_path.exists():
            log_path.unlink()
        ext_dir, installed_sha = compose_extension(
            lane_root / 'extensions', dll_path=args.dll,
            profile=args.profile, gamestart='custom_creative', epoch=EPOCH,
        )
        assert installed_sha == EXPECTED_PACKAGED_DLL_SHA256

        si = subprocess.STARTUPINFO()
        si.dwFlags |= STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWNOACTIVATE
        cmd = [
            str(lane_root / 'X4.exe'),
            '-personalfolderid', str(int(args.profile)),
            '-skipintro', '-noworkshopsync', '-nocputhrottle',
            '-debug', 'scripts', '-logfile', LOGFILE,
        ]
        py_before_dll = time.perf_counter()
        proc = subprocess.Popen(
            cmd, cwd=str(lane_root), env=launcher_steam.client_environment(), startupinfo=si,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        job = attach_kill_on_close_job(proc.pid)
        proc_owner = _LiveChildProcessOwner(proc)

        tucked = False
        loaded_modules = None
        py_after_dll = None
        ready_kv = None
        responses = {}
        log_pos = 0
        pending_line = ''
        deadline = time.monotonic() + args.timeout

        def pump_log_and_net():
            nonlocal tucked, loaded_modules, py_after_dll, ready_kv, log_pos, pending_line
            if not tucked:
                hwnd = find_window(proc.pid)
                if hwnd:
                    tuck_window_bottom_corner(hwnd)
                    tucked = True
            if service is not None:
                service.poll()
            if auth_client is not None:
                auth_client.poll()
            if obs_client is not None:
                obs_client.poll()
            if service is not None:
                service.poll()
            if log_path.is_file():
                with log_path.open('rb') as handle:
                    handle.seek(log_pos)
                    chunk = handle.read()
                    log_pos += len(chunk)
                if chunk:
                    pending_line += chunk.decode('utf-8', errors='replace')
                    lines, _, pending_line = pending_line.rpartition('\n')
                    for line in lines.splitlines():
                        if '[X4LAN] AUTH_LIVE_NPC_DLL_CLOCK ' in line and loaded_modules is None:
                            py_after_dll = time.perf_counter()
                            loaded_modules = snapshot_process_modules(proc.pid)
                        elif '[X4LAN] AUTH_LIVE_NPC_READY ' in line:
                            ready_kv = parse_kv(line.split('AUTH_LIVE_NPC_READY ', 1)[1])
                        elif '[X4LAN] AUTH_LIVE_NPC_RESP ' in line:
                            kv = parse_kv(line.split('AUTH_LIVE_NPC_RESP ', 1)[1])
                            responses[int(kv['seq'])] = kv
                        elif '[X4LAN] AUTH_LIVE_NPC_ERROR ' in line:
                            raise RuntimeError(line.strip())
            if proc.poll() is not None:
                raise RuntimeError(f'X4 exited unexpectedly with code {proc.returncode}')

        while ready_kv is None:
            if time.monotonic() >= deadline:
                raise TimeoutError('timed out waiting for AUTH_LIVE_NPC_READY')
            pump_log_and_net()
            time.sleep(0.05)

        total_registry = int(ready_kv['total_registry'])
        sector_macro = ready_kv['sector']
        own_index = int(ready_kv['own_index'])
        replica_index = int(ready_kv['replica_index'])
        unrelated_index = int(ready_kv['unrelated_index'])
        op_bits = '1' * max(total_registry, own_index, replica_index, unrelated_index)

        src_stages, obs_stages = [], []
        service = CompanyMotionService()
        service.set_running(True)
        auth_cred = service.admit(1, secrets.token_hex(16), replica_index, sector_macro, SESSION, EPOCH)
        obs_cred = service.admit(2, secrets.token_hex(16), own_index, sector_macro, SESSION, EPOCH)

        npc_source = NpcMotionSource(enabled=True, on_stage=lambda s: src_stages.append({'stage': s, 'qpc': time.perf_counter()}))
        src_cfg = npc_source.configure(EPOCH, replica_index, operational_bits=op_bits)
        obs_source = CompanyMotionSource(
            playback_index=replica_index, cockpit_radar_playback=True,
            on_stage=lambda s: obs_stages.append({'stage': s, 'qpc': time.perf_counter()}),
        )
        obs_cfg = obs_source.configure(EPOCH, own_index, operational_bits=op_bits)

        src_peer_val = WindowsMotionPeer(proc_owner, npc_source.socket)
        obs_peer_val = WindowsMotionPeer(proc_owner, obs_source.socket)
        observer = obs_source.observe(obs_peer_val)

        auth_client = CompanyMotionClient(service.port, auth_cred, npc_source, src_peer_val)
        obs_client = CompanyMotionClient(
            service.port, obs_cred, obs_source, obs_peer_val,
            on_snapshot=observer.snapshot, on_end=observer.end,
            on_view_reset=observer.reset_world, on_transport_lost=observer.transport_lost,
            trial_index=replica_index,
        )
        observer.bind_views(obs_client.set_views)

        cmd_seq = 0

        def send_cmd(op, **params):
            nonlocal cmd_seq
            cmd_seq += 1
            fields = [f'seq = {cmd_seq}', f'op = {json.dumps(op)}']
            for k, v in params.items():
                fields.append(f'{k} = {_lua_val(v)}')
            partial = ext_dir / f'cmd_{cmd_seq}.partial'
            partial.write_text('return { ' + ', '.join(fields) + ' }\n', encoding='ascii')
            os.replace(partial, ext_dir / f'cmd_{cmd_seq}.lua')
            t_end = time.monotonic() + 30.0
            while cmd_seq not in responses:
                if time.monotonic() >= t_end:
                    raise TimeoutError(f'command {op} timed out')
                pump_log_and_net()
                time.sleep(0.01)
            return dict(responses[cmd_seq])

        def pump_for(seconds):
            t_end = time.monotonic() + seconds
            while time.monotonic() < t_end:
                pump_log_and_net()
                time.sleep(0.01)

        def pump_until(pred, timeout=15.0, label='condition'):
            t_end = time.monotonic() + timeout
            while not pred():
                if time.monotonic() >= t_end:
                    raise TimeoutError(f'timed out waiting for {label}')
                pump_log_and_net()
                time.sleep(0.01)

        # Stage 0: Configure both PID-validated UDP sockets & run live mutual-exclusion check.
        stage0 = send_cmd(
            'configure_links',
            src_port=src_cfg['port'], src_lease=src_cfg['lease'], src_generation=src_cfg['generation'],
            obs_port=obs_cfg['port'], obs_lease=obs_cfg['lease'], obs_generation=obs_cfg['generation'],
        )
        assert stage0['company_refused_npc'] == 'true' and stage0['npc_refused_player_ship'] == 'true'

        # Stage 1: Pre-interest (live authority NPC streams & acks, observer has NO interest -> 0 delivery, 0 drift).
        t1_start = time.monotonic()
        while time.monotonic() - t1_start < 1.2:
            pump_log_and_net()
            a_st = auth_client.stats()
            if a_st['samples_acknowledged'] >= 6:
                p = send_cmd('probe_state')
                if float(p['src_disp_m']) >= 22.0:
                    break
            time.sleep(0.02)
        stage1_probe = send_cmd('probe_state')
        stage1_auth = auth_client.stats()
        stage1_obs_client = obs_client.stats()
        stage1_obs = obs_source.observer_stats()
        assert stage1_auth['samples_acknowledged'] >= 5, f'expected live authority samples in stage1: {stage1_auth}'
        assert stage1_obs_client['snapshots_received'] == 0, f'unexpected delivery without interest: {stage1_obs_client}'
        assert float(stage1_probe['rep_disp_m']) == 0.0

        # Capture Win32 GetExtendedUdpTable proof for BOTH live X4 UDP sockets.
        rows4 = _get_extended_udp_table(_AF_INET, _load_udp_table_function())
        udp_table_proof = {
            'authority_sampler_udp': [
                {'local_ip': ip, 'local_port': port, 'pid': pid}
                for ip, port, pid in rows4
                if npc_source.peer and port == npc_source.peer[1] and pid == proc.pid
            ],
            'observer_client_udp': [
                {'local_ip': ip, 'local_port': port, 'pid': pid}
                for ip, port, pid in rows4
                if obs_source._peer and port == obs_source._peer[1] and pid == proc.pid
            ],
        }

        # Stage 2: Enable verified cockpit radar interest -> live displacement on in-radar NPC R!
        send_cmd('set_radar_interest', active=True)
        pump_until(lambda: replica_index in (obs_client._sent_view or {}).get(sector_macro, ()),
                   timeout=10.0, label='stage2_radar_view_subscribed')
        pump_for(3.8)
        stage2_probe = send_cmd('probe_state')
        stage2_auth = auth_client.stats()
        stage2_obs_client = obs_client.stats()
        stage2_obs = obs_source.observer_stats()
        assert stage2_probe['radar_observed'] == 'true'
        assert float(stage2_probe['rep_disp_m']) > 25.0, f'expected live NPC displacement >25 m: {stage2_probe}'
        assert float(stage2_probe['unrelated_disp_m']) == 0.0
        assert float(stage2_probe['own_disp_m']) == 0.0

        # Stage 3: Withdraw cockpit radar interest -> 0 new snapshots & 0 drift while live authority flies >60 m further!
        send_cmd('set_radar_interest', active=False)
        pump_until(lambda: replica_index not in (obs_client._sent_view or {}).get(sector_macro, ()),
                   timeout=10.0, label='stage3_radar_view_withdrawn')
        pump_for(0.25)
        w_start_probe = send_cmd('probe_state')
        w_start_snaps = obs_client.stats()['snapshots_received']
        w_start_acked = auth_client.stats()['samples_acknowledged']
        w_start_src_disp = float(w_start_probe['src_disp_m'])
        w_start_rep_pose = (float(w_start_probe['rep_x']), float(w_start_probe['rep_y']), float(w_start_probe['rep_z']))

        t3_start = time.monotonic()
        while time.monotonic() - t3_start < 4.8:
            pump_for(0.25)
            p = send_cmd('probe_state')
            if time.monotonic() - t3_start >= 2.8 and (float(p['src_disp_m']) - w_start_src_disp) >= 62.0:
                break
        stage3_probe = send_cmd('probe_state')
        stage3_auth = auth_client.stats()
        stage3_obs_client = obs_client.stats()
        w_end_rep_pose = (float(stage3_probe['rep_x']), float(stage3_probe['rep_y']), float(stage3_probe['rep_z']))
        stage3_rep_drift_m = math.dist(w_start_rep_pose, w_end_rep_pose)
        stage3_new_auth_acks = stage3_auth['samples_acknowledged'] - w_start_acked
        stage3_new_obs_snaps = stage3_obs_client['snapshots_received'] - w_start_snaps
        stage3_src_advance_m = float(stage3_probe['src_disp_m']) - w_start_src_disp
        assert stage3_new_auth_acks >= 15, f'expected live authority to keep streaming: {stage3_new_auth_acks}'
        assert stage3_new_obs_snaps == 0, f'expected 0 snapshots during withdrawal: {stage3_new_obs_snaps}'
        assert stage3_rep_drift_m == 0.0, f'expected 0 drift during withdrawal: {stage3_rep_drift_m}'

        # Stage 4: Reacquire cockpit radar interest -> single authorized alignment (if >50 m) + resumed live playback!
        send_cmd('set_radar_interest', active=True)
        pump_until(lambda: replica_index in (obs_client._sent_view or {}).get(sector_macro, ()),
                   timeout=10.0, label='stage4_radar_reacquired')
        pump_for(3.5)
        stage4_probe = send_cmd('probe_state')
        stage4_auth = auth_client.stats()
        stage4_obs_client = obs_client.stats()
        stage4_obs = obs_source.observer_stats()
        stage4_extra_disp_m = math.dist(
            w_end_rep_pose,
            (float(stage4_probe['rep_x']), float(stage4_probe['rep_y']), float(stage4_probe['rep_z'])),
        )
        assert int(stage4_probe['reacquisitions']) >= 1, f'expected reacquisition: {stage4_probe}'
        assert stage4_extra_disp_m > 40.0, f'expected post-reacquisition displacement >40 m: {stage4_extra_disp_m}'
        assert float(stage4_probe['unrelated_disp_m']) == 0.0
        assert float(stage4_probe['own_disp_m']) == 0.0

        # Stage 5: Verified map-interest slot delivery & withdrawal (while cockpit radar is withdrawn).
        send_cmd('set_radar_interest', active=False)
        pump_until(lambda: replica_index not in (obs_client._sent_view or {}).get(sector_macro, ()),
                   timeout=10.0, label='stage5_cockpit_withdrawn')
        send_cmd('set_map_interest', active=True)
        pump_until(lambda: replica_index in (obs_client._sent_view or {}).get(sector_macro, ()),
                   timeout=10.0, label='stage5_map_subscribed')
        pump_for(1.5)
        stage5_map_open_obs = obs_source.observer_stats()
        stage5_map_open_probe = send_cmd('probe_state')
        assert stage5_map_open_probe['map_observed'] == 'true'
        assert stage5_map_open_obs['slots']['map']['samples_confirmed'] >= 5

        send_cmd('set_map_interest', active=False)
        pump_until(lambda: replica_index not in (obs_client._sent_view or {}).get(sector_macro, ()),
                   timeout=10.0, label='stage5_map_withdrawn')
        pump_for(0.2)
        map_snaps_before = obs_client.stats()['snapshots_received']
        map_acks_before = auth_client.stats()['samples_acknowledged']
        pump_for(1.2)
        stage5_map_closed_new_acks = auth_client.stats()['samples_acknowledged'] - map_acks_before
        stage5_map_closed_new_snaps = obs_client.stats()['snapshots_received'] - map_snaps_before
        assert stage5_map_closed_new_acks >= 5
        assert stage5_map_closed_new_snaps == 0

        # Stage 6: Independent final MD + FFI readback & clean Wait/constantvel release.
        final_auth_stats = auth_client.stats()
        final_obs_client_stats = obs_client.stats()
        final_obs_stats = obs_source.observer_stats()
        final_npc_src_stats = npc_source.stats()
        final_service_traffic = service.participant_traffic()
        send_cmd('finish')
    finally:
        for obj in (auth_client, obs_client, npc_source, obs_source, service):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if proc is not None:
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
        if log_path is not None and log_path.is_file():
            shutil.copyfile(log_path, args.out / LOGFILE)
        if lane_root.exists():
            remove_view_patiently(launcher_game, lane_root)
        release_shared_lock()

    log_text = (args.out / LOGFILE).read_text(encoding='utf-8', errors='replace')
    errors = count_project_errors(log_text)
    dll_clock_kv = {}
    final_kv = {}
    readbacks = []
    align_readbacks = []
    playback_events = []
    observer_events = []
    for line in log_text.splitlines():
        if '[X4LAN] AUTH_LIVE_NPC_DLL_CLOCK ' in line:
            dll_clock_kv = parse_kv(line.split('AUTH_LIVE_NPC_DLL_CLOCK ', 1)[1])
        elif '[X4LAN] AUTH_LIVE_NPC_FINAL ' in line:
            final_kv = parse_kv(line.split('AUTH_LIVE_NPC_FINAL ', 1)[1])
        elif '[X4LAN] AUTH_LIVE_NPC_READBACK ' in line:
            kv = parse_kv(line.split('AUTH_LIVE_NPC_READBACK ', 1)[1])
            (align_readbacks if kv.get('kind') == 'alignment_readback' else readbacks).append(kv)
        elif '[X4LAN] AUTH_LIVE_NPC_PLAYBACK_EVENT ' in line:
            playback_events.append(parse_kv(line.split('AUTH_LIVE_NPC_PLAYBACK_EVENT ', 1)[1]))
        elif '[X4LAN] AUTH_LIVE_NPC_OBSERVER_EVENT ' in line:
            observer_events.append(parse_kv(line.split('AUTH_LIVE_NPC_OBSERVER_EVENT ', 1)[1]))

    protected_after = {
        'other_profiles': {
            c.name: tree_metadata(c)
            for c in sorted(launcher_profile.documents().iterdir())
            if c.is_dir() and c.name != str(args.profile)
        },
        'game_extensions': sorted(e.name for e in (game_dir / 'extensions').iterdir()),
    }

    result = {
        'status': 'ok',
        'x4_exe_sha256': x4_exe_sha,
        'packaged_dll_sha256': dll_sha,
        'profile': args.profile,
        'explicit_fixture_links': [
            'Single-host local-origin translation: live AI flight displacement & velocity of natural Argon/faction NPC S translated onto in-radar registered Argon NPC replica R anchor so one Windows X4.exe instance serves as both live authority source and receiving cockpit.',
            'Private local loopback CompanyMotionService on one Windows 11 PC (no remote WAN/Fedora server or TLS save-download bootstrap).',
            'Replica R and nearby unrelated ship U spawned & enrolled in X4AuthorityShipRegistry in player.sector; withdrawal/reacquisition and map-interest transitions triggered by harness commands.',
        ],
        'dll_clock': dll_clock_kv,
        'py_clock_bracket': {'before_dll_qpc': py_before_dll, 'after_dll_qpc': py_after_dll},
        'loaded_modules': loaded_modules,
        'udp_table_proof': udp_table_proof,
        'source_stages': src_stages,
        'observer_stages': obs_stages,
        'ready': ready_kv,
        'stage0_gate_check': stage0,
        'stage1_pre_interest': {
            'authority_samples_acked': stage1_auth['samples_acknowledged'],
            'observer_snapshots_received': stage1_obs_client['snapshots_received'],
            'replica_disp_m': float(stage1_probe['rep_disp_m']),
            'src_disp_m': float(stage1_probe['src_disp_m']),
        },
        'stage2_radar_interest_playback': {
            'probe': stage2_probe,
            'authority_samples_acked': stage2_auth['samples_acknowledged'],
            'observer_snapshots_received': stage2_obs_client['snapshots_received'],
            'observer_cockpit_seen': stage2_obs['slots']['cockpit'],
        },
        'stage3_withdrawal': {
            'new_authority_samples_acked': stage3_new_auth_acks,
            'new_observer_snapshots_received': stage3_new_obs_snaps,
            'live_src_advance_m': stage3_src_advance_m,
            'replica_drift_m': stage3_rep_drift_m,
            'probe': stage3_probe,
        },
        'stage4_reacquisition': {
            'extra_replica_disp_m': stage4_extra_disp_m,
            'probe': stage4_probe,
            'authority_samples_acked': stage4_auth['samples_acknowledged'],
            'observer_snapshots_received': stage4_obs_client['snapshots_received'],
            'observer_cockpit_seen': stage4_obs['slots']['cockpit'],
        },
        'stage5_map_interest': {
            'map_open_observed': stage5_map_open_probe['map_observed'] == 'true',
            'map_slot_confirmed': stage5_map_open_obs['slots']['map'],
            'map_closed_new_authority_acks': stage5_map_closed_new_acks,
            'map_closed_new_observer_snapshots': stage5_map_closed_new_snaps,
        },
        'stage6_final': {
            'final_readback': final_kv,
            'npc_source_stats': final_npc_src_stats,
            'authority_sender_stats': final_auth_stats,
            'observer_client_stats': final_obs_client_stats,
            'observer_stats': final_obs_stats,
            'service_traffic': final_service_traffic,
            'readbacks_logged': readbacks,
            'alignment_readbacks_logged': align_readbacks,
            'playback_events': playback_events,
            'observer_events': observer_events,
        },
        'hygiene': {
            'protected_unchanged': protected_before == protected_after,
            'view_removed': not lane_root.exists(),
            'x4_running_after': x4_running(),
            'project_error_count': len(errors),
            'project_errors': errors,
        },
    }
    (args.out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({
        'status': result['status'],
        'out': str(args.out),
        'hygiene': result['hygiene'],
        'ready': {k: ready_kv[k] for k in ('total_registry', 'sector', 'radar_range_m', 'replica_index', 'replica_code', 'replica_owner', 'replica_dist_m', 'replica_within_radar', 'src_index', 'src_code', 'src_owner', 'src_scout_speed_mps', 'unrelated_index', 'unrelated_code', 'unrelated_dist_m', 'unrelated_onscreen') if k in ready_kv},
        'stage0_gate_check': stage0,
        'stage1_pre_interest': result['stage1_pre_interest'],
        'stage2_disp_m': float(stage2_probe['rep_disp_m']),
        'stage3_withdrawal': {k: result['stage3_withdrawal'][k] for k in ('new_authority_samples_acked', 'new_observer_snapshots_received', 'live_src_advance_m', 'replica_drift_m')},
        'stage4_reacq': {'extra_disp_m': stage4_extra_disp_m, 'alignments': int(stage4_probe['alignments']), 'align_readbacks': int(stage4_probe['align_readbacks']), 'reacquisitions': int(stage4_probe['reacquisitions']), 'readbacks': int(stage4_probe['readbacks']), 'max_rb_err_m': float(stage4_probe['max_rb_err_m'])},
        'stage5_map': {'map_seen': stage5_map_open_obs['slots']['map']['samples_confirmed'], 'closed_new_acks': stage5_map_closed_new_acks, 'closed_new_snaps': stage5_map_closed_new_snaps},
        'final': final_kv,
    }, indent=2))
    assert result['hygiene']['protected_unchanged']
    assert result['hygiene']['view_removed']
    assert not result['hygiene']['x4_running_after']
    assert result['hygiene']['project_error_count'] == 0


if __name__ == '__main__':
    main()
