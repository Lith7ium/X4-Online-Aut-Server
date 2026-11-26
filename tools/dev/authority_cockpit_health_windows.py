"""Isolated native Windows X4 9.00 cockpit health correction through the real opt-in receiver.

The X4 side runs the REAL src/authority/lua/company_cockpit_health.lua receiver (new + motion_link)
inside the REAL X4LAN_COMPANY_MOTION_SOURCE.new host and the REAL lease-bound
src/authority/md/company_cockpit_health.xml, in the player's living, occupied ship, registered as
Company index 1 in a disposable creative world. The Python side runs the REAL CompanyMotionSource
(enable_cockpit_health, configure, cockpit_health_md, bind_cockpit_health, poll) and the REAL
CompanyCockpitHealthBridge it binds.

Private fixture boundary (test-only, named in the result):
  * fixture server: stands in for the authenticated combat connection. It supplies the server
    clock window (on_clock) and answers bridge.take_request() with fixture health through
    bridge.accept_reply(). No server, TLS or combat worker runs.
  * fixture sampler: replaces the saved-baseline descriptor sampler inside the real motion source
    host. It reports the ship's real sector position at 10 Hz.
  * test rows: TEST|gap and TEST|readback on the same authenticated UDP lease. The gap makes the
    host's own availability check false, so the host sends GAP and calls the receiver's gap().
  * held update: one real HEALTH row produced by the bridge is held in transit across that gap and
    released after its deadline, to model a late pre-gap update.

Independent readbacks come from MD ship properties (shield, hull, percentages), not from the
receiver's Lua GetComponentData read.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tools' / 'dev')]

EXTENSION_ID = 'x4_neon_cockpit_health'
LOGFILE = 'neon-cockpit-health.log'
EPOCH = '5e1c0d7a3b9f42e68a0c4d2f7b1e9a36'
INDEX = 1
UI_FILES = [
    ('combat_observer.lua', 'src/authority/lua/combat_observer.lua'),
    ('company_cockpit_health.lua', 'src/authority/lua/company_cockpit_health.lua'),
    ('company_motion_source.lua', 'src/authority/lua/company_motion_source.lua'),
    ('authority_cockpit_health_x4.lua', 'tools/dev/authority_cockpit_health_x4.lua'),
]
MD_FILES = [
    ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
    ('authority_cockpit_health_probe.xml', 'tools/dev/authority_cockpit_health_probe.xml'),
]
RECEIVER_MD = 'src/authority/md/company_cockpit_health.xml'
X4LAN_NET_DLL = 'src/lockstep/base/native/x4lan_net_64.dll'


def network_dll_bytes(path):
    """The receiver's deadlines use udp.monotonic, so the module must export it."""
    data = Path(path).read_bytes()
    if b'monotonic' not in data:
        raise SystemExit(f'{path} does not export monotonic; pass --network-dll from a current Windows package')
    return data
# Percentages of the ship's own maxima. Integer percentages keep the MD percentage setters exact.
TARGETS = {'down': (40, 55), 'up': (90, 85), 'stale': (25, 30), 'fresh': (65, 70)}
FIXTURE_BOUNDARY = [
    'fixture server: clock window and health replies through bridge.on_clock/take_request/accept_reply; '
    'no server, TLS or combat worker',
    'fixture sampler: real ship sector position at 10 Hz in place of the saved-baseline descriptor sampler',
    'TEST|gap and TEST|readback rows on the authenticated UDP lease',
    'one real bridge HEALTH row held in transit across the gap and released after its deadline',
    'disposable probe MD registers the occupied ship as Company index 1',
]

CONTENT_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<content id="{EXTENSION_ID}" name="Cockpit health correction probe" '
    'description="Disposable opt-in cockpit health correction verification" '
    'author="Cobalt" version="100" date="2026-11-26" save="false">\n'
    '  <dependency version="900" />\n'
    '</content>\n'
)

