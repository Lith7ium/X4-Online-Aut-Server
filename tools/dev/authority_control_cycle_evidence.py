"""Extract independently revalidated identity/time evidence from a checkpoint.

``read_cycle_evidence(store, checkpoint_id, expected_save_name,
previous_game_time=None)`` calls ``store.get`` on the explicit generation and
then streams and parses its entire gzip/XML content again. It returns immutable
``CycleEvidence`` with a Decimal game time, or raises ``EvidenceError``. A
supplied previous time must itself be finite/nonnegative and the new time must
be strictly greater. This proves properties of the saved bytes only: it does not
prove that X4 can load them or that a multiplayer cycle occurred.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import os
import re
from pathlib import Path
import stat
import sys
import zlib
from xml.parsers import expat

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
from authority.checkpoints import CheckpointError, MAX_COMPRESSED, MAX_XML

CHUNK = 64 * 1024
MAX_TOKEN = 4 * 1024 * 1024
MAX_DEPTH = 1024
MAX_VALUE = 1024
_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')


class EvidenceError(ValueError):
    """Checkpoint evidence is invalid or could not be verified."""


@dataclass(frozen=True)
class CycleEvidence:
    checkpoint_id: str
    sha256: str
    size: int
    save_name: str
    game_time: Decimal


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise EvidenceError('invalid game time')
    text = str(value)
    if len(text) > MAX_VALUE or not _NUMBER.fullmatch(text):
        raise EvidenceError('invalid game time')
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise EvidenceError('invalid game time') from exc
    if not parsed.is_finite() or parsed < 0:
        raise EvidenceError('invalid game time')
    return parsed


class _DigestReader:
    def __init__(self, raw):
        self.raw = raw
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, count=-1):
        block = self.raw.read(CHUNK if count < 0 else min(count, CHUNK))
        self.size += len(block)
        if self.size > MAX_COMPRESSED:
            raise EvidenceError('compressed save exceeds limit')
        self.digest.update(block)
        return block


def read_cycle_evidence(store, checkpoint_id, expected_save_name, *, previous_game_time=None):
    if not isinstance(expected_save_name, str) or not expected_save_name or len(expected_save_name) > MAX_VALUE:
        raise EvidenceError('invalid expected save name')
    previous = None if previous_game_time is None else _time(previous_game_time)
    try:
        checkpoint = store.get(checkpoint_id)
        parser = expat.ParserCreate()
        path = []
        seen = set()
        result = {}

        def start(name, attrs):
            path.append(name)
            depth = len(path)
            if depth > MAX_DEPTH:
                raise EvidenceError('XML nesting exceeds limit')
            if depth == 1 and name != 'savegame':
                raise EvidenceError('unexpected save XML root')
            if depth == 3 and path[1] == 'info' and name in ('save', 'game'):
                attr = 'name' if name == 'save' else 'time'
                result[attr] = attrs.get(attr)
            elif depth != 2 or name != 'info':
                return
            if name in seen:
                raise EvidenceError('duplicate save identity metadata')
            seen.add(name)

        def end(_name):
            path.pop()

        def reject(*_args):
            raise EvidenceError('DOCTYPE and external entities are not supported')

        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.StartDoctypeDeclHandler = reject
        parser.ExternalEntityRefHandler = reject
        fd = os.open(checkpoint.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as raw:
            before = os.fstat(raw.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise EvidenceError('save is not a regular file')
            reader = _DigestReader(raw)
            total = 0
            with gzip.GzipFile(fileobj=reader, mode='rb') as decompressed:
                while True:
                    block = decompressed.read(CHUNK)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_XML:
                        raise EvidenceError('decompressed save exceeds limit')
                    parser.Parse(block, False)
                    if total - parser.CurrentByteIndex > MAX_TOKEN:
                        raise EvidenceError('XML token exceeds limit')
                parser.Parse(b'', True)
            after = os.fstat(raw.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise EvidenceError('checkpoint changed while reading')
            if (reader.digest.hexdigest(), reader.size) != (checkpoint.sha256, checkpoint.size):
                raise EvidenceError('checkpoint content changed after store verification')
        if seen != {'info', 'save', 'game'}:
            raise EvidenceError('missing save identity metadata')
        name = result.get('name')
        if name != expected_save_name:
            raise EvidenceError('save name does not match expected name')
        game_time = _time(result.get('time'))
        if previous is not None and game_time <= previous:
            raise EvidenceError('game time did not advance')
        return CycleEvidence(checkpoint.checkpoint_id, checkpoint.sha256, checkpoint.size, name, game_time)
    except EvidenceError:
        raise
    except (CheckpointError, OSError, EOFError, expat.ExpatError, zlib.error) as exc:
        raise EvidenceError('checkpoint evidence could not be verified') from exc
