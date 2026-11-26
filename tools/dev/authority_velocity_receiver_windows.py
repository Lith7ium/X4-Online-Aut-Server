"""Native Windows X4 9.00 receiver for recorded Company velocity samples on the velocity playback trial.

Reuses Neon's isolated cockpit harness (authority_company_cockpit_windows / _x4.lua): real loopback
CompanyMotionService, CompanyMotionClient and CompanyMotionSource feed the REAL cockpit observer, observer
stream, cockpit playback and hull playback inside a disposable X4 profile. Test-only additions:
  * a recorded trajectory (JSON list of V3 samples) replaces the synthetic constant-velocity pilot. It is
    translated so its first sample sits on the remote Pulsar, and mirrored per axis (distance preserving)
    to keep clear of the occupied ship. Samples are sent at the recorded capture intervals;
  * authority_velocity_receiver_trace.lua logs the bind, each observation, each dispatched write (with its
    step) and each delayed readback with an independent FFI position read;
  * --network-dll supplies a native module with monotonic(), so playback and the runner share QPC.
The run reports every dispatched step over 50 m. It is accepted when there is one initial bind, no fault
or refusal, every write and readback succeeds, and the occupied ship does not move.
"""
import argparse
import collections
import ctypes
import csv
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tools' / 'dev')]

import authority_company_cockpit_windows as cockpit  # noqa: E402

LOGFILE = 'cobalt-velocity-receiver.log'
TRACE_LUA = 'tools/dev/authority_velocity_receiver_trace.lua'
STEP_LIMIT = 50.0
CLEARANCE = 400.0
READBACK_TOLERANCE = 5.0


def load_trajectory(path):
    path = Path(path)
    if path.suffix.lower() == '.tsv':
        with path.open(encoding='ascii', newline='') as stream:
            rows = [dict(sequence=row['sequence'], source_time=row['relative_game_s'],
                         capture_time=row['relative_capture_s'],
                         real_velocity={axis: row[f'v{axis}_m_s'] for axis in 'xyz'},
                         **{axis: row[f'd{axis}_m'] for axis in 'xyz'})
                    for row in csv.DictReader(stream, delimiter='\t')]
    else:
        data = json.loads(path.read_text(encoding='utf-8'))
        rows = data['samples'] if isinstance(data, dict) else data
    samples = []
    track = None
    for row in rows:
        identity = tuple(row.get(key) for key in ('epoch', 'index', 'generation', 'sector_macro'))
        if track is None:
            track = identity
        elif identity != track:
            raise ValueError('trajectory must contain a single ship, generation and sector')
        velocity = row.get('real_velocity')
        if isinstance(velocity, (list, tuple)):
            velocity = dict(zip('xyz', velocity))
        samples.append({
            'sequence': int(row['sequence']), 'source_time': float(row['source_time']),
            'capture_time': float(row['capture_time']), 'x': float(row['x']), 'y': float(row['y']),
            'z': float(row['z']),
            'real_velocity': None if velocity is None else {axis: float(velocity[axis]) for axis in 'xyz'},
        })
    if not samples:
        raise ValueError('trajectory is empty')
    for before, after in zip(samples, samples[1:]):
        if not (after['sequence'] > before['sequence'] and after['capture_time'] > before['capture_time']
                and after['source_time'] >= before['source_time']):
            raise ValueError(f'trajectory is not increasing at sequence {after["sequence"]}')
    return samples


