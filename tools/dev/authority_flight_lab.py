"""Run the flight lab and compare its scorecard with the kept baseline.

The lab plays scripted flights through a simulated internet link into the real
cockpit playback modules. It needs LuaJIT and no X4. Its results are synthetic
evidence.

    python3 tools/dev/authority_flight_lab.py                 # print the table
    python3 tools/dev/authority_flight_lab.py --compare       # differences from the baseline
    python3 tools/dev/authority_flight_lab.py --wan speed_limit --seeds 1,2,3
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path('tests/fixtures/flight_lab_baseline.json')
LAB = 'tools/dev/authority_flight_lab.lua'


def run_lab(root: Path, *, seeds=(1,), version=4, wan=()) -> list[dict]:
    """Run the Lua scorecard and return its results, sorted by key."""
    command = ['luajit', LAB, '--scorecard', '--seeds', ','.join(str(seed) for seed in seeds),
               '--version', str(version)]
    if wan:
        command += ['--wan', ','.join(wan)]
    done = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=600, check=True)
    return json.loads(done.stdout)


def _cell(value, digits=1):
    if value is None:
        return '-'
    return f'{value:.{digits}f}' if isinstance(value, float) else str(value)


def format_table(results: list[dict]) -> str:
    """One row per flight, link, mode, sample version and seed."""
    header = ('flight/link/mode/version/seed', 'p95 m', 'max m', 'hdg p95', 'freeze s', 'refused', 'jumps',
              'back', 'overshoot m')
    rows = [header]
    for result in results:
        position = result.get('position_error_m', {})
        rows.append((result['key'], _cell(position.get('p95')), _cell(position.get('max')),
                     _cell(result.get('heading_error_deg', {}).get('p95')), _cell(result['longest_freeze_s'], 2),
                     _cell(result['refusals']), _cell(result['jumps']), _cell(result['backwards']),
                     _cell(result.get('overshoot_m'))))
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    lines = []
    for row in rows:
        lines.append('  '.join(cell.ljust(widths[0]) if i == 0 else cell.rjust(widths[i])
                               for i, cell in enumerate(row)))
    return '\n'.join(lines)


def _flatten(result, prefix=''):
    flat = {}
    for name, value in result.items():
        if isinstance(value, dict):
            flat.update(_flatten(value, f'{prefix}{name}.'))
        else:
            flat[prefix + name] = value
    return flat


def compare(baseline: list[dict], results: list[dict]) -> list[str]:
    """Return one line per key whose fields differ; empty when they match."""
    before = {item['key']: _flatten(item) for item in baseline}
    after = {item['key']: _flatten(item) for item in results}
    lines = []
    for key in sorted(before.keys() | after.keys()):
        if key not in after:
            lines.append(f'{key}: missing from results')
        elif key not in before:
            lines.append(f'{key}: not in the baseline')
        else:
            changed = [f'{name} {before[key].get(name)} -> {after[key].get(name)}'
                       for name in sorted(before[key].keys() | after[key].keys())
                       if before[key].get(name) != after[key].get(name)]
            if changed:
                lines.append(f'{key}: ' + ', '.join(changed))
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--seeds', default='1', help='comma-separated seeds (default 1)')
    parser.add_argument('--version', type=int, choices=(3, 4), default=4, help='sample version (default 4)')
    parser.add_argument('--wan', default='', help='comma-separated playback fixes to enable')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--write-baseline', action='store_true', help=f'write the results to {BASELINE}')
    action.add_argument('--compare', action='store_true', help='print differences from the baseline')
    args = parser.parse_args(argv)
    seeds = tuple(int(seed) for seed in args.seeds.split(','))
    wan = tuple(name for name in args.wan.split(',') if name)
    results = run_lab(ROOT, seeds=seeds, version=args.version, wan=wan)
    if args.write_baseline:
        (ROOT / BASELINE).write_text(json.dumps(results, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        print(f'wrote {len(results)} results to {BASELINE}')
        return 0
    if args.compare:
        lines = compare(json.loads((ROOT / BASELINE).read_text(encoding='utf-8')), results)
        print('\n'.join(lines) if lines else 'same as the baseline')
        return 1 if lines else 0
    print(format_table(results))
    return 0


if __name__ == '__main__':
    sys.exit(main())
