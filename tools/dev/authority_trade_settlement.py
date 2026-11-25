"""Verify native Company stock trade audit receipts."""
from decimal import Decimal, ROUND_HALF_UP
import re


TRIALS = {'buy': ('one_unit', 1), 'six-unit': ('six_unit', 6)}
DIAGNOSTIC_WARE = 'water'
_WARE_ID = re.compile(r'[a-z][a-z0-9_]{0,127}')
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


def _journal_reason(ledger, buyer, seller, dock_time, completed, quoted_unit, ware, quantity, expected_money,
                    *, live=False, sell=False):
    trade_id = ledger['trade_id']
    if ledger['status'] != 'observed' or not trade_id or trade_id <= 0:
        return 'ledger_unavailable', None
    if ledger['start'] is None or ledger['end'] is None:
        return 'ledger_window_missing', None
    start, end = _time(ledger['start']), _time(ledger['end'])
    # MD formats READY in milliseconds. Lua can sample the same game tick
    # before that rounded value, while all journal timestamps retain precision.
    same_dock_tick = (Decimal('0') < dock_time - start <= Decimal('0.0005')
                      and start.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP) == dock_time)
    if live:
        # The live provider samples the start before raising the MD Buy event.
        # MD observes binding and quote together, possibly on the next update.
        invalid_start = start > dock_time + Decimal('0.0005')
    else:
        invalid_start = start < dock_time and not same_dock_tick
    if invalid_start or not start < completed <= end or end - start > 30:
        return 'ledger_window_disagrees', None
    if any(not start < _time(entry['time']) <= end for entry in ledger['entries']):
        return 'ledger_entry_outside_window', None
    if live and any(_time(entry['time']) + Decimal('0.0005') < dock_time
                    for entry in ledger['entries'] if entry['linked']):
        return 'ledger_entry_predates_native_binding', None
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
            or entry['ware'] != ware or entry['quantity'] != quantity
            for entry in completed_entries):
        return 'ledger_completed_trade_unconfirmed', None

    if any(entry['price_cents'] != quoted_unit for entry in completed_entries):
        return 'ledger_unit_price_disagrees', None

    if sell:
        ship_entry = next((entry for entry in completed_entries if entry['source'] == 'ship'), None)
        if not ship_entry or ship_entry['entry_id'] <= 0:
            return 'ledger_ship_sale_missing', None
        if ship_entry['money_cents'] != expected_money:
            return 'ledger_credit_disagrees', None
        return 'verified', [ship_entry]

    # A transaction may appear in multiple X4 log sources. Its native entry ID,
    # game time and all transaction fields must agree before collapsing copies.
    purchases = {}
    for entry in trade_entries:
        if entry['eventtype'] != 'trade' or entry['money_cents'] >= 0:
            continue
        if (not entry['complete'] or entry['entry_id'] <= 0
                or entry['buyer_id'] not in (0, buyer) or entry['seller_id'] not in (0, seller)
                or entry['partner_id'] not in (0, buyer, seller)
                or entry['ware'] not in ('', ware) or entry['quantity'] not in (0, quantity)):
            return 'ledger_purchase_unconfirmed', None
        transaction = tuple(entry[field] for field in ENTRY_FIELDS if field != 'source')
        copies = purchases.setdefault(transaction, {})
        if entry['source'] in copies:
            return 'ledger_purchase_ambiguous', None
        copies[entry['source']] = entry
    if len(purchases) != 1:
        return ('ledger_purchase_missing' if not purchases else 'ledger_purchase_ambiguous'), None
    copies = next(iter(purchases.values()))
    if next(iter(copies.values()))['money_cents'] != expected_money:
        return 'ledger_debit_disagrees', None
    return 'verified', list(copies.values())


def bound_dock(dock, nonce):
    """Read the native READY binding shared by ledgers and price refusals."""
    if len(dock) != 7 or dock[:3] != ['DOCK', nonce, 'READY']:
        raise ValueError(f'unbound native docking receipt: {dock!r}')
    ship, station, bay = map(_component, dock[3:6])
    if len({ship, station, bay}) != 3:
        raise ValueError(f'overlapping native docking handles: {dock!r}')
    return ship, station, bay, _time(dock[6])


def _valid_ware_quantity(ware, quantity):
    return (isinstance(ware, str) and _WARE_ID.fullmatch(ware) is not None
            and type(quantity) is int and 1 <= quantity <= 65535)


