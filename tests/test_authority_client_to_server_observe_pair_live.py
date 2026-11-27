import copy
import sys
import unittest
from types import SimpleNamespace
import tempfile
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools/dev')]
import authority_client_to_server_observe_pair_live as m
import authority_census_archive_provenance as archive
from authority.experimental_tcp_motion import motion_digest
from authority_census_test_fixture import synthetic_sector_witness

class ResultTest(unittest.TestCase):
    def setUp(self):
        self.co=m.Coordinator.__new__(m.Coordinator);self.co.observe_limit=2;self.co.generation='b'*32
        snap={'role':'server','nonce':'c'*32,'epoch':archive.REGISTRY_EPOCH,'index':1318,'code':'WRG-830',
            'macro':'ship_ter_m_corvette_01_a_macro','faction':'pioneers','sector_macro':m.base.SECTOR,
            'game_time':3.,'position':[0.,0.,0.]}
        self.co.server_snapshot=snap
        samples=[{'version':1,'epoch':archive.REGISTRY_EPOCH,'generation':self.co.generation,'index':1318,'sequence':n,
            'sector_macro':m.base.SECTOR,'source_time':float(n),'x':x,'y':0.,'z':0.,'velocity':None}
            for n,x in ((1,1.),(2,10.),(3,11.),(4,20.))]
        self.result={'event':'result','ok':True,'tcp_admitted':True,'synthetic_server_checkpoint_and_admission':True,
            'client_authority_registered':True,'index':1318,'sector_macro':m.base.SECTOR,'server_observe_receipts':True,
            'frame_identity_proven':False,'samples':samples,'server_snapshot':snap,'candidate_distance':10.,'selected_sequence':2,
            'observed_sequences':[2,4],'skipped_near_sequences':[3]}
    def test_accept_partition_and_first_candidate(self):
        self.assertEqual(len(self.co.validate_result(self.result)),4)
    def test_reject_incomplete_partition_or_forged_candidate(self):
        for change in ({'skipped_near_sequences':[]},{'observed_sequences':[2,3]},{'candidate_distance':1.},
                       {'selected_sequence':3},{'server_observe_receipts':False}):
            case=copy.deepcopy(self.result);case.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):self.co.validate_result(case)
    def test_reject_one_shot_receipt_masquerading_as_observation(self):
        case=copy.deepcopy(self.result);case['server_apply_receipt']=True
        with self.assertRaises(ValueError):self.co.validate_result(case)
    def test_lifecycle_routes_observe_without_claiming_one_shot_completion(self):
        details={'client_authority_registered':True,'client_authority_index':1318,'client_authority_sample_received':True,
            'checkpoint_identity_proven':False,'frame_identity_proven':False,'synthetic_tcp_checkpoint_and_admission':True,
            'server_observe_receipts':True,'observed_sequences':[2,4]}
        args=SimpleNamespace(client_to_server_observe=True,client_to_server_apply=False,client_authority_index=1318)
        self.assertTrue(m.lifecycle.validate_authority_mode_completion(details,args,False))
        with self.assertRaises(RuntimeError):m.lifecycle.validate_authority_mode_completion(details,args,True)
        args.client_to_server_apply=True
        with self.assertRaises(RuntimeError):m.lifecycle.validate_authority_mode_completion(details,args,False)
        args.client_to_server_apply=False
        with self.assertRaises(RuntimeError):m.lifecycle.validate_authority_mode_completion(dict(details,server_apply_receipt=True),args,False)
    def test_final_log_path_correlates_client_samples_and_server_observations(self):
        self.co.result=self.result;self.co.output_details={};self.co.tickets={'server':{},'client':{}}
        self.co.frame_nonces={'server':'c'*32,'client':'d'*32};self.co.frame_records={'fixture':True}
        client=['[X4LAN] AUTH_PARTICIPANT_JOB RUNNING 0']
        server=[]
        for s in self.result['samples']:
            n=s['sequence'];x=s['x'];digest=motion_digest(s);p=format(2*n,'032x');w=format(2*n+1,'032x')
            client.append(f'[X4LAN] AUTH_CLIENT_AUTHORITY SAMPLE 1318 {n} {m.base.SECTOR} {n} {x} 0 0')
            if n<2:continue
            server.append(f'[X4LAN] AUTH_PARTICIPANT_MOTION BUFFERED 1318 {n} {m.base.SECTOR} {n}')
            before=x if n==3 else 0;dist=abs(x-before);status='NEAR' if n==3 else 'READY'
            server.append(f'[X4LAN] AUTH_MOTION_APPLY PREFLIGHT {p} 1318 {n} {digest} {before} 0 0 {x} 0 0 5 {dist} {status}')
            if n!=3:
                server.extend([f'[X4LAN] AUTH_MOTION_APPLY INTENT {w} 1318 {n} {digest} 0 0 0 {x} 0 0 5',
                    f'[X4LAN] AUTH_MOTION_APPLY WRITE_ENTERED {w}',f'[X4LAN] AUTH_MOTION_APPLY IMMEDIATE {w} 1318 {n} {digest} {x} 0 0 5'])
        ship={'code':'WRG-830','macro':'ship_ter_m_corvette_01_a_macro','faction':'pioneers',
              'sector':m.base.SECTOR,'game':3.,'position':(0.,0.,0.)}
        logs={'server':('\n'.join(server)+'\n').encode(),'client':('\n'.join(client)+'\n').encode()}
        with patch.object(self.co,'check_sources',return_value=True),patch.object(m.lifecycle,'load_observation',return_value={}),\
             patch.object(m.frame,'parse',return_value={'ship':ship}),patch.object(m.frame,'compare',return_value=self.co.frame_records):
            self.assertTrue(self.co.verify_final(logs))
            self.assertEqual([r['outcome'] for r in self.co.output_details['observations']],['observed','near_skipped','observed'])
            logs['client']=logs['client'].replace(b' 4 20.0 0 0',b' 4 21.0 0 0')
            with self.assertRaises(ValueError):self.co.verify_final(logs)

