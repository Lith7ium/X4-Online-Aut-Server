"""Verify native Company purchase audit receipts."""
from decimal import Decimal
import re


TRIALS = {'buy': ('one_unit', 1), 'six-unit': ('six_unit', 6)}
WARE = 'water'
SOURCES = {'ship', 'station', 'player'}
ROLES = {'completed_trade', 'matching_account_or_order_event'}
DEAL_FIELDS = (
    'request_id', 'registry_epoch', 'trial', 'requested_qty', 'quote_unit_ct',
    'quote_game_time', 'complete_game_time', 'deal_buyer_component', 'deal_seller_component',
    'deal_ware', 'transferred_qty', 'destroyed_qty', 'deal_unit_ct', 'deal_nominal_ct',
    'player_before_ct', 'player_after_ct', 'faction_hasown_before', 'faction_hasown_after',
    'faction_before_ct', 'faction_after_ct',
)
SHIP_CARGO_FIELDS = ('ship_ware_before', 'ship_ware_after')
ENTRY_FIELDS = (
    'source', 'time', 'buyer_id', 'seller_id', 'partner_id', 'ware', 'quantity',
    'complete', 'entry_id', 'trade_id', 'eventtype', 'trade_eventtype', 'role',
    'buyer_matches', 'seller_matches', 'money_cents', 'price_cents', 'linked',
)


def _integer(raw, field):
    if not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,19})', raw):
        raise ValueError(f'invalid native {field}: {raw!r}')
    return int(raw)


def _time(raw):
    if not re.fullmatch(r'[0-9]{1,20}(?:\.[0-9]{1,20})?(?:[eE][+-]?[0-9]{1,3})?', raw):
        raise ValueError(f'invalid native game time: {raw!r}')
    return Decimal(raw)


def _bool(raw):
    if raw not in ('0', '1', 'false', 'true'):
        raise ValueError(f'invalid native flag: {raw!r}')
    return raw in ('1', 'true')


def _component(raw):
    if not re.fullmatch(r'0x[0-9a-fA-F]{1,16}', raw) or int(raw, 16) == 0:
        raise ValueError(f'invalid native component: {raw!r}')
    return int(raw, 16)


def _fields(row, names):
    if len(row) != len(names) + 1:
        raise ValueError(f'malformed native {row[0]} receipt: {row!r}')
    return dict(zip(names, row[1:], strict=True))


def _ledger(rows):
    header = _fields(rows[0], ('status', 'reason', 'trade_id', 'start', 'end'))
    header['start'] = None if header['start'] == '-' else header['start']
    header['end'] = None if header['end'] == '-' else header['end']
    for field in ('start', 'end'):
        if header[field] is not None:
            _time(header[field])
    header['trade_id'] = (None if header['trade_id'] == '-'
                          else _integer(header['trade_id'], 'ledger trade ID'))
    if header['status'] not in ('observed', 'unknown'):
        raise ValueError(f"invalid native ledger status: {header['status']!r}")
    header['entries'] = entries = []
    for row in rows[1:]:
        entry = _fields(row, ENTRY_FIELDS)
        _time(entry['time'])
        if entry['source'] not in SOURCES or entry['role'] not in ROLES:
            raise ValueError(f'invalid native ledger source or role: {row!r}')
        for field in ('buyer_id', 'seller_id', 'partner_id'):
            if not re.fullmatch(r'0|[1-9][0-9]{0,19}', entry[field]):
                raise ValueError(f'invalid native {field}: {entry[field]!r}')
            entry[field] = int(entry[field])
        for field in ('quantity', 'entry_id', 'trade_id', 'money_cents', 'price_cents'):
            entry[field] = _integer(entry[field], field)
        for field in ('complete', 'buyer_matches', 'seller_matches', 'linked'):
            entry[field] = _bool(entry[field])
        entries.append(entry)
    if any(sum(entry['source'] == source for entry in entries) > 64 for source in SOURCES):
        raise ValueError('native ledger exceeded a bounded source')
    return header