_LINE = re.compile(r'\[X4LAN\] (AUTH_COCKPIT_HEALTH_(?:DRIVER|READBACK|TRACE)) (.*)$')
_KV = re.compile(r'([a-z_]+)=(\S*)')


def compose_extension(extensions_dir, *, profile, epoch, port, lease, generation, receiver_md, network_dll,
                      gamestart='custom_creative'):
    target = Path(extensions_dir) / EXTENSION_ID
    if target.exists():
        raise FileExistsError(f'{target} already exists')
    for sub in ('ui', 'md', 'native'):
        (target / sub).mkdir(parents=True)
    (target / 'native' / 'x4lan_net_64.dll').write_bytes(network_dll)
    for name, source in MD_FILES:
        (target / 'md' / name).write_bytes((ROOT / source).read_bytes())
    (target / 'md' / 'company_cockpit_health.xml').write_bytes(receiver_md)
    for name, source in UI_FILES:
        (target / 'ui' / name).write_bytes((ROOT / source).read_bytes())
    (target / 'ui' / 'config.lua').write_text(
        'X4NEON_COCKPIT_HEALTH = {\n'
        '  enabled = true,\n'
        f'  extension_id = "{EXTENSION_ID}",\n'
        f'  profile = "{int(profile)}",\n'
        f'  gamestart = "{gamestart}",\n'
        f'  epoch = "{epoch}",\n'
        f'  udp_port = {int(port)},\n'
        f'  lease = "{lease}",\n'
        f'  generation = "{generation}",\n'
        '}\n', encoding='ascii')
    entries = ''.join(f'    <file name="ui/{name}" />\n' for name in ['config.lua', *(n for n, _ in UI_FILES)])
    (target / 'ui.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<addon name="{EXTENSION_ID}">\n'
        '  <environment type="menus">\n'
        '    <dependency name="ego_detailmonitor" />\n'
        f'{entries}'
        '  </environment>\n'
        '</addon>\n', encoding='utf-8')
    (target / 'content.xml').write_text(CONTENT_XML, encoding='utf-8')
    return target


def targets(maxshield, maxhull):
    return {label: (maxshield * s / 100, maxhull * h / 100) for label, (s, h) in TARGETS.items()}


def health_state(revision, shield, maxshield, hull, maxhull):
    return {'revision': revision, 'shield': shield, 'maxshield': maxshield, 'hull': hull, 'maxhull': maxhull}


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_line(line):
    match = _LINE.search(line)
    if not match:
        return None
    kind, body = match.groups()
    head, _, _ = body.partition(' ')
    fields = dict(_KV.findall(body))
    return {'kind': kind, 'head': head if kind.endswith('DRIVER') else None, 'fields': fields, 'text': body.strip()}


class FixtureServer:
    """Private fixture in place of the authenticated server health read."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.answer = None
        self.answered = []
        self.refused = 0

    def step(self):
        request = self.bridge.take_request()
        if request is None:
            return None
        window, sequence, after = request
        if self.answer is None:
            # A failed read has no side effects. The bridge asks again later.
            self.bridge.accept_reply(window, sequence, None)
            self.refused += 1
            return None
        label, state = self.answer
        self.answer = None
        accepted = self.bridge.accept_reply(window, sequence, dict(state))
        self.answered.append(dict(state, label=label, window=window, sequence=sequence,
                                  after_revision=after, accepted=accepted))
        return label, sequence


def hold_next_update(bridge):
    """Wrap the bridge's one-shot delivery so one HEALTH row can be held in transit."""
    original = bridge.deliver
    gate = {'armed': False, 'held': None}

    def deliver(send):
        def gated(message):
            if gate['armed'] and message.startswith('HEALTH|'):
                gate['armed'] = False
                gate['held'] = message
                return True  # The datagram left the owner but is delayed in transit.
            return send(message)
        return original(gated)

    bridge.deliver = deliver
    return gate


def _key(text):
    """MD may print a whole number as 3 or 3.0; Lua formats sequences with %.0f."""
    value = _number(text)
    return str(int(value)) if value is not None and value == int(value) else str(text)


