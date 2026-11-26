"""Disposable X4 profiles (-personalfolderid) for launcher clients.

prepare() creates a marked profile in a reserved slot or resets one it owns.
prepare_fresh() exclusively creates a separate profile for its caller.
"""
import ctypes
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import xml.etree.ElementTree as ET

from authority.file_fingerprints import fingerprint, same_path_and_descriptor

from .game import is_link_or_reparse

PROFILES = (45850, 45851, 45853, 45854, 45855, 45856, 45857, 45858)
CAPTURE_PROFILE = 45841  # tools/dev/native_gate_snapshot.lua only saves inside .../45841/save
SNAPSHOT_SLOT = 'save_009.xml.gz'
_OWNER_MARKER = '.x4ls-profile'
_OWNER_FORMAT = re.compile(r'x4ls-profile-v1 ([0-9]+:[1-9][0-9]*) ([0-9]+:[1-9][0-9]*)\n'
                           r'(none|[0-9]+:[1-9][0-9]*:[0-9]+:-?[0-9]+:-?[0-9]+:[a-f0-9]{64})\n')


class _KnownFolderId(ctypes.Structure):
    _fields_ = [
        ('data1', ctypes.c_uint32),
        ('data2', ctypes.c_uint16),
        ('data3', ctypes.c_uint16),
        ('data4', ctypes.c_ubyte * 8),
    ]


