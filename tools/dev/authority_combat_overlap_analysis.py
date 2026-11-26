"""Compare direct attack witnesses and projectile disappearance in one native run.

Reads the AUTH_OVERLAP_* trace lines and the observer's events from one native log. The game
names no attacker on shield or hull callbacks. Native runs show that a bullet still exists
during its own impact callbacks and is gone at the next observation (the next impact frame or
a sweep). Disappearance is only a frame-level heuristic. Acceptance uses
direct attack events and independent observations of target damage.
"""
from collections import Counter
import math
import re

PHASE_PREFIX = '[X4LAN] AUTH_COMBAT_PHASE '
TRACE_PREFIX = '[X4LAN] AUTH_OVERLAP_'
PROBE_SETUP = '[X4LAN] AUTH_COMBAT_PROBE setup_ok '
WEAPON_PROFILE = '[X4LAN] AUTH_COMBAT_DRIVER WEAPON_PROFILE '
FIELD = re.compile(r'(\w+)=(\S*)')
ACTOR = re.compile(r'(0x[0-9a-f]+)\(([A-Z0-9]{3}-[A-Z0-9]{3})(?:,w=\d+)?\)')
WEAPON_PAIR = re.compile(r'\[component\.\{(0x[0-9a-f]+)L\},\s*component\.\{(0x[0-9a-f]+)L\}\]')
BULLET_METHODS = {'hitbybullet', 'killmethod.hitbybullet'}
SHOOTER_LABELS = {'1': 'company_player', '2': 'second_shooter'}
# The nominal flight is about 0.1 s from 200 m. Longer observation gaps
# cannot distinguish hits from unrelated disappearances in this fixture.
MAX_FLIGHT_S = 0.5
OVERLAP_PHASES = ('control_pilot', 'control_second', 'control_second_yawfix', 'overlap', 'miss_pilot')


def _fields(text):
    return dict(FIELD.findall(text))