def _near(actual, expected, tolerance):
    return actual is not None and expected is not None and abs(actual - expected) <= tolerance


def evaluate(events, plan, held, stats, maxima):
    """Check the corrections, the ignored held update and the fresh baseline from the native log."""
    maxshield, maxhull = maxima
    goals = targets(maxshield, maxhull)
    receipts = {}
    readbacks = {}
    traces = {}
    rx = []
    for event in events:
        fields = event['fields']
        if event['kind'].endswith('DRIVER') and event['head'] == 'RECEIPT':
            receipts.setdefault(_key(fields.get('sequence')), []).append(fields)
        elif event['kind'].endswith('DRIVER') and event['head'] == 'RX':
            rx.append(event['text'])
        elif event['kind'].endswith('READBACK'):
            readbacks.setdefault(fields.get('tag'), []).append(fields)
        elif event['kind'].endswith('TRACE'):
            traces.setdefault(_key(fields.get('sequence')), []).append(fields)
    shield_slack = 0.05 * maxshield  # Shields recharge between the write and the MD readback.
    report = {}

    def correction(label, direction):
        entry = next((a for a in plan if a['label'] == label), None)
        if entry is None:
            return {'ok': False, 'reason': 'not answered'}
        sequence = str(entry['sequence'])
        shield, hull = goals[label]
        receipt = (receipts.get(sequence) or [{}])[0]
        prepare = next((t for t in traces.get(sequence, []) if t.get('control') == 'Prepare'), {})
        apply = next((t for t in traces.get(sequence, []) if t.get('control') == 'Apply'), {})
        readback = (readbacks.get(f'receipt-{sequence}-confirmed') or [{}])[0]
        before = (_number(prepare.get('shield')), _number(prepare.get('hull')))
        after = (_number(readback.get('shield')), _number(readback.get('hull')))
        moved = ((before[0] is not None and before[1] is not None)
                 and (before[0] > shield and before[1] > hull if direction == 'down'
                      else before[0] < shield and before[1] < hull))
        ok = (receipt.get('status') == 'confirmed' and moved and bool(apply)
              and _near(_number(receipt.get('shield')), shield, 1e-3) and _near(_number(receipt.get('hull')), hull, 1e-3)
              and _near(after[1], hull, 1e-3) and after[0] is not None and shield - 1e-3 <= after[0] <= shield + shield_slack
              and readback.get('occupied') in ('1', 'true') and readback.get('operational') in ('1', 'true'))
        return {'ok': ok, 'direction': direction, 'sequence': entry['sequence'], 'window': entry['window'],
                'target': {'shield': shield, 'hull': hull}, 'before_md': {'shield': before[0], 'hull': before[1]},
                'receipt': receipt, 'md_readback': readback}

    report['down'] = correction('down', 'down')
    report['up'] = correction('up', 'up')
    report['fresh'] = correction('fresh', 'down')

    stale = {'ok': False}
    if held is not None:
        fields = held['message'].split('|')
        sequence = fields[6]
        arrived = [line for line in rx if line.startswith('RX HEALTH ')
                   and _key(dict(_KV.findall(line)).get('sequence')) == sequence]
        deadline_in = None
        if arrived:
            deadline_in = _number(dict(_KV.findall(arrived[-1])).get('deadline_in'))
        pre = (readbacks.get('pre_replay') or [{}])[0]
        post = (readbacks.get('post_replay') or [{}])[0]
        up_hull = goals['up'][1]
        stale_shield, stale_hull = goals['stale']
        fresh = report['fresh']
        stale = {
            'held_revision': int(fields[1]), 'window': int(fields[5]), 'sequence': int(sequence),
            'anchor_sample': int(fields[7]), 'held_values': {'shield': stale_shield, 'hull': stale_hull},
            'arrived_after_gap': bool(arrived), 'deadline_in_at_arrival': deadline_in,
            'trace_commands': traces.get(sequence, []), 'receipts': receipts.get(sequence, []),
            'md_pre_replay': pre, 'md_post_replay': post,
        }
        stale['ok'] = (bool(arrived) and deadline_in is not None and deadline_in < 0
                       and not stale['trace_commands'] and not stale['receipts']
                       and _near(_number(pre.get('hull')), up_hull, 1e-3)
                       and _near(_number(post.get('hull')), up_hull, 1e-3)
                       and (_number(post.get('shield')) or 0) > stale_shield + 1
                       and fresh.get('sequence', 0) > int(sequence))
    report['stale_pre_gap'] = stale
    report['bridge'] = stats
    report['accepted'] = (all(report[name]['ok'] for name in ('down', 'up', 'stale_pre_gap', 'fresh'))
                          and stats.get('confirmed') == 3 and stats.get('unknown') == 0 and stats.get('rejected') == 0)
    return report


