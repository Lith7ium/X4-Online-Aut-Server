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


@dataclass(frozen=True)
class ProtectedStation:
    code: str
    sector_macro: str
    source_entry: str
    docks: int
    defence_modules: int
    turrets: int
    shield_generators: int


def protected_fallback_stations(checkpoint):
    """Return saved candidates in preference order. The caller verifies access in X4."""
    sector_depth = station_depth = excluded_depth = None
    station = None
    matches = []

    def finish_station():
        nonlocal station, station_depth, excluded_depth
        if station and station['source_entry'] in _STATIONS and _STATIONS[station['source_entry']] == station['macro']:
            if (station['docks'] and station['defence_modules'] and station['turrets']
                    and station['shield_generators']):
                matches.append(ProtectedStation(station['code'], BLACK_HOLE_SUN_V,
                    station['source_entry'], station['docks'], station['defence_modules'],
                    station['turrets'], station['shield_generators']))
        station = station_depth = excluded_depth = None

    def observe(tag, depth, attributes, _ship_index):
        nonlocal sector_depth, station_depth, excluded_depth, station
        if station_depth is not None and depth <= station_depth:
            finish_station()
        if sector_depth is not None and depth <= sector_depth:
            sector_depth = None
        component = attributes.get('class') if tag == 'component' else None
        if component == 'sector' and attributes.get('macro', '').lower() == BLACK_HOLE_SUN_V:
            sector_depth = depth
        if sector_depth is None:
            return
        if excluded_depth is not None:
            if depth > excluded_depth:
                return
            excluded_depth = None
        if station is None:
            code = attributes.get('code', '')
            if component == 'station' and attributes.get('owner') == 'argon' and _CODE.fullmatch(code):
                station_depth = depth
                station = dict(code=code, macro=attributes.get('macro'), source_entry=None,
                               docks=0, defence_modules=0, turrets=0, shield_generators=0)
        elif tag == 'source' and depth == station_depth + 1 and attributes.get('class') == 'godstation':
            station['source_entry'] = attributes.get('entry')
        elif component:
            if component.startswith('ship_') or component in ('station', 'buildstorage', 'spacesuit'):
                excluded_depth = depth  # Docked ships and their equipment belong to separate objects.
            elif component in _EQUIPMENT:
                station[_EQUIPMENT[component]] += 1

    verify_join_snapshot(checkpoint, observe_universe=observe)
    finish_station()
    priority = tuple(_STATIONS)
    return tuple(sorted(matches, key=lambda site: (priority.index(site.source_entry), site.code)))


_EQUIPMENT = {'dockarea': 'docks', 'defencemodule': 'defence_modules',
              'turret': 'turrets', 'shieldgenerator': 'shield_generators'}