def _purchase_reason(ledger, buyer, seller, dock_time, completed, quoted_unit, quantity, debit):
    trade_id = ledger['trade_id']
    if ledger['status'] != 'observed' or not trade_id or trade_id <= 0:
        return 'ledger_unavailable', None
    if ledger['start'] is None or ledger['end'] is None:
        return 'ledger_window_missing', None
    start, end = _time(ledger['start']), _time(ledger['end'])
    if not dock_time <= start < completed <= end or end - start > 30:
        return 'ledger_window_disagrees', None
    if any(not start < _time(entry['time']) <= end for entry in ledger['entries']):
        return 'ledger_entry_outside_window', None
    trade_entries = [entry for entry in ledger['entries']
                     if entry['trade_id'] == trade_id and entry['linked']]
    completed_entries = [entry for entry in ledger['entries'] if entry['role'] == 'completed_trade']
    if len(completed_entries) != len({entry['source'] for entry in completed_entries}):
        return 'ledger_completed_trade_ambiguous', None
    if not completed_entries or any(
            not entry['complete'] or entry['eventtype'] != 'trade'
            or not entry['linked'] or entry['trade_id'] != trade_id
            or entry['buyer_id'] != buyer or entry['seller_id'] != seller
            or not entry['buyer_matches'] or not entry['seller_matches']
            or entry['ware'] != WARE or entry['quantity'] != quantity
            for entry in completed_entries):
        return 'ledger_completed_trade_unconfirmed', None

    if any(entry['price_cents'] != quoted_unit for entry in completed_entries):
        return 'ledger_unit_price_disagrees', None

    # A transaction may appear in multiple X4 log sources. Its native entry ID,
    # game time and all transaction fields must agree before collapsing copies.
    purchases = {}
    for entry in trade_entries:
        if entry['eventtype'] != 'trade' or entry['money_cents'] >= 0:
            continue
        if (not entry['complete'] or entry['entry_id'] <= 0
                or entry['buyer_id'] not in (0, buyer) or entry['seller_id'] not in (0, seller)
                or entry['partner_id'] not in (0, buyer, seller)
                or entry['ware'] not in ('', WARE) or entry['quantity'] not in (0, quantity)):
            return 'ledger_purchase_unconfirmed', None
        transaction = tuple(entry[field] for field in ENTRY_FIELDS if field != 'source')
        copies = purchases.setdefault(transaction, {})
        if entry['source'] in copies:
            return 'ledger_purchase_ambiguous', None
        copies[entry['source']] = entry
    if len(purchases) != 1:
        return ('ledger_purchase_missing' if not purchases else 'ledger_purchase_ambiguous'), None
    copies = next(iter(purchases.values()))
    if next(iter(copies.values()))['money_cents'] != -debit:
        return 'ledger_debit_disagrees', None
    return 'verified', list(copies.values())