def live_purchase_before(parts, nonce, epoch, *, ware, quantity, sell=False):
    """Validate the docked ship's MD pre-submission cargo and account observation."""
    key = 'sale_id' if sell else 'purchase_id'
    ship_side, station_side = ('seller', 'buyer') if sell else ('buyer', 'seller')
    components = (ship_side, station_side, 'bay')
    fields = (key, 'registry_epoch', *components, 'ware', 'quantity',
              'price_cents', 'cargo_units', 'player_balance_cents', 'company_balance_cents', 'game_time')
    if sell:
        fields += ('station_cargo_units',)
    if not _valid_ware_quantity(ware, quantity):
        raise ValueError('invalid native purchase request')
    dock, quote, before = parts[:3]
    ship, station, bay, bound_at = bound_dock(dock, nonce)
    state = _fields(before, fields)
    if (before[0] != 'BEFORE' or state[key] != nonce or state['registry_epoch'] != epoch
            or state['ware'] != ware or _time(state['game_time']) != bound_at
            or [_component(state[key]) for key in components] != [ship, station, bay]
            or quote != ['RESULT', nonce, 'quote', state['price_cents']]):
        raise ValueError('unbound native pre-purchase observation')
    for field in ('quantity', 'price_cents', 'cargo_units', 'player_balance_cents', 'company_balance_cents'):
        state[field] = _integer(state[field], field)
    if (state['quantity'] != quantity or state['price_cents'] <= 0 or state['cargo_units'] < 0
            or state['player_balance_cents'] < 0 or state['company_balance_cents'] != state['player_balance_cents']):
        raise ValueError('invalid native pre-purchase amounts')
    if sell:
        state['station_cargo_units'] = _integer(state['station_cargo_units'], 'station cargo')
        if state['station_cargo_units'] < 0 or state['cargo_units'] < quantity:
            raise ValueError('invalid native pre-sale cargo amounts')
    return state


def purchase_settlement(rows, nonce, epoch, mode, *, require_absolute_cargo=False, ware=None, quantity=None):
    """Parse unprefixed driver payloads and retain ambiguous economic evidence.

    Malformed sequences or unbound primary receipts raise ValueError. Monetary
    ambiguity produces a JSON-safe result with raw payloads and observed debit.
    """
    live = mode == 'live_purchase'
    if live:
        trial = mode
        require_absolute_cargo = True
    else:
        if ware is not None or quantity is not None:
            raise ValueError('diagnostic purchase uses a fixed ware and quantity')
        trial, quantity = TRIALS[mode]
        ware = DIAGNOSTIC_WARE
    records = list(rows)
    parts = [row.split('|') for row in records]
    steps = [part[0] for part in parts]
    if live:
        opening = ['DOCK', 'RESULT', 'BEFORE', 'RESULT', 'DEAL', 'OBSERVED', 'LEDGER']
        ledger_start = 6
    else:
        opening = ['START', 'DOCK', 'RESULT', 'RESULT', 'DEAL', 'OBSERVED', 'RESULT', 'LEDGER']
        ledger_start = 7
    if (not ledger_start + 2 <= len(parts) <= 201 or steps[:len(opening)] != opening
            or any(step != 'ENTRY' for step in steps[len(opening):-1])
            or (not live and parts[0] != ['START']) or parts[-1] != ['DONE']):
        raise ValueError(f'unexpected native trade sequence: {records!r}')

    if live:
        before = live_purchase_before(parts, nonce, epoch, ware=ware, quantity=quantity)
        dock, quote, _, submitted, raw_deal, observed = parts[:6]
    else:
        dock, quote, submitted, raw_deal, observed = parts[1:6]
    buyer, seller, _, dock_time = bound_dock(dock, nonce)
    if (len(quote) != 4 or quote[1:3] != [nonce, 'quote']
            or submitted != ['RESULT', nonce, 'submitted', quote[3]]
            or (not live and parts[6] != ['RESULT', nonce, 'already_attempted', '0'])):
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
            or deal['deal_ware'] != ware
            or _component(deal['deal_buyer_component']) != buyer
            or _component(deal['deal_seller_component']) != seller):
        raise ValueError(f'unbound native deal: {raw_deal!r}')
    quoted_at = _time(deal['quote_game_time'])
    completed_at = _time(deal['complete_game_time'])
    if not dock_time <= quoted_at < completed_at or (live and dock_time != quoted_at):
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
    ledger = _ledger(parts[ledger_start:-1])
    nominal = deal['deal_unit_ct'] * deal['transferred_qty']
    debit = deal['player_before_ct'] - deal['player_after_ct']
    faction_debit = deal['faction_before_ct'] - deal['faction_after_ct']
    if not docked or not parked:
        reason = 'docking_or_avatar_disagrees'
    elif live and (deal['player_before_ct'] != before['player_balance_cents']
          or deal['faction_before_ct'] != before['company_balance_cents']
          or deal['ship_ware_before'] != before['cargo_units']):
        reason = 'pre_purchase_snapshot_disagrees'
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
        reason, purchase = _journal_reason(
            ledger, buyer, seller, dock_time, completed_at, quoted_unit, ware, quantity, -debit, live=live)
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

    if live:
        result['before'] = before
    if has_absolute_cargo:
        result['ship_ware_units_after'] = deal['ship_ware_after'] if verified else None
    return result


