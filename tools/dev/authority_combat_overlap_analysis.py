"""Per-impact attribution analysis for the overlapping primary-gun run.

Reads the AUTH_OVERLAP_* trace lines and the observer's events from one native log. The game
names no attacker on shield or hull callbacks. Native runs show that a bullet still exists
during its own impact callbacks and is gone at the next observation (the next impact frame or
a sweep). So the bullets seen gone at the next observation are the candidates for the previous
impact frame. That is an inference per frame, never per callback: when one frame holds several
callbacks, nothing says which callback belongs to which bullet.
"""
import re

PHASE_PREFIX = '[X4LAN] AUTH_COMBAT_PHASE '
TRACE_PREFIX = '[X4LAN] AUTH_OVERLAP_'
FIELD = re.compile(r'(\w+)=(\S*)')
# Bullets reach #4 in about 0.1 s (200 m) or 0.12 s (250 m) at 2100 m/s. A bullet first seen
# gone later than this was not a hit in the frame just before.
MAX_FLIGHT_S = 0.5
OVERLAP_PHASES = ('control_pilot', 'control_second', 'control_second_yawfix', 'overlap', 'miss_pilot')


def _fields(text):
    return dict(FIELD.findall(text))


def _gone(ids):
    out = []
    for item in filter(None, (ids or '').split(';')):
        parts = item.split(':')
        if len(parts) == 3:
            try:
                out.append({'shooter': parts[0], 'projectile': parts[1], 'flight_s': float(parts[2])})
            except ValueError:
                continue
    return out


def parse_trace(text):
    """Return {phase: {'fires', 'records', 'attacked', 'player_attacked', 'ctrl'}} in log order."""
    phase = 'unlabelled'
    phases = {}
    for line in text.splitlines():
        if PHASE_PREFIX in line:
            match = re.search(r'\bphase=(\S+)', line.split(PHASE_PREFIX, 1)[1])
            if match:
                phase = match.group(1)
            continue
        if TRACE_PREFIX not in line:
            continue
        kind, _, rest = line.split(TRACE_PREFIX, 1)[1].partition(' ')
        bucket = phases.setdefault(phase, {'fires': [], 'records': [], 'attacked': [], 'player_attacked': [], 'ctrl': []})
        f = _fields(rest)
        if kind == 'FIRE':
            bucket['fires'].append(f)
        elif kind in ('IMPACT', 'SWEEP'):
            f['record'] = kind
            f['gone_list'] = _gone(f.get('ids'))
            bucket['records'].append(f)
        elif kind == 'ATTACKED_OBJECT':
            bucket['attacked'].append(f)
        elif kind == 'PLAYER_ATTACKED':
            bucket['player_attacked'].append(f)
        else:
            bucket['ctrl'].append(rest.strip())
    return phases


