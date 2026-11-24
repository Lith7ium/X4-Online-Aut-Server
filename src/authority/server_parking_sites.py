"""Identify fortified, save-specific fallback stations for the dedicated avatar."""
from dataclasses import dataclass
import re

from .registry_availability_evidence import verify_join_snapshot

BLACK_HOLE_SUN_V = 'cluster_06_sector002_macro'
# The game's universe generator associates these entries with this sector.
# A station's display code and component ID are specific to one world.
_STATIONS = {
    'arg_defence_cluster_06_sector002': 'station_gen_factory_base_01_macro',
    'tradestation_argon_01': 'station_arg_tradestation_base_01_macro',
}
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_EQUIPMENT = {'dockarea': 'docks', 'defencemodule': 'defence_modules',
              'turret': 'turrets', 'shieldgenerator': 'shield_generators'}


@dataclass(frozen=True)
class ProtectedStation:
    code: str
    sector_macro: str
    source_entry: str
    docks: int
    defence_modules: int
    turrets: int
    shield_generators: int



class SavedProtectedStations:
    """Observe an existing registry verification pass, then collect its stations."""

    def __init__(self):
        self._sector_depth = self._station_depth = self._excluded_depth = None
        self._station = None
        self._matches = []

    def _finish_station(self):
        station = self._station
        if (station and station['source_entry'] in _STATIONS
                and _STATIONS[station['source_entry']] == station['macro']
                and all(station[name] for name in _EQUIPMENT.values())):
            self._matches.append(ProtectedStation(
                code=station['code'], sector_macro=BLACK_HOLE_SUN_V, source_entry=station['source_entry'],
                docks=station['docks'], defence_modules=station['defence_modules'],
                turrets=station['turrets'], shield_generators=station['shield_generators'],
            ))
        self._station = self._station_depth = self._excluded_depth = None

    def observe(self, tag, depth, attributes, _ship_index):
        if self._station_depth is not None and depth <= self._station_depth:
            self._finish_station()
        if self._sector_depth is not None and depth <= self._sector_depth:
            self._sector_depth = None
        component = attributes.get('class') if tag == 'component' else None
        if component == 'sector' and attributes.get('macro', '').lower() == BLACK_HOLE_SUN_V:
            self._sector_depth = depth
        if self._sector_depth is None:
            return
        if self._excluded_depth is not None:
            if depth > self._excluded_depth:
                return
            self._excluded_depth = None
        if self._station is None:
            code = attributes.get('code', '')
            if component == 'station' and attributes.get('owner') == 'argon' and _CODE.fullmatch(code):
                self._station_depth = depth
                self._station = dict(code=code, macro=attributes.get('macro'), source_entry=None,
                                     docks=0, defence_modules=0, turrets=0, shield_generators=0)
        elif tag == 'source' and depth == self._station_depth + 1 and attributes.get('class') == 'godstation':
            self._station['source_entry'] = attributes.get('entry')
        elif component:
            if component.startswith('ship_') or component in ('station', 'buildstorage', 'spacesuit'):
                self._excluded_depth = depth  # Attached ship equipment is separate from station equipment.
            elif component in _EQUIPMENT:
                self._station[_EQUIPMENT[component]] += 1

    def ranked(self):
        """Call after successful verification. Prefer the sector's defence platform."""
        self._finish_station()
        priority = tuple(_STATIONS)
        return tuple(sorted(self._matches, key=lambda site: (priority.index(site.source_entry), site.code)))


def protected_fallback_stations(checkpoint):
    """Return saved candidates in preference order. The caller verifies access in X4."""
    stations = SavedProtectedStations()
    verify_join_snapshot(checkpoint, observe_universe=stations.observe)
    return stations.ranked()
