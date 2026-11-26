"""Create a fresh base-game Company world and enroll its ships in the native registry."""
import argparse
import gzip
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import signal
import socket
import sys
import time
from xml.etree import ElementTree as ET

import authority_recovered_loopback_server as supervisor
import authority_recovery_registry_status as registry
import authority_software_vulkan as software
import authority_world_bridge_live as world_bridge
from authority_provider_server import private_display

from authority.checkpoints import CheckpointStore
from authority.local_control_bridge import LocalControlBridge
from authority_control_cycle_evidence import read_cycle_evidence

bridge, live, owned, snapshot = supervisor.bridge, supervisor.live, supervisor.owned, supervisor.snapshot
ROOT = Path(__file__).resolve().parents[2]


def verify_base_save(checkpoint):
    """Read only X4's header. Accept an empty current and historical DLC list."""
    with gzip.open(checkpoint.path, 'rb') as incoming:
        header = incoming.read(1 << 16)
    info, separator, _ = header.partition(b'</info>')
    if not separator:
        raise ValueError('missing X4 save info')
    root = ET.fromstring(info + separator + b'</savegame>')
    game = root.find('info/game')
    if (root.tag != 'savegame' or game is None or game.get('start') != 'custom_creative'
            or root.findall('./info/patches//patch')):
        raise ValueError('expected a fresh base-game Creative universe without DLC')


def install(case, profile, port, lease, epoch, nonce, enrollment, pair_nonce, *, fighter_count=2,
            boarding_transport=False):
    import authority_vanilla_pair as pair
    world_bridge.install_probe(case, port, lease, epoch, marker_required=True)
    lab = case / 'extensions/x4_lan_lab'
    shutil.copyfile(ROOT / 'tools/dev/authority_identity_fixture.xml', lab / 'md/authority_identity_fixture.xml')
    probe = lab / 'ui/authority_world_bridge_probe.lua'
    text = probe.read_text('ascii')
    text = bridge.replace_once(text, 'local function frame()',
        'local activated=false\nlocal setup_pair, pair_ready, pause_requested, paused_at\nlocal function frame()')
    # The overlay acts only in the new Creative world. Keep it paused through
    # fixture preparation, full faction census, enrollment and the native save.
    activation = """ if not pair_ready then
  if not engine.is_paused() then
   assert(not paused_at, 'genesis pause lost')
   if not pause_requested then engine.pause(); pause_requested=true end
   return
  end
  if not paused_at then paused_at=engine.game_time() end
  if not setup_pair then
   setup_pair=X4LAN_VANILLA_PAIR.new_x4({nonce='PAIR_NONCE',mode='prepare'},function()
    return engine.ready() and engine.is_paused() and engine.game_time()==paused_at
   end)
  end
  if not setup_pair() then return end
  pair_ready=true
 end
 if not activated then
  activated=true
 end
"""
    text = bridge.replace_once(text, ' if not bridge then\n', activation.replace('PAIR_NONCE', pair_nonce)
                               + ' if not bridge then\n')
    # The status/enrollment overlay guards both startup and the save marker.
    probe.write_text(text, 'ascii')
    pair.install_genesis(case, profile, pair_nonce, fighter_count=fighter_count, boarding_transport=boarding_transport)
    registry.install(case, nonce, ('uninitialized',), enroll=enrollment)
    return [path for path in lab.rglob('*') if path.is_file()] + [profile/'config.xml', profile/'content.xml']