def sale_settlement(rows, nonce, epoch, *, ware, quantity):
    """Verify one exact sale, two settled readbacks, and the linked ship credit."""
    parts = [row.split('|') for row in rows]
    opening = ['DOCK', 'RESULT', 'BEFORE', 'RESULT', 'SALE_COMPLETE',
               'SALE_TRANSFER', 'SALE_READBACK', 'SALE_READBACK', 'LEDGER']
    if (not 10 <= len(parts) <= 201 or [part[0] for part in parts[:9]] != opening
            or any(part[0] != 'ENTRY' for part in parts[9:-1]) or parts[-1] != ['DONE']):
        raise ValueError('unexpected native sale sequence')

    before = live_purchase_before(parts, nonce, epoch, ware=ware, quantity=quantity, sell=True)
    ship, station, bay, dock_time = bound_dock(parts[0], nonce)
    quoted_unit = before['price_cents']
    if parts[3] != ['RESULT', nonce, 'submitted', str(quoted_unit)]:
        raise ValueError('unbound native sale submission')
    completion = _fields(parts[4], ('sale_id', 'epoch', 'buyer', 'seller', 'ware', 'order', 'deal', 'time'))
    order, deal = _component(completion['order']), _component(completion['deal'])
    completed_at = _time(completion['time'])
    if (completion['sale_id'] != nonce or completion['epoch'] != epoch or completion['ware'] != ware
            or _component(completion['buyer']) != station or _component(completion['seller']) != ship
            or len({ship, station, bay, order, deal}) != 5 or completed_at <= dock_time):
        raise ValueError('unbound native sale completion')
    transfer = _fields(parts[5], ('sale_id', 'deal', 'quantity', 'destroyed', 'price_cents', 'time'))
    transferred_at = _time(transfer['time'])
    if (transfer['sale_id'] != nonce or _component(transfer['deal']) != deal or transferred_at < completed_at
            or _integer(transfer['quantity'], 'transfer quantity') != quantity
            or _integer(transfer['destroyed'], 'destroyed cargo') != 0
            or _integer(transfer['price_cents'], 'deal unit price') != quoted_unit):
        raise ValueError('unbound native sale transfer')

    snapshots = []
    last_time = transferred_at
    for part in parts[6:8]:
        snapshot = _fields(part, ('sale_id', 'epoch', 'ship', 'station', 'bay', 'time', 'cargo_units',
                                  'station_cargo_units', 'player_balance_cents',
                                  'company_balance_cents', 'ship_credit_cents'))
        read_at = _time(snapshot['time'])
        if (snapshot['sale_id'] != nonce or snapshot['epoch'] != epoch or read_at <= last_time
                or [_component(snapshot[k]) for k in ('ship', 'station', 'bay')] != [ship, station, bay]):
            raise ValueError('unbound native sale readback')
        last_time = read_at
        for name in ('cargo_units', 'station_cargo_units', 'player_balance_cents',
                     'company_balance_cents', 'ship_credit_cents'):
            snapshot[name] = _integer(snapshot[name], name)
        snapshots.append(snapshot)

    final = snapshots[-1]
    credit = final['company_balance_cents'] - before['company_balance_cents']
    ledger = _ledger(parts[8:-1])
    matched = None
    if any(snapshot['cargo_units'] != before['cargo_units'] - quantity
           or snapshot['station_cargo_units'] != before['station_cargo_units'] + quantity
           for snapshot in snapshots):
        reason = 'sale_cargo_readback_disagrees'
    elif credit <= 0 or any(
            snapshot['company_balance_cents'] != final['company_balance_cents']
            or snapshot['player_balance_cents'] != final['company_balance_cents']
            or snapshot['ship_credit_cents'] != credit for snapshot in snapshots):
        reason = 'sale_credit_readback_disagrees'
    elif ledger['end'] is not None and _time(ledger['end']) + Decimal('0.0005') < last_time:
        reason = 'ledger_predates_readback'
    else:
        reason, matched = _journal_reason(ledger, station, ship, dock_time, completed_at,
                                          quoted_unit, ware, quantity, credit, live=True, sell=True)
    return {
        'verified': reason == 'verified', 'reason': reason, 'price_cents': quoted_unit,
        'credited_cents': credit, 'company_balance_cents': final['company_balance_cents'],
        'cargo_units': final['cargo_units'], 'before': before, 'completion': completion,
        'transfer': transfer, 'readbacks': snapshots, 'ledger': ledger,
        'matched_sale_entries': matched,
    }


