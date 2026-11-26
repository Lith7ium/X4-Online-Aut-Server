"""Isolated native Windows X4 9.00 runner for read-only combat observation.

Runs the reusable read-only X4AuthorityCombatObserver + X4LAN_COMBAT_OBSERVER inside an
isolated disposable Windows X4 9.00 world (profile 45850, isolated x4ls view, 800x600,
fast=True, SW_SHOWNOACTIVATE + HWND_BOTTOM, Kill-on-close Job) and captures real in-game
weapon hits across:
  - Same-Company friendly fire (player.controlled Pulsar -> unoccupied registered Company Pulsar):
    shield-absorbed-only phase and shield-depletion + hull-overflow phase.
  - Hostile NPC (Xenon fighter) -> unoccupied registered Company Pulsar.
  - Player Company Pulsar -> Xenon NPC fighter: shield-absorbed-only and hull-overflow phases.
  - Hostile NPC -> player-occupied Company Pulsar (verifying event_player_ship_hit).

Strictly refuses to disturb an already-running X4.exe and verifies protected_unchanged,
view_removed, and x4_running_after == False on teardown.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tools' / 'dev')]

from authority.combat_observation import (
    parse_log,
    summarize_by_phase,
    validate_lua_read_only,
    validate_md_read_only,
)

EXTENSION_ID = 'x4_neon_combat_obs'
LOGFILE = 'neon-combat-obs.log'
EPOCH = 'c0ba70b54ba7be438ebb04ec5bb85fdb'

SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
STARTF_USESHOWWINDOW = 0x00000001
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

UI_FILES = [
    ('combat_observer.lua', 'src/authority/lua/combat_observer.lua'),
    ('authority_combat_observation_x4.lua', 'tools/dev/authority_combat_observation_x4.lua'),
]
MD_FILES = [
    ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
    ('combat_observer.xml', 'src/authority/md/combat_observer.xml'),
    ('authority_combat_observation_probe.xml', 'tools/dev/authority_combat_observation_probe.xml'),
]
AISCRIPT_FILES = [
    ('x4neon.combat.shoot_at.xml', 'tools/dev/authority_combat_observation_shoot_at.xml'),
]
LIBRARY_FILES = [
    ('loadouts.xml', 'tools/dev/fixtures/vanilla-flight/libraries/loadouts.xml'),
]
# The overlap scenario swaps the case driver for the two-shooter driver and adds its trace.
OVERLAP_UI_FILES = [
    UI_FILES[0],
    ('authority_combat_overlap_x4.lua', 'tools/dev/authority_combat_overlap_x4.lua'),
]
OVERLAP_MD_FILES = MD_FILES + [
    ('authority_combat_overlap_trace.xml', 'tools/dev/authority_combat_overlap_trace.xml'),
]

CONTENT_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<content id="{EXTENSION_ID}" name="Authority read-only combat observation" '
    'description="Disposable native read-only combat observation run" '
    'author="Neon" version="100" date="2026-11-25" save="false">\n'
    '  <dependency version="900" />\n'
    '</content>\n'
)


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def compose_extension(extensions_dir, *, profile, gamestart, epoch, scenario='cases'):
    # Guard read-only contract on the reusable production observer modules before launching X4.
    validate_md_read_only((ROOT / 'src/authority/md/combat_observer.xml').read_text(encoding='utf-8'))
    validate_lua_read_only((ROOT / 'src/authority/lua/combat_observer.lua').read_text(encoding='utf-8'))
    ui_files = OVERLAP_UI_FILES if scenario == 'overlap' else UI_FILES
    md_files = OVERLAP_MD_FILES if scenario == 'overlap' else MD_FILES

    target = Path(extensions_dir) / EXTENSION_ID
    target.mkdir()
    for sub in ('ui', 'md', 'aiscripts', 'libraries'):
        (target / sub).mkdir()
    for dst_name, rel_src in md_files:
        shutil.copyfile(ROOT / rel_src, target / 'md' / dst_name)
    for dst_name, rel_src in ui_files:
        shutil.copyfile(ROOT / rel_src, target / 'ui' / dst_name)
    for dst_name, rel_src in AISCRIPT_FILES:
        shutil.copyfile(ROOT / rel_src, target / 'aiscripts' / dst_name)
    for dst_name, rel_src in LIBRARY_FILES:
        shutil.copyfile(ROOT / rel_src, target / 'libraries' / dst_name)

    cfg_text = (
        'X4NEON_COMBAT_OBS = {\n'
        '  enabled = true,\n'
        f'  extension_id = "{EXTENSION_ID}",\n'
        f'  profile = "{int(profile)}",\n'
        f'  gamestart = "{gamestart}",\n'
        f'  epoch = "{epoch}",\n'
        '}\n'
    )
    (target / 'ui' / 'config.lua').write_text(cfg_text, encoding='ascii')
    ui_entries = ''.join(
        ['    <file name="ui/config.lua" />\n']
        + [f'    <file name="ui/{dst}" />\n' for dst, _ in ui_files]
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


def x4_running():
    out = subprocess.run(
        ['tasklist', '/FI', 'IMAGENAME eq X4.exe', '/FO', 'CSV', '/NH'],
        capture_output=True, text=True, check=True,
    ).stdout
    return 'x4.exe' in out.lower()


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
                ('f', ctypes.c_size_t), ('g', ctypes.c_size_t), ('h', wintypes.DWORD),
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


def main(argv=None):
    from lockstep.launcher import game as game_mod, profile as profile_mod, steam

    parser = argparse.ArgumentParser(description='Isolated native Windows X4 9.00 combat observation run')
    parser.add_argument('--game', help='Optional explicit path to X4 installation directory')
    parser.add_argument('--out', required=True, help='Output directory under .local/')
    parser.add_argument('--profile', type=int, default=45850, help='Disposable launcher profile ID')
    parser.add_argument('--gamestart', default='custom_creative')
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--timeout', type=float, default=180.0)
    parser.add_argument('--scenario', choices=('cases', 'overlap'), default='cases',
                        help='cases: the six single-shooter cases; overlap: two shooters on one registered NPC')
    args = parser.parse_args(argv)

    if os.name != 'nt' or sys.version_info < (3, 12):
        raise SystemExit('Python 3.12+ required on Windows for safe x4ls view/profile teardown')
    if args.profile not in profile_mod.PROFILES:
        raise SystemExit(f'Profile {args.profile} is outside disposable launcher lanes')
    out_dir = Path(args.out).resolve()
    if out_dir.exists():
        raise SystemExit(f'{out_dir} already exists; use a fresh output directory')
    if x4_running():
        raise SystemExit('ABORT: X4.exe is already running; refusing to disturb an existing X4 session')
    ok, msg = steam.status()
    if not ok:
        raise SystemExit(msg)

    game_dir = Path(args.game).resolve() if args.game else game_mod.find_install()
    game_mod.verify_exe(game_dir)
    exe_path = game_dir / 'X4.exe'

    view_dir = Path(os.path.splitdrive(str(game_dir.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-combat-{out_dir.name}'
    if view_dir.exists():
        raise SystemExit(f'{view_dir} already exists')

    documents = profile_mod.documents()
    protected_before = {
        'other_profiles': {
            entry.name: tree_metadata(entry)
            for entry in sorted(documents.iterdir())
            if entry.is_dir() and entry.name != str(args.profile)
        },
        'game_extensions': sorted(entry.name for entry in (game_dir / 'extensions').iterdir()),
    }
    out_dir.mkdir(parents=True)
    (out_dir / 'protected-before.json').write_text(json.dumps(protected_before, indent=2), encoding='utf-8')
    ctypes.windll.user32.SetProcessDPIAware()

    process = None
    view_created = False
    job = None
    log_path = None
    outcome = 'timeout'
    driver_lines = []
    probe_lines = []
    error_lines = []
    started_at = time.monotonic()

    try:
        game_mod.create_view(game_dir, view_dir)
        view_created = True
        compose_extension(
            view_dir / 'extensions',
            profile=args.profile,
            gamestart=args.gamestart,
            epoch=EPOCH,
            scenario=args.scenario,
        )
        profile_dir = profile_mod.prepare_fresh(
            args.profile,
            [*game_mod.dlc_ids(view_dir), EXTENSION_ID],
            root=documents,
            width=args.width, height=args.height, fast=True,
        )
        log_path = profile_dir / LOGFILE
        if log_path.exists():
            raise FileExistsError(f'unexpected combat log already at {log_path}')

        cmd = [
            str(view_dir / 'X4.exe'),
            '-personalfolderid', str(args.profile),
            '-skipintro',
            '-noworkshopsync',
            '-nocputhrottle',
            '-debug', 'scripts',
            '-logfile', LOGFILE,
        ]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWNOACTIVATE
        process = subprocess.Popen(cmd, cwd=str(view_dir), env=steam.client_environment(), startupinfo=si)
        job = attach_kill_on_close_job(process.pid)

        hwnd = None
        last_tuck = 0.0
        pos = 0
        pending = ''
        deadline = time.monotonic() + args.timeout

        while time.monotonic() < deadline:
            now = time.monotonic()
            if hwnd is None:
                hwnd = find_window(process.pid)
                if hwnd is not None:
                    tuck_window_bottom_corner(hwnd)
                    last_tuck = now
            elif now - last_tuck >= 0.75:
                tuck_window_bottom_corner(hwnd)
                last_tuck = now

            if log_path.is_file():
                try:
                    with log_path.open('rb') as handle:
                        handle.seek(pos)
                        chunk = handle.read()
                        pos += len(chunk)
                except OSError:
                    chunk = b''
                if chunk:
                    pending += chunk.decode('utf-8', errors='replace')
                    lines, _, pending = pending.rpartition('\n')
                    for line in lines.splitlines():
                        if '[X4LAN] AUTH_COMBAT_DRIVER ' in line:
                            msg = line.split('[X4LAN] AUTH_COMBAT_DRIVER ', 1)[1].strip()
                            driver_lines.append(msg)
                            if msg == 'DONE':
                                outcome = 'done'
                            elif msg.startswith('ERROR '):
                                outcome = 'error'
                                error_lines.append(msg)
                        elif '[X4LAN] AUTH_COMBAT_PROBE ' in line:
                            probe_lines.append(line.split('[X4LAN] AUTH_COMBAT_PROBE ', 1)[1].strip())
                    if outcome in ('done', 'error'):
                        time.sleep(0.4)
                        break

            if process.poll() is not None:
                if outcome == 'timeout':
                    outcome = f'exited_{process.returncode}'
                break
            time.sleep(0.15)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if job is not None and os.name == 'nt':
            try:
                ctypes.windll.kernel32.CloseHandle(job)
            except OSError:
                pass
        if log_path is not None and log_path.is_file():
            shutil.copyfile(log_path, out_dir / LOGFILE)
        if view_created:
            remove_view_patiently(game_mod, view_dir)

    protected_after = {
        'other_profiles': {
            entry.name: tree_metadata(entry)
            for entry in sorted(documents.iterdir())
            if entry.is_dir() and entry.name != str(args.profile)
        },
        'game_extensions': sorted(entry.name for entry in (game_dir / 'extensions').iterdir()),
    }
    (out_dir / 'protected-after.json').write_text(json.dumps(protected_after, indent=2), encoding='utf-8')
    protected_unchanged = (protected_before == protected_after)
    view_removed = not view_dir.exists()
    still_running = x4_running()

    copied_log = out_dir / LOGFILE
    log_text = copied_log.read_text(encoding='utf-8', errors='replace') if copied_log.is_file() else ''
    events, readbacks = parse_log(log_text)
    by_phase = summarize_by_phase(
        events,
        readbacks,
        phase_victims={
            'ff_shield_only': 'company_ff_target',
            'ff_hull_overflow': 'company_ff_target',
            'npc_to_company': 'company_npc_target',
            'npc_shield_only': 'npc_target',
            'npc_hull_overflow': 'npc_target',
            'npc_to_player_occupied': 'company_player',
        },
    )

    project_errors = [
        line.strip()
        for line in log_text.splitlines()
        if any(tok in line for tok in (EXTENSION_ID, 'X4AuthorityCombatObserver', 'X4AuthorityCombatProbe', 'X4AuthorityCombatOverlapTrace', 'authority_combat_overlap_x4', 'combat_observer', 'x4neon.combat.shoot_at'))
        and ('[=ERROR=]' in line or 'Error in MD cue' in line or 'Error in AI script' in line or 'Error while executing' in line)
        and 'Could not find signature file' not in line
    ]

    weapon_profiles = [line for line in driver_lines if line.startswith('WEAPON_PROFILE ')]
    fallbacks = [line for line in driver_lines if line.startswith('FALLBACK ')]

    if args.scenario == 'overlap':
        from authority_combat_overlap_analysis import OVERLAP_PHASES, accepted as overlap_ok, analyse
        by_phase = summarize_by_phase(events, readbacks, phase_victims={p: 'npc_target' for p in OVERLAP_PHASES})
        overlap = analyse(log_text, events, epoch=EPOCH)
        driver_errors = [line.split('[X4LAN] AUTH_COMBAT_DRIVER ', 1)[1].strip()
                         for line in log_text.splitlines() if '[X4LAN] AUTH_COMBAT_DRIVER ERROR ' in line]
        outside_phase_damage = [event.seq for event in events if event.phase not in overlap
                                and event.kind in ('shield_damaged', 'hull_damaged')]
        accepted = (
            outcome == 'done' and protected_unchanged and view_removed and not still_running
            and not project_errors and not error_lines and not driver_errors and not outside_phase_damage
            and overlap_ok(overlap)
        )
        result = {
            'scenario': 'overlap', 'outcome': outcome, 'accepted': accepted,
            'elapsed_s': round(time.monotonic() - started_at, 2),
            'x4_exe_sha256': sha256_file(exe_path),
            'logfile': str(copied_log) if copied_log.is_file() else None,
            'logfile_sha256': sha256_file(copied_log) if copied_log.is_file() else None,
            'protected_unchanged': protected_unchanged, 'view_removed': view_removed,
            'x4_running_after': still_running,
            'project_error_count': len(project_errors), 'project_errors': project_errors[:20],
            'driver_errors': driver_errors or error_lines, 'weapon_profiles': weapon_profiles, 'fallbacks': fallbacks,
            'outside_phase_damage_sequences': outside_phase_damage,
            'driver_lines': driver_lines, 'probe_lines': probe_lines,
            'overlap': overlap, 'phases': by_phase,
        }
        res_path = out_dir / 'result.json'
        res_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({k: result[k] for k in ('outcome', 'accepted', 'elapsed_s', 'project_error_count',
                                                  'protected_unchanged', 'view_removed', 'x4_running_after')}
                         | {'result_json': str(res_path)}, indent=2))
        raise SystemExit(0 if accepted else 1)

    # Verify all required native combat observations succeeded.
    p1a = by_phase.get('ff_shield_only', {})
    p1b = by_phase.get('ff_hull_overflow', {})
    p2 = by_phase.get('npc_to_company', {})
    p3a = by_phase.get('npc_shield_only', {})
    p3b = by_phase.get('npc_hull_overflow', {})
    p4 = by_phase.get('npc_to_player_occupied', {})

    accepted = (
        outcome == 'done'
        and protected_unchanged
        and view_removed
        and not still_running
        and len(project_errors) == 0
        and len(error_lines) == 0
        and p1a.get('shield_only_absorbed') is True
        and p1b.get('hull_damage_callbacks', 0) > 0
        and p2.get('shield_damage_callbacks', 0) > 0
        and p3a.get('shield_only_absorbed') is True
        and p3b.get('hull_damage_callbacks', 0) > 0
        and p4.get('shield_damage_callbacks', 0) > 0
        and p4.get('player_ship_hit_count', 0) > 0
    )

    result = {
        'outcome': outcome,
        'accepted': accepted,
        'elapsed_s': round(time.monotonic() - started_at, 2),
        'x4_exe_sha256': sha256_file(exe_path),
        'logfile': str(copied_log) if copied_log.is_file() else None,
        'logfile_bytes': copied_log.stat().st_size if copied_log.is_file() else 0,
        'logfile_sha256': sha256_file(copied_log) if copied_log.is_file() else None,
        'protected_unchanged': protected_unchanged,
        'view_removed': view_removed,
        'x4_running_after': still_running,
        'project_error_count': len(project_errors),
        'project_errors': project_errors[:20],
        'driver_errors': error_lines,
        'total_md_events': len(events),
        'total_lua_readbacks': len(readbacks),
        'weapon_profiles': weapon_profiles,
        'fallbacks': fallbacks,
        'driver_lines': driver_lines,
        'probe_lines': probe_lines,
        'phases': by_phase,
    }
    res_path = out_dir / 'result.json'
    res_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({
        'outcome': outcome,
        'accepted': accepted,
        'elapsed_s': result['elapsed_s'],
        'project_error_count': len(project_errors),
        'total_md_events': len(events),
        'total_lua_readbacks': len(readbacks),
        'protected_unchanged': protected_unchanged,
        'view_removed': view_removed,
        'x4_running_after': still_running,
        'result_json': str(res_path),
    }, indent=2))
    raise SystemExit(0 if accepted else 1)


if __name__ == '__main__':
    main()
