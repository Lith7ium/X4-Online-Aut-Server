#!/usr/bin/env python3
"""Native Windows X4 9.00 multi-pilot verification for opt-in receiving-cockpit Company ship playback.

Exercises up to three other pilots in one cockpit (cockpit, cockpit2, cockpit3) plus independent map interest
through the real production CompanyMotionSource(cockpit_radar_playback=True) -> ReconnectingCompanyMotionClient ->
CompanyMotionService -> real native Windows X4 9.00 production Lua/MD modules over PID-validated loopback UDP.

Remote pilot trajectories are recorded/loopback feeds on the same Windows host driving real native X4 hull writes
and delayed readbacks; own-ship displacement must stay 0.0000 m throughout.

Safety:
- Refuses to launch if X4.exe is already running.
- Disposable profile 45850 and isolated x4ls view only; asserts protected_unchanged, view_removed, x4_running_after==False.
- Small 800x600 muted fast window started with SW_SHOWNOACTIVATE and kept at HWND_BOTTOM.
"""
import argparse
import ctypes
import json
import math
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

EXTENSION_ID = 'x4_neon_company_cockpit_multi'
LOGFILE = 'neon-company-cockpit-multi.log'
PROFILE_DEFAULT = 45850


def compose_extension(extensions_dir, *, profile, gamestart, epoch, udp_port, udp_lease, udp_generation):
    ext = Path(extensions_dir) / EXTENSION_ID
    if ext.exists():
        shutil.rmtree(ext)
    for sub in ('ui', 'md', 'native'):
        (ext / sub).mkdir(parents=True)

    (ext / 'content.xml').write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<content id="{EXTENSION_ID}" name="X4 Neon Multi-Pilot Cockpit Trial" '
        'description="Disposable native Windows multi-pilot cockpit playback verification" '
        'author="Neon" version="100" date="2026-11-25" save="false">\n'
        '  <dependency version="900" />\n'
        '</content>\n',
        encoding='utf-8',
    )

    ui_sources = [
        ('authority_registry_resolve_string.lua', 'tools/dev/authority_registry_resolve_string.lua'),
        ('motion_buffer.lua', 'src/authority/lua/motion_buffer.lua'),
        ('motion_playback.lua', 'src/authority/lua/motion_playback.lua'),
        ('company_hull_playback.lua', 'src/authority/lua/company_hull_playback.lua'),
        ('company_hull_playback_x4.lua', 'src/authority/lua/company_hull_playback_x4.lua'),
        ('company_cockpit_observer.lua', 'src/authority/lua/company_cockpit_observer.lua'),
        ('company_cockpit_playback.lua', 'src/authority/lua/company_cockpit_playback.lua'),
        ('company_observer_stream.lua', 'src/authority/lua/company_observer_stream.lua'),
        ('authority_company_cockpit_multipilot_x4.lua', 'tools/dev/authority_company_cockpit_multipilot_x4.lua'),
    ]
    md_sources = [
        ('authority_ship_registry.xml', 'tools/dev/authority_ship_registry.xml'),
        ('company_cockpit_observer.xml', 'src/authority/md/company_cockpit_observer.xml'),
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

    dll_src = ROOT / 'src/lockstep/base/native/x4lan_net_64.dll'
    shutil.copyfile(dll_src, ext / 'native' / 'x4lan_net_64.dll')

    (ext / 'ui/config.lua').write_text(
        'X4NEON_COMPANY_COCKPIT_MULTI = {\n'
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


class X4MultiBridge:
    _TAG = '[X4LAN] AUTH_COMPANY_COCKPIT_MULTI '
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
        self.events = []
        self.align_readbacks = []
        self._next_seq = 1
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    @staticmethod
    def _parse_kv(text):
        return {m.group(1): m.group(2) for m in X4MultiBridge._KV.finditer(text)}

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
                            elif body.startswith('ALIGN_READBACK '):
                                self.align_readbacks.append(self._parse_kv(body[15:]))
                            elif body.startswith('PLAYBACK_EVENT ') or body.startswith('PLAYBACK_FAULT '):
                                self.events.append(body)
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
                    raise TimeoutError('timed out waiting for X4 multi-pilot setup READY')
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


def run_live_multipilot_trial(view, profile_dir, out, args):
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_reconnect import ReconnectingCompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.company_motion_source import CompanyMotionSource
    from lockstep.launcher import steam

    log_path = profile_dir / LOGFILE
    if log_path.exists():
        log_path.unlink()

    # Real production multi-pilot CompanyMotionSource(cockpit_radar_playback=True) -> up to 3 cockpit slots + map!
    recv_source = CompanyMotionSource(cockpit_radar_playback=True)
    assert recv_source.cockpit_max_pilots == 3
    recv_cfg = recv_source.configure(EPOCH, 1, operational_bits='11111')
    service = CompanyMotionService()
    client_recv = client_b = client_c = client_d = client_map = None
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

        bridge = X4MultiBridge(process, log_path, ext_dir, timeout=60.0)
        ready = bridge.wait_ready(timeout=args.timeout)
        home_sector = ready['home_sector']
        map_sector = ready['map_sector']
        p0 = {
            idx: {'x': float(ready[f'p{idx}_x']), 'y': float(ready[f'p{idx}_y']), 'z': float(ready[f'p{idx}_z'])}
            for idx in (2, 3, 4)
        }

        # Admit Receiving Pilot A (1), Pilot B (2), Pilot C (3), Pilot D (4), and Map Pilot E (5)!
        grant_a = service.admit(1, 'a1' * 16, 1, home_sector, SESSION, EPOCH)
        grant_b1 = service.admit(2, 'b1' * 16, 2, home_sector, SESSION, EPOCH)
        grant_c = service.admit(3, 'c1' * 16, 3, home_sector, SESSION, EPOCH)
        grant_d = service.admit(4, 'd1' * 16, 4, home_sector, SESSION, EPOCH)
        grant_map = service.admit(5, 'e1' * 16, 5, map_sector, SESSION, EPOCH)
        service.set_running(True)

        # Real production ReconnectingCompanyMotionClient.observing(...) for multi-pilot cockpit playback!
        client_recv = ReconnectingCompanyMotionClient.observing(
            service.port, grant_a, recv_source, validator,
        )
        src_b1 = _QueuedPilotSource(EPOCH, 2, '1' * 32)
        src_c = _QueuedPilotSource(EPOCH, 3, '2' * 32)
        src_d = _QueuedPilotSource(EPOCH, 4, '3' * 32)
        src_map = _QueuedPilotSource(EPOCH, 5, '4' * 32)
        client_b = CompanyMotionClient(service.port, grant_b1, src_b1, lambda _: True)
        client_c = CompanyMotionClient(service.port, grant_c, src_c, lambda _: True)
        client_d = CompanyMotionClient(service.port, grant_d, src_d, lambda _: True)
        client_map = CompanyMotionClient(service.port, grant_map, src_map, lambda _: True)

        def pump_all():
            service.poll()
            client_recv.poll()
            for cli in (client_b, client_c, client_d, client_map):
                if cli is not None:
                    cli.poll()

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

        pump_until(lambda: (
            client_recv._client is not None and client_recv._client._running
            and client_b._running and client_c._running and client_d._running and client_map._running
        ), label='all_clients_running')

        # Match Windows QPC clock domain used by company_motion_observer.py (346bd5f1).
        r_start = bridge.request('start_stream', pump=pump_all, clock_base=time.perf_counter())
        assert r_start.get('status') == 'stream_started'

        # Three distinct recorded/loopback remote pilot velocity vectors (m/s):
        velocities = {
            2: (18.0, 4.0, 12.0),     # Pilot B: 22.00 m/s
            3: (-14.0, 10.0, -24.0),  # Pilot C: 29.53 m/s
            4: (0.0, -15.0, 20.0),    # Pilot D: 25.00 m/s
        }
        dt = 0.10
        sim_t = {2: 0.0, 3: 0.0, 4: 0.0}
        seqs = {2: 0, 3: 0, 4: 0}
        last_sent = {2: dict(p0[2]), 3: dict(p0[3]), 4: dict(p0[4])}

        def push_pilot(idx, source_obj, *, with_vel=True, override_pos=None):
            seqs[idx] += 1
            sim_t[idx] += dt
            vx, vy, vz = velocities[idx]
            if override_pos is not None:
                px, py, pz = override_pos
            else:
                px = p0[idx]['x'] + vx * sim_t[idx]
                py = p0[idx]['y'] + vy * sim_t[idx]
                pz = p0[idx]['z'] + vz * sim_t[idx]
            last_sent[idx] = {'x': px, 'y': py, 'z': pz}
            source_obj.items.append({
                'version': 3, 'epoch': EPOCH, 'generation': source_obj.local_generation,
                'index': idx, 'sequence': seqs[idx], 'sector_macro': home_sector,
                'source_time': 100.0 + sim_t[idx], 'capture_time': 500.0 + sim_t[idx],
                'x': px, 'y': py, 'z': pz,
                'real_velocity': {'x': vx, 'y': vy, 'z': vz} if with_vel else None,
            })
            return {'x': px, 'y': py, 'z': pz, 'sequence': seqs[idx]}

        def push_map(seq_m):
            src_map.items.append({
                'version': 3, 'epoch': EPOCH, 'generation': src_map.local_generation,
                'index': 5, 'sequence': seq_m, 'sector_macro': map_sector,
                'source_time': 100.0 + seq_m * 0.2, 'capture_time': 500.0 + seq_m * 0.2,
                'x': 10000.0 + seq_m * 5.0, 'y': 0.0, 'z': 10000.0,
                'real_velocity': None if seq_m == 1 else {'x': 25.0, 'y': 0.0, 'z': 0.0},
            })

        # ==================== STAGE 1: 3 Visible Pilots (HUD + 2 Radar) at Different Velocities + Map ====================
        r_lock = bridge.request('lock_hud', pump=pump_all, index=2)
        assert r_lock.get('status') == 'hud_locked' and r_lock.get('locked') == 'true', f'HUD lock failed: {r_lock}'
        r_map = bridge.request('set_map_enabled', pump=pump_all, enabled=True)
        assert r_map.get('status') == 'map_set'

        pump_until(lambda: (
            {2, 3, 4}.issubset((client_recv._client._sent_view or {}).get(home_sector, ()))
            and 5 in (client_recv._client._sent_view or {}).get(map_sector, ())
        ), label='stage1_3_cockpit_pilots_plus_map_subscribed')

        push_map(1); push_map(2)
        for i in range(22):
            push_pilot(2, src_b1, with_vel=(i > 0))
            push_pilot(3, src_c, with_vel=(i > 0))
            push_pilot(4, src_d, with_vel=(i > 0))
            pump_for(dt)
        pump_for(0.25)

        s1 = bridge.request('probe_state', pump=pump_all)
        obs_s1 = recv_source.observer_stats()
        s1_poses = {idx: {a: float(s1[f'p{idx}_{a}']) for a in ('x', 'y', 'z')} for idx in (2, 3, 4)}
        s1_disps = {idx: _dist3(p0[idx], s1_poses[idx]) for idx in (2, 3, 4)}
        slot_b_s1, slot_c_s1, slot_d_s1 = s1['p2_slot'], s1['p3_slot'], s1['p4_slot']
        assert len({slot_b_s1, slot_c_s1, slot_d_s1}) == 3 and 'none' not in {slot_b_s1, slot_c_s1, slot_d_s1}, f'bad slots: {s1}'
        assert s1['p2_mode'] == 'hud' and s1['p3_mode'] == 'radar' and s1['p4_mode'] == 'radar', f'bad modes: {s1}'
        for idx in (2, 3, 4):
            assert s1_disps[idx] > 25.0, f'pilot {idx} did not move enough in stage 1: {s1_disps}'
            assert int(s1[f'p{idx}_rb']) >= 10, f'pilot {idx} readbacks missing: {s1}'
        assert obs_s1['slots']['map']['samples_confirmed'] >= 1, f'map interest missing: {obs_s1}'
        assert float(s1['own_disp_m']) < 0.01
        c_serial_before = obs_s1['slots'][slot_c_s1]['last_confirmed']['serial']

        # ==================== STAGE 2: Unlock HUD -> Seamless hud->radar for Pilot B while all 3 keep moving ====================
        r_unlock = bridge.request('unlock_hud', pump=pump_all)
        assert r_unlock.get('status') == 'hud_unlocked' and r_unlock.get('has_target') == 'false'
        for _ in range(12):
            push_pilot(2, src_b1, with_vel=True)
            push_pilot(3, src_c, with_vel=True)
            push_pilot(4, src_d, with_vel=True)
            pump_for(dt)
        pump_for(0.20)
        s2 = bridge.request('probe_state', pump=pump_all)
        s2_poses = {idx: {a: float(s2[f'p{idx}_{a}']) for a in ('x', 'y', 'z')} for idx in (2, 3, 4)}
        assert s2['p2_mode'] == 'radar' and s2['has_target'] == 'false'
        assert s2['p2_slot'] == slot_b_s1 and s2['p3_slot'] == slot_c_s1 and s2['p4_slot'] == slot_d_s1
        assert float(s2['own_disp_m']) < 0.01

        # ==================== STAGE 3: Independent 5 s Loss on Pilot B ONLY while Pilot C & D Keep Moving ====================
        b_loss_t0 = time.monotonic()
        service.transport_lost(2)
        client_b.close(transport_lost=True)
        client_b = None
        loss_samples = []
        target_times = [1.0, 4.2, 5.5, 6.3]
        next_t_idx = 0
        next_push_at = time.monotonic() + dt
        while next_t_idx < len(target_times):
            now = time.monotonic()
            if now >= next_push_at:
                next_push_at = now + dt
                push_pilot(3, src_c, with_vel=True)
                push_pilot(4, src_d, with_vel=True)
            pump_all()
            if now - b_loss_t0 >= target_times[next_t_idx]:
                pr = bridge.request('probe_state', pump=pump_all)
                loss_samples.append({
                    'elapsed_s': round(time.monotonic() - b_loss_t0, 3),
                    'p2_x': float(pr['p2_x']), 'p2_writes': int(pr[f'{slot_b_s1}_writes']),
                    'p2_stale': int(pr[f'{slot_b_s1}_stale']),
                    'p3_x': float(pr['p3_x']), 'p3_writes': int(pr[f'{slot_c_s1}_writes']),
                    'p3_stale': int(pr[f'{slot_c_s1}_stale']),
                    'own_disp_m': float(pr['own_disp_m']),
                })
                next_t_idx += 1
            time.sleep(0.005)

        assert loss_samples[1]['p2_x'] > loss_samples[0]['p2_x'] + 20.0, f'Pilot B did not extrapolate: {loss_samples}'
        assert loss_samples[2]['p2_stale'] >= 1, f'Pilot B did not hit 5s cutoff: {loss_samples}'
        assert loss_samples[3]['p2_writes'] == loss_samples[2]['p2_writes'], f'Pilot B wrote after 5s cutoff: {loss_samples}'
        assert abs(loss_samples[3]['p2_x'] - loss_samples[2]['p2_x']) < 1e-4, f'Pilot B drifted after 5s cutoff: {loss_samples}'
        assert loss_samples[3]['p3_stale'] == 0, f'Pilot C must NOT stale when Pilot B drops: {loss_samples}'
        assert loss_samples[3]['p3_writes'] > loss_samples[0]['p3_writes'] + 100, f'Pilot C stopped moving: {loss_samples}'
        assert loss_samples[3]['p3_x'] < loss_samples[0]['p3_x'] - 50.0, f'Pilot C did not keep moving: {loss_samples}'

        # ==================== STAGE 4: Remove Pilot B (Revoke + Leave Radar) -> Pilot C Stays Sticky & Keeps Moving ====================
        # Save Pilot B's frozen pose before moving it out of radar range so Stage 5 can restore it cleanly.
        b_frozen_probe = bridge.request('probe_state', pump=pump_all)
        b_frozen_pose = {a: float(b_frozen_probe[f'p2_{a}']) for a in ('x', 'y', 'z')}
        c_before_remove = {a: float(b_frozen_probe[f'p3_{a}']) for a in ('x', 'y', 'z')}

        # Re-admit Pilot B on gen2 (revokes gen1 via real END|cockpit!) AND move Pilot B to other sector (leaves radar!).
        grant_b2 = service.admit(6, 'b2' * 16, 2, home_sector, SESSION, EPOCH)
        assert grant_b2['generation'] != grant_b1['generation']
        pump_for(0.15)
        r_rem = bridge.request('remove_ship', pump=pump_all, index=2)
        assert r_rem.get('status') == 'removed', f'remove_ship failed: {r_rem}'
        pump_until(lambda: 2 not in (client_recv._client._sent_view or {}).get(home_sector, ()),
                   label='stage4_pilot_b_unsubscribed')

        # Stream Pilot C (and independent map ship E) continuously while Pilot B is gone!
        push_map(3)
        for _ in range(20):
            push_pilot(3, src_c, with_vel=True)
            push_pilot(4, src_d, with_vel=True)
            pump_for(dt)
        pump_for(0.25)

        s4 = bridge.request('probe_state', pump=pump_all)
        obs_s4 = recv_source.observer_stats()
        c_after_remove = {a: float(s4[f'p3_{a}']) for a in ('x', 'y', 'z')}
        c_disp_while_b_removed = _dist3(c_before_remove, c_after_remove)
        assert s4['p2_slot'] == 'none', f'Pilot B slot must clear when removed: {s4}'
        assert s4['p3_slot'] == slot_c_s1, f'Pilot C must stay sticky in {slot_c_s1}: {s4}'
        assert obs_s4['slots'][slot_c_s1]['last_confirmed']['serial'] == c_serial_before, (
            f'Pilot C serial must not churn when Pilot B leaves: {obs_s4["slots"][slot_c_s1]}'
        )
        assert c_disp_while_b_removed > 40.0, f'Pilot C did not keep moving after Pilot B removal: {c_disp_while_b_removed}'
        assert obs_s4['slots']['map']['samples_confirmed'] >= 2, f'map interest broken: {obs_s4}'
        assert float(s4['own_disp_m']) < 0.01

        # ==================== STAGE 5: Pilot B Re-Enters & Reacquires on gen2 (>50 m) While Pilot C Keeps Moving ====================
        r_res = bridge.request('restore_ship', pump=pump_all, index=2,
                               x=b_frozen_pose['x'], y=b_frozen_pose['y'], z=b_frozen_pose['z'])
        assert r_res.get('status') == 'restored', f'restore_ship failed: {r_res}'
        pump_until(lambda: 2 in (client_recv._client._sent_view or {}).get(home_sector, ()),
                   label='stage5_pilot_b_resubscribed')

        # Late revoked gen1 sample MUST be rejected even after Pilot B re-enters!
        obs_s5_pre = recv_source.observer_stats()
        b_slot_now = bridge.request('probe_state', pump=pump_all)['p2_slot']
        assert b_slot_now != 'none'
        b_new_serial = obs_s5_pre['slots'][b_slot_now]['last_confirmed']['serial'] if obs_s5_pre['slots'][b_slot_now]['last_confirmed'] else 1
        r_revoked = bridge.request('inject_generation', pump=pump_all, index=2,
                                   generation=grant_b1['generation'], serial=b_new_serial, sample_seq=999)
        assert r_revoked.get('ok') == 'false' and r_revoked.get('reason') == 'revoked_generation', (
            f'revoked gen1 must be rejected after re-entry: {r_revoked}'
        )

        # Fresh Pilot B client on gen2 resumes 110 m (>50 m) ahead -> 1 authorized alignment + next-update readback!
        src_b2 = _QueuedPilotSource(EPOCH, 2, '9' * 32)
        client_b = CompanyMotionClient(service.port, grant_b2, src_b2, lambda _: True)
        pump_until(lambda: client_b._running, label='client_b_gen2_running')
        seqs[2] = 0
        align_pos = (b_frozen_pose['x'] + 110.0, b_frozen_pose['y'] + 12.0, b_frozen_pose['z'] + 20.0)
        push_pilot(2, src_b2, with_vel=False, override_pos=align_pos)
        pump_for(0.45)

        s5_aligned = bridge.request('probe_state', pump=pump_all)
        assert int(s5_aligned[f'{b_slot_now}_align']) == 1, f'expected 1 alignment on gen2: {s5_aligned}'
        assert 0.0 <= float(s5_aligned['p2_align_err']) < 0.05 and float(s5_aligned['p2_align_el']) > 0.0, (
            f'expected next-update alignment readback: {s5_aligned}'
        )
        # Resume ordinary <=50 m playback for Pilot B on gen2 while Pilot C keeps moving!
        for k in range(1, 16):
            push_pilot(2, src_b2, with_vel=True, override_pos=(
                align_pos[0] + 18.0 * k * dt, align_pos[1] + 4.0 * k * dt, align_pos[2] + 12.0 * k * dt,
            ))
            push_pilot(3, src_c, with_vel=True)
            pump_for(dt)
        pump_for(0.25)

        # Active >50 m jump guard on Pilot B refused while bound!
        r_50m = bridge.request('probe_50m', pump=pump_all, index=2, offset_m=120.0)
        assert r_50m.get('ok') == 'false' and r_50m.get('reason') == 'correction_out_of_bounds', f'50m guard failed: {r_50m}'

        # ==================== STAGE 6: Independent Final MD + FFI Readback on All Pilots ====================
        final_readbacks = {}
        final_disps = {}
        for idx in (2, 3, 4):
            rb = bridge.request('readback', pump=pump_all, index=idx, min_game_delay=0.15)
            assert rb.get('status') == 'readback', f'readback {idx} failed: {rb}'
            pose = {a: float(rb[a]) for a in ('x', 'y', 'z')}
            final_readbacks[str(idx)] = rb
            final_disps[str(idx)] = round(_dist3(p0[idx], pose), 3)

        final_probe = bridge.request('probe_state', pump=pump_all)
        obs_final = recv_source.observer_stats()
        r_done = bridge.request('done', pump=pump_all)
        assert r_done.get('status') == 'done'

        return {
            'outcome': 'done',
            'accepted': True,
            'evidence_source': {
                'remote_pilots': 'recorded_loopback_trajectories_driving_native_x4_production_lua_md',
                'receiving_cockpit': 'real_native_windows_x4_900',
            },
            'ready': ready,
            'stage1_3pilots_plus_map': {
                'slots': {'2': slot_b_s1, '3': slot_c_s1, '4': slot_d_s1},
                'modes': {'2': s1['p2_mode'], '3': s1['p3_mode'], '4': s1['p4_mode']},
                'displacements_m': {str(k): round(v, 3) for k, v in s1_disps.items()},
                'map_samples_confirmed': obs_s1['slots']['map']['samples_confirmed'],
                'own_disp_m': float(s1['own_disp_m']),
            },
            'stage2_hud_to_radar_handoff': {
                'p2_mode': s2['p2_mode'], 'has_target': s2['has_target'],
                'slots_unchanged': (s2['p2_slot'] == slot_b_s1 and s2['p3_slot'] == slot_c_s1 and s2['p4_slot'] == slot_d_s1),
                'own_disp_m': float(s2['own_disp_m']),
            },
            'stage3_independent_5s_loss': loss_samples,
            'stage4_remove_pilot_b_pilot_c_keeps_moving': {
                'p2_slot_after_remove': s4['p2_slot'],
                'p3_slot_sticky': s4['p3_slot'],
                'p3_serial_unchanged': obs_s4['slots'][slot_c_s1]['last_confirmed']['serial'] == c_serial_before,
                'p3_disp_while_p2_removed_m': round(c_disp_while_b_removed, 3),
                'map_samples_confirmed': obs_s4['slots']['map']['samples_confirmed'],
                'own_disp_m': float(s4['own_disp_m']),
            },
            'stage5_reacquisition_and_guards': {
                'revoked_gen1_rejected': r_revoked,
                'align_err_m': float(s5_aligned['p2_align_err']),
                'align_elapsed_s': float(s5_aligned['p2_align_el']),
                'guard_50m_refused': r_50m,
                'align_readbacks': bridge.align_readbacks,
            },
            'stage6_final_readbacks': final_readbacks,
            'final_total_displacements_m': final_disps,
            'final_probe': final_probe,
            'observer_stats_final': obs_final,
            'playback_events': bridge.events,
            'service_traffic': service.participant_traffic(),
        }
    finally:
        for cli in (client_recv, client_b, client_c, client_d, client_map):
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
    if x4_running():
        raise SystemExit('REFUSED: X4.exe is already running; never disturb a running X4 instance.')
    ok, msg = steam.status()
    if not ok:
        raise SystemExit(msg)
    game = Path(args.game) if args.game else game_mod.find_install()
    game_mod.verify_exe(game)
    view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-multipilot-{out.name}'
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
    result = {'options': vars(args), 'game': str(game), 'view': str(view), 'outcome': 'started'}
    ctypes.windll.user32.SetProcessDPIAware()
    try:
        game_mod.create_view(game, view)
        prof_dir = profile_mod.prepare(
            args.profile,
            [*game_mod.dlc_ids(view), EXTENSION_ID],
            width=args.width, height=args.height, fast=True,
        )
        trial = run_live_multipilot_trial(view, prof_dir, out, args)
        result['native_multipilot_trial'] = trial
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
        if log_file.is_file():
            for line in log_file.read_text(encoding='utf-8', errors='replace').splitlines():
                if 'Could not find signature file' in line:
                    continue
                if '[X4LAN] AUTH_COMPANY_COCKPIT_MULTI ' in line:
                    if ' ERROR ' in line or (' PLAYBACK_FAULT ' in line and 'reason=stale' not in line):
                        project_errors.append(line)
                    continue
                if ('[=ERROR=]' in line or 'Error in MD cue' in line) and (
                    'x4_neon_company_cockpit_multi' in line or 'X4Authority' in line or 'X4LAN' in line
                ):
                    project_errors.append(line)
        result.update({
            'protected_unchanged': protected_unchanged,
            'view_removed': view_removed,
            'x4_running_after': x4_running_after,
            'project_error_count': len(project_errors),
            'project_errors': project_errors,
        })
        (out / 'result.json').write_text(json.dumps(result, indent=1, sort_keys=True))

    assert result['protected_unchanged'], 'protected profile/config changed!'
    assert result['view_removed'], 'isolated x4 view was not removed!'
    assert not result['x4_running_after'], 'X4.exe still running after test!'
    assert result['project_error_count'] == 0, f'MD/Lua errors detected: {result["project_errors"]}'

    trial = result['native_multipilot_trial']
    print(json.dumps({
        'outcome': result['outcome'],
        'accepted': trial['accepted'],
        'stage1_disps_m': trial['stage1_3pilots_plus_map']['displacements_m'],
        'stage4_p3_disp_while_p2_removed_m': trial['stage4_remove_pilot_b_pilot_c_keeps_moving']['p3_disp_while_p2_removed_m'],
        'final_disps_m': trial['final_total_displacements_m'],
        'own_disp_m': trial['stage4_remove_pilot_b_pilot_c_keeps_moving']['own_disp_m'],
        'protected_unchanged': result['protected_unchanged'],
        'view_removed': result['view_removed'],
        'x4_running_after': result['x4_running_after'],
        'project_error_count': result['project_error_count'],
        'out': str(out),
    }, indent=2))
    return 0


if __name__ == '__main__':
    main()
