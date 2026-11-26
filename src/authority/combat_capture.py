"""Opt-in, read-only combat capture for an admitted Company pilot.

Diagnostic runs only. The overlay installs the native X4AuthorityCombatObserver
and a small UI module that, after a confirmed participant resume and helm check, arms it on
the offered world's registry epoch and watches only the admitted registry
index. The observer's AUTH_COMBAT_OBS lines in the pilot's own log are the
evidence. The local receipt binds the checkpoint offered to this pilot (ID and
save digest), that registry epoch and the admitted index to the captured
native hit sequences. It holds no ticket, lease, key or private path, and it
never decides damage. The host can verify the capture window and all its hits
against the provider's store in one pass over the offered save.
"""
import json
import os
from pathlib import Path
import re
import tempfile

from .combat_observation import (FORBIDDEN_LUA_MUTATION_CALLS, MD_EVENT_PREFIX, parse_md_event,
                                 validate_md_read_only)

FORMAT = 1
DAMAGE_KINDS = ('shield_damaged', 'hull_damaged')
OBSERVER_SCREEN = 'X4AuthorityClientCombatCapture'
_EVENT_SOURCE = f'[{OBSERVER_SCREEN}] '
_CAPTURE_STATUS = '[X4LAN] AUTH_COMBAT_CAPTURE '
_WATCH_SOURCE = f'Context:md.{OBSERVER_SCREEN}.Watch<'
_WATCH_CONTROL = '[X4LAN] AUTH_COMBAT_OBS_CTRL action=Watch '
CONTROLS = frozenset({'Arm', 'Watch', 'Disarm'})
_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_CHECKPOINT = re.compile(r'checkpoint-[0-9a-f]{32}')
_FIELDS = frozenset({'format', 'checkpoint_id', 'checkpoint_sha256', 'registry_epoch', 'piloted_ship_index', 'hits'})


def private_capture_observer(text):
    """Give the client capture its own observer state and one sequence per log."""
    source = 'X4AuthorityCombatObserver'
    reset = '      <set_value name="State.$seq" exact="0"/>\n'
    if f'<mdscript name="{source}"' not in text or text.count(reset) != 1:
        raise ValueError('unexpected source for the private client combat observer')
    private = text.replace(source, OBSERVER_SCREEN).replace(reset, '')
    if private.count(MD_EVENT_PREFIX) != 1:
        raise ValueError('unexpected number of native combat event emitters')
    private = private.replace(MD_EVENT_PREFIX, _EVENT_SOURCE + MD_EVENT_PREFIX)
    watch = 'State.$armed and @$ship.exists and $ship.isclass.ship'
    if private.count(watch) != 1:
        raise ValueError('unexpected private combat watch gate')
    private = private.replace(watch, watch + ' and @player.controlled == $ship and @player.occupiedship == $ship')
    helm_events = ('shield_damaged', 'hull_damaged', 'attacked_object')
    for event in helm_events:
        group = 'sources' if event == 'attacked_object' else 'targets'
        condition = f'<event_object_{event} group="State.${group}"'
        gate = condition + '/>\n      <check_value value="State.$armed"/>'
        if private.count(gate) != 1:
            raise ValueError(f'unexpected private combat gate for {event}')
        if event == 'attacked_object':
            condition += ' method="killmethod.hitbybullet"'
        private = private.replace(gate, condition + '/>\n      <check_value value="State.$armed'
            ' and @player.controlled == event.object and @player.occupiedship == event.object"/>')
    # X4's projectile callback is global and puts the victim in event.param.
    projectile_gate = 'State.$armed and State.$watch_player_ship"'
    if private.count(projectile_gate) != 1:
        raise ValueError('unexpected private projectile contact gate')
    private = private.replace(projectile_gate,
        'State.$armed and State.$watch_player_ship and State.$targets.indexof.{@event.param} gt 0'
        ' and @player.controlled == event.param and @player.occupiedship == event.param"')
    validate_md_read_only(private)
    return private


def _code(lua_text):
    return '\n'.join(line.split('--', 1)[0] for line in lua_text.splitlines())


def validate_capture_lua(lua_text):
    """The capture module may only send Arm, Watch and Disarm to the observer screen."""
    code = _code(lua_text)
    found = [call for call in FORBIDDEN_LUA_MUTATION_CALLS if call != 'AddUITriggeredEvent' and call in code]
    sends = re.findall(r'AddUITriggeredEvent\(([^,]*),', code)
    controls = set(re.findall(r"\bsend\('([^']*)'", code))
    if found or sends != [repr(OBSERVER_SCREEN)] or not controls or not controls <= CONTROLS:
        raise ValueError('combat capture may only control its private read-only observer')
    return {'read_only': True, 'controls': sorted(controls)}


