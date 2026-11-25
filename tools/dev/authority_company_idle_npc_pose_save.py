"""Read the private WRN pose through the saved registry and component ancestry."""
import hashlib
import json
import math
import xml.etree.ElementTree as ET

from authority.checkpoints import CheckpointStore
from authority.registry_availability_evidence import verify_join_snapshot
from authority_company_idle_npc_pose_trial import INDEX, READBACK_METRES, SECTOR
import authority_company_undock_trial as undock
from authority_trade_checkpoint import TradeSaveEvidence
from lockstep.launcher import gamedata

CAPTAIN = 'PYN-740'
CAPTAIN_SEED = '2211373338772438693'
STOCK_SECTORS = 'maps/xu_ep2_universe/sectors.xml'
STOCK_SECTORS_SHA256 = '1a8d489782fd6d50be5ead86e3b1554bfb7ad4e6260aa7970fa81a6bdf82715b'


def _xyz(position):
    result = tuple(float(position.get(axis, 0)) for axis in 'xyz')
    if not all(math.isfinite(value) for value in result):
        raise ValueError('nonfinite saved component coordinates')
    return result


def _stock_origin(game, zone, connection):
    """Resolve the fixture's saved default zone from pinned installed map data."""
    matches = []
    for _, catalog in gamedata.catalogs(game):
        offset = 0
        for path, size in gamedata.index(catalog):
            if path.lower() == STOCK_SECTORS:
                matches.append((catalog, offset, size))
            offset += size
    if len(matches) != 1:
        raise ValueError('saved default zone requires one pinned stock sectors map')
    catalog, offset, size = matches[0]
    with catalog.with_suffix('.dat').open('rb') as source:
        source.seek(offset)
        data = source.read(size)
    if hashlib.sha256(data).hexdigest() != STOCK_SECTORS_SHA256:
        raise ValueError('stock sectors map differs from the private fixture build')
    root = ET.fromstring(data)
    results = []
    for sector in root.findall('macro'):
        if sector.get('name', '').lower() != SECTOR:
            continue
        for child in sector.findall('connections/connection'):
            ref = child.find('macro')
            transform = child.find('offset')
            if (child.get('name', '').lower() == connection and ref is not None
                    and ref.get('ref', '').lower() == zone and ref.get('connection') == 'sector'
                    and transform is not None and [item.tag for item in transform] == ['position']):
                results.append(_xyz(transform[0].attrib))
    if len(results) != 1:
        raise ValueError('saved zone lacks a unique unrotated stock sector origin')
    return results[0]


class _SavedPose(TradeSaveEvidence):
    def __init__(self):
        super().__init__()
        self.path = []
        self.actor = self.zone = self.captain = None
        self.captain_frame = self.assigned_pilot = None
        self.zone_connection = None

    def observe(self, tag, depth, attrs, index):
        super().observe(tag, depth, attrs, index)
        # The universe observer starts at depth two, below the XML root.
        del self.path[depth - 2:]
        self.path.append((tag, attrs, {}))
        if tag == 'offset' and self.path[-2][0] == 'component':
            self.path[-2][2]['default'] = attrs.get('default') == '1'
        if tag == 'position' or tag in ('rotation', 'quaternion'):
            if depth >= 3 and self.path[-2][0] == 'offset' and self.path[-3][0] == 'component':
                transform = self.path[-3][2]
                if tag in transform:
                    raise ValueError('duplicate saved component transform')
                transform[tag] = attrs
        if index == INDEX:
            self.actor = self.path[-1]
            parents = [item for item in self.path[:-1] if item[0] == 'component']
            self.zone = parents[-1] if parents[-1][1].get('class') == 'zone' else None
            if self.zone is not None:
                depth = next(i for i, item in enumerate(self.path) if item is self.zone)
                connection = self.path[depth - 1]
                if connection[0] != 'connection':
                    raise ValueError('saved zone is missing a surrounding sector connection')
                self.zone_connection = connection[1]['connection']
        if (tag == 'component' and attrs.get('code') == CAPTAIN
                and any(item is self.actor for item in self.path[:-1])):
            if self.captain is not None or (attrs.get('class'), attrs.get('owner')) != ('npc', 'player'):
                raise ValueError('saved captain identity is ambiguous')
            self.captain = {'code': CAPTAIN, 'macro': attrs['macro'], 'owner': attrs['owner']}
            self.captain_frame = self.path[-1]
        if self.captain_frame is not None and self.path[-2] is self.captain_frame:
            if tag == 'npcseed':
                self.captain['seed'] = attrs.get('seed')
            elif tag == 'entity':
                self.captain['post'] = attrs.get('post')
        if (tag == 'post' and attrs.get('id') == 'aipilot' and len(self.path) >= 3
                and self.path[-2][0] == 'control' and self.path[-3] is self.actor):
            if self.assigned_pilot is not None:
                raise ValueError('duplicate saved WRN pilot assignment')
            self.assigned_pilot = attrs['component']

    def position(self, game):
        ship = _xyz(self.actor[2].get('position', {}))
        if self.zone is None:
            return ship, None
        zone = self.zone[2]
        # This private fixture requires unrotated saved zone axes. A rotated
        # zone needs its full transform and cannot be treated as a translation.
        if (any(float(value) != 0 for value in zone.get('rotation', {}).values())
                or any(float(zone.get('quaternion', {}).get(axis, 0)) != 0 for axis in 'xyz')
                or abs(float(zone.get('quaternion', {}).get('w', 1))) != 1):
            raise ValueError('rotated saved zone cannot be inspected as a translation')
        macro = self.zone[1]['macro']
        origin = (_stock_origin(game, macro, self.zone_connection) if zone.get('default')
                  else _xyz(zone.get('position', {})))
        return tuple(a + b for a, b in zip(origin, ship)), {
            'macro': macro, 'connection': self.zone_connection, 'origin_m': origin,
            'ship_offset_m': ship, 'stock_sectors_sha256': STOCK_SECTORS_SHA256 if zone.get('default') else None,
        }


