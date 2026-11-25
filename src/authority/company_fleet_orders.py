"""Portable sector destinations and native acceptance receipts for Company ships."""

import re


_ORDER_FIELDS = {'order_id', 'ship_index', 'ship_code', 'sector', 'x', 'y', 'z'}
_MAX_SECTOR_COORDINATE = 10_000_000


def _matches(value, expression):
    return type(value) is str and re.fullmatch(expression, value) is not None


def fleet_order(value):
    if type(value) is not dict or value.keys() != _ORDER_FIELDS:
        raise ValueError('invalid Company fleet order fields')
    if (not _matches(value['order_id'], r'[0-9a-f]{32}')
            or type(value['ship_index']) is not int or not 1 <= value['ship_index'] <= 200_000
            or not _matches(value['ship_code'], r'[A-Z0-9]{3}-[A-Z0-9]{3}')
            or not _matches(value['sector'], r'[A-Za-z0-9_]{1,128}')):
        raise ValueError('invalid Company fleet order identity')
    for axis in ('x', 'y', 'z'):
        coordinate = value[axis]
        if type(coordinate) not in (int, float) or not abs(coordinate) <= _MAX_SECTOR_COORDINATE:
            raise ValueError('invalid Company fleet destination')
    return dict(value)


def fleet_receipt(value):
    if type(value) is not dict:
        raise ValueError('invalid Company fleet receipt')
    fields = _ORDER_FIELDS | {'outcome'}
    outcome = value.get('outcome')
    if outcome == 'refused':
        fields.add('reason')
        if not _matches(value.get('reason'), r'[a-z][a-z0-9_]{0,63}'):
            raise ValueError('invalid Company fleet refusal')
    elif outcome not in ('accepted', 'unknown'):
        raise ValueError('invalid Company fleet outcome')
    if value.keys() != fields:
        raise ValueError('invalid Company fleet receipt fields')
    fleet_order({key: value[key] for key in _ORDER_FIELDS})
    return dict(value)
