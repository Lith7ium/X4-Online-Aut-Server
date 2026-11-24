#!/usr/bin/env python3
"""Isolated opt-in recovery-to-coordinator experiment; importing starts nothing.

The source is a completed marked-bridge fixture. A private copy is loaded, its
marker and pause are checked in the owned process, then an explicit loopback
activation enables a fresh bridge and coordinator. One synthetic waiting join
causes a fresh marked checkpoint in a separate store; no client is admitted.
This does not prove byte-exact loading, zero advance, persistence of client
gameplay, Windows, internet transport or multiplayer.
"""
import argparse
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tools/dev')]
import authority_recovery_live as recovery
import authority_tcp_recovery_source as tcp_source
import authority_two_x4_live as owned
import authority_world_bridge_live as bridge_install
import authority_load_live as load
import linux_gate_live as live
import linux_snapshot_capture as snapshot
from authority.checkpoints import CheckpointStore
from authority.recovery import stage_confirmed_recovery
from authority.local_control_bridge import LocalControlBridge
from authority.world import WorldCoordinator
from authority_control_cycle_evidence import read_cycle_evidence
from lockstep.launcher import extension


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('probe hook missing or ambiguous')
    return text.replace(old, new)


def install_probe(case, profile, port, lease, epoch, *, early_pause=False, marker_timeout=None):
    """Install modified copies only in a new disposable case."""
    if marker_timeout is not None and (type(marker_timeout) is not int or not 1 <= marker_timeout <= 330):
        raise ValueError('invalid recovery marker timeout')
    bridge_install.install_probe(case, port, lease, epoch, marker_required=True)
    lab = case/'extensions/x4_lan_lab'; ui = lab/'ui'
    marker = (ROOT/'tools/dev/authority_checkpoint_marker_probe.lua').read_text()
    stored = """RegisterEvent('X4AuthorityCheckpointMarker.Stored',function(_,marker)
 if phase~='save' or state~='store' or marker~=cfg.authority_marker then fail();return end
 state='done';DebugError('[X4LAN] AUTH_MARKER save STORED '..marker)
end)
"""
    # Load-side inspection never needs a Stored handler. The bridge owns it.
    marker = replace_once(marker, stored, '')
    (ui/'authority_checkpoint_marker_probe.lua').write_text(marker)
    probe = (ROOT/'tools/dev/authority_recovery_probe.lua').read_text()
    probe = replace_once(probe, "SetScript('onUpdate',function()\n",
        "function X4LAN_AUTHORITY_RECOVERY_READY() return state=='done' and not failed end\nfunction X4LAN_AUTHORITY_RECOVERY_UPDATE()\n")
    probe = replace_once(probe, " if not ok then failed=true;log('ERROR') end\nend)\n",
        " if not ok then failed=true;log('ERROR') end\nend\n")
    (ui/'authority_recovery_probe.lua').write_text(probe)
    probe = (ui/'authority_world_bridge_probe.lua').read_text()
    if marker_timeout is not None:
        probe = replace_once(probe, '   marker_required=cfg.authority_marker_required,\n',
                             '   marker_required=cfg.authority_marker_required,\n'
                             f'   marker_timeout_seconds={marker_timeout},\n')
    probe = replace_once(probe, "SetScript('onUpdate',function()\n if stopped then return end\n",
        "SetScript('onUpdate',function()\n if stopped then return end\n X4LAN_AUTHORITY_RECOVERY_UPDATE()\n")
    probe = replace_once(probe, 'local started,bridge,net,engine,next_hello,loaded_at=false,nil,nil,nil,0,nil\n',
        'local started,bridge,net,engine,next_hello,loaded_at=false,nil,nil,nil,0,nil\nlocal activated=false\n')
    probe = replace_once(probe, ' if not C.IsPlayerValid() then return end\n',
        " if not X4LAN_AUTHORITY_RECOVERY_READY() then return end\n if not C.IsPlayerValid() then error('recovered player lost') end\n")
    probe = replace_once(probe, 'bool IsPlayerValid(void);bool IsSaveListLoadingComplete(void);',
        'bool IsPlayerValid(void);bool IsGamePaused(void);bool IsSaveListLoadingComplete(void);')
    gate = """ if not activated then
  assert(C.IsGamePaused(),'recovered world lost pause before activation')
  if now>=next_hello then
   assert(net:send('RECOVERY_READY|'..cfg.authority_control_lease..'|'..cfg.authority_world_epoch)==true)
   next_hello=now+.25
  end
  local packet=net:receive()
  if packet then
   assert(packet=='ACTIVATE|'..cfg.authority_control_lease..'|'..cfg.authority_world_epoch,'invalid activation')
   activated=true
   assert(net:send('ACTIVATED|'..cfg.authority_control_lease..'|'..cfg.authority_world_epoch)==true)
  end
  return
 end
"""
    probe = replace_once(probe, ' if not bridge then\n', gate+' if not bridge then\n')
    # Recovery has its own READY/ACTIVATED handshake. Afterwards the peer is
    # already known, so periodic HELLO traffic serves no purpose and can fill
    # the supervisor's UDP receive queue during synchronous save verification.
    # Keep the ordinary, non-recovery world-bridge probe unchanged.
    probe = replace_once(probe, """ if now>=next_hello then
  assert(net:send('HELLO|'..cfg.authority_control_lease..'|'..cfg.authority_world_epoch)==true)
  next_hello=now+.25
 end
""", '')
    (ui/'authority_world_bridge_probe.lua').write_text(probe)
    files = [*extension.BASE_UI, 'ui/authority_checkpoint_marker_probe.lua', 'ui/authority_recovery_probe.lua',
             'ui/authority_control_state.lua', 'ui/x4_control_engine.lua', 'ui/local_control_bridge.lua',
             'ui/authority_world_bridge_probe.lua']
    (lab/'ui.xml').write_text(extension.ui_xml(files))
    config = ui/'config.lua'
    pause_setting = 'authority_recovery_early_pause=true,' if early_pause else ''
    config.write_text('X4LAN_CONFIG = {enabled=false,test_profile="45841",authority_recovery_probe=true,'
        'authority_marker_phase="load",authority_load_save_path='+load.lua_literal(str(profile/'save'))+','
        f'authority_world_bridge=true,authority_marker_required=true,{pause_setting}'
        f'authority_control_port={port},authority_control_lease="{lease}",authority_world_epoch="{epoch}"'+'}\n', encoding='ascii')
    return [config, lab/'ui.xml', profile/'content.xml', profile/'config.xml', lab/'native/x4lan_net.so',
            lab/'md/authority_checkpoint_marker.xml', *[ui/name for name in
            ('sha256.lua','authority_checkpoint_marker_probe.lua','authority_recovery_probe.lua','authority_control_state.lua',
             'x4_control_engine.lua','local_control_bridge.lua','authority_world_bridge_probe.lua')]]