def main(argv=None):
    import authority_combat_observation_windows as host
    from authority_company_cockpit_windows import make_windows_pid_udp_validator
    from authority.company_motion_source import CompanyMotionSource
    from lockstep.launcher import game as game_mod, profile as profile_mod, steam

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--game')
    parser.add_argument('--out', required=True)
    parser.add_argument('--profile', type=int, required=True)
    parser.add_argument('--gamestart', default='custom_creative')
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--timeout', type=float, default=240.0)
    parser.add_argument('--network-dll', default=str(ROOT / X4LAN_NET_DLL),
                        help='x4lan_net_64.dll that exports monotonic (the committed copy predates it)')
    parser.add_argument('--preflight', action='store_true', help='compose and check only; never launch X4')
    args = parser.parse_args(argv)

    if os.name != 'nt' or sys.version_info < (3, 12):
        raise SystemExit('Python 3.12+ required on Windows for safe x4ls view/profile teardown')
    if args.profile not in profile_mod.PROFILES or args.profile in (45841, 45851):
        raise SystemExit(f'Profile {args.profile} is not a free disposable launcher lane')
    network_dll = network_dll_bytes(args.network_dll)
    out_dir = Path(args.out).resolve()
    if out_dir.exists():
        raise SystemExit(f'{out_dir} already exists; use a fresh output directory')
    if host.x4_running():
        raise SystemExit('ABORT: X4.exe is already running')
    ok, msg = steam.status()
    if not ok:
        raise SystemExit(msg)
    game_dir = Path(args.game).resolve() if args.game else game_mod.find_install()
    game_mod.verify_exe(game_dir)
    documents = profile_mod.documents()
    if (documents / str(args.profile)).exists():
        raise SystemExit(f'profile folder {args.profile} already exists')
    view_dir = Path(os.path.splitdrive(str(game_dir.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-health-{out_dir.name}'
    if view_dir.exists():
        raise SystemExit(f'{view_dir} already exists')

    source = CompanyMotionSource()
    source.enable_cockpit_health()
    config = source.configure(EPOCH, INDEX, operational_bits='1')
    receiver_md = source.cockpit_health_md((ROOT / RECEIVER_MD).read_bytes())
    generation = secrets.token_hex(16)  # Fixture admission generation.
    bridge = source.bind_cockpit_health({'epoch': EPOCH, 'index': INDEX, 'generation': generation})
    fixture = FixtureServer(bridge)
    gate = hold_next_update(bridge)
    out_dir.mkdir(parents=True)

    if args.preflight:
        scratch = out_dir / 'preflight-extension'
        compose_extension(scratch, profile=args.profile, epoch=EPOCH, port=config['port'], lease=config['lease'],
                          generation=config['generation'], receiver_md=receiver_md, network_dll=network_dll)
        names = sorted(str(p.relative_to(scratch)) for p in scratch.rglob('*') if p.is_file())
        md = (scratch / EXTENSION_ID / 'md' / 'company_cockpit_health.xml').read_bytes()
        source.close()
        result = {'preflight': True, 'files': names, 'network_dll_sha256': hashlib.sha256(network_dll).hexdigest(), 'lease_substituted': b'__COCKPIT_HEALTH_LEASE__' not in md,
                  'cockpit_health_config': config.get('cockpit_health') is True, 'profile_free': True,
                  'view_free': True, 'x4_running': host.x4_running()}
        shutil.rmtree(scratch)
        (out_dir / 'preflight.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['lease_substituted'] and result['cockpit_health_config'] else 1)

    protected_before = {
        'other_profiles': {e.name: host.tree_metadata(e) for e in sorted(documents.iterdir())
                           if e.is_dir() and e.name != str(args.profile)},
        'game_extensions': sorted(e.name for e in (game_dir / 'extensions').iterdir()),
    }
    (out_dir / 'protected-before.json').write_text(json.dumps(protected_before, indent=2), encoding='utf-8')
    ctypes.windll.user32.SetProcessDPIAware()

    process = job = log_path = None
    view_created = False
    outcome = 'timeout'
    events = []
    timeline = []
    held = None
    maxima = None
    clock_skew = None
    started = time.monotonic()

    def mark(name, **extra):
        timeline.append(dict(extra, phase=name, t=round(time.perf_counter(), 6)))

    try:
        game_mod.create_view(game_dir, view_dir)
        view_created = True
        compose_extension(view_dir / 'extensions', profile=args.profile, epoch=EPOCH, port=config['port'],
                          lease=config['lease'], generation=config['generation'], receiver_md=receiver_md,
                          network_dll=network_dll, gamestart=args.gamestart)
        profile_dir = profile_mod.prepare_fresh(args.profile, [*game_mod.dlc_ids(view_dir), EXTENSION_ID],
                                                root=documents, width=args.width, height=args.height, fast=True)
        log_path = profile_dir / LOGFILE
        if log_path.exists():
            raise FileExistsError(f'unexpected log already at {log_path}')
        command = [str(view_dir / 'X4.exe'), '-personalfolderid', str(args.profile), '-skipintro',
                   '-noworkshopsync', '-nocputhrottle', '-debug', 'scripts', '-logfile', LOGFILE]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= host.STARTF_USESHOWWINDOW
        si.wShowWindow = host.SW_SHOWNOACTIVATE
        process = subprocess.Popen(command, cwd=str(view_dir), env=steam.client_environment(), startupinfo=si)
        job = host.attach_kill_on_close_job(process.pid)
        validator = make_windows_pid_udp_validator(process)
        bridge.on_clock(1, True)  # Fixture server clock: window 1, running.

        hwnd = None
        last_tuck = last_read = 0.0
        position = 0
        pending = ''
        phase = 'boot'
        phase_at = time.monotonic()
        samples = []
        expect = None
        wait_until = None
        deadline = time.monotonic() + args.timeout

        def test(row):
            if not source._send_to_peer(validator, row):
                raise RuntimeError(f'could not send {row.split("|")[1]}')

        def heads(head):
            return [e for e in events if e['head'] == head]

        def receipt_for(sequence):
            return next((e for e in heads('RECEIPT') if e['fields'].get('sequence') == str(sequence)), None)

        def enter(name):
            nonlocal phase, phase_at
            phase, phase_at = name, time.monotonic()
            mark(name)

        while time.monotonic() < deadline:
            now = time.monotonic()
            if hwnd is None:
                hwnd = host.find_window(process.pid)
                if hwnd is not None:
                    host.tuck_window_bottom_corner(hwnd)
                    last_tuck = now
            elif now - last_tuck >= 0.75:
                host.tuck_window_bottom_corner(hwnd)
                last_tuck = now
            if now - last_read >= 0.05 and log_path.is_file():
                last_read = now
                try:
                    with log_path.open('rb') as handle:
                        handle.seek(position)
                        chunk = handle.read()
                        position += len(chunk)
                except OSError:
                    chunk = b''
                if chunk:
                    pending += chunk.decode('utf-8', errors='replace')
                    lines, _, pending = pending.rpartition('\n')
                    for line in lines.splitlines():
                        event = parse_line(line)
                        if event is None:
                            continue
                        event['seen'] = round(time.perf_counter(), 6)
                        events.append(event)
                        if event['head'] == 'ERROR':
                            raise RuntimeError('driver error: ' + event['text'])
                        if event['head'] == 'READY':
                            fields = event['fields']
                            maxima = (float(fields['lua_maxshield']), float(fields['lua_maxhull']))
                            if maxima != (float(fields['maxshield']), float(fields['maxhull'])):
                                raise RuntimeError('MD and Lua ship maxima differ')
                            clock_skew = round(event['seen'] - float(fields['mono']), 6)
            if process.poll() is not None:
                outcome = f'exited_{process.returncode}'
                break

            sample = source.poll(validator)
            if sample is not None:
                samples.append((time.perf_counter(), sample['sequence']))
            answered = fixture.step()
            if answered is not None:
                mark('answered', label=answered[0], sequence=answered[1])
                expect = answered
            if gate['held'] is not None and held is None:
                fields = gate['held'].split('|')
                held = {'message': gate['held'], 'deadline': float(fields[8]), 'held_at': time.perf_counter()}
                mark('held', sequence=int(fields[6]), revision=int(fields[1]))

            goals = targets(*maxima) if maxima else None
            if phase == 'boot':
                if maxima and source._peer is not None and len(samples) >= 5:
                    test('TEST|readback|pre_down')
                    fixture.answer = ('down', health_state(1, goals['down'][0], maxima[0], goals['down'][1], maxima[1]))
                    enter('down')
            elif phase in ('down', 'up', 'fresh'):
                if expect and expect[0] == phase:
                    receipt = receipt_for(expect[1])
                    if receipt is not None:
                        if receipt['fields'].get('status') != 'confirmed':
                            raise RuntimeError(f'{phase} receipt {receipt["fields"].get("status")}')
                        if wait_until is None:
                            wait_until = now + 0.8
                        elif now >= wait_until:
                            wait_until = None
                            if phase == 'down':
                                fixture.answer = ('up', health_state(2, goals['up'][0], maxima[0], goals['up'][1], maxima[1]))
                                enter('up')
                            elif phase == 'up':
                                gate['armed'] = True
                                fixture.answer = ('stale', health_state(3, goals['stale'][0], maxima[0],
                                                                        goals['stale'][1], maxima[1]))
                                enter('hold')
                            else:
                                test('TEST|readback|final')
                                test('TEST|done')
                                enter('done_wait')
            elif phase == 'hold':
                if held is not None:
                    test('TEST|gap|0.8')
                    enter('gap')
            elif phase == 'gap':
                gap_end = next((e for e in heads('GAP_END')), None)
                clear_seen = any(e['text'].startswith('RX HEALTH_CLEAR ') and int(e['fields'].get('revision', 0))
                                 > int(held['message'].split('|')[1]) for e in heads('RX'))
                post = [s for s in samples if gap_end and s[0] > gap_end['seen']]
                if gap_end and clear_seen and len(post) >= 3 and time.perf_counter() > held['deadline'] + 0.25:
                    test('TEST|readback|pre_replay')
                    wait_until = now + 0.3
                    enter('replay_ready')
            elif phase == 'replay_ready':
                if now >= wait_until:
                    test(held['message'])
                    held['released_at'] = time.perf_counter()
                    mark('released', late_by=round(held['released_at'] - held['deadline'], 6))
                    wait_until = now + 1.5
                    enter('replay_watch')
            elif phase == 'replay_watch':
                if now >= wait_until:
                    test('TEST|readback|post_replay')
                    wait_until = now + 0.4
                    enter('replay_post')
            elif phase == 'replay_post':
                if now >= wait_until:
                    wait_until = None
                    fixture.answer = ('fresh', health_state(4, goals['fresh'][0], maxima[0], goals['fresh'][1], maxima[1]))
                    enter('fresh')
            elif phase == 'done_wait':
                if heads('DONE'):
                    outcome = 'done'
                    time.sleep(0.5)
                    break
            if phase not in ('boot',) and now - phase_at > 30:
                raise TimeoutError(f'phase {phase} stalled')
            time.sleep(0.005)
    except Exception as error:  # Recorded in the result; cleanup always runs.
        outcome = f'error: {type(error).__name__}: {error}'
    finally:
        stats = bridge.stats()
        try:
            source.close()
        except Exception:
            pass
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if job is not None:
            try:
                ctypes.windll.kernel32.CloseHandle(job)
            except OSError:
                pass
        if log_path is not None and log_path.is_file():
            (out_dir / LOGFILE).write_bytes(log_path.read_bytes())
        if view_created:
            host.remove_view_patiently(game_mod, view_dir)

    protected_after = {
        'other_profiles': {e.name: host.tree_metadata(e) for e in sorted(documents.iterdir())
                           if e.is_dir() and e.name != str(args.profile)},
        'game_extensions': sorted(e.name for e in (game_dir / 'extensions').iterdir()),
    }
    (out_dir / 'protected-after.json').write_text(json.dumps(protected_after, indent=2), encoding='utf-8')
    log_text = (out_dir / LOGFILE).read_text(encoding='utf-8', errors='replace') if (out_dir / LOGFILE).is_file() else ''
    all_events = [e for e in (parse_line(line) for line in log_text.splitlines()) if e is not None]
    tokens = ('authority_cockpit_health_x4', 'company_cockpit_health', 'company_motion_source', 'combat_observer',
              'X4AuthorityCockpitHealthProbe', 'X4AuthorityCompanyCockpitHealth', 'X4AuthorityShipRegistry')
    project_errors = [line.strip() for line in log_text.splitlines()
                      if any(t in line for t in tokens) and '[X4LAN]' not in line
                      and ('[=ERROR=]' in line or 'Error in MD cue' in line or 'Error while executing' in line)]
    report = evaluate(all_events, fixture.answered, held, stats, maxima) if maxima else {'accepted': False}
    checks = {
        'outcome_done': outcome == 'done',
        'protected_unchanged': protected_before == protected_after,
        'view_removed': not view_dir.exists(),
        'x4_running_after': host.x4_running(),
        'project_errors': project_errors[:20],
    }
    accepted = (report.get('accepted') is True and checks['outcome_done'] and checks['protected_unchanged']
                and checks['view_removed'] and not checks['x4_running_after'] and not project_errors)
    result = {
        'accepted': accepted, 'outcome': outcome, 'elapsed_s': round(time.monotonic() - started, 2),
        'profile': args.profile, 'x4_exe_sha256': host.sha256_file(game_dir / 'X4.exe'),
        'network_dll_sha256': hashlib.sha256(network_dll).hexdigest(),
        'fixture_boundary': FIXTURE_BOUNDARY, 'maxima': maxima, 'qpc_skew_s': clock_skew,
        'fixture_answers': fixture.answered, 'fixture_refused_reads': fixture.refused,
        'held': {k: v for k, v in (held or {}).items() if k != 'message'},
        'checks': checks, 'report': report, 'timeline': timeline,
        'driver_lines': [e['text'] for e in all_events if e['kind'].endswith('DRIVER') and e['head'] != 'RX'][:200],
        'rx_lines': [e['text'] for e in all_events if e['head'] == 'RX'][:200],
        'readbacks': [e['text'] for e in all_events if e['kind'].endswith('READBACK')],
        'traces': [e['text'] for e in all_events if e['kind'].endswith('TRACE')],
    }
    (out_dir / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('accepted', 'outcome', 'elapsed_s', 'maxima', 'qpc_skew_s')}
                     | {'checks': checks, 'result_json': str(out_dir / 'result.json')}, indent=2))
    raise SystemExit(0 if accepted else 1)


if __name__ == '__main__':
    main()
