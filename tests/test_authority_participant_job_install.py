"""Combined private UI installation and fake participant tick; no X4."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools/dev'),str(ROOT/'src')]
import authority_participant_job_install as m
from authority_census_test_fixture import synthetic_sector_witness
class Install(unittest.TestCase):
    def settings(self):
        return dict(participant_port=1234,participant_lease='a'*32,job_port=1235,lease='b'*32,attempt='c'*32,
                    epoch=m.archive.REGISTRY_EPOCH,generation='d'*32,nonce='e'*32,seed=7183,timeout=20)
    @synthetic_sector_witness()
    def test_combined_overlay_has_one_update_and_pinned_witness(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=Path(tmp)/'case';profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
            ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
            for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',
                      lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
            ticket=dict(world_epoch='f'*32,connection_handle=1,baseline_id=1,load_generation=1,
                        checkpoint_id=m.archive.CHECKPOINT_ID,sha256=m.archive.SHA256,attempt_id='0'*32,slot='save_009')
            with self.assertRaises(ValueError):m.install(case,profile,ticket,self.settings(),{'cluster_01_sector001_macro':53})
            files=m.install(case,profile,ticket,self.settings(),{'cluster_01_sector001_macro':41})
            text=(ui/'authority_download_load_probe.lua').read_text();self.assertEqual(text.count("SetScript('onUpdate'"),1)
            self.assertIn('X4LAN_PARTICIPANT_JOB_TICK()',text)
            self.assertIn(ui/'job_client_owner.lua',files);self.assertIn(lab/'native/x4lan_net.so',files)
            self.assertIn(ui/'local_udp_module.lua',files)
            self.assertLess((lab/'ui.xml').read_text().index('ui/local_udp_module.lua'),(lab/'ui.xml').read_text().index('ui/authority_participant_job_session.lua'))
            self.assertNotIn('SetScript(', (ui/'authority_participant_job_session.lua').read_text())
    def test_platform_udp_module_selection(self):
        subprocess.run(['luajit','tests/authority_local_udp_module.lua'],cwd=ROOT,check=True,capture_output=True,text=True,timeout=5)
    @synthetic_sector_witness()
    def test_multi_mode_requires_the_complete_pinned_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            case=Path(tmp)/'case';profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
            ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
            for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',
                      lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
            ticket=dict(world_epoch='f'*32,connection_handle=1,baseline_id=1,load_generation=1,
                        checkpoint_id=m.archive.CHECKPOINT_ID,sha256=m.archive.SHA256,attempt_id='0'*32,slot='save_009')
            candidates={name:list(values) for name,values in m.inputs.load_witness().items() if values}
            settings={**self.settings(),'multi_witness':True}
            one=next(name for name,values in candidates.items() if len(values)>1)
            bad={**candidates,one:candidates[one][:-1]}
            with self.assertRaises(ValueError):m.install(case,profile,ticket,settings,bad)
            bad=dict(candidates);bad.pop(one)
            with self.assertRaises(ValueError):m.install(case,profile,ticket,settings,bad)
            bad={**candidates,one:list(reversed(candidates[one]))}
            with self.assertRaises(ValueError):m.install(case,profile,ticket,settings,bad)
            m.install(case,profile,ticket,settings,candidates)
            config=(ui/'config.lua').read_text()
            self.assertIn('['+m.base.base.lua_literal(one)+']={'+','.join(map(str,candidates[one]))+'}',config)

    def test_participant_and_job_share_one_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib=Path(tmp)/'fake.so'
            subprocess.run(['cc','-shared','-fPIC','-O2',str(ROOT/'tests/fixtures/multi_create_running_owner_abi.c'),'-o',str(lib)],check=True,capture_output=True)
            for mode in ('0','1','2','motion','saved'):
                env=dict(os.environ,LD_PRELOAD=str(lib))
                if mode in ('motion','saved'):env['TEST_MOTION_SESSION']='saved' if mode=='saved' else '1'
                elif mode!='0':env['TEST_CLIENT_FAULT']=mode
                subprocess.run(['luajit','tests/authority_participant_job_session.lua'],cwd=ROOT,env=env,check=True,capture_output=True,text=True,timeout=5)
