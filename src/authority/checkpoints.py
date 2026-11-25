"""Private checkpoint storage for completed X4 saves; it does not save or load X4.

The caller chooses an existing, separate, private directory and provides a finished
save. A committed checkpoint is immutable to this module. CURRENT is the publish
point; CONFIRMED separately records the last checkpoint explicitly confirmed by
the controller. Neither pointer adopts orphaned directories. A confirmed checkpoint
is not proof that no simulation occurred afterwards. Only one writer may use a
store at once. Readers can use it concurrently.
"""
from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
import zlib
from xml.parsers import expat

MAX_COMPRESSED = 512 * 1024 * 1024
MAX_XML = 2 * 1024 * 1024 * 1024
CHUNK = 1024 * 1024
_ID = re.compile(r'checkpoint-[0-9a-f]{32}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_MARKER = re.compile(r'[0-9a-f]{32}\Z')


class CheckpointError(ValueError):
    """The checkpoint is invalid or the caller supplied an unsafe path."""


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    path: Path
    sha256: str
    size: int
    marker: str | None = None


def _read_regular(path, limit):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CheckpointError('expected a regular file')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise CheckpointError('metadata too large')
        return data
    finally:
        if fd >= 0:
            os.close(fd)


def _json(data):
    try:
        return json.loads(data.decode('ascii'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CheckpointError('invalid metadata') from exc


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _validate_save(path):
    """Read every compressed byte, validate gzip CRC/trailer and complete XML."""
    parser = expat.ParserCreate()
    root = []
    def start(name, _attrs):
        if not root:
            root.append(name)
    def reject(*_args):
        raise CheckpointError('DOCTYPE and external entities are not supported')
    parser.StartElementHandler = start
    parser.StartDoctypeDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    total = 0
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as raw:
            if not stat.S_ISREG(os.fstat(raw.fileno()).st_mode):
                raise CheckpointError('save is not a regular file')
            with gzip.GzipFile(fileobj=raw, mode='rb') as stream:
                while True:
                    block = stream.read(CHUNK)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_XML:
                        raise CheckpointError('decompressed save exceeds limit')
                    parser.Parse(block, False)
                parser.Parse(b'', True)
    except (OSError, EOFError, expat.ExpatError, zlib.error) as exc:
        raise CheckpointError('incomplete or invalid gzip/XML save') from exc
    if root != ['savegame']:
        raise CheckpointError('unexpected save XML root')


def _file_digest(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise CheckpointError('save is not a regular file')
        digest = hashlib.sha256()
        size = 0
        while True:
            block = stream.read(CHUNK)
            if not block:
                break
            size += len(block)
            if size > MAX_COMPRESSED:
                raise CheckpointError('compressed save exceeds limit')
            digest.update(block)
        if not size:
            raise CheckpointError('empty save')
        return digest.hexdigest(), size


def _sync_dir(path):
    if os.name == 'nt':
        # Windows has no directory fsync, and os.open cannot open a directory
        # there. The files are fsynced before each rename; the rename itself is
        # left to the NTFS metadata journal.
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class CheckpointStore:
    """Store immutable generations under an existing caller-selected directory.

    This is a single-writer store, not an interprocess lock. Concurrent readers
    observe an old or new complete checkpoint. It never falls back to an orphan
    if CURRENT is corrupt: callers can explicitly verify a known older ID via get.
    """
    def __init__(self, directory):
        directory = Path(directory)
        if directory.is_symlink() or not directory.is_dir():
            raise CheckpointError('store must be an existing non-symlink directory')
        self.directory = directory.resolve(strict=True)

    def publish(self, source, *, marker=None):
        """Publish a save and optionally bind the caller's marker assertion.

        Metadata alone does not prove that X4 wrote the marker into the save.
        That requires an independently observed native load.
        If an error occurs after replacing CURRENT, the new pointer may already
        be visible even though its directory update was not confirmed durable.
        """
        if marker is not None and (not isinstance(marker, str) or not _MARKER.fullmatch(marker)):
            raise CheckpointError('invalid checkpoint marker')
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise CheckpointError('source must be an existing non-symlink file')
        if source.resolve().is_relative_to(self.directory):
            raise CheckpointError('source must be outside the checkpoint directory')
        checkpoint_id = 'checkpoint-' + uuid.uuid4().hex
        stage = Path(tempfile.mkdtemp(prefix='.staging-', dir=self.directory))
        pointer_temp = None
        try:
            target = stage / 'save.xml.gz'
            fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as incoming, target.open('xb') as outgoing:
                before = os.fstat(incoming.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise CheckpointError('source is not a regular file')
                digest, size = hashlib.sha256(), 0
                while True:
                    block = incoming.read(CHUNK)
                    if not block:
                        break
                    size += len(block)
                    if size > MAX_COMPRESSED:
                        raise CheckpointError('compressed save exceeds limit')
                    digest.update(block)
                    outgoing.write(block)
                after = os.fstat(incoming.fileno())
                if not size or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise CheckpointError('source was empty or changed during copy')
                outgoing.flush()
                os.fsync(outgoing.fileno())
            _validate_save(target)
            manifest_value = {'version': 1, 'id': checkpoint_id,
                              'sha256': digest.hexdigest(), 'size': size}
            if marker is not None:
                manifest_value.update(version=2, marker=marker)
            metadata = _canonical(manifest_value)
            with (stage / 'manifest.json').open('xb') as manifest:
                manifest.write(metadata)
                manifest.flush()
                os.fsync(manifest.fileno())
            _sync_dir(stage)
            os.rename(stage, self.directory / checkpoint_id)
            _sync_dir(self.directory)
            pointer = _canonical({'version': 1, 'id': checkpoint_id,
                                  'manifest_sha256': hashlib.sha256(metadata).hexdigest()})
            fd, name = tempfile.mkstemp(prefix='.current-', dir=self.directory)
            pointer_temp = Path(name)
            with os.fdopen(fd, 'wb') as file:
                file.write(pointer)
                file.flush()
                os.fsync(file.fileno())
            os.replace(pointer_temp, self.directory / 'CURRENT')
            pointer_temp = None
            _sync_dir(self.directory)
            return Checkpoint(checkpoint_id, self.directory / checkpoint_id / 'save.xml.gz', digest.hexdigest(), size, marker)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
            if pointer_temp is not None:
                pointer_temp.unlink(missing_ok=True)

    def get(self, checkpoint_id, *, manifest_sha256=None):
        if not isinstance(checkpoint_id, str) or not _ID.fullmatch(checkpoint_id):
            raise CheckpointError('invalid checkpoint ID')
        folder = self.directory / checkpoint_id
        if folder.is_symlink() or not folder.is_dir():
            raise CheckpointError('checkpoint directory missing or invalid')
        metadata = _read_regular(folder / 'manifest.json', 4096)
        if manifest_sha256 is not None and hashlib.sha256(metadata).hexdigest() != manifest_sha256:
            raise CheckpointError('checkpoint manifest does not match CURRENT')
        value = _json(metadata)
        if not isinstance(value, dict):
            raise CheckpointError('invalid checkpoint manifest')
        version = value.get('version')
        fields = {'version', 'id', 'sha256', 'size'}
        if version == 2:
            fields.add('marker')
        if (set(value) != fields or type(version) is not int or version not in (1, 2)
                or (version == 2 and (not isinstance(value['marker'], str) or not _MARKER.fullmatch(value['marker'])))
                or value['id'] != checkpoint_id
                or not isinstance(value['sha256'], str) or not _HASH.fullmatch(value['sha256'])
                or type(value['size']) is not int or not 0 < value['size'] <= MAX_COMPRESSED):
            raise CheckpointError('invalid checkpoint manifest')
        save = folder / 'save.xml.gz'
        digest, size = _file_digest(save)
        if (digest, size) != (value['sha256'], value['size']):
            raise CheckpointError('checkpoint content does not match manifest')
        _validate_save(save)
        return Checkpoint(checkpoint_id, save, digest, size, value.get('marker'))

    def _pointed_checkpoint(self, contents):
        pointer = _json(contents)
        if (not isinstance(pointer, dict) or set(pointer) != {'version', 'id', 'manifest_sha256'}
                or type(pointer['version']) is not int or pointer['version'] != 1
                or not isinstance(pointer['manifest_sha256'], str) or not _HASH.fullmatch(pointer['manifest_sha256'])):
            raise CheckpointError('invalid checkpoint pointer')
        return self.get(pointer['id'], manifest_sha256=pointer['manifest_sha256'])

    def latest(self):
        return self._pointed_checkpoint(_read_regular(self.directory / 'CURRENT', 4096))

    def confirm(self, checkpoint):
        """Durably record an explicitly controller-confirmed CURRENT checkpoint.

        The caller must first obtain confirmation from the controller. The store
        cannot verify that assertion. Single-writer serialization also covers
        publication and confirmation; a stale generation is rejected. On an
        error after replacement the pointer may already be visible, so callers
        must fault the session rather than infer that the old pointer survived.
        """
        if not isinstance(checkpoint, Checkpoint):
            raise CheckpointError('expected a checkpoint')
        pointer = _read_regular(self.directory / 'CURRENT', 4096)
        if self._pointed_checkpoint(pointer) != checkpoint:
            raise CheckpointError('only the current checkpoint can be confirmed')
        fd, name = tempfile.mkstemp(prefix='.confirmed-', dir=self.directory)
        temp = Path(name)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(pointer)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.directory / 'CONFIRMED')
            temp = None
            _sync_dir(self.directory)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def latest_confirmed(self):
        """Verify the separately confirmed checkpoint; never fall back to CURRENT."""
        return self._pointed_checkpoint(_read_regular(self.directory / 'CONFIRMED', 4096))

    def latest_marked_confirmed(self):
        """Return a confirmed checkpoint with a marker assertion in its manifest.

        This validates metadata and bytes; it does not prove the marker exists in
        the X4 save or that an X4 process loaded them. Rejects legacy saves.
        """
        checkpoint = self.latest_confirmed()
        if checkpoint.marker is None:
            raise CheckpointError('confirmed checkpoint has no marker')
        return checkpoint
