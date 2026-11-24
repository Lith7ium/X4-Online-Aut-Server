"""One-shot station parking and read-only cold verification for the private seed."""
import json
from pathlib import Path
import re

from authority.registry_availability_evidence import verify_join_snapshot

ROOT = Path(__file__).resolve().parents[2]
STATION_SCRIPT = 'authority_station_parking.xml'
SHIP_SCRIPT = 'authority_station_parking_ships.xml'
TAG = b'[X4LAN] AUTH_STATION_PARKING '
SEED = 'ae92930bd5bfa8eed14a1c93ecb50e706897226243c160544494995ce0c934f6'
EPOCH, COUNT = '4ba7be438ebb04ec5bb85fdb95b5a488', 10082
FALLBACK_SECTOR = 'cluster_06_sector002_macro'
FALLBACK_CODES = ('XPJ-169', 'JZJ-119')
FALLBACK_MACROS = ('station_gen_factory_base_01_macro', 'station_arg_tradestation_base_01_macro')
SHIPS = (
    {'index': 1970, 'code': 'WOO-831', 'macro': 'ship_arg_s_scout_01_a_macro',
     'sector': 'cluster_14_sector001_macro'},
    {'index': 7354, 'code': 'WRN-302', 'macro': 'ship_arg_s_fighter_02_a_macro',
     'sector': 'cluster_01_sector001_macro'},
)
CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
MACRO = re.compile(r'[a-zA-Z0-9_]{1,128}')
DECLARATION = 'local registry_checkpoint, registry_checkpoint_pause_time, registry_checkpoint_started\n'
ACTIVATION = ' if not activated then\n'
UI = ('authority_registry_resolve_string.lua', 'authority_station_parking.lua')


def mode(args, *, require_snapshot, tls, company_ships, company_motion):
    choice = getattr(args, 'park_server_avatar', None)
    if choice is not None and (choice not in ('prepare', 'verify') or not require_snapshot
                              or tls is not None or company_ships or company_motion):
        raise ValueError('station parking requires isolated format-3 recovery')
    return choice


def _location(raw):
    if (type(raw) is not dict or set(raw) != {'kind', 'code', 'sector', 'room'}
            or raw['kind'] not in ('HQ', 'FALLBACK')
            or not all(type(raw[name]) is str for name in ('code', 'sector', 'room'))
            or not CODE.fullmatch(raw['code']) or not MACRO.fullmatch(raw['sector'])
            or not MACRO.fullmatch(raw['room'])
            or (raw['kind'] == 'FALLBACK' and
                (raw['code'] not in FALLBACK_CODES or raw['sector'] != FALLBACK_SECTOR))):
        raise ValueError('invalid parking location')
    return raw


def source_location(choice, checkpoint, source_result):
    if choice == 'prepare':
        if checkpoint.sha256 != SEED:
            raise ValueError('station parking requires pinned two-company seed')
        return None
    data = source_result.get('station_parking')
    if (type(data) is not dict or data.get('mode') != 'prepare'
            or data.get('source_sha256') != SEED or data.get('native_parked') is not True
            or 'park_cue_sha256' not in data):
        raise ValueError('cold parking verification requires original preparation provenance')
    return _location(data.get('location'))


def verify_saved(checkpoint, expected=None):
    """Verify the registered hulls and the saved player/station/room ancestry."""
    ancestors, ships, players, stations = [], {}, [], []

    def observe(element, depth, attrs, index):
        while ancestors and ancestors[-1][0] >= depth:
            ancestors.pop()
        if element != 'component':
            return
        kind = attrs.get('class', '')
        ancestors.append((depth, (kind, attrs.get('macro'), attrs.get('code'))))
        if index is None and kind != 'player' and not (expected and attrs.get('code') == expected['code']):
            return
        sector = next((fields[1] for _, fields in reversed(ancestors) if fields[0] == 'sector'), None)
        if index is not None and attrs.get('owner') == 'player':
            ships[index] = (attrs.get('code'), attrs.get('macro'), sector)
        if kind == 'player':
            players.append((attrs.get('owner'), sector, tuple(fields for _, fields in ancestors[:-1])))
        if expected and attrs.get('code') == expected['code'] and kind.startswith('station'):
            stations.append((attrs.get('owner'), attrs.get('macro'), sector))

    snapshot = verify_join_snapshot(checkpoint, observe_universe=observe)
    if ((snapshot['epoch'], snapshot['issued_count']) != (EPOCH, COUNT)
            or ships != {s['index']: (s['code'], s['macro'], s['sector']) for s in SHIPS}
            or len(players) != 1 or players[0][0] != 'player'):
        raise ValueError('saved parking fixture changed registry or Company hulls')
    if expected is not None:
        owned, sector, ancestors = players[0]
        station_ancestors = {code for kind, _, code in ancestors if kind.startswith('station')}
        if (len(stations) != 1 or stations[0][2] != expected['sector']
                or sector != expected['sector'] or station_ancestors != {expected['code']}
                or any(kind.startswith('ship_') for kind, _, _ in ancestors)
                or expected['room'] not in (macro for _, macro, _ in ancestors)):
            raise ValueError(f'saved player room / station differs: {sector}, {ancestors}')
        if expected['kind'] == 'FALLBACK':
            fallback = FALLBACK_CODES.index(expected['code'])
            if stations[0][:2] != ('argon', FALLBACK_MACROS[fallback]):
                raise ValueError('saved fallback station owner or macro changed')
        elif stations[0][:2] != ('player', 'station_pla_headquarters_base_01_macro'):
            raise ValueError('saved HQ owner or macro changed')
    return {'company_ships': [dict(ship) for ship in SHIPS],
            'epoch': EPOCH, 'count': COUNT,
            'station': {'owner': stations[0][0], 'macro': stations[0][1]} if expected else None}


