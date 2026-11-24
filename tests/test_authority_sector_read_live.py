"""Synthetic wiring, signals and harmless exec-gate tests; no X4 process."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, Mock
from contextlib import ExitStack
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('sector_read_live',ROOT/'tools/dev/authority_sector_read_live.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ReadRunner(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.ticket=dict(world_epoch='a'*32,connection_handle=1,baseline_id=1,load_generation=1,
            checkpoint_id=m.archive.CHECKPOINT_ID,sha256=m.archive.SHA256,attempt_id='b'*32,slot='save_009')
        self.loaded='load_requested|'+'|'.join(str(self.ticket[k]) for k in m.load.FIELDS)
        self.paused='pause_observed|'+'|'.join(str(self.ticket[k]) for k in m.load.FIELDS)
        self.rows=[m.load.TAG.decode()+self.loaded,'[X4LAN] AUTH_MARKER load INSPECTED '+m.archive.MARKER,
            '[X4LAN] AUTH_SECTOR_READ DONE slot=1 pilot=present order=Patrol state=started game=100 x=1 y=2 z=3',
            m.load.TAG.decode()+self.paused]
    def tearDown(self): self.tmp.cleanup()
    def log(self,rows):
        p=self.path/'log';p.write_text('\n'.join(rows)+'\n');return p
    def test_receipts_are_ordered_unique_and_bounded(self):
        result=m.check_log(self.log(self.rows),self.ticket,1)
        self.assertEqual(result['reported_position'],[1,2,3]);self.assertEqual(result['reported_order_id'],'Patrol')
        cases=[self.rows+self.rows[-1:],self.rows[:1]+self.rows[2:],self.rows[:2]+[self.rows[1]]+self.rows[2:],
               self.rows[:2]+['[X4LAN] AUTH_SECTOR_READ ERROR fault']+self.rows[2:],
               [self.rows[1],self.rows[0],*self.rows[2:]],
               self.rows[:1]+[self.rows[1].replace(m.archive.MARKER,'f'*32)]+self.rows[2:],
               self.rows[:2]+[self.rows[2].replace('x=1','x=nan')]+self.rows[3:],
               self.rows[:2]+[self.rows[2].replace('slot=1','slot=2')]+self.rows[3:]]
        for rows in cases:
            with self.subTest(rows=rows),self.assertRaises(ValueError): m.check_log(self.log(rows),self.ticket,1)
    def test_fault_scans_only_probe_error_tags(self):
        self.assertFalse(m.fault_log(('\n'.join(self.rows)).encode()))
        for error in ('[X4LAN] AUTH_SECTOR_READ ERROR fault', 'Error in MD cue md.X4AuthoritySectorReadGuard.Read',
                      "Error while executing the Lua script 'extensions/x4_lan_lab/ui/example.lua'"):
            self.assertTrue(m.fault_log(('\n'.join(self.rows+[error])).encode()))
    def test_log_snapshot_continuity(self):
        snapshot=m.log_continuity((1,2,3,4,5),b'abc',None)
        m.log_continuity((1,2,7,8,9),b'abcdef',snapshot)
        for identity,data in (((1,3,3,4,5),b'abcdef'),((1,2,3,4,5),b'ab'),((1,2,3,4,5),b'xbcdef')):
            with self.subTest(identity=identity,data=data),self.assertRaises(ValueError):
                m.log_continuity(identity,data,snapshot)

    def test_real_append_during_read_is_retried_with_prefix_continuity(self):
        path=self.path/'live.log';path.write_bytes(b'old prefix\n')
        _,previous=m.read_live_log(path,None)
        original=os.read;changed=[False]
        def append_during_read(fd,size):
            data=original(fd,size)
            if not changed[0]:
                changed[0]=True
                with path.open('ab') as writer: writer.write(b'new line\n')
            return data
        with patch.object(m.os,'read',side_effect=append_during_read):
            self.assertIsNone(m.read_live_log(path,previous))
        raw,snapshot=m.read_live_log(path,previous)
        self.assertEqual(raw,b'old prefix\nnew line\n')
        self.assertEqual(snapshot[1],len(raw))

    def test_replaced_truncated_or_rewritten_growing_log_is_not_retried(self):
        for mode in ('replace','truncate','rewrite'):
            with self.subTest(mode=mode):
                path=self.path/('live-'+mode);path.write_bytes(b'old prefix\n')
                _,previous=m.read_live_log(path,None)
                if mode=='truncate': path.write_bytes(b'old')
                original=os.read;changed=[False]
                def mutate_during_read(fd,size):
                    data=original(fd,size)
                    if not changed[0]:
                        changed[0]=True
                        if mode=='replace':
                            with path.open('ab') as writer:
                                path.rename(path.with_name(path.name+'-old'))
                                path.write_bytes(b'replacement')
                                writer.write(b'append')
                        else:
                            if mode=='rewrite':
                                with path.open('r+b') as writer: writer.write(b'X')
                            with path.open('ab') as writer: writer.write(b'additional bytes')
                    return data
                with patch.object(m.os,'read',side_effect=mutate_during_read):
                    with self.assertRaises(ValueError): m.read_live_log(path,previous)

    def test_nonappend_change_or_missing_previously_seen_log_fails(self):
        path=self.path/'live.log';path.write_bytes(b'original\n')
        _,previous=m.read_live_log(path,None)
        original=os.read;changed=[False]
        def rewrite_during_read(fd,size):
            data=original(fd,size)
            if not changed[0]:
                changed[0]=True
                with path.open('r+b') as writer: writer.write(b'X')
            return data
        with patch.object(m.os,'read',side_effect=rewrite_during_read):
            with self.assertRaisesRegex(ValueError,'changed while reading'): m.read_live_log(path,previous)
        path.unlink()
        with self.assertRaisesRegex(ValueError,'disappeared'): m.read_live_log(path,previous)

    def test_log_truncated_between_capture_and_path_check_fails(self):
        path=self.path/'live.log';path.write_bytes(b'original log\n')
        original=m.stable_read
        def truncate_after_capture(*args,**kwargs):
            result=original(*args,**kwargs)
            path.write_bytes(b'new')
            return result
        with patch.object(m,'stable_read',side_effect=truncate_after_capture):
            with self.assertRaisesRegex(ValueError,'truncated after capture'): m.read_live_log(path,None)
    def simulate_interruption(self, mode):
        game=self.path/'game';(game/'extensions').mkdir(parents=True);(game/'X4').write_text('')
        auth=self.path/'auth';auth.write_text('')
        out=self.path/'out';case=self.path/'case';profile=case/'profile';profile.mkdir(parents=True)
        lock=self.path/'lock';target=self.path/'save';target.write_text('')
        proc=Mock(pid=100);proc.poll.return_value=None
        checkpoint=SimpleNamespace(checkpoint_id=m.archive.CHECKPOINT_ID,sha256=m.archive.SHA256,size=m.archive.SIZE)
        archive_store=self.path/'archive'/'checkpoints';archive_store.mkdir(parents=True)
        args=SimpleNamespace(out=out,store=archive_store,slot=1,timeout=30,seed=1,game=game,display=':0')
        user=SimpleNamespace(pw_dir=str(self.path),pw_uid=1000,pw_gid=1000,pw_name='tester')
        with ExitStack() as stack:
            def set_(obj,name,value): return stack.enter_context(patch.object(obj,name,value))
            set_(m.os,'geteuid',lambda:1000);stack.enter_context(patch.dict(m.os.environ,{'XAUTHORITY':str(auth)}))
            set_(m.snapshot,'output_path',lambda _:out);set_(m.base,'local_directory',lambda _:archive_store)
            set_(m.archive,'DEFAULT_STORE',archive_store)
            set_(m.archive,'_path_fingerprints',lambda _: {'stable':1})
            set_(m.archive,'verify',lambda _: {'sha256':m.archive.SHA256,'size':m.archive.SIZE})
            set_(m.live,'EXE_SHA',m.archive.SHA256);set_(m.live,'sha',lambda _:m.archive.SHA256)
            set_(m.live,'x4_executables',lambda:[]);set_(m.shutil,'which',lambda _: '/bin/bwrap')
            set_(m.pwd,'getpwuid',lambda _:user);steam=set_(m.live,'steam_identity',Mock(return_value=('stable',)))
            set_(m.snapshot,'prepare_case',lambda *_:(case,profile,[],lock));set_(m,'check_origin_scripts',lambda *_:None)
            set_(m.base,'stage_checkpoint',lambda *_:(checkpoint,target));set_(m,'install',lambda *_:[])
            set_(m,'_read_regular',lambda *_:b'same')
            set_(m,'stable_read',lambda *_ ,**__:((1,2,3,4,5),m.archive.SHA256,b''))
            lock_fd=os.open(target,os.O_RDONLY)
            set_(m.live,'create_lock_target',lambda *_:(lock_fd,'lockstat'))
            def release_lock(*_): os.close(lock_fd)
            release=set_(m.live,'release_lock_target',Mock(side_effect=release_lock))
            handlers={}
            def signal_set(number,handler): handlers[number]=handler;return m.signal.SIG_DFL
            set_(m.signal,'signal',signal_set)
            if mode in ('finalizer','finalizer_raises','finalizer_escalates','finalizer_truthy'):
                def finalizer(result):
                    self.assertFalse((out/'result.json').exists())
                    self.assertTrue(all(handler==m.signal.SIG_IGN for handler in handlers.values()))
                    result['finalizer_checked']=True
                    if mode=='finalizer_raises': raise ValueError('source changed')
                    if mode=='finalizer_escalates': result['success']=True
                    if mode=='finalizer_truthy': result['success']=1
                set_(m,'result_finalizer',finalizer)
            set_(m.base,'readonly_save_command',lambda *_:['bwrap','--die-with-parent'])
            def spawn(*_,**__):
                if mode=='popen': handlers[m.signal.SIGTERM](m.signal.SIGTERM,None)
                return proc
            set_(m.subprocess,'Popen',spawn)
            set_(m.live,'start_ticks',Mock(side_effect=RuntimeError('no start identity')) if mode=='no_identity' else lambda _:7)
            def write_gate(*_):
                if mode=='gate_race': handlers[m.signal.SIGTERM](m.signal.SIGTERM,None)
                return 1
            write=set_(m.os,'write',Mock(side_effect=write_gate))
            set_(m.load,'game_identity',Mock(side_effect=KeyboardInterrupt()))
            stop=set_(m.snapshot,'stop_owned',Mock(side_effect=KeyboardInterrupt()) if mode=='stop_raises' else Mock(return_value=[99] if mode=='stop_survives' else []))
            result=m.run(args)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'],{'popen':'signal 15','gate_race':'signal 15','running':'KeyboardInterrupt','no_identity':'no start identity','stop_raises':'KeyboardInterrupt','stop_survives':'KeyboardInterrupt','finalizer':'KeyboardInterrupt','finalizer_raises':'final result check: source changed','finalizer_escalates':'final result check: finalizer cannot convert failure to success','finalizer_truthy':'final result check: finalizer must preserve boolean success'}[mode])
        if mode.startswith('finalizer'):
            self.assertTrue(result['finalizer_checked'])
            import json
            self.assertEqual(json.loads((out/'result.json').read_text()),result)
        if mode=='no_identity':
            stop.assert_not_called();proc.terminate.assert_called_once();proc.wait.assert_called_once()
            self.assertEqual(result['cleanup_error'],'owned process identity unavailable')
        else: stop.assert_called_once_with(proc,7)
        if mode in ('stop_raises','stop_survives'):
            release.assert_not_called();self.assertIn('lock retained: owned process stop unconfirmed',result['cleanup_error'])
        else: release.assert_called_once()
        self.assertTrue((out/'result.json').is_file());self.assertGreaterEqual(steam.call_count,2)
        self.assertTrue(all(handler==m.signal.SIG_DFL for handler in handlers.values()))
        if mode in ('popen','no_identity'): write.assert_not_called()

    def test_finalizer_runs_fenced_before_single_publication(self):
        for mode in ('finalizer','finalizer_raises','finalizer_escalates','finalizer_truthy'):
            with self.subTest(mode=mode):
                self.tearDown();self.setUp();self.simulate_interruption(mode)

    def test_interrupted_owned_process_is_cleaned_without_success(self): self.simulate_interruption('running')
    def test_signal_during_popen_does_not_release_gate(self): self.simulate_interruption('popen')

    def test_missing_identity_does_not_release_gate(self): self.simulate_interruption('no_identity')
    def test_signal_during_gate_write_cleans_up_without_success(self): self.simulate_interruption('gate_race')
    def test_stop_baseexception_retains_lock_and_reports(self): self.simulate_interruption('stop_raises')
    def test_surviving_owned_process_retains_lock_and_reports(self): self.simulate_interruption('stop_survives')

    def test_exec_gate_refuses_eof_and_only_execs_after_release(self):
        marker=self.path/'executed';helper=ROOT/'tools/dev/authority_exec_gate.py'
        for release in (False,True):
            readfd,writefd=os.pipe2(os.O_CLOEXEC)
            command=[sys.executable,str(helper),str(readfd),sys.executable,'-c',
                     'from pathlib import Path;Path('+repr(str(marker))+').write_text("yes")']
            child=subprocess.Popen(command,pass_fds=(readfd,),start_new_session=True)
            os.close(readfd)
            if release: os.write(writefd,b'G')
            os.close(writefd);code=child.wait(timeout=5)
            self.assertEqual(code,0 if release else 1);self.assertEqual(marker.exists(),release)

    def test_install_has_one_update_owner_and_pause_marker_read_order(self):
        case=self.path/'case';profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
        ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
        for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',
                  lab/'content.xml',profile/'content.xml',profile/'config.xml'): p.write_text('test')
        protected=m.install(case,profile,self.ticket,2)
        owner=(ui/'authority_download_load_probe.lua').read_text()
        self.assertEqual(owner.count("SetScript('onUpdate'"),1)
        points=[owner.index(s) for s in ('if not engine.is_paused()', 'sector_gate_started=true',
                'if not X4LAN_AUTHORITY_MARKER_READY()', 'if not X4LAN_SECTOR_READ_READY()', 'if barrier.update()')]
        self.assertEqual(points,sorted(points));self.assertIn('if sector_gate_started then error',owner)
        marker=(ui/'authority_checkpoint_marker_probe.lua').read_text()
        self.assertIn('marker~=cfg.authority_expected_marker',marker)
        self.assertIn('authority_sector_slot=2',(ui/'config.lua').read_text())
        self.assertIn(lab/'content.xml',protected);self.assertIn(ui/'sha256.lua',protected)
        self.assertEqual({p.name for p in md.iterdir()},set((*m.MD,'authority_sector_read_guard.xml')))

if __name__=='__main__': unittest.main()
