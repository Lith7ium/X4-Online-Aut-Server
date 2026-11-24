"""Stage and observe the private stock purchase menu without booking a trade."""
from pathlib import Path
import re
import shutil
import time


ROOT = Path(__file__).resolve().parents[2]
PREFIX = b'[X4LAN] AUTH_TRADE_COCKPIT '


def _insert(path, anchor, replacement):
    contents = path.read_text(encoding='utf-8')
    if contents.count(anchor) != 1:
        raise ValueError(f'missing private cockpit hook in {path.name}')
    path.write_text(contents.replace(anchor, replacement), encoding='utf-8')


def install(case, mode):
    if mode not in ('offer', 'cockpit'):
        raise ValueError('unknown stock purchase inspection mode')
    extension = case / 'extensions/x4_lan_lab'
    modules = (
        'tools/dev/authority_registry_resolve_string.lua',
        'src/authority/lua/company_trade_capture.lua',
        'tools/dev/authority_trade_cockpit_probe.lua',
    )
    cue = extension / 'md/authority_trade_cockpit_probe.xml'
    shutil.copyfile(ROOT / 'tools/dev' / cue.name, cue)
    installed = [cue]
    for source in modules:
        target = extension / 'ui' / Path(source).name
        shutil.copyfile(ROOT / source, target)
        installed.append(target)
    manifest = extension / 'ui.xml'
    anchor = '    <file name="ui/authority_recovery_probe.lua" />'
    manifests = ''.join(f'    <file name="ui/{Path(source).name}" />\n' for source in modules)
    _insert(manifest, anchor, manifests + anchor)
    config = extension / 'ui/config.lua'
    with config.open('a', encoding='ascii') as stream:
        stream.write(f"X4LAN_CONFIG.authority_trade_probe_mode = '{mode}'\n")
    recovery = extension / 'ui/authority_recovery_probe.lua'
    _insert(recovery, " if state=='done' then return end",
            " if state=='done' then X4LAN_RECOVERY_ON_PAUSED(); return end")
    return [*installed, manifest, config, recovery]


def result_from_rows(rows, mode):
    """Report each observed stage independently, including a denied seat."""
    if not rows or not rows[-1].startswith('DONE|'):
        raise ValueError(f'incomplete native stock menu inspection: {rows}')
    if any(row.startswith(('DONE|', 'ERROR|')) for row in rows[:-1]):
        raise ValueError('unexpected terminal stock menu receipt')
    terminal = rows[-1].split('|', 1)[1]
    allowed = {'offer_read'} if mode == 'offer' else {'intent_captured', 'seat_unavailable'}
    if terminal not in allowed:
        raise ValueError(f'unexpected stock menu outcome: {terminal}')
    offer = [row for row in rows if row.startswith('OFFER|')]
    if len(offer) != 1:
        raise ValueError('missing or repeated stock offer')
    match = re.fullmatch(r'OFFER\|([1-9][0-9]*)\|([0-9]+)\|([0-9]+)\|([01])', offer[0])
    if not match:
        raise ValueError('invalid stock offer')
    cents, amount, minimum, can_trade = map(int, match.groups())
    inspections = [row.split('|') for row in rows[:rows.index(offer[0])] if row.startswith('INSPECT|READY|')]
    if (len(inspections) != 1 or len(inspections[0]) != 9
            or not all(re.fullmatch(r'0x[0-9a-fA-F]+', handle) for handle in inspections[0][2:5])):
        raise ValueError('stock offer lacks a prior native ship and station binding')
    intents = [row for row in rows if row.startswith('INTENT|')]
    if terminal == 'intent_captured':
        if (len(intents) != 1 or
                not re.fullmatch(r'INTENT\|cluster_01_sector001_macro\|BIM-169\|water\|6\|[1-9][0-9]*',
                                 intents[0])):
            raise ValueError('stock purchase confirmation lacks a matching client intent')
        before_intent = rows[:rows.index(intents[0])]
        ship, station, dock, captain = inspections[0][2:6]
        permission = [row for row in before_intent if re.fullmatch(r'PERMISSION\|[^|]+\|granted', row)]
        take = [row for row in before_intent if row.startswith(f'TAKE|DISPATCHED|{ship}|{station}|{dock}|')]
        controlled = f'VERIFY|CONTROLLED|{ship}|{station}|{dock}|{captain}|{ship}|{ship}|{ship}'
        if (len(permission) != 1 or len(take) != 1 or controlled not in before_intent
                or not before_intent.index(permission[0]) < before_intent.index(take[0]) < before_intent.index(controlled)):
            raise ValueError('stock purchase confirmation lacks permission, takeover or matching native cockpit')
    elif intents:
        raise ValueError('unexpected stock purchase intent')
    return {'outcome': terminal, 'unit_price_cents': cents, 'available_units': amount,
            'minimum_units': minimum, 'can_trade_six': bool(can_trade),
            'client_intent_captured': bool(intents), 'records': rows}


def observe(log, mode, deadline, guard):
    rows = []
    pending = b''
    with log.open('rb') as stream:
        while time.monotonic() < deadline:
            guard()
            chunk = stream.read(256 << 10)
            if chunk:
                lines = (pending + chunk).split(b'\n')
                pending = lines.pop()
                if len(pending) > 65536:
                    raise ValueError('unbounded stock-menu log line')
                for line in lines:
                    if PREFIX not in line:
                        continue
                    row = line.split(PREFIX, 1)[1].strip().decode('ascii')
                    rows.append(row)
                    if len(rows) > 160 or row.startswith('ERROR|'):
                        raise ValueError(f'native stock menu inspection stopped: {rows}')
                    if row.startswith('DONE|'):
                        return result_from_rows(rows, mode)
            else:
                time.sleep(.25)
    raise TimeoutError(f'native stock menu inspection timed out: {rows}')