def reconcile_observed_purchase(before, after, journal_rows):
    """Read-only assessment of an ambiguous native purchase.

    Before is the verified native pre-submission observation. After is an
    independent stock observation for the same resolved registry epoch, buyer
    and seller. Both contain exact cargo/account amounts and game time. A
    missing observation or unmatched ledger leaves the effect unproven.
    """
    def unproven(reason):
        return {'status': 'no_proven_economic_delta', 'reason': reason}

    if not before:
        return unproven('native_pre_purchase_snapshot_unavailable')
    if not after:
        return unproven('native_post_purchase_snapshot_unavailable')

    try:
        epoch = before['registry_epoch']
        buyer, seller = (_component(before[field]) for field in ('buyer', 'seller'))
        ware, quantity, unit = before['ware'], before['quantity'], before['price_cents']
        if (not re.fullmatch(r'[0-9a-f]{32}', epoch) or epoch != after['registry_epoch']
                or ware != after['ware']
                or buyer != _component(after['buyer']) or seller != _component(after['seller'])):
            return unproven('native_binding_disagrees')
        if buyer == seller or not _valid_ware_quantity(ware, quantity) or type(unit) is not int or unit <= 0:
            return unproven('invalid_native_purchase')
        bound_at, observed_at = (_time(state['game_time']) for state in (before, after))
        if observed_at <= bound_at:
            return unproven('native_observation_not_later')
        cargo, balance, player = ('cargo_units', 'company_balance_cents', 'player_balance_cents')
        amounts = [state[field] for state in (before, after) for field in (cargo, balance, player)]
        if any(type(amount) is not int or amount < 0 for amount in amounts):
            return unproven('invalid_native_amounts')
        if after[cargo] - before[cargo] != quantity:
            return unproven('native_cargo_delta_disagrees')
        debit = before[balance] - after[balance]
        if debit <= 0 or any(state[balance] != state[player] for state in (before, after)):
            return unproven('native_balances_disagree')
        rows = [row.split('|') for row in journal_rows or ()]
        if not rows:
            return unproven('native_journal_unavailable')
        if len(rows) > 193 or rows[0][0] != 'LEDGER' or any(row[0] != 'ENTRY' for row in rows[1:]):
            return unproven('malformed_native_observations')
        ledger = _ledger(rows)
        if (ledger['end'] is not None
                and _time(ledger['end']) > observed_at + Decimal('0.0005')):
            return unproven('native_ledger_after_observation')
        completed = [_time(entry['time']) for entry in ledger['entries'] if entry['role'] == 'completed_trade']
        if ledger['status'] == 'observed' and not completed:
            return unproven('ledger_completed_trade_unconfirmed')
        reason, entries = _journal_reason(ledger, buyer, seller, bound_at, max(completed, default=observed_at),
                                           unit, ware, quantity, -debit, live=True)
    except (KeyError, ValueError, TypeError, IndexError):
        return unproven('malformed_native_observations')
    if entries is None:
        return unproven(reason)
    return {'status': 'already_applied', 'reason': reason, 'trade_id': ledger['trade_id'],
            'settled_total_cents': debit, 'cargo_units': after[cargo],
            'company_balance_cents': after[balance], 'matched_purchase_entries': entries}


def six_unit_settlement(rows, nonce, epoch):
    """Read historical six-unit records that predate absolute cargo fields."""
    return purchase_settlement(rows, nonce, epoch, 'six-unit')