def activate(sock, watch, lease, epoch, deadline, check, verify):
    """Only signal the peer after ordered host evidence and a live-context check."""
    ready = f'RECOVERY_READY|{lease}|{epoch}'.encode()
    activate_packet = f'ACTIVATE|{lease}|{epoch}'.encode()
    activated = f'ACTIVATED|{lease}|{epoch}'.encode()
    peer = None
    while peer is None:
        check()
        if time.monotonic() >= deadline: raise TimeoutError('recovery activation timed out')
        watch.poll()
        try: data, address = sock.recvfrom(769)
        except socket.timeout: continue
        if data != ready or address[0] != '127.0.0.1':
            raise ValueError('unexpected activation peer or message')
        # The game may have flushed its UDP READY before the log reader saw
        # the final pause line. Never activate early; wait for another READY.
        if not watch.poll() or watch.stage != 3:
            continue
        peer = address
    check()
    if not watch.poll() or watch.stage != 3: raise ValueError('recovery receipt changed')
    verify()
    if sock.sendto(activate_packet, peer) != len(activate_packet): raise RuntimeError('activation send failed')
    while True:
        check()
        if time.monotonic() >= deadline: raise TimeoutError('activation acknowledgement timed out')
        try: data, address = sock.recvfrom(769)
        except socket.timeout:
            watch.poll()
            continue
        if address != peer: raise ValueError('activation peer changed')
        if data == ready: continue
        if data != activated: raise ValueError('unexpected activation acknowledgement')
        check()
        if not watch.poll() or watch.stage != 3: raise ValueError('recovery receipt changed')
        verify()
        return peer