def _count(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def impact_frames(records):
    """Group impact callbacks by frame and attach the bullets seen gone at the next observation."""
    frames = []
    pending = None
    unassigned = []
    for rec in records:
        is_new_frame = rec['record'] == 'IMPACT' and (not frames or frames[-1]['age'] != rec.get('age') or pending is None)
        if rec['record'] == 'SWEEP' or is_new_frame:
            gone = rec['gone_list']
            closing_sweep = rec['record'] == 'SWEEP' and rec.get('tag', '').endswith('_end')
            if pending is not None:
                if closing_sweep:
                    # Too late to separate the last frame's hits from expired misses.
                    pending['observed_by'] = 'end_sweep'
                    pending['bullets'] = gone
                else:
                    pending['observed_by'] = 'next_frame' if rec['record'] == 'IMPACT' else 'stop_sweep'
                    pending['bullets'] = [g for g in gone if g['flight_s'] <= MAX_FLIGHT_S]
                    unassigned += [g for g in gone if g['flight_s'] > MAX_FLIGHT_S]
                pending = None
            else:
                unassigned += gone
            if rec['record'] == 'SWEEP':
                continue
            pending = {'age': rec.get('age'), 'callbacks': [], 'bullets': None, 'observed_by': None}
            frames.append(pending)
        if rec['record'] == 'IMPACT':
            frames[-1]['callbacks'].append(rec)
    return frames, unassigned


def classify_frame(frame, projectile_weapon):
    callbacks = frame['callbacks']
    shields = [c for c in callbacks if c.get('kind') == 'shield']
    hulls = [c for c in callbacks if c.get('kind') == 'hull']
    # One bullet that empties the shield reports a shield and a hull callback in the same frame.
    overflow = min(sum(1 for c in shields if c.get('value') in ('0', '0.0')), len(hulls))
    bullets_needed = len(callbacks) - overflow
    bullets = frame['bullets'] or []
    shooters = sorted({b['shooter'] for b in bullets})
    if frame['observed_by'] in (None, 'end_sweep'):
        label = 'unresolved_last_frame'
    elif frame['observed_by'] == 'stop_sweep' and not bullets:
        # The stop sweep ran in the impact's own frame, before its bullets were deleted.
        label = 'unresolved_stop_sweep_same_frame'
    elif not bullets:
        label = 'no_bullet_seen_gone'
    elif len(shooters) > 1:
        label = 'ambiguous_both_shooters'
    elif len(bullets) == bullets_needed:
        label = 'one_shooter_count_matches'
    else:
        label = 'one_shooter_count_differs'
    return {
        'age': frame['age'], 'callbacks': len(callbacks), 'shield': len(shields), 'hull': len(hulls),
        'overflow_pairs': overflow, 'bullets_needed': bullets_needed, 'bullets_seen_gone': len(bullets),
        'observed_by': frame['observed_by'], 'shooters': shooters,
        'weapons': sorted({projectile_weapon.get(b['projectile'], '?') for b in bullets}),
        'amounts': [c.get('amount') for c in callbacks], 'class': label,
        # Inside one frame every callback could belong to any of the frame's bullets.
        'per_callback_ambiguous': len(callbacks) > 1,
    }


def analyse_phase(bucket, events):
    fires = bucket['fires']
    projectile_weapon = {f.get('proj'): f.get('weapon') for f in fires}
    frames, unassigned = impact_frames(bucket['records'])
    classified = [classify_frame(f, projectile_weapon) for f in frames]
    named = {}
    for source, rows in (('attacked_object', bucket['attacked']), ('player_attacked_object', bucket['player_attacked'])):
        for row in rows:
            named.setdefault(row.get('age'), set()).add(row.get('weapon_shooter'))
    agree = disagree = 0
    for frame in classified:
        if frame['class'] == 'one_shooter_count_matches' and frame['age'] in named:
            if named[frame['age']] <= set(frame['shooters']):
                agree += 1
            else:
                disagree += 1
    damage = [e for e in events if e.kind in ('shield_damaged', 'hull_damaged') and e.victim_label == 'npc_target']
    other_victims = [e for e in events if e.kind in ('shield_damaged', 'hull_damaged') and e.victim_label != 'npc_target']
    inferred_by_age = {f['age']: f['shooters'] for f in classified if f['class'] == 'one_shooter_count_matches'}
    label_index = {'company_player': '1', 'second_shooter': '2'}
    la_agree = la_disagree = 0
    for e in damage:
        shooters = inferred_by_age.get(f'{e.age:g}') or inferred_by_age.get(str(e.age))
        if shooters:
            if label_index.get(e.last_att_label) in shooters:
                la_agree += 1
            else:
                la_disagree += 1
    last_attackers = [e.last_att_label for e in damage]
    callbacks = sum(f['callbacks'] for f in classified)
    return {
        'shots_by_shooter': _count(f.get('shooter') for f in fires),
        'weapons_by_shooter': {s: len({f.get('weapon') for f in fires if f.get('shooter') == s})
                               for s in sorted({f.get('shooter') for f in fires})},
        'bullet_object_on_fire': _count(f.get('exists') for f in fires),
        'bullet_macros': sorted({f.get('pmacro') for f in fires}),
        'impact_callbacks': callbacks,
        'impact_amounts': sorted({a for f in classified for a in f['amounts']}),
        'frames': len(classified),
        'frames_with_several_callbacks': sum(1 for f in classified if f['callbacks'] > 1),
        'callbacks_in_shared_frames': sum(f['callbacks'] for f in classified if f['callbacks'] > 1),
        'max_callbacks_in_one_frame': max((f['callbacks'] for f in classified), default=0),
        'frame_classes': _count(f['class'] for f in classified),
        'bullets_gone_without_impact_frame': len(unassigned),
        'unassigned_by_shooter': _count(g['shooter'] for g in unassigned),
        'named_attack_events': {'attacked_object': _count(a.get('weapon_shooter') for a in bucket['attacked']),
                                'player_attacked_object': _count(a.get('weapon_shooter') for a in bucket['player_attacked'])},
        'named_attack_vs_inferred_frame': {'agree': agree, 'disagree': disagree},
        'observer_damage_callbacks': len(damage),
        'observer_damage_with_event_attacker': sum(1 for e in damage if e.ev_att != '0'),
        'observer_lastattacker_labels': _count(last_attackers),
        'observer_lastattacker_switches': sum(1 for a, b in zip(last_attackers, last_attackers[1:]) if a != b),
        'lastattacker_vs_inferred_frame': {'agree': la_agree, 'disagree': la_disagree},
        'observer_other_victim_damage': _count(e.victim_label for e in other_victims),
        'frame_detail': classified,
    }


LABEL_INDEX = {'company_player': '1', 'second_shooter': '2'}


def _target_damage(events, phase):
    return [e for e in events if e.phase == phase and e.victim_label == 'npc_target'
            and e.kind in ('shield_damaged', 'hull_damaged', 'object_attacked')]


def damage_baselines(events):
    """Shield-only amounts per shooter from the single-shooter controls (shield still up after the hit)."""
    out = {}
    for shooter, phases in (('1', ('control_pilot',)), ('2', ('control_second_yawfix', 'control_second'))):
        for phase in phases:
            amounts = [e.amount for e in _target_damage(events, phase) if e.kind == 'shield_damaged' and e.shield > 0]
            if amounts:
                out[shooter] = _count(f'{a:g}' for a in amounts)
                break
    return out


def damage_attribution(events, phase, baselines):
    """Assign each callback on #4 by its amount alone, then compare with lastattacker and object_attacked."""
    values, shared = {}, set()
    for shooter, counts in baselines.items():
        if len(counts) == 1:
            value = float(next(iter(counts)))
            if value in values:
                shared.add(value)
            values[value] = shooter
    for value in shared:
        # Same gun on both ships: the amount cannot name the shooter.
        del values[value]
    rows = []
    frame_events = {}
    for e in _target_damage(events, phase):
        frame_events.setdefault(f'{e.age:g}', []).append(e)
    for age, evs in frame_events.items():
        notified = None
        used_hulls = set()
        hulls = [e for e in evs if e.kind == 'hull_damaged']
        for e in evs:
            if e.kind == 'object_attacked':
                notified = LABEL_INDEX.get(e.ev_att_label, e.ev_att_label)
                continue
            row = {'age': age, 'seq': e.seq, 'kind': e.kind, 'amount': e.amount, 'after': e.shield if e.kind == 'shield_damaged' else e.hull,
                   'lastattacker': LABEL_INDEX.get(e.last_att_label, e.last_att_label), 'notified_in_frame': notified}
            if e.kind == 'shield_damaged':
                if e.amount in values:
                    row['by_damage'] = values[e.amount]
                    row['class'] = 'shield_only' if e.shield > 0 else 'shield_exact_depletion'
                elif e.shield > 0:
                    row['by_damage'], row['class'] = None, 'shield_unknown_amount'
                else:
                    # Shield emptied by part of one bullet: pair it with a hull piece from this frame.
                    options = [(h, values[e.amount + h.amount]) for h in hulls
                               if h.seq > e.seq and h.seq not in used_hulls and (e.amount + h.amount) in values]
                    shooters = {w for _, w in options}
                    clean = [(h, w) for h, w in options if h.amount not in values]
                    if len(shooters) == 1 and len(clean) == len(options) and len({h.seq for h, _ in clean}) == 1:
                        hull, shooter = clean[0]
                        used_hulls.add(hull.seq)
                        row['by_damage'], row['class'], row['hull_piece'] = shooter, 'overflow_one_bullet', hull.amount
                    else:
                        # Unpaired, several weapons possible, or the hull piece could be a separate full hit.
                        row['by_damage'], row['class'] = None, 'overflow_ambiguous' if options else 'overflow_unpaired'
            else:
                if e.seq in used_hulls:
                    continue
                row['by_damage'] = values.get(e.amount)
                row['class'] = 'hull_only' if row['by_damage'] else 'hull_unknown_amount'
            rows.append(row)

    def tally(cls):
        picked = [r for r in rows if r['class'] in cls]
        return {
            'callbacks': len(picked),
            'by_damage': _count(r['by_damage'] for r in picked),
            'lastattacker_agrees': sum(1 for r in picked if r['by_damage'] and r['lastattacker'] == r['by_damage']),
            'lastattacker_differs': sum(1 for r in picked if r['by_damage'] and r['lastattacker'] != r['by_damage']),
            'notification_in_frame_agrees': sum(1 for r in picked if r['by_damage'] and r['notified_in_frame'] == r['by_damage']),
            'notification_in_frame_differs': sum(1 for r in picked if r['by_damage'] and r['notified_in_frame'] not in (None, r['by_damage'])),
            'no_notification_yet_in_frame': sum(1 for r in picked if r['notified_in_frame'] is None),
        }

    shield_classes = ('shield_only', 'shield_exact_depletion')
    return {
        'damage_values': {f'{v:g}': s for v, s in values.items()},
        'shield_only': tally(shield_classes),
        'overflow_one_bullet': tally(('overflow_one_bullet',)),
        'hull_only': tally(('hull_only',)),
        'classes': _count(r['class'] for r in rows),
        'ambiguous_or_disagreeing': [r for r in rows if not r.get('by_damage') or r['lastattacker'] != r['by_damage']
                                     or r['notified_in_frame'] not in (None, r['by_damage'])],
    }


def analyse(text, events):
    trace = parse_trace(text)
    return {phase: analyse_phase(bucket, [e for e in events if e.phase == phase])
            for phase, bucket in trace.items() if phase in OVERLAP_PHASES}


def accepted(by_phase):
    pilot = by_phase.get('control_pilot', {})
    second = by_phase.get('control_second_yawfix') or by_phase.get('control_second', {})
    both = by_phase.get('overlap', {})
    miss = by_phase.get('miss_pilot', {})
    return (
        set(pilot.get('shots_by_shooter', {})) == {'1'} and pilot.get('impact_callbacks', 0) > 0
        and set(second.get('shots_by_shooter', {})) == {'2'} and second.get('impact_callbacks', 0) > 0
        and set(both.get('shots_by_shooter', {})) == {'1', '2'} and both.get('impact_callbacks', 0) > 0
        and miss.get('shots_by_shooter', {}).get('1', 0) > 0 and miss.get('impact_callbacks', 1) == 0
    )