def source_position_error(samples, *, position_only_baseline=False):
    """Score the next captured XYZ on one replay trajectory, before receiver placement."""
    hold_errors, projection_errors = [], []
    skipped = 0
    first_gap = None
    for before, after in zip(samples, samples[1:]):
        elapsed = after['capture_time'] - before['capture_time']
        displacement = [after[axis] - before[axis] for axis in 'xyz']
        hold = math.hypot(*displacement)
        velocity = before['real_velocity']
        if first_gap is None:
            if position_only_baseline:
                velocity = None
            first_gap = {'sequences': [before['sequence'], after['sequence']], 'capture_dt_s': elapsed,
                         'displacement_m': hold, 'previous_velocity_available': velocity is not None}
        if velocity is None:
            skipped += 1
            continue
        hold_errors.append(hold)
        projection_errors.append(math.hypot(*(delta - velocity[axis] * elapsed
                                              for axis, delta in zip('xyz', displacement))))

    def stats(errors):
        return {'mean': statistics.fmean(errors) if errors else None, 'max': max(errors, default=None)}

    return {'compared_pairs': len(hold_errors), 'skipped_no_previous_velocity': skipped,
            'first_gap': first_gap, 'hold_previous_position_error_m': stats(hold_errors),
            'project_previous_velocity_error_m': stats(projection_errors)}


def synthetic_trajectory(count=44, speed=1524.0, seed_interval=0.127):
    """Shake-out pilot only: a curved 1.5 km/s path with uneven capture intervals."""
    samples, t, x, z, heading = [], 0.0, 0.0, 0.0, 0.0
    for index in range(count):
        if index:
            dt = seed_interval + (0.03 if index % 3 == 0 else -0.015)
            heading += 0.02
            t += dt
            x += speed * math.cos(heading) * dt
            z += speed * math.sin(heading) * dt
        samples.append({'sequence': 143 + index, 'source_time': t, 'capture_time': t, 'x': x, 'y': 0.0, 'z': z,
                        'real_velocity': {'x': speed * math.cos(heading), 'y': 0.0,
                                          'z': speed * math.sin(heading)}})
    return samples


def place(samples, start, own=None, bind_sample=0, flip=None):
    """Translate samples[bind_sample] onto start and mirror axes (distance preserving) away from own."""
    base = samples[bind_sample]
    best = None
    candidates = [tuple(flip)] if flip else itertools.product((1, -1), repeat=3) if own else [(1, 1, 1)]
    for flip in candidates:
        points = [tuple(start[a] + f * (s[a] - base[a]) for a, f in zip('xyz', flip)) for s in samples]
        if own:
            distances = [math.dist(p, (own['x'], own['y'], own['z'])) for p in points]
            near, far = min(distances), max(distances)
            key = (near >= CLEARANCE, -far if near >= CLEARANCE else near)
        else:
            near = far = None
            key = (True, 0)
        if best is None or key > best[0]:
            best = (key, flip, points, near, far)
    _, flip, points, near, far = best
    placed = []
    for sample, point in zip(samples, points):
        velocity = sample['real_velocity']
        placed.append({**sample, 'x': point[0], 'y': point[1], 'z': point[2],
                       'real_velocity': None if velocity is None else
                       {a: f * velocity[a] for a, f in zip('xyz', flip)}})
    return placed, {'flip': list(flip), 'nearest_own_m': near, 'farthest_own_m': far}


def schedule(samples, epoch, generation, sector):
    """V3 rows for the queued pilot, keeping the recorded capture spacing and a local origin."""
    first = samples[0]
    rows = []
    for sample in samples:
        offset = sample['capture_time'] - first['capture_time']
        rows.append((offset, {
            'version': 3, 'epoch': epoch, 'generation': generation, 'index': 2,
            'sequence': sample['sequence'], 'sector_macro': sector,
            'source_time': 100.0 + sample['source_time'] - first['source_time'],
            'capture_time': 500.0 + offset,
            'x': sample['x'], 'y': sample['y'], 'z': sample['z'], 'real_velocity': sample['real_velocity'],
        }))
    return rows


def parse_trace(payload):
    """'TRACE KIND k=v ...' -> (kind, fields) or None."""
    if not payload.startswith('TRACE '):
        return None
    kind, _, rest = payload[len('TRACE '):].partition(' ')
    return kind, cockpit.parse_kv(rest)


