#!/usr/bin/env python3
"""Check a recorded native water-sale result offline."""

import argparse
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


def verify(result):
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
    return (f'Verified {quantity} {ware} from ship {ship} to station {station}. '
            f'Order {order["order"]}, deal {deal}, journal trade {trade_id}, ship entry {entry}. '
            f'Company credit +{credit} cents. Quoted total {price * quantity} cents.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path, help='recorded native diagnostic result.json')
    args = parser.parse_args()
    try:
        summary = verify(json.loads(args.result.read_text(encoding='utf-8')))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, InvalidOperation) as error:
        parser.exit(1, f'Sale evidence rejected: {error}\n')
    print(summary)


if __name__ == '__main__':
    main()
