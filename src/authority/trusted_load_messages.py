"""Experimental bounded encoding for cooperative marker-load messages.

This module supplies no transport, peer binding, authentication or freshness.
The receiving connection owner must validate each claim against current state.
"""
import json
import re

from .experimental_client_load import HonestClientLoadClaim
from .load_messages import (MAX_MESSAGE, Offer, Receipt, _pairs, _reject_constant,
                            decode_offer, decode_receipt, encode_offer, encode_receipt)
from .trusted_marker_load_session import MarkerBoundOffer

MOTION_CLAIM_KIND = 'marked_motion_claim'

_TICKET = re.compile(r'[0-9a-f]{64}\Z')
_MARKER = re.compile(r'[0-9a-f]{32}\Z')


def _marker(value):
    if type(value) is not str or not _MARKER.fullmatch(value):
        raise ValueError('invalid checkpoint marker')
    return value


def _encode(kind, marker, payload, **extra):
    obj = {'version': 1, 'kind': kind, 'marker': _marker(marker),
           'payload': json.loads(payload), **extra}
    raw = json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    if len(raw) > MAX_MESSAGE: raise ValueError('message too large')
    return raw


def encode_bound_offer(bound):
    if type(bound) is not MarkerBoundOffer or type(bound.offer) is not Offer:
        raise ValueError('marker-bound offer required')
    return _encode('marked_offer', bound.marker, encode_offer(bound.offer))


def encode_load_claim(claim, *, ship_motion=False):
    if (type(claim) is not HonestClientLoadClaim or type(claim.receipt) is not Receipt
            or claim.load_event_observed is not True or claim.paused_observed is not True):
        raise ValueError('complete cooperative load claim required')
    return _encode(_claim_kind(ship_motion), claim.marker, encode_receipt(claim.receipt),
                   load_event_observed=True, paused_observed=True)


def _decode(raw, kind, extra):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_MESSAGE:
        raise ValueError('invalid message size or type')
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('invalid JSON message') from exc
    if (type(obj) is not dict or set(obj) != {'version', 'kind', 'marker', 'payload'} | set(extra)
            or type(obj['version']) is not int or obj['version'] != 1 or obj['kind'] != kind
            or type(obj['payload']) is not dict):
        raise ValueError('invalid message fields or version')
    _marker(obj['marker'])
    for key in extra:
        if obj[key] is not True: raise ValueError('missing cooperative observation')
    payload = json.dumps(obj['payload'], separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')
    return obj['marker'], payload


def decode_bound_offer(raw):
    marker, payload = _decode(raw, 'marked_offer', ())
    return MarkerBoundOffer(decode_offer(payload), marker)


def _claim_kind(ship_motion):
    if type(ship_motion) is not bool:
        raise ValueError('invalid ship motion claim mode')
    return MOTION_CLAIM_KIND if ship_motion else 'marked_claim'


def decode_load_claim(raw, *, ship_motion=False):
    marker, payload = _decode(raw, _claim_kind(ship_motion), ('load_event_observed', 'paused_observed'))
    return HonestClientLoadClaim(decode_receipt(payload), marker, True, True)


def validate_ship_motion_credential(value, *, index, session_epoch, registry_epoch=None):
    """Return a detached credential for the selected ship and acknowledged session."""
    if (type(value) is not dict or set(value) != {'ticket', 'generation', 'epoch', 'index', 'session_epoch'}
            or type(value['ticket']) is not str or not _TICKET.fullmatch(value['ticket'])
            or any(type(value[key]) is not str or not _MARKER.fullmatch(value[key])
                   for key in ('generation', 'epoch', 'session_epoch'))
            or type(value['index']) is not int or not 1 <= value['index'] <= 200000
            or value['index'] != index or value['session_epoch'] != session_epoch
            or (registry_epoch is not None and value['epoch'] != registry_epoch)):
        raise ValueError('invalid ship motion credential')
    return dict(value)