def purchase_settlement(rows, nonce, epoch, mode, *, require_absolute_cargo=False):
    """Parse unprefixed driver payloads and retain ambiguous economic evidence.

    Malformed sequences or unbound primary receipts raise ValueError. Monetary
    ambiguity produces a JSON-safe result with raw payloads and observed debit.
    """
    trial, quantity = TRIALS[mode]
    records = list(rows)
    parts = [row.split('|') for row in records]
    steps = [part[0] for part in parts]
    opening = ['START', 'DOCK', 'RESULT', 'RESULT', 'DEAL', 'OBSERVED', 'RESULT', 'LEDGER']
    if (not 9 <= len(parts) <= 201 or steps[:8] != opening
            or any(step != 'ENTRY' for step in steps[8:-1])
            or parts[0] != ['START'] or parts[-1] != ['DONE']):
        raise ValueError(f'unexpected native trade sequence: {records!r}')

    _, dock, quote, submitted, raw_deal, observed, duplicate, *_ = parts
    if len(dock) != 7 or dock[1:3] != [nonce, 'READY']:
        raise ValueError(f'unbound native docking receipt: {dock!r}')
    # READY has ship, station, external bay and later observation time.
    buyer, seller, bay = map(_component, dock[3:6])
    dock_time = _time(dock[6])
    if len({buyer, seller, bay}) != 3:
        raise ValueError(f'overlapping native docking handles: {dock!r}')
    if (len(quote) != 4 or quote[1:3] != [nonce, 'quote']
            or submitted != ['RESULT', nonce, 'submitted', quote[3]]
            or duplicate != ['RESULT', nonce, 'already_attempted', '0']):
        raise ValueError(f'unbound native quote, submission or duplicate receipt: {records[:7]!r}')
    quoted_unit = _integer(quote[3], 'quoted price')
    if quoted_unit <= 0:
        raise ValueError('nonpositive native quote')

    has_absolute_cargo = len(raw_deal) == 1 + len(DEAL_FIELDS) + len(SHIP_CARGO_FIELDS)
    if require_absolute_cargo and not has_absolute_cargo:
        raise ValueError('missing native ship cargo counts in deal')
    deal = _fields(raw_deal, DEAL_FIELDS + (SHIP_CARGO_FIELDS if has_absolute_cargo else ()))
    for field in SHIP_CARGO_FIELDS if has_absolute_cargo else ():
        deal[field] = _integer(deal[field], field)
    if (deal['request_id'] != nonce or deal['registry_epoch'] != epoch or deal['trial'] != trial
            or deal['deal_ware'] != WARE
            or _component(deal['deal_buyer_component']) != buyer
            or _component(deal['deal_seller_component']) != seller):
        raise ValueError(f'unbound native deal: {raw_deal!r}')
    quoted_at = _time(deal['quote_game_time'])
    completed_at = _time(deal['complete_game_time'])
    if not dock_time <= quoted_at < completed_at:
        raise ValueError(f'native deal time is out of sequence: {raw_deal!r}')
    for field in ('requested_qty', 'quote_unit_ct', 'transferred_qty', 'destroyed_qty',
                  'deal_unit_ct', 'deal_nominal_ct', 'player_before_ct', 'player_after_ct',
                  'faction_before_ct', 'faction_after_ct'):
        deal[field] = _integer(deal[field], field)
    for field in ('faction_hasown_before', 'faction_hasown_after'):
        deal[field] = _bool(deal[field])
    if deal['requested_qty'] != quantity or deal['quote_unit_ct'] != quoted_unit:
        raise ValueError(f'native deal does not match the requested quote: {raw_deal!r}')

    if len(observed) != 10 or observed[1:3] != [nonce, quote[3]]:
        raise ValueError(f'unbound native observation: {observed!r}')
    ship_cargo, station_cargo, ship_money, station_money, player_money = (
        _integer(value, 'observed delta') for value in observed[3:8]
    )
    docked, parked = map(_bool, observed[8:])
    ledger = _ledger(parts[7:-1])
    nominal = deal['deal_unit_ct'] * deal['transferred_qty']
    debit = deal['player_before_ct'] - deal['player_after_ct']
    faction_debit = deal['faction_before_ct'] - deal['faction_after_ct']
    if not docked or not parked:
        reason = 'docking_or_avatar_disagrees'
    elif deal['transferred_qty'] != quantity or deal['destroyed_qty'] != 0 or ship_cargo != quantity:
        reason = 'cargo_or_transfer_disagrees'
    elif has_absolute_cargo and (deal['ship_ware_before'] < 0 or deal['ship_ware_after'] < 0
            or deal['ship_ware_after'] - deal['ship_ware_before'] != ship_cargo):
        reason = 'ship_cargo_counts_disagree'
    elif deal['deal_unit_ct'] != quoted_unit:
        reason = 'accepted_unit_price_disagrees'
    elif deal['deal_nominal_ct'] != nominal:
        reason = 'deal_nominal_disagrees'
    elif (not deal['faction_hasown_before'] or not deal['faction_hasown_after']
          or deal['player_before_ct'] != deal['faction_before_ct']
          or deal['player_after_ct'] != deal['faction_after_ct']
          or deal['player_after_ct'] < 0 or debit <= 0 or player_money != -debit):
        reason = 'account_balances_disagree'
    else:
        reason = None

    purchase = None
    if reason is None:
        reason, purchase = _purchase_reason(ledger, buyer, seller, dock_time, completed_at, quoted_unit, quantity, debit)
    verified = reason == 'verified'
    result = {
        'verified': verified, 'reason': reason,
        'price_cents': quoted_unit, 'quoted_total_cents': quantity * quoted_unit,
        'effective_unit_cents': deal['deal_unit_ct'], 'nominal_total_cents': nominal,
        'settled_total_cents': debit if verified else None,
        'settled_minus_nominal_cents': debit - nominal if verified else None,
        'ship_cargo_delta': ship_cargo, 'station_cargo_delta': station_cargo,
        'ship_money_delta_cents': ship_money, 'station_money_delta_cents': station_money,
        'company_money_delta_cents': player_money, 'observed_player_debit_cents': debit,
        'observed_faction_debit_cents': faction_debit,
        'buyer_component': dock[3], 'seller_component': dock[4], 'bay_component': dock[5],
        'dock_game_time': dock[6], 'deal': deal, 'ledger': ledger,
        'matched_purchase_entries': purchase, 'records': records,
    }

    if has_absolute_cargo:
        result['ship_ware_units_after'] = deal['ship_ware_after'] if verified else None
    return result


def six_unit_settlement(rows, nonce, epoch):
    """Read historical six-unit records that predate absolute cargo fields."""
    return purchase_settlement(rows, nonce, epoch, 'six-unit')
