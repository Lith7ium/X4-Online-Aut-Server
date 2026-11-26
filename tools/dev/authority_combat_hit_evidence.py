"""Verify native combat observations against the checkpoint offered to the pilot."""
import argparse
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from authority.checkpoints import CheckpointStore
from authority.combat_capture import (DAMAGE_KINDS, capture_binding, captured_events, new_receipt,
                                      read_incoming_projectile_capture, read_outgoing_capture)
from authority.combat_observation import MD_EVENT_PREFIX, parse_md_event
from authority.load_observations import LOAD_TAG, MARKER_TAG, TICKET_FIELDS, validate_client_receipts
from authority.marker_evidence import verify_saved_marker
from authority.registry_availability_evidence import verify_join_snapshot


GUN_CONTACT_KIND = 'object_attacked_object'
_NPC_PROJECTILE = 'bullet_xen_s_laser_01_mk1_macro'
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


def _verify_contact_load(receipts, checkpoint, observation='gun contact'):
    if len(receipts) != 3 or LOAD_TAG not in receipts[0] or not checkpoint.marker:
        raise ValueError(f'expected one checkpoint load before the {observation}')
    fields = receipts[0].split(LOAD_TAG, 1)[1].strip().decode('ascii').split('|')
    if len(fields) != len(TICKET_FIELDS) + 1 or fields[0] != 'load_requested':
        raise ValueError(f'expected a checkpoint load request before the {observation}')
    ticket = dict(zip(TICKET_FIELDS, fields[1:]))
    if (ticket['checkpoint_id'], ticket['sha256']) != (checkpoint.checkpoint_id, checkpoint.sha256):
        raise ValueError(f'{observation} log load differs from the pilot load offer')
    validate_client_receipts(b''.join(receipts), ticket, checkpoint.marker)


def read_hit(log, sequence):
    return _read_event(log, sequence, DAMAGE_KINDS, 'shield or hull callback')


def read_gun_contact(log, sequence, checkpoint):
    return _read_event(log, sequence, {GUN_CONTACT_KIND}, 'direct outgoing contact', checkpoint)


def verify_private_gun_contacts(log, checkpoint, registry_epoch, shooter_ship_index, *, sequence=None):
    receipt = new_receipt(checkpoint.checkpoint_id, checkpoint.sha256, registry_epoch, shooter_ship_index)
    with log.open(encoding='utf-8', errors='replace') as stream:
        shooter_code, events = read_outgoing_capture(receipt, stream)
    if sequence is not None:
        events = [event for event in events if event.seq == sequence]
        if len(events) != 1:
            raise ValueError('expected one direct outgoing contact inside the active shooter capture')
    verify_saved_marker(checkpoint, checkpoint.marker,
                        script_name='X4AuthorityCheckpointMarker', cue_name='MarkerState')
    shooter, contacts = _resolve_gun_contacts(checkpoint, events, registry_epoch, shooter_ship_index, shooter_code)
    if sequence is not None:
        return contacts[0]
    return {
        'checkpoint_id': checkpoint.checkpoint_id, 'checkpoint_sha256': checkpoint.sha256,
        'registry_epoch': registry_epoch, 'shooter': shooter, 'contacts': contacts,
    }


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


def _gun_contact_components(event, shooter_ship_index):
    if event.kind != GUN_CONTACT_KIND or event.param2 not in ('hitbybullet', 'killmethod.hitbybullet'):
        raise ValueError('expected a direct outgoing bullet contact')
    if not math.isfinite(event.age) or event.age < 0 or event.seq <= 0 or not event.registry_epoch:
        raise ValueError('invalid outgoing contact observation')
    if (shooter_ship_index <= 0 or event.ev_att_reg != shooter_ship_index
            or not event.ev_att_isship or not event.ev_att_ctrl):
        raise ValueError('direct attacker was not the controlled ship at the selected shooter index')
    if event.ev_att_owner != 'player':
        raise ValueError('direct attacker lacks confirmed Company ownership')
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
    return hex(component), hex(weapon)


