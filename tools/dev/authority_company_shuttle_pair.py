"""Native preparation and cold inspection of the two-ship human flight seed."""
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from authority.registry_availability_evidence import verify_join_snapshot
from authority_station_parking import ACTIVATION, CODE, DECLARATION, EPOCH, _lua

DEV = Path(__file__).resolve().parent
SOURCE = '68dc79959984dbd3499b7b6360bde5b0e24d5cbc845b49d24a908d043a7612ca'
SECTOR = 'cluster_06_sector002_macro'
HULL = 'ship_ter_s_xperimental_01_a_macro'
INDICES = (10083, 10084)
CREATE = 'authority_company_shuttle_create.xml'
INSPECT = 'authority_company_shuttle_inspect.xml'
LOADOUT = DEV / 'fixtures/human-flight/libraries/loadouts.xml'
UI = ('authority_registry_resolve_string.lua', 'authority_company_shuttle_pair.lua')
TAG = b'[X4LAN] AUTH_COMPANY_SHUTTLE_PAIR '


def _pair(codes):
    if (len(codes) != 2 or not all(isinstance(code, str) and CODE.fullmatch(code) for code in codes)
            or codes[0] == codes[1]):
        raise ValueError('expected two distinct shuttle codes')
    return {'sector': SECTOR, 'ships': [
        dict(index=index, code=code, macro=HULL, sector=SECTOR)
        for index, code in zip(INDICES, codes, strict=True)]}


def _checked(pair):
    if not isinstance(pair, dict) or not isinstance(pair.get('ships'), list):
        raise ValueError('invalid shuttle pair receipt')
    codes = [entry.get('code') if isinstance(entry, dict) else None for entry in pair['ships']]
    if pair != _pair(codes):
        raise ValueError('shuttle pair descriptor changed')
    return pair


def source_expected(choice, checkpoint, source_result):
    if choice == 'prepare':
        if checkpoint.sha256 != SOURCE:
            raise ValueError('shuttle preparation requires the pinned parked seed')
        return None
    proof = source_result.get('company_shuttle_pair')
    if (choice != 'verify' or source_result.get('success') is not True
            or source_result.get('final_checkpoint_sha256') != checkpoint.sha256
            or not isinstance(proof, dict) or proof.get('mode') != 'prepare'
            or proof.get('source_sha256') != SOURCE or proof.get('native_candidates') is not True
            or proof.get('cue_sha256') != hashlib.sha256((DEV / CREATE).read_bytes()).hexdigest()):
        raise ValueError('cold shuttle verification requires a confirmed preparation receipt')
    return _checked(proof.get('pair'))


def install(case, nonce, choice, expected):
    if not re.fullmatch('[a-f0-9]{32}', nonce) or choice not in ('prepare', 'verify'):
        raise ValueError('invalid shuttle overlay configuration')
    loadout = ET.parse(LOADOUT).find('./add/loadout')
    config = dict(nonce=nonce, epoch=EPOCH, mode=choice,
                  software=[node.attrib['ware'] for node in loadout.find('software')])
    if choice == 'verify':
        config['codes'] = [ship['code'] for ship in _checked(expected)['ships']]
    lab = Path(case) / 'extensions/x4_lan_lab'
    probe = lab / 'ui/authority_world_bridge_probe.lua'
    manifest = lab / 'ui.xml'
    text, ui = probe.read_text('ascii'), manifest.read_text('ascii')
    marker = '    <file name="ui/authority_world_bridge_probe.lua" />\n'
    gate = 'registry_checkpoint = X4LAN_AUTHORITY_REGISTRY_CHECKPOINT_GATE.new({\n'
    if (text.count(DECLARATION) != 1 or text.count(ACTIVATION) != 1 or text.count(gate) != 1
            or ui.count(marker) != 1):
        raise ValueError('company shuttle pair requires the format-3 recovery bridge')
    files = {lab/'md'/INSPECT: DEV/INSPECT, **{lab/'ui'/name: DEV/name for name in UI}}
    completion = ''
    if choice == 'prepare':
        files[lab/'md'/CREATE] = DEV/CREATE
        files[lab/'libraries/loadouts.xml'] = LOADOUT
        text = text.replace(gate, gate + '   company_shuttle_seed=true,\n')
        completion = "  assert(registry_checkpoint:accept_company_shuttle_pair(), 'registry append refused')\n"
    hook = f''' if not activated and not shuttle_checked then
  if not shuttle_pair then
   shuttle_pair=X4LAN_COMPANY_SHUTTLE_PAIR.new_x4({_lua(config)},function()
    return registry_checkpoint:ready() and C.IsGamePaused() and engine.game_time() == registry_checkpoint_pause_time
   end)
  end
  if not shuttle_pair() then return end
{completion}  shuttle_checked=true
 end
'''
    for target, source in files.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(source.read_bytes())
    probe.write_text(text.replace(DECLARATION, DECLARATION + 'local shuttle_pair, shuttle_checked\n').replace(
        ACTIVATION, hook + ACTIVATION), 'ascii')
    manifest.write_text(ui.replace(marker, ''.join(f'    <file name="ui/{name}" />\n' for name in UI)
                                   + marker), 'ascii')
    return [*files, probe, manifest]


