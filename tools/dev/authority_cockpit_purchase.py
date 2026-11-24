"""Import a private stock cockpit intent and translate native trade evidence."""
import hashlib
import json
import re

from authority_player_dock_flight import REGISTRY_EPOCH, SHIP_INDEX, SOURCE_DIGEST
from authority_player_dock_observation import verify_player_flight
from authority_trade_settlement import _component, _integer, _time, purchase_settlement
from authority.checkpoints import _read_regular
from authority.load_messages import _pairs, _reject_constant


CHECKPOINT_ID = 'checkpoint-12aba53907ec43a6becfc7dde919d309'
MAX_RESULT = 1 << 20
MAX_PRICE = 2147483647
MAX_EXACT = 9007199254740991
PURCHASE = {'sector': 'cluster_01_sector001_macro', 'station_code': 'BIM-169',
            'ship_code': 'WRN-302', 'ware': 'water', 'quantity': 6}
SCOPES = {
    'private stock cockpit flight and docking diagnostic': False,
    'private assigned-berth cockpit diagnostic': True,
}
REQUIRED_METADATA = {
    'success': True, 'checkpoint_id': CHECKPOINT_ID, 'sha256': SOURCE_DIGEST,
    'load_requested': True, 'marker_after_game_loaded': True, 'pause_observed': True,
    'exact_loaded_file_proven': False, 'zero_advance_proven': False, 'admitted': False,
    'owned_processes_remaining': [], 'ship_index': SHIP_INDEX, 'session_listener': False,
    'saved': False, 'published': False,
}


def _nonce(value):
    return (type(value) is str and re.fullmatch(r'[0-9a-f]{32}', value) is not None
            and value != '0' * 32)


