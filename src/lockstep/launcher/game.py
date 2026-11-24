"""Locate and verify the X4 installation; build and remove per-client game views.

A view is a directory on the installation's drive whose top-level files are hard
links to the game's files and whose folders are junctions to the game's folders,
except `extensions`: that is a real folder holding junctions to the official DLCs
and the client's own x4_lan_lab. X4 runs from the view, so the probe's relative
`./extensions/x4_lan_lab/...` paths resolve per client. Third-party extensions in
the installation are not linked. Removal unlinks; it never follows a junction.
"""
import hashlib
import os
from pathlib import Path
import re
import stat

# X4 9.00 Windows (Steam build 23660954): the build the lockstep gate is pinned to.
EXE_SIZE = 55412768
EXE_SHA256 = '19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891'
APP_ID = '392160'
MARKER = '.x4ls-view'


def _is_windows():
    return os.name == 'nt'


def is_link_or_reparse(path):
    """Detect links and Windows reparse points without Path.is_junction (3.12+)."""
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    if _is_windows():
        attributes = getattr(info, 'st_file_attributes', None)
        if type(attributes) is not int:
            raise OSError(f'cannot inspect Windows reparse attributes: {path}')
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return False


def steam_root():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
        return Path(winreg.QueryValueEx(key, 'SteamPath')[0])


def library_paths(vdf_text):
    return [Path(m.replace('\\\\', '\\')) for m in re.findall(r'"path"\s+"([^"]+)"', vdf_text)]


def find_install(steam=None):
    steam = Path(steam) if steam else steam_root()
    libraries = library_paths((steam / 'steamapps/libraryfolders.vdf').read_text(encoding='utf-8', errors='replace'))
    for library in [steam, *libraries]:
        if (library / f'steamapps/appmanifest_{APP_ID}.acf').is_file():
            game = library / 'steamapps/common/X4 Foundations'
            if (game / 'X4.exe').is_file():
                return game
    raise SystemExit('X4 Foundations not found in any Steam library; pass --game')


def verify_exe(game):
    exe = Path(game) / 'X4.exe'
    if exe.stat().st_size != EXE_SIZE:
        raise SystemExit(f'{exe} is not X4 9.00 (size {exe.stat().st_size}); the gate refuses other builds')
    digest = hashlib.sha256()
    with exe.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            digest.update(block)
    if digest.hexdigest() != EXE_SHA256:
        raise SystemExit(f'{exe} SHA-256 differs from the pinned X4 9.00 build')


def _junction(target, link):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def create_view(game, view, junction=_junction, *, base_game_only=False):
    """Build a view of `game` at `view` (new directory on the same drive)."""
    game, view = Path(game).resolve(), Path(view)
    if view.exists():
        raise SystemExit(f'{view} already exists; remove it with x4ls clean')
    if os.path.splitdrive(str(game))[0].lower() != os.path.splitdrive(str(view.resolve()))[0].lower():
        raise SystemExit(f'views must be on the game drive ({os.path.splitdrive(str(game))[0]}) for hard links')
    view.mkdir(parents=True)
    (view / MARKER).write_text(str(game) + '\n')
    for entry in game.iterdir():
        if entry.name == 'extensions':
            continue
        if entry.is_dir():
            junction(entry, view / entry.name)
        else:
            os.link(entry, view / entry.name)
    extensions = view / 'extensions'
    extensions.mkdir()
    if not base_game_only and (game / 'extensions').is_dir():
        for dlc in sorted((game / 'extensions').glob('ego_dlc_*')):
            if dlc.is_dir():
                junction(dlc, extensions / dlc.name)
    return view


def dlc_ids(view):
    return sorted(p.name for p in (Path(view) / 'extensions').glob('ego_dlc_*'))


def _remove_owned(path):
    """Delete a folder the launcher created; links inside are unlinked, never entered."""
    for entry in path.iterdir():
        if is_link_or_reparse(entry):
            os.rmdir(entry) if _is_windows() and entry.is_dir() else os.unlink(entry)
        elif entry.is_dir():
            _remove_owned(entry)
        else:
            os.unlink(entry)
    os.rmdir(path)


def remove_view(view):
    view = Path(view)
    if is_link_or_reparse(view):
        raise SystemExit(f'{view} is a link; refusing to delete')
    if not (view / MARKER).is_file():
        raise SystemExit(f'{view} is not an x4ls view; refusing to delete')
    _remove_owned(view)
