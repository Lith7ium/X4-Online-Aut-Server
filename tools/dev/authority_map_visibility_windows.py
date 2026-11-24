"""One isolated Windows X4 run of the map visibility probe, with a screenshot per view.

  python tools/dev/authority_map_visibility_windows.py --out .local/map-visibility-win-01 [--game DIR]

What it does, in order:
  1. Refuses to start when X4 is running, Steam is not registered, X4.exe is not the pinned 9.00
     build, the output folder exists, or the view folder exists.
  2. Records file metadata (no contents) of the player's other X4 profiles and of the installation's
     extensions folder, to compare after the run.
  3. Builds an x4ls-style view of the game on the game drive (hard links, junctions, official DLCs
     only; the installation's third-party mods are not linked) and composes the probe extension
     x4_cobalt_mapvis in it.
  4. Prepares disposable launcher profile 45850 (refuses a profile the launcher did not create).
  5. Starts X4 windowed in the view and follows its log. On each "SHOT" row it captures the X4
     window's client area to shot-<seq>-<view>.png and answers with ack_<seq>.lua in the extension.
  6. Stops X4 after DONE, ERROR or the timeout, copies the log, removes the view, re-checks the
     protected metadata and writes result.json plus the evidence table.
No save is loaded or written by the probe; the game starts a new creative game in the disposable
profile and pauses before the map is opened.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools' / 'dev'))

EXTENSION_ID = 'x4_cobalt_mapvis'
PROBE = ROOT / 'tools/dev/authority_map_visibility_probe.lua'
SAMPLER = ROOT / 'src/authority/lua/map_render_observation.lua'
LOGFILE = 'cobalt-mapvis.log'
CONTENT = ('<?xml version="1.0" encoding="utf-8"?>\n'
           f'<content id="{EXTENSION_ID}" name="Map visibility probe" description="Disposable read-only map probe" '
           'author="Cobalt" version="100" date="2026-11-20" save="false">\n'
           '  <dependency version="900" />\n</content>\n')


def ui_xml(files):
    rows = ''.join(f'    <file name="{name}" />\n' for name in files)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            f'<addon name="{EXTENSION_ID}">\n  <environment type="menus">\n'
            f'    <dependency name="ego_detailmonitor" />\n{rows}  </environment>\n</addon>\n')


def config_lua(options):
    return ('X4COBALT_MAPVIS = {\n'
            '  enabled = true,\n'
            f'  extension_id = "{EXTENSION_ID}",\n'
            f'  profile = "{int(options["profile"])}",\n'
            f'  gamestart = "{options["gamestart"]}",\n'
            f'  distance = {float(options["distance"])!r},\n'
            f'  pan_factor = {float(options["pan_factor"])!r},\n'
            f'  min_m = {float(options["min_m"])!r},\n'
            f'  max_m = {float(options["max_m"])!r},\n'
            f'  warmup_s = {float(options["warmup_s"])!r},\n'
            f'  map_settle_s = {float(options["map_settle_s"])!r},\n'
            f'  hint_wait_s = {float(options["hint_wait_s"])!r},\n'
            f'  pick_retry_s = {float(options.get("pick_retry_s", 5.0))!r},\n'
            f'  pick_timeout_s = {float(options.get("pick_timeout_s", 300.0))!r},\n'
            '}\n')


def compose_extension(extensions_dir, options):
    """Write <extensions_dir>/x4_cobalt_mapvis; refuses an existing folder."""
    target = Path(extensions_dir) / EXTENSION_ID
    if not options['gamestart'].replace('_', '').isalnum():
        raise SystemExit('invalid gamestart id')
    target.mkdir()
    (target / 'ui').mkdir()
    shutil.copyfile(SAMPLER, target / 'ui/map_render_observation.lua')
    shutil.copyfile(PROBE, target / 'ui/map_visibility_probe.lua')
    (target / 'ui/config.lua').write_text(config_lua(options), encoding='ascii')
    (target / 'content.xml').write_text(CONTENT, encoding='utf-8')
    (target / 'ui.xml').write_text(ui_xml(['ui/config.lua', 'ui/map_render_observation.lua',
                                           'ui/map_visibility_probe.lua']), encoding='utf-8')
    return target


def png_bytes(width, height, rgb_rows):
    """A minimal 8-bit RGB PNG from `height` rows of width*3 bytes."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b''.join(b'\x00' + row for row in rgb_rows)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def tree_metadata(path):
    """{relative path: [size, mtime_ns]} of every file below `path`; links are listed, never entered."""
    path = Path(path)
    result = {}
    if not path.exists():
        return result
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            full = Path(dirpath) / name
            info = full.lstat()
            result[str(full.relative_to(path))] = [info.st_size, info.st_mtime_ns]
    return result


