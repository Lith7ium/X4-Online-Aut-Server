"""Bounded newline-delimited messages on the authenticated company movement stream."""
import json
import re

from .load_messages import MAX_MESSAGE, _pairs, _reject_constant

MAX_PEERS = 16
MAX_INDICES = 256
MAX_COUNTER = 2**53 - 1
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


def validate_view(sector, indices):
    if (type(sector) is not str or not SECTOR.fullmatch(sector) or
            type(indices) is not list or len(indices) > MAX_INDICES or
            any(type(index) is not int or not 1 <= index <= 200000 for index in indices) or
            len(indices) != len(set(indices))):
        raise ValueError('invalid observed sector or ship indices')
