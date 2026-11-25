"""Company trade requests and receipts, from the perspective of the pilot's ship."""

import re

from .load_messages import MAX_INTEGER


_BASKET = frozenset({'sector', 'station_code', 'ware', 'quantity', 'price_cents'})
PURCHASE_FIELDS = _BASKET | {'purchase_id'}
SALE_FIELDS = _BASKET | {'sale_id'}


def _text(value, pattern):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def _integer(value, minimum=1, maximum=MAX_INTEGER):
    return type(value) is int and minimum <= value <= maximum


def _order(value, fields, id_field, kind):
    if (type(value) is not dict or value.keys() != fields
            or not _text(value[id_field], r'[0-9a-f]{32}')
            or not _text(value['sector'], r'[A-Za-z0-9_]{1,128}')
            or not _text(value['station_code'], r'[A-Z0-9]{3}-[A-Z0-9]{3}')
            or not _text(value['ware'], r'[a-z][a-z0-9_]{0,127}')
            or not _integer(value['quantity'], maximum=2**31 - 1)
            or not _integer(value['price_cents'])):
        raise ValueError(f'invalid company {kind}')
    return dict(value)


def purchase_value(value):
    return _order(value, PURCHASE_FIELDS, 'purchase_id', 'purchase')


def sale_value(value):
    return _order(value, SALE_FIELDS, 'sale_id', 'sale')


def _receipt(value, fields, validate_order, kind, amount_field):
    if type(value) is not dict or not fields <= value.keys():
        raise ValueError(f'invalid company {kind} receipt')
    validate_order({name: value[name] for name in fields})
    keys = fields | {'outcome'}
    outcome = value.get('outcome')
    if outcome == 'confirmed':
        keys |= {amount_field, 'company_balance_cents', 'cargo_units', 'ship_code'}
        if (not _integer(value.get(amount_field))
                or not _integer(value.get('company_balance_cents'), minimum=0)
                or not _integer(value.get('cargo_units'), minimum=0)
                or not _text(value.get('ship_code'), r'[A-Z0-9]{3}-[A-Z0-9]{3}')):
            raise ValueError(f'invalid confirmed company {kind} receipt')
    elif outcome == 'refused':
        keys |= {'reason'}
        if not _text(value.get('reason'), r'[a-z][a-z0-9_]{0,63}'):
            raise ValueError(f'invalid company {kind} refusal reason')
        if value['reason'] == 'price_changed':
            keys |= {'current_price_cents'}
            if not _integer(value.get('current_price_cents')) or value['current_price_cents'] == value['price_cents']:
                raise ValueError(f'invalid changed company {kind} quote')
    elif kind == 'purchase' and outcome == 'pilot_reported':
        keys |= {'settled_total_cents'}
        if not _integer(value.get('settled_total_cents')):
            raise ValueError('invalid pilot-reported total')
    elif outcome != 'unknown' and not (kind == 'purchase' and outcome == 'accepted_pending_statement'):
        raise ValueError(f'unknown company {kind} outcome')
    if keys != value.keys():
        raise ValueError(f'unexpected company {kind} receipt fields')
    return dict(value)


def purchase_receipt(value):
    return _receipt(value, PURCHASE_FIELDS, purchase_value, 'purchase', 'settled_total_cents')


def sale_receipt(value):
    """A sale reports the credit X4 applied, which may differ from quantity × unit price."""
    return _receipt(value, SALE_FIELDS, sale_value, 'sale', 'credited_cents')