def _number(raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _gone(ids):
    out = []
    for item in filter(None, (ids or '').split(';')):
        parts = item.split(':')
        if len(parts) == 3:
            out.append({'shooter': parts[0], 'projectile': parts[1], 'flight_s': _number(parts[2])})
    return out


def fixture_actors(text):
    rows = [_fields(line.split(PROBE_SETUP, 1)[1]) for line in text.splitlines() if PROBE_SETUP in line]
    if len(rows) != 1:
        return {}
    parsed = {index: ACTOR.fullmatch(rows[0].get(field, ''))
              for index, field in (('1', 'p1'), ('2', 'p2'), ('4', 'npc_target'))}
    if not all(parsed.values()) or len({match[1] for match in parsed.values()}) != 3:
        return {}
    return {index: match.groups() for index, match in parsed.items()}


def parse_trace(text):
    """Collect fixture trace records by phase, preserving their trace order."""
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
        bucket = phases.setdefault(phase, {'fires': [], 'records': [], 'attacked': [], 'player_attacked': [],
                                           'dodges': []})
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
        elif kind == 'DODGE':
            bucket['dodges'].append(f)
    return phases


def _count(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def impact_frames(records, projectiles):
    """Group impact callbacks by frame and attach the bullets seen gone at the next observation."""
    frames = []
    pending = None
    unassigned = []
    for rec in records:
        age = _number(rec.get('age'))
        gone = rec['gone_list']
        sweep = rec['record'] == 'SWEEP'
        same_age = pending is not None and age == pending['time']
        if same_age:
            unassigned.extend(gone)
            if sweep or gone:
                pending['observed_by'] = 'same_age_stop_sweep' if sweep else 'same_age_removal'
        elif pending is not None:
            if pending['observed_by'] is not None:
                unassigned.extend(gone)
            elif age is None or pending['time'] is None or age <= pending['time']:
                pending['observed_by'] = 'invalid_age_boundary'
                unassigned.extend(gone)
            elif sweep and rec.get('tag', '').endswith('_end'):
                pending['observed_by'] = 'end_sweep'
                unassigned.extend(gone)
            else:
                pending['observed_by'] = 'stop_sweep' if sweep else 'next_frame'
                for bullet in gone:
                    fire = projectiles.get(bullet['projectile'])
                    flight = bullet['flight_s']
                    if (fire is not None and fire['shooter'] == bullet['shooter']
                            and fire['time'] < pending['time'] and flight is not None
                            and 0 < flight <= MAX_FLIGHT_S):
                        pending['bullets'].append(bullet)
                    else:
                        unassigned.append(bullet)
            pending = None
        else:
            unassigned.extend(gone)
        if not sweep:
            if pending is None:
                pending = {'age': rec.get('age'), 'time': age, 'callbacks': [], 'bullets': [], 'observed_by': None}
                frames.append(pending)
            pending['callbacks'].append(rec)
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
    elif frame['observed_by'] == 'same_age_stop_sweep':
        label = 'unresolved_stop_sweep_same_frame'
    elif frame['observed_by'] in ('same_age_removal', 'invalid_age_boundary'):
        label = 'unresolved_age_boundary'
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


def _target_event(event, actors, epoch):
    return (actors.get('4') == (event.victim, event.victim_code) and event.victim_reg == 4
            and event.victim_label == 'npc_target' and event.victim_owner == 'xenon'
            and event.registry_epoch == epoch)


def _registered_last_attacker(event, actors):
    """Resolve the target's sampled last attacker independently of who fired this round."""
    source = str(event.last_att_reg)
    if (source in SHOOTER_LABELS and actors.get(source) == (event.last_att, event.last_att_code)
            and event.last_att_label == SHOOTER_LABELS[source] and event.last_att_owner == 'player'
            and event.last_att_isship and event.last_att_ctrl == (source == '1')):
        return source
    return None


def _collision_ages(events, actors, epoch):
    """Fence same-age damage even when the collision callback arrived later."""
    target = actors.get('4')
    if target is None:
        return set()
    ages = set()
    for event in events:
        tracked_victim = _target_event(event, actors, epoch)
        if event.kind == 'collided':
            tracked_collider = (event.registry_epoch == epoch and event.ev_att_reg == 4
                                and target == (event.ev_att, event.ev_att_code))
            if tracked_victim or tracked_collider:
                ages.add(event.age)
        elif tracked_victim and event.kind in ('shield_damaged', 'hull_damaged') and event.collided:
            ages.add(event.age)
    return ages


def _direct_sources(bucket, events, actors, weapon_owners, epoch):
    trace = {'attacked_object': [], 'player_attacked_object': []}
    for kind, rows in (('attacked_object', bucket['attacked']), ('player_attacked_object', bucket['player_attacked'])):
        for row in rows:
            source = row.get('weapon_shooter')
            target, _ = actors.get('4', (None, None))
            actor, _ = actors.get(source, (None, None))
            age = _number(row.get('age'))
            if (source in SHOOTER_LABELS and target is not None and row.get('victim') == target
                    and row.get('comp') == target
                    and row.get('method') in BULLET_METHODS and age is not None
                    and weapon_owners.get(row.get('weapon')) == {source}
                    and (row.get('source') == actor if kind == 'attacked_object' else source == '1')):
                trace[kind].append((age, source, row.get('comp'), row['weapon']))
    # Each eligible witness must also appear as a direct attacker-side event in the
    # reusable observer. The player-only event may corroborate #1 but cannot supply #2.
    direct_trace = set(trace['attacked_object'])
    named = {}
    for event in events:
        source = str(event.ev_att_reg)
        if (event.kind != 'object_attacked_object' or source not in SHOOTER_LABELS
                or event.param2 not in BULLET_METHODS or not _target_event(event, actors, epoch)
                or actors.get(source) != (event.ev_att, event.ev_att_code)
                or event.ev_att_label != SHOOTER_LABELS[source] or event.ev_att_owner != 'player'
                or not event.ev_att_isship or event.ev_att_ctrl != (source == '1')):
            continue
        pair = WEAPON_PAIR.fullmatch(event.param3)
        if pair and (event.age, source, *pair.groups()) in direct_trace:
            named.setdefault(event.age, set()).add(source)
    return trace, named


def _verified_miss(bucket, target_events, actors):
    before = [e for e in target_events if e.kind == 'snapshot' and e.param2 == 'before_miss_pilot']
    after = [e for e in target_events if e.kind == 'snapshot' and e.param2 == 'after_miss_pilot']
    if len(before) != 1 or len(after) != 1 or len(bucket['dodges']) != 1:
        return False
    start, end = before[0], after[0]
    dodge = bucket['dodges'][0]
    dodge_age = _number(dodge.get('age'))
    fire_ages = [_number(row.get('age')) for row in bucket['fires']]
    return (
        dodge.get('target') == actors['4'][0] and dodge_age is not None and bool(fire_ages)
        and all(age is not None and start.age <= age < dodge_age for age in fire_ages)
        and end.age > dodge_age and start.param3 in {'1', 'true'} and end.param3 in {'1', 'true'}
        and start.maxshield > 0 and start.maxhull > 0 and end.maxshield == start.maxshield
        and end.maxhull == start.maxhull and 0 <= start.shield <= end.shield <= end.maxshield
        and 0 < start.hull == end.hull <= end.maxhull
    )


def analyse_phase(bucket, events, actors, epoch):
    fires = bucket['fires']
    projectiles, weapon_owners = {}, {}
    repeated = Counter(f.get('proj') for f in fires)
    for fire in fires:
        shooter, weapon, projectile = (fire.get(key) for key in ('shooter', 'weapon', 'proj'))
        age = _number(fire.get('age'))
        if (shooter not in SHOOTER_LABELS or not weapon or age is None or not projectile or projectile in ('0', 'null')
                or fire.get('exists') not in {'1', 'true'} or repeated[projectile] != 1):
            continue
        projectiles[projectile] = dict(fire, time=age)
        weapon_owners.setdefault(weapon, set()).add(shooter)
    projectile_weapon = {key: fire['weapon'] for key, fire in projectiles.items()}
    frames, unassigned = impact_frames(bucket['records'], projectiles)
    classified = [classify_frame(f, projectile_weapon) for f in frames]
    collision_ages = _collision_ages(events, actors, epoch)
    for frame in classified:
        if _number(frame['age']) in collision_ages:
            frame['class'] = 'collision_ambiguous'
            frame['per_callback_ambiguous'] = True
    trace, named = _direct_sources(bucket, events, actors, weapon_owners, epoch)
    target_events = [e for e in events if _target_event(e, actors, epoch)]
    damage = [e for e in target_events if e.kind in ('shield_damaged', 'hull_damaged')]
    other_victims = [e for e in events if e.kind in ('shield_damaged', 'hull_damaged')
                     and not _target_event(e, actors, epoch)]
    observer_damage = Counter((e.age, e.kind.removesuffix('_damaged'), e.amount, e.comp) for e in damage)
    traced_damage = Counter((f['time'], c.get('kind'), _number(c.get('amount')), c.get('comp'))
                            for f in frames for c in f['callbacks'])
    damage_agrees = observer_damage == traced_damage and all(
        age is not None and amount is not None and amount > 0 for age, _, amount, _ in traced_damage)
    damage_ages = {age for age, _, _, _ in traced_damage} - collision_ages
    direct_hit_shooters = {source for age, sources in named.items() if age in damage_ages for source in sources}
    coincident = []
    agree = disagree = 0
    for frame in classified:
        direct_shooters = named.get(_number(frame['age']), set())
        if (damage_agrees and frame['class'] != 'collision_ambiguous'
                and frame['bullets_needed'] >= 2 and direct_shooters == {'1', '2'}):
            coincident.append(frame['age'])
        if damage_agrees and frame['class'] == 'one_shooter_count_matches' and direct_shooters:
            if direct_shooters == set(frame['shooters']):
                agree += 1
            else:
                disagree += 1
    inferred_by_age = {_number(f['age']): f['shooters'] for f in classified if f['class'] == 'one_shooter_count_matches'}
    la_agree = la_disagree = 0
    coincident_ages = {_number(age) for age in coincident}
    coincident_callbacks = []
    for e in damage:
        last_attacker = _registered_last_attacker(e, actors)
        shooters = inferred_by_age.get(e.age)
        if damage_agrees and shooters and last_attacker is not None:
            if last_attacker in shooters:
                la_agree += 1
            else:
                la_disagree += 1
        if e.age in coincident_ages:
            # Both shooters made contact at this age. Neither the frame nor the
            # sampled last attacker identifies an individual projectile.
            coincident_callbacks.append({
                'age': e.age, 'sequence': e.seq, 'kind': e.kind, 'amount': e.amount,
                'component': e.comp, 'lastattacker': last_attacker,
                'last_attack_time': e.last_att_time if e.last_att_time >= 0 else None,
            })
    last_attackers = [e.last_att_label for e in damage]
    callbacks = sum(f['callbacks'] for f in classified)
    return {
        'shots_by_shooter': _count(f.get('shooter', '?') for f in fires),
        'weapons_by_shooter': {s: len({f.get('weapon') for f in fires if f.get('shooter', '?') == s})
                               for s in sorted({f.get('shooter', '?') for f in fires})},
        'bullet_object_on_fire': _count(f.get('exists', '?') for f in fires),
        'bullet_macros': sorted({f.get('pmacro', '?') for f in fires}),
        'impact_callbacks': callbacks,
        'impact_amounts': sorted({a for f in classified for a in f['amounts']}),
        'frames': len(classified),
        'frames_with_several_callbacks': sum(1 for f in classified if f['callbacks'] > 1),
        'callbacks_in_shared_frames': sum(f['callbacks'] for f in classified if f['callbacks'] > 1),
        'max_callbacks_in_one_frame': max((f['callbacks'] for f in classified), default=0),
        'frame_classes': _count(f['class'] for f in classified),
        'bullets_gone_without_impact_frame': len(unassigned),
        'unassigned_by_shooter': _count(g['shooter'] for g in unassigned),
        'named_attack_events': {kind: _count(source for _, source, _, _ in rows) for kind, rows in trace.items()},
        'direct_hit_shooters': sorted(direct_hit_shooters),
        'direct_contact_frames': len(named),
        'coincident_direct_hit_frames': coincident,
        'coincident_damage_callbacks': coincident_callbacks,
        'named_attack_vs_inferred_frame': {'agree': agree, 'disagree': disagree, 'compared': agree + disagree,
                                           'unverified': len(frames) - agree - disagree},
        'observer_damage_callbacks': len(damage),
        'observer_collision_ambiguous_ages': sorted({event.age for event in damage} & collision_ages),
        'observer_collision_ambiguous_callbacks': sum(event.age in collision_ages for event in damage),
        'observer_damage_matches_trace': damage_agrees,
        'observer_damage_with_event_attacker': sum(1 for e in damage if e.ev_att != '0'),
        'observer_lastattacker_labels': _count(last_attackers),
        'observer_lastattacker_switches': sum(1 for a, b in zip(last_attackers, last_attackers[1:]) if a != b),
        'lastattacker_vs_inferred_frame': {'agree': la_agree, 'disagree': la_disagree,
                                          'compared_callbacks': la_agree + la_disagree,
                                          'unverified_callbacks': len(damage) - la_agree - la_disagree},
        'observer_other_victim_damage': _count(e.victim_label for e in other_victims),
        'miss_dodge_and_health_verified': len(projectiles) == len(fires) and _verified_miss(bucket, target_events, actors),
        'frame_detail': classified,
    }


def analyse(text, events, *, epoch=None):
    trace = parse_trace(text)
    actors = fixture_actors(text)
    return {phase: analyse_phase(bucket, [e for e in events if e.phase == phase], actors, epoch)
            for phase, bucket in trace.items() if phase in OVERLAP_PHASES}


def _root_damage(events, actors, epoch):
    return [e for e in events if e.kind in ('shield_damaged', 'hull_damaged')
            and _target_event(e, actors, epoch) and e.comp == e.victim and e.amount > 0
            and math.isfinite(e.amount) and math.isfinite(e.age) and e.age >= 0
            and math.isfinite(e.shield) and e.shield >= 0 and math.isfinite(e.hull) and e.hull >= 0]


def _damage_controls(text, events, trace, overlap, actors, epoch):
    profiles = [_fields(line.split(WEAPON_PROFILE, 1)[1]) for line in text.splitlines() if WEAPON_PROFILE in line]
    baselines = {}
    for shooter, phases in (('1', ('control_pilot',)), ('2', ('control_second_yawfix', 'control_second'))):
        matches = [row for row in profiles if row.get('label') == SHOOTER_LABELS[shooter]]
        if len(matches) != 1:
            continue
        profile = matches[0]
        amount, bullet = _number(profile.get('hs_shot')), profile.get('bullet')
        if (profile.get('ok') != 'true' or profile.get('modified') != 'false' or profile.get('mixed') != 'false'
                or profile.get('root_hullshield_only') != 'true'
                or profile.get('weapons') != '6' or not re.fullmatch(r'bullet_[a-z0-9_]+_macro', bullet or '')
                or amount is None or amount <= 0):
            continue
        for phase in phases:
            outcome = overlap.get(phase, {})
            if (not outcome.get('observer_damage_matches_trace') or outcome.get('observer_other_victim_damage')
                    or set(outcome.get('shots_by_shooter', {})) != {shooter}
                    or outcome.get('direct_hit_shooters') != [shooter]):
                continue
            if {row.get('pmacro') for row in trace[phase]['fires']} != {bullet}:
                continue
            phase_events = events.get(phase, ())
            damage = _root_damage(phase_events, actors, epoch)
            if len(damage) != outcome['observer_damage_callbacks']:
                continue
            collision_ages = _collision_ages(phase_events, actors, epoch)
            amounts = Counter(e.amount for e in damage if e.kind == 'shield_damaged' and e.shield > 0
                              and e.age not in collision_ages)
            if set(amounts) == {amount}:
                baselines[shooter] = {'amount': amount, 'bullet': bullet, 'phase': phase,
                                      'calibration_callbacks': amounts[amount]}
                break
    return baselines


def _preceding_notices(events, actors, epoch):
    notices = {}
    previous, shooter = None, None
    for event in events:
        if previous is None or event.age != previous.age or event.seq != previous.seq + 1:
            shooter = None
        if event.kind == 'object_attacked' and _target_event(event, actors, epoch):
            source = str(event.ev_att_reg)
            shooter = source if (source in SHOOTER_LABELS and event.param2 in BULLET_METHODS
                                 and actors.get(source) == (event.ev_att, event.ev_att_code)
                                 and event.ev_att_label == SHOOTER_LABELS[source]
                                 and event.ev_att_owner == 'player' and event.ev_att_isship
                                 and event.ev_att_ctrl == (source == '1')) else None
        notices[event.seq] = shooter
        previous = event
    return notices


def _frame_damage_rows(frame, values):
    hulls = [row for row in frame if row.kind == 'hull_damaged']
    options = {row.seq: [(h, values[row.amount + h.amount]) for h in hulls
                         if h.seq > row.seq and row.amount + h.amount in values]
               for row in frame if row.kind == 'shield_damaged' and row.shield == 0}
    hull_candidates = Counter(h.seq for pairs in options.values() for h, _ in pairs)
    used = set()
    for row in frame:
        if row.seq in used:
            continue
        shooter, kind, pair = None, 'unresolved', None
        if row.kind == 'shield_damaged':
            candidates = options.get(row.seq, [])
            if len(candidates) == 1:
                hull, source = candidates[0]
                if (hull.seq == row.seq + 1 and hull_candidates[hull.seq] == 1
                        and hull.amount not in values and hull.hull > 0 and hull.shield == 0):
                    shooter, kind, pair = source, 'overflow', hull
                    used.add(hull.seq)
            if candidates and pair is None:
                kind = 'ambiguous_overflow'
            elif not candidates and row.hull > 0 and row.amount in values:
                shooter, kind = values[row.amount], 'shield' if row.shield > 0 else 'shield_depletion'
        elif hull_candidates[row.seq]:
            kind = 'ambiguous_overflow'
        elif row.hull > 0 and row.shield == 0 and row.amount in values:
            shooter, kind = values[row.amount], 'hull'
        yield row, shooter, kind, pair


def _attribute_damage(events, damage, values, actors, epoch):
    notices = _preceding_notices(events, actors, epoch)
    collision_ages = _collision_ages(events, actors, epoch)
    frames, rows = {}, []
    mixed = alternations = 0
    for event in damage:
        frames.setdefault(event.age, []).append(event)
    for age, frame in frames.items():
        sources = []
        if age in collision_ages:
            classified = ((event, None, 'collision_ambiguous', None) for event in frame)
        else:
            classified = _frame_damage_rows(frame, values)
        for event, source, kind, pair in classified:
            sources.append(source)
            rows.append({
                'age': event.age, 'sequence': event.seq, 'amount': event.amount, 'kind': kind,
                'source_by_amount': source, 'overflow_hull_sequence': None if pair is None else pair.seq,
                'overflow_hull_amount': None if pair is None else pair.amount,
                'lastattacker': _registered_last_attacker(event, actors),
                'preceding_same_age_notification': notices[event.seq],
            })
        mixed += {'1', '2'} <= set(sources)
        alternations += any(a is not None and b is not None and a == c and a != b
                            for a, b, c in zip(sources, sources[1:], sources[2:]))
    assigned = [row for row in rows if row['source_by_amount'] is not None]
    compared = [row for row in assigned if row['preceding_same_age_notification'] is not None]
    last = [row for row in assigned if row['lastattacker'] is not None]
    return {'callbacks': len(damage), 'assigned_hits': len(assigned),
            'unassigned_callbacks': len(damage) - len(assigned) - sum(row['kind'] == 'overflow' for row in assigned),
            'assigned_by_source': _count(row['source_by_amount'] for row in assigned),
            'lastattacker_compared': len(last),
            'lastattacker_agrees': sum(row['lastattacker'] == row['source_by_amount'] for row in last),
            'preceding_same_age_notification_compared': len(compared),
            'preceding_same_age_notification_agrees': sum(row['preceding_same_age_notification'] == row['source_by_amount']
                                                           for row in compared),
            'mixed_game_ages': mixed, 'alternating_game_ages': alternations, 'rows': rows}


def analyse_damage(text, events, overlap, *, epoch):
    """Compare calibrated fixture damage amounts alongside direct-witness acceptance."""
    actors, trace = fixture_actors(text), parse_trace(text)
    by_phase = {phase: [e for e in events if e.phase == phase] for phase in overlap}
    baselines = _damage_controls(text, by_phase, trace, overlap, actors, epoch)
    amounts = {item['amount']: source for source, item in baselines.items()}
    if len(amounts) != 2 or len({item['bullet'] for item in baselines.values()}) != 2:
        return baselines, {}
    result = {}
    for phase, outcome in overlap.items():
        if phase == 'miss_pilot' or not outcome['observer_damage_matches_trace'] or outcome['observer_other_victim_damage']:
            continue
        fires = trace[phase]['fires']
        if any(row.get('shooter') not in baselines or
               row.get('pmacro') != baselines[row['shooter']]['bullet'] for row in fires):
            continue
        phase_events = by_phase[phase]
        damage = _root_damage(phase_events, actors, epoch)
        if (len(damage) != outcome['observer_damage_callbacks']
                or any(a.seq >= b.seq or a.age > b.age for a, b in zip(phase_events, phase_events[1:]))):
            continue
        attribution = _attribute_damage(phase_events, damage, amounts, actors, epoch)
        if all(count <= outcome['shots_by_shooter'].get(source, 0)
               for source, count in attribution['assigned_by_source'].items()):
            result[phase] = attribution
    return baselines, result


def accepted(by_phase):
    pilot = by_phase.get('control_pilot', {})
    second = by_phase.get('control_second_yawfix') or by_phase.get('control_second', {})
    both = by_phase.get('overlap', {})
    miss = by_phase.get('miss_pilot', {})
    for phase in (pilot, second, both, miss):
        if not phase.get('observer_damage_matches_trace'):
            return False
    if any(phase.get('observer_other_victim_damage') for phase in by_phase.values()):
        return False
    return (set(pilot.get('shots_by_shooter', {})) == {'1'} and pilot.get('direct_hit_shooters') == ['1']
            and set(second.get('shots_by_shooter', {})) == {'2'} and second.get('direct_hit_shooters') == ['2']
            and set(both.get('shots_by_shooter', {})) == {'1', '2'} and bool(both.get('coincident_direct_hit_frames'))
            and set(miss.get('shots_by_shooter', {})) == {'1'} and miss.get('impact_callbacks') == 0
            and miss.get('direct_contact_frames') == 0 and miss.get('miss_dodge_and_health_verified') is True)