class NoTransfers:
    def invalidate(self): pass


def verify_selected_source(source_kind, store_dir, checkpoint, profile):
    """Preserve the original verifier unless the fixed TCP source is requested."""
    if source_kind == 'marked-bridge':
        recovery.verify_source(store_dir, checkpoint, profile)
        return None
    if source_kind == 'tcp-last-disconnect':
        return tcp_source.verify(store_dir, checkpoint, profile)
    raise ValueError('unsupported recovery source type')


def install_saved_identity_fixture(case, data):
    """Copy only verified source bytes into the disposable protected overlay."""
    if type(data) is not bytes or not data or len(data) > 65536:
        raise ValueError('invalid saved identity fixture')
    target = case/'extensions/x4_lan_lab/md/authority_identity_fixture.xml'
    fd = os.open(target, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    return target


def acquire_lock_with_handlers(handlers, lockbox, uid, gid):
    """Record the lock before delivering signals; caller owns cleanup in finally."""
    signals=(signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    for number in signals:
        handlers[number] = signal.signal(number, snapshot.cancel_capture)
    deferred=[None]; previous={}
    def remember(number, _frame): deferred[0]=deferred[0] or number
    try:
        for number in signals: previous[number]=signal.signal(number, remember)
        fd, info = live.create_lock_target(snapshot.LOCK_TARGET, uid, gid)
        lockbox.append((fd,info))
    finally:
        for number, handler in previous.items(): signal.signal(number,handler)
    if deferred[0] is not None: raise InterruptedError('signal '+str(deferred[0]))


def stop_owned_processes(processes, errors):
    """Return survivor PIDs, or None if any launcher's cleanup is uncertain."""
    survivors = []
    cleanup_uncertain = False
    for process, started in reversed(processes):
        try:
            if started is None:
                cleanup_uncertain = True
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
                errors.append('launcher identity unavailable')
                continue
            remaining = snapshot.stop_owned(process, started)
            if not isinstance(remaining, list) or any(type(pid) is not int or pid <= 0 for pid in remaining):
                raise ValueError('invalid owned process cleanup result')
            survivors.extend(remaining)
        except BaseException as error:
            cleanup_uncertain = True
            errors.append('owned cleanup: '+type(error).__name__)
    if survivors:
        errors.append('owned processes remain')
    return None if cleanup_uncertain else survivors


def redact_stopped_config(config, remaining):
    """Redact an existing private config only after every owned process stopped."""
    if not config.is_file():
        return
    if remaining != []:
        raise RuntimeError('owned X4 stop unconfirmed; config retained')
    text = config.read_text(encoding='ascii')
    redacted, count = re.subn(r'authority_control_lease="[0-9a-f]{32}"',
                              'authority_control_lease="redacted"', text)
    if count != 1:
        raise ValueError('invalid lease')
    config.write_text(redacted, encoding='ascii')


def run(args):
    if sys.platform != 'linux' or os.geteuid() == 0: raise ValueError('unprivileged Linux user required')
    out = snapshot.output_path(args.out)
    store_dir = load.local_directory(args.store)
    source_store = CheckpointStore(store_dir)
    out_resolved = out.resolve()
    source_experiment = source_store.directory.parent
    if out_resolved.is_relative_to(source_experiment) or source_experiment.is_relative_to(out_resolved):
        raise ValueError('output and source experiment must be separate')
    game = args.game.resolve(strict=True)
    if live.sha(game/'X4') != live.EXE_SHA or not (game/'extensions').is_dir() or not shutil.which('bwrap'):
        raise ValueError('pinned X4 or bubblewrap unavailable')
    if live.x4_executables(): raise ValueError('another X4 process is running')
    if not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', args.display): raise ValueError('local X11 display required')
    auth = Path(os.environ.get('XAUTHORITY',''))
    if not auth.is_file() or not os.access(auth, os.R_OK): raise ValueError('readable XAUTHORITY required')
    auth = auth.resolve(strict=True)
    if not math.isfinite(args.timeout) or args.timeout <= 0 or not 0 <= args.seed <= 0xffffffff:
        raise ValueError('invalid timeout or seed')
    user = pwd.getpwuid(os.geteuid()); home = Path(user.pw_dir)
    steam = live.steam_identity(home, user.pw_uid)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = {'success':False, 'scope':'one isolated Linux X4 recovery and new server checkpoint; synthetic waiting join; no client or gameplay'}
    processes=[]; handlers={}; lockbox=[]; sock=None
    watch=target=checkpoint=expected=None
    try:
        case, profile, binds, lock = snapshot.prepare_case(out, game, home)
        stage = out/'staged-profile'; stage.mkdir(mode=0o700)
        checkpoint, staged = stage_confirmed_recovery(source_store, stage)
        source_kind = getattr(args, 'source_type', 'marked-bridge')
        fixture = verify_selected_source(source_kind, store_dir, checkpoint, profile)
        target = profile/'save/save_008.xml.gz'
        if any((profile/'save').iterdir()): raise ValueError('new profile save directory not empty')
        os.link(staged, target, follow_symlinks=False); target.chmod(0o400)
        if live.sha(target) != checkpoint.sha256: raise ValueError('staged checkpoint changed')
        sock = socket.socket(socket.AF_INET,socket.SOCK_DGRAM); sock.bind(('127.0.0.1',0)); sock.settimeout(.25)
        lease, epoch = secrets.token_hex(16), secrets.token_hex(16)
        protected = install_probe(case, profile, sock.getsockname()[1], lease, epoch)
        if fixture is not None:
            protected.append(install_saved_identity_fixture(case, fixture))
        expected = {path:live.sha(path) for path in protected}
        store_path = out/'checkpoints'; store_path.mkdir(mode=0o700)
        store = CheckpointStore(store_path)
        acquire_lock_with_handlers(handlers,lockbox,user.pw_uid,user.pw_gid)
        deadline = time.monotonic()+args.timeout
        command = load.readonly_save_command(case,game,binds,lock,args.display,auth,user.pw_name,args.seed,target)
        pos = command.index('--die-with-parent')
        for path in protected:
            command[pos:pos] = ['--ro-bind',str(path),str(path)]; pos += 3
        proc, started = owned.spawn_owned_gated(command,out/'process.log',processes)
        identity = None
        watch = recovery.RecoveryWatch(profile/'engine-output.log',checkpoint.marker)
        def check():
            nonlocal identity
            if time.monotonic() >= deadline: raise TimeoutError('experiment timed out')
            actual = owned.client.game_identity(proc,started,game)
            if actual is not None:
                if identity is not None and actual != identity: raise RuntimeError('owned X4 changed')
                identity = actual
            elif identity is not None: raise RuntimeError('owned X4 disappeared')
            if live.steam_identity(home,user.pw_uid) != steam: raise RuntimeError('Steam identity changed')
            if watch.stage and identity is None: raise RuntimeError('recovery evidence without owned X4')
        def active_check():
            check()
            if identity is None: raise RuntimeError('recovery evidence without owned X4')
            if live.sha(target) != checkpoint.sha256 or any(live.sha(p) != h for p,h in expected.items()):
                raise ValueError('staged checkpoint or protected configuration changed')
            check()
        peer = activate(sock, watch, lease, epoch, deadline, check, active_check)
        adapter = LocalControlBridge(enabled=True,sock=sock,peer=peer,lease=lease,world_epoch=epoch,
            profile=profile,store=store,verify_checkpoint=read_cycle_evidence,timeout=130,marker_required=True)
        world = WorldCoordinator(epoch,adapter,store,NoTransfers())
        world.join('synthetic-waiter')
        for operation in ('pause','save'):
            # The synchronous bridge can block for its full configured timeout.
            active_check()
            if deadline-time.monotonic() <= 135: raise TimeoutError('insufficient bridge deadline reserve')
            if world.session.pending is None or world.session.pending.operation != operation:
                raise RuntimeError('unexpected coordinator operation')
            world.step(); active_check()
        fresh = store.latest_marked_confirmed()
        if fresh.checkpoint_id == checkpoint.checkpoint_id or fresh.marker == checkpoint.marker:
            raise ValueError('fresh checkpoint not distinct')
        if world.session.state != 'paused' or world.session.ready_players:
            raise ValueError('unexpected coordinator state')
        if not watch.poll() or watch.stage != 3: raise ValueError('recovery evidence changed')
        result.update(success=True,source_checkpoint_id=checkpoint.checkpoint_id,source_sha256=checkpoint.sha256,
            fresh_checkpoint_id=fresh.checkpoint_id,fresh_sha256=fresh.sha256,
            load_marker_observed=True,pause_observed=True,bridge_activated=True,coordinator_paused=True,
            exact_loaded_file_proven=False,zero_advance_proven=False,client_admitted=False,gameplay_tested=False)
        if source_kind == 'tcp-last-disconnect':
            result.update(source_type=source_kind, source_client_disconnect_simulated=True,
                          saved_identity_fixture_preserved=True, identity_fixture_runtime_proven=False)
    except BaseException as error:
        result.update(success=False,error=str(error) or type(error).__name__)
        if not isinstance(error,(Exception,KeyboardInterrupt)): raise
    finally:
        errors=[]
        for number in handlers:
            try: signal.signal(number,signal.SIG_IGN)
            except BaseException as error: errors.append('cannot fence signals: '+type(error).__name__)
        if sock is not None:
            try: sock.close()
            except BaseException as error: errors.append('socket cleanup: '+type(error).__name__)
        remaining=stop_owned_processes(processes,errors)
        result['owned_processes_remaining']=remaining
        if result['success'] and remaining == []:
            try:
                while True:
                    offset=watch.offset
                    if watch.poll(): break
                    if watch.offset==offset: raise ValueError('incomplete recovery receipt')
                if (watch.stage!=3 or live.sha(target)!=checkpoint.sha256 or
                        any(live.sha(path)!=digest for path,digest in expected.items())):
                    raise ValueError('final evidence changed')
            except BaseException as error:
                result.update(success=False,error='final recovery evidence invalid: '+str(error))
        if lockbox:
            lock_fd, lock_stat = lockbox[0]
            if remaining is None or remaining:
                errors.append('lock retained: owned process stop unconfirmed')
                try: os.close(lock_fd)
                except BaseException as error: errors.append('lock descriptor: '+type(error).__name__)
            else:
                try: live.release_lock_target(snapshot.LOCK_TARGET,lock_fd,lock_stat)
                except BaseException as error: errors.append('lock cleanup: '+type(error).__name__)
        try:
            if live.steam_identity(home,user.pw_uid) != steam: errors.append('Steam identity changed')
        except BaseException as error: errors.append('Steam identity unavailable: '+type(error).__name__)
        config=out/'case/extensions/x4_lan_lab/ui/config.lua'
        try:
            redact_stopped_config(config,remaining)
        except BaseException as error: errors.append('lease cleanup: '+type(error).__name__)
        if errors: result.update(success=False,cleanup_errors=errors)
        try: (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        finally:
            for number,handler in handlers.items():
                try: signal.signal(number,handler)
                except BaseException: pass
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--game',type=Path,required=True); p.add_argument('--store',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True); p.add_argument('--display',required=True)
    p.add_argument('--timeout',type=float,default=600); p.add_argument('--seed',type=int,default=276944566)
    p.add_argument('--source-type',choices=('marked-bridge','tcp-last-disconnect'),default='marked-bridge')
    try: result=run(p.parse_args())
    except (OSError,ValueError,RuntimeError) as error: p.exit(1,'REFUSED: '+str(error)+'\n')
    print(json.dumps(result,indent=2)); return 0 if result['success'] else 1

if __name__ == '__main__': sys.exit(main())
