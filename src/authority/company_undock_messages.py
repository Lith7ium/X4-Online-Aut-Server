"""Portable berthing identity and request correlation for Company departure."""
import re


_REQUEST_FIELDS = {'request_id', 'sector', 'station_code', 'bay_seed', 'bay_macro', 'dockarea_macro'}
_IDENTIFIERS = {
    'request_id': re.compile(r'[0-9a-f]{32}'),
    'sector': re.compile(r'[A-Za-z0-9_]{1,128}'),
    'station_code': re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}'),
    'bay_macro': re.compile(r'[a-z][a-z0-9_]{0,127}'),
    'dockarea_macro': re.compile(r'[a-z][a-z0-9_]{0,127}'),
    'bay_seed': re.compile(r'-?[1-9][0-9]{0,19}'),
}


def undock_request(value):
    if type(value) is not dict or value.keys() != _REQUEST_FIELDS:
        raise ValueError('invalid company undock request fields')
    for field, pattern in _IDENTIFIERS.items():
        if type(value[field]) is not str or pattern.fullmatch(value[field]) is None:
            raise ValueError(f'invalid company undock {field}')
    # X4 can format the same width of native seed as signed or unsigned text.
    if not -(2**63) <= int(value['bay_seed']) < 2**64:
        raise ValueError('company dock seed outside native range')
    return dict(value)


def undock_receipt(value):
    if type(value) is not dict:
        raise ValueError('invalid company undock receipt')
    outcome = value.get('outcome')
    fields = _REQUEST_FIELDS | {'outcome'}
    if outcome == 'refused':
        fields.add('reason')
        if type(value.get('reason')) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value['reason']):
            raise ValueError('invalid company undock refusal')
    elif outcome not in ('confirmed', 'unknown'):
        raise ValueError('invalid company undock outcome')
    if value.keys() != fields:
        raise ValueError('unexpected company undock receipt fields')
    undock_request({key: value[key] for key in _REQUEST_FIELDS})
    return dict(value)


class UndockWindow:
    """Correlate every terminal response with the request from this admission."""

    def __init__(self):
        self.pending = None
        self._seen = set()

    def receipt(self, value):
        if self.pending is None and value is None:
            return None
        receipt = undock_receipt(value)
        if self.pending is None or any(receipt[key] != expected for key, expected in self.pending.items()):
            raise ValueError('company undock receipt differs from pending departure')
        return receipt

    def next_request(self, value):
        if value is None:
            return None
        request = undock_request(value)
        if request['request_id'] in self._seen or len(self._seen) >= 4096:
            raise ValueError('duplicate or exhausted company undock request')
        return request

    def advance(self, request):
        self.pending = dict(request) if request is not None else None
        if request is not None:
            self._seen.add(request['request_id'])
