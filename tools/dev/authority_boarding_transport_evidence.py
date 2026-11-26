#!/usr/bin/env python3
"""Verify a seeded Company boarding transport in a sealed checkpoint."""
import argparse
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from authority.checkpoints import CheckpointError, CheckpointStore
from authority.company_ships import _DISPLAY_CODE, _SECTOR_MACRO, _SavedCaptainScan
from authority.registry_availability_evidence import verify_join_snapshot


SHIP_MACRO = 'ship_arg_m_trans_container_01_a_macro'


def verify_boarding_transport(checkpoint, *, code, sector_macro):
    """Return saved identity, captain seed and marine count after registry verification."""
    if (not isinstance(code, str) or not _DISPLAY_CODE.fullmatch(code)
            or not isinstance(sector_macro, str) or not _SECTOR_MACRO.fullmatch(sector_macro)):
        raise ValueError('invalid boarding transport code or sector macro')

    ancestors = []
    captains = _SavedCaptainScan()
    ship_depth = ship_index = npc_depth = None
    active = False
    marines = people = 0

    def observe(element, depth, attrs, index):
        nonlocal ship_depth, ship_index, npc_depth, active, people, marines
        while ancestors and ancestors[-1][0] >= depth:
            ancestors.pop()
        parent = ancestors[-1][1] if ancestors else None
        if npc_depth is not None and depth <= npc_depth:
            npc_depth = None

        if active and depth <= ship_depth:
            active = False

        kind = attrs.get('class', '') if element == 'component' else ''
        if kind.startswith('ship_') and attrs.get('code') == code:
            if ship_depth is not None:
                raise ValueError('duplicate saved boarding transport code')
            if (index is None or kind != 'ship_m' or attrs.get('macro') != SHIP_MACRO
                    or attrs.get('owner') != 'player'):
                raise ValueError('boarding transport is not a registered operational Company transport')
            sectors = [macro for _, _, kind, macro in ancestors if kind == 'sector']
            if sectors != [sector_macro]:
                raise ValueError('saved boarding transport sector differs')
            ship_depth, ship_index = depth, index
            active = True

        if active:
            if element == 'person' and parent == 'people' and depth == ship_depth + 2:
                people += 1
                marines += attrs.get('role') == 'marine'
            if npc_depth is None:
                captains.observe(element, depth, attrs, index if depth == ship_depth else None)
                if kind == 'npc':
                    npc_depth = depth
            elif depth == npc_depth + 1 and element in ('entity', 'npcseed'):
                captains.observe(element, depth, attrs, None)
        ancestors.append((depth, element, kind, attrs.get('macro')))

    verify_join_snapshot(checkpoint, observe_universe=observe)
    if ship_index is None:
        raise ValueError('saved boarding transport code was not found')
    captains.finish_before(0)
    captain_seed = captains.seeds.get(ship_index)
    if captain_seed in (None, '0'):
        raise ValueError('boarding transport has no real assigned Company NPC captain')
    if (people, marines) != (8, 8):
        raise ValueError('boarding transport must have exactly eight saved crew, all marines')
    return dict(ship_index=ship_index, code=code, sector_macro=sector_macro,
                captain_seed=captain_seed, marines=marines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--code', required=True)
    parser.add_argument('--sector', required=True)
    args = parser.parse_args(argv)
    try:
        checkpoint = CheckpointStore(args.store).get(args.checkpoint)
        if checkpoint.sha256 != args.sha256:
            raise ValueError('checkpoint SHA-256 differs')
        result = verify_boarding_transport(checkpoint, code=args.code, sector_macro=args.sector)
    except (CheckpointError, ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