class InstallTest(unittest.TestCase):
    @synthetic_sector_witness()
    def test_both_isolated_overlays_enable_observation_only_on_server(self):
        with patch.object(m.Coordinator,'_sources',side_effect=m.pair.Coordinator._sources):
            co=m.Coordinator({m.base.SECTOR:(1318,1322)},defer_source_ack=True,observe_limit=3)
        co.add_role('server');co.add_role('client');self.addCleanup(co.cleanup_after_owned_processes)
        with tempfile.TemporaryDirectory() as tmp,patch.object(co,'check_sources',return_value=True):
            for role in ('server','client'):
                case=Path(tmp)/role;profile=case/'profile';lab=case/'extensions/x4_lan_lab';ui=lab/'ui';md=lab/'md'
                ui.mkdir(parents=True);md.mkdir();(profile/'save').mkdir(parents=True)
                for p in (ui/'native_gate_snapshot.lua',md/'native_gate_battle.xml',ui/'config.lua',ui/'sha256.lua',lab/'content.xml',profile/'content.xml',profile/'config.xml'):p.write_text('test')
                ticket=dict(world_epoch=co.attempt,connection_handle=1,baseline_id=1,load_generation=1,
                    checkpoint_id=m.archive.CHECKPOINT_ID,sha256=m.archive.SHA256,attempt_id='0'*32,slot='save_009')
                co.install(case,profile,ticket,{'role':role},'unused',300,60)
                text=(ui/'config.lua').read_text()
                if role=='server':
                    self.assertIn('authority_motion_apply_observe_limit=3',text)
                    self.assertIn('authority_motion_apply_immediate_readback=true',text)
                else:self.assertNotIn('authority_motion_apply_observe_limit',text)
if __name__=='__main__':unittest.main()
