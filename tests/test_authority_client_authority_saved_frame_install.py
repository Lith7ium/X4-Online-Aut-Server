"""Stage private server diagnostic and paused-frame gate without X4."""
from pathlib import Path
import sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools/dev'),str(ROOT/'src')]
import authority_client_authority_saved_frame_install as subject
from authority_census_test_fixture import synthetic_sector_witness

class SavedFrameInstall(unittest.TestCase):
 @synthetic_sector_witness()
 def test_saved_setter_server_and_pause_handoff(self):
  settings=dict(participant_port=1234,participant_lease='a'*32,job_port=1235,lease='b'*32,attempt='c'*32,
    epoch=subject.archive.REGISTRY_EPOCH,generation='d'*32,nonce='e'*32,seed=7183,timeout=20)
  motion=dict(port=1236,lease='1'*32,attempt='2'*32,epoch=settings['epoch'],generation=settings['generation'],
    nonce='4'*32,timeout=10,max_age=5,max_tracks=100,saved_baseline=True,saved_apply=True,immediate_readback=True)
  ticket=dict(world_epoch='f'*32,connection_handle=1,baseline_id=1,load_generation=1,checkpoint_id=subject.archive.CHECKPOINT_ID,
    sha256=subject.archive.SHA256,attempt_id='0'*32,slot='save_009')
  with tempfile.TemporaryDirectory() as tmp:
   case=Path(tmp)/'case';profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
   ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
   for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
   args=(case,profile,ticket,settings,{'cluster_01_sector001_macro':41},motion)
   for kw in ({'frame_nonce':'9'*32},{'frame_nonce':'a'*32,'enabled':True}):
    with self.assertRaises(ValueError):subject.install_server(*args,**kw)
   files=subject.install_server(*args,frame_nonce='9'*32,enabled=True)
   owner=(ui/'authority_download_load_probe.lua').read_text();cfg=(ui/'config.lua').read_text()
   self.assertLess(owner.index('X4LAN_FRAME_READ_TICK()'),owner.index('X4LAN_FRAME_READ_DONE()'))
   self.assertLess(owner.index('X4LAN_FRAME_READ_DONE()'),owner.index('X4LAN_PARTICIPANT_JOB_TICK()'))
   self.assertIn('else return end',owner);self.assertEqual(owner.count("SetScript('onUpdate'"),1)
   for p in (ui/'authority_frame_read.lua',md/'authority_frame_read.xml',ui/'saved_motion_apply.lua',md/'authority_saved_motion_apply.xml'):
    self.assertIn(p,files);self.assertTrue(p.is_file())
   self.assertIn('authority_motion_apply_enabled=true',cfg);self.assertIn('authority_motion_client_saved_enabled=true',cfg)
   self.assertIn('authority_motion_apply_immediate_readback=true',cfg)
   with self.assertRaises(ValueError):subject.add_frame(case,nonce='8'*32,forbidden=[],enabled=True)
if __name__=='__main__':unittest.main()
