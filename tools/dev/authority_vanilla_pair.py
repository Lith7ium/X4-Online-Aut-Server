"""Place base-game fighters, discover a parking station and verify their cold identities."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from authority_company_shuttle_pair import verify_flight_pair
from authority_station_parking import ACTIVATION, CODE, DECLARATION, MACRO, _lua

DEV = Path(__file__).resolve().parent
LOADOUT = DEV/'fixtures/vanilla-flight/libraries/loadouts.xml'
CREATE = 'authority_vanilla_pair_create.xml'
INSPECT = 'authority_vanilla_pair_inspect.xml'
SECTOR = 'cluster_06_sector002_macro'
UI = ('authority_registry_resolve_string.lua', 'authority_company_shuttle_pair.lua')
TAG = b'[X4LAN] AUTH_VANILLA_PAIR '


def _receipt_kind(fighter_count):
    if type(fighter_count) is not int or fighter_count not in (2, 4):
        raise ValueError('expected two or four vanilla fighters')
    return 'PAIR' if fighter_count == 2 else 'GROUP'


def _checked(pair):
    if (not isinstance(pair, dict) or set(pair) != {'codes','station','room','sector'}
            or pair['sector'] != SECTOR or not isinstance(pair['station'], str) or not CODE.fullmatch(pair['station'])
            or not isinstance(pair['room'], str) or not MACRO.fullmatch(pair['room']) or not isinstance(pair['codes'], list)
            or len(pair['codes']) not in (2, 4)
            or any(not isinstance(c, str) or not CODE.fullmatch(c) for c in pair['codes'])
            or len(set(pair['codes'])) != len(pair['codes'])):
        raise ValueError('invalid native vanilla pair receipt')
    return pair


def verify_genesis_log(log, nonce, read_evidence, *, fighter_count=2):
    kind = _receipt_kind(fighter_count)
    rows = [line.split(TAG, 1)[1].strip() for line in read_evidence(log, 64 << 20).splitlines() if TAG in line]
    if len(rows) != 1:
        raise ValueError(f'expected a single native fighter receipt, got {len(rows)}')
    fields = rows[0].decode('ascii').split('|')
    if len(fields) != fighter_count + 4 or fields[:2] != [nonce, kind]:
        raise ValueError(f'native fighter inspection failed: {rows[0][:192]!r}')
    return _checked(dict(codes=fields[2:-2], station=fields[-2], room=fields[-1], sector=SECTOR))


def verify_saved(checkpoint, pair):
    pair = _checked(pair)
    result, snapshot = verify_flight_pair(checkpoint, pair['codes'], loadout_path=LOADOUT,
        sector_macro=SECTOR, station_code=pair['station'], room_macro=pair['room'])
    result.update(epoch=snapshot['epoch'], issued_count=snapshot['issued_count'])
    return result


def _install(case, nonce, mode, pair=None, ships=None, *, fighter_count=2):
    if re.fullmatch('[0-9a-f]{32}', nonce) is None:
        raise ValueError('invalid vanilla fixture nonce')
    lab = Path(case)/'extensions/x4_lan_lab'
    manifest = lab/'ui.xml'
    marker = '    <file name="ui/authority_world_bridge_probe.lua" />'
    ui = manifest.read_text('utf-8')
    if ui.count(marker) != 1:
        raise ValueError('vanilla pair requires the world bridge')
    config = dict(nonce=nonce, mode=mode, live_station=True, tag='AUTH_VANILLA_PAIR',
                  screen='X4AuthorityVanillaPair',
                  software=[n.attrib['ware'] for n in ET.parse(LOADOUT).findall('./add/loadout/software/software')])
    files = {lab/'md'/INSPECT: DEV/INSPECT}
    if mode == 'prepare':
        files[lab/'md'/CREATE] = DEV/CREATE
        files[lab/'libraries/loadouts.xml'] = LOADOUT
        config['indices'] = list(range(1, fighter_count + 1))
    else:
        pair = _checked(pair)
        if ([s['code'] for s in ships] != pair['codes'] or any(type(s['index']) is not int or s['index'] < 1 for s in ships)
                or len({s['index'] for s in ships}) != len(pair['codes'])):
            raise ValueError('cold fighter registry indices differ')
        config.update(codes=pair['codes'], station=pair['station'], room=pair['room'], indices=[s['index'] for s in ships])
    includes = []
    for name in UI:
        target = lab/'ui'/name
        include = f'    <file name="ui/{name}" />'
        if include in ui:
            if target.read_bytes() != (DEV/name).read_bytes():
                raise ValueError('conflicting vanilla fixture UI file')
        else:
            files[target] = DEV/name
            includes.append(include)
    for target, source in files.items():
        target.parent.mkdir(exist_ok=True, parents=True)
        with target.open('xb') as output:
            output.write(source.read_bytes())
    # The wrapper pins all fixture settings to the generated private overlay.
    wrapper = lab/'ui/authority_vanilla_pair.lua'
    with wrapper.open('x', encoding='ascii') as output:
        output.write('X4LAN_VANILLA_PAIR = { new_x4 = function(request, guard)\n'
                     f' local config = {_lua(config)}\n'
                     ' assert(request.mode == config.mode and request.nonce == config.nonce)\n'
                     ' return X4LAN_COMPANY_SHUTTLE_PAIR.new_x4(config,guard)\nend }\n')
    includes.append('    <file name="ui/authority_vanilla_pair.lua" />')
    manifest.write_text(ui.replace(marker,'\n'.join(includes+[marker])), 'utf-8')
    return [*files,wrapper,manifest]


def install_genesis(case, profile, nonce, *, fighter_count=2):
    _receipt_kind(fighter_count)
    return _install(case, nonce, 'prepare', fighter_count=fighter_count)


def install_cold(case, profile, nonce, pair, ships):
    probe = Path(case)/'extensions/x4_lan_lab/ui/authority_world_bridge_probe.lua'
    text = probe.read_text('ascii')
    if text.count(DECLARATION) != 1 or text.count(ACTIVATION) != 1:
        raise ValueError('cold fighter inspection requires the format-3 recovery gate')
    files = _install(case, nonce, 'verify', pair, ships)
    hook = f""" if not activated and not fighter_checked then
  if not fighter_tick then
   fighter_tick=X4LAN_VANILLA_PAIR.new_x4({{nonce="{nonce}",mode="verify"}},function()
    return registry_checkpoint:ready() and C.IsGamePaused() and engine.game_time() == registry_checkpoint_pause_time
   end)
  end
  if not fighter_tick() then return end
  fighter_checked=true
 end
"""
    probe.write_text(text.replace(DECLARATION,DECLARATION+'local fighter_tick, fighter_checked\n')
                     .replace(ACTIVATION,hook+ACTIVATION),'ascii')
    return [*files,probe]


def archive_genesis(case, out):
    lab = Path(case)/'extensions/x4_lan_lab'
    paths = [lab/'md'/INSPECT]
    if (lab/'md'/CREATE).exists():
        paths.extend([lab/'md'/CREATE,lab/'libraries/loadouts.xml'])
    for path in paths:
        path.rename(Path(out)/path.name)
