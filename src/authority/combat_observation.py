"""Reusable read-only combat observation parser, contract guard, and evidence analyzer.

Strictly observational: validates that the reusable MD/Lua combat observer modules
contain zero damage, health, weapon, or destruction mutations, parses structured
[X4LAN] AUTH_COMBAT_OBS and [X4LAN] AUTH_COMBAT_LUA records, and summarizes attacker
reporting, shield/hull state changes, shield-absorbed event visibility, and attack
notification throttling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import xml.etree.ElementTree as ET


MD_EVENT_PREFIX = '[X4LAN] AUTH_COMBAT_OBS '
LUA_READBACK_PREFIX = '[X4LAN] AUTH_COMBAT_LUA '
PHASE_MARKER_PREFIX = '[X4LAN] AUTH_COMBAT_PHASE '

FORBIDDEN_MD_MUTATION_TAGS = frozenset({
    'set_object_hull',
    'set_object_shield',
    'set_object_min_hull',
    'apply_attackstrength',
    'destroy_object',
    'shoot',
    'shoot_at',
    'stop_shooting',
    'create_order',
    'apply_loadout',
    'add_weapon_to_weapongroup',
    'remove_weapon_from_all_weapongroups',
    'set_owner',
})

FORBIDDEN_LUA_MUTATION_CALLS = (
    'SetWeaponLaunchedMacro',
    'SetObjectHull',
    'SetObjectShield',
    'ApplyAttackStrength',
    'AddUITriggeredEvent',
)

EXPECTED_MD_EVENT_TAGS = (
    'event_object_shield_damaged',
    'event_object_hull_damaged',
    'event_object_attacked',
    'event_object_attacked_object',
    'event_player_owned_attacked',
    'event_player_owned_attacked_object',
    'event_player_ship_hit',
    'event_object_collided',
    'event_object_destroyed',
    'event_weapon_fired',
)


@dataclass(frozen=True)
class CombatEvent:
    seq: int
    kind: str
    age: float
    victim: str
    victim_label: str
    victim_code: str
    victim_owner: str
    victim_reg: int
    victim_ctrl: bool
    victim_occ: bool
    ev_att: str
    ev_att_label: str
    ev_att_code: str
    ev_att_owner: str
    ev_att_reg: int
    ev_att_ctrl: bool
    ev_att_isship: bool
    ev_att_pilot: str
    last_att: str
    last_att_label: str
    last_att_code: str
    last_att_owner: str
    last_att_reg: int
    last_att_ctrl: bool
    last_att_isship: bool
    last_att_time: float
    comp: str
    is_root: bool
    comp_macro: str
    param2: str
    param3: str
    amount: float
    shield: float
    maxshield: float
    shieldpct: float
    hull: float
    maxhull: float
    hullpct: float
    collided: bool
    phase: str = 'unlabelled'


@dataclass(frozen=True)
class LuaReadback:
    seq: int
    kind: str
    classification: str
    victim: str
    md_sh: float
    md_hp: float
    lua_ok: bool
    lua_shpct: float | None
    lua_hppct: float | None
    lua_sh: float | None
    lua_shmax: float | None
    lua_hp: float | None
    lua_hpmax: float | None


def validate_md_read_only(xml_text: str) -> dict:
    """Verify combat_observer.xml is well-formed, strictly read-only, and namespace-isolated."""
    root = ET.fromstring(xml_text)
    if root.tag != 'mdscript':
        raise ValueError(f'expected mdscript root, got {root.tag}')
    mutations = [elem.tag for elem in root.iter() if elem.tag in FORBIDDEN_MD_MUTATION_TAGS]
    if mutations:
        raise ValueError(f'forbidden mutating MD tags found: {sorted(set(mutations))}')

    present_events = {elem.tag for elem in root.iter() if elem.tag.startswith('event_')}
    missing_events = [tag for tag in EXPECTED_MD_EVENT_TAGS if tag not in present_events]
    if missing_events:
        raise ValueError(f'missing expected combat MD events: {missing_events}')

    bad_namespaces = []
    for cue in root.iter('cue'):
        if cue.get('instantiate') == 'true' and cue.get('namespace') != 'this':
            bad_namespaces.append(cue.get('name', '<unnamed>'))
    if bad_namespaces:
        raise ValueError(f'instantiated cues missing namespace="this": {bad_namespaces}')

    return {
        'read_only': True,
        'events_registered': sorted(present_events),
        'instantiated_cues_isolated': True,
    }


def validate_lua_read_only(lua_text: str) -> dict:
    """Verify combat_observer.lua contains zero forbidden write/mutation API calls."""
    # Strip single-line Lua comments so header documentation notes do not trip checks.
    code_lines = []
    for line in lua_text.splitlines():
        idx = line.find('--')
        code_lines.append(line if idx < 0 else line[:idx])
    code = '\n'.join(code_lines)
    found = [token for token in FORBIDDEN_LUA_MUTATION_CALLS if token in code]
    if found:
        raise ValueError(f'forbidden mutating Lua calls found: {found}')
    return {'read_only': True, 'checked_calls': list(FORBIDDEN_LUA_MUTATION_CALLS)}


def _float(raw: str, default: float = -1.0) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v


def _opt_float(raw: str | None) -> float | None:
    if raw is None or raw == 'nil':
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_md_event(payload: str, *, phase: str = 'unlabelled') -> CombatEvent | None:
    parts = payload.strip().split('|')
    if len(parts) < 39:
        return None
    try:
        seq = int(float(parts[0]))
        age = float(parts[2])
    except ValueError:
        return None
    return CombatEvent(
        seq=seq,
        kind=parts[1],
        age=age,
        victim=parts[3],
        victim_label=parts[4],
        victim_code=parts[5],
        victim_owner=parts[6],
        victim_reg=int(float(parts[7])) if parts[7].replace('-', '', 1).isdigit() else 0,
        victim_ctrl=parts[8] == '1',
        victim_occ=parts[9] == '1',
        ev_att=parts[10],
        ev_att_label=parts[11],
        ev_att_code=parts[12],
        ev_att_owner=parts[13],
        ev_att_reg=int(float(parts[14])) if parts[14].replace('-', '', 1).isdigit() else 0,
        ev_att_ctrl=parts[15] == '1',
        ev_att_isship=parts[16] == '1',
        ev_att_pilot=parts[17],
        last_att=parts[18],
        last_att_label=parts[19],
        last_att_code=parts[20],
        last_att_owner=parts[21],
        last_att_reg=int(float(parts[22])) if parts[22].replace('-', '', 1).isdigit() else 0,
        last_att_ctrl=parts[23] == '1',
        last_att_isship=parts[24] == '1',
        last_att_time=_float(parts[25], -1.0),
        comp=parts[26],
        is_root=parts[27] == '1',
        comp_macro=parts[28],
        param2=parts[29],
        param3=parts[30],
        amount=_float(parts[31], 0.0),
        shield=_float(parts[32], -1.0),
        maxshield=_float(parts[33], -1.0),
        shieldpct=_float(parts[34], -1.0),
        hull=_float(parts[35], -1.0),
        maxhull=_float(parts[36], -1.0),
        hullpct=_float(parts[37], -1.0),
        collided=parts[38] == '1',
        phase=phase,
    )


def parse_lua_readback(payload: str) -> LuaReadback | None:
    kv = {}
    for token in payload.strip().split():
        if '=' in token:
            k, v = token.split('=', 1)
            kv[k] = v
    if 'seq' not in kv or 'kind' not in kv:
        return None
    try:
        seq = int(kv['seq'])
    except ValueError:
        return None
    return LuaReadback(
        seq=seq,
        kind=kv['kind'],
        classification=kv.get('class', ''),
        victim=kv.get('victim', 'none'),
        md_sh=_float(kv.get('md_sh', '-1'), -1.0),
        md_hp=_float(kv.get('md_hp', '-1'), -1.0),
        lua_ok=kv.get('lua_ok') == 'true',
        lua_shpct=_opt_float(kv.get('lua_shpct')),
        lua_hppct=_opt_float(kv.get('lua_hppct')),
        lua_sh=_opt_float(kv.get('lua_sh')),
        lua_shmax=_opt_float(kv.get('lua_shmax')),
        lua_hp=_opt_float(kv.get('lua_hp')),
        lua_hpmax=_opt_float(kv.get('lua_hpmax')),
    )


def parse_log(text: str) -> tuple[list[CombatEvent], dict[int, LuaReadback]]:
    """Parse MD combat events and Lua health readbacks from an X4 debug log."""
    phase = 'unlabelled'
    events: list[CombatEvent] = []
    readbacks: dict[int, LuaReadback] = {}
    for line in text.splitlines():
        if PHASE_MARKER_PREFIX in line:
            marker = line.split(PHASE_MARKER_PREFIX, 1)[1].strip()
            match = re.search(r'\bphase=([^\s]+)', marker)
            if match:
                phase = match.group(1)
        elif MD_EVENT_PREFIX in line:
            payload = line.split(MD_EVENT_PREFIX, 1)[1].strip()
            ev = parse_md_event(payload, phase=phase)
            if ev is not None:
                events.append(ev)
        elif LUA_READBACK_PREFIX in line:
            payload = line.split(LUA_READBACK_PREFIX, 1)[1].strip()
            rb = parse_lua_readback(payload)
            if rb is not None:
                readbacks[rb.seq] = rb
    return events, readbacks


def summarize_phase(
    events: list[CombatEvent],
    readbacks: dict[int, LuaReadback] | None = None,
    *,
    victim_label: str | None = None,
) -> dict:
    """Summarize attacker identity, shield/hull changes, absorption, and throttle for one phase."""
    readbacks = readbacks or {}
    scoped = [
        e for e in events
        if victim_label is None or e.victim_label == victim_label or e.kind == 'weapon_fired'
    ]
    counts: dict[str, int] = {}
    for e in scoped:
        counts[e.kind] = counts.get(e.kind, 0) + 1

    shield_evs = [e for e in scoped if e.kind == 'shield_damaged']
    hull_evs = [e for e in scoped if e.kind == 'hull_damaged']
    attack_evs = [e for e in scoped if e.kind == 'object_attacked']
    src_attack_evs = [e for e in scoped if e.kind == 'object_attacked_object']
    po_att_evs = [e for e in scoped if e.kind == 'player_owned_attacked']
    po_src_evs = [e for e in scoped if e.kind == 'player_owned_attacked_object']
    pship_hit_evs = [e for e in scoped if e.kind == 'player_ship_hit']
    collided_evs = [e for e in scoped if e.kind == 'collided']
    weapon_evs = [e for e in scoped if e.kind == 'weapon_fired']

    # Count same-age shield/hull candidates. Rounded timestamps cannot pair
    # projectiles uniquely or resolve multiple attackers in the same frame.
    shield_ages_by_victim: dict[str, set[float]] = {}
    for e in shield_evs:
        shield_ages_by_victim.setdefault(e.victim, set()).add(round(e.age, 6))
    overflow_hull_hits = [
        e for e in hull_evs
        if round(e.age, 6) in shield_ages_by_victim.get(e.victim, set())
    ]

    # Measure attack notification intervals (throttle check).
    attack_intervals = [
        round(attack_evs[i].age - attack_evs[i - 1].age, 4)
        for i in range(1, len(attack_evs))
    ]

    # Measure lastattacktime freshness lag on shield/hull callbacks.
    dmg_evs = shield_evs + hull_evs
    lag_deltas = [
        round(e.age - e.last_att_time, 6)
        for e in dmg_evs
        if e.last_att_time >= 0 and (e.age - e.last_att_time) > 1e-3
    ]

    # Attacker reporting breakdown.
    damage_last_attackers = sorted({
        f'{e.last_att_label}:{e.last_att_code}:{e.last_att_owner}:reg={e.last_att_reg}:ctrl={int(e.last_att_ctrl)}:ship={int(e.last_att_isship)}'
        for e in dmg_evs
    })
    attack_event_attackers = sorted({
        f'{e.ev_att_label}:{e.ev_att_code}:{e.ev_att_owner}:reg={e.ev_att_reg}:ctrl={int(e.ev_att_ctrl)}:ship={int(e.ev_att_isship)}:method={e.param2}:p3={e.param3}'
        for e in attack_evs
    })

    # Lua GetComponentData readback coverage & MD-vs-Lua percentage agreement.
    lua_keys_present: dict[str, int] = {
        'hullpercent': 0, 'shieldpercent': 0, 'hull': 0, 'hullmax': 0, 'shield': 0, 'shieldmax': 0,
    }
    sh_diffs = []
    hp_diffs = []
    snap_abs_sh_diffs = []
    snap_abs_hp_diffs = []
    lua_checked = 0
    for e in scoped:
        rb = readbacks.get(e.seq)
        if rb and rb.lua_ok and (rb.kind, rb.victim) == (e.kind, e.victim_label):
            lua_checked += 1
            for key, val in (
                ('hullpercent', rb.lua_hppct), ('shieldpercent', rb.lua_shpct),
                ('hull', rb.lua_hp), ('hullmax', rb.lua_hpmax),
                ('shield', rb.lua_sh), ('shieldmax', rb.lua_shmax),
            ):
                if val is not None:
                    lua_keys_present[key] += 1
            if rb.lua_shpct is not None and e.shieldpct >= 0:
                sh_diffs.append(round(abs(rb.lua_shpct - e.shieldpct), 6))
            if rb.lua_hppct is not None and e.hullpct >= 0:
                hp_diffs.append(round(abs(rb.lua_hppct - e.hullpct), 6))
            if e.kind == 'snapshot':
                if rb.lua_sh is not None and e.shield >= 0:
                    snap_abs_sh_diffs.append(round(abs(rb.lua_sh - e.shield), 6))
                if rb.lua_hp is not None and e.hull >= 0:
                    snap_abs_hp_diffs.append(round(abs(rb.lua_hp - e.hull), 6))

    player_ship_hit_details = [
        {
            'age': e.age,
            'victim': f'{e.victim_label}:{e.victim_code}',
            'attacker': f'{e.ev_att_label}:{e.ev_att_code}:{e.ev_att_owner}:reg={e.ev_att_reg}',
            'projectile': e.param2,
            'projectile_macro': e.param3,
        }
        for e in pship_hit_evs
    ]

    return {
        'counts_by_kind': counts,
        'weapon_shots': len(weapon_evs),
        'shield_damage_callbacks': len(shield_evs),
        'shield_damage_total': round(sum(e.amount for e in shield_evs), 4),
        'shield_damage_amounts': sorted({round(e.amount, 4) for e in shield_evs}),
        'shield_all_root': all(e.is_root for e in shield_evs) if shield_evs else None,
        'hull_damage_callbacks': len(hull_evs),
        'hull_damage_total': round(sum(e.amount for e in hull_evs), 4),
        'hull_damage_amounts': sorted({round(e.amount, 4) for e in hull_evs}),
        'hull_all_root': all(e.is_root for e in hull_evs) if hull_evs else None,
        'overflow_same_age_hull_hits': len(overflow_hull_hits),
        'shield_only_absorbed': bool(shield_evs) and len(hull_evs) == 0,
        'object_attacked_count': len(attack_evs),
        'object_attacked_object_count': len(src_attack_evs),
        'player_owned_attacked_count': len(po_att_evs),
        'player_owned_attacked_object_count': len(po_src_evs),
        'player_ship_hit_count': len(pship_hit_evs),
        'collided_count': len(collided_evs),
        'attack_intervals_s': attack_intervals,
        'damage_last_attackers': damage_last_attackers,
        'attack_event_attackers': attack_event_attackers,
        'lastattacktime_lag_count': len(lag_deltas),
        'lastattacktime_max_lag_s': max(lag_deltas) if lag_deltas else 0.0,
        'lua_readbacks_checked': lua_checked,
        'lua_keys_present': lua_keys_present,
        'max_lua_md_shieldpct_diff': max(sh_diffs, default=None),
        'max_lua_md_hullpct_diff': max(hp_diffs, default=None),
        'snapshot_max_lua_md_abs_sh_diff': max(snap_abs_sh_diffs, default=None),
        'snapshot_max_lua_md_abs_hp_diff': max(snap_abs_hp_diffs, default=None),
        'player_ship_hit_details': player_ship_hit_details,
        'first_shield_state': (
            {
                'age': shield_evs[0].age, 'amount': shield_evs[0].amount,
                'shield': shield_evs[0].shield, 'maxshield': shield_evs[0].maxshield,
                'shieldpct': shield_evs[0].shieldpct,
                'hull': shield_evs[0].hull, 'maxhull': shield_evs[0].maxhull,
                'hullpct': shield_evs[0].hullpct,
                'param2': shield_evs[0].param2, 'comp_macro': shield_evs[0].comp_macro,
            }
            if shield_evs else None
        ),
        'last_shield_state': (
            {
                'age': shield_evs[-1].age, 'amount': shield_evs[-1].amount,
                'shield': shield_evs[-1].shield, 'maxshield': shield_evs[-1].maxshield,
                'shieldpct': shield_evs[-1].shieldpct,
                'hull': shield_evs[-1].hull, 'maxhull': shield_evs[-1].maxhull,
                'hullpct': shield_evs[-1].hullpct,
            }
            if shield_evs else None
        ),
        'first_hull_state': (
            {
                'age': hull_evs[0].age, 'amount': hull_evs[0].amount,
                'shield': hull_evs[0].shield, 'hull': hull_evs[0].hull,
                'maxhull': hull_evs[0].maxhull, 'hullpct': hull_evs[0].hullpct,
                'param2': hull_evs[0].param2, 'comp_macro': hull_evs[0].comp_macro,
            }
            if hull_evs else None
        ),
        'last_hull_state': (
            {
                'age': hull_evs[-1].age, 'amount': hull_evs[-1].amount,
                'shield': hull_evs[-1].shield, 'hull': hull_evs[-1].hull,
                'maxhull': hull_evs[-1].maxhull, 'hullpct': hull_evs[-1].hullpct,
            }
            if hull_evs else None
        ),
    }


def summarize_by_phase(
    events: list[CombatEvent],
    readbacks: dict[int, LuaReadback] | None = None,
    *,
    phase_victims: dict[str, str] | None = None,
) -> dict[str, dict]:
    grouped: dict[str, list[CombatEvent]] = {}
    for e in events:
        grouped.setdefault(e.phase, []).append(e)
    phase_victims = phase_victims or {}
    return {
        phase: summarize_phase(evs, readbacks, victim_label=phase_victims.get(phase))
        for phase, evs in grouped.items()
    }
