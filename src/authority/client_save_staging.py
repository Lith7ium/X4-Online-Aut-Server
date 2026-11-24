"""Copy one offered save to a new X4 slot in caller-owned directories.

The caller must fence other writers and X4's save-list scan while the copy is
in progress. This module does not find profiles, load a save, or authenticate a
server. A slot may remain after failure during fenced fallback copying or
after publication if subsequent verification fails.
"""
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from .load_messages import Offer
from .file_fingerprints import fingerprint, same_path_and_descriptor

_SLOT = re.compile(r'save_[0-9]{3}\.xml\.gz\Z')
_CHUNK_SIZE = 1024 * 1024
_WINDOWS = os.name == 'nt'
_LINK_UNAVAILABLE = {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EPERM, errno.EMLINK}


def _write_all(outgoing, block, pump):
    remaining = memoryview(block)
    while remaining:
        pump()
        written = outgoing.write(remaining)
        pump()
        if written is None or written <= 0:
            raise OSError('incomplete save copy')
        remaining = remaining[written:]


def _copy_verified(source, outgoing, offer, pump):
    pump()
    before_path = os.lstat(source)
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError('downloaded save is not a regular file')
    source_flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_CLOEXEC', 0)
                    | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    source_fd = os.open(source, source_flags)
    try:
        incoming = os.fdopen(source_fd, 'rb', buffering=0)
        source_fd = -1
        with incoming:
            pump()
            before = os.fstat(incoming.fileno())
            pump()
            if not stat.S_ISREG(before.st_mode) or not same_path_and_descriptor(before_path, before):
                raise ValueError('downloaded save changed before copying')
            digest = hashlib.sha256()
            size = 0
            while True:
                pump()
                block = incoming.read(min(_CHUNK_SIZE, offer.size - size + 1))
                pump()
                if not block:
                    break
                size += len(block)
                if size > offer.size:
                    raise ValueError('downloaded save exceeds offer size')
                digest.update(block)
                _write_all(outgoing, block, pump)
            after = os.fstat(incoming.fileno())
            pump()
            after_path = os.lstat(source)
            pump()
            if (not stat.S_ISREG(after.st_mode) or not stat.S_ISREG(after_path.st_mode) or
                    fingerprint(before) != fingerprint(after) or
                    fingerprint(before_path) != fingerprint(after_path) or
                    not same_path_and_descriptor(after_path, after) or
                    size != offer.size or digest.hexdigest() != offer.sha256):
                raise ValueError('downloaded save changed or differs from offer')
            outgoing.flush()
            os.fsync(outgoing.fileno())
            pump()
    finally:
        if source_fd >= 0:
            os.close(source_fd)


def _open_slot(path, flags):
    return os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)


def _publish_unix(part, target, offer, pump):
    try:
        os.link(part, target)
    except OSError as error:
        if error.errno not in _LINK_UNAVAILABLE:
            raise
    else:
        return

    # Keep the exclusively created slot under the caller's existing scan fence.
    pump()
    with open(target, 'xb', buffering=0, opener=_open_slot) as outgoing:
        _copy_verified(part, outgoing, offer, pump)


def copy_offered_save(source, target, offer, *, pump):
    """Copy and verify one offer before publishing it to an absent X4 slot.

    The caller exclusively controls the directories and keeps X4's save-list
    scan fenced. On filesystems without hard links on Unix, the fenced
    exclusive-copy fallback may leave an incomplete target after failure.
    A published slot can remain if later verification fails. The source and
    any existing slot are preserved. The caller must separately verify the
    completed target before requesting an X4 load.

    The owner pump checks the deadline between bounded I/O operations. The
    directory owner guards against path replacement and Windows reparse
    points. Windows behavior has not been validated in a native run.
    """
    if type(offer) is not Offer or not callable(pump):
        raise ValueError('offer and owner pump required')
    source, target = Path(source), Path(target)
    if not _SLOT.fullmatch(target.name):
        raise ValueError('target must be an X4 compressed save slot')

    pump()
    try:
        target.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), target)

    with tempfile.NamedTemporaryFile(mode='wb', buffering=0, dir=target.parent,
                                     prefix='.' + target.name + '.', suffix='.part', delete=False) as outgoing:
        part = Path(outgoing.name)
        try:
            _copy_verified(source, outgoing, offer, pump)
            outgoing.close()
            pump()
            if _WINDOWS:
                os.rename(part, target)  # Windows rename refuses an existing destination.
                return target
            _publish_unix(part, target, offer, pump)
        except BaseException as error:
            # Close before removing the temporary file, including on Windows.
            try:
                outgoing.close()
                part.unlink()
            except OSError as cleanup_error:
                error.add_note(f'could not clean up staging file {part}: {cleanup_error}')
            raise
        part.unlink()
    return target
