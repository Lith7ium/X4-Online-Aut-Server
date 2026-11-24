"""One isolated Windows X4 9.00 run of opt-in NPC sector-change reacquisition.

Exercises the real ported ExperimentalTCPMotion(allow_sector_reentry=True),
ExperimentalSourceMotionIPC(latest=True), and ExperimentalTCPParticipantClient
reentry callbacks against a live native Windows X4 9.00 process running in an
isolated x4ls view and disposable launcher profile (default: 45850).

Window policy:
  800x600, muted/low-gfx (fast=True), windowed, launched with SW_SHOWNOACTIVATE
  and tucked behind existing windows via HWND_BOTTOM so it keeps a live Vulkan
  swapchain without stealing focus or covering active monitors.

Safety invariants:
  - Refuses if X4.exe is running, Steam is not registered, X4.exe SHA-256 differs
    from pinned X4 9.00, output dir exists, or view dir exists.
  - Snapshots metadata (no file contents) of all other profiles in Documents/Egosoft/X4
    and the installation's extensions folder before and after; asserts identical.
  - Never loads, modifies, or deletes normal player saves or profiles.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tools' / 'dev')]

EXTENSION_ID = 'x4_neon_npc_reacq'
LOGFILE = 'neon-npc-reacq.log'
REGISTRY_XML = ROOT / 'tools/dev/authority_ship_registry.xml'
REACQ_XML = ROOT / 'tools/dev/authority_npc_reacquisition.xml'
REACQ_LUA = ROOT / 'tools/dev/authority_npc_reacquisition_x4.lua'

SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
STARTF_USESHOWWINDOW = 0x00000001

EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
GENERATION = 'c' * 32
OTHER_TRACK = 10055
OTHER_SECTOR = 'cluster_other_observer_macro'
MOTION_INTERVAL = 1 / 240
POLL_INTERVAL = 0.005

CONTENT_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<content id="{EXTENSION_ID}" name="NPC reacquisition probe" '
    'description="Disposable opt-in NPC sector-change reacquisition test" '
    'author="Neon" version="100" date="2026-11-24" save="false">\n'
    '  <dependency version="900" />\n'
    '</content>\n'
)

UI_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<addon name="{EXTENSION_ID}">\n'
    '  <environment type="menus">\n'
    '    <dependency name="ego_detailmonitor" />\n'
    '    <file name="ui/config.lua" />\n'
    '    <file name="ui/authority_npc_reacquisition_x4.lua" />\n'
    '  </environment>\n'
    '</addon>\n'
)


def config_lua(profile, gamestart, epoch):
    return (
        'X4NEON_NPC_REACQ = {\n'
        '  enabled = true,\n'
        f'  extension_id = "{EXTENSION_ID}",\n'
        f'  profile = "{int(profile)}",\n'
        f'  gamestart = "{gamestart}",\n'
        f'  epoch = "{epoch}",\n'
        '}\n'
    )


def compose_extension(extensions_dir, *, profile, gamestart, epoch):
    if not gamestart.replace('_', '').isalnum():
        raise SystemExit('invalid gamestart id')
    target = Path(extensions_dir) / EXTENSION_ID
    target.mkdir()
    (target / 'ui').mkdir()
    (target / 'md').mkdir()
    shutil.copyfile(REGISTRY_XML, target / 'md' / 'authority_ship_registry.xml')
    shutil.copyfile(REACQ_XML, target / 'md' / 'authority_npc_reacquisition.xml')
    shutil.copyfile(REACQ_LUA, target / 'ui' / 'authority_npc_reacquisition_x4.lua')
    (target / 'ui' / 'config.lua').write_text(config_lua(profile, gamestart, epoch), encoding='ascii')
    (target / 'content.xml').write_text(CONTENT_XML, encoding='utf-8')
    (target / 'ui.xml').write_text(UI_XML, encoding='utf-8')
    return target


def tree_metadata(path):
    path = Path(path)
    result = {}
    if not path.exists():
        return result
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            full = Path(dirpath) / name
            info = full.lstat()
            result[str(full.relative_to(path))] = [info.st_size, info.st_mtime_ns]
    return result


def x4_running():
    out = subprocess.run(
        ['tasklist', '/FI', 'IMAGENAME eq X4.exe', '/FO', 'CSV', '/NH'],
        capture_output=True, text=True, check=True,
    ).stdout
    return 'x4.exe' in out.lower()


def find_window(pid):
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            if area > 0:
                found.append((area, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return max(found)[1] if found else None


def tuck_window_bottom_corner(hwnd):
    user32 = ctypes.windll.user32
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    win_w = max(100, rect.right - rect.left)
    win_h = max(100, rect.bottom - rect.top)
    x = max(0, screen_w - win_w - 8)
    y = max(0, screen_h - win_h - 48)
    user32.SetWindowPos(
        hwnd, HWND_BOTTOM, x, y, 0, 0,
        SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def remove_view_patiently(game_mod, view, attempts=20, delay=1.5):
    if not view.exists():
        return
    try:
        game_mod.remove_view(view)
        return
    except PermissionError:
        pass
    for _ in range(attempts):
        time.sleep(delay)
        if game_mod.is_link_or_reparse(view) or any(view.iterdir()):
            raise OSError(f'{view} is not an empty view root; left in place')
        try:
            os.rmdir(view)
            return
        except PermissionError:
            continue
    raise OSError(f'{view} stayed in use')


def parse_kv(text):
    fields = {}
    for token in text.split():
        if '=' in token:
            k, v = token.split('=', 1)
            fields[k] = v
    return fields


class X4LogBridge:
    """Thread-safe request/response bridge to the live X4 extension via atomic req_N.lua + log rows."""

    def __init__(self, process, log_path, ext_dir, *, timeout=60.0):
        self.process = process
        self.log_path = Path(log_path)
        self.ext_dir = Path(ext_dir)
        self.timeout = float(timeout)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._next_seq = 1
        self.ready_info = None
        self.responses = {}
        self.events = []
        self.error = None
        self.done = False
        self._hwnd = None
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self):
        position = 0
        pending = ''
        last_tuck = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if self._hwnd is None:
                self._hwnd = find_window(self.process.pid)
                if self._hwnd is not None:
                    tuck_window_bottom_corner(self._hwnd)
                    last_tuck = now
            elif now - last_tuck >= 0.75:
                tuck_window_bottom_corner(self._hwnd)
                last_tuck = now

            if self.log_path.is_file():
                try:
                    with self.log_path.open('rb') as handle:
                        handle.seek(position)
                        chunk = handle.read()
                        position += len(chunk)
                except OSError:
                    chunk = b''
                if chunk:
                    pending += chunk.decode('utf-8', errors='replace')
                    lines, _, pending = pending.rpartition('\n')
                    with self._cond:
                        for line in lines.splitlines():
                            if '[X4LAN] AUTH_NPC_REACQ ' not in line:
                                continue
                            payload = line.split('[X4LAN] AUTH_NPC_REACQ ', 1)[1].strip()
                            self.events.append(payload)
                            if payload.startswith('READY '):
                                self.ready_info = parse_kv(payload[len('READY '):])
                                self._cond.notify_all()
                            elif payload.startswith('RESP '):
                                kv = parse_kv(payload[len('RESP '):])
                                seq = int(kv['seq'])
                                self.responses[seq] = kv
                                self._cond.notify_all()
                            elif payload.startswith('ERROR '):
                                self.error = payload[len('ERROR '):]
                                self._cond.notify_all()
                            elif payload == 'DONE':
                                self.done = True
                                self._cond.notify_all()
            if self.process.poll() is not None:
                with self._cond:
                    if not self.done and self.error is None:
                        self.error = f'X4 exited unexpectedly with code {self.process.returncode}'
                    self._cond.notify_all()
                return
            time.sleep(0.05)

    def wait_ready(self, timeout=180.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.ready_info is None and self.error is None:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError('timed out waiting for X4 NPC reacquisition READY')
                self._cond.wait(min(0.25, rem))
            if self.error is not None:
                raise RuntimeError(f'X4 error before READY: {self.error}')
            return dict(self.ready_info)

    def request(self, op, **params):
        with self._lock:
            seq = self._next_seq
            self._next_seq += 1
        fields = [f'seq = {seq}', f'op = {json.dumps(op)}', f'request_id = {json.dumps(f"req-{seq}")}']
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                fields.append(f'{key} = {"true" if value else "false"}')
            elif isinstance(value, (int, float)):
                fields.append(f'{key} = {value!r}')
            else:
                fields.append(f'{key} = {json.dumps(str(value))}')
        body = 'return { ' + ', '.join(fields) + ' }\n'
        partial = self.ext_dir / f'req_{seq}.partial'
        partial.write_text(body, encoding='ascii')
        os.replace(partial, self.ext_dir / f'req_{seq}.lua')

        deadline = time.monotonic() + self.timeout
        with self._cond:
            while seq not in self.responses and self.error is None:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError(f'timed out waiting for X4 response to seq={seq} op={op}')
                self._cond.wait(min(0.2, rem))
            if self.error is not None:
                raise RuntimeError(f'X4 error during seq={seq} op={op}: {self.error}')
            return dict(self.responses[seq])

    def stop(self):
        self._stop.set()
        self._thread.join(3)


class X4NpcReentryController:
    """Opt-in participant client callback backed by a live X4 9.00 process."""

    def __init__(self, bridge, *, expected_index, expected_epoch):
        self.bridge = bridge
        self.expected_index = int(expected_index)
        self.expected_epoch = expected_epoch
        self.history = []
        self._prepared_request = None
        self._aligned_sector = None

    def apply_motion(self, sample, despawn):
        if despawn or sample['index'] != self.expected_index or sample['epoch'] != self.expected_epoch:
            return False
        if self._aligned_sector is None:
            resp = self.bridge.request('bind_initial')
            self.history.append(('snapshot_initial', sample['sequence'], resp))
            return resp.get('status') == 'bound' and resp.get('sector_macro') == sample['sector_macro']
        vel = sample.get('real_velocity') or {}
        resp = self.bridge.request(
            'apply_step',
            expected_sector=sample['sector_macro'],
            x=float(sample['x']),
            y=float(sample['y']),
            z=float(sample['z']),
            vx=vel.get('x'),
            vy=vel.get('y'),
            vz=vel.get('z'),
        )
        self.history.append(('snapshot_post_align', sample['sequence'], resp))
        return resp.get('status') == 'step_applied'

    def prepare(self, previous_sector, sample):
        if sample['index'] != self.expected_index or sample['epoch'] != self.expected_epoch:
            return False
        if sample.get('real_velocity') is not None:
            return False
        resp = self.bridge.request(
            'prepare',
            previous_sector=previous_sector,
            target_sector=sample['sector_macro'],
        )
        self.history.append(('prepare', sample['sequence'], resp))
        if resp.get('status') == 'prepared':
            self._prepared_request = f"req-{resp['seq']}"
            return True
        return False

    def cancel(self, previous_sector, prepared_sample):
        if self._prepared_request is None:
            return False
        resp = self.bridge.request('cancel', prepared_request=self._prepared_request)
        self.history.append(('cancel', prepared_sample['sequence'], resp))
        self._prepared_request = None
        return resp.get('status') == 'canceled' and resp.get('sector_macro') == previous_sector

    def align(self, previous_sector, prepared_sample, sample):
        if self._prepared_request is None:
            return False
        vel = sample.get('real_velocity') or {}
        resp = self.bridge.request(
            'align',
            prepared_request=self._prepared_request,
            target_sector=sample['sector_macro'],
            x=float(sample['x']),
            y=float(sample['y']),
            z=float(sample['z']),
            yaw=35.0,
            pitch=0.0,
            roll=0.0,
            vx=vel.get('x', 0.0),
            vy=vel.get('y', 0.0),
            vz=vel.get('z', 0.0),
        )
        self.history.append(('align', sample['sequence'], resp))
        self._prepared_request = None
        if resp.get('status') == 'aligned' and resp.get('sector_macro') == sample['sector_macro']:
            self._aligned_sector = sample['sector_macro']
            return True
        return False


def run_transport_and_x4(bridge, ready_info):
    """Run real ExperimentalTCPMotion(allow_sector_reentry=True) + 2 participants against live X4."""
    import authority.checkpoints as checkpoints
    from authority.checkpoints import CheckpointStore
    from authority.experimental_local_host import ExperimentalLocalHost
    from authority.experimental_source_motion_ipc import ExperimentalSourceMotionIPC
    from authority.experimental_tcp_control import ExperimentalTCPControl, offer_request
    from authority.experimental_tcp_job_delivery import ExperimentalTCPJobDelivery
    from authority.experimental_tcp_motion import ExperimentalTCPMotion, motion_digest
    from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient
    from authority.experimental_tcp_participants import ExperimentalTCPParticipants
    from authority.save_transfer import SaveOffers
    from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
    from authority.trusted_marker_load_session import TrustedMarkerLoadSession
    from authority.world import WorldCoordinator
    from lockstep.launcher.joinfiles import FileService
    from test_authority_experimental_local_host import Engine
    from test_authority_trusted_marker_load_session import claim_for

    original_sync_dir = checkpoints._sync_dir
    if os.name == 'nt':
        checkpoints._sync_dir = lambda path: None

    class DummyControl:
        def pause(self, handles): return True
        def resume(self, handles): return True

    temp = tempfile.TemporaryDirectory()
    stop_event = threading.Event()
    threads = []
    errors = []
    observer_a_received = []
    sockets_to_close = []
    service = control = None

    try:
        root = Path(temp.name)
        (root / 'store').mkdir()
        store = CheckpointStore(root / 'store')
        service = FileService('a' * 32, bind='127.0.0.1', port=0, slots=(1, 2)).start()
        engine = Engine(root)
        world = WorldCoordinator('a' * 32, engine, store, SaveOffers(service, store))
        control = ExperimentalTCPControl(TrustedMarkerLoadSession(world, enabled=True), enabled=True)
        host = ExperimentalLocalHost(world, control, service, enabled=True)
        participants = ExperimentalTCPParticipants(host, enabled=True, timeout=1.0)
        world._participants = participants

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sockets_to_close.append(listener)
        listener.bind(('127.0.0.1', 0))
        listener.listen(4)
        host.tick()

        def connect_and_admit(handle, *, apply_motion, reentry_controller=None):
            client = socket.create_connection(listener.getsockname(), timeout=2)
            sockets_to_close.append(client)
            server_conn, _ = listener.accept()
            host.adopt(server_conn)
            host.tick()
            client.sendall(offer_request())
            host.tick(0.1)
            host.tick(0.1)
            bound = decode_bound_offer(client.recv(8192).rstrip(b'\n'))
            client.sendall(encode_load_claim(claim_for(bound.offer)) + b'\n')
            host.tick(0.1)

            def run_client():
                try:
                    client.settimeout(5)
                    frame = b''
                    while not frame.endswith(b'\n'):
                        part = client.recv(1)
                        if not part:
                            raise RuntimeError('missing admission')
                        frame += part
                    assert json.loads(frame)['kind'] == 'accepted'
                    app = ExperimentalTCPParticipantClient(
                        client, DummyControl(), enabled=True, epoch='a' * 32,
                        handle=handle, timeout=30, apply_motion=apply_motion,
                        apply_job=lambda x: True, motion_reentry_controller=reentry_controller,
                    )
                    while not stop_event.is_set():
                        app.poll(0.05)
                    app.close()
                except BaseException as exc:
                    if not stop_event.is_set():
                        errors.append(f'peer-{handle}: {type(exc).__name__}: {exc}')

            thread = threading.Thread(target=run_client, daemon=True)
            threads.append(thread)
            thread.start()

        npc_index = int(ready_info['index'])
        origin_sector = ready_info['origin_sector_macro']
        target_sector = ready_info['target_sector_macro']

        x4_controller = X4NpcReentryController(
            bridge, expected_index=npc_index, expected_epoch=EPOCH,
        )

        connect_and_admit(
            1,
            apply_motion=lambda sample, despawn: observer_a_received.append(dict(sample)) or True,
        )
        connect_and_admit(
            2,
            apply_motion=x4_controller.apply_motion,
            reentry_controller=x4_controller,
        )
        host.tick(0.1)
        assert world.session.state == 'running'

        control.enable_latest_motion(enabled=True, timeout=30.0)
        delivery = ExperimentalTCPJobDelivery(host, enabled=True, timeout=30.0)
        motion = ExperimentalTCPMotion(
            delivery, enabled=True, min_interval=MOTION_INTERVAL, allow_sector_reentry=True,
        )

        local_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        remote_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sockets_to_close.extend([local_udp, remote_udp])
        local_udp.bind(('127.0.0.1', 0))
        remote_udp.bind(('127.0.0.1', 0))
        local_udp.connect(remote_udp.getsockname())
        remote_udp.connect(local_udp.getsockname())
        remote_udp.settimeout(15.0)

        members = delivery.membership()
        observer_a, observer_b = members[0], members[1]
        identity_a = (observer_a['id'], observer_a['incarnation'])
        identity_b = (observer_b['id'], observer_b['incarnation'])
        recipients = {OTHER_SECTOR: observer_a, origin_sector: observer_b}

        def select_recipients(sample, _members):
            member = recipients.get(sample['sector_macro'])
            return [dict(member)] if member is not None else []

        source = ExperimentalSourceMotionIPC(
            local_udp, motion, enabled=True, lease='1' * 32, attempt='2' * 32,
            epoch=EPOCH, generation=GENERATION, guard=lambda: True,
            validate_peer=lambda peer: peer == remote_udp.getsockname(), timeout=45.0,
            select_recipients=select_recipients, latest=True,
            saved_baseline=20000,
            saved_guard=lambda epoch, count: epoch == EPOCH and count == 20000,
            saved_lifetime_guard=lambda epoch, count: epoch == EPOCH and count == 20000,
        )
        source.bind_confirmed(npc_index)
        source.bind_confirmed(OTHER_TRACK)

        def wait_for(predicate, timeout=60.0, label='condition'):
            deadline = time.monotonic() + timeout
            while not predicate():
                if errors:
                    raise RuntimeError(f'participant error while waiting for {label}: {errors}')
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'timed out waiting for {label}')
                host.tick(POLL_INTERVAL)

        def pull_v3(index, sequence, sector, source_time, capture_time, pos, rate):
            deadline = time.monotonic() + 1.0
            time.sleep(MOTION_INTERVAL + 0.001)
            while not source.begin_latest():
                if time.monotonic() >= deadline:
                    raise TimeoutError('begin_latest remained rate-limited')
                time.sleep(MOTION_INTERVAL + 0.001)
            req = remote_udp.recv(769).decode('ascii').split('|')[-1]
            remote_udp.send('|'.join([
                'S', '1' * 32, '2' * 32, 'SAMPLE3', EPOCH, GENERATION,
                req, str(index), str(sequence), sector, str(source_time), str(capture_time),
                str(pos[0]), str(pos[1]), str(pos[2]), *rate,
            ]).encode('ascii'))
            complete, value = source.poll_latest()
            assert complete
            return value

        npc_track = motion._tracks[(EPOCH, GENERATION, npc_index)]
        other_track = motion._tracks[(EPOCH, GENERATION, OTHER_TRACK)]
        init_pos = (float(ready_info['x']), float(ready_info['y']), float(ready_info['z']))
        align_pos = (15000.0, 2500.0, -4000.0)
        align_vel = ('20', '0', '0')

        steps = []

        # Step 1: Initial snapshot in origin_sector (X4 confirms same actor + origin sector/zone)
        s1 = pull_v3(npc_index, 1, origin_sector, 100.0, 10.0, init_pos, ('-', '-', '-'))
        wait_for(lambda: npc_track['peer_samples'].get(identity_b) == (1, motion_digest(s1)), label='step1_initial')
        steps.append({'step': '1_initial', 'x4': x4_controller.history[-1][2], 'peer_b_frame': npc_track['peer_frames'][identity_b]})

        # Step 2: Unseen sector change while Peer B excluded; Observer A keeps moving
        assert pull_v3(npc_index, 2, target_sector, 102.0, 12.0, (14800.0, 2500.0, -4000.0), ('-', '-', '-')) is None
        oa1 = pull_v3(OTHER_TRACK, 1, OTHER_SECTOR, 102.5, 12.5, (10.0, 20.0, 30.0), ('15', '0', '0'))
        wait_for(lambda: other_track['peer_samples'].get(identity_a) == (1, motion_digest(oa1)), label='observer_a_1')
        steps.append({'step': '2_unseen_sector_change', 'peer_b_frame': npc_track['peer_frames'][identity_b], 'observer_a_seq': 1})

        # Step 3: Peer B re-sights NPC -> real X4 prepare (zero movement)
        recipients[target_sector] = observer_b
        pull_v3(npc_index, 3, target_sector, 103.0, 13.0, (14900.0, 2500.0, -4000.0), ('20', '0', '0'))
        wait_for(lambda: npc_track['reentries'].get(identity_b, {}).get('phase') == 'prepared', label='step3_prepare')
        steps.append({'step': '3_prepare', 'x4': x4_controller.history[-1][2], 'peer_b_frame': npc_track['peer_frames'][identity_b]})

        # Step 4: View lost during prepare -> real X4 cancel (zero movement, no disconnect)
        del recipients[target_sector]
        assert pull_v3(npc_index, 4, target_sector, 104.0, 14.0, (14920.0, 2500.0, -4000.0), ('20', '0', '0')) is None
        wait_for(control._motion_idle, label='step4_cancel')
        oa2 = pull_v3(OTHER_TRACK, 2, OTHER_SECTOR, 104.5, 14.5, (40.0, 20.0, 30.0), ('15', '0', '0'))
        wait_for(lambda: other_track['peer_samples'].get(identity_a) == (2, motion_digest(oa2)), label='observer_a_2')
        steps.append({'step': '4_cancel_on_view_loss', 'x4': x4_controller.history[-1][2], 'peer_b_frame': npc_track['peer_frames'][identity_b], 'observer_a_seq': 2})

        # Step 5: Re-sighted again -> fresh prepare -> post-prepare PULL -> real X4 cross-sector warp align
        recipients[target_sector] = observer_b
        pull_v3(npc_index, 5, target_sector, 105.0, 15.0, (14960.0, 2500.0, -4000.0), ('20', '0', '0'))
        wait_for(lambda: npc_track['reentries'].get(identity_b, {}).get('phase') == 'prepared', label='step5_reprepare')
        steps.append({'step': '5a_reprepare', 'x4': x4_controller.history[-1][2], 'peer_b_frame': npc_track['peer_frames'][identity_b]})

        fresh = pull_v3(npc_index, 6, target_sector, 106.0, 16.0, align_pos, align_vel)
        wait_for(lambda: npc_track['peer_samples'].get(identity_b) == (6, motion_digest(fresh)), label='step5_align')
        align_x4 = x4_controller.history[-1][2]
        steps.append({'step': '5b_fresh_align', 'x4': align_x4, 'peer_b_frame': npc_track['peer_frames'][identity_b]})

        # Step 6: Independent later X4 readback after >= 1.5s game time (confirms same registry ship in target sector & motion)
        later_rb = bridge.request('readback', min_game_delay=1.5)
        assert later_rb.get('status') == 'readback'
        assert later_rb.get('sector_macro') == target_sector
        assert later_rb.get('actor_key') == ready_info['actor_key']
        assert later_rb.get('idcode') == ready_info['idcode']
        dx_drift = float(later_rb['x']) - float(align_x4['x'])
        dy_drift = float(later_rb['y']) - float(align_x4['y'])
        dz_drift = float(later_rb['z']) - float(align_x4['z'])
        drift_m = math.sqrt(dx_drift ** 2 + dy_drift ** 2 + dz_drift ** 2)
        steps.append({'step': '6_later_x4_readback', 'x4': later_rb, 'displacement_since_align_m': round(drift_m, 3)})

        # Step 7a: Resumed normal motion_snapshot in target sector (<= 50m step from current readback)
        step_target = (float(later_rb['x']) + 12.0, float(later_rb['y']), float(later_rb['z']))
        s7 = pull_v3(npc_index, 7, target_sector, 108.0, 18.0, step_target, align_vel)
        wait_for(lambda: npc_track['peer_samples'].get(identity_b) == (7, motion_digest(s7)), label='step7_resumed_motion')
        steps.append({'step': '7a_resumed_snapshot_within_50m', 'x4': x4_controller.history[-1][2], 'peer_b_frame': npc_track['peer_frames'][identity_b]})

        # Step 7b: Direct >50m correction probe must be refused by the 50m limit
        over_50m = bridge.request(
            'apply_step',
            expected_sector=target_sector,
            x=step_target[0] + 120.0,
            y=step_target[1],
            z=step_target[2],
        )
        assert over_50m.get('status') == 'refused_over_50m', f'expected 50m refusal, got {over_50m}'
        steps.append({'step': '7b_50m_limit_check', 'x4': over_50m})

        # Observer A receives final uninterrupted sample
        oa3 = pull_v3(OTHER_TRACK, 3, OTHER_SECTOR, 108.5, 18.5, (85.0, 20.0, 30.0), ('15', '0', '0'))
        wait_for(lambda: other_track['peer_samples'].get(identity_a) == (3, motion_digest(oa3)), label='observer_a_3')

        done_resp = bridge.request('done')
        assert done_resp.get('status') == 'done'

        return {
            'accepted': True,
            'npc': ready_info,
            'steps': steps,
            'observer_a_sequences': [s['sequence'] for s in observer_a_received],
            'peer_errors': errors,
        }
    finally:
        stop_event.set()
        for t in threads:
            t.join(2)
        for sock in sockets_to_close:
            try:
                sock.close()
            except OSError:
                pass
        if control is not None and not (control._closed or control._failed):
            try:
                control.close()
            except BaseException:
                pass
        if service is not None:
            try:
                service.close()
                service.thread.join(2)
            except BaseException:
                pass
        temp.cleanup()
        checkpoints._sync_dir = original_sync_dir


def main(argv=None):
    from lockstep.launcher import game as game_mod, profile as profile_mod, steam

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--game')
    parser.add_argument('--profile', type=int, default=45850)
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
        raise SystemExit('X4.exe is running; close it first')
    ok, message = steam.status()
    if not ok:
        raise SystemExit(message)
    game = Path(args.game) if args.game else game_mod.find_install()
    game_mod.verify_exe(game)
    view = Path(os.path.splitdrive(str(game.resolve()))[0] + '\\') / 'x4ls-lanes' / f'neon-reacq-{out.name}'
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
    (out / 'protected-before.json').write_text(json.dumps(protected_before, indent=1, sort_keys=True))

    result = {
        'options': vars(args),
        'game': str(game),
        'view': str(view),
        'outcome': 'started',
    }
    process = log_path = bridge = None
    ctypes.windll.user32.SetProcessDPIAware()
    try:
        game_mod.create_view(game, view)
        ext_dir = compose_extension(
            view / 'extensions', profile=args.profile, gamestart=args.gamestart, epoch=EPOCH,
        )
        profile_dir = profile_mod.prepare(
            args.profile,
            [*game_mod.dlc_ids(view), EXTENSION_ID],
            width=args.width,
            height=args.height,
            fast=True,
        )
        log_path = profile_dir / LOGFILE
        command = [
            str(view / 'X4.exe'),
            '-personalfolderid', str(args.profile),
            '-skipintro', '-noworkshopsync', '-nocputhrottle',
            '-debug', 'scripts', '-logfile', LOGFILE,
        ]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= STARTF_USESHOWWINDOW
        si.wShowWindow = SW_SHOWNOACTIVATE
        process = subprocess.Popen(
            command, cwd=view, env=steam.client_environment(), startupinfo=si,
        )
        result['pid'] = process.pid
        bridge = X4LogBridge(process, log_path, ext_dir, timeout=60.0)
        ready_info = bridge.wait_ready(timeout=args.timeout)
        run_data = run_transport_and_x4(bridge, ready_info)
        result['run'] = run_data
        result['outcome'] = 'done'
    except BaseException as exc:
        result['outcome'] = 'error'
        result['error'] = f'{type(exc).__name__}: {exc}'
        if bridge is not None:
            result['x4_events'] = list(bridge.events)
    finally:
        if bridge is not None:
            bridge.stop()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(25)
        if process is not None:
            result['exit_code'] = process.returncode
        time.sleep(1.5)
        if log_path is not None and log_path.is_file():
            shutil.copyfile(log_path, out / LOGFILE)
        try:
            remove_view_patiently(game_mod, view)
        except OSError as exc:
            result['cleanup_error'] = f'{type(exc).__name__}: {exc}'
        protected_after = {
            'other_profiles': {
                entry.name: tree_metadata(entry)
                for entry in sorted(documents.iterdir())
                if entry.is_dir() and entry.name != str(args.profile)
            },
            'game_extensions': sorted(entry.name for entry in (game / 'extensions').iterdir()),
        }
        (out / 'protected-after.json').write_text(json.dumps(protected_after, indent=1, sort_keys=True))
        result['protected_unchanged'] = protected_after == protected_before
        result['view_removed'] = not view.exists()
        result['x4_running_after'] = x4_running()
        (out / 'result.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
        print(json.dumps(result, indent=2, default=str))
    return 0 if (result['outcome'] == 'done' and result['protected_unchanged']
                 and result['view_removed'] and not result['x4_running_after']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
