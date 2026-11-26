"""Verify native combat observations against the checkpoint offered to the pilot."""
import argparse
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from authority.checkpoints import CheckpointStore
from authority.combat_capture import DAMAGE_KINDS, captured_events, captured_outgoing_contacts, new_receipt
from authority.combat_observation import MD_EVENT_PREFIX, parse_md_event
from authority.load_observations import LOAD_TAG, MARKER_TAG, TICKET_FIELDS, validate_client_receipts
from authority.marker_evidence import verify_saved_marker
from authority.registry_availability_evidence import verify_join_snapshot


GUN_CONTACT_KIND = 'object_attacked_object'
_HANDLE = r'0x[0-9a-fA-F]{1,16}'
_WEAPON_PAIR = re.compile(rf'\[component\.\{{({_HANDLE})L\}},\s*component\.\{{({_HANDLE})L\}}\]')
_GENERIC_CONTACT_SOURCE = re.compile(
    rb'\[Scripts\].* \*\*\* Context:md\.X4AuthorityCombatObserver\.([A-Za-z0-9_]+)<[^<>]+>: '
)


def _read_event(log, sequence, kinds, description, checkpoint=None):
    event_tag = MD_EVENT_PREFIX.encode('ascii')
    sequence_prefix = f'{sequence}|'.encode('ascii')
    event = None
    receipts = []
    with log.open('rb') as stream:
        for line in stream:
            source, marker, payload = line.partition(event_tag)
            if marker and payload.startswith(sequence_prefix):
                if checkpoint is not None:
                    observer = _GENERIC_CONTACT_SOURCE.fullmatch(source)
                    if observer is None:
                        continue
                    if observer[1] != b'ObjectAttackedObject':
                        raise ValueError(f'expected one {description} with the requested sequence')
                if event is not None:
                    raise ValueError(f'expected one {description} with the requested sequence')
                event = parse_md_event(payload.decode('utf-8', errors='replace'))
                if event is None or event.kind not in kinds:
                    raise ValueError(f'expected one {description} with the requested sequence')
            elif checkpoint is not None and event is None and (LOAD_TAG in line or MARKER_TAG in line):
                receipts.append(line)
                if len(receipts) > 3:
                    raise ValueError('expected one checkpoint load before the gun contact')
    if event is None:
        raise ValueError(f'expected one {description} with the requested sequence')
    if checkpoint is not None:
        _verify_contact_load(receipts, checkpoint)
    return event


def _verify_contact_load(receipts, checkpoint):
    if len(receipts) != 3 or LOAD_TAG not in receipts[0] or not checkpoint.marker:
        raise ValueError('expected one checkpoint load before the gun contact')
    fields = receipts[0].split(LOAD_TAG, 1)[1].strip().decode('ascii').split('|')
    if len(fields) != len(TICKET_FIELDS) + 1 or fields[0] != 'load_requested':
        raise ValueError('expected a checkpoint load request before the gun contact')
    ticket = dict(zip(TICKET_FIELDS, fields[1:]))
    if (ticket['checkpoint_id'], ticket['sha256']) != (checkpoint.checkpoint_id, checkpoint.sha256):
        raise ValueError('gun contact log load differs from the pilot load offer')
    validate_client_receipts(b''.join(receipts), ticket, checkpoint.marker)


def read_hit(log, sequence):
    return _read_event(log, sequence, DAMAGE_KINDS, 'shield or hull callback')


def read_gun_contact(log, sequence, checkpoint):
    return _read_event(log, sequence, {GUN_CONTACT_KIND}, 'direct outgoing contact', checkpoint)


def read_private_gun_contact(log, sequence, checkpoint, registry_epoch, shooter_ship_index):
    receipt = new_receipt(checkpoint.checkpoint_id, checkpoint.sha256, registry_epoch, shooter_ship_index)
    with log.open(encoding='utf-8', errors='replace') as stream:
        matches = [event for event in captured_outgoing_contacts(receipt, stream) if event.seq == sequence]
    if len(matches) != 1:
        raise ValueError('expected one direct outgoing contact inside the active shooter capture')
    verify_saved_marker(checkpoint, checkpoint.marker,
                        script_name='X4AuthorityCheckpointMarker', cue_name='MarkerState')
    return matches[0]