def x4_running():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq X4.exe', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True, check=True).stdout
    return 'x4.exe' in out.lower()


# --- Window capture (Windows only) -------------------------------------------------------------

class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]


def find_window(pid):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            found.append((rect.right * rect.bottom, hwnd))
        return True
    user32.EnumWindows(callback, 0)
    return max(found)[1] if found else None


def capture_window(hwnd, path):
    """Client area to PNG. PrintWindow first; a black frame falls back to a screen copy. Returns the method."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right, rect.bottom
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    window_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    buffer = ctypes.create_string_buffer(width * height * 4)

    def read():
        gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        return buffer.raw

    method = 'print_window'
    ok = user32.PrintWindow(hwnd, memory_dc, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
    data = read()
    if not ok or not any(data[index] for index in range(0, len(data), 4 * 97)):
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.7)
        screen_dc = user32.GetDC(None)
        gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, origin.x, origin.y, 0x00CC0020)
        user32.ReleaseDC(None, screen_dc)
        data = read()
        method = 'screen_copy'
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)
    rows = []
    for y in range(height):
        line = data[y * width * 4:(y + 1) * width * 4]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
        rows.append(bytes(rgb))
    Path(path).write_bytes(png_bytes(width, height, rows))
    return method


# --- Run ---------------------------------------------------------------------------------------

def remove_view_patiently(game_mod, view, attempts=20, delay=1.5):
    """remove_view; when Windows still holds the emptied root for a moment, retry rmdir of that empty folder."""
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
    raise OSError(f'{view} stayed in use; the empty folder was left in place')

def run(args):
    from lockstep.launcher import game as game_mod, profile as profile_mod, steam
    import authority_map_visibility_evidence as evidence_mod

    if args.profile not in profile_mod.PROFILES:
        raise SystemExit(f'Profile {args.profile} is outside the disposable launcher lanes')
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f'{out} exists; output folders are single-use')
    if x4_running():
        raise SystemExit('X4 is running; close it first')
    ok, message = steam.status()
    if not ok:
        raise SystemExit(message)
    game = Path(args.game) if args.game else game_mod.find_install()
    game_mod.verify_exe(game)
    view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'cobalt-mapvis-{out.name}'
    if view.exists():
        raise SystemExit(f'{view} exists; remove it first (it must be an x4ls view)')
    documents = profile_mod.documents()
    protected = {
        'other_profiles': {entry.name: tree_metadata(entry) for entry in sorted(documents.iterdir())
                           if entry.is_dir() and entry.name != str(args.profile)},
        'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
    }
    out.mkdir(parents=True)
    (out / 'protected-before.json').write_text(json.dumps(protected, indent=1, sort_keys=True))
    options = {'profile': args.profile, 'gamestart': args.gamestart, 'distance': args.distance,
               'pan_factor': args.pan_factor, 'min_m': args.min_m, 'max_m': args.max_m,
               'warmup_s': args.warmup, 'map_settle_s': args.map_settle, 'hint_wait_s': args.hint_wait}
    result = {'options': options, 'game': str(game), 'view': str(view), 'shots': {}, 'outcome': 'started'}
    process = log_path = None
    ctypes.windll.user32.SetProcessDPIAware()
    try:
        game_mod.create_view(game, view)
        extension = compose_extension(view / 'extensions', options)
        profile_dir = profile_mod.prepare(args.profile, [*game_mod.dlc_ids(view), EXTENSION_ID],
                                          width=args.width, height=args.height)
        log_path = profile_dir / LOGFILE
        command = [str(view / 'X4.exe'), '-personalfolderid', str(args.profile), '-skipintro', '-noworkshopsync',
                   '-nocputhrottle', '-debug', 'scripts', '-logfile', LOGFILE]
        result['command'] = command
        process = subprocess.Popen(command, cwd=view, env=steam.client_environment())
        result['pid'] = process.pid
        deadline, position, pending = time.monotonic() + args.timeout, 0, ''
        while True:
            if process.poll() is not None:
                result['outcome'] = f'x4_exited_{process.returncode}'
                break
            if time.monotonic() > deadline:
                result['outcome'] = 'timeout'
                break
            if log_path.is_file():
                with log_path.open('rb') as handle:
                    handle.seek(position)
                    chunk = handle.read()
                    position += len(chunk)
                pending += chunk.decode('utf-8', errors='replace')
                lines, _, pending = pending.rpartition('\n')
                finished = None
                for kind, fields in evidence_mod.parse_rows(lines):
                    if kind == 'SHOT':
                        seq, name = int(fields['seq']), fields['view']
                        shot = out / f'shot-{seq}-{name}.png'
                        hwnd = find_window(process.pid)
                        if hwnd is None:
                            raise RuntimeError('X4 window not found for screenshot')
                        time.sleep(args.shot_delay)
                        method = capture_window(hwnd, shot)
                        result['shots'][seq] = {'view': name, 'file': shot.name, 'method': method}
                        partial = extension / f'ack_{seq}.partial'
                        partial.write_text(f'return {{seq = {seq}}}\n', encoding='ascii')
                        os.replace(partial, extension / f'ack_{seq}.lua')  # X4 polls the file every frame
                    elif kind in ('DONE', 'ERROR'):
                        finished = kind
                if finished:
                    result['outcome'] = finished.lower()
                    time.sleep(1.0)
                    break
            time.sleep(0.2)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(30)
        if process is not None:
            result['exit_code'] = process.returncode
        time.sleep(2.0)
        if process is not None and log_path.is_file():
            shutil.copyfile(log_path, out / LOGFILE)
        try:
            remove_view_patiently(game_mod, view)
        except OSError as error:
            result['cleanup_error'] = f'{type(error).__name__}: {error}'
        after = {
            'other_profiles': {entry.name: tree_metadata(entry) for entry in sorted(documents.iterdir())
                               if entry.is_dir() and entry.name != str(args.profile)},
            'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
        }
        (out / 'protected-after.json').write_text(json.dumps(after, indent=1, sort_keys=True))
        result['protected_unchanged'] = after == protected
        result['view_removed'] = not view.exists()
        result['x4_running_after'] = x4_running()
        if (out / LOGFILE).is_file():
            rows = evidence_mod.parse_rows((out / LOGFILE).read_text(encoding='utf-8', errors='replace'))
            shots = {int(seq): out / item['file'] for seq, item in result['shots'].items()}
            result['evidence'] = evidence_mod.evaluate(rows, shots)
            (out / 'evidence.md').write_text(evidence_mod.markdown(result['evidence']), encoding='utf-8')
        with (out / 'result.json').open('x', encoding='utf-8') as handle:
            json.dump(result, handle, indent=2, default=str)
    return result


def successful_run(result):
    return (result['outcome'] == 'done' and result['protected_unchanged']
            and result['view_removed'] and not result['x4_running_after']
            and result.get('evidence', {}).get('accepted', False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--game')
    parser.add_argument('--profile', type=int, default=45850)
    parser.add_argument('--gamestart', default='custom_creative')
    parser.add_argument('--distance', type=float, default=20000.0, help='map camera distance of the views')
    parser.add_argument('--pan-factor', type=float, default=4.0, help='offscreen pan in camera distances along X')
    parser.add_argument('--min-m', type=float, default=500.0)
    parser.add_argument('--max-m', type=float, default=1e7, help='NPC distance limit; the onscreen view centres on the NPC')
    parser.add_argument('--warmup', type=float, default=20.0, help='seconds of play before picking the NPC')
    parser.add_argument('--hint-wait', type=float, default=10.0, help='extra seconds of play for start hints to expire')
    parser.add_argument('--map-settle', type=float, default=3.0)
    parser.add_argument('--shot-delay', type=float, default=0.5)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--timeout', type=float, default=900.0)
    args = parser.parse_args(argv)
    result = run(args)
    print(json.dumps({key: result.get(key) for key in ('outcome', 'exit_code', 'protected_unchanged',
                                                         'view_removed', 'x4_running_after')}))
    if 'evidence' in result:
        print((Path(args.out) / 'evidence.md').read_text(encoding='utf-8'))
    return 0 if successful_run(result) else 1


if __name__ == '__main__':
    raise SystemExit(main())