_FOLDERID_DOCUMENTS = _KnownFolderId(
    0xFDD39AD0, 0x238F, 0x46AF,
    (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
)


def _windows_documents_directory():
    """Resolve the current user's Documents folder through Windows Known Folders."""
    shell32 = ctypes.WinDLL('shell32')
    ole32 = ctypes.WinDLL('ole32')
    get_known_folder_path = shell32.SHGetKnownFolderPath
    get_known_folder_path.argtypes = [
        ctypes.POINTER(_KnownFolderId), ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_known_folder_path.restype = ctypes.c_int32
    free_task_memory = ole32.CoTaskMemFree
    free_task_memory.argtypes = [ctypes.c_void_p]
    free_task_memory.restype = None

    path_pointer = ctypes.c_void_p()
    try:
        result = get_known_folder_path(
            ctypes.byref(_FOLDERID_DOCUMENTS), 0, None, ctypes.byref(path_pointer),
        )
        if result < 0:
            raise OSError(f'SHGetKnownFolderPath failed with HRESULT 0x{result & 0xFFFFFFFF:08X}')
        if not path_pointer.value:
            raise OSError('SHGetKnownFolderPath succeeded without returning a path')
        path_text = ctypes.wstring_at(path_pointer.value)
        if not path_text:
            raise OSError('SHGetKnownFolderPath returned an empty path')
        return Path(path_text)
    finally:
        if path_pointer.value:
            free_task_memory(path_pointer)


def documents():
    if sys.platform == 'win32':
        documents_directory = _windows_documents_directory()
    else:
        documents_directory = Path(os.environ['USERPROFILE']) / 'Documents'
    return documents_directory / 'Egosoft' / 'X4'


def profile_dir(profile, root=None):
    if profile not in (*PROFILES, CAPTURE_PROFILE):
        raise SystemExit(f'profile {profile} is not a disposable launcher profile')
    path = (Path(root) if root else documents()) / str(profile)
    if is_link_or_reparse(path):
        raise SystemExit(f'{path} is a link; refusing')
    return path


# Render-only settings turned down for unattended soaks (--fast): more frames per second means more
# admitted ticks per second. Detail distances (lodfactor, effectdistance) stay at 1.0 because they can
# change what the simulation does; every client gets identical settings either way.
FAST = {'gfxquality': 0, 'shaderquality': 0, 'shadows': 0, 'softshadows': 'false', 'ssao': 0, 'glow': 0, 'uiglow': 0,
        'distortion': 'false', 'ssr': 'false', 'texturequality': 'low', 'chromaticaberration': 'false', 'volumetric': 0,
        'pom': 'off', 'envmapprobes': 'low', 'frameratelimit': 'false', 'presentmode': 'immediate', 'nosound': 'true'}


# X4 9.00's display options and graphics presets, including platform and
# upscaler settings written by the game. Preserve the pilot's own enum values.
_GRAPHICS_FIELDS = frozenset('''
    fullscreen borderless res_width res_height antialiasing vsync upmode fsr3g
    dlss dlssmode dlssg presentmode colorcorrection gamma fov capturehq
    frameratelimit frameratetarget gfxquality shaderquality shadows softshadows
    ssao glow uiglow uiglowintensity lodfactor effectdistance distortion
    disableradar radarquality ssr ssrquality texturequality chromaticaberration
    volumetric pom envmapprobes envmapinsideglassfactor chardensity trafficdensity
    globallightscale stardustintensity hudscale cockpitcamerascale
'''.split())


class GraphicsConfigError(ValueError):
    """An actionable, credential-free reason the player's display settings could not be read."""


def _normal_graphics_source(root, source, account_id):
    if source is not None:
        return Path(source)
    reserved = {str(number) for number in (*PROFILES, CAPTURE_PROFILE)}
    if account_id is not None:
        if type(account_id) is not int or not 1 <= account_id < 2**32 or str(account_id) in reserved:
            raise GraphicsConfigError('Invalid normal X4 account for graphics settings.')
        candidate = root / str(account_id) / 'config.xml'
        return candidate if candidate.is_file() else None
    if not root.is_dir():
        return None
    candidates = [entry / 'config.xml' for entry in root.iterdir()
                  if entry.name.isascii() and entry.name.isdecimal() and entry.name not in reserved
                  and not is_link_or_reparse(entry) and (entry / 'config.xml').is_file()]
    if (root / 'config.xml').is_file():
        candidates.append(root / 'config.xml')
    if len(candidates) > 1:
        raise GraphicsConfigError('Multiple normal X4 profiles found. Select your config.xml with '
                                  '--graphics-config (PowerShell: -GraphicsConfig).')
    return next(iter(candidates), None)


def human_graphics_xml(profile_root, *, source=None, account_id=None):
    """Read the pilot's saved display preferences into a new isolated configuration."""
    result = ET.fromstring('<root><autosave>false</autosave><uisafemode>false</uisafemode></root>')
    try:
        config = _normal_graphics_source(Path(profile_root), source, account_id)
        if config is not None:
            if is_link_or_reparse(config.parent) or is_link_or_reparse(config) or not config.is_file():
                raise GraphicsConfigError('Select a regular X4 config.xml file for graphics settings.')
            with config.open('rb') as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise GraphicsConfigError('The selected X4 graphics configuration is too large.')
            user_config = ET.fromstring(data)
            if user_config.tag != 'root':
                raise GraphicsConfigError('Expected an X4 config.xml with a root element named root.')
            for setting in user_config:
                if setting.tag in _GRAPHICS_FIELDS and setting.text is not None and not len(setting):
                    ET.SubElement(result, setting.tag).text = setting.text
    except (OSError, ET.ParseError) as error:
        raise GraphicsConfigError('Could not read the selected X4 config.xml. Check the graphics config path.') from error
    return ET.tostring(result, encoding='unicode') + '\n'


def config_xml(width, height, fast=False, borderless=False, *, disable_chromatic_aberration=False):
    """borderless: X4's borderless window (it fills the screen and cannot be resized: a resize reloads X4's UI)."""
    settings = FAST if fast else {}
    if disable_chromatic_aberration and not fast:
        settings = {'chromaticaberration': 'false'}
    extra = ''.join(f'<{key}>{value}</{key}>' for key, value in settings.items())
    return ('<?xml version="1.0"?><root><autosave>false</autosave><uisafemode>false</uisafemode>'
            f'<fullscreen>false</fullscreen><borderless>{"true" if borderless else "false"}</borderless><res_width>{width}</res_width>'
            f'<res_height>{height}</res_height><antialiasing>0</antialiasing><vsync>false</vsync>{extra}</root>\n')


def content_xml(extension_ids):
    rows = ''.join(f'<extension id="{name}" enabled="true" />' for name in extension_ids)
    return f'<?xml version="1.0"?><content>{rows}</content>\n'


def prepare(profile, extension_ids, width=1280, height=720, snapshot=None, root=None, fast=False, borderless=False):
    """Create a disposable profile, resetting only one previously created here."""
    path = profile_dir(profile, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _directory_id(path.parent) is None:
        raise SystemExit(f'{path.parent} is not an ordinary profile root; refusing')
    if path.exists():
        if _owned_staging(path) is None:
            raise SystemExit(f'{path} is not a launcher-owned profile; move it to a backup location before reusing this lane')
        shutil.rmtree(path)
    # Exclusive creation is the ownership boundary. Never mark a folder that already existed.
    path.mkdir()
    (path / 'save').mkdir()
    (path / 'config.xml').write_text(config_xml(width, height, fast, borderless))
    (path / 'content.xml').write_text(content_xml(extension_ids))
    snapshot_identity = 'none'
    if snapshot:
        staged = path / 'save' / SNAPSHOT_SLOT
        with Path(snapshot).open('rb') as source, staged.open('xb') as destination:
            shutil.copyfileobj(source, destination)
        snapshot_identity = _snapshot_id(staged)
    profile_identity = _directory_id(path)
    save_identity = _directory_id(path / 'save')
    if None in (profile_identity, save_identity, snapshot_identity):
        raise OSError('cannot identify the newly prepared launcher profile')
    with (path / _OWNER_MARKER).open('x', encoding='ascii', newline='\n') as marker:
        marker.write(f'x4ls-profile-v1 {profile_identity} {save_identity}\n{snapshot_identity}\n')
    return path


def _valid_profile_root(info, *, windows):
    """Reject links and, on Windows, all final-component reparse points."""
    if not stat.S_ISDIR(info.st_mode):
        return False
    if windows:
        reparse_point = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', None)
        attributes = getattr(info, 'st_file_attributes', None)
        if (type(reparse_point) is not int or reparse_point <= 0 or
                type(attributes) is not int or attributes < 0):
            return False
        if attributes & reparse_point:
            return False
    return True


def _directory_id(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not _valid_profile_root(info, windows=os.name == 'nt') or not info.st_ino:
        return None
    return f'{info.st_dev}:{info.st_ino}'


def _ordinary_file(info):
    return (stat.S_ISREG(info.st_mode) and info.st_ino and info.st_nlink == 1
            and not (getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def _file_id(info):
    return ':'.join(str(value) for value in fingerprint(info))


def _snapshot_id(path):
    info = path.lstat()
    if not _ordinary_file(info):
        return None
    identity = _file_id(info)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        opened = os.fstat(source.fileno())
        if not same_path_and_descriptor(info, opened):
            return None
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
        if fingerprint(os.fstat(source.fileno())) != fingerprint(opened):
            return None
    if _file_id(path.lstat()) != identity:
        return None
    return f'{identity}:{digest.hexdigest()}'


def _owned_staging(path):
    """Return the recorded snapshot identity, or None if ownership is unproven."""
    marker = path / _OWNER_MARKER
    try:
        if _directory_id(path.parent) is None:
            return None
        profile_identity = _directory_id(path)
        save_identity = _directory_id(path / 'save') if profile_identity else None
        if not save_identity:
            return None
        info = marker.lstat()
        if not _ordinary_file(info) or info.st_size > 512:
            return None
        with marker.open('rb') as source:
            opened = os.fstat(source.fileno())
            if not same_path_and_descriptor(info, opened):
                return None
            contents = source.read(513).decode('ascii')
            if fingerprint(os.fstat(source.fileno())) != fingerprint(opened):
                return None
        if fingerprint(marker.lstat()) != fingerprint(info):
            return None
    except (OSError, UnicodeError):
        return None
    match = _OWNER_FORMAT.fullmatch(contents)
    if match and match.group(1, 2) == (profile_identity, save_identity):
        return match.group(3)
    return None


def owned_save_dir(profile, root=None):
    """Return the save directory only if its launcher ownership is intact."""
    path = profile_dir(profile, root)
    if _owned_staging(path) is not None:
        return path / 'save'
    return None


def prepare_fresh(profile, extension_ids, *, root, width=1280, height=720,
                  fast=False, borderless=False, graphics_xml=None):
    """Create an absent disposable profile without removing existing files.

    This is for experiments whose supervisor exclusively controls ``root``,
    its ancestors and the new profile throughout preparation and use. The
    root must already exist; no save is staged. A failure after directory
    creation can leave a partial profile, which the caller must inspect
    rather than reuse. This does not guard against another process replacing
    a directory during preparation.
    """
    if (type(profile) is not int or profile not in (*PROFILES, CAPTURE_PROFILE) or
            type(extension_ids) not in (list, tuple) or
            any(type(name) is not str or not re.fullmatch(r'[A-Za-z0-9_]+', name)
                for name in extension_ids) or
            type(width) is not int or not 320 <= width <= 16384 or
            type(height) is not int or not 200 <= height <= 16384 or
            type(fast) is not bool or type(borderless) is not bool or
            (graphics_xml is not None and not isinstance(graphics_xml, str))):
        raise ValueError('invalid disposable profile configuration')
    root = Path(root)
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise ValueError('existing, non-link profile root required') from exc
    if not _valid_profile_root(root_info, windows=os.name == 'nt'):
        raise ValueError('existing, non-link profile root required')
    path = profile_dir(profile, root)
    # mkdir without exist_ok is the ownership boundary: existing directories,
    # links and files all fail without modifying them.
    path.mkdir()
    (path / 'save').mkdir()
    with (path / 'config.xml').open('x', encoding='utf-8', newline='\n') as output:
        output.write(config_xml(width, height, fast, borderless) if graphics_xml is None else graphics_xml)
    with (path / 'content.xml').open('x', encoding='utf-8', newline='\n') as output:
        output.write(content_xml(extension_ids))
    return path


def unstage(profile, root=None):
    """Remove only the same snapshot prepare() staged in an owned profile."""
    path = profile_dir(profile, root)
    expected = _owned_staging(path)
    if expected in (None, 'none'):
        return False
    staged = path / 'save' / SNAPSHOT_SLOT
    try:
        if _snapshot_id(staged) != expected or _owned_staging(path) != expected:
            return False
        if _file_id(staged.lstat()) != expected.rsplit(':', 1)[0]:
            return False
        staged.unlink()
    except FileNotFoundError:
        return False
    return True


def collect(profile, logfile, out, root=None):
    """Copy the client's log (and any saves it wrote) into the run directory."""
    path = profile_dir(profile, root)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if (path / logfile).is_file():
        shutil.copy2(path / logfile, out / 'engine-output.log')  # the name the census tools read
    if (path / 'x4lan-crash.txt').is_file():  # the crash note (native_gate_crash_note): an unhandled exception's CRASH line
        shutil.copy2(path / 'x4lan-crash.txt', out / 'x4lan-crash.txt')
    saves = [p for p in (path / 'save').glob('*') if p.is_file() and p.name != SNAPSHOT_SLOT] if (path / 'save').is_dir() else []   # files only: a hot join's save/mpjoin stays
    for save in saves:
        shutil.copy2(save, out / save.name)