def _gun_contact_report(checkpoint, event, shooter, saved_ships, component, weapon):
    if event.ev_att_code != shooter['code']:
        raise ValueError('direct attacker ship code differs from the verified shooter')
    identities = _verify_identities({'target': (event.victim_reg, event.victim_code)}, saved_ships)
    return {
        'checkpoint_id': checkpoint.checkpoint_id, 'checkpoint_sha256': checkpoint.sha256,
        'registry_epoch': event.registry_epoch, 'sequence': event.seq, 'event': event.kind,
        'game_age': event.age, 'method': 'hitbybullet',
        'shooter': shooter, 'target': identities['target'],
        'damaged_component': component, 'weapon': weapon,
    }


def _resolve_gun_contacts(checkpoint, events, registry_epoch, shooter_ship_index, shooter_code):
    observations = [(event, _gun_contact_components(event, shooter_ship_index)) for event in events]
    wanted = {shooter_ship_index}
    wanted.update(event.victim_reg for event, _ in observations)
    snapshot, saved_ships = _saved_ships(checkpoint, wanted)
    if registry_epoch != snapshot['epoch'] or any(event.registry_epoch != registry_epoch for event, _ in observations):
        raise ValueError('gun contact and checkpoint have different registry epochs')
    shooter = _verify_identities({'shooter': (shooter_ship_index, shooter_code)}, saved_ships)['shooter']
    if saved_ships[shooter_ship_index]['owner'] != 'player':
        raise ValueError('direct attacker lacks confirmed Company ownership')
    contacts = [_gun_contact_report(checkpoint, event, shooter, saved_ships, *components)
                for event, components in observations]
    return shooter, contacts


def resolve_gun_contact(checkpoint, event, *, shooter_ship_index):
    """Bind a throttled attacker-side observation to the offered ship registry."""
    _, contacts = _resolve_gun_contacts(checkpoint, [event], event.registry_epoch, shooter_ship_index, event.ev_att_code)
    return contacts[0]


def _loaded_capture_lines(stream, checkpoint):
    receipts = []
    load_verified = False
    for raw in stream:
        if LOAD_TAG in raw or MARKER_TAG in raw:
            if load_verified or len(receipts) >= 3:
                raise ValueError('incoming projectile log has an ambiguous load history')
            receipts.append(raw)
        line = raw.decode('utf-8', errors='replace')
        if not load_verified and '[X4LAN] AUTH_COMBAT_CAPTURE capturing ' in line:
            _verify_contact_load(receipts, checkpoint, 'incoming projectile capture')
            load_verified = True
        yield line


def _npc_projectile_components(event, pilot):
    if (not math.isfinite(event.age) or event.age < 0 or event.seq <= 0
            or not event.ev_att_isship or event.ev_att_ctrl or not 1 <= event.ev_att_reg <= 200000
            or event.ev_att_reg == pilot or event.ev_att_owner != 'xenon'
            or event.param3 != _NPC_PROJECTILE or not event.is_root or event.collided):
        raise ValueError('expected one unambiguous registered Xenon stock laser contact')
    values = (event.ev_att, event.victim, event.comp, event.param2)
    if any(re.fullmatch(_HANDLE, value) is None for value in values):
        raise ValueError('invalid native attacker, victim or projectile handle')
    attacker, target, component, projectile = (int(value, 16) for value in values)
    if min(attacker, target, projectile) <= 0 or target != component or len({attacker, target, projectile}) != 3:
        raise ValueError('inconsistent native attacker, victim or projectile handles')
    return attacker, target, projectile


