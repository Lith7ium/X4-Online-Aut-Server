"""Bounded newline-delimited messages on the authenticated company movement stream."""
import json
import re

from .company_limits import MAX_COCKPIT_PLAYBACK_PILOTS
from .load_messages import MAX_MESSAGE, _pairs, _reject_constant

MAX_PEERS = 16
MAX_INDICES = 256
MAX_COUNTER = 2**53 - 1
BIND_TIMEOUT = 15
# A stopped sampler cannot keep refreshing an old pose.
MAX_SAMPLE_AGE = 3.0
SECTOR = re.compile(r'[A-Za-z0-9_]{1,128}')


def encode_frame(message):
    data = json.dumps(message, allow_nan=False, separators=(',', ':')).encode('ascii')
    if len(data) > MAX_MESSAGE:
        raise ValueError('movement frame exceeds its bound')
    return data + b'\n'


def read_frames(connection, buffer):
    """Yield up to eight complete frames without blocking on a partial message."""
    for _ in range(8):
        newline = buffer.find(b'\n')
        if newline < 0:
            try:
                block = connection.recv(MAX_MESSAGE + 1 - len(buffer))
            except BlockingIOError:
                return
            if not block:
                raise ConnectionError('movement stream closed')
            buffer.extend(block)
            newline = buffer.find(b'\n')
            if newline < 0:
                if len(buffer) > MAX_MESSAGE:
                    raise ValueError('movement frame too large')
                return
        message = json.loads(bytes(buffer[:newline]).decode('ascii'),
                             object_pairs_hook=_pairs, parse_constant=_reject_constant)
        del buffer[:newline + 1]
        if type(message) is not dict:
            raise ValueError('movement message object required')
        yield message


def validate_pilot_roster(message, *, epoch, own_index):
    indices = message.get('ship_indices')
    if (set(message) != {'kind', 'window', 'epoch', 'ship_indices'} or
            message['epoch'] != epoch or type(indices) is not list or len(indices) > MAX_COCKPIT_PLAYBACK_PILOTS or
            any(type(index) is not int or not 1 <= index <= 200000 or index == own_index
                for index in indices) or
            any(left >= right for left, right in zip(indices, indices[1:]))):
        raise ValueError('invalid movement pilot roster')
    return tuple(indices)


def validate_view(sector, indices):
    if (type(sector) is not str or not SECTOR.fullmatch(sector) or
            type(indices) is not list or len(indices) > MAX_INDICES or
            any(type(index) is not int or not 1 <= index <= 200000 for index in indices) or
            len(indices) != len(set(indices))):
        raise ValueError('invalid observed sector or ship indices')


def normalize_views(views, *, own_index=None):
    """Validate a bounded replacement and merge rows for the same sector."""
    if type(views) is not list or len(views) > MAX_INDICES:
        raise ValueError('invalid observed views')
    merged = {}
    for row in views:
        if type(row) is not dict or set(row) != {'sector_macro', 'indices'}:
            raise ValueError('invalid observed view row')
        sector, indices = row['sector_macro'], row['indices']
        validate_view(sector, indices)
        selected = set(indices) - {own_index}
        if selected:
            merged.setdefault(sector, set()).update(selected)
    if len(merged) > 2 or sum(map(len, merged.values())) > MAX_INDICES:
        raise ValueError('observed views exceed their bounds')
    return {sector: frozenset(indices) for sector, indices in merged.items()}


def view_matches(views, index, sector):
    return index in views.get(sector, ())