def capture_files(observer_template, client_script):
    """Build the same passive observer and UI module for both pilot platforms."""
    observer = private_capture_observer(observer_template.decode('utf-8'))
    validate_capture_lua(client_script.decode('utf-8'))
    return {'md/client_combat_capture.xml': observer.encode('utf-8'),
            'ui/client_combat_capture.lua': client_script}


def _validate_binding(checkpoint_id, checkpoint_sha256, epoch, index):
    if (type(checkpoint_id) is not str or not _CHECKPOINT.fullmatch(checkpoint_id)
            or type(checkpoint_sha256) is not str or not _HEX64.fullmatch(checkpoint_sha256)
            or type(epoch) is not str or not _HEX32.fullmatch(epoch)
            or type(index) is not int or index < 1):
        raise ValueError('invalid checkpoint, registry epoch or pilot seat in combat capture')


def capture_config(full_registry, index, checkpoint_id, checkpoint_sha256):
    """Bind the observer to the exact offered save and requested Company seat."""
    epoch = full_registry['epoch']
    _validate_binding(checkpoint_id, checkpoint_sha256, epoch, index)
    if index > full_registry['issued_count'] or full_registry['operational_bits'][index - 1] != '1':
        raise ValueError('combat capture needs an operational admitted index on the offered registry')
    return (f',authority_combat_capture={{epoch={json.dumps(epoch)},index={index},'
            f'checkpoint={json.dumps(checkpoint_id)},sha256={json.dumps(checkpoint_sha256)}}}')


def capture_binding(checkpoint_id, checkpoint_sha256, registry_epoch, piloted_ship_index):
    """Identify the offered save and registered pilot seat in a session result."""
    _validate_binding(checkpoint_id, checkpoint_sha256, registry_epoch, piloted_ship_index)
    return {'format': FORMAT, 'checkpoint_id': checkpoint_id, 'checkpoint_sha256': checkpoint_sha256,
            'registry_epoch': registry_epoch, 'piloted_ship_index': piloted_ship_index}


def new_receipt(checkpoint_id, checkpoint_sha256, registry_epoch, piloted_ship_index):
    return dict(capture_binding(checkpoint_id, checkpoint_sha256, registry_epoch, piloted_ship_index), hits=[])


def _ship(isship, index, code):
    return {'index': index, 'code': code} if isship and index > 0 else None


def _incoming_damage(event, ship_index, ship_code):
    return (event.kind in DAMAGE_KINDS and event.victim_reg == ship_index
            and event.victim_code == ship_code and event.victim_owner == 'player'
            and event.victim_ctrl and event.victim_occ)


def _outgoing_gun_contact(event, ship_index, ship_code):
    return (event.kind == 'object_attacked_object' and event.param2 in ('hitbybullet', 'killmethod.hitbybullet')
            and event.ev_att_reg == ship_index and event.ev_att_code == ship_code
            and event.ev_att_owner == 'player' and event.ev_att_isship and event.ev_att_ctrl
            and event.victim_reg > 0 and event.victim_reg != ship_index)


def captured_outgoing_contacts(receipt, log_text):
    """Read X4's throttled gun-contact notifications at the admitted pilot's helm."""
    _, contacts = read_outgoing_capture(receipt, log_text)
    return contacts


def read_outgoing_capture(receipt, log_text):
    """Return the pilot's observed ship code even when no bullets hit a target."""
    ship_codes, contacts = _captured_events(receipt, log_text, _outgoing_gun_contact, require_source_watch=True)
    if len(ship_codes) != 1:
        raise ValueError('conflicting ship codes for the outgoing pilot capture')
    return ship_codes.pop(), contacts


def captured_events(receipt, log_text):
    """Read private damage callbacks on the admitted pilot's ship."""
    _, hits = _captured_events(receipt, log_text, _incoming_damage)
    return hits


def _watched_source_code(line, ship_index):
    """Read the private MD Watch receipt, including X4's numeric or text booleans."""
    prefix, watch, fields = line.partition(_WATCH_CONTROL)
    if not watch or _WATCH_SOURCE not in prefix:
        return None
    values = dict(token.split('=', 1) for token in fields.split() if '=' in token)
    if (values.get('source') in ('1', 'true') and values.get('owner') == 'player'
            and values.get('label') == 'admitted_pilot' and values.get('reg') == str(ship_index)):
        return values.get('code')
    return None


