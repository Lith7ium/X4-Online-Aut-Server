#!/usr/bin/env python3
"""Check a recorded native water-sale result offline."""

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def one(rows, kind):
    matching = [row[1:] for row in rows if row[0] == kind]
    require(len(matching) == 1, f'expected one {kind} row')
    return matching[0]


def fields(row):
    pairs = [field.split('=', 1) for field in row]
    result = dict(pairs)
    require(len(result) == len(row), 'duplicate field')
    return result


def component(value):
    require(re.fullmatch(r'0x[0-9a-f]+', value) and int(value, 16) > 0,
            'invalid native component')
    return value


@dataclass(frozen=True)
class SaleRequest:
    ship_id: int
    station_id: int
    ware: str
    quantity: int
    unit_price_cents: int

    def __post_init__(self):
        for field in ('ship_id', 'station_id', 'quantity', 'unit_price_cents'):
            value = getattr(self, field)
            require(type(value) is int and value > 0, f'invalid requested sale {field}')
        require(self.ship_id != self.station_id, 'requested sale needs distinct ship and station IDs')
        require(self.ware == 'water', 'this recording format only supports water')


@dataclass(frozen=True)
class SaleSettlement:
    ship_id: int
    station_id: int
    ware: str
    quantity: int
    unit_price_cents: int
    credited_cents: int
    cargo_units: int
    company_balance_cents: int
    journal_trade_id: int
    journal_ship_entry_id: int
    order_id: int
    deal_id: int


def verify_settlement(result, expected: SaleRequest) -> SaleSettlement:
    """Verify a recorded water sale against independent IDs from the recorded run.

    Runtime IDs only identify the objects in that run. This does not establish
    their identity in another game process or after a reload.
    """
    require(isinstance(expected, SaleRequest), 'an independent SaleRequest is required')
    return _verify_recording(result, expected)[0]