def confirmed_zone_crossings(traces):
    crossings = []
    previous_write = None
    for kind, fields in traces:
        if kind == 'BIND':
            previous_write = None
        elif kind == 'WRITE':
            previous_write = fields if fields.get('ok') == 'true' else None
        elif kind == 'READBACK' and previous_write is not None:
            write, previous_write = previous_write, None
            before, destination = write.get('zone_before'), write.get('zone_target')
            if (before and destination and before != destination and int(before) > 0 and int(destination) > 0
                    and fields.get('zone') == destination
                    and all(write.get(key) and write[key] != '-' and fields.get(key) == write[key]
                            for key in ('actor', 'sector', 'code'))
                    and float(fields['elapsed']) > 0 and 0 <= float(fields['ffi_error']) <= 3):
                crossings.append(dict(actor=write['actor'], sector=write['sector'], code=write['code'],
                                      before=before, after=destination, sequence=int(fields['seq']),
                                      requested_xyz=write['target'], actual_xyz=fields['ffi']))
    return crossings


def xyz_text(text):
    return tuple(map(float, text.split(','))) if text and text not in ('none', '-') else None


def own_track(events, writes, ready_own=None, final_own=None):
    """Occupied-ship XYZ at READY, stream start, first and last remote write, and the final readback."""
    stream = next((parse_trace(e)[1].get('pose') for e in events if e.startswith('TRACE OWN ')), None)
    points = {'ready': ready_own, 'stream_start': stream,
              'first_write_before': writes[0].get('own_before') if writes else None,
              'last_write_after': writes[-1].get('own_after') if writes else None,
              'final_readback': final_own}

    def gap(a, b):
        a, b = xyz_text(points[a]), xyz_text(points[b])
        return round(math.dist(a, b), 4) if a and b else None
    return {'xyz': points, 'spawn_drift_m': gap('ready', 'first_write_before'),
            'during_playback_m': gap('first_write_before', 'last_write_after'),
            'after_playback_m': gap('last_write_after', 'final_readback')}


def zone_boundary(traces):
    """First write requesting a zone other than the hull's current one, with the next native readback."""
    for index, (kind, fields) in enumerate(traces):
        if kind == 'WRITE' and fields.get('zone_target') not in (None, fields.get('zone_before')):
            after = next((f for k, f in traces[index + 1:] if k == 'READBACK'), None) or {}
            return {'actor': fields.get('actor'), 'code': fields.get('code'), 'sector': fields.get('sector'),
                    'write_ok': fields['ok'], 't': float(fields['t']), 'zone_before': fields.get('zone_before'),
                    'requested_zone': fields['zone_target'], 'requested_xyz': fields['target'],
                    'own_before': fields.get('own_before'), 'later_seq': after.get('seq'),
                    'later_elapsed': after.get('elapsed'), 'later_actual_zone': after.get('zone'),
                    'later_actual_xyz': after.get('ffi')}
    return None


