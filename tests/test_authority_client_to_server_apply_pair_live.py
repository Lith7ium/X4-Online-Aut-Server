"""Private diagnostic staging and completion fences; no X4 start."""
import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools/dev'),str(ROOT/'src')]
import authority_client_to_server_apply_pair_live as m
import authority_client_to_server_apply_worker as worker
from authority.experimental_tcp_motion import motion_digest
from authority_census_test_fixture import synthetic_sector_witness

class ApplyPair(unittest.TestCase):
 def setUp(self):
  with patch.object(m.Coordinator,'_sources',side_effect=m.pair.Coordinator._sources):
   self.c=m.Coordinator({m.base.SECTOR:(1318,1322)},defer_source_ack=True)
  self.c.add_role('server');self.c.add_role('client');self.addCleanup(self.c.cleanup_after_owned_processes)
 def snapshot(self):
  return {'role':'server','nonce':'0'*32,'epoch':m.archive.REGISTRY_EPOCH,'index':1318,'code':'WRG-830',
   'macro':'ship_ter_m_corvette_01_a_macro','faction':'pioneers','sector_macro':m.base.SECTOR,
   'game_time':3.,'position':[-3.5,2.,3.]}
 @synthetic_sector_witness()
 def test_both_installers_and_worker_config(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(self.c,'check_sources',return_value=True):
   for role in ('server','client'):
    case=Path(tmp)/role;profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
    ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
    for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
    ticket=dict(world_epoch=self.c.attempt,connection_handle=1,baseline_id=1,load_generation=1,
     checkpoint_id=m.archive.CHECKPOINT_ID,sha256=m.archive.SHA256,attempt_id='0'*32,slot='save_009')
    files=self.c.install(case,profile,ticket,{'role':role},'unused',300,60)
    self.assertIn(ui/'authority_frame_read.lua',files)
    self.assertEqual((ui/'authority_download_load_probe.lua').read_text().count("SetScript('onUpdate'"),1)
    if role=='server':self.assertIn('authority_motion_apply_immediate_readback=true',(ui/'config.lua').read_text())
   client={'pid':11,'start_ticks':20,'exe_dev':1,'exe_ino':2};server={'pid':12,'start_ticks':21,'exe_dev':1,'exe_ino':2}
   r=self.c.roles['client'];s=self.c.roles['server'];cfg=self.c.worker_config(r,server,client,time.monotonic()+60)
   cfg.update(server_participant_lease=s['control_lease'],server_job_lease=s['lease'],server_job_attempt=s['attempt'],
    server_motion_lease=s['motion_lease'],server_motion_attempt=s['motion_attempt'],server_snapshot=self.snapshot())
   with patch.object(worker.base,'check_process',return_value=None):
    self.assertEqual(worker.config(json.dumps(cfg).encode()),cfg)
    with self.assertRaises(ValueError):worker.config(json.dumps({**cfg,'server_motion_lease':cfg['lease']}).encode())
 def test_explicit_completion_has_no_fabricated_job(self):
  d={'client_authority_registered':True,'client_authority_index':1318,'client_authority_sample_received':True,
   'server_x4_motion_applied':True,'server_apply_receipt':True,'checkpoint_identity_proven':False,
   'frame_identity_proven':False,'synthetic_tcp_checkpoint_and_admission':True}
  self.assertTrue(m.lifecycle.validate_client_to_server_apply_completion(d,1318))
  for bad in ({'server_x4_motion_applied':False},{'server_apply_receipt':False},{'source_queue_acked':True}):
   with self.assertRaises(RuntimeError):m.lifecycle.validate_client_to_server_apply_completion({**d,**bad},1318)
 def test_exact_second_sample_and_separate_immediate_readback(self):
  first={'version':1,'epoch':m.archive.REGISTRY_EPOCH,'generation':self.c.generation,'index':1318,'sequence':1,
   'sector_macro':m.base.SECTOR,'source_time':4.,'x':1.,'y':2.,'z':3.,'velocity':None}
  second={**first,'sequence':2,'source_time':4.5,'x':2.,'velocity':{'x':2.,'y':0.,'z':0.}}
  self.c.result={'event':'result','ok':True,'tcp_admitted':True,'synthetic_server_checkpoint_and_admission':True,
   'client_authority_registered':True,'index':1318,'sector_macro':m.base.SECTOR,'server_x4_motion_applied':True,
   'server_apply_receipt':True,'frame_identity_proven':False,'samples':[first,second],
   'server_snapshot':self.snapshot(),'candidate_distance':5.5,'selected_sequence':2}
  self.c.server_snapshot=self.snapshot()
  self.c.validate_result(self.c.result);self.c.output_details={};self.c.tickets={'server':{},'client':{}}
  for changed in ({'server_snapshot':{**self.snapshot(),'position':[-3.6,2.,3.]}},
                  {'candidate_distance':8.}, {'selected_sequence':1},
                  {'samples':[{**first,'x':2.},second]}):
   with self.subTest(changed=changed),self.assertRaises(ValueError):self.c.validate_result({**self.c.result,**changed})
  self.c.frame_nonces={'server':'0'*32,'client':'2'*32};self.c.frame_records={'bounded':True}
  digest=motion_digest(second);request='a'*32;pre='b'*32
  server=(f'[X4LAN] AUTH_PARTICIPANT_MOTION BUFFERED 1318 2 {m.base.SECTOR} 4.5\n'
   f'[X4LAN] AUTH_MOTION_APPLY PREFLIGHT {pre} 1318 2 {digest} 12 2 3 2 2 3 5 10 READY\n'
   f'[X4LAN] AUTH_MOTION_APPLY INTENT {request} 1318 2 {digest} 12 2 3 2 2 3 5\n'
   f'[X4LAN] AUTH_MOTION_APPLY WRITE_ENTERED {request}\n'
   f'[X4LAN] AUTH_MOTION_APPLY IMMEDIATE {request} 1318 2 {digest} 2 2 3 5\n'
   f'[X4LAN] AUTH_MOTION_APPLY POST_OBSERVED {request} 1318 2 {digest} 2 2 3 6 5 1 0\n'
   f'[X4LAN] AUTH_MOTION_APPLY READBACK {request} 1318 2 {digest} 2 2 3 6 1\n').encode()
  client=(f'[X4LAN] AUTH_PARTICIPANT_JOB RUNNING 3\n[X4LAN] AUTH_CLIENT_AUTHORITY SAMPLE 1318 1 {m.base.SECTOR} 4 1 2 3\n'
   f'[X4LAN] AUTH_CLIENT_AUTHORITY SAMPLE 1318 2 {m.base.SECTOR} 4.5 2 2 3\n').encode()
  frame_row={'ship':{'code':'WRG-830','macro':'ship_ter_m_corvette_01_a_macro','faction':'pioneers',
   'sector':m.base.SECTOR,'game':3.,'position':(-3.5,2.,3.)}}
  with patch.object(self.c,'check_sources',return_value=True),patch.object(m.lifecycle,'load_observation',return_value={}),\
       patch.object(m.frame,'parse',return_value=frame_row),patch.object(m.frame,'compare',return_value=self.c.frame_records):
   self.assertTrue(self.c.verify_final({'server':server,'client':client}))
   evidence=self.c.output_details['setter_readback']
   self.assertEqual((evidence['server_game_before'],evidence['server_game_after']),(5.,6.))
   self.assertNotIn('client_game_before',evidence);self.assertEqual(evidence['readback_coordinate_process'],'server')
   for bad in (server.replace(b'IMMEDIATE ',b'UNKNOWN '),server.replace(b'POST_OBSERVED ',b'UNKNOWN '),
               server.replace(b' 2 2 3 6 5 1 0\n',b' 2 2 3 6 5 1 9\n'),
               server.replace(b'READBACK '+request.encode(),b'READBACK '+pre.encode())):
    with self.assertRaises(ValueError):self.c.verify_final({'server':bad,'client':client})
if __name__=='__main__':unittest.main()