def _verify_recording(result, expected=None):
    sale = result['native_water_sale']
    require(result.get('success') is True and sale.get('status') == 'CONFIRMED',
            'recording has no confirmed outcome')
    rows = [row.split('|') for row in sale['rows']]
    attempted = fields(one(rows, 'ATTEMPTED'))
    order = fields(one(rows, 'ORDER'))
    event = fields(one(rows, 'EVENT'))
    quote = fields(one(rows, 'BERTH_QUOTE'))
    ship, station = component(attempted['ship']), component(attempted['station'])
    deal = component(order['deal'])
    require(ship != station and event['buyer'] == station and event['seller'] == ship,
            'completion event does not identify the station as buyer and ship as seller')
    require(event['order'] == component(order['order'])
            and event['deal'] == event['param'] == deal and event['matches'] == '1',
            'completion event does not match the submitted order and deal')

    quantity = int(attempted['quantity'])
    price = int(attempted['price_ct'])
    if expected is not None:
        require(int(ship, 16) == expected.ship_id, 'recorded ship differs from requested sale')
        require(int(station, 16) == expected.station_id, 'recorded station differs from requested sale')
        require(quantity == expected.quantity, 'recorded quantity differs from requested sale')
        require(price == expected.unit_price_cents, 'recorded unit price differs from requested sale')

    opening_water = int(attempted['water'])
    opening_company = int(attempted['money_ct'])
    opening_faction = int(attempted['ship_ct'])
    require(0 < quantity <= opening_water and price == int(quote['price_ct']) > 0,
            'invalid quantity or inconsistent quote')

    labels = [row[0] for row in rows]
    quote_at, attempt_at, order_at, event_at, confirmed_at = (
        labels.index(key) for key in ('BERTH_QUOTE', 'ATTEMPTED', 'ORDER', 'EVENT', 'CONFIRMED'))
    require(quote_at < attempt_at < order_at < event_at < confirmed_at,
            'sale events are out of order')
    observed = rows[event_at + 1:confirmed_at]
    samples = [fields(row[1:]) for row in observed if row[0] == 'PASSIVE']
    require(len(samples) >= 3, 'missing transfer or settled readbacks')
    require(all(sample['ship'] == ship and sample['station'] == station
                and sample['deal'] == deal and sample['bay'] == quote['bay']
                and sample['identity'] == sample['berth'] == '1'
                for sample in samples), 'readbacks refer to another ship, station, deal or berth')
    require(any(sample['bound'] == '1' and int(sample['transferred']) == quantity
                and int(sample['destroyed']) == 0 for sample in samples[:-2]),
            'missing linked transfer readback with zero destroyed units')
    first, final = samples[-2:]
    credit = int(final['credit_ct'])
    sample_times = [Decimal(sample['age_s']) for sample in samples]
    require(all(time.is_finite() for time in sample_times)
            and all(before < after for before, after in zip(sample_times, sample_times[1:]))
            and credit > 0, 'missing positive credit or ordered settlement readbacks')
    for sample in (first, final):
        require(int(sample['water']) == opening_water - quantity
                and int(sample['station_water_delta']) == quantity,
                'settled cargo does not show ship debit and station gain')
        require(int(sample['company_ct']) - opening_company == credit
                and int(sample['faction_ct']) - opening_faction == credit
                and int(sample['credit_ct']) == int(sample['ship_credit_ct']) == credit,
                'settled accounts do not match the positive credit')
        require(sample['current'] == 'Wait'
                and sample['order_exists'] == sample['queue'] == sample['tradeorders'] == '0',
                'trade remains active at settlement')

    trade_id, = one(rows, 'CONFIRMED')
    require(int(trade_id) > 0, 'invalid confirmed journal trade ID')
    ledger = {tuple(row[1:]) for row in observed if row[0] == 'LEDGER'}
    journal = {tuple(row[1:]) for row in observed if row[0] == 'JOURNAL'}
    require(ledger == {('observed', 'nil', trade_id)} and len(journal) == 1,
            'missing or conflicting observations of the linked native journal')
    source, entry, linked_trade, role, buyer, seller, ware, units, unit_price, money, linked = (
        next(iter(journal)))
    require(source == 'ship' and int(entry) > 0 and linked_trade == trade_id
            and role == 'completed_trade' and linked == 'true',
            'journal entry is not the linked completed ship trade')
    require(int(buyer) == int(station, 16) and int(seller) == int(ship, 16),
            'journal buyer or seller differs from the completion event')
    require(ware == 'water' and int(units) == quantity and int(unit_price) == price
            and int(money) == credit, 'journal ware, quantity, price or signed credit differs')
    settlement = SaleSettlement(
        ship_id=int(ship, 16), station_id=int(station, 16), ware=ware, quantity=quantity,
        unit_price_cents=price, credited_cents=credit, cargo_units=int(final['water']),
        company_balance_cents=int(final['company_ct']), journal_trade_id=int(trade_id),
        journal_ship_entry_id=int(entry), order_id=int(order['order'], 16), deal_id=int(deal, 16),
    )
    summary = (f'Verified {quantity} {ware} from ship {ship} to station {station}. '
               f'Order {order["order"]}, deal {deal}, journal trade {trade_id}, ship entry {entry}. '
               f'Company credit +{credit} cents. Quoted total {price * quantity} cents.')
    return settlement, summary


def verify(result):
    return _verify_recording(result)[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path, help='recorded native diagnostic result.json')
    parser.add_argument('--ship-id', type=lambda value: int(value, 0), help='expected runtime ship ID')
    parser.add_argument('--station-id', type=lambda value: int(value, 0), help='expected runtime station ID')
    parser.add_argument('--ware', help='expected ware (water)')
    parser.add_argument('--quantity', type=int, help='expected positive sale quantity')
    parser.add_argument('--unit-price-cents', type=int, help='expected unit price in cents')
    args = parser.parse_args()
    expected = (args.ship_id, args.station_id, args.ware, args.quantity, args.unit_price_cents)
    if any(value is not None for value in expected) and any(value is None for value in expected):
        parser.error('expected sale requires --ship-id, --station-id, --ware, --quantity and --unit-price-cents')
    try:
        result = json.loads(args.result.read_text(encoding='utf-8'))
        if args.ship_id is None:
            summary = verify(result)
        else:
            summary = json.dumps(asdict(verify_settlement(result, SaleRequest(*expected))), sort_keys=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, InvalidOperation) as error:
        parser.exit(1, f'Sale evidence rejected: {error}\n')
    print(summary)


if __name__ == '__main__':
    main()
