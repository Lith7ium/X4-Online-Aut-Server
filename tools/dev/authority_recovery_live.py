#!/usr/bin/env python3
"""One opt-in isolated Linux X4 restart observation; no clients or admission.

Copies the last marked CONFIRMED save to a new disposable profile, starts one
owned X4 process, observes its MD load marker and pause, then stops the process.
The marker is evidence of saved MD state after X4's load event, not proof of all
loaded bytes or zero simulation before pause. Importing this file starts nothing.
"""
import argparse
import json
import math
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools/dev')]
from authority.checkpoints import CheckpointStore
from authority.recovery import stage_confirmed_recovery
from lockstep.launcher import extension
import authority_download_load_live as prior
import authority_load_live as base
import linux_gate_live as live
import linux_snapshot_capture as snapshot

RECOVERY=b'[X4LAN] AUTH_RECOVERY '
MARKER=b'[X4LAN] AUTH_MARKER load '
WORLD_BRIDGE_ERROR = b'[X4LAN] AUTH_WORLD_BRIDGE ERROR '
REGISTRY_ERROR = re.compile(rb'\[X4LAN\] AUTH_REGISTRY_CHECKPOINT [0-9a-f]{32}\|ERROR\|')
# Optional hooks for explicitly isolated recovery experiments. The invoking
# runner must restore them and provide only files in its own disposable case.
extra_protected_files=None
result_finalizer=None
observation_callback=None


class RecoveryWatch(base.LoadWatch):
    """Require one ordered request, matching marker and pause in the same log."""
    def __init__(self,path,marker):
        super().__init__(path)
        if not isinstance(marker,str) or not re.fullmatch(r'[0-9a-f]{32}',marker):
            raise ValueError('invalid expected marker')
        self.expected=(b'REQUEST', b'INSPECTED '+marker.encode('ascii'), b'PAUSED')

    def poll(self):
        try: fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        except FileNotFoundError:
            if self.identity is None: return False
            raise ValueError('recovery log disappeared') from None
        try:
            info=os.fstat(fd);identity=(info.st_dev,info.st_ino)
            if not stat.S_ISREG(info.st_mode) or info.st_size<self.offset or info.st_size>self.MAX_LOG:
                raise ValueError('recovery log changed or exceeded limit')
            if self.identity is not None and self.identity!=identity: raise ValueError('recovery log replaced')
            self.identity=identity;os.lseek(fd,self.offset,os.SEEK_SET)
            for _ in range(4):
                chunk=os.read(fd,1<<20)
                if not chunk: break
                self.offset+=len(chunk);lines=(self.carry+chunk).split(b'\n');self.carry=lines.pop()
                if len(self.carry)>self.MAX_LINE: raise ValueError('recovery line too long')
                for line in lines:
                    if len(line)>self.MAX_LINE: raise ValueError('recovery line too long')
                    if WORLD_BRIDGE_ERROR in line:
                        raise ValueError('X4 reported a world bridge failure during recovery')
                    if REGISTRY_ERROR.search(line):
                        raise ValueError('X4 reported a registry checkpoint failure during recovery')
                    if RECOVERY in line: row=line.split(RECOVERY,1)[1].strip()
                    elif MARKER in line: row=line.split(MARKER,1)[1].strip()
                    else: continue
                    if self.stage>=3 or row!=self.expected[self.stage]:
                        raise ValueError('unexpected, duplicate, mismatched or out-of-order recovery receipt')
                    self.stage+=1
            return self.offset==os.fstat(fd).st_size and not self.carry
        finally: os.close(fd)


def install_probe(case,profile):
    lab=case/'extensions/x4_lan_lab';ui=lab/'ui'
    if lab.is_symlink() or not lab.is_dir(): raise ValueError('private extension missing')
    (ui/'native_gate_snapshot.lua').unlink();(lab/'md/native_gate_battle.xml').unlink()
    for source,target in ((ROOT/'src/authority/md/checkpoint_marker.xml',lab/'md/authority_checkpoint_marker.xml'),
                          (ROOT/'tools/dev/authority_checkpoint_marker_probe.lua',ui/'authority_checkpoint_marker_probe.lua'),
                          (ROOT/'tools/dev/authority_recovery_probe.lua',ui/'authority_recovery_probe.lua')):
        shutil.copyfile(source,target)
    files=[*extension.BASE_UI,'ui/authority_checkpoint_marker_probe.lua','ui/authority_recovery_probe.lua']
    (lab/'ui.xml').write_text(extension.ui_xml(files),encoding='utf-8')
    config=ui/'config.lua';config.chmod(0o600)
    config.write_text('X4LAN_CONFIG = {enabled=false,test_profile="45841",authority_recovery_probe=true,'
      'authority_marker_phase="load",authority_load_save_path='+base.lua_literal(str(profile/'save'))+'}\n',encoding='ascii')
    return config


