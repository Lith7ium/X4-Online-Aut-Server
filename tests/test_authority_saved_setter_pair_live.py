"""Correlate a setter diagnostic to the exact source sample and readback."""
import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from tools.dev import authority_saved_setter_pair_live as subject
from tools.dev.authority_saved_setter_pair_live import verify_apply
from authority.experimental_tcp_motion import motion_digest
from tests import test_authority_experimental_tcp_motion as fixture
from authority_census_test_fixture import synthetic_sector_witness

class SetterEvidence(unittest.TestCase):
 def setUp(self):
  self.sample=fixture.MotionTCP.sample();self.request='d'*32;digest=motion_digest(self.sample)
  self.buffer='[X4LAN] AUTH_PARTICIPANT_MOTION BUFFERED 10055 1 sector_a 1\n'
  self.preflight=f'[X4LAN] AUTH_MOTION_APPLY PREFLIGHT {"e"*32} 10055 1 {digest} -9 2 3 1 2 3 2 10 READY\n'
  self.intent=f'[X4LAN] AUTH_MOTION_APPLY INTENT {self.request} 10055 1 {digest} -9 2 3 1 2 3 2\n'
  self.write=f'[X4LAN] AUTH_MOTION_APPLY WRITE_ENTERED {self.request}\n'
  self.read=f'[X4LAN] AUTH_MOTION_APPLY READBACK {self.request} 10055 1 {digest} 1 2 3 3 1.1\n'
 def verify(self,s):return verify_apply(s.encode(),self.sample,[self.sample],['ready'])
 def test_exact_correlated_source_sample(self):
  evidence=self.verify(self.buffer+self.preflight+self.intent+self.write+self.read)
  self.assertEqual(evidence['target'],[1,2,3]);self.assertEqual(evidence['target_distance_before'],10)
 def test_rejects_missing_buffer_reordered_or_repeated_write(self):
  for raw in (self.preflight+self.intent+self.write+self.read,self.buffer+self.preflight+self.write+self.intent+self.read,
              self.buffer+self.preflight+self.intent+self.write+self.write+self.read):
   with self.subTest(raw=raw),self.assertRaises(ValueError):self.verify(raw)
 def test_rejects_wrong_target_readback_or_time(self):
  for raw in (self.intent.replace(' -9 2 3 1 2 3 ', ' -9 2 3 2 2 3 ')+self.write+self.read,
              self.intent+self.write+self.read.replace(' 1 2 3 3 1.1',' 6 2 3 3 1.1'),
              self.intent+self.write+self.read.replace(' 1 2 3 3 1.1',' 1 2 3 2 0.9')):
   with self.subTest(raw=raw),self.assertRaises(ValueError):self.verify(self.buffer+self.preflight+raw)
 def test_rejects_wrong_digest_or_request(self):
  for raw in (self.read.replace(motion_digest(self.sample),'0'*64),self.read.replace(self.request,'e'*32)):
   with self.subTest(raw=raw),self.assertRaises(ValueError):self.verify(self.buffer+self.preflight+self.intent+self.write+raw)
 def test_two_preflights_require_correlated_near_then_ready(self):
  first=self.sample;second=dict(first,sequence=2,source_time=2,x=2);digest1=motion_digest(first);digest2=motion_digest(second)
  tag='[X4LAN] AUTH_MOTION_APPLY ';r1='a'*32;r2='b'*32;write='c'*32
  pre1=f'{tag}PREFLIGHT {r1} 10055 1 {digest1} 1 2 3 1 2 3 2 0 NEAR\n'
  buf2='[X4LAN] AUTH_PARTICIPANT_MOTION BUFFERED 10055 2 sector_a 2\n'
  pre2=f'{tag}PREFLIGHT {r2} 10055 2 {digest2} -8 2 3 2 2 3 3 10 READY\n'
  intent=f'{tag}INTENT {write} 10055 2 {digest2} -8 2 3 2 2 3 4\n'
  entered=f'{tag}WRITE_ENTERED {write}\n';read=f'{tag}READBACK {write} 10055 2 {digest2} 2 2 3 5 1.1\n'
  raw=(self.buffer+pre1+buf2+pre2+intent+entered+read).encode()
  self.assertEqual(verify_apply(raw,second,[first,second],['near','ready'])['sequence'],2)
  for bad in (raw.replace(b'0 NEAR',b'0 READY'),raw.replace(b'10 READY',b'11 READY'),raw.replace(pre2.encode(),b'')):
   with self.assertRaises(ValueError):verify_apply(bad,second,[first,second],['near','ready'])
  for bad in (raw.replace(r2.encode(),r1.encode()),raw.replace(write.encode(),r2.encode())):
   with self.assertRaisesRegex(ValueError,'request reused'):verify_apply(bad,second,[first,second],['near','ready'])
 def test_frame_tick_precedes_session_tick_for_both_private_cases(self):
  def staged(self,case,profile,ticket,config,nonce,timeout,window):
   lab=case/'extensions/x4_lan_lab';ui=lab/'ui';ui.mkdir(parents=True);(lab/'md').mkdir()
   (lab/'ui.xml').write_text('    <file name="ui/authority_download_load_probe.lua" />\n')
   (ui/'config.lua').write_text('return {}\n')
   role='SOURCE_SINGLE_JOB' if config['role']=='server' else 'PARTICIPANT_JOB'
   (ui/'authority_download_load_probe.lua').write_text("local state='initial';local failed=false\n if state=='done' then X4LAN_"+role+"_TICK();return end\n")
   return []
  with tempfile.TemporaryDirectory() as tmp, patch.object(subject.SetterCoordinator,'_sources',return_value=()), \
       patch.object(subject.motion.MotionCoordinator,'install',new=staged):
   coord=subject.SetterCoordinator({subject.frame.SECTOR:(1318,)},defer_source_ack=True)
   for role in ('server','client'):
    case=Path(tmp)/role;coord.install(case,case/'profile',{}, {'role':role},'a'*32,120,60)
    ui=case/'extensions/x4_lan_lab/ui';owner=(ui/'authority_download_load_probe.lua').read_text()
    self.assertLess(owner.index('X4LAN_FRAME_READ_TICK()'),owner.index('X4LAN_FRAME_READ_DONE()'))
    self.assertLess(owner.index('X4LAN_FRAME_READ_DONE()'),owner.index('X4LAN_'+('SOURCE_SINGLE_JOB' if role=='server' else 'PARTICIPANT_JOB')+'_TICK()'))
    self.assertIn('else return end',owner);self.assertTrue((ui/'authority_frame_read.lua').is_file())
   self.assertNotEqual(coord.frame_nonces['server'],coord.frame_nonces['client'])
 @synthetic_sector_witness()
 def test_full_saved_setter_staging_installs_both_frame_owners(self):
  with tempfile.TemporaryDirectory() as tmp:
   witnesses=subject.motion.client_install.base.inputs.load_witness()
   coord=subject.SetterCoordinator({name:items for name,items in witnesses.items() if items},defer_source_ack=True)
   self.addCleanup(coord.cleanup_after_owned_processes)
   for role in ('server','client'):
    case=Path(tmp)/role;profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
    ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
    for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
    ticket=dict(world_epoch=coord.attempt,connection_handle=1,baseline_id=1,load_generation=1,
      checkpoint_id=subject.archive.CHECKPOINT_ID,sha256=subject.archive.SHA256,attempt_id='0'*32,slot='save_009')
    config=coord.add_role(role)
    protected=coord.install(case,profile,ticket,config,'9'*32,120,60)
    owner=(ui/'authority_download_load_probe.lua').read_text()
    self.assertIn('X4LAN_FRAME_READ_DONE()',owner);self.assertIn(ui/'authority_frame_read.lua',protected)
    if role=='client':
     self.assertIn(ui/'saved_motion_apply.lua',protected)
     self.assertIn(md/'authority_saved_motion_apply.xml',protected)
     self.assertIn('authority_motion_apply_enabled=true',(ui/'config.lua').read_text())
if __name__=='__main__':unittest.main()