def _is_amount(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def load_cockpit_capture(path):
    """Return (intent, capture nonce, result SHA-256) from the same bounded read."""
    data = _read_regular(path, MAX_RESULT)
    try:
        result = json.loads(data, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('invalid private cockpit result JSON') from error
    if (type(result) is not dict or type(result.get('scope')) is not str
            or result['scope'] not in SCOPES or not _nonce(result.get('trial_nonce'))
            or set(result) - (REQUIRED_METADATA.keys() | {
                    'scope', 'trial_nonce', 'experiment', 'owned_descendant_cleanup'})
            or any(type(result.get(key)) is not type(value) or result.get(key) != value
                   for key, value in REQUIRED_METADATA.items())):
        raise ValueError('cockpit result is not a successful private capture from the pinned source')

    if 'owned_descendant_cleanup' in result:
        cleanup = result['owned_descendant_cleanup']
        if (type(cleanup) is not dict or cleanup.get('refused') != []
                or type(cleanup.get('processes')) is not list
                or any(type(process) is not dict or process.get('outcome') not in ('exited', 'already_exited')
                       for process in cleanup['processes'])):
            raise ValueError('cockpit capture has unconfirmed process cleanup')

    experiment = result.get('experiment')
    records = experiment.get('records') if type(experiment) is dict else None
    if (type(records) is not list or not 1 <= len(records) <= 1024
            or any(type(row) is not str or not row.isascii() or not 1 <= len(row) <= 4096
                   or '\n' in row or '\r' in row or row.startswith('ERROR|') for row in records)):
        raise ValueError('invalid private cockpit observation records')
    try:
        observed = verify_player_flight(records, prepare_berth=SCOPES[result['scope']])
    except (TypeError, KeyError, ArithmeticError, IndexError) as error:
        raise ValueError('malformed private cockpit observation') from error
    if json.dumps(observed, sort_keys=True) != json.dumps(experiment, sort_keys=True):
        raise ValueError('cockpit result metadata differs from the independently verified records')
    price = observed['unit_price_cents']
    if not _is_amount(price, 1, MAX_PRICE):
        raise ValueError('native cockpit unit quote is outside the supported exact range')
    return {**PURCHASE, 'price_cents': price}, result['trial_nonce'], hashlib.sha256(data).hexdigest()


def read_cockpit_purchase(path):
    return load_cockpit_capture(path)[0]


def _refused_receipt(intent, records, nonce):
    if len(records) != 6 or records[0] != 'START' or records[-1] != 'DONE':
        raise ValueError('unexpected native stale-price sequence')
    dock, quote, price_changed, refused = (row.split('|') for row in records[1:-1])
    if len(dock) != 7 or dock[:3] != ['DOCK', nonce, 'READY']:
        raise ValueError('stale-price refusal is missing its native dock')
    if len(set(map(_component, dock[3:6]))) != 3 or _time(dock[6]) <= 0:
        raise ValueError('invalid native dock provenance for stale-price refusal')
    if len(quote) != 4 or quote[:3] != ['RESULT', nonce, 'quote']:
        raise ValueError('stale-price refusal is missing its native quote')
    if len(refused) != 9 or refused[:3] != ['REFUSED', nonce, 'price_changed']:
        raise ValueError('unbound native stale-price refusal')
    requested, current, before_ware, after_ware, before_balance, after_balance = (
        _integer(raw, 'refusal field') for raw in refused[3:]
    )
    quote_unit = _integer(quote[3], 'quoted price')
    if (not _is_amount(quote_unit, 1, MAX_PRICE) or not _is_amount(current, 1, MAX_PRICE)
            or requested != intent['price_cents'] or requested == current
            or price_changed != ['RESULT', nonce, 'price_changed', refused[4]]
            or not _is_amount(before_ware, 0, MAX_PRICE) or before_ware != after_ware
            or not _is_amount(before_balance, 0, MAX_EXACT) or before_balance != after_balance):
        raise ValueError('native stale-price refusal does not establish a clean rejection')
    return {'outcome': 'refused', **intent, 'native_evidence': {
        'reason': 'price_changed', 'quoted_unit_cents': quote_unit, 'current_unit_cents': current,
        'unchanged_water_units': after_ware, 'unchanged_company_balance_cents': after_balance,
        'records': records,
    }}


def cockpit_receipt(intent, records, nonce, epoch):
    """Translate a bound native result only after settlement or clean refusal is proven."""
    if (type(intent) is not dict or set(intent) != PURCHASE.keys() | {'price_cents'}
            or any(type(intent.get(key)) is not type(value) or intent.get(key) != value
                   for key, value in PURCHASE.items())
            or not _is_amount(intent.get('price_cents'), 1, MAX_PRICE)
            or not _nonce(nonce) or epoch != REGISTRY_EPOCH):
        raise ValueError('invalid private cockpit purchase context')
    if (type(records) not in (tuple, list) or not 1 <= len(records) <= 201
            or any(type(row) is not str or not row.isascii() or not 1 <= len(row) <= 4096
                   or '\n' in row or '\r' in row for row in records)):
        raise ValueError('invalid native trade records')
    records = list(records)
    if any(row.startswith('REFUSED|') for row in records):
        return _refused_receipt(intent, records, nonce)
    settlement = purchase_settlement(records, nonce, epoch, 'six-unit', require_absolute_cargo=True)
    if settlement['price_cents'] != intent['price_cents'] or settlement['verified'] is not True:
        raise ValueError(f"native purchase does not confirm the imported quote: {settlement['reason']}")
    debit = settlement['settled_total_cents']
    balance = settlement['deal']['faction_after_ct']
    cargo = settlement['ship_ware_units_after']
    if (not _is_amount(debit, 1, MAX_EXACT) or not _is_amount(balance, 0, MAX_EXACT)
            or not _is_amount(cargo, PURCHASE['quantity'], MAX_PRICE)):
        raise ValueError('native settlement is outside the supported adapter range')
    return {'outcome': 'confirmed', **intent, 'settled_total_cents': debit,
            'company_balance_cents': balance, 'cargo_units': cargo, 'native_evidence': settlement}
