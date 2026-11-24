"""Reconcile the native piloted approach, external dock and unbooked intent."""
from dataclasses import dataclass
from decimal import Decimal
import re

from authority_trade_cockpit import CONFIRM_PATH, DOCK_TRADE_ENTRY, read_probe_rows


PREFIX = b'[X4LAN] AUTH_PLAYER_DOCK_FLIGHT '


def _handle(raw):
    if re.fullmatch(r'0x[0-9a-fA-F]{1,16}', raw):
        return int(raw, 16)
    if re.fullmatch(r'(0|[1-9][0-9]{0,19})(ULL)?', raw):
        return int(raw.removesuffix('ULL'))
    raise ValueError(f'invalid X4 component {raw!r}')


@dataclass(frozen=True)
class FlightObservation:
    state: str
    ship: int
    station: int
    bay: int
    controlled: int
    occupied: int
    container: int
    captain: int
    distance: int
    water: int
    balance: int
    age: Decimal

    @classmethod
    def parse(cls, raw):
        fields = raw.split('|')
        if len(fields) != 12 or fields[0] not in ('IN_SPACE', 'DOCKED'):
            raise ValueError(f'invalid native flight observation: {raw}')
        age = Decimal(fields[-1])
        if not age.is_finite() or age < 0:
            raise ValueError('invalid native game time')
        values = [int(value) for value in fields[8:11]]
        if min(values) < 0:
            raise ValueError('missing native flight distance, cargo or balance')
        return cls(fields[0], *map(_handle, fields[1:8]), *values, age)

    def at_target(self, source):
        return (self.state == 'DOCKED' and self.ship == source.ship and self.station == source.station
                and self.bay not in (0, self.ship, self.station)
                and self.ship == self.controlled == self.occupied == self.container)


def _once(rows, name):
    prefix = name + '|'
    matches = [(index, row[len(prefix):]) for index, row in enumerate(rows) if row.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f'expected exactly one {name} receipt, found {len(matches)}')
    return matches[0]


def _verify_berth(stages, source, docked):
    if stages['ROUTE'][1] != 'nearby_berth':
        raise ValueError('unknown docking fixture route')
    placed = stages['PLACEMENT'][1].split('|')
    if len(placed) != 7 or placed[0] != 'PLACED':
        raise ValueError('missing near-station placement readback')
    ship, station, bay = map(_handle, placed[1:4])
    station_m, bay_m = map(int, placed[4:6])
    previous = Decimal(placed[6])
    if (ship != source.ship or station != source.station or bay in (0, ship, station)
            or not 0 <= station_m < source.distance or not 0 <= bay_m < source.distance
            or not previous.is_finite() or not source.age <= previous <= docked.age):
        raise ValueError('positioned ship, station, distances or game time disagree')
    if stages['PLAYER_DOCK_REQUEST'][1] not in ('granted', 'queued'):
        raise ValueError('native station docking request was refused')
    for status in ('REQUESTED', 'ASSIGNED', 'DISPATCHED'):
        fields = stages['BERTH|' + status][1].split('|')
        if len(fields) != 7:
            raise ValueError('invalid native berth receipt')
        ship, station, bay, controlled, occupied, container = map(_handle, fields[:6])
        age = Decimal(fields[6])
        if not age.is_finite() or not previous <= age <= docked.age:
            raise ValueError('out-of-order docking assignment')
        if (ship != source.ship or station != source.station or not ship == controlled == occupied == container
                or (bay != docked.bay and not (status == 'REQUESTED' and bay == 0))):
            raise ValueError('stock berth assignment and physical docking observations disagree')
        previous = age


def _acquired_berth(rows, stages, docked):
    index, raw = _once(rows, 'ACQUIRED_BERTH')
    fields = raw.split('|')
    if len(fields) != 10 or index != stages['DOCKED'][0] + 1:
        raise ValueError('invalid native acquired berth receipt')
    ship, station, bay = map(_handle, fields[:3])
    seed, sector, station_code, bay_macro, dockarea_macro, external, occupant = fields[3:]
    if (ship, station, bay, _handle(occupant)) != (docked.ship, docked.station, docked.bay, docked.ship):
        raise ValueError('acquired berth does not bind to the occupied pilot ship')
    if (external != '1' or sector != 'cluster_01_sector001_macro' or station_code != 'BIM-169'
            or not all(re.fullmatch(r'[a-z][a-z0-9_]{0,127}', macro) for macro in (bay_macro, dockarea_macro))
            or not re.fullmatch(r'-?[1-9][0-9]{0,19}', seed) or not -(2**63) <= int(seed) < 2**64):
        raise ValueError('invalid external berth identity')
    # Keep native largeint text exact across the Lua and JSON boundaries.
    return {'sector': sector, 'station_code': station_code, 'bay_macro': bay_macro,
            'dockarea_macro': dockarea_macro, 'bay_seed': seed,
            'external': True, 'occupied_by_ship': True}