def verify_log(path, nonce, read_evidence):
    rows = [line.split(TAG, 1)[1].strip() for line in read_evidence(path, 64 << 20).splitlines()
            if TAG in line]
    if len(rows) != 1:
        raise ValueError(f'expected one shuttle receipt, got {len(rows)}')
    fields = rows[0].decode('ascii').split('|')
    if len(fields) != 4 or fields[:2] != [nonce, 'PAIR']:
        raise ValueError(f'shuttle readback failed: {rows[0][:200]!r}')
    return _pair(fields[2:])


def verify_saved(checkpoint, pair):
    ships = _checked(pair)['ships']
    result, snapshot = verify_flight_pair(checkpoint, [ship['code'] for ship in ships],
        loadout_path=LOADOUT, sector_macro=SECTOR, station_code='XPJ-169', room_macro='dockingbay_arg_s_01_macro')
    if ([ship['index'] for ship in result['ships']] != list(INDICES)
            or (snapshot['epoch'], snapshot['issued_count']) != (EPOCH, INDICES[-1])):
        raise ValueError('saved format-3 shuttle identities differ')
    return result


def verify_flight_pair(checkpoint, codes, *, loadout_path, sector_macro, station_code, room_macro):
    """Resolve the native codes from the verified snapshot and inspect their equipment."""
    loadout = ET.parse(loadout_path).find('./add/loadout')
    hull = loadout.attrib['macro']
    mounts = {node.attrib['path'].removeprefix('../'): node.attrib['macro']
              for node in loadout.find('macros')}
    software = sorted(node.attrib['ware'] for node in loadout.find('software'))
    thruster = loadout.find('virtualmacros/thruster').attrib['macro']
    components, ships, avatars, owned_shuttles = [], {}, [], []
    ship = ship_depth = mount = None

    def observe(tag, depth, attrs, index):
        nonlocal ship, ship_depth, mount
        if ship is not None:
            offset = depth - ship_depth
            if offset <= 0:
                ship = ship_depth = mount = None
            elif offset == 1 and tag == 'software':
                if ship['software'] is not None:
                    raise ValueError('duplicate shuttle software list')
                ship['software'] = sorted(attrs.get('wares', '').split())
            elif offset == 2 and tag == 'connection':
                mount = attrs.get('connection')
            elif offset == 3 and tag == 'component' and mount in mounts:
                if mount in ship['mounts']:
                    raise ValueError('duplicate shuttle mount ' + mount)
                ship['mounts'][mount] = attrs.get('macro')
        if tag != 'component':
            return
        while components and components[-1][0] >= depth:
            components.pop()
        kind = attrs.get('class', '')
        if attrs.get('owner') == 'player' and attrs.get('macro') == hull:
            owned_shuttles.append(index)
        code = attrs.get('code')
        if code in codes or kind == 'player':
            sector = next((a.get('macro') for _, a in reversed(components) if a.get('class') == 'sector'), None)
            if sector != sector_macro:
                raise ValueError('shuttle or avatar in unexpected sector')
            if kind == 'player':
                stations = [a.get('code') for _, a in components if a.get('class', '').startswith('station')]
                rooms = [a.get('macro') for _, a in components if a.get('class') in ('dockingbay','room')]
                if (stations != [station_code] or room_macro not in rooms
                        or any(a.get('class', '').startswith('ship_') for _, a in components)):
                    raise ValueError('server avatar is outside its parking room')
                avatars.append(attrs.get('owner'))
            else:
                if (type(index) is not int or index < 1 or code in ships or attrs.get('macro') != hull
                        or attrs.get('owner') != 'player' or attrs.get('connection') != 'space'
                        or attrs.get('thruster') != thruster or components[-1][1].get('class') not in ('zone','sector')):
                    raise ValueError(f'invalid saved shuttle at registry index {index}')
                ship = {'index': index, 'code': attrs['code'], 'mounts': {}, 'software': None,
                        'thruster': thruster}
                ships[code], ship_depth, mount = ship, depth, None
        components.append((depth, attrs))

    snapshot = verify_join_snapshot(checkpoint, observe_universe=observe)
    ship_indices = {ship['index'] for ship in ships.values()}
    if (set(ships) != set(codes) or len(ship_indices) != len(codes) or len(owned_shuttles) != len(codes)
            or set(owned_shuttles) != ship_indices or avatars != ['player']):
        raise ValueError('saved format-3 ship identities or parked avatar differ')
    for ship in ships.values():
        index = ship['index']
        # Stock saves can omit factory-default software. The native inspection
        # reads the current version of every slot, including both hull defaults.
        recorded = ship['software']
        defaults = {'software_flightassistmk1', 'software_targetmk1'}
        if (ship['mounts'] != mounts or recorded is None or len(set(recorded)) != len(recorded)
                or set(recorded) - set(software) or set(software) - set(recorded) - defaults):
            raise ValueError(f'saved shuttle equipment differs at {index}: {ship!r}')
    return {'ships': [ships[code] for code in codes], 'sector': sector_macro, 'parked_station': station_code}, snapshot


def archive_cues(out, case, read_expected, digest):
    lab = Path(case) / 'extensions/x4_lan_lab'
    paths = [lab/'md'/INSPECT]
    if (lab/'md'/CREATE).exists():
        paths.extend([lab/'libraries/loadouts.xml', lab/'md'/CREATE])
    for path in paths:
        archived = Path(out) / path.name
        path.rename(archived)
        if digest(archived) != read_expected[path]:
            raise ValueError('executed shuttle fixture changed')
    return digest(archived)