def _lua(value):
    if isinstance(value, dict):
        return '{' + ','.join(key + '=' + _lua(entry) for key, entry in value.items()) + '}'
    if isinstance(value, (tuple, list)):
        return '{' + ','.join(map(_lua, value)) + '}'
    return json.dumps(value, ensure_ascii=True)


def install(case, nonce, choice, expected):
    if not re.fullmatch(r'[0-9a-f]{32}', nonce):
        raise ValueError('invalid station parking nonce')
    lab = Path(case) / 'extensions/x4_lan_lab'
    probe = lab / 'ui/authority_world_bridge_probe.lua'
    manifest = lab / 'ui.xml'
    text = probe.read_text(encoding='ascii')
    ui = manifest.read_text(encoding='ascii')
    marker = '    <file name="ui/authority_world_bridge_probe.lua" />\n'
    if text.count(DECLARATION) != 1 or text.count(ACTIVATION) != 1 or ui.count(marker) != 1:
        raise ValueError('station parking requires registry bridge')
    station = (ROOT / 'tools/dev' / STATION_SCRIPT).read_bytes()
    if choice == 'verify':
        _location(expected)
        station, count = re.subn(rb'(?ms)^([ \t]*)<cue name="Park"[^>]*>.*?^\1</cue>\s*\n', b'', station)
        if count != 1 or b'<teleport_player ' in station:
            raise ValueError('cold parking probe must never contain a teleport action')
    files = {lab / 'md' / STATION_SCRIPT: station,
             lab / 'md' / SHIP_SCRIPT: (ROOT / 'tools/dev' / SHIP_SCRIPT).read_bytes()}
    files.update({lab / 'ui' / name: (ROOT / 'tools/dev' / name).read_bytes() for name in UI})
    config = dict(mode=choice, nonce=nonce, epoch=EPOCH, count=COUNT, ships=SHIPS)
    if choice == 'prepare':
        config.update(sector=FALLBACK_SECTOR, codes=FALLBACK_CODES,
                      owner='argon', macros=FALLBACK_MACROS)
    else:
        config['expected'] = expected
    hook = f''' if not activated then
  if not parking then
   parking=X4LAN_AUTHORITY_STATION_PARKING.new_x4({_lua(config)},function()
    return registry_checkpoint:ready() and C.IsGamePaused() and engine.game_time() == registry_checkpoint_pause_time
   end)
  end
  if not parking() then return end
 end
'''
    for path, data in files.items():
        with path.open('xb') as output:
            output.write(data)
    probe.write_text(text.replace(DECLARATION, DECLARATION + 'local parking\n').replace(
        ACTIVATION, hook + ACTIVATION), encoding='ascii')
    manifest.write_text(ui.replace(marker, ''.join(f'    <file name="ui/{name}" />\n'
                                                   for name in UI) + marker), encoding='ascii')
    return [*files, probe, manifest]


def verify_log(path, nonce, read_evidence):
    raw = read_evidence(path, 64 << 20)
    rows = [line.split(TAG, 1)[1].strip() for line in raw.splitlines() if TAG in line]
    if len(rows) != 1 or not rows[0].startswith((nonce + '|PARKED|').encode('ascii')):
        raise ValueError('station parking receipt missing, refused or duplicated')
    parts = rows[0].decode('ascii').split('|')
    return _location(dict(zip(('kind', 'code', 'sector', 'room'), parts[2:], strict=True)))


def archive_cues(out, case, read_expected, digest):
    """Keep the executed test scripts outside the reusable game extension."""
    folder = Path(case) / 'extensions/x4_lan_lab/md'
    for name in (STATION_SCRIPT, SHIP_SCRIPT):
        path = folder / name
        archived = Path(out) / name
        path.rename(archived)
        found = digest(archived)
        if found != read_expected[path]:
            raise ValueError('parked fixture test script changed')
    return digest(Path(out) / STATION_SCRIPT)