def _verify_approach(stages, source):
    start, later = map(int, stages['DEPARTURE'][1].split('|'))
    if start != source.distance or start - later < 1000 or later < 0:
        raise ValueError('normal flight lacks an observed kilometre of approach')
    if not stages['AUTOPILOT'][1].startswith('started|'):
        raise ValueError('native approach lacks an autopilot receipt')
    if stages['QUICKDOCK'][1] != 'dispatched':
        raise ValueError('native quick docking command lacks its receipt')


def verify_player_flight(rows, *, prepare_berth=False, require_dock_trade_entry=False,
                         require_acquired_berth=False):
    docking = (('ROUTE', 'PLACEMENT', 'PLAYER_DOCK_REQUEST', 'BERTH|REQUESTED', 'BERTH|ASSIGNED', 'BERTH|DISPATCHED')
               if prepare_berth else ('AUTOPILOT', 'DEPARTURE', 'QUICKDOCK'))
    names = ('START', 'SEAT', *docking, 'DOCKED', 'BEFORE_CONFIRM', 'CONFIRM_PATH', 'AFTER_CONFIRM', 'DONE')
    stages = {name: _once(rows, name) for name in names}
    order = [stage[0] for stage in stages.values()]
    if order != sorted(order) or rows[-1] != 'DONE|intent_captured':
        raise ValueError('incomplete or out-of-order player docking receipts')
    source = FlightObservation.parse(stages['START'][1])
    if (source.state != 'IN_SPACE' or not source.ship or not source.station or source.ship == source.station
            or source.controlled or source.occupied or source.bay or source.captain):
        raise ValueError('source ship, cockpit or dock differs from the confirmed parked fixture')
    seat = stages['SEAT'][1].split('|')
    if len(seat) != 2 or seat[0] != 'controlled' or _handle(seat[1]) != source.ship:
        raise ValueError('normal seat confirmation identifies another ship')
    docked, before, after = (FlightObservation.parse(stages[name][1])
                             for name in ('DOCKED', 'BEFORE_CONFIRM', 'AFTER_CONFIRM'))
    if (not all(sample.at_target(source) for sample in (docked, before, after))
            or not docked.bay == before.bay == after.bay
            or not source.age < docked.age <= before.age <= after.age or docked.distance >= source.distance):
        raise ValueError('pilot or physical docking observations disagree')
    if prepare_berth:
        _verify_berth(stages, source, docked)
    else:
        _verify_approach(stages, source)
    if before.water != after.water or before.balance != after.balance:
        raise ValueError('cargo or Company account changed during the capture-only confirmation')
    dock_entries = [row for row in rows if row.startswith('DOCK_TRADE_ENTRY|')]
    if dock_entries or require_dock_trade_entry:
        entry_index, path = _once(rows, 'DOCK_TRADE_ENTRY')
        if (path != DOCK_TRADE_ENTRY
                or not stages['DOCKED'][0] < entry_index < stages['BEFORE_CONFIRM'][0]):
            raise ValueError('invalid native DockedMenu trade entry')
    if stages['CONFIRM_PATH'][1] != CONFIRM_PATH:
        raise ValueError('stock confirmation lacks a registered active widget callback')
    intent_index, intent = _once(rows, 'INTENT')
    match = re.fullmatch(r'cluster_01_sector001_macro\|BIM-169\|water\|6\|([1-9][0-9]*)', intent)
    if not match or not stages['AFTER_CONFIRM'][0] < intent_index < stages['DONE'][0]:
        raise ValueError('missing stock intent for six water at BIM-169')
    result = {'client_intent_captured': True, 'purchase_executed': False, 'admitted': False,
            'start_distance_m': source.distance, 'docked_distance_m': docked.distance,
            'docking_route': 'nearby_assigned_berth_fixture' if prepare_berth else 'piloted_approach',
            'flight_game_seconds': None if prepare_berth else float(docked.age - source.age), 'unit_price_cents': int(match[1]),
            'water_units': after.water, 'company_balance_cents': after.balance,
            'stock_confirm_path': CONFIRM_PATH, 'records': rows}
    if dock_entries:
        result['stock_dock_trade_entry'] = DOCK_TRADE_ENTRY
    if require_acquired_berth or any(row.startswith('ACQUIRED_BERTH|') for row in rows):
        result['acquired_berth'] = _acquired_berth(rows, stages, docked)
    return result


def observe_player_flight(log, deadline, guard, *, prepare_berth=False):
    rows = read_probe_rows(log, PREFIX, deadline, guard, max_records=1024,
                           error_markers=(b'[X4LAN] AUTH_REGISTRY_FULL_INSPECT_ERROR ',
                                          b'[X4LAN] AUTH_RECOVERY ERROR',
                                          b'[X4LAN] AUTH_CLIENT_SEAT_REFUSED '))
    return verify_player_flight(rows, prepare_berth=prepare_berth, require_dock_trade_entry=True,
                                require_acquired_berth=True)
