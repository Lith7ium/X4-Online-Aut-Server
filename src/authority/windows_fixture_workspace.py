"""Private, disposable filesystem workspace for an opt-in Windows X4 fixture.

``view_directory`` is a new private container. The linked game view lives at
``view_directory / 'game'`` so its hardlinked files never receive ACL edits.
The run directory and profile are also new and retained after close. This
class does not start X4, remove profiles/views, or prevent a malicious local
process from racing path checks. Its checks are sampled observations for an
isolated workstation, not an atomic filesystem transaction.

ACL setup uses ``icacls`` on owned empty roots (without /T); the Windows
command syntax is documented at:
https://learn.microsoft.com/windows-server/administration/windows-commands/icacls
The preflight process inventory uses tasklist CSV:
https://learn.microsoft.com/windows-server/administration/windows-commands/tasklist
"""
import csv
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from authority.file_fingerprints import fingerprint
from lockstep.launcher import game, profile


PROFILE_ID = 45841
MAX_LOG_BYTES = 8 << 20
_EXTENSION_ID = 'x4_lan_lab'
_SAVE_NAME = 'save_009.xml.gz'
_SID = re.compile(r'S-1-(?:[0-9]+-){1,14}[0-9]+\Z', re.IGNORECASE)


def _is_windows():
    return sys.platform == 'win32'


def _ordinary(path, *, directory):
    """Return a like-for-like lstat fingerprint for an ordinary path."""
    info = Path(path).lstat()
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected:
        raise ValueError('ordinary directory or file required')
    if os.name == 'nt':
        attributes = getattr(info, 'st_file_attributes', None)
        if type(attributes) is not int or attributes < 0 or attributes & 0x400:
            raise ValueError('Windows reparse point not permitted')
    return fingerprint(info)


def _directory_identity(path):
    _ordinary(path, directory=True)
    info = Path(path).lstat()
    if type(info.st_ino) is not int or info.st_ino == 0:
        raise ValueError('directory identity unavailable')
    return info.st_dev, info.st_ino


def _file_identity(info):
    if type(info.st_ino) is not int or info.st_ino == 0:
        raise ValueError('file identity unavailable')
    return info.st_dev, info.st_ino


def _read_small_file(path, *, maximum=1 << 20):
    before = Path(path).lstat()
    _ordinary(path, directory=False)
    if before.st_size > maximum:
        raise ValueError('workspace settings file exceeds its bound')
    with Path(path).open('rb') as source:
        data = source.read(maximum + 1)
    after = Path(path).lstat()
    _ordinary(path, directory=False)
    if (len(data) != before.st_size or len(data) > maximum or
            fingerprint(before) != fingerprint(after)):
        raise RuntimeError('workspace settings changed while reading')
    return data


def _directory_pins(paths):
    pins = {}
    for path in paths:
        for directory in (Path(path), *Path(path).parents):
            if directory not in pins:
                pins[directory] = _directory_identity(directory)
    return pins


def _exists(path):
    try:
        Path(path).lstat()
        return True
    except FileNotFoundError:
        return False