def _ship_roles(event):
    if event.kind not in DAMAGE_KINDS:
        raise ValueError('expected a native shield or hull damage observation')
    if not event.registry_epoch or event.victim_reg <= 0:
        raise ValueError('damage observation has no verified registry identity')
    if event.amount <= 0 or not all(math.isfinite(value) and value >= 0
                                    for value in (event.amount, event.shield, event.hull)):
        raise ValueError('invalid damage or post-hit health')
    roles = {'victim': (event.victim_reg, event.victim_code)}
    for role, is_ship, index, code in (
        ('event_attacker', event.ev_att_isship, event.ev_att_reg, event.ev_att_code),
        ('last_attacker', event.last_att_isship, event.last_att_reg, event.last_att_code),
    ):
        if is_ship and index > 0:
            roles[role] = (index, code)
    return roles


def _verify_identities(roles, saved_ships):
    identities = {}
    for role, (index, code) in roles.items():
        if not code or saved_ships.get(index, {}).get('code') != code:
            raise ValueError(f'{role} index and ship code do not match the checkpoint')
        identities[role] = {'index': index, 'code': code}
    return identities


def _hit_report(checkpoint, event, roles, saved_ships, piloted_ship_index):
    identities = _verify_identities(roles, saved_ships)
    if piloted_ship_index is not None:
        if event.victim_reg != piloted_ship_index:
            raise ValueError('damage victim differs from the admitted pilot ship')
        if saved_ships[piloted_ship_index]['owner'] != 'player' or event.victim_owner != 'player':
            raise ValueError('damage victim lacks confirmed Company ownership')
        if not event.victim_occ or not event.victim_ctrl:
            raise ValueError('damage victim was not both occupied and controlled by the player')
    return {
        'checkpoint_id': checkpoint.checkpoint_id, 'checkpoint_sha256': checkpoint.sha256,
        'registry_epoch': event.registry_epoch, 'sequence': event.seq, 'event': event.kind,
        'victim': identities['victim'], 'event_attacker': identities.get('event_attacker'),
        'victim_occupied_by_player': event.victim_occ, 'victim_controlled_by_player': event.victim_ctrl,
        'last_attacker': identities.get('last_attacker'), 'amount': event.amount,
        'shield_after': event.shield, 'hull_after': event.hull,
    }


def _saved_ships(checkpoint, wanted):
    saved_ships = {}

    def observe(_tag, _depth, attributes, index):
        if index in wanted:
            saved_ships[index] = {'code': attributes.get('code'), 'owner': attributes.get('owner')}

    snapshot = verify_join_snapshot(checkpoint, observe_universe=observe)
    return snapshot, saved_ships


def resolve_hits(checkpoint, events, *, piloted_ship_index=None):
    observations = [(event, _ship_roles(event)) for event in events]
    if not observations:
        raise ValueError('no matching damage callback in the active pilot capture')
    wanted = {index for _, roles in observations for index, _code in roles.values()}
    snapshot, saved_ships = _saved_ships(checkpoint, wanted)
    if any(event.registry_epoch != snapshot['epoch'] for event, _ in observations):
        raise ValueError('damage observation and checkpoint have different registry epochs')
    return [_hit_report(checkpoint, event, roles, saved_ships, piloted_ship_index)
            for event, roles in observations]


def resolve_hit(checkpoint, event, *, piloted_ship_index=None):
    return resolve_hits(checkpoint, [event], piloted_ship_index=piloted_ship_index)[0]


