"""Summarize the opt-in, read-only Company first-contact diagnostic."""

import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import re

MARKER = '[X4LAN] DYNAMIC_CONTACT '
REFERENCE = '[X4LAN] DYNAMIC_REFERENCE '
DAMAGE = frozenset(('shield', 'root_hull', 'component_hull'))
KINDS = DAMAGE | {'first_direct', 'direct', 'collision'}
IDENTITY = re.compile(r'0x[0-9a-fA-F]{1,16}')


def records(lines, marker):
    return [(number, line.partition(marker)[2].strip().split('|'))
            for number, line in enumerate(lines, 1) if marker in line]


def summarize(lines):
    lines = list(lines)
    events = records(lines, MARKER)
    if not events or len(events) > 128 or any(len(row) != 11 for _, row in events):
        raise ValueError('missing or malformed dynamic contact events')
    first_line, first = events[0]
    source, target, index = first[3:6]
    if (first[0] != 'first_direct' or not IDENTITY.fullmatch(source)
            or not IDENTITY.fullmatch(target) or source == target
            or not index.isdigit() or not 1 <= int(index) <= 200000
            or first[6] != target):
        raise ValueError('first event does not establish a distinct registered target')
    ages = []
    for sequence, (_, row) in enumerate(events, 1):
        if (row[1] != str(sequence) or row[3:6] != [source, target, index]
                or row[0] not in KINDS or (sequence > 1 and row[0] == 'first_direct')):
            raise ValueError('dynamic sequence or registered identity changed')
        age = Decimal(row[2])
        if not age.is_finite() or age < 0 or (ages and age < ages[-1]):
            raise ValueError('dynamic event time changed backwards')
        ages.append(age)
    if ages[-1] - ages[0] > 10:
        raise ValueError('dynamic event duration exceeded the watch window')

    # The event kind, native age, target, component, amount, last attacker and
    # post-event shield identify a debit in this controlled fixture. Counting
    # occurrences avoids collapsing equal rows in a shared game update.
    damage = [row for _, row in events if row[0] in DAMAGE]
    observed = Counter((row[0], row[2], row[4], *row[6:10]) for row in damage)
    counts = Counter(row[0] for _, row in events)
    result = {'source': source, 'target': target, 'target_index': int(index),
              'first_direct_line': first_line, 'first_direct_game_s': first[2],
              'shield_at_first_direct': first[9],
              'counts': {kind: counts[kind] for kind in sorted(KINDS)}}
    oracle = records(lines, REFERENCE)
    if not oracle:
        return result
    baseline = [row for _, row in oracle if row[0] == 'baseline']
    debits = [(line, row) for line, row in oracle if row[0] in DAMAGE]
    if (len(baseline) != 1 or len(baseline[0]) != 5 or baseline[0][2:4] != [source, target]
            or any(len(row) != 7 or row[2] != target for _, row in debits)):
        raise ValueError('reference does not match the dynamic actor binding')
    reference = Counter(tuple(row) for _, row in debits)
    if observed - reference:
        raise ValueError('dynamic debit has no matching reference occurrence')
    own_shield = [(line, row) for line, row in debits
                  if row[0] == 'shield' and row[5] == source and Decimal(row[4]) > 0]
    result.update(reference_damage_events=len(debits), dynamic_damage_events=len(damage),
                  missing_damage_events=sum((reference - observed).values()),
                  reference_collision_events=sum(row[0] == 'collision' for _, row in oracle))
    if own_shield:
        first_debit_line, first_debit = own_shield[0]
        # Equal keys at the subscription boundary cannot identify which
        # occurrence was caught unless all equal occurrences were observed.
        first_key = tuple(first_debit)
        seen = observed[first_key]
        result.update(first_debit_line=first_debit_line,
                      first_debit_game_s=first_debit[1], first_debit_amount=first_debit[4],
                      first_debit_precedes_subscription=first_debit_line < first_line,
                      first_debit_seen=(bool(seen) if seen in (0, reference[first_key]) else None))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path, help='private native X4 engine log')
    args = parser.parse_args()
    try:
        with args.log.open(encoding='utf-8', errors='replace') as stream:
            print(json.dumps(summarize(stream), indent=2))
    except (OSError, ValueError, ArithmeticError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
