"""Evidence table for the map visibility probe (tools/dev/authority_map_visibility_probe.lua).

Reads the probe's log rows ("[X4LAN] AUTH_MAPVIS ...") and reports, for the three views
onscreen / offscreen / back, the NPC's raw-list membership, the camera readback and whether the
requested camera effect was reached. Screen visibility of the icon is judged from the captured
images, not from these rows; the table names the image of each view.

  python tools/dev/authority_map_visibility_evidence.py <x4 log> [--shots DIR] [--json OUT]
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

PREFIX = '[X4LAN] AUTH_MAPVIS '
VIEWS = ('onscreen', 'offscreen', 'back')
POSITION = ('x', 'y', 'z', 'distance')
ROTATION = ('yaw', 'pitch', 'roll')
CAMERA = POSITION + ROTATION
_FIELD = re.compile(r'(\w+)=(\S*)')


def parse_rows(text):
    """Probe rows as (kind, {field: value}) in log order; LIST ids become a list of decimal strings."""
    rows = []
    for line in text.splitlines():
        at = line.find(PREFIX)
        if at < 0:
            continue
        body = line[at + len(PREFIX):].strip()
        kind, _, rest = body.partition(' ')
        if kind == 'ERROR':
            rows.append((kind, {'message': rest}))
            continue
        fields = dict(_FIELD.findall(rest))
        if kind == 'LIST' and 'ids' in fields:
            fields['ids'] = [item for item in fields['ids'].split(',') if item]
        rows.append((kind, fields))
    return rows


def _flag(value):
    if value not in ('true', 'false'):
        raise ValueError(f'not a boolean: {value!r}')
    return value == 'true'


def evaluate(rows, shots=None):
    """Evidence dict. `shots` maps seq (int) to an image path, when the images are at hand."""
    errors = [fields['message'] for kind, fields in rows if kind == 'ERROR']
    pick = next((fields for kind, fields in rows if kind == 'PICK'), None)
    views, seq_of = {}, {}
    table, problems = [], list(errors)
    done = [fields for kind, fields in rows if kind == 'DONE']
    if done != [{'views': '3'}]:
        problems.append('missing or invalid DONE row')
    if not pick or pick.get('npc') in (None, 'none', '0'):
        problems.append('missing NPC fixture')
    for kind, fields in rows:
        name = fields.get('view')
        if name not in VIEWS:
            continue
        if kind in ('VIEW', 'LIST'):
            key = (kind, fields['phase'])
            view = views.setdefault(name, {})
            if key in view:
                problems.append(f'{name}: duplicate {key[0]} {key[1]}')
            view[key] = fields
        elif kind == 'SHOT':
            if name in seq_of:
                problems.append(f'{name}: duplicate SHOT row')
            seq_of[name] = int(fields['seq'])
    if [seq_of.get(name) for name in VIEWS] != [1, 2, 3]:
        problems.append('missing or invalid screenshot sequence')
    npc_ids, distances, selections, players = set(), set(), set(), set()
    for name in VIEWS:
        phases = views.get(name, {})
        before = phases.get(('VIEW', 'before_shot'))
        after = phases.get(('VIEW', 'after_shot'))
        if not before or not after:
            problems.append(f'{name}: missing view rows')
            continue
        raw_before = phases.get(('LIST', 'before_shot'), {}).get('ids')
        raw_after = phases.get(('LIST', 'after_shot'), {}).get('ids')
        stable_list = raw_before is not None and raw_before == raw_after
        if raw_before is None or raw_after is None:
            problems.append(f'{name}: missing raw list rows')
        elif not stable_list:
            problems.append(f'{name}: raw list changed during the screenshot')
        for row, ids in ((before, raw_before), (after, raw_after)):
            npc_ids.add(row['npc'])
            players.add(row['player'])
            distances.add(row['npc_player_m'])
            selections.add(row['selected'])
            if not _flag(row['paused']):
                problems.append(f'{name}: game not paused')
            if not (_flag(row['npc_known']) and _flag(row['npc_radar'])):
                problems.append(f'{name}: NPC not known and radar-visible')
            if ids is not None and (int(row['raw_count']) != len(ids)
                                    or _flag(row['npc_listed']) != (row['npc'] in ids)
                                    or _flag(row['player_listed']) != (row['player'] in ids)):
                problems.append(f'{name}: raw list disagrees with view row')
        if before['focus'] != after['focus'] or any(before[f'camera_{key}'] != after[f'camera_{key}'] for key in CAMERA):
            problems.append(f'{name}: camera or focus changed during the screenshot')
        image = None
        seq = seq_of.get(name)
        if shots and seq in shots:
            path = Path(shots[seq])
            if path.name == f'shot-{seq}-{name}.png' and path.is_file():
                png = path.read_bytes()
                if png.startswith(b'\x89PNG\r\n\x1a\n'):
                    image = {'file': path.name, 'sha256': hashlib.sha256(png).hexdigest()}
        if image is None:
            problems.append(f'{name}: missing screenshot')
        entry = {
            'view': name,
            'control': before['control'],
            'reached': _flag(before['reached']) and _flag(after['reached']),
            'npc_listed': _flag(before['npc_listed']) and _flag(after['npc_listed']),
            'npc_listed_before_after': [_flag(before['npc_listed']), _flag(after['npc_listed'])],
            'player_listed': _flag(before['player_listed']),
            'raw_count': int(before['raw_count']),
            'list_stable': stable_list,
            'focus': before['focus'],
            'selected': int(before['selected']),
            'camera': {key: float(before[f'camera_{key}']) for key in CAMERA},
            'target': ({key: float(before[f'target_{key}']) for key in CAMERA}
                       if 'target_x' in before else None),
            'npc_known': _flag(before['npc_known']),
            'npc_radar': _flag(before['npc_radar']),
            'shot_seq': seq_of.get(name),
            'image': image,
        }
        entry['failed_controls'] = [
            {'phase': phase, 'control': phases['VIEW', phase]['control'], 'focus': phases['VIEW', phase]['focus'],
             'npc_listed': _flag(phases['VIEW', phase]['npc_listed']), 'raw_count': int(phases['VIEW', phase]['raw_count']),
             'camera': {key: float(phases['VIEW', phase][f'camera_{key}']) for key in ('x', 'y', 'z', 'distance')}}
            for phase in ('pan_failed', 'focus_failed') if ('VIEW', phase) in phases]
        table.append(entry)
    if pick and (npc_ids - {pick.get('npc')} or players - {pick.get('player')}):
        problems.append('NPC or player identity differs from the fixture')
    if len(distances) > 1:
        problems.append('NPC-player distance changed although paused')
    if len(selections) > 1:
        problems.append('map selection changed between views')
    by_name = {entry['view']: entry for entry in table}
    focus_changed = len({entry['focus'] for entry in table}) > 1
    conclusion = 'inconclusive'
    accepted = False
    if not problems and len(by_name) == 3:
        on, off, back = by_name['onscreen'], by_name['offscreen'], by_name['back']
        if not (on['reached'] and off['reached'] and back['reached']):
            conclusion = 'inconclusive: a requested camera effect was not reached'
        elif not all(camera_reached(entry['camera'], entry['target']) for entry in table):
            conclusion = 'inconclusive: camera readback did not match the target'
        elif not camera_reached(back['camera'], on['camera']) or camera_reached(off['camera'], on['camera']):
            conclusion = 'inconclusive: camera did not pan away and return'
        elif on['npc_listed'] and back['npc_listed'] and not off['npc_listed']:
            accepted = True
            conclusion = 'listed onscreen, absent offscreen, listed again for this fixture'
        elif on['npc_listed'] and off['npc_listed'] and back['npc_listed']:
            accepted = True
            conclusion = 'listed in all three views for this fixture'
        elif not on['npc_listed'] and not back['npc_listed']:
            conclusion = 'inconclusive: the NPC is not listed even onscreen'
        else:
            conclusion = 'inconclusive: membership does not follow onscreen/offscreen/back consistently'
    return {
        'pick': pick,
        'views': table,
        'problems': problems,
        'conclusion': conclusion,
        'icon_visibility': 'judge from the images; not derived from the API under test',
        'done': done == [{'views': '3'}],
        'accepted': accepted,
        'focus_changed': focus_changed,
    }


def camera_reached(camera, target):
    if target is None or any(not math.isfinite(state[key]) for state in (camera, target) for key in CAMERA):
        return False
    if camera['distance'] <= 0 or target['distance'] <= 0:
        return False
    tolerance = max(1.0, 0.01 * target['distance'])
    return (all(abs(camera[key] - target[key]) <= tolerance for key in POSITION)
            and all(abs(camera[key] - target[key]) <= 1e-3 for key in ROTATION))


def markdown(evidence):
    lines = ['| View | Control | Reached | NPC listed | Raw count | Camera X | Camera distance | Focus | Image |',
             '| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |']
    for entry in evidence['views']:
        image = entry['image']['file'] if entry['image'] else '-'
        lines.append(f"| {entry['view']} | {entry['control']} | {entry['reached']} | {entry['npc_listed']} | "
                     f"{entry['raw_count']} | {entry['camera']['x']:.1f} | {entry['camera']['distance']:.1f} | "
                     f"{entry['focus']} | {image} |")
    lines.append('')
    lines.append(f"Conclusion: {evidence['conclusion']}")
    if evidence['focus_changed']:
        lines.append('Map focus changed between views. The measurements do not isolate the effect of the pan.')
    for problem in evidence['problems']:
        lines.append(f'- problem: {problem}')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('log', type=Path)
    parser.add_argument('--shots', type=Path)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args(argv)
    rows = parse_rows(args.log.read_text(encoding='utf-8', errors='replace'))
    shots = None
    if args.shots:
        shots = {int(path.name.split('-')[1]): path for path in sorted(args.shots.glob('shot-*.png'))}
    evidence = evaluate(rows, shots)
    if args.json:
        with args.json.open('x', encoding='utf-8') as handle:
            json.dump(evidence, handle, indent=2)
    sys.stdout.write(markdown(evidence))
    return 0 if evidence['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
