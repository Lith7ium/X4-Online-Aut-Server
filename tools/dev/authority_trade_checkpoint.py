"""Read saved Company trade evidence from the private two-ship Authority fixture."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from authority.checkpoints import CheckpointStore
from authority.registry_availability_evidence import verify_join_snapshot
from authority_station_parking import SHIPS

WARE = 'water'
SELLER_CODE = 'BIM-169'
SELLER_SECTOR = 'cluster_01_sector001_macro'
AVATAR_STATION = 'XPJ-169'
AVATAR_SECTOR = 'cluster_06_sector002_macro'


def _nonnegative(raw, field):
    if not isinstance(raw, str) or not re.fullmatch(r'0|[1-9][0-9]{0,18}', raw):
        raise ValueError(f'invalid saved {field}: {raw!r}')
    return int(raw)


def _account_cents(raw):
    if raw is None:
        return None
    match = re.fullmatch(r'(0|[1-9][0-9]{0,18})(?:\.([0-9]{1,2})0*)?', raw)
    if not match:
        raise ValueError(f'invalid or inexact saved account amount: {raw!r}')
    whole, fraction = match.groups()
    return int(whole) * 100 + int((fraction or '').ljust(2, '0'))


def _station(attrs):
    return {field: attrs.get(field) for field in ('code', 'macro', 'owner')}


class TradeSaveEvidence:
    """Collect evidence during the verified registry's existing universe pass."""

    def __init__(self):
        self.targets = {ship['index']: ship for ship in SHIPS}
        self.ships = {}
        self.components = []
        self.ship_depth = None
        self.ship = None
        self.cargo_depth = None
        self.factions = 0
        self.in_factions = self.in_player_faction = False
        self.account = None
        self.avatar = None
        self.seller = None

    def observe(self, tag, depth, attrs, index):
        if depth <= 3:
            self.in_factions = depth == 3 and tag == 'factions'
        if depth <= 4:
            self.in_player_faction = self.in_factions and depth == 4 and tag == 'faction' and attrs.get('id') == 'player'
            self.factions += int(self.in_player_faction)
        if self.in_player_faction and depth == 5 and tag == 'account':
            if (self.account is not None or set(attrs) - {'id', 'amount'}
                    or not re.fullmatch(r'\[0x[0-9a-f]+\]', attrs.get('id', ''))):
                raise ValueError('ambiguous or malformed saved player faction account')
            self.account = {
                'id': attrs['id'],
                'amount': attrs.get('amount'),
                'amount_cents': _account_cents(attrs.get('amount')),
                'xml_path': '/savegame/universe/factions/faction[@id="player"]/account/@amount',
            }

        if self.ship_depth is not None:
            if depth <= self.ship_depth:
                self.ship_depth = self.ship = self.cargo_depth = None
            elif depth == self.ship_depth + 1:
                self.cargo_depth = None
                if tag == 'cargo':
                    if self.ship['cargo_encoding'] != 'no_cargo_node':
                        raise ValueError('duplicate direct ship cargo node')
                    self.ship['cargo_encoding'] = 'no_ware_row'
                    self.cargo_depth = depth
            elif self.cargo_depth is not None and depth == self.cargo_depth + 1 and tag == 'ware':
                if not attrs.get('ware'):
                    raise ValueError('unnamed ware in direct ship cargo')
                if attrs['ware'] == WARE:
                    if self.ship['cargo_encoding'] == 'explicit_ware_row':
                        raise ValueError(f'duplicate {WARE} ware in direct ship cargo')
                    self.ship['cargo_units'] = _nonnegative(attrs.get('amount'), 'cargo amount')
                    self.ship['cargo_encoding'] = 'explicit_ware_row'

        if tag != 'component':
            return
        while self.components and self.components[-1][0] >= depth:
            self.components.pop()
        self.components.append((depth, attrs))
        kind = attrs.get('class', '')
        if index not in self.targets and kind != 'player' and attrs.get('code') != SELLER_CODE:
            return
        ancestors = [item for _, item in reversed(self.components[:-1])]
        sector = next((item.get('macro') for item in ancestors if item.get('class') == 'sector'), None)
        if not sector:
            raise ValueError('saved component is missing a sector')
        host = next((item for item in ancestors
                     if item.get('class') == 'station' or item.get('class', '').startswith('ship_')), None)
        direct_parent = ancestors[0]
        if index in self.targets:
            expected = self.targets[index]
            if (index in self.ships or not kind.startswith('ship_') or attrs.get('owner') != 'player'
                    or (attrs.get('code'), attrs.get('macro')) != (expected['code'], expected['macro'])):
                raise ValueError(f'saved Company ship identity differs at registry index {index}')
            connection = attrs.get('connection')
            if connection == 'space' and direct_parent.get('class') in ('zone', 'sector') and host is None:
                dock = None
            elif connection == 'dock' and direct_parent.get('class') == 'dockingbay' and host is not None:
                dock = {'host_code': host.get('code'), 'host_class': host.get('class'),
                        'station_code': host.get('code') if host.get('class') == 'station' else None}
                if not dock['host_code']:
                    raise ValueError('saved docking host has no code')
            else:
                raise ValueError(f'unrecognized saved dock ancestry for {expected["code"]}')
            self.ship = {'index': index, 'code': expected['code'], 'sector': sector, 'dock': dock,
                         'cargo_units': 0, 'cargo_encoding': 'no_cargo_node'}
            self.ships[index] = self.ship
            self.ship_depth = depth
        elif kind == 'player':
            if (self.avatar is not None or attrs.get('owner') != 'player' or attrs.get('connection') != 'room'
                    or host is None or host.get('class') != 'station' or host.get('code') != AVATAR_STATION
                    or sector != AVATAR_SECTOR):
                raise ValueError('saved server avatar is not unambiguously parked at expected station')
            self.avatar = {'station': _station(host), 'sector': sector, 'room': direct_parent.get('macro')}
        elif attrs.get('code') == SELLER_CODE:
            if self.seller is not None or kind != 'station' or sector != SELLER_SECTOR or attrs.get('owner') != 'teladi':
                raise ValueError('saved trade seller identity differs')
            self.seller = {**_station(attrs), 'sector': sector}

    def result(self):
        if self.factions != 1 or self.account is None or self.ships.keys() != self.targets.keys():
            raise ValueError('missing saved faction account or exact Company ship bindings')
        if self.avatar is None or self.seller is None:
            raise ValueError('saved avatar or trade seller missing')
        return {'ware': WARE, 'account': self.account, 'ships': [self.ships[index] for index in self.targets],
                'avatar': self.avatar, 'seller': self.seller}