def evaluate(events, final, sent, bind_sample=0, *, ready_own=None):
    """bind_sample > 0 allows that many earlier samples to be refused before the initial bind."""
    first_bind = next((i for i, e in enumerate(events) if e.startswith('TRACE BIND ')), len(events))
    pre_bind = [e for e in events[:first_bind] if 'refused_over_50m' in e]
    all_events, events = events, events[first_bind:]
    traces = [t for t in map(parse_trace, events) if t is not None]
    binds = [f for k, f in traces if k == 'BIND']
    observations = [f for k, f in traces if k == 'OBS']
    writes = [f for k, f in traces if k == 'WRITE']
    readbacks = [f for k, f in traces if k == 'READBACK']
    playback_events = [e for e in events if e.startswith(('PLAYBACK_EVENT', 'PLAYBACK_FAULT'))]
    steps = [float(f['step']) for f in writes]
    over = [{'t': float(f['t']), 'step_m': float(f['step']), 'ok': f['ok'], 'velocity_authorized': f['recheck']}
            for f in writes if float(f['step']) > STEP_LIMIT]
    times = [float(f['t']) for f in observations]
    intervals = sorted(b - a for a, b in zip(times, times[1:]))
    errors = [float(f['error']) for f in readbacks]
    ffi_gap = [float(f['ffi_vs_actual']) for f in readbacks]
    sequences = sorted({int(f['seq']) for f in observations if f['seq'].isdigit()})
    report = {
        'binds': len(binds), 'bind': binds[0] if binds else None,
        'confirmed_zone_crossings': confirmed_zone_crossings(traces),
        'observations': len(observations), 'writes': len(writes),
        'writes_refused': sum(f['ok'] != 'true' for f in writes),
        'readbacks': len(readbacks), 'samples_sent': len(sent),
        'samples_rendered': len(sequences),
        'steps_m': {'max': max(steps, default=0.0), 'median': statistics.median(steps) if steps else 0.0},
        'steps_over_50m': over,
        'frame_interval_s': {'median': statistics.median(intervals) if intervals else None,
                             'p95': intervals[int(0.95 * (len(intervals) - 1))] if intervals else None,
                             'max': intervals[-1] if intervals else None},
        'readback_error_m': {'max': max(errors, default=0.0)},
        'independent_ffi_vs_readback_m': {'max': max(ffi_gap, default=0.0)},
        'playback_events': playback_events,
        'pre_bind_refusals': pre_bind,
        'own_track': own_track(all_events, writes, ready_own, final.get('own_xyz')),
        'zone_boundary': zone_boundary(traces),
        'zone_targets': dict(collections.Counter(f.get('zone_target') for f in writes)),
        'first_fault': next((e for e in playback_events if e.startswith('PLAYBACK_FAULT')
                             or any(k in e for k in ('refused_over_50m', 'track_stopped', 'closed_uncertain'))), None),
        'final': final,
    }
    faults = [e for e in playback_events if e.startswith('PLAYBACK_FAULT')
              or 'refused_over_50m' in e or 'track_stopped' in e or 'closed_uncertain' in e]
    report['accepted'] = bool(
        len(binds) == 1 and not faults and writes and report['writes_refused'] == 0
        and len(pre_bind) <= bind_sample
        and int(final.get('refusals_50m', -1)) == len(pre_bind)
        and readbacks and report['readback_error_m']['max'] <= READBACK_TOLERANCE
        and report['independent_ffi_vs_readback_m']['max'] <= 0.01
        and float(final.get('own_disp_m', 1)) < 0.01)
    return report


def compose(view, args, recv_cfg, network_dll):
    ext_dir = cockpit.compose_extension(
        view / 'extensions', profile=args.profile, gamestart=args.gamestart, epoch=cockpit.EPOCH,
        udp_port=recv_cfg['port'], udp_lease=recv_cfg['lease'], udp_generation=recv_cfg['generation'])
    (ext_dir / 'native' / 'x4lan_net_64.dll').write_bytes(network_dll)
    with (ext_dir / 'ui' / 'config.lua').open('a', encoding='ascii') as handle:
        handle.write('X4NEON_COMPANY_COCKPIT.velocity_trace = true\n')
        if getattr(args, 'trial_first_contact', False):
            handle.write('X4NEON_COMPANY_COCKPIT.trial_first_contact = true\n')
    shutil.copyfile(ROOT / TRACE_LUA, ext_dir / 'ui' / 'velocity_trace.lua')
    ui = (ext_dir / 'ui.xml').read_text(encoding='utf-8')
    ui = ui.replace('  </environment>', '    <file name="ui/velocity_trace.lua" />\n  </environment>', 1)
    (ext_dir / 'ui.xml').write_text(ui, encoding='utf-8')
    return ext_dir