def _captured_events(receipt, log_text, matches_ship, *, require_source_watch=False):
    validate_receipt(receipt)
    hits, sequence_counts = [], {}
    start = (f"capturing epoch={receipt['registry_epoch']} index={receipt['piloted_ship_index']} "
             f"checkpoint={receipt['checkpoint_id']} sha256={receipt['checkpoint_sha256']} code=")
    ship_code = None
    source_watch_code = None
    observed_ship_codes = set()
    lines = log_text.splitlines() if isinstance(log_text, str) else log_text
    for line in lines:
        if require_source_watch and _WATCH_SOURCE in line:
            source_watch_code = _watched_source_code(line, receipt['piloted_ship_index'])
            continue
        _, status_marker, status = line.partition(_CAPTURE_STATUS)
        if status_marker:
            if status.startswith(('capturing ', 'stopped ')):
                ship_code = None
            if status.startswith(start):
                actor = re.fullmatch(r'([A-Z0-9]{3}-[A-Z0-9]{3}) owner=player', status[len(start):].strip())
                if actor:
                    ship_code = actor[1]
                    if require_source_watch and source_watch_code != ship_code:
                        raise ValueError('outgoing gun contacts were not confirmed enabled for this pilot capture')
                    observed_ship_codes.add(ship_code)
            source_watch_code = None
            continue
        source, marker, payload = line.partition(MD_EVENT_PREFIX)
        event = parse_md_event(payload) if marker and source.endswith(_EVENT_SOURCE) else None
        if event is None:
            continue
        sequence_counts[event.seq] = sequence_counts.get(event.seq, 0) + 1
        if (not ship_code or event.registry_epoch != receipt['registry_epoch']
                or not matches_ship(event, receipt['piloted_ship_index'], ship_code)):
            continue
        hits.append(event)
    if not observed_ship_codes:
        raise ValueError('no active pilot capture for the offered checkpoint and seat')
    if any(sequence_counts[event.seq] != 1 for event in hits):
        raise ValueError('native combat sequence repeats in one capture log')
    return observed_ship_codes, hits


def collect_hits(receipt, log_text):
    """Record damage callbacks from a text stream or an in-memory log, in order."""
    hits = []
    for event in captured_events(receipt, log_text):
        hits.append({
            'sequence': event.seq, 'event': event.kind, 'victim_code': event.victim_code,
            'victim_occupied_by_player': event.victim_occ, 'victim_controlled_by_player': event.victim_ctrl,
            # The native callback attacker and the victim's lastattacker stay separate.
            'event_attacker': _ship(event.ev_att_isship, event.ev_att_reg, event.ev_att_code),
            'last_attacker': _ship(event.last_att_isship, event.last_att_reg, event.last_att_code),
            'amount': event.amount, 'shield_after': event.shield, 'hull_after': event.hull,
        })
    return dict(receipt, hits=hits)


def validate_receipt(receipt):
    if (type(receipt) is not dict or receipt.keys() != _FIELDS
            or type(receipt['format']) is not int or receipt['format'] != FORMAT):
        raise ValueError('unexpected combat capture receipt')
    _validate_binding(receipt['checkpoint_id'], receipt['checkpoint_sha256'],
                      receipt['registry_epoch'], receipt['piloted_ship_index'])
    if type(receipt['hits']) is not list:
        raise ValueError('invalid combat hit list')
    return receipt


def verifier_arguments(receipt, store, log, sequence=None):
    """Recheck one sequence or the whole capture window against the original log and save."""
    validate_receipt(receipt)
    if sequence is not None and sequence not in {hit['sequence'] for hit in receipt['hits']}:
        raise ValueError('sequence was not captured on the admitted ship')
    arguments = ['--store', str(store), '--checkpoint', receipt['checkpoint_id'],
                 '--sha256', receipt['checkpoint_sha256'], '--log', str(log),
                 '--capture-epoch', receipt['registry_epoch'],
                 '--piloted-ship-index', str(receipt['piloted_ship_index'])]
    if sequence is not None:
        arguments.extend(['--sequence', str(sequence)])
    return arguments


def write_receipt(path, receipt):
    """Publish a complete receipt exactly once at the requested path."""
    validate_receipt(receipt)
    path = Path(path)
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        candidate = Path(staging) / 'receipt.json'
        with candidate.open('x', encoding='utf-8') as output:
            json.dump(receipt, output, indent=2, allow_nan=False)
            output.write('\n')
        # Windows rename and POSIX link both fail if another writer won the race.
        if os.name == 'nt':
            candidate.rename(path)
        else:
            os.link(candidate, path)
