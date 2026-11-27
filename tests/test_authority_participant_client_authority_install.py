"""Explicit private client-authority install and local IPC; no X4 launch."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools/dev'),str(ROOT/'src')]
import authority_participant_client_authority_install as m
from authority_census_test_fixture import synthetic_sector_witness
class ClientAuthorityInstall(unittest.TestCase):
 def test_local_session_ipc(self):
  for script in ('authority_client_authority_motion_session.lua','authority_client_authority_motion_integration.lua'):
   with self.subTest(script=script):
    subprocess.run(['luajit','tests/'+script],cwd=ROOT,check=True,capture_output=True,text=True,timeout=5)
 def test_client_authority_joins_existing_tick(self):
  with tempfile.TemporaryDirectory() as tmp:
   abi=Path(tmp)/'fake.so'
   subprocess.run(['cc','-shared','-fPIC','-O2',str(ROOT/'tests/fixtures/multi_create_running_owner_abi.c'),'-o',str(abi)],check=True,capture_output=True)
   for mode in ('normal','reentrant','reentrant_event'):
    with self.subTest(mode=mode):
     subprocess.run(['luajit','tests/authority_participant_job_session.lua'],cwd=ROOT,env=dict(os.environ,LD_PRELOAD=str(abi),TEST_CLIENT_AUTHORITY=mode),check=True,capture_output=True,text=True,timeout=5)
 @synthetic_sector_witness()
 def test_explicit_install_and_single_update(self):
  with tempfile.TemporaryDirectory() as tmp:
   case=Path(tmp)/'case';profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
   ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
   for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
   archive=m.base.archive
   ticket=dict(world_epoch='f'*32,connection_handle=1,baseline_id=1,load_generation=1,checkpoint_id=archive.CHECKPOINT_ID,sha256=archive.SHA256,attempt_id='0'*32,slot='save_009')
   settings=dict(participant_port=1234,participant_lease='a'*32,job_port=1235,lease='b'*32,attempt='c'*32,epoch=archive.REGISTRY_EPOCH,generation='d'*32,nonce='e'*32,seed=7183,timeout=20)
   authority=dict(port=1236,lease='1'*32,attempt='2'*32,bootstrap_nonce='3'*32,sampler_nonce='4'*32,token='5'*32,incarnation='6'*32,timeout=15,interval=.1)
   with self.assertRaises(ValueError):m.install(case,profile,ticket,settings,{'cluster_01_sector001_macro':41},{**authority,'token':'a'*32})
   with self.assertRaises(ValueError):m.install(case,profile,ticket,settings,{'cluster_01_sector001_macro':41},{**authority,'include_orientation':'true'})
   authority['include_orientation']=True
   files=m.install(case,profile,ticket,settings,{'cluster_01_sector001_macro':41},authority)
   self.assertIn(ui/'client_authority_motion_session.lua',files);self.assertIn(md/'authority_job_descriptor_probe.xml',files)
   self.assertEqual((ui/'authority_download_load_probe.lua').read_text().count("SetScript('onUpdate'"),1)
   config=(ui/'config.lua').read_text();self.assertIn('X4LAN_CLIENT_AUTHORITY_TEST_GRANT',config)
   self.assertNotIn('function X4LAN_CLIENT_AUTHORITY_TEST_GRANT',config)
   self.assertIn('authority_client_authority_test={enabled=true',config)
   self.assertIn('include_orientation=true',config)