def run(game, out, display, auth, timeout, seed, *, fighter_count=2, boarding_transport=False,
        software_rendered=True, display_hostname=None):
    import authority_vanilla_pair as pair
    game = game.resolve(strict=True)
    out = snapshot.output_path(out)
    if os.geteuid() == 0 or live.sha(game/'X4') != live.EXE_SHA or live.x4_executables():
        raise ValueError('need a stopped pinned X4 installation and unprivileged user')
    user = pwd.getpwuid(os.geteuid())
    home = Path(user.pw_dir)
    steam = live.steam_identity(home, user.pw_uid)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {'supervisor_version': 1, 'success': False, 'official_extensions': [],
              'display_mode': 'private_software' if software_rendered else 'desktop_gpu'}
    handlers, lockbox, processes, errors = {}, [], [], []
    case = checkpoint = None
    try:
        case, profile, binds, lock = snapshot.prepare_case(out, game, home, base_game_only=True)
        if binds or {p.name for p in (case/'extensions').iterdir()} != {'x4_lan_lab'}:
            raise ValueError('fresh private game view includes unexpected extensions')
        epoch, lease, nonce, pair_nonce = [secrets.token_hex(16) for _ in range(4)]
        enrollment = dict(epoch=secrets.token_hex(16), nonce=secrets.token_hex(16),
                          limit=200000, save_path=str(profile/'save'))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.1', 0))
            sock.settimeout(.5)
            files = install(case, profile, sock.getsockname()[1], lease, epoch, nonce, enrollment, pair_nonce,
                            fighter_count=fighter_count, boarding_transport=boarding_transport)
            expected = {path: live.sha(path) for path in files}
            (out/'checkpoints').mkdir(mode=0o700)
            store = CheckpointStore(out/'checkpoints')
            cmd = snapshot.command_for(case, game, binds, lock, display, auth, user.pw_name, seed)
            boundary = cmd.index('--die-with-parent')
            cmd[boundary:boundary] = [part for path in files for part in ('--ro-bind', str(path), str(path))]
            bridge.acquire_lock_with_handlers(handlers, lockbox, user.pw_uid, user.pw_gid)
            if software_rendered:
                cmd = software.constrain(cmd, display=display, authorization=auth, icd=live.lavapipe_icd(),
                                         display_hostname=display_hostname)
                software.verify(cmd, out/'software-vulkan.log')
            proc, started = owned.spawn_owned_gated(cmd, out/'process.log', processes)
            deadline = time.monotonic() + timeout
            log = profile/'engine-output.log'
            last_offset = 0
            hello = f'HELLO|{lease}|{epoch}'.encode()
            while True:
                if proc.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('fresh game exited or timed out before registry enrollment')
                if log.exists():
                    with log.open('rb') as stream:
                        stream.seek(last_offset)
                        for line in stream:
                            if not line.endswith(b'\n'):
                                break
                            last_offset += len(line)
                            if b'[X4LAN] AUTH_WORLD_BRIDGE ERROR' in line:
                                raise ValueError('fresh world overlay stopped; see private engine log')
                try:
                    data, peer = sock.recvfrom(769)
                except socket.timeout:
                    continue
                if data != hello or peer[0] != '127.0.0.1':
                    raise ValueError('unexpected enrollment datagram')
                break
            identity = owned.client.game_identity(proc, started, game)
            if identity is None:
                raise ValueError('missing owned X4 at enrollment')
            def verify():
                if (live.steam_identity(home, user.pw_uid) != steam
                        or owned.client.game_identity(proc, started, game) != identity
                        or any(live.sha(path) != digest for path, digest in expected.items())):
                    raise ValueError('genesis process or inputs changed')
            verify()
            receipts = pair.genesis_receipts(log, pair_nonce, supervisor.read_evidence,
                                             fighter_count=fighter_count, boarding_transport=boarding_transport)
            result.update(receipts)
            registry.matches_log(log, nonce, 'startup', ('uninitialized',))
            count = registry.enrollment_receipt(log, enrollment['nonce'], enrollment['epoch'], enrollment['limit'])
            adapter = LocalControlBridge(enabled=True, sock=sock, peer=peer, lease=lease, world_epoch=epoch,
                                         profile=profile, store=store, verify_checkpoint=read_cycle_evidence,
                                         marker_required=True, timeout=130, save_timeout=480)
            checkpoint = supervisor.capture_first_registry_snapshot(adapter, store, verify)
            status = registry.saved_status(checkpoint)
            registry.checkpoint_status(('bootstrap', enrollment['epoch'], count), status)
            registry.matches_log(log, nonce, checkpoint.marker, status)
            verify_base_save(checkpoint)
            result['registry_status'] = dict(state='ready', epoch=status[1], count=count,
                                             mapping_sha256=status[3], checkpoint_id=checkpoint.checkpoint_id)
            result.update(final_world_state='paused', final_checkpoint_id=checkpoint.checkpoint_id,
                          final_checkpoint_sha256=checkpoint.sha256, success=True)
    except (Exception, KeyboardInterrupt) as error:
        result.update(success=False, error=str(error) or type(error).__name__)
    finally:
        for number in handlers:
            signal.signal(number, signal.SIG_IGN)
        remaining = bridge.stop_owned_processes(processes, errors)
        result['owned_processes_remaining'] = remaining
        supervisor.finish_recovery_lock(lockbox, remaining, True, errors)
        try:
            if case:
                if result['success'] and remaining == []:
                    final_receipts = pair.genesis_receipts(log, pair_nonce, supervisor.read_evidence,
                                                           fighter_count=fighter_count, boarding_transport=boarding_transport)
                    if final_receipts != receipts:
                        raise ValueError('native ship receipts changed before cleanup')
                    if any(live.sha(path) != digest for path, digest in expected.items()):
                        raise ValueError('genesis overlay changed before cleanup')
                    pair.archive_genesis(case, out)
                bridge.redact_stopped_config(case/'extensions/x4_lan_lab/ui/config.lua', remaining)
            if result['success'] and remaining == [] and not errors:
                if live.steam_identity(home, user.pw_uid) != steam:
                    raise ValueError('Steam identity changed')
                store.confirm(checkpoint)
        except Exception as error:
            errors.append(str(error))
        if errors or remaining != []:
            result.update(success=False, cleanup_errors=errors)
        for number, handler in handlers.items():
            signal.signal(number, handler)
        (out/'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    screens = parser.add_mutually_exclusive_group()
    screens.add_argument('--xvfb', default='Xvfb')
    screens.add_argument('--desktop-display', help='use a licensed local GPU for the one-time census')
    parser.add_argument('--timeout', type=int, default=7200)
    parser.add_argument('--seed', type=int, default=276944566)
    parser.add_argument('--fighter-count', type=int, choices=(2, 4), default=2,
                        help='number of equipped company fighters to place in Black Hole Sun V')
    parser.add_argument('--boarding-transport', action='store_true',
                        help='place a captained Company transport with eight marines before registry enrollment')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 86400 or not 0 <= args.seed < 2**32:
        parser.error('invalid timeout or seed')
    if args.desktop_display:
        display, auth = live.desktop_display(args.desktop_display)
        if not os.access(auth, os.R_OK):
            parser.error('desktop rendering requires readable Xauthority')
        result = run(args.game, args.out, display, auth, args.timeout, args.seed,
                     fighter_count=args.fighter_count, boarding_transport=args.boarding_transport, software_rendered=False)
    else:
        with private_display(args.xvfb, 'xauth', 'xdpyinfo') as (display, auth, hostname):
            result = run(args.game, args.out, display, auth, args.timeout, args.seed,
                         fighter_count=args.fighter_count, boarding_transport=args.boarding_transport, display_hostname=hostname)
    print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
