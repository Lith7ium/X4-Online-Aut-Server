"""Offline supervisor/log tests; this module never launches X4."""
import json
import os
import tempfile
import types
from unittest.mock import patch
from pathlib import Path
import socket
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools/dev'),str(ROOT/'src')]
import authority_tcp_job_x4_live as m

class Exited:
    def poll(self):return 0

class Supervisor(unittest.TestCase):
    def ticket(self):
        return dict(world_epoch='a'*32,connection_handle=1,baseline_id=1,load_generation=1,
                    checkpoint_id=m.archive.CHECKPOINT_ID,sha256=m.archive.SHA256,attempt_id='b'*32,slot='save_009')
    def test_load_and_running_apply_order_and_progress(self):
        ticket=self.ticket();fields='|'.join(str(ticket[k]) for k in m.base.load.FIELDS);tag=m.base.load.TAG.decode()
        prefix=[tag+'load_requested|'+fields,'[X4LAN] AUTH_MARKER load INSPECTED '+m.archive.MARKER,tag+'pause_observed|'+fields]
        def raw(rows):return ('\n'.join(rows)+'\n').encode()
        valid=prefix+[m.TAG+'RUNNING 3.0',m.TAG+'LOCAL_APPLIED 10055 3.1']
        result=m.observe(raw(valid),ticket,sector='sector_a');self.assertTrue(result['local_job_applied'])
        self.assertFalse(result['continuous_running_proven'])
        self.assertIsNone(m.observe(raw(prefix+[m.TAG+'RUNNING 3.0']),ticket,sector='sector_a',partial=True))
        for rows in (prefix+[m.TAG+'RUNNING 3.0',m.TAG+'LOCAL_APPLIED 10055 3.0'],
                     prefix+[m.TAG+'RUNNING 3.0',m.TAG+'LOCAL_APPLIED 10056 3.1'],
                     prefix[:2]+[m.TAG+'RUNNING 3.0']+prefix[2:]+[m.TAG+'LOCAL_APPLIED 10055 3.1'],
                     valid+[m.TAG+'LOCAL_APPLIED 10055 3.1']):
            with self.assertRaises(ValueError):m.observe(raw(rows),ticket,sector='sector_a')
    def test_worker_contract_requires_all_specific_receipts(self):
        event={'sequence':1,'index':10055}
        result={'event':'result','ok':True,'tcp_admitted':True,'tcp_job_receipt':True,
                'x4_md_ffi_asserted_by_owner':True,'synthetic_server_and_checkpoint':True,
                'checkpoint_offered_to_x4':False,'event_sequence':1,'index':10055}
        self.assertTrue(m.validate_worker(result,event))
        for change in ({'ok':False},{'tcp_job_receipt':False},{'index':10056},{'checkpoint_offered_to_x4':True},{'extra':True}):
            with self.assertRaises(ValueError):m.validate_worker({**result,**change},event)
    def test_worker_result_requires_single_frame_and_eof(self):
        for data,accepted in ((b'{"ok":true}\n',True),(b'{"ok":true}\n{}\n',False),(b'{"ok":true,"ok":false}\n',False)):
            a,b=socket.socketpair();a.setblocking(False);self.addCleanup(a.close);self.addCleanup(b.close)
            worker=m.Worker.__new__(m.Worker);worker.proc=Exited();worker.control=a;worker.buffer=b'';worker.result=None;worker.eof=False
            b.sendall(data);b.close()
            if accepted:self.assertEqual(worker.poll(),{'ok':True})
            else:
                with self.assertRaises(ValueError):worker.poll()

    def test_supervisor_sends_go_only_after_owned_identity_and_paused_load(self):
        ticket=self.ticket();sector='cluster_01_sector001_macro';holders=[]
        class FakeWorker:
            def __init__(self,settings):
                self.participant=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.participant.bind(('127.0.0.1',0))
                self.job=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.job.bind(('127.0.0.1',0))
                self.proc=None;self.go_value=None;self.started=False;holders.append(self)
            def start(self):self.started=True;self.proc=Exited()
            def go(self,config):self.go_value=config
            def poll(self):
                if self.go_value is None:return None
                event=self.go_value['event']
                return {'event':'result','ok':True,'tcp_admitted':True,'tcp_job_receipt':True,
                    'x4_md_ffi_asserted_by_owner':True,'synthetic_server_and_checkpoint':True,
                    'checkpoint_offered_to_x4':False,'event_sequence':event['sequence'],'index':event['index']}
            def close(self):self.participant.close();self.job.close()
        def fake_run(args):
            with tempfile.TemporaryDirectory() as tmp:
                case=Path(tmp)/'case';profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
                ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
                for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',
                          lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
                m.base.install(case,profile,ticket,1);worker=holders[0];self.assertFalse(worker.started)
                fields='|'.join(str(ticket[k]) for k in m.base.load.FIELDS);tag=m.base.load.TAG.decode()
                rows=[tag+'load_requested|'+fields,'[X4LAN] AUTH_MARKER load INSPECTED '+m.archive.MARKER]
                raw=lambda values:('\n'.join(values)+'\n').encode()
                self.assertIsNone(m.base.check_log_bytes(raw(rows),ticket,1,partial=True));self.assertFalse(worker.started)
                m.base.load.game_identity(None,None,None)
                rows.append(tag+'pause_observed|'+fields)
                self.assertIsNone(m.base.check_log_bytes(raw(rows),ticket,1,partial=True))
                self.assertTrue(worker.started);self.assertEqual(worker.go_value['epoch'],ticket['world_epoch'])
                self.assertEqual(worker.go_value['event']['descriptor']['sector_macro'],sector)
                rows += [m.TAG+'RUNNING 3.0',m.TAG+'LOCAL_APPLIED 10055 3.1']
                result=m.base.check_log_bytes(raw(rows),ticket,1);result['success']=True
                m.base.result_finalizer(result);return result
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root=Path(tmp)
            store=fixture_root/'.local/source/checkpoints'
            generation=store/m.archive.CHECKPOINT_ID
            generation.mkdir(parents=True)
            # The simulated loader never opens a checkpoint, but the real
            # supervisor still checks that its archive files stay unchanged.
            for name in ('CURRENT','CONFIRMED'):
                (store/name).write_text(m.archive.CHECKPOINT_ID+'\n')
            for name in ('manifest.json','save.xml.gz'):
                (generation/name).touch()
            args=types.SimpleNamespace(out=fixture_root/'.local/receiver',store=store,timeout=300)
            with (patch.object(m,'Worker',FakeWorker),patch.object(m.base,'run',side_effect=fake_run),
                  patch.object(m.base.base,'REPO',fixture_root),patch.object(m.base.snapshot,'REPO',fixture_root),
                  patch.object(m.base.load,'game_identity',return_value=(os.getpid(),123))):
                result=m.run(args)
        self.assertTrue(result['coordinator_reports_tcp_job_receipt'])