def resolve_gun_contact(checkpoint, event, *, shooter_ship_index):
    """Bind a throttled attacker-side observation to the offered ship registry."""
    if event.kind != GUN_CONTACT_KIND or event.param2 not in ('hitbybullet', 'killmethod.hitbybullet'):
        raise ValueError('expected a direct outgoing bullet contact')
    if not math.isfinite(event.age) or event.age < 0 or event.seq <= 0 or not event.registry_epoch:
        raise ValueError('invalid outgoing contact observation')
    if (shooter_ship_index <= 0 or event.ev_att_reg != shooter_ship_index
            or not event.ev_att_isship or not event.ev_att_ctrl):
        raise ValueError('direct attacker was not the controlled ship at the selected shooter index')
    if event.victim_reg <= 0 or event.victim_reg == shooter_ship_index or event.victim_ctrl:
        raise ValueError('direct target must have a separate registered ship index')
    pair = _WEAPON_PAIR.fullmatch(event.param3)
    if pair is None or any(re.fullmatch(_HANDLE, value) is None for value in (event.ev_att, event.victim, event.comp)):
        raise ValueError('missing or invalid direct component and weapon references')
    shooter, target, reported_target = (int(value, 16) for value in (event.ev_att, event.victim, event.comp))
    component, weapon = (int(value, 16) for value in pair.groups())
    if (not all((shooter, target, component, weapon)) or shooter == target or target != reported_target
            or component == shooter or weapon in (shooter, target, component)):
        raise ValueError('inconsistent direct component and weapon references')
    roles = {'shooter': (shooter_ship_index, event.ev_att_code),
             'target': (event.victim_reg, event.victim_code)}
    snapshot, saved_ships = _saved_ships(checkpoint, {shooter_ship_index, event.victim_reg})
    if event.registry_epoch != snapshot['epoch']:
        raise ValueError('gun contact and checkpoint have different registry epochs')
    identities = _verify_identities(roles, saved_ships)
    if event.ev_att_owner != 'player' or saved_ships[shooter_ship_index]['owner'] != 'player':
        raise ValueError('direct attacker lacks confirmed Company ownership')
    return {
        'checkpoint_id': checkpoint.checkpoint_id, 'checkpoint_sha256': checkpoint.sha256,
        'registry_epoch': event.registry_epoch, 'sequence': event.seq, 'event': event.kind,
        'game_age': event.age, 'method': 'hitbybullet',
        'shooter': identities['shooter'], 'target': identities['target'],
        'damaged_component': hex(component), 'weapon': hex(weapon),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--sha256', required=True, help="save digest from this pilot's load offer")
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--sequence', type=int, help='select one hit; omit to verify all hits in a pilot capture')
    parser.add_argument('--capture-epoch', help="verify the private capture window using the offered registry epoch")
    parser.add_argument('--piloted-ship-index', type=int,
                        help='require the hit victim to be the occupied, controlled Company ship at this admitted index')
    parser.add_argument('--shooter-ship-index', type=int,
                        help='verify one direct bullet contact from the controlled Company shooter at this index; '
                             'include --capture-epoch for private pilot captures')
    args = parser.parse_args()
    if args.shooter_ship_index is not None and (args.shooter_ship_index <= 0 or args.sequence is None
                                               or args.piloted_ship_index is not None):
        parser.error('--shooter-ship-index requires a positive index and --sequence without --piloted-ship-index')
    if args.capture_epoch is not None and args.piloted_ship_index is None and args.shooter_ship_index is None:
        parser.error('--capture-epoch requires --piloted-ship-index or --shooter-ship-index')
    if args.capture_epoch is None and args.sequence is None:
        parser.error('specify --sequence or the admitted pilot capture window')
    try:
        checkpoint = CheckpointStore(args.store).get(args.checkpoint)
        if checkpoint.sha256 != args.sha256:
            raise ValueError('checkpoint differs from the pilot load offer')
        if args.shooter_ship_index is not None:
            if args.capture_epoch is None:
                event = read_gun_contact(args.log, args.sequence, checkpoint)
            else:
                event = read_private_gun_contact(args.log, args.sequence, checkpoint, args.capture_epoch,
                                                 args.shooter_ship_index)
            report = resolve_gun_contact(checkpoint, event, shooter_ship_index=args.shooter_ship_index)
        elif args.capture_epoch is None:
            report = resolve_hit(checkpoint, read_hit(args.log, args.sequence),
                                 piloted_ship_index=args.piloted_ship_index)
        else:
            capture = new_receipt(checkpoint.checkpoint_id, checkpoint.sha256,
                                  args.capture_epoch, args.piloted_ship_index)
            with args.log.open(encoding='utf-8', errors='replace') as stream:
                events = captured_events(capture, stream)
            if args.sequence is not None:
                events = [event for event in events if event.seq == args.sequence]
            reports = resolve_hits(checkpoint, events, piloted_ship_index=args.piloted_ship_index)
            report = dict(capture, hits=reports) if args.sequence is None else reports[0]
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