def run_trial(view, profile_dir, out, args, samples, network_dll):
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.company_motion_source import CompanyMotionSource
    from lockstep.launcher import steam

    log_path = profile_dir / LOGFILE
    if log_path.exists():
        log_path.unlink()
    recv_source = CompanyMotionSource(playback_index=None if args.trial_first_contact else 2,
                                      cockpit_radar_playback=True, cockpit_max_pilots=1)
    recv_cfg = recv_source.configure(cockpit.EPOCH, 1, operational_bits='111')
    service = CompanyMotionService()
    client_recv = client_remote = process = job_handle = bridge = None
    try:
        ext_dir = compose(view, args, recv_cfg, network_dll)
        command = [str(view / 'X4.exe'), '-personalfolderid', str(args.profile), '-skipintro',
                   '-noworkshopsync', '-nocputhrottle', '-debug', 'scripts', '-logfile', LOGFILE]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= cockpit.STARTF_USESHOWWINDOW
        si.wShowWindow = cockpit.SW_SHOWNOACTIVATE
        process = subprocess.Popen(command, cwd=str(view), env=steam.client_environment(), startupinfo=si)
        job_handle = cockpit.attach_kill_on_close_job(process.pid)
        validator = cockpit.make_windows_pid_udp_validator(process)
        bridge = cockpit.X4CockpitBridge(process, log_path, ext_dir, timeout=60.0)
        ready = bridge.wait_ready(timeout=args.timeout)
        sector = ready['remote_sector']
        r0 = {axis: float(ready[f'remote_{axis}']) for axis in 'xyz'}

        grant_a = service.admit(1, 'a1' * 16, 1, sector, cockpit.SESSION, cockpit.EPOCH)
        grant_b = service.admit(2, 'b1' * 16, 2, sector, cockpit.SESSION, cockpit.EPOCH)
        service.set_running(True)
        observer = recv_source.observe(validator)
        route = (dict(cockpit_playback_limit=1, on_pilot_roster=observer.set_pilot_roster)
                 if args.trial_first_contact else dict(trial_index=2))
        client_recv = CompanyMotionClient(
            service.port, grant_a, recv_source, validator, on_snapshot=observer.snapshot, on_end=observer.end,
            on_transport_lost=observer.transport_lost, on_view_reset=observer.reset_world, **route)
        observer.bind_views(client_recv.set_views)
        pilot = cockpit._QueuedPilotSource(cockpit.EPOCH, 2, '1' * 32)
        client_remote = CompanyMotionClient(service.port, grant_b, pilot, lambda _: True)

        def pump():
            service.poll()
            client_recv.poll()
            client_remote.poll()

        def pump_until(predicate, label, timeout=15.0):
            deadline = time.monotonic() + timeout
            while not predicate():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'timed out waiting for {label}')
                pump()
                time.sleep(0.005)

        def viewing():
            return 2 in (client_recv._sent_view or {}).get(sector, ())

        pump_until(lambda: client_recv._running and client_remote._running, 'clients_running')
        started = bridge.request('start_stream', pump=pump, clock_base=time.perf_counter())
        assert started.get('status') == 'stream_started', started
        own = next((parse_trace(e)[1] for e in bridge.events if e.startswith('TRACE OWN ')), None)
        own_pose = dict(zip('xyz', map(float, own['pose'].split(',')))) if own else None
        placed, placement = place(samples, r0, own_pose, args.bind_sample, args.flip)
        placement['own_radar_range_m'] = float(own['radar_range']) if own else None
        placement['bind_sample'] = args.bind_sample

        # Neon's proven acquisition path: HUD lock, then untargeted radar, before the first sample.
        locked = bridge.request('lock_hud', pump=pump)
        assert locked.get('locked') == 'true', locked
        pump_until(viewing, 'hud_view')
        unlocked = bridge.request('unlock_hud', pump=pump)
        assert unlocked.get('has_target') == 'false', unlocked
        pump_until(viewing, 'radar_view')

        if args.trial_first_contact:
            def pilot_has_radar():
                current = bridge.request('probe_pilot', pump=pump)
                return current['active'] == 'true' and float(current['range']) > 0
            pump_until(pilot_has_radar, 'admitted pilot on local radar')

        rows = schedule(placed, cockpit.EPOCH, pilot.local_generation, sector)
        sent = []
        begin = time.perf_counter()
        for offset, row in rows:
            while time.perf_counter() - begin < offset:
                pump()
                time.sleep(0.001)
            pilot.items.append(row)
            sent.append({'sequence': row['sequence'], 'sent_at': time.perf_counter() - begin})
            pump()
        settle = time.perf_counter()
        while time.perf_counter() - settle < 0.4:
            pump()
            time.sleep(0.005)

        state = bridge.request('probe_state', pump=pump)
        final = bridge.request('readback', pump=pump, min_game_delay=0.2)
        assert final.get('status') == 'readback', final
        last = placed[-1]
        final['distance_to_last_sample_m'] = round(math.dist(
            [float(final[a]) for a in 'xyz'], [last[a] for a in 'xyz']), 3)
        done = bridge.request('done', pump=pump)
        assert done.get('status') == 'done', done
        report = evaluate(list(bridge.events), final, sent, args.bind_sample, ready_own=ready.get('own_xyz'))
        if args.trial_first_contact:
            contacts = [parsed[1] for event in bridge.events
                        if (parsed := parse_trace(event)) and parsed[0] == 'FIRST_CONTACT']
            report['first_contacts'] = contacts
            reached_last = any(parsed[0] == 'READBACK' and int(parsed[1]['seq']) == sent[-1]['sequence']
                               for event in bridge.events if (parsed := parse_trace(event)))
            report['rendered_final_sequence'] = reached_last
            report['accepted'] = bool(report['accepted'] and len(contacts) == 1 and reached_last
                                      and contacts[0]['code'] == ready['remote_code']
                                      and contacts[0]['actor'] == ready['remote_hex']
                                      and int(contacts[0]['seq']) == sent[1]['sequence']
                                      and float(contacts[0]['elapsed']) > 0
                                      and max(float(contacts[0][key]) for key in ('error', 'ffi_error')) <= 3)
        report.update({
            'evidence_source': args.evidence,
            'placement': placement, 'probe_state': state,
            'ships': {'own_code': ready['own_code'], 'remote_code': ready['remote_code'],
                      'remote_hex': ready['remote_hex'], 'sector': sector, 'remote_start': r0},
            'udp_peer_validation': dict(validator.info),
            'observer_stats': recv_source.observer_stats(),
        })
        return report
    finally:
        if bridge is not None:
            bridge.stop()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if job_handle is not None:
            ctypes.windll.kernel32.CloseHandle(job_handle)
        for client in (client_recv, client_remote):
            if client is not None:
                client.close()
        service.close()
        recv_source.close()
        if log_path.is_file():
            shutil.copyfile(log_path, out / LOGFILE)