def verify_source(store_dir,checkpoint,profile):
    """Bind this diagnostic to a completed marked bridge experiment's output."""
    if store_dir.name!='checkpoints': raise ValueError('unsupported source store')
    origin=store_dir.parent
    result_path=origin/'result.json'
    if result_path.is_symlink() or not result_path.is_file() or result_path.stat().st_size>65536:
        raise ValueError('source result unavailable')
    result=json.loads(result_path.read_text(encoding='utf-8'))
    if (not isinstance(result,dict) or result.get('success') is not True or
        result.get('marked_saves_checked') is not True or result.get('owned_processes_remaining') != []):
        raise ValueError('source marked bridge did not finish cleanly')
    last=result.get('lifecycle',{}).get('last_departure_checkpoint',{})
    if last.get('checkpoint_id')!=checkpoint.checkpoint_id or last.get('sha256')!=checkpoint.sha256:
        raise ValueError('CONFIRMED differs from last-departure checkpoint')
    source_profile=origin/'case/home/.config/EgoSoft/X4/45841'
    for source,target in ((source_profile/'content.xml',profile/'content.xml'),
                          (origin/'case/extensions/x4_lan_lab/md/authority_checkpoint_marker.xml',ROOT/'src/authority/md/checkpoint_marker.xml')):
        if source.is_symlink() or not source.is_file() or source.stat().st_size>65536 or source.read_bytes()!=target.read_bytes():
            raise ValueError('source and recovery extension configuration differs')


def stop_recovery_process(proc, started, launch_attempted, *, owned_launch=None, result=None):
    """Return (survivors, error); None means that process cleanup is unconfirmed.

    This older runner starts the launcher without an exec gate. If its start
    identity could not be recorded, stopping the launcher does not establish
    that it never spawned X4 or that all descendants have stopped.
    """
    if proc is None:
        if launch_attempted:
            return None, 'launcher start outcome unconfirmed'
        return [], None
    if started is None:
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)
        except BaseException:
            return None, 'launcher cleanup failed; process identity unavailable'
        return None, 'owned process identity unavailable'
    try:
        remaining = snapshot.stop_owned(proc, started)
        if not isinstance(remaining, list) or any(type(pid) is not int or pid <= 0 for pid in remaining):
            raise ValueError('invalid owned cleanup result')
    except BaseException:
        return None, 'process cleanup failed'
    if remaining and owned_launch is not None:
        try:
            cleanup = live.stop_owned_game_descendants(owned_launch)
            if result is not None:
                result['owned_descendant_cleanup'] = cleanup
            remaining = live.wait_owned_groups({proc.pid: started}).get(proc.pid, [])
            if cleanup['refused']:
                return remaining, 'owned descendant identity refused'
        except (OSError, RuntimeError, ValueError) as error:
            return None, 'owned descendant cleanup failed: ' + str(error)
    if remaining:
        return remaining, 'owned processes remain'
    return remaining, None