def read(checkpoint, *, game=None):
    evidence = _SavedPose()
    snapshot = verify_join_snapshot(checkpoint, observe_universe=evidence.observe)
    trade = evidence.result()
    ship = next(ship for ship in trade['ships'] if ship['index'] == INDEX)
    if (evidence.actor is None or evidence.captain is None or evidence.assigned_pilot is None
            or evidence.assigned_pilot != evidence.captain_frame[1].get('id')
            or evidence.captain.get('post') != 'aipilot'
            or evidence.captain.get('seed') != CAPTAIN_SEED):
        raise ValueError('saved WRN or its original assigned captain is absent')
    position, zone = evidence.position(game) if ship['dock'] is None else (None, None)
    return {'checkpoint_id': checkpoint.checkpoint_id, 'sha256': checkpoint.sha256,
            'marker': checkpoint.marker, 'registry_epoch': snapshot['epoch'],
            'ship': ship, 'captain': evidence.captain, 'avatar': trade['avatar'],
            'account_cents': trade['account']['amount_cents'],
            'position_m': position, 'saved_zone': zone}


def verify(store, output, *, game):
    source = undock.source_checkpoint(store)
    final = CheckpointStore(output / 'checkpoints').latest_marked_confirmed()
    result = json.loads((output / 'result.json').read_bytes())
    if (result.get('success') is not True or result.get('owned_processes_remaining') != []
            or result.get('final_world_state') != 'paused'
            or result.get('source_sha256') != source.sha256
            or result.get('final_checkpoint_id') != final.checkpoint_id
            or result.get('final_checkpoint_sha256') != final.sha256 or final.marker == source.marker):
        raise ValueError('private provider and confirmed save identities differ')
    proof = result['company_trade_trial']['native_idle_npc_pose']
    first, second = proof['steps']
    if (any(step['writes_dispatched'] != 1 for step in (first, second))
            or math.dist(first['target'], second['target']) < 49):
        raise ValueError('private provider did not verify two separate targets')
    before, after = read(source), read(final, game=game)
    if (any(before[field] != after[field] for field in ('registry_epoch', 'captain', 'avatar', 'account_cents'))
            or after['ship']['sector'] != SECTOR or after['ship']['dock'] is not None
            or after['ship']['cargo_units'] != before['ship']['cargo_units']):
        raise ValueError('saved Company ship, captain, avatar, account or cargo changed')
    error = math.dist(second['target'], after['position_m'])
    if error > READBACK_METRES:
        raise ValueError(f'saved NPC pose differs from second target by {error:.3f} m')
    return {'source': before, 'final': after, 'native_pose': proof, 'saved_error_m': error}
