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
            if self.account is not None or not re.fullmatch(r'\[0x[0-9a-f]+\]', attrs.get('id', '')):
                raise ValueError('ambiguous or malformed saved player faction account')
            self.account = {
                'id': attrs['id'],
                'amount': _nonnegative(attrs['amount'], 'account amount') if 'amount' in attrs else None,
                'unit': 'unconfirmed',
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path, help='specific checkpoint directory within a checkpoint store')
    args = parser.parse_args()
    try:
        checkpoint = CheckpointStore(args.checkpoint.parent).get(args.checkpoint.name)
        result = read_verified_trade_checkpoint(checkpoint)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