def run(args):
    if sys.platform!='linux' or os.geteuid()==0: raise ValueError('unprivileged Linux user required')
    out=snapshot.output_path(args.out);store_dir=base.local_directory(args.store);store=CheckpointStore(store_dir)
    game=args.game.resolve(strict=True)
    if live.sha(game/'X4')!=live.EXE_SHA or not (game/'extensions').is_dir() or not shutil.which('bwrap'):
        raise ValueError('pinned X4 or bubblewrap unavailable')
    if live.x4_executables(): raise ValueError('another X4 process is running')
    if not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?',args.display): raise ValueError('local X11 display required')
    auth=Path(os.environ.get('XAUTHORITY',''))
    if not auth.is_file() or not os.access(auth,os.R_OK): raise ValueError('readable XAUTHORITY required')
    auth=auth.resolve(strict=True)
    if not math.isfinite(args.timeout) or args.timeout<=0 or not 0<=args.seed<=0xffffffff: raise ValueError('invalid timeout or seed')
    user=pwd.getpwuid(os.geteuid());home=Path(user.pw_dir);steam=live.steam_identity(home,user.pw_uid)
    out.mkdir(mode=0o700,parents=True,exist_ok=False)
    result={'success':False,'scope':'one isolated Linux X4 restart; no client, admission, multiplayer or gameplay'}
    proc=started=lock_fd=lock_stat=owned_launch=None;handlers={};launch_attempted=False
    try:
        case,profile,binds,lock=snapshot.prepare_case(out,game,home)
        stage=out/'staged-profile';stage.mkdir(mode=0o700)
        checkpoint,staged=stage_confirmed_recovery(store,stage)
        verify_source(store_dir,checkpoint,profile)
        target=profile/'save'/'save_008.xml.gz'
        if any(p.name!='steam_autocloud.vdf' for p in target.parent.iterdir()):
            raise ValueError('new isolated save directory is not empty')
        os.link(staged,target,follow_symlinks=False);target.chmod(0o400)
        if live.sha(target)!=checkpoint.sha256: raise ValueError('staged checkpoint changed')
        config=install_probe(case,profile);lab=case/'extensions/x4_lan_lab'
        protected=[config,lab/'ui.xml',profile/'content.xml',profile/'config.xml',
                   lab/'md/authority_checkpoint_marker.xml',lab/'ui/authority_checkpoint_marker_probe.lua',
                   lab/'ui/authority_recovery_probe.lua']
        if extra_protected_files is not None:
            if not callable(extra_protected_files): raise ValueError('invalid recovery protection hook')
            additions=extra_protected_files(case,profile)
            if not isinstance(additions,list): raise ValueError('invalid recovery protection list')
            for path in additions:
                if not isinstance(path,Path) or not path.is_relative_to(case):
                    raise ValueError('recovery protected file outside private case')
                if path not in protected: protected.append(path)
        expected={path:live.sha(path) for path in protected}
        lock_fd,lock_stat=live.create_lock_target(snapshot.LOCK_TARGET,user.pw_uid,user.pw_gid)
        for number in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP): handlers[number]=signal.signal(number,snapshot.cancel_capture)
        command=base.readonly_save_command(case,game,binds,lock,args.display,auth,user.pw_name,args.seed,target)
        pos=command.index('--die-with-parent')
        for path in protected:
            command[pos:pos]=['--ro-bind',str(path),str(path)];pos+=3
        with (out/'process.log').open('wb') as log:
            # A cancellation during Popen must not hide a child before its
            # process handle and start identity can be recorded for cleanup.
            pending_signal = [None]
            previous_handlers = {}
            def defer_signal(number, _frame):
                pending_signal[0] = pending_signal[0] or number
            try:
                for number in handlers:
                    previous_handlers[number] = signal.signal(number, defer_signal)
                launch_attempted=True
                proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                started=live.start_ticks(proc.pid)
                owned_launch=live.record_owned_game(proc,started,case/'home',game/'X4',user.pw_uid)
            finally:
                for number,handler in previous_handlers.items(): signal.signal(number,handler)
            if pending_signal[0] is not None:
                raise InterruptedError('signal '+str(pending_signal[0]))
            deadline=time.monotonic()+args.timeout
            watch=RecoveryWatch(profile/'engine-output.log',checkpoint.marker);seen_game=None
            while True:
                if time.monotonic()>=deadline: raise TimeoutError('recovery observation timed out')
                identity=prior.game_identity(proc,started,game)
                if identity is not None:
                    if seen_game is not None and identity!=seen_game: raise RuntimeError('X4 process changed')
                    seen_game=identity
                elif seen_game is not None: raise RuntimeError('X4 process disappeared')
                if any(live.sha(path)!=digest for path,digest in expected.items()): raise ValueError('protected configuration changed')
                if live.steam_identity(home,user.pw_uid)!=steam: raise RuntimeError('Steam identity changed')
                caught_up=watch.poll()
                if watch.stage and seen_game is None: raise RuntimeError('receipt without owned X4 process')
                if caught_up and watch.stage==3:
                    if live.sha(target)!=checkpoint.sha256: raise ValueError('staged checkpoint changed')
                    if observation_callback is not None:
                        if not callable(observation_callback): raise ValueError('invalid observation hook')
                        def guard():
                            if time.monotonic()>=deadline: raise TimeoutError('recovery observation timed out')
                            if prior.game_identity(proc,started,game)!=seen_game or seen_game is None:
                                raise RuntimeError('owned X4 process changed')
                            if live.steam_identity(home,user.pw_uid)!=steam: raise RuntimeError('Steam identity changed')
                            if any(live.sha(path)!=digest for path,digest in expected.items()):
                                raise ValueError('protected configuration changed')
                        guard()
                        experiment=observation_callback(profile=profile,out=out,game=game,
                                                        identity=seen_game,deadline=deadline,guard=guard)
                        guard()
                        if type(experiment) is not dict: raise ValueError('invalid observation result')
                        result['experiment']=experiment
                    result.update(success=True,checkpoint_id=checkpoint.checkpoint_id,sha256=checkpoint.sha256,
                                  load_requested=True,marker_after_game_loaded=True,pause_observed=True,
                                  exact_loaded_file_proven=False,zero_advance_proven=False,admitted=False)
                    break
                time.sleep(.1)
    except (Exception,KeyboardInterrupt) as error: result['error']=str(error)
    finally:
        cleanup_errors=[]
        for number in handlers:
            try: signal.signal(number, signal.SIG_IGN)
            except BaseException: cleanup_errors.append('cannot fence cleanup signals')
        remaining, process_error = stop_recovery_process(
            proc, started, launch_attempted, owned_launch=owned_launch, result=result)
        if proc is not None and started is not None and owned_launch is None:
            cleanup_errors.append('owned game launch identity unavailable')
        result['owned_processes_remaining'] = remaining
        if process_error is not None: cleanup_errors.append(process_error)
        if cleanup_errors: result['success'] = False
        if result['success']:
            try:
                while True:
                    old=watch.offset
                    if watch.poll(): break
                    if old==watch.offset: raise ValueError('incomplete receipt')
                if (watch.stage!=3 or live.sha(target)!=checkpoint.sha256 or
                    any(live.sha(path)!=digest for path,digest in expected.items())): raise ValueError('final state invalid')
            except (OSError,ValueError): result.update(success=False,error='final receipt or staged state invalid')
        if lock_stat is not None:
            if remaining == [] and not cleanup_errors:
                try: live.release_lock_target(snapshot.LOCK_TARGET,lock_fd,lock_stat)
                except BaseException: cleanup_errors.append('lock cleanup failed')
            else:
                cleanup_errors.append('lock retained: owned process stop unconfirmed')
                try: os.close(lock_fd)
                except BaseException: cleanup_errors.append('lock descriptor close failed')
        if cleanup_errors: result.update(success=False,cleanup_error='; '.join(cleanup_errors))
        for number,handler in handlers.items():
            try: signal.signal(number,handler)
            except BaseException:
                result.update(success=False,cleanup_error='; '.join(filter(None,(result.get('cleanup_error'),'signal restoration failed'))))
        try:
            if live.steam_identity(home,user.pw_uid)!=steam: result.update(success=False,error='Steam identity changed')
        except (OSError,ValueError,RuntimeError): result.update(success=False,error='Steam identity unavailable')
        if result_finalizer is not None:
            was_success=result.get('success') is True
            try:
                result_finalizer(result)
                if type(result.get('success')) is not bool or (not was_success and result['success']):
                    raise ValueError('recovery finalizer changed failure to success')
            except BaseException as error:
                result.update(success=False,error='final result check: '+(str(error) or type(error).__name__))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--game',type=Path,required=True);p.add_argument('--store',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--display',required=True)
    p.add_argument('--timeout',type=float,default=600);p.add_argument('--seed',type=int,default=276944566)
    try: result=run(p.parse_args())
    except (OSError,ValueError,RuntimeError) as error: p.exit(1,'REFUSED: '+str(error)+'\n')
    print(json.dumps(result,indent=2));return 0 if result['success'] else 1

if __name__=='__main__': sys.exit(main())
