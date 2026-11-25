#!/usr/bin/env python3
"""Correlate a disposable provider's passive stock boarding witness."""

import argparse
import json
from pathlib import Path
import re


MARKER = 'AUTH_BOARDING_WITNESS|'
SCHEMA = {'ARM': 5, 'START': 3, 'COMPANY': 4, 'BOARDED': 3,
          'OWNER': 4, 'READBACK': 4, 'REMOVED': 2}


def component(text):
    if not re.fullmatch(r'0[xX][0-9a-fA-F]{1,16}', text) or int(text, 16) == 0:
        raise ValueError('invalid runtime component')
    return int(text, 16)


def positive(text):
    if not re.fullmatch(r'[1-9][0-9]*', text):
        raise ValueError('invalid registry index or count')
    return int(text)


def verify(lines, *, epoch, index, target, macro, company_indices):
    if (not re.fullmatch(r'[0-9a-f]{32}', epoch) or index < 1 or target < 1
            or not re.fullmatch(r'[a-zA-Z0-9_]+', macro)
            or not 1 <= len(company_indices) <= 8
            or any(i < 1 or i == index for i in company_indices)):
        raise ValueError('invalid independent fixture identity')
    rows = {}
    company = {}
    company_lines = []
    for line_no, line in enumerate(lines, 1):
        if MARKER not in line:
            continue
        kind, *cells = line.split(MARKER, 1)[1].strip().split('|')
        if kind not in SCHEMA or len(cells) != SCHEMA[kind] or not all(cells):
            raise ValueError(f'invalid witness row at line {line_no}')
        if kind != 'ARM':
            if component(cells[1]) != target:
                raise ValueError('witness names another target')
            if kind != 'START' and ('START' not in rows or cells[0] != rows['START'][1][0]):
                raise ValueError('witness names another operation or precedes start')
        if kind == 'COMPANY':
            attacker_index, attacker = positive(cells[2]), component(cells[3])
            if (attacker_index not in company_indices or attacker == target
                    or attacker_index in company or attacker in company.values()):
                raise ValueError('unrecognized or duplicate Company attacker')
            company[attacker_index] = attacker
            company_lines.append(line_no)
        elif kind in rows:
            raise ValueError(f'duplicate {kind} witness')
        else:
            rows[kind] = line_no, cells
    if 'ARM' not in rows:
        raise ValueError('missing arming witness')
    armed_at, (armed_epoch, armed_index, armed_target, armed_macro, old_owner) = rows['ARM']
    if (armed_epoch, positive(armed_index), component(armed_target), armed_macro) != (epoch, index, target, macro):
        raise ValueError('arming witness differs from independent fixture identity')
    if old_owner == 'player':
        raise ValueError('boardee was already player-owned')
    if 'START' not in rows:
        return {'outcome': 'no_start_observed'}
    started_at, (operation, _, attacker_count) = rows['START']
    if started_at <= armed_at or not len(company) <= positive(attacker_count) <= 8:
        raise ValueError('invalid start order or attacker count')
    if company_lines and any(max(company_lines) >= rows[key][0]
                             for key in ('BOARDED', 'OWNER', 'READBACK', 'REMOVED') if key in rows):
        raise ValueError('Company attacker evidence follows the operation outcome')
    if 'BOARDED' in rows and rows['BOARDED'][1][2] != 'player':
        raise ValueError('unexpected boarded faction')
    if 'OWNER' in rows and rows['OWNER'][1][2:] != [old_owner, 'player']:
        raise ValueError('unexpected true-owner transition')
    if 'READBACK' in rows and rows['READBACK'][1][2:] != [macro, 'player']:
        raise ValueError('unexpected takeover readback')
    takeover = {'BOARDED', 'OWNER', 'READBACK'}
    if 'READBACK' in rows and (not takeover <= rows.keys()
            or rows['READBACK'][0] <= max(rows['BOARDED'][0], rows['OWNER'][0])):
        raise ValueError('readback without prior target signal and true-owner transition')
    removed = 'REMOVED' in rows
    if removed and any(rows['REMOVED'][0] < rows[key][0] for key in ('BOARDED', 'OWNER') if key in rows):
        raise ValueError('takeover evidence follows operation removal')
    outcome = 'unattributed'
    if company:
        if takeover <= rows.keys():
            outcome = 'takeover_observed'
        elif removed:
            outcome = 'removed_without_confirmed_takeover'
        else:
            outcome = 'incomplete'
    return {'outcome': outcome, 'operation': operation, 'epoch': epoch, 'index': index,
            'target': hex(target), 'previous_true_owner': old_owner,
            'company_attackers': {key: hex(value) for key, value in company.items()},
            'removal_observed': removed, 'evidence_lines': {key: row[0] for key, row in rows.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--epoch', required=True)
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--target', type=component, required=True)
    parser.add_argument('--macro', required=True)
    parser.add_argument('--company-index', type=int, action='append', required=True)
    args = parser.parse_args()
    try:
        with args.log.open(encoding='utf-8', errors='replace') as lines:
            result = verify(lines, epoch=args.epoch, index=args.index, target=args.target,
                            macro=args.macro, company_indices=set(args.company_index))
    except (OSError, ValueError) as error:
        parser.exit(2, f'Boarding evidence rejected: {error}\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['outcome'] == 'takeover_observed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
