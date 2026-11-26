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
    for event in ('shield_damaged', 'hull_damaged'):
        gate = f'<event_object_{event} group="State.$targets"/>\n      <check_value value="State.$armed"/>'
        if private.count(gate) != 1:
            raise ValueError('unexpected private combat damage gate')
        private = private.replace(gate, gate.replace('State.$armed"',
            'State.$armed and @player.controlled == event.object and @player.occupiedship == event.object"'))
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


def captured_events(receipt, log_text):
    """Read private damage callbacks from the offered checkpoint's active helm windows."""
    validate_receipt(receipt)
    hits, sequence_counts = [], {}
    start = (f"capturing epoch={receipt['registry_epoch']} index={receipt['piloted_ship_index']} "
             f"checkpoint={receipt['checkpoint_id']} sha256={receipt['checkpoint_sha256']} code=")
    ship_code = None
    observed_window = False
    lines = log_text.splitlines() if isinstance(log_text, str) else log_text
    for line in lines:
        _, status_marker, status = line.partition(_CAPTURE_STATUS)
        if status_marker:
            if status.startswith(('capturing ', 'stopped ')):
                ship_code = None
            if status.startswith(start):
                actor = re.fullmatch(r'([A-Z0-9]{3}-[A-Z0-9]{3}) owner=player', status[len(start):].strip())
                if actor:
                    ship_code, observed_window = actor[1], True
            continue
        source, marker, payload = line.partition(MD_EVENT_PREFIX)
        event = parse_md_event(payload) if marker and source.endswith(_EVENT_SOURCE) else None
        if event is None:
            continue
        sequence_counts[event.seq] = sequence_counts.get(event.seq, 0) + 1
        if (not ship_code or event.kind not in DAMAGE_KINDS
                or event.registry_epoch != receipt['registry_epoch'] or event.victim_reg != receipt['piloted_ship_index']
                or event.victim_code != ship_code or event.victim_owner != 'player'
                or not event.victim_ctrl or not event.victim_occ):
            continue
        hits.append(event)
    if not observed_window:
        raise ValueError('no active pilot capture for the offered checkpoint and seat')
    if any(sequence_counts[event.seq] != 1 for event in hits):
        raise ValueError('native combat sequence repeats in one capture log')
    return hits


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
