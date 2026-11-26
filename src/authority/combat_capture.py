"""Opt-in, read-only combat capture for an admitted Windows Company pilot.

Diagnostic runs only. The overlay installs the native X4AuthorityCombatObserver
and a small UI module that, after the seat gate admits the pilot, arms it on
the offered world's registry epoch and watches only the admitted registry
index. The observer's AUTH_COMBAT_OBS lines in the pilot's own log are the
evidence. The local receipt binds the checkpoint offered to this pilot (ID and
save digest), that registry epoch and the admitted index to the captured
native hit sequences. It holds no ticket, lease, key or private path, and it
never decides damage: tools/dev/authority_combat_hit_evidence.py with
--piloted-ship-index verifies each sequence against the provider's store.
"""
import json
import os
from pathlib import Path
import re
import tempfile

from .combat_observation import FORBIDDEN_LUA_MUTATION_CALLS, MD_EVENT_PREFIX, parse_md_event

FORMAT = 1
DAMAGE_KINDS = ('shield_damaged', 'hull_damaged')
OBSERVER_SCREEN = 'X4AuthorityCombatObserver'
CONTROLS = frozenset({'Arm', 'Watch'})
_HEX32 = re.compile(r'[0-9a-f]{32}')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_CHECKPOINT = re.compile(r'checkpoint-[0-9a-f]{32}')
_FIELDS = frozenset({'format', 'checkpoint_id', 'checkpoint_sha256', 'registry_epoch', 'piloted_ship_index', 'hits'})


def _code(lua_text):
    return '\n'.join(line.split('--', 1)[0] for line in lua_text.splitlines())


def validate_capture_lua(lua_text):
    """The capture module may only send Arm and Watch to the observer screen."""
    code = _code(lua_text)
    found = [call for call in FORBIDDEN_LUA_MUTATION_CALLS if call != 'AddUITriggeredEvent' and call in code]
    sends = re.findall(r'AddUITriggeredEvent\(([^,]*),', code)
    controls = set(re.findall(r"\bsend\('([^']*)'", code))
    if found or sends != [repr(OBSERVER_SCREEN)] or not controls or not controls <= CONTROLS:
        raise ValueError('combat capture may only arm and watch the read-only observer')
    return {'read_only': True, 'controls': sorted(controls)}


def capture_config(full_registry, index):
    """Lua config fragment for the admitted seat on the offered registry."""
    epoch = full_registry['epoch']
    if (type(epoch) is not str or not _HEX32.fullmatch(epoch) or type(index) is not int
            or not 1 <= index <= full_registry['issued_count']
            or full_registry['operational_bits'][index - 1] != '1'):
        raise ValueError('combat capture needs an operational admitted index on the offered registry')
    return f',authority_combat_capture={{epoch={json.dumps(epoch)},index={index}}}'


def new_receipt(checkpoint_id, checkpoint_sha256, registry_epoch, piloted_ship_index):
    """Start a receipt from the pilot's load offer and its admitted seat."""
    receipt = {'format': FORMAT, 'checkpoint_id': checkpoint_id, 'checkpoint_sha256': checkpoint_sha256,
               'registry_epoch': registry_epoch, 'piloted_ship_index': piloted_ship_index, 'hits': []}
    return validate_receipt(receipt)


def _ship(isship, index, code):
    return {'index': index, 'code': code} if isship and index > 0 else None


def collect_hits(receipt, log_text):
    """Record damage callbacks from a text stream or an in-memory log, in order."""
    validate_receipt(receipt)
    hits, seen = [], set()
    lines = log_text.splitlines() if isinstance(log_text, str) else log_text
    for line in lines:
        _, marker, payload = line.partition(MD_EVENT_PREFIX)
        event = parse_md_event(payload) if marker else None
        if event is None:
            continue
        if event.seq in seen:
            # The verifier reads each sequence from this same log and needs it to be unique.
            raise ValueError('native combat sequence repeats in one capture log')
        seen.add(event.seq)
        if (event.kind not in DAMAGE_KINDS or event.registry_epoch != receipt['registry_epoch']
                or event.victim_reg != receipt['piloted_ship_index']):
            continue
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
    if (type(receipt['checkpoint_id']) is not str or not _CHECKPOINT.fullmatch(receipt['checkpoint_id'])
            or type(receipt['checkpoint_sha256']) is not str or not _HEX64.fullmatch(receipt['checkpoint_sha256'])
            or type(receipt['registry_epoch']) is not str or not _HEX32.fullmatch(receipt['registry_epoch'])
            or type(receipt['piloted_ship_index']) is not int or receipt['piloted_ship_index'] < 1
            or type(receipt['hits']) is not list):
        raise ValueError('invalid combat capture receipt')
    return receipt


def verifier_arguments(receipt, store, log, sequence):
    """Arguments for authority_combat_hit_evidence.py for one captured sequence."""
    validate_receipt(receipt)
    if sequence not in {hit['sequence'] for hit in receipt['hits']}:
        raise ValueError('sequence was not captured on the admitted ship')
    return ['--store', str(store), '--checkpoint', receipt['checkpoint_id'],
            '--sha256', receipt['checkpoint_sha256'], '--log', str(log),
            '--sequence', str(sequence), '--piloted-ship-index', str(receipt['piloted_ship_index'])]


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
