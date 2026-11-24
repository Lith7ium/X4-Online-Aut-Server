"""Reconcile the native piloted approach, external dock and unbooked intent."""
from dataclasses import dataclass
from decimal import Decimal
import re

from authority_trade_cockpit import read_probe_rows


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


def verify_player_flight(rows):
    names = ('START', 'SEAT', 'AUTOPILOT', 'DEPARTURE', 'QUICKDOCK', 'DOCKED',
             'BEFORE_CONFIRM', 'AFTER_CONFIRM', 'DONE')
    stages = {name: _once(rows, name) for name in names}
    order = [stage[0] for stage in stages.values()]
    if order != sorted(order) or not rows[-1] == 'DONE|intent_captured':
        raise ValueError('incomplete or out-of-order player docking receipts')
    source = FlightObservation.parse(stages['START'][1])
    if (source.state != 'IN_SPACE' or not source.ship or not source.station or source.ship == source.station
            or source.controlled or source.occupied or source.bay or source.captain):
        raise ValueError('source ship, cockpit or dock differs from the confirmed parked fixture')
    start, later = map(int, stages['DEPARTURE'][1].split('|'))
    if start != source.distance or start - later < 1000 or later < 0:
        raise ValueError('normal flight lacks an observed kilometre of approach')
    seat = stages['SEAT'][1].split('|')
    if len(seat) != 2 or seat[0] != 'controlled' or _handle(seat[1]) != source.ship:
        raise ValueError('normal seat confirmation identifies another ship')
    if not stages['AUTOPILOT'][1].startswith('started|') or stages['QUICKDOCK'][1] != 'dispatched':
        raise ValueError('native approach or docking command lacks its correlated receipt')
    docked, before, after = (FlightObservation.parse(stages[name][1])
                             for name in ('DOCKED', 'BEFORE_CONFIRM', 'AFTER_CONFIRM'))
    if (not all(sample.at_target(source) for sample in (docked, before, after))
            or not docked.bay == before.bay == after.bay
            or not source.age < docked.age <= before.age <= after.age or docked.distance >= source.distance):
        raise ValueError('pilot or physical docking observations disagree')
    if before.water != after.water or before.balance != after.balance:
        raise ValueError('cargo or Company account changed during the capture-only confirmation')
    intent_index, intent = _once(rows, 'INTENT')
    match = re.fullmatch(r'cluster_01_sector001_macro\|BIM-169\|water\|6\|([1-9][0-9]*)', intent)
    if not match or not stages['BEFORE_CONFIRM'][0] < intent_index < stages['DONE'][0]:
        raise ValueError('missing stock intent for six water at BIM-169')
    return {'client_intent_captured': True, 'purchase_executed': False, 'admitted': False,
            'start_distance_m': source.distance, 'docked_distance_m': docked.distance,
            'flight_game_seconds': float(docked.age - source.age), 'unit_price_cents': int(match[1]),
            'water_units': after.water, 'company_balance_cents': after.balance, 'records': rows}


def observe_player_flight(log, deadline, guard):
    rows = read_probe_rows(log, PREFIX, deadline, guard, max_records=1024,
                           error_markers=(b'[X4LAN] AUTH_REGISTRY_FULL_INSPECT_ERROR ',
                                          b'[X4LAN] AUTH_RECOVERY ERROR',
                                          b'[X4LAN] AUTH_CLIENT_SEAT_REFUSED '))
    return verify_player_flight(rows)