def read_verified_trade_checkpoint(checkpoint):
    evidence = TradeSaveEvidence()
    snapshot = verify_join_snapshot(checkpoint, observe_universe=evidence.observe)
    return {'checkpoint': checkpoint.checkpoint_id, 'sha256': checkpoint.sha256, 'marker': checkpoint.marker,
            'registry_epoch': snapshot['epoch'], **evidence.result()}


def compare_verified_trade(before, after, *, quantity, debit_cents):
    """Compare two verified snapshots against an independent native receipt."""
    if (type(quantity) is not int or quantity <= 0
            or type(debit_cents) is not int or debit_cents <= 0):
        raise ValueError('expected positive quantity and debit in cents')
    if (before['sha256'] == after['sha256'] or before['marker'] == after['marker']
            or any(before[key] != after[key] for key in ('registry_epoch', 'ware', 'avatar', 'seller'))):
        raise ValueError('snapshots are identical or their trade context changed')
    initial_account, final_account = (state['account']['amount_cents'] for state in (before, after))
    if initial_account is None or final_account is None or initial_account - final_account != debit_cents:
        raise ValueError('saved account debit does not match the native receipt')
    initial, final = ({ship['index']: ship for ship in state['ships']} for state in (before, after))
    purchased_ship = final[7354]
    if (final[1970] != initial[1970] or purchased_ship['sector'] != after['seller']['sector']
            or not purchased_ship['dock'] or purchased_ship['dock']['station_code'] != after['seller']['code']
            or purchased_ship['cargo_units'] - initial[7354]['cargo_units'] != quantity):
        raise ValueError('saved cargo delta or docking evidence does not match the native receipt')
    return {'before_sha256': before['sha256'], 'after_sha256': after['sha256'],
            'debit_cents': debit_cents, 'added_units': quantity}


def _read_directory(directory):
    return read_verified_trade_checkpoint(CheckpointStore(directory.parent).get(directory.name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path, help='specific checkpoint directory within a checkpoint store')
    parser.add_argument('--before', type=Path, help='compare an earlier checkpoint with the positional checkpoint')
    parser.add_argument('--quantity', type=int, help='positive quantity from the native receipt')
    parser.add_argument('--debit-cents', type=int, help='positive debit in cents from the native receipt')
    args = parser.parse_args()
    if any(value is not None for value in (args.before, args.quantity, args.debit_cents)):
        if any(value is None for value in (args.before, args.quantity, args.debit_cents)):
            parser.error('--before, --quantity and --debit-cents are required together')
    try:
        result = _read_directory(args.checkpoint)
        if args.before is not None:
            result['comparison'] = compare_verified_trade(_read_directory(args.before), result,
                                                          quantity=args.quantity, debit_cents=args.debit_cents)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
