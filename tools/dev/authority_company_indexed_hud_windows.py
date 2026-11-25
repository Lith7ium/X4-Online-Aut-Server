#!/usr/bin/env python3
"""Bounded native Windows GPU verification of the d0756d51 indexed cockpit observer.

Reuses the existing disposable flight fixture (authority_company_cockpit_multipilot_probe.xml)
with the unmodified d0756d51 production observer, playback, AUTH_COMPANY_COCKPIT_READBACK
(with view serial), and AUTH_COMPANY_COCKPIT_WITHDRAWAL modules:
  - Locks a third same-sector Company ship (index=3) on the stock X4 HUD.
  - Runs the indexed cockpit observer (target_index=2, radar_fallback=true) while the
    reserved remote Company ship (index=2) moves within radar range from a recorded/loopback
    remote trajectory over real PID-validated loopback UDP.
  - Records the selected HUD ship, the remote ship's first and last actual poses and view
    serial, the local (index=1) and third (index=3) ships' displacement, and any
    AUTH_COMPANY_COCKPIT_WITHDRAWAL reason.

Safety:
  - Enforces the shared Windows native-X4 lockfile and refuses if X4.exe is already running.
  - Disposable profile 45850 and isolated x4ls view only; asserts protected_unchanged,
    view_removed, x4_running_after == False, and project_error_count == 0.
  - 800x600 muted fast window started with SW_SHOWNOACTIVATE and kept at HWND_BOTTOM.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tools'), str(ROOT / 'tools/dev')]

from authority_company_cockpit_windows import (
    EPOCH, NONCE, SESSION, SW_SHOWNOACTIVATE, STARTF_USESHOWWINDOW,
    _dist3, _QueuedPilotSource, attach_kill_on_close_job, find_window,
    make_windows_pid_udp_validator, remove_view_patiently, tree_metadata,
    tuck_window_bottom_corner, x4_running,
)

EXTENSION_ID = 'x4_neon_indexed_hud'
LOGFILE = 'neon-indexed-hud.log'
PROFILE_DEFAULT = 45850
SHARED_LOCKFILE = ROOT.parent / '.x4-native-test.lock'


def _pid_alive(pid):
    try:
        out = subprocess.run(
            ['tasklist', '/FI', f'PID eq {int(pid)}', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, check=True,
        ).stdout
        return f'"{int(pid)}"' in out
    except Exception:
        return False


def acquire_shared_lock(profile):
    deadline = time.monotonic() + 300.0
    while True:
        try:
            with open(SHARED_LOCKFILE, 'x', encoding='utf-8') as handle:
                json.dump({
                    'agent': 'Neon',
                    'pid': os.getpid(),
                    'profile': int(profile),
                    'started_at': datetime.now(timezone.utc).isoformat(),
                }, handle)
            return
        except FileExistsError:
            try:
                info = json.loads(SHARED_LOCKFILE.read_text(encoding='utf-8'))
            except Exception:
                info = {}
            owner_pid = info.get('pid')
            owner_alive = isinstance(owner_pid, int) and _pid_alive(owner_pid)
            if not owner_alive and not x4_running():
                try:
                    SHARED_LOCKFILE.unlink()
                    continue
                except OSError:
                    pass
            if info.get('agent') == 'Quartz' and owner_alive and time.monotonic() < deadline:
                time.sleep(15.0)
                continue
            raise SystemExit(
                f'REFUSED: shared lockfile {SHARED_LOCKFILE} held by {info!r}; '
                'will not disturb concurrent native X4 session.'
            )


def release_shared_lock():
    try:
        if SHARED_LOCKFILE.is_file():
            info = json.loads(SHARED_LOCKFILE.read_text(encoding='utf-8'))
            if info.get('pid') == os.getpid():
                SHARED_LOCKFILE.unlink()
    except Exception:
        pass


def compose_extension(extensions_dir, *, profile, gamestart, epoch, udp_port, udp_lease, udp_generation):
    ext = Path(extensions_dir) / EXTENSION_ID
    if ext.exists():
        shutil.rmtree(ext)
    for sub in ('ui', 'md', 'native'):
        (ext / sub).mkdir(parents=True)

    (ext / 'content.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<content id="{EXTENSION_ID}" name="X4 Neon Indexed HUD Cockpit Trial" '
        'description="Disposable native Windows d0756d51 indexed cockpit observer trial" '
        'author="Neon" version="100" date="2026-11-25" save="false">\n'
        '  <dependency version="900" />\n'
        '</content>\n',
        encoding='utf-8',
    )

    # Full production module set for d0756d51 indexed cockpit observer + playback + map observer
    ui_sources = [
        ('authority_registry_resolve_string.lua', 'tools/dev/authority_registry_resolve_string.lua'),
        ('motion_buffer.lua', 'src/authority/lua/motion_buffer.lua'),
        ('motion_playback.lua', 'src/authority/lua/motion_playback.lua'),
        ('map_render_observation.lua', 'src/authority/lua/map_render_observation.lua'),
        ('company_observer_target.lua', 'src/authority/lua/company_observer_target.lua'),
        ('company_observer_map.lua', 'src/authority/lua/company_observer_map.lua'),
        ('company_hull_playback.lua', 'src/authority/lua/company_hull_playback.lua'),
        ('company_hull_playback_x4.lua', 'src/authority/lua/company_hull_playback_x4.lua'),
        ('company_cockpit_observer.lua', 'src/authority/lua/company_cockpit_observer.lua'),
        ('company_cockpit_playback.lua', 'src/authority/lua/company_cockpit_playback.lua'),
        ('company_observer_stream.lua', 'src/authority/lua/company_observer_stream.lua'),
        ('authority_company_indexed_hud_x4.lua', 'tools/dev/authority_company_indexed_hud_x4.lua'),
    ]
    md_sources = [
        ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
        ('company_cockpit_observer.xml', 'src/authority/md/company_cockpit_observer.xml'),
        ('company_observer_target.xml', 'src/authority/md/company_observer_target.xml'),
        ('authority_company_cockpit_multipilot_probe.xml', 'tools/dev/authority_company_cockpit_multipilot_probe.xml'),
    ]

    for dst_name, rel_src in ui_sources:
        src = ROOT / rel_src
        if dst_name == 'motion_buffer.lua':
            text = src.read_text(encoding='utf-8').replace('\r\n', '\n')
            idx = text.rfind('return M')
            assert idx >= 0
            text = text[:idx] + 'X4LAN_MOTION_BUFFER = M\n' + text[idx:]
            (ext / 'ui' / dst_name).write_text(text, encoding='utf-8')
        else:
            shutil.copyfile(src, ext / 'ui' / dst_name)

    for dst_name, rel_src in md_sources:
        shutil.copyfile(ROOT / rel_src, ext / 'md' / dst_name)

    shutil.copyfile(ROOT / 'src/lockstep/base/native/x4lan_net_64.dll', ext / 'native' / 'x4lan_net_64.dll')

    (ext / 'ui/config.lua').write_text(
        'X4NEON_COMPANY_INDEXED_HUD = {\n'
        '  enabled = true,\n'
        f'  extension_id = {json.dumps(EXTENSION_ID)},\n'
        f'  profile = {json.dumps(str(profile))},\n'
        f'  gamestart = {json.dumps(gamestart)},\n'
        f'  epoch = {json.dumps(epoch)},\n'
        f'  nonce = {json.dumps(NONCE)},\n'
        f'  udp_port = {int(udp_port)},\n'
        f'  udp_lease = {json.dumps(udp_lease)},\n'
        f'  udp_generation = {json.dumps(udp_generation)},\n'
        '}\n',
        encoding='ascii',
    )
    ui_entries = ''.join(
        ['    <file name="ui/config.lua" />\n']
        + [f'    <file name="ui/{dst}" />\n' for dst, _ in ui_sources]
    )
    (ext / 'ui.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<addon name="{EXTENSION_ID}">\n'
        '  <environment type="menus">\n'
        '    <dependency name="ego_detailmonitor" />\n'
        f'{ui_entries}'
        '  </environment>\n'
        '</addon>\n',
        encoding='utf-8',
    )
    return ext


class X4IndexedHudBridge:
    _TAG = '[X4LAN] AUTH_INDEXED_HUD '
    _KV = re.compile(r'([A-Za-z0-9_]+)=([^ ]+)')

    def __init__(self, process, log_path, ext_dir, *, timeout=90.0):
        self.process = process
        self.log_path = log_path
        self.ext_dir = ext_dir
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self.ready = None
        self.error = None
        self.done = False
        self.responses = {}
        self._next_seq = 1
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    @staticmethod
    def _parse_kv(text):
        return {m.group(1): m.group(2) for m in X4IndexedHudBridge._KV.finditer(text)}

    def _watch(self):
        pos = 0
        hwnd = None
        last_tuck = 0.0
        while not self._stop.is_set():
            now_t = time.monotonic()
            if now_t - last_tuck >= 0.4:
                last_tuck = now_t
                if hwnd is None:
                    hwnd = find_window(self.process.pid)
                if hwnd is not None:
                    tuck_window_bottom_corner(hwnd)
            if self.log_path.exists():
                try:
                    data = self.log_path.read_bytes()
                except OSError:
                    data = b''
                if len(data) > pos:
                    chunk = data[pos:].decode('utf-8', errors='replace')
                    pos = len(data)
                    for raw_line in chunk.splitlines():
                        idx = raw_line.find(self._TAG)
                        if idx < 0:
                            continue
                        body = raw_line[idx + len(self._TAG):].strip()
                        with self._cond:
                            if body.startswith('READY '):
                                self.ready = self._parse_kv(body[6:])
                                self._cond.notify_all()
                            elif body.startswith('RESP '):
                                kv = self._parse_kv(body[5:])
                                if 'seq' in kv:
                                    self.responses[int(kv['seq'])] = kv
                                    self._cond.notify_all()
                            elif body.startswith('ERROR '):
                                self.error = body[6:]
                                self._cond.notify_all()
                            elif body == 'DONE':
                                self.done = True
                                self._cond.notify_all()
            if self.process.poll() is not None and not self.done:
                with self._cond:
                    if self.error is None:
                        self.error = f'X4 exited early with code {self.process.returncode}'
                    self._cond.notify_all()
                return
            time.sleep(0.05)

    def wait_ready(self, timeout=180.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.ready is None and self.error is None:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError('timed out waiting for X4 READY')
                self._cond.wait(min(0.25, rem))
            if self.error is not None:
                raise RuntimeError(f'X4 error before READY: {self.error}')
            return dict(self.ready)

    @staticmethod
    def _lua_val(v):
        if isinstance(v, bool):
            return 'true' if v else 'false'
        if isinstance(v, (int, float)):
            return repr(v)
        return json.dumps(str(v))

    def request(self, op, *, pump=None, timeout=None, **params):
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
        fields = [f'seq = {seq}', f'op = {json.dumps(op)}', f'request_id = {json.dumps(f"req-{seq}")}']
        for k, v in params.items():
            if v is not None:
                fields.append(f'{k} = {self._lua_val(v)}')
        partial = self.ext_dir / f'req_{seq}.partial'
        partial.write_text('return { ' + ', '.join(fields) + ' }\n', encoding='ascii')
        os.replace(partial, self.ext_dir / f'req_{seq}.lua')
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            if pump is not None:
                pump()
            with self._cond:
                if seq in self.responses:
                    return dict(self.responses[seq])
                if self.error is not None:
                    raise RuntimeError(f'X4 error during seq={seq} op={op}: {self.error}')
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError(f'timed out waiting for seq={seq} op={op}')
                self._cond.wait(min(0.02 if pump else 0.2, rem))

    def stop(self):
        self._stop.set()
        self._thread.join(3)


def parse_production_log(log_text):
    readbacks = []
    withdrawals = []
    events = []
    stops = []
    renderer_lines = []
    kv_re = re.compile(r'([A-Za-z0-9_]+)=([^ ]+)')
    for line in log_text.splitlines():
        if '[X4LAN] AUTH_COMPANY_COCKPIT_READBACK ' in line:
            body = line.split('[X4LAN] AUTH_COMPANY_COCKPIT_READBACK ', 1)[1].strip()
            kv = {m.group(1): m.group(2) for m in kv_re.finditer(body)}
            readbacks.append({'raw': line, 'fields': kv})
        elif '[X4LAN] AUTH_COMPANY_COCKPIT_WITHDRAWAL ' in line:
            reason = line.split('[X4LAN] AUTH_COMPANY_COCKPIT_WITHDRAWAL ', 1)[1].strip()
            withdrawals.append({'raw': line, 'reason': reason})
        elif '[X4LAN] AUTH_COMPANY_COCKPIT_EVENT ' in line:
            events.append(line)
        elif '[X4LAN] AUTH_COMPANY_COCKPIT_STOP ' in line:
            stops.append(line)
        elif 'Vulkan' in line or 'GPU' in line or 'NVIDIA' in line or 'AMD Radeon' in line:
            if len(renderer_lines) < 12:
                renderer_lines.append(line.strip())
    return {
        'readbacks': readbacks,
        'withdrawals': withdrawals,
        'events': events,
        'stops': stops,
        'renderer_lines': renderer_lines,
    }


def run_indexed_hud_trial(view, profile_dir, out, args):
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_reconnect import ReconnectingCompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.company_motion_source import CompanyMotionSource
    from lockstep.launcher import steam

    log_path = profile_dir / LOGFILE
    if log_path.exists():
        log_path.unlink()

    # Production indexed CompanyMotionSource: playback_index=2, cockpit_radar_playback=True -> 1 indexed cockpit slot!
    recv_source = CompanyMotionSource(playback_index=2, cockpit_radar_playback=True)
    assert recv_source.cockpit_max_pilots == 1 and recv_source.playback_index == 2
    recv_cfg = recv_source.configure(EPOCH, 1, operational_bits='11111')
    service = CompanyMotionService()
    client_recv = client_remote = None
    process = job_handle = bridge = None
    try:
        ext_dir = compose_extension(
            view / 'extensions',
            profile=args.profile,
            gamestart=args.gamestart,
            epoch=EPOCH,
            udp_port=recv_cfg['port'],
            udp_lease=recv_cfg['lease'],
            udp_generation=recv_cfg['generation'],
        )
        command = [
            str(view / 'X4.exe'),
            '-personalfolderid', str(args.profile),
            '-skipintro', '-noworkshopsync', '-nocputhrottle',
            '-debug', 'scripts', '-logfile', LOGFILE,
        ]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWNOACTIVATE
        process = subprocess.Popen(command, cwd=str(view), env=steam.client_environment(), startupinfo=si)
        job_handle = attach_kill_on_close_job(process.pid)
        validator = make_windows_pid_udp_validator(process)

        bridge = X4IndexedHudBridge(process, log_path, ext_dir, timeout=60.0)
        ready = bridge.wait_ready(timeout=args.timeout)
        home_sector = ready['home_sector']
        own_p0 = {a: float(ready[f'own_{a}']) for a in ('x', 'y', 'z')}
        p2_0 = {a: float(ready[f'p2_{a}']) for a in ('x', 'y', 'z')}
        p3_0 = {a: float(ready[f'p3_{a}']) for a in ('x', 'y', 'z')}

        grant_a = service.admit(1, 'a1' * 16, 1, home_sector, SESSION, EPOCH)
        grant_b = service.admit(2, 'b1' * 16, 2, home_sector, SESSION, EPOCH)
        service.set_running(True)

        client_recv = ReconnectingCompanyMotionClient.observing(
            service.port, grant_a, recv_source, validator,
        )
        src_b = _QueuedPilotSource(EPOCH, 2, '1' * 32)
        client_remote = CompanyMotionClient(service.port, grant_b, src_b, lambda _: True)

        def pump_all():
            service.poll()
            client_recv.poll()
            client_remote.poll()

        def pump_until(pred, timeout=15.0, label='condition'):
            deadline = time.monotonic() + timeout
            while not pred():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'timed out waiting for {label}')
                pump_all()
                time.sleep(0.005)

        def pump_for(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                pump_all()
                time.sleep(0.005)

        pump_until(
            lambda: client_recv._client is not None and client_recv._client._running and client_remote._running,
            label='clients_running',
        )

        # 1. Lock the THIRD Company ship (index=3) in the stock X4 HUD BEFORE starting the observer!
        r_lock = bridge.request('lock_hud', pump=pump_all, index=3)
        assert r_lock.get('status') == 'hud_locked' and r_lock.get('locked') == 'true', f'HUD lock failed: {r_lock}'

        # 2. Start the exact d0756d51 indexed cockpit observer (target_index=2) + playback + stream.
        r_start = bridge.request('start_stream', pump=pump_all, clock_base=time.perf_counter())
        assert r_start.get('status') == 'stream_started'
        assert int(r_start['hud_index']) == 3, f'HUD target lost on stream start: {r_start}'

        # 3. Wait until the indexed observer subscribes reserved remote ship index=2 on radar while index=3 stays on HUD.
        pump_until(
            lambda: 2 in (client_recv._client._sent_view or {}).get(home_sector, ()),
            label='indexed_cockpit_radar_view_subscribed',
        )
        probe_subscribed = bridge.request('probe_state', pump=pump_all)
        assert int(probe_subscribed['hud_index']) == 3, f'HUD target changed: {probe_subscribed}'
        assert int(probe_subscribed['obs_index']) == 2, f'Observer bound wrong ship: {probe_subscribed}'
        assert probe_subscribed['obs_mode'] == 'radar', f'Expected radar mode while 3rd ship targeted: {probe_subscribed}'

        # 4. Stream recorded/loopback remote pilot trajectory for index=2 (30 samples @ 10 Hz, 22.0 m/s, within radar range).
        vx, vy, vz = 18.0, 4.0, 12.0
        dt = 0.10
        sent_samples = []
        probe_mid = None
        for seq in range(1, 31):
            t_s = seq * dt
            px = p2_0['x'] + vx * t_s
            py = p2_0['y'] + vy * t_s
            pz = p2_0['z'] + vz * t_s
            src_b.items.append({
                'version': 3, 'epoch': EPOCH, 'generation': src_b.local_generation,
                'index': 2, 'sequence': seq, 'sector_macro': home_sector,
                'source_time': 100.0 + t_s, 'capture_time': 500.0 + t_s,
                'x': px, 'y': py, 'z': pz,
                'real_velocity': {'x': vx, 'y': vy, 'z': vz} if seq > 1 else None,
            })
            sent_samples.append({'sequence': seq, 'x': px, 'y': py, 'z': pz})
            pump_for(dt)
            if seq == 15:
                probe_mid = bridge.request('probe_state', pump=pump_all)
        # Allow >0.5 s after final sample so make_x4_record emits a late AUTH_COMPANY_COCKPIT_READBACK
        # while staying well within the 5.0 s loss window.
        pump_for(0.65)

        probe_post = bridge.request('probe_state', pump=pump_all)
        rb_p2 = bridge.request('readback', pump=pump_all, index=2, min_game_delay=0.15)
        rb_p3 = bridge.request('readback', pump=pump_all, index=3, min_game_delay=0.15)
        obs_final = recv_source.observer_stats()
        r_done = bridge.request('done', pump=pump_all)
        assert r_done.get('status') == 'done'

        own_final = {a: float(probe_post[f'own_{a}']) for a in ('x', 'y', 'z')}
        p2_final = {a: float(rb_p2[a]) for a in ('x', 'y', 'z')}
        p3_final = {a: float(rb_p3[a]) for a in ('x', 'y', 'z')}

        return {
            'outcome': 'done',
            'evidence_source': {
                'remote_pilot_index_2': 'recorded_loopback_trajectory_driving_native_x4_production_d0756d51',
                'receiving_cockpit': 'real_native_windows_x4_900_gpu',
            },
            'clock_source': r_start['clock_source'],
            'udp_peer_validation': dict(validator.info),
            'ships': {
                'local_index_1': {'code': ready['own_code'], 'hex': ready['own_hex'], 'sector': home_sector,
                                  'initial_pose': own_p0, 'final_pose': own_final,
                                  'displacement_m': round(_dist3(own_p0, own_final), 6)},
                'remote_index_2': {'code': ready['p2_code'], 'hex': ready['p2_hex'], 'sector': home_sector,
                                   'initial_pose': p2_0, 'final_pose': p2_final,
                                   'displacement_m': round(_dist3(p2_0, p2_final), 6)},
                'hud_third_index_3': {'code': ready['p3_code'], 'hex': ready['p3_hex'], 'sector': home_sector,
                                      'initial_pose': p3_0, 'final_pose': p3_final,
                                      'displacement_m': round(_dist3(p3_0, p3_final), 6)},
            },
            'hud_lock': r_lock,
            'probes': {
                'on_subscribe': probe_subscribed,
                'mid_stream': probe_mid,
                'post_stream': probe_post,
            },
            'independent_md_readbacks': {'index_2': rb_p2, 'index_3': rb_p3},
            'observer_stats_final': obs_final,
            'sent_sample_count': len(sent_samples),
            'first_sent_sample': sent_samples[0],
            'last_sent_sample': sent_samples[-1],
        }
    finally:
        for cli in (client_recv, client_remote):
            if cli is not None:
                try:
                    cli.close()
                except Exception:
                    pass
        try:
            recv_source.close()
        except Exception:
            pass
        try:
            service.close()
        except Exception:
            pass
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
            try:
                ctypes.windll.kernel32.CloseHandle(job_handle)
            except BaseException:
                pass
        if log_path.is_file():
            shutil.copyfile(log_path, out / LOGFILE)


def main(argv=None):
    from lockstep.launcher import game as game_mod, profile as profile_mod, steam

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--game')
    parser.add_argument('--profile', type=int, default=PROFILE_DEFAULT)
    parser.add_argument('--gamestart', default='custom_creative')
    parser.add_argument('--width', type=int, default=800)
    parser.add_argument('--height', type=int, default=600)
    parser.add_argument('--timeout', type=float, default=240.0)
    args = parser.parse_args(argv)

    if args.profile not in profile_mod.PROFILES:
        raise SystemExit(f'Profile {args.profile} is outside disposable launcher lanes')
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f'{out} already exists; use a fresh output directory')

    acquire_shared_lock(args.profile)
    try:
        if x4_running():
            raise SystemExit('REFUSED: X4.exe is already running; never disturb a running X4 instance.')
        ok, msg = steam.status()
        if not ok:
            raise SystemExit(msg)
        game = Path(args.game) if args.game else game_mod.find_install()
        game_mod.verify_exe(game)
        exe_sha256 = hashlib.sha256((game / 'X4.exe').read_bytes()).hexdigest()
        view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-indexed-hud-{out.name}'
        if view.exists():
            raise SystemExit(f'{view} already exists')

        documents = profile_mod.documents()
        protected_before = {
            'other_profiles': {
                entry.name: tree_metadata(entry)
                for entry in sorted(documents.iterdir())
                if entry.is_dir() and entry.name != str(args.profile)
            },
            'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
        }
        out.mkdir(parents=True)
        result = {
            'options': vars(args), 'game': str(game), 'x4_exe_sha256': exe_sha256,
            'view': str(view), 'outcome': 'started',
        }
        ctypes.windll.user32.SetProcessDPIAware()
        try:
            game_mod.create_view(game, view)
            prof_dir = profile_mod.prepare(
                args.profile,
                [*game_mod.dlc_ids(view), EXTENSION_ID],
                width=args.width, height=args.height, fast=True,
            )
            trial = run_indexed_hud_trial(view, prof_dir, out, args)
            result['native_indexed_hud_trial'] = trial
            result['outcome'] = 'done'
        except BaseException as exc:
            result['outcome'] = 'error'
            result['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            if view.exists():
                remove_view_patiently(game_mod, view)
            protected_after = {
                'other_profiles': {
                    entry.name: tree_metadata(entry)
                    for entry in sorted(documents.iterdir())
                    if entry.is_dir() and entry.name != str(args.profile)
                },
                'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
            }
            protected_unchanged = (protected_after == protected_before)
            view_removed = not view.exists()
            x4_running_after = x4_running()
            log_file = out / LOGFILE
            project_errors = []
            parsed_log = {'readbacks': [], 'withdrawals': [], 'events': [], 'stops': [], 'renderer_lines': []}
            if log_file.is_file():
                log_text = log_file.read_text(encoding='utf-8', errors='replace')
                parsed_log = parse_production_log(log_text)
                for line in log_text.splitlines():
                    if 'Could not find signature file' in line:
                        continue
                    if '[X4LAN] AUTH_INDEXED_HUD ' in line:
                        if ' ERROR ' in line:
                            project_errors.append(line)
                        continue
                    if '[X4LAN] AUTH_COMPANY_COCKPIT_STOP ' in line:
                        project_errors.append(line)
                        continue
                    if any(tag in line for tag in (
                        '[X4LAN] AUTH_COMPANY_COCKPIT_READBACK ',
                        '[X4LAN] AUTH_COMPANY_COCKPIT_EVENT ',
                        '[X4LAN] AUTH_COMPANY_COCKPIT_WITHDRAWAL ',
                    )):
                        continue
                    if ('[=ERROR=]' in line or 'Error in MD cue' in line) and (
                        EXTENSION_ID in line or 'X4Authority' in line or 'X4LAN' in line
                    ):
                        project_errors.append(line)
            readbacks = parsed_log['readbacks']
            withdrawals = parsed_log['withdrawals']
            result.update({
                'protected_unchanged': protected_unchanged,
                'view_removed': view_removed,
                'x4_running_after': x4_running_after,
                'project_error_count': len(project_errors),
                'project_errors': project_errors,
                'production_log_summary': {
                    'readback_line_count': len(readbacks),
                    'first_readback': readbacks[0]['fields'] if readbacks else None,
                    'last_readback': readbacks[-1]['fields'] if readbacks else None,
                    'all_readbacks': [r['fields'] for r in readbacks],
                    'withdrawal_count': len(withdrawals),
                    'first_withdrawal_reason': withdrawals[0]['reason'] if withdrawals else None,
                    'withdrawals': withdrawals,
                    'cockpit_events': parsed_log['events'],
                    'cockpit_stops': parsed_log['stops'],
                    'renderer_lines': parsed_log['renderer_lines'],
                },
            })
            (out / 'result.json').write_text(json.dumps(result, indent=1, sort_keys=True))

        assert result['protected_unchanged'], 'protected profile/config changed!'
        assert result['view_removed'], 'isolated x4 view was not removed!'
        assert not result['x4_running_after'], 'X4.exe still running after test!'
        assert result['project_error_count'] == 0, f'MD/Lua errors detected: {result["project_errors"]}'

        summary = result['production_log_summary']
        trial = result['native_indexed_hud_trial']
        print(json.dumps({
            'outcome': result['outcome'],
            'clock_source': trial['clock_source'],
            'hud_target': {
                'code': trial['ships']['hud_third_index_3']['code'],
                'hex': trial['ships']['hud_third_index_3']['hex'],
                'locked_on_subscribe': trial['probes']['on_subscribe']['hud_code'],
                'locked_mid_stream': trial['probes']['mid_stream']['hud_code'],
                'locked_post_stream': trial['probes']['post_stream']['hud_code'],
            },
            'remote_index_2_disp_m': trial['ships']['remote_index_2']['displacement_m'],
            'local_index_1_disp_m': trial['ships']['local_index_1']['displacement_m'],
            'third_index_3_disp_m': trial['ships']['hud_third_index_3']['displacement_m'],
            'readback_line_count': summary['readback_line_count'],
            'first_readback': summary['first_readback'],
            'last_readback': summary['last_readback'],
            'withdrawal_count': summary['withdrawal_count'],
            'first_withdrawal_reason': summary['first_withdrawal_reason'],
            'protected_unchanged': result['protected_unchanged'],
            'view_removed': result['view_removed'],
            'x4_running_after': result['x4_running_after'],
            'project_error_count': result['project_error_count'],
            'out': str(out),
        }, indent=2))
        return 0
    finally:
        release_shared_lock()


if __name__ == '__main__':
    main()