def verify_private_npc_projectiles(log, checkpoint, registry_epoch, piloted_ship_index, *, sequence=None):
    receipt = new_receipt(checkpoint.checkpoint_id, checkpoint.sha256, registry_epoch, piloted_ship_index)
    with log.open('rb') as stream:
        events = read_incoming_projectile_capture(receipt, _loaded_capture_lines(stream, checkpoint))
    verify_saved_marker(checkpoint, checkpoint.marker,
                        script_name='X4AuthorityCheckpointMarker', cue_name='MarkerState')

    observations, actors, projectiles = [], {}, set()
    prior_sequence, prior_age = 0, 0
    for event in events:
        if event.ev_att_owner == 'player':
            continue
        attacker, target, projectile = _npc_projectile_components(event, piloted_ship_index)
        if event.seq <= prior_sequence or event.age < prior_age or projectile in projectiles:
            raise ValueError('ambiguous, duplicate or stale incoming projectile observation')
        prior_sequence, prior_age = event.seq, event.age
        index = event.ev_att_reg
        identity = attacker, event.ev_att_code
        if actors.setdefault(index, identity) != identity:
            raise ValueError('native NPC identity changed in the active capture')
        projectiles.add(projectile)
        observations.append((event, attacker, target, projectile))

    if not observations or len({identity[0] for identity in actors.values()}) != len(actors):
        raise ValueError('no uniquely registered incoming NPC projectile contacts in the active capture')
    wanted = {piloted_ship_index, *actors}
    snapshot, saved_ships = _saved_ships(checkpoint, wanted)
    if (snapshot['epoch'] != registry_epoch or any(
            index > len(snapshot['operational_bits']) or snapshot['operational_bits'][index - 1] != '1'
            for index in wanted)):
        raise ValueError('incoming projectile identities differ from the offered operational registry')

    contacts = []
    for event, attacker, target, projectile in observations:
        roles = {'attacker': (event.ev_att_reg, event.ev_att_code), 'target': (event.victim_reg, event.victim_code)}
        identities = _verify_identities(roles, saved_ships)
        for role, owner, actor in (('attacker', 'xenon', attacker), ('target', 'player', target)):
            identity = identities[role]
            if saved_ships[identity['index']]['owner'] != owner:
                raise ValueError(f'{role} faction differs from the offered checkpoint')
            identity.update(faction=owner, native_actor=hex(actor))
        contacts.append({
            'checkpoint_id': checkpoint.checkpoint_id, 'checkpoint_sha256': checkpoint.sha256,
            'registry_epoch': registry_epoch, 'sequence': event.seq, 'event': event.kind,
            'game_age': event.age, **identities, 'projectile': {'native_actor': hex(projectile), 'macro': event.param3},
        })
    if sequence is not None:
        for contact in contacts:
            if contact['sequence'] == sequence:
                return contact
        raise ValueError('no registered NPC projectile with the requested sequence')
    binding = capture_binding(checkpoint.checkpoint_id, checkpoint.sha256, registry_epoch, piloted_ship_index)
    return dict(binding, contacts=contacts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--sha256', required=True, help="save digest from this pilot's load offer")
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--sequence', type=int, help='select one event; omit to verify all events in a private capture')
    parser.add_argument('--capture-epoch', help="verify the private capture window using the offered registry epoch")
    parser.add_argument('--piloted-ship-index', type=int,
                        help='require the hit victim to be the occupied, controlled Company ship at this admitted index')
    parser.add_argument('--shooter-ship-index', type=int,
                        help='verify direct bullet contacts from the controlled Company shooter at this index')
    parser.add_argument('--incoming-npc-projectiles', action='store_true',
                        help='verify the measured stock Xenon Mk1 laser from registered Xenon ships hitting the occupied pilot')
    args = parser.parse_args()
    if args.incoming_npc_projectiles and (args.capture_epoch is None or args.piloted_ship_index is None
                                         or not 1 <= args.piloted_ship_index <= 200000
                                         or args.shooter_ship_index is not None):
        parser.error('--incoming-npc-projectiles requires --capture-epoch and --piloted-ship-index')
    if args.shooter_ship_index is not None and (args.shooter_ship_index <= 0 or args.piloted_ship_index is not None):
        parser.error('--shooter-ship-index requires a positive index without --piloted-ship-index')
    if args.capture_epoch is not None and args.piloted_ship_index is None and args.shooter_ship_index is None:
        parser.error('--capture-epoch requires --piloted-ship-index or --shooter-ship-index')
    if args.capture_epoch is None and args.sequence is None:
        parser.error('specify --sequence or the admitted pilot capture window')
    try:
        checkpoint = CheckpointStore(args.store).get(args.checkpoint)
        if checkpoint.sha256 != args.sha256:
            raise ValueError('checkpoint differs from the pilot load offer')
        if args.incoming_npc_projectiles:
            report = verify_private_npc_projectiles(args.log, checkpoint, args.capture_epoch,
                                                    args.piloted_ship_index, sequence=args.sequence)
        elif args.shooter_ship_index is not None:
            if args.capture_epoch is None:
                event = read_gun_contact(args.log, args.sequence, checkpoint)
                report = resolve_gun_contact(checkpoint, event, shooter_ship_index=args.shooter_ship_index)
            else:
                report = verify_private_gun_contacts(args.log, checkpoint, args.capture_epoch,
                                                     args.shooter_ship_index, sequence=args.sequence)
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
