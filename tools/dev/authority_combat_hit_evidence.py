"""Resolve a native shield or hull callback against the checkpoint offered to its pilot."""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from authority.checkpoints import CheckpointStore
from authority.combat_observation import MD_EVENT_PREFIX, parse_md_event
from authority.registry_availability_evidence import verify_join_snapshot


def read_hit(log, sequence):
    hits = []
    with log.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            _, marker, payload = line.partition(MD_EVENT_PREFIX)
            if marker and payload.startswith(f'{sequence}|'):
                hits.append(parse_md_event(payload))
    if len(hits) != 1 or hits[0] is None or hits[0].kind not in ('shield_damaged', 'hull_damaged'):
        raise ValueError('expected one shield or hull callback with the requested sequence')
    return hits[0]


def resolve_hit(checkpoint, event, *, piloted_ship_index=None):
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
    wanted = {index for index, _ in roles.values()}
    saved_ships = {}

    def observe(_tag, _depth, attributes, index):
        if index in wanted:
            saved_ships[index] = {'code': attributes.get('code'), 'owner': attributes.get('owner')}

    snapshot = verify_join_snapshot(checkpoint, observe_universe=observe)
    if snapshot['epoch'] != event.registry_epoch:
        raise ValueError('damage observation and checkpoint have different registry epochs')
    identities = {}
    for role, (index, code) in roles.items():
        if not code or saved_ships.get(index, {}).get('code') != code:
            raise ValueError(f'{role} index and ship code do not match the checkpoint')
        identities[role] = {'index': index, 'code': code}
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--sha256', required=True, help="save digest from this pilot's load offer")
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--sequence', type=int, required=True)
    parser.add_argument('--piloted-ship-index', type=int,
                        help='require the hit victim to be the occupied, controlled Company ship at this admitted index')
    args = parser.parse_args()
    checkpoint = CheckpointStore(args.store).get(args.checkpoint)
    if checkpoint.sha256 != args.sha256:
        parser.error('checkpoint differs from the pilot load offer')
    try:
        report = resolve_hit(checkpoint, read_hit(args.log, args.sequence),
                             piloted_ship_index=args.piloted_ship_index)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