def _directory_chain(path):
    """Check existing ancestors without traversing links or reparse points."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute Windows paths required')
    chain = list(reversed((path, *path.parents)))
    for component in chain:
        _ordinary(component, directory=True)
    return path


def _normal_path(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _overlap(first, second):
    first, second = _normal_path(first), _normal_path(second)
    try:
        return os.path.commonpath((first, second)) in (first, second)
    except ValueError:  # Different Windows drives.
        return False


def _check_disjoint(paths):
    for index, first in enumerate(paths):
        for second in paths[index + 1:]:
            if _overlap(first, second):
                raise ValueError('workspace paths must be disjoint')


def _run(command):
    return subprocess.run(command, check=True, capture_output=True, text=True)


def _current_sid():
    result = _run(['whoami', '/user', '/fo', 'csv', '/nh'])
    rows = list(csv.reader(io.StringIO(result.stdout)))
    if len(rows) != 1 or len(rows[0]) < 2 or not _SID.fullmatch(rows[0][1].strip()):
        raise RuntimeError('could not read current Windows user SID')
    return rows[0][1].strip()


def _assert_no_x4_process():
    result = _run(['tasklist', '/fo', 'csv', '/nh'])
    for row in csv.reader(io.StringIO(result.stdout)):
        if row and row[0].strip().casefold() == 'x4.exe':
            raise RuntimeError('X4.exe is already running')


def _restrict_directory(path, sid):
    _run(['icacls', str(path), '/inheritancelevel:r', '/grant:r', f'*{sid}:(OI)(CI)F'])


def _restrict_file(path, sid):
    _run(['icacls', str(path), '/inheritancelevel:r', '/grant:r', f'*{sid}:F'])


def _entry_observation(path):
    """Pin a view entry, accepting only expected game junctions as reparse points."""
    path = Path(path)
    info = path.lstat()
    attributes = getattr(info, 'st_file_attributes', 0)
    is_reparse = bool(type(attributes) is int and attributes & 0x400)
    if is_reparse:
        if not _is_windows() or not stat.S_ISDIR(info.st_mode):
            raise ValueError('unexpected reparse point in game view')
        target = os.path.normcase(os.path.realpath(path))
        target_identity = _ordinary(target, directory=True)
        return ('junction', (info.st_dev, info.st_ino), target,
                target_identity[:2])
    kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file'
    if kind == 'file' and not stat.S_ISREG(info.st_mode):
        raise ValueError('unexpected special file in game view')
    identity = ((info.st_dev, info.st_ino) if kind == 'directory' else fingerprint(info))
    return (kind, identity)


class WindowsFixtureWorkspace:
    """Own only the paths and log handle created for one private fixture run."""

    def __init__(self, game_directory, view_directory, run_directory, *, base_game_only=False):
        self.base_game_only = base_game_only
        self.game_directory = Path(game_directory)
        self.view_directory = Path(view_directory)
        self.run_directory = Path(run_directory)
        self.profile_root = None
        self.profile_directory = None
        self.view = self.view_directory / 'game'
        self.save_dir = None
        self.download_dir = self.run_directory / 'download'
        self.log_path = None
        self._state = 'new'
        self._sid = None
        self._directory_pins = {}
        self._file_pins = {}
        self._overlay_directory_pins = {}
        self._view_entries = {}
        self._manifest = None
        self._initial_settings = {}
        self._log_file = None
        self._log_identity = None
        self._log_snapshot = b''

    def prepare(self):
        """Preflight every path/process before creating private directories."""
        if self._state != 'new':
            raise RuntimeError('workspace prepare is one-shot')
        if not _is_windows():
            raise RuntimeError('Windows fixture workspace requires Windows')
        game_root = _directory_chain(self.game_directory)
        _directory_chain(self.view_directory.parent)
        _directory_chain(self.run_directory.parent)
        documents_root = _directory_chain(profile.documents())
        profile_directory = documents_root / str(PROFILE_ID)

        # The owned container paths must be absent before any filesystem change.
        if _exists(self.view_directory):
            raise FileExistsError(str(self.view_directory))
        if _exists(self.run_directory):
            raise FileExistsError(str(self.run_directory))
        if _exists(profile_directory):
            raise FileExistsError(str(profile_directory))
        _check_disjoint((game_root, documents_root, self.view_directory, self.run_directory))
        game.verify_exe(game_root)
        game_drive = os.path.splitdrive(str(game_root))[0].casefold()
        view_drive = os.path.splitdrive(str(self.view_directory))[0].casefold()
        if game_drive != view_drive:
            raise ValueError('view container must be on the game drive')
        _assert_no_x4_process()
        sid = _current_sid()

        # From here onward, failures intentionally leave owned partial artifacts.
        self._state = 'preparing'
        self.view_directory.mkdir()
        _restrict_directory(self.view_directory, sid)
        self.run_directory.mkdir()
        _restrict_directory(self.run_directory, sid)
        extension_ids = {_EXTENSION_ID}
        if not self.base_game_only:
            extension_ids.update(game.dlc_ids(game_root))
        extension_ids = sorted(extension_ids)
        self.profile_directory = profile.prepare_fresh(PROFILE_ID, extension_ids,
                                                       root=documents_root)
        self.save_dir = self.profile_directory / 'save'
        _restrict_directory(self.profile_directory, sid)
        _restrict_directory(self.save_dir, sid)
        for name in ('config.xml', 'content.xml'):
            _restrict_file(self.profile_directory / name, sid)
        self._initial_settings = {
            self.profile_directory / name: _read_small_file(self.profile_directory / name)
            for name in ('config.xml', 'content.xml')
        }

        view_options = {'base_game_only': True} if self.base_game_only else {}
        game.create_view(game_root, self.view, **view_options)
        self.profile_root = documents_root
        self.log_path = self.profile_directory / 'x4authority.log'
        self._sid = sid
        self._directory_pins = _directory_pins((game_root, self.view, self.run_directory,
                                                self.profile_directory, self.save_dir))
        marker = self.view / '.x4ls-view'
        self._file_pins = {marker: _ordinary(marker, directory=False)}
        self._view_entries = self._capture_view_entries(game_root, allow_fixture=False)
        self._state = 'prepared'
        return self

    def _capture_view_entries(self, game_root, *, allow_fixture):
        expected = {entry.name for entry in game_root.iterdir() if entry.name != 'extensions'}
        expected.update(('extensions', '.x4ls-view'))
        actual = {entry.name for entry in self.view.iterdir()}
        if actual != expected:
            raise ValueError('game view entries differ from source')
        entries = {}
        for entry in self.view.iterdir():
            if entry.name == 'extensions':
                children = {child.name: _entry_observation(child) for child in entry.iterdir()}
                expected_dlcs = set()
                if not self.base_game_only and (game_root / 'extensions').is_dir():
                    expected_dlcs.update(child.name for child in (game_root / 'extensions').glob('ego_dlc_*')
                                         if child.is_dir())
                allowed = expected_dlcs | ({_EXTENSION_ID} if allow_fixture else set())
                if set(children) != allowed:
                    raise ValueError('game DLC links differ from source')
                if allow_fixture:
                    _directory_identity(self.view / 'extensions' / _EXTENSION_ID)
                entries['extensions'] = (_entry_observation(entry), children)
            else:
                entries[entry.name] = _entry_observation(entry)
        return entries

    def _check_view(self):
        current = self._capture_view_entries(self.game_directory,
                                             allow_fixture=self._manifest is not None)
        if current != self._view_entries:
            raise RuntimeError('game view identity changed')

    def _check_pins(self):
        for path, expected in self._directory_pins.items():
            if _directory_identity(path) != expected:
                raise RuntimeError('workspace directory identity changed')
        for path, expected in self._file_pins.items():
            if _ordinary(path, directory=False) != expected:
                raise RuntimeError('workspace file identity changed')
        if _exists(self.download_dir):
            if self.download_dir not in self._directory_pins:
                self._directory_pins[self.download_dir] = _directory_identity(self.download_dir)
            elif _directory_identity(self.download_dir) != self._directory_pins[self.download_dir]:
                raise RuntimeError('download directory identity changed')
        self._check_view()

    def register_overlay(self, hashes):
        """Validate the installed fixture manifest once and pin its files/save."""
        if self._state != 'prepared' or self._manifest is not None or type(hashes) is not dict:
            raise RuntimeError('prepared workspace and one overlay manifest required')
        if _exists(self.log_path):
            raise ValueError('unexpected X4 log before process start')
        extension_root = self.view / 'extensions' / _EXTENSION_ID
        manifest = {}
        for relative, expected_hash in hashes.items():
            if (type(relative) is not str or not relative or '\\' in relative or
                    relative.startswith('/') or any(part in ('', '.', '..') for part in relative.split('/')) or
                    type(expected_hash) is not str or not re.fullmatch(r'[0-9a-f]{64}', expected_hash)):
                raise ValueError('invalid fixture overlay manifest')
            path = extension_root.joinpath(*relative.split('/'))
            identity = _ordinary(path, directory=False)
            if identity[2] > 8 << 20:
                raise ValueError('fixture overlay file exceeds its size bound')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected_hash:
                raise ValueError('fixture overlay file differs from manifest')
            manifest[path] = (identity, expected_hash)
        config = extension_root / 'ui' / 'config.lua'
        if config not in manifest:
            raise ValueError('fixture config is missing from overlay manifest')
        save_files = list(self.save_dir.iterdir())
        if len(save_files) != 1 or save_files[0].name != _SAVE_NAME:
            raise ValueError('exactly one staged fixture save is required')
        save_identity = _ordinary(save_files[0], directory=False)
        overlay_directory_pins = self._capture_fixture_tree(extension_root, manifest)
        view_entries = self._capture_view_entries(self.game_directory, allow_fixture=True)
        old_extensions, current_extensions = self._view_entries['extensions'], view_entries['extensions']
        if old_extensions[0] != current_extensions[0]:
            raise ValueError('pre-existing extension directory changed')
        for name, old_entry in old_extensions[1].items():
            if current_extensions[1].get(name) != old_entry:
                raise ValueError('pre-existing game extension changed')
        for name, old_entry in self._view_entries.items():
            if name != 'extensions' and view_entries.get(name) != old_entry:
                raise ValueError('pre-existing game view entry changed')
        for path, expected in self._initial_settings.items():
            if _read_small_file(path) != expected:
                raise ValueError('profile settings changed before launch')
        self._file_pins[save_files[0]] = save_identity
        self._overlay_directory_pins = overlay_directory_pins
        self._manifest = manifest
        self._view_entries = view_entries
        self._state = 'registered'
        self.guard()
        return True

    def guard(self):
        """Check sampled directory, view, manifest, and staged-save identities."""
        if self._state not in ('prepared', 'registered'):
            raise RuntimeError('workspace is not guardable')
        self._check_pins()
        if self._manifest is not None:
            current_directories = self._capture_fixture_tree(
                self.view / 'extensions' / _EXTENSION_ID, self._manifest)
            if current_directories != self._overlay_directory_pins:
                raise RuntimeError('fixture overlay tree changed')
            for path, expected in self._overlay_directory_pins.items():
                if _directory_identity(path) != expected:
                    raise RuntimeError('fixture overlay directory identity changed')
            for path, (expected_identity, _) in self._manifest.items():
                if _ordinary(path, directory=False) != expected_identity:
                    raise RuntimeError('fixture overlay file identity changed')
        return True

    def read_log(self):
        """Return one bounded complete-prefix snapshot from the retained log handle."""
        if self._state not in ('prepared', 'registered'):
            raise RuntimeError('workspace log is unavailable')
        self.guard()
        if self._log_file is None:
            try:
                path_info = self.log_path.lstat()
                _ordinary(self.log_path, directory=False)
            except FileNotFoundError:
                return self._log_snapshot
            self._log_file = self.log_path.open('rb', buffering=0)
            descriptor_info = os.fstat(self._log_file.fileno())
            opened_path_info = self.log_path.lstat()
            if (not stat.S_ISREG(descriptor_info.st_mode) or
                    _file_identity(path_info) != _file_identity(descriptor_info) or
                    _file_identity(opened_path_info) != _file_identity(descriptor_info)):
                self._log_file.close()
                self._log_file = None
                raise RuntimeError('X4 log path changed while opening')
            self._log_identity = _file_identity(descriptor_info)

        _ordinary(self.log_path, directory=False)
        path_info = self.log_path.lstat()
        descriptor_before = os.fstat(self._log_file.fileno())
        if (not stat.S_ISREG(path_info.st_mode) or not stat.S_ISREG(descriptor_before.st_mode) or
                _file_identity(path_info) != self._log_identity or
                _file_identity(descriptor_before) != self._log_identity or
                descriptor_before.st_size < len(self._log_snapshot) or
                descriptor_before.st_size > MAX_LOG_BYTES):
            raise RuntimeError('X4 log was replaced, shrunk, or exceeded its bound')
        self._log_file.seek(0)
        remaining = descriptor_before.st_size
        chunks = []
        while remaining:
            block = self._log_file.read(remaining)
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        snapshot = b''.join(chunks)
        descriptor_after = os.fstat(self._log_file.fileno())
        _ordinary(self.log_path, directory=False)
        path_after = self.log_path.lstat()
        if (len(snapshot) != descriptor_before.st_size or
                descriptor_after.st_size < descriptor_before.st_size or
                _file_identity(descriptor_after) != self._log_identity or
                _file_identity(path_after) != self._log_identity or
                not snapshot.startswith(self._log_snapshot)):
            raise RuntimeError('X4 log changed or shrunk while reading')
        if snapshot == self._log_snapshot:
            return self._log_snapshot
        self._log_snapshot = snapshot
        return snapshot

    def _capture_fixture_tree(self, extension_root, manifest):
        _directory_identity(extension_root)
        expected_files = {path.relative_to(extension_root).as_posix() for path in manifest}
        expected_directories = set()
        for relative in expected_files:
            parts = relative.split('/')[:-1]
            for length in range(1, len(parts) + 1):
                expected_directories.add('/'.join(parts[:length]))
        found_files, found_directories = set(), set()

        def visit(directory, prefix=''):
            for child in directory.iterdir():
                relative = f'{prefix}/{child.name}' if prefix else child.name
                info = child.lstat()
                attributes = getattr(info, 'st_file_attributes', 0)
                if type(attributes) is int and attributes & 0x400:
                    raise ValueError('fixture overlay reparse point not permitted')
                if stat.S_ISDIR(info.st_mode):
                    found_directories.add(relative)
                    visit(child, relative)
                elif stat.S_ISREG(info.st_mode):
                    found_files.add(relative)
                else:
                    raise ValueError('unexpected fixture overlay file type')

        visit(extension_root)
        if found_files != expected_files or found_directories != expected_directories:
            raise ValueError('fixture overlay tree differs from manifest')
        pins = {extension_root: _directory_identity(extension_root)}
        for relative in expected_directories:
            directory = extension_root.joinpath(*relative.split('/'))
            pins[directory] = _directory_identity(directory)
        return pins

    def redact_config(self):
        """Remove only the registered, still-pinned ticket-bearing config.lua.

        The caller must invoke this only after X4 never started or its owned
        process cleanup was confirmed. This method cannot prove that condition.
        """
        if self._state == 'prepared':
            return False
        if self._state != 'registered':
            raise RuntimeError('registered workspace required')
        config = self.view / 'extensions' / _EXTENSION_ID / 'ui' / 'config.lua'
        expected = self._manifest.get(config)
        if expected is None:
            raise RuntimeError('pinned fixture config missing')
        self.guard()
        if _ordinary(config, directory=False) != expected[0]:
            raise RuntimeError('fixture config identity changed')
        config.unlink()
        self._manifest.pop(config)
        self._state = 'redacted'
        return True

    def close(self):
        """Close only this workspace's retained log handle; preserve all artifacts."""
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
        if self._state != 'closed':
            self._state = 'closed'
