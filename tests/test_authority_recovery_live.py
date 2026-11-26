"""Synthetic checks for the restart harness; no X4 process is started."""
import gzip
import json
import os
from contextlib import ExitStack
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools/dev')]
from authority.checkpoints import CheckpointStore
import authority_recovery_live as recovery


class RecoveryLiveTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("luajit"), "LuaJIT required")
    def test_probe_reload_waits_for_marker_before_pause(self):
        subprocess.run(["luajit", "tests/authority_recovery_probe.lua"], cwd=ROOT, check=True)

    def test_receipts_require_request_matching_marker_and_pause_in_order(self):
        marker='a'*32
        rows=[b'[X4LAN] AUTH_RECOVERY REQUEST\n',
              b'[X4LAN] AUTH_MARKER load INSPECTED '+marker.encode()+b'\n',
              b'[X4LAN] AUTH_RECOVERY PAUSED\n']
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log';watch=recovery.RecoveryWatch(path,marker)
            path.write_bytes(rows[0]);self.assertTrue(watch.poll());self.assertEqual(watch.stage,1)
            with path.open('ab') as out: out.write(rows[1]+rows[2])
            self.assertTrue(watch.poll());self.assertEqual(watch.stage,3)
            with path.open('ab') as out: out.write(rows[2])
            with self.assertRaises(ValueError): watch.poll()
            for bad in ([rows[1],rows[0],rows[2]], [rows[0],rows[1].replace(b'a'*32,b'b'*32),rows[2]],
                        [rows[0],b'[X4LAN] AUTH_MARKER load ERROR\n']):
                path.write_bytes(b''.join(bad));watch=recovery.RecoveryWatch(path,marker)
                with self.assertRaises(ValueError): watch.poll()

    def test_terminal_game_errors_end_the_recovery_wait_on_a_complete_log_line(self):
        failures = {
            b'[X4LAN] AUTH_WORLD_BRIDGE ERROR update stopped': 'world bridge failure',
            b'[X4LAN] AUTH_REGISTRY_CHECKPOINT ' + b'b' * 32 + b'|ERROR|inspection failed':
                'registry checkpoint failure',
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'engine-output.log'
            for message, expected_error in failures.items():
                with self.subTest(message=message):
                    watch = recovery.RecoveryWatch(path, 'a' * 32)
                    path.write_bytes(b'[X4LAN] AUTH_RECOVERY REQUEST\n' + message[:18])
                    self.assertFalse(watch.poll())
                    with path.open('ab') as output:
                        output.write(message[18:] + b'\n')
                    with self.assertRaisesRegex(ValueError, expected_error):
                        watch.poll()

    def test_log_may_be_missing_only_before_first_observation(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log';watch=recovery.RecoveryWatch(path,'a'*32)
            self.assertFalse(watch.poll())
            path.write_bytes(b'[X4LAN] AUTH_RECOVERY REQUEST\n')
            self.assertTrue(watch.poll())
            path.unlink()
            with self.assertRaisesRegex(ValueError,'log disappeared'): watch.poll()

    def test_observed_log_replacement_and_truncation_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log';watch=recovery.RecoveryWatch(path,'a'*32)
            path.write_bytes(b'[X4LAN] AUTH_RECOVERY REQUEST\n')
            self.assertTrue(watch.poll())
            path.write_bytes(b'')
            with self.assertRaisesRegex(ValueError,'log changed'): watch.poll()
            path.write_bytes(b'[X4LAN] AUTH_RECOVERY REQUEST\n')
            replacement=Path(d)/'replacement';replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
            with self.assertRaisesRegex(ValueError,'log replaced'): watch.poll()

    def test_cleanup_keeps_lock_when_owned_process_stop_is_unconfirmed(self):
        for mode in ('not_started','stopped','survives','raises','invalid','unknown_identity','interrupted_spawn','spawn_raises','observation_timeout'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                root=Path(d);game=root/'game';(game/'extensions').mkdir(parents=True);(game/'X4').touch()
                auth=root/'auth';auth.touch();out=root/'output';profile=root/'profile';(profile/'save').mkdir(parents=True)
                source=root/'source';source.write_bytes(b'saved bytes');lock=root/'lock'
                checkpoint=SimpleNamespace(checkpoint_id='checkpoint-'+'b'*32,marker='a'*32,sha256='expected')
                args=SimpleNamespace(out=out,store=root/'store',game=game,display=':0',timeout=5,seed=1)
                user=SimpleNamespace(pw_dir=str(root),pw_uid=1000,pw_gid=1000,pw_name='tester')
                proc=Mock(pid=123);proc.poll.return_value=None
                with ExitStack() as stack:
                    def replace(obj,name,value): return stack.enter_context(patch.object(obj,name,value))
                    stack.enter_context(patch.dict(os.environ,{'XAUTHORITY':str(auth)}))
                    replace(recovery.os,'geteuid',lambda:1000)
                    replace(recovery.snapshot,'output_path',lambda _:out)
                    replace(recovery.base,'local_directory',lambda p:p)
                    replace(recovery,'CheckpointStore',lambda _:object())
                    replace(recovery.live,'sha',lambda _: 'expected')
                    replace(recovery.live,'EXE_SHA','expected')
                    replace(recovery.live,'x4_executables',lambda:[])
                    replace(recovery.shutil,'which',lambda _:'/bin/bwrap')
                    replace(recovery.pwd,'getpwuid',lambda _:user)
                    replace(recovery.live,'steam_identity',lambda *_:('stable',))
                    replace(recovery.snapshot,'prepare_case',lambda *_:(root/'case',profile,[],lock))
                    replace(recovery,'stage_confirmed_recovery',lambda *_:(checkpoint,source))
                    replace(recovery,'verify_source',lambda *_:None)
                    replace(recovery,'install_probe',lambda *_:root/'config.lua')
                    def acquire(*_):
                        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
                        return fd,os.fstat(fd)
                    replace(recovery.live,'create_lock_target',acquire)
                    def release(path,fd,info):
                        self.assertEqual((lock.stat().st_dev,lock.stat().st_ino),(info.st_dev,info.st_ino))
                        lock.unlink();os.close(fd)
                    release_mock=replace(recovery.live,'release_lock_target',Mock(side_effect=release))
                    installed_handlers={}
                    def set_signal(number,handler):
                        previous=installed_handlers.get(number,recovery.signal.SIG_DFL)
                        installed_handlers[number]=handler
                        return previous
                    replace(recovery.signal,'signal',set_signal)
                    replace(recovery.base,'readonly_save_command',Mock(side_effect=RuntimeError('no launch')) if mode=='not_started' else lambda *_:['bwrap','--die-with-parent'])
                    def spawn(*_args,**_kwargs):
                        if mode=='spawn_raises': raise OSError('launch outcome unknown')
                        if mode=='interrupted_spawn': installed_handlers[recovery.signal.SIGTERM](recovery.signal.SIGTERM,None)
                        if mode=='observation_timeout':
                            (profile/'engine-output.log').write_text(
                                '[X4LAN] AUTH_RECOVERY REQUEST\n'
                                f'[X4LAN] AUTH_MARKER load INSPECTED {checkpoint.marker}\n'
                                '[X4LAN] AUTH_RECOVERY PAUSED\n')
                        return proc
                    popen=replace(recovery.subprocess,'Popen',Mock(side_effect=spawn))
                    replace(recovery.live,'start_ticks',Mock(side_effect=RuntimeError('no identity')) if mode=='unknown_identity' else lambda _:7)
                    replace(recovery.live,'record_owned_game',Mock(return_value={'leader':123}))
                    replace(recovery.live,'stop_owned_game_descendants',Mock(return_value={'refused':[]}))
                    replace(recovery.live,'wait_owned_groups',Mock(return_value={123:[99]}))
                    if mode=='observation_timeout':
                        def observe(*, guard, **_):
                            guard()
                            raise TimeoutError('optional observation timed out')
                        replace(recovery.prior,'game_identity',lambda *_:(123,7))
                        replace(recovery,'observation_callback',observe)
                    else:
                        replace(recovery.prior,'game_identity',Mock(side_effect=RuntimeError('end observation')))
                    stop=replace(recovery.snapshot,'stop_owned',Mock(side_effect=RuntimeError('uncertain')) if mode=='raises' else Mock(return_value=[99] if mode=='survives' else (None if mode=='invalid' else [])))
                    result=recovery.run(args)
                self.assertFalse(result['success'])
                if mode in ('not_started','stopped','interrupted_spawn','observation_timeout'):
                    release_mock.assert_called_once();self.assertFalse(lock.exists())
                    self.assertEqual(result['owned_processes_remaining'],[])
                else:
                    release_mock.assert_not_called();self.assertTrue(lock.exists())
                    self.assertIn('lock retained',result['cleanup_error'])
                    self.assertEqual(result['owned_processes_remaining'],[99] if mode=='survives' else None)
                if mode=='not_started':popen.assert_not_called()
                if mode=='unknown_identity':
                    stop.assert_not_called();proc.terminate.assert_called_once();proc.wait.assert_called_once()
                if mode=='interrupted_spawn':
                    self.assertEqual(result['error'],'signal '+str(recovery.signal.SIGTERM))
                    stop.assert_called_once_with(proc,7)
                if mode=='observation_timeout':
                    self.assertEqual(result['error'],'optional observation timed out')
                    self.assertEqual(result['checkpoint_id'],checkpoint.checkpoint_id)
                    self.assertEqual(result['sha256'],checkpoint.sha256)
                    for key in ('load_requested','marker_after_game_loaded','pause_observed'):
                        self.assertIs(result[key],True)
                    for key in ('exact_loaded_file_proven','zero_advance_proven','admitted'):
                        self.assertIs(result[key],False)
                    self.assertNotIn('experiment',result)
                    self.assertEqual(json.loads((out/'result.json').read_text()),result)

    def test_stopped_leader_reuses_authenticated_descendant_cleanup(self):
        process = Mock(pid=123)
        launch = {'leader': 123}
        for refused, remaining, expected in (
            ([], [], None),
            ([{'pid': 99}], [], 'owned descendant identity refused'),
            ([], [99], 'owned processes remain'),
        ):
            with self.subTest(refused=refused, remaining=remaining):
                result = {}
                evidence = {'refused': refused, 'processes': [{'pid': 99}]}
                groups = {123: remaining} if remaining else {}
                with (patch.object(recovery.snapshot, 'stop_owned', return_value=[99]),
                      patch.object(recovery.live, 'stop_owned_game_descendants',
                                   return_value=evidence) as cleanup,
                      patch.object(recovery.live, 'wait_owned_groups', return_value=groups)):
                    found = recovery.stop_recovery_process(
                        process, 7, True, owned_launch=launch, result=result)
                cleanup.assert_called_once_with(launch)
                self.assertEqual(found, (remaining, expected))
                self.assertEqual(result['owned_descendant_cleanup'], evidence)

    def test_source_requires_exact_confirmed_last_departure_and_matching_extensions(self):
        with tempfile.TemporaryDirectory() as d:
            origin=Path(d);directory=origin/'checkpoints';directory.mkdir()
            source=origin/'save.gz';source.write_bytes(gzip.compress(b'<savegame/>'))
            store=CheckpointStore(directory);checkpoint=store.publish(source,marker='a'*32);store.confirm(checkpoint)
            source_profile=origin/'case/home/.config/EgoSoft/X4/45841';source_profile.mkdir(parents=True)
            profile=origin/'new-profile';profile.mkdir()
            for folder in (source_profile,profile): (folder/'content.xml').write_text('<content/>')
            md=origin/'case/extensions/x4_lan_lab/md';md.mkdir(parents=True)
            md.joinpath('authority_checkpoint_marker.xml').write_bytes((ROOT/'src/authority/md/checkpoint_marker.xml').read_bytes())
            result={'success':True,'marked_saves_checked':True,'owned_processes_remaining':[],
                    'lifecycle':{'last_departure_checkpoint':{'checkpoint_id':checkpoint.checkpoint_id,'sha256':checkpoint.sha256}}}
            result_path=origin/'result.json';result_path.write_text(json.dumps(result))
            recovery.verify_source(directory,checkpoint,profile)
            result['lifecycle']['last_departure_checkpoint']['sha256']='0'*64
            result_path.write_text(json.dumps(result))
            with self.assertRaises(ValueError): recovery.verify_source(directory,checkpoint,profile)
            result['lifecycle']['last_departure_checkpoint']['sha256']=checkpoint.sha256
            result_path.write_text(json.dumps(result));(profile/'content.xml').write_text('<other/>')
            with self.assertRaises(ValueError): recovery.verify_source(directory,checkpoint,profile)


if __name__=='__main__': unittest.main()
