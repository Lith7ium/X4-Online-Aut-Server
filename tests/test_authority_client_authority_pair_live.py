"""Saved client authority pair result/lifecycle fences; no X4 start."""
import argparse
import json
import time
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools/dev'),str(ROOT/'src')]
import authority_client_authority_pair_live as m
import authority_client_authority_tcp_worker as worker
from authority_census_test_fixture import synthetic_sector_witness

class ClientAuthorityPair(unittest.TestCase):
 def setUp(self):
  with patch.object(m.ClientAuthorityCoordinator,'_sources',side_effect=m.pair.Coordinator._sources):
   self.c=m.ClientAuthorityCoordinator({m.SECTOR:(1318,1322)},defer_source_ack=True,sample_count=2)
  self.c.add_role('server');self.c.add_role('client');self.addCleanup(self.c.cleanup_after_owned_processes)
 def samples(self):
  first={'version':1,'epoch':m.archive.REGISTRY_EPOCH,'generation':self.c.generation,'index':1318,'sequence':1,
   'sector_macro':m.SECTOR,'source_time':4.,'x':1.,'y':2.,'z':3.,'velocity':None}
  return [first,{**first,'sequence':2,'source_time':4.5,'x':2.,'velocity':{'x':2.,'y':0.,'z':0.}}]
 def result(self):
  return {'event':'result','ok':True,'tcp_admitted':True,'synthetic_server_checkpoint_and_admission':True,
   'client_authority_registered':True,'index':1318,'sector_macro':m.SECTOR,'server_x4_motion_applied':False,'frame_identity_proven':False,'samples':self.samples()}
 def test_saved_index_unique_in_pinned_sector(self):
  with patch.object(m.ClientAuthorityCoordinator,'_sources',side_effect=m.pair.Coordinator._sources):
   for candidates in ({'other':(1318,)},{m.SECTOR:(1,)},{m.SECTOR:(1318,),'other':(1318,)}):
    with self.subTest(candidates=candidates),self.assertRaises(ValueError):m.ClientAuthorityCoordinator(candidates,defer_source_ack=True)
 def test_exact_identity_sequence_velocity_and_no_write(self):
  result=self.result();self.c.validate_result(result)
  for change in ({'server_x4_motion_applied':True},{'frame_identity_proven':True},{'source_queue_acked':True},
                 {'samples':[result['samples'][0],{**result['samples'][1],'source_time':4.}]},
                 {'samples':[result['samples'][0],{**result['samples'][1],'velocity':{'x':9,'y':0,'z':0}}]}):
   with self.subTest(change=change),self.assertRaises(ValueError):self.c.validate_result({**result,**change})
 def test_opt_in_requires_consistent_orientation_and_derived_angular_rate(self):
  self.c.include_orientation=True
  result=self.result()
  first,second=result['samples']
  result['samples']=[{**first,'version':2,'orientation':{'yaw':0.,'pitch':0.,'roll':0.},'angular_rate':None},
   {**second,'version':2,'orientation':{'yaw':.5,'pitch':0.,'roll':0.},'angular_rate':{'yaw':1.,'pitch':0.,'roll':0.}}]
  self.c.validate_result(result)
  for change in ({**result['samples'][1],'angular_rate':{'yaw':2.,'pitch':0.,'roll':0.}},self.samples()[1]):
   with self.subTest(change=change),self.assertRaises(ValueError):self.c.validate_result({**result,'samples':[result['samples'][0],change]})
 def test_local_marker_and_worker_result_must_match(self):
  self.c.result=self.result();self.c.tickets={'server':{},'client':{}}
  client=(b'[X4LAN] AUTH_PARTICIPANT_JOB RUNNING 3.037\n'
   b'[X4LAN] AUTH_CLIENT_AUTHORITY SAMPLE 1318 1 cluster_115_sector001_macro 4 1 2 3\n'
   b'[X4LAN] AUTH_CLIENT_AUTHORITY SAMPLE 1318 2 cluster_115_sector001_macro 4.5 2 2 3\n')
  with patch.object(self.c,'check_sources',return_value=True),patch.object(m.lifecycle,'load_observation',return_value={'marker_checked':True}):
   self.assertTrue(self.c.verify_final({'server':b'normal\n','client':client}))
   for bad in (client.replace(b'4.5 2',b'4.5 9'),client+client,client.replace(b'RUNNING 3.037',b'RUNNING 4'),client+b'[X4LAN] AUTH_PARTICIPANT_JOB LOCAL_APPLIED 1 4\n'):
    with self.subTest(bad=bad),self.assertRaises(ValueError):self.c.verify_final({'server':b'normal\n','client':bad})
 @synthetic_sector_witness()
 def test_private_install_has_explicit_grant_and_one_update_per_role(self):
  self.c.include_orientation=True
  with tempfile.TemporaryDirectory() as tmp,patch.object(self.c,'check_sources',return_value=True):
   for role in ('server','client'):
    case=Path(tmp)/role;profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
    ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
    for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
    ticket=dict(world_epoch=self.c.attempt,connection_handle=1,baseline_id=1,load_generation=1,
     checkpoint_id=m.archive.CHECKPOINT_ID,sha256=m.archive.SHA256,attempt_id='0'*32,slot='save_009')
    files=self.c.install(case,profile,ticket,{'role':role},'unused',300,60)
    self.assertEqual((ui/'authority_download_load_probe.lua').read_text().count("SetScript('onUpdate'"),1)
    if role=='client':
     self.assertIn(ui/'client_authority_test_grant.lua',files)
     self.assertIn('X4LAN_CLIENT_AUTHORITY_TEST_GRANT',(ui/'client_authority_test_grant.lua').read_text())
     self.assertIn('authority_client_authority_test={enabled=true',(ui/'config.lua').read_text())
     self.assertIn('include_orientation=true',(ui/'config.lua').read_text())
    else:
     self.assertFalse((ui/'client_authority_test_grant.lua').exists())
     self.assertNotIn('authority_client_authority_test',(ui/'config.lua').read_text())
 def test_runner_config_is_accepted_by_actual_worker(self):
  client={'pid':11,'start_ticks':20,'exe_dev':1,'exe_ino':2};server={'pid':12,'start_ticks':21,'exe_dev':1,'exe_ino':2}
  cfg=self.c.worker_config(self.c.roles['client'],server,client,time.monotonic()+60)
  with patch.object(worker,'check_process',return_value=None):
   self.assertEqual(worker.config(json.dumps(cfg).encode()),cfg)
   self.c.include_orientation=True;cfg=self.c.worker_config(self.c.roles['client'],server,client,time.monotonic()+60)
   self.assertTrue(worker.config(json.dumps(cfg).encode())['include_orientation'])
   with self.assertRaises(ValueError):worker.config(json.dumps({**cfg,'include_orientation':'true'}).encode())
   with self.assertRaises(ValueError):worker.config(json.dumps({**cfg,'incarnation':cfg['epoch']}).encode())
 def test_explicit_lifecycle_success_without_job_ack(self):
  details={'client_authority_registered':True,'client_authority_index':1318,'client_authority_sample_received':True,
   'server_x4_motion_applied':False,'checkpoint_identity_proven':False,'frame_identity_proven':False,'synthetic_tcp_checkpoint_and_admission':True}
  self.assertTrue(m.lifecycle.validate_client_authority_completion(details,1318))
  for bad in ({'server_x4_motion_applied':True},{'source_queue_acked':True},{'client_authority_registered':False}):
   with self.assertRaises(RuntimeError):m.lifecycle.validate_client_authority_completion({**details,**bad},1318)