def main(argv=None):
    from lockstep.launcher import game as game_mod, profile as profile_mod, steam

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--trajectory', help='recorded V3 JSON or the repository gate-flight TSV')
    parser.add_argument('--synthetic', action='store_true', help='harness shake-out with a generated path')
    parser.add_argument('--network-dll', required=True, help='x4lan_net_64.dll exporting monotonic()')
    parser.add_argument('--game')
    parser.add_argument('--profile', type=int, default=45856)
    parser.add_argument('--gamestart', default='custom_creative')
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--timeout', type=float, default=240.0)
    parser.add_argument('--trial-first-contact', action='store_true',
                        help='use the live pilot roster and verify one bounded alignment from sample zero')
    parser.add_argument('--flip', type=lambda text: tuple(int(v) for v in text.split(',')),
                        help='fixed per-axis mirror, e.g. 1,1,-1, to repeat an earlier path')
    parser.add_argument('--bind-sample', type=int,
                        help='sample placed on the resting hull; 0 is the velocity-less V3 baseline')
    parser.add_argument('--preflight', action='store_true', help='check inputs and placement, no launch')
    args = parser.parse_args(argv)

    if bool(args.trajectory) == args.synthetic:
        raise SystemExit('pass exactly one of --trajectory or --synthetic')
    samples = synthetic_trajectory() if args.synthetic else load_trajectory(args.trajectory)
    source_error = {
        'input_trajectory': source_position_error(samples),
        # The client clears the first rate before sending a new grant's baseline.
        'with_position_only_baseline': source_position_error(samples, position_only_baseline=True),
    }
    if args.bind_sample is None:
        args.bind_sample = 0 if args.trial_first_contact else 1
    if args.trial_first_contact and (args.bind_sample != 0 or len(samples) < 2):
        parser.error('--trial-first-contact requires sample zero and at least two recorded positions')
    if not 0 <= args.bind_sample < len(samples):
        raise SystemExit('--bind-sample is outside the trajectory')
    args.evidence = ('synthetic_shakeout_path' if args.synthetic else
                     'recorded_trajectory_driving_real_native_windows_x4')
    network_dll = Path(args.network_dll).read_bytes()
    if b'monotonic' not in network_dll:
        raise SystemExit('network DLL lacks monotonic(); use a recorded-build module')
    if args.profile not in profile_mod.PROFILES or args.profile == 45851:
        raise SystemExit(f'profile {args.profile} is not a free disposable lane')
    if args.preflight:
        span = samples[-1]['capture_time'] - samples[0]['capture_time']
        print(json.dumps({'samples': len(samples), 'span_s': round(span, 3),
                          'path_m': round(sum(math.dist([a[k] for k in 'xyz'], [b[k] for k in 'xyz'])
                                              for a, b in zip(samples, samples[1:])), 1),
                          'with_velocity': sum(s['real_velocity'] is not None for s in samples),
                          'source_trajectory': 'synthetic' if args.synthetic else 'recorded',
                          'source_position_error': source_error,
                          'x4_running': cockpit.x4_running() if os.name == 'nt' else None}, indent=1))
        return 0
    if os.name != 'nt':
        parser.error('a native receiver run requires Windows')
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f'{out} already exists; use a fresh output directory')
    if cockpit.x4_running():
        raise SystemExit('X4.exe is running; deferring to avoid disturbing the user session')
    ok, msg = steam.status()
    if not ok:
        raise SystemExit(msg)
    game = Path(args.game) if args.game else game_mod.find_install()
    game_mod.verify_exe(game)
    view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'cobalt-velocity-{out.name}'
    if view.exists():
        raise SystemExit(f'{view} already exists')

    documents = profile_mod.documents()

    def protected():
        return {'other_profiles': {e.name: cockpit.tree_metadata(e) for e in sorted(documents.iterdir())
                                   if e.is_dir() and e.name != str(args.profile)},
                'game_extensions': sorted(e.name for e in (game / 'extensions').iterdir())}

    before = protected()
    out.mkdir(parents=True)
    result = {'options': {k: v for k, v in vars(args).items() if k != 'trajectory'},
              'trajectory_private': bool(args.trajectory), 'source_position_error': source_error,
              'game': str(game), 'view': str(view),
              'outcome': 'started'}
    ctypes.windll.user32.SetProcessDPIAware()
    try:
        game_mod.create_view(game, view)
        profile_dir = profile_mod.prepare(args.profile, [*game_mod.dlc_ids(view), cockpit.EXTENSION_ID],
                                          width=args.width, height=args.height, fast=True)
        result['velocity_receiver'] = run_trial(view, profile_dir, out, args, samples, network_dll)
        result['outcome'] = 'done'
    except BaseException as exc:
        result['outcome'] = 'error'
        result['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if view.exists():
            cockpit.remove_view_patiently(game_mod, view)
        result['protected_unchanged'] = protected() == before
        result['view_removed'] = not view.exists()
        result['x4_running_after'] = cockpit.x4_running()
        (out / 'result.json').write_text(json.dumps(result, indent=1, sort_keys=True))
    receiver = result.get('velocity_receiver', {})
    print(json.dumps({'outcome': result['outcome'], 'accepted': receiver.get('accepted'),
                      'source_position_error': source_error,
                      'binds': receiver.get('binds'), 'writes': receiver.get('writes'),
                      'pre_bind_refusals': len(receiver.get('pre_bind_refusals', [])),
                      'steps_over_50m': len(receiver.get('steps_over_50m', [])),
                      'max_step_m': receiver.get('steps_m', {}).get('max'),
                      'protected_unchanged': result['protected_unchanged'], 'view_removed': result['view_removed'],
                      'x4_running_after': result['x4_running_after'], 'out': str(out)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
