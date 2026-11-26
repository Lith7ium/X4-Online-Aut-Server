"""Bounded requests and receipts for the separate Company combat stream."""
import hmac
import json
import math
import re

from .load_messages import _pairs, _reject_constant

MAX_BATCH = 32
MAX_COUNTER = 2**53 - 1
MAX_FRAME = 8192
BINDING_FIELDS = ('generation', 'registry_epoch', 'shooter_index')
_TOKEN = re.compile(r'[0-9a-f]{32}')
_TICKET = re.compile(r'[0-9a-f]{64}')
_IMPACT_FIELDS = {'id', 'target_index', 'amount'}


def derive_combat_ticket(motion_ticket):
    """Give combat a credential that cannot be presented as a movement ticket."""
    if type(motion_ticket) is not str or not _TICKET.fullmatch(motion_ticket):
        raise ValueError('invalid Company movement ticket')
    return hmac.digest(bytes.fromhex(motion_ticket), b'X4 Authority Company Combat v1', 'sha256').hex()


def encode_frame(message):
    data = json.dumps(message, allow_nan=False, separators=(',', ':')).encode('ascii')
    if len(data) > MAX_FRAME:
        raise ValueError('Company combat frame too large')
    return data + b'\n'


def read_frame(connection, buffer):
    """Read at most one frame, preserving partial input and any following bytes."""
    newline = buffer.find(b'\n')
    if newline < 0:
        try:
            block = connection.recv(MAX_FRAME + 1 - len(buffer))
        except BlockingIOError:
            return None
        if not block:
            raise ConnectionError('Company combat stream closed')
        buffer.extend(block)
        newline = buffer.find(b'\n')
        if newline < 0:
            if len(buffer) > MAX_FRAME:
                raise ValueError('Company combat frame too large')
            return None
    message = json.loads(bytes(buffer[:newline]).decode('ascii'), object_pairs_hook=_pairs,
                         parse_constant=_reject_constant)
    del buffer[:newline + 1]
    if type(message) is not dict:
        raise ValueError('Company combat message object required')
    return message


def number(value, minimum=0):
    return type(value) in (int, float) and minimum <= value <= MAX_COUNTER and math.isfinite(value)


def _index(value):
    return type(value) is int and 1 <= value <= 200000


def validate_impacts(impacts, shooter_index):
    if type(impacts) is not list or not 1 <= len(impacts) <= MAX_BATCH:
        raise ValueError('invalid Company impact batch size')
    previous = None
    for impact in impacts:
        if type(impact) is not dict or impact.keys() != _IMPACT_FIELDS:
            raise ValueError('invalid Company impact fields')
        sequence, target, amount = impact['id'], impact['target_index'], impact['amount']
        if (type(sequence) is not int or not 1 <= sequence <= MAX_COUNTER
                or (previous is not None and sequence != previous + 1)
                or not _index(target) or target == shooter_index or not number(amount) or amount == 0):
            raise ValueError('invalid Company impact id, target or amount')
        previous = sequence
    return impacts


def decode_request(frame):
    if frame.keys() != {'kind', 'ticket', 'impacts', *BINDING_FIELDS} or frame['kind'] != 'combat_impacts':
        raise ValueError('invalid Company combat request')
    generation, epoch, shooter_index = (frame[key] for key in BINDING_FIELDS)
    ticket = frame['ticket']
    if (type(ticket) is not str or not _TICKET.fullmatch(ticket)
            or any(type(token) is not str or not _TOKEN.fullmatch(token) for token in (generation, epoch))
            or not _index(shooter_index)):
        raise ValueError('invalid Company combat binding')
    return ticket, (generation, epoch, shooter_index), validate_impacts(frame['impacts'], shooter_index)


def decode_receipts(frame, binding, impacts):
    if (frame.keys() != {'kind', 'receipts', *BINDING_FIELDS} or frame['kind'] != 'combat_receipts'
            or tuple(frame[key] for key in BINDING_FIELDS) != binding or type(frame['shooter_index']) is not int
            or type(frame['receipts']) is not list or len(frame['receipts']) != len(impacts)):
        raise ValueError('invalid Company combat receipt frame')
    previous_order = 0
    for receipt, impact in zip(frame['receipts'], impacts):
        if (type(receipt) is not dict or receipt.keys() != {*_IMPACT_FIELDS, 'status', 'shield', 'hull', 'order'}
                or type(receipt['id']) is not int or type(receipt['target_index']) is not int
                or not number(receipt['amount']) or any(receipt[key] != impact[key] for key in _IMPACT_FIELDS)
                or type(receipt['order']) is not int or not previous_order < receipt['order'] <= MAX_COUNTER):
            raise ValueError('invalid Company combat impact receipt')
        if receipt['status'] == 'confirmed':
            if not number(receipt['shield']) or not number(receipt['hull']):
                raise ValueError('invalid Company combat health receipt')
        elif receipt['status'] not in ('rejected', 'unknown') or any(
                receipt[key] is not None for key in ('shield', 'hull')):
            raise ValueError('invalid Company combat status receipt')
        previous_order = receipt['order']
    return frame['receipts']
