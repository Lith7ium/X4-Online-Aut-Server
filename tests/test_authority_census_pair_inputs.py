"""Synthetic census evidence and small byte copies; never reads the X4 archive."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools/dev'),str(ROOT/'src')]
import authority_census_pair_inputs as m

from authority_census_test_fixture import make_census_witness


class Inputs(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def load(self,data):
        raw=json.dumps(data,separators=(',',':')).encode();path=self.root/'witness.json';path.write_bytes(raw)
        with patch.object(m,'WITNESS_SHA256',hashlib.sha256(raw).hexdigest()):return m.load_witness(path)

    def test_valid_witness_snapshot_is_immutable_and_bounded(self):
        data=make_census_witness();data['sectors'][0]['witnesses'][0]['saved_under_dockingbay']=True
        result=self.load(data)
        self.assertEqual(len(result),152);self.assertNotIn(1,result['sector_000'])
        self.assertEqual(sum(map(len,result.values())),m.archive.COUNT-1)
        with self.assertRaises(TypeError):result['sector_000']=()

    def test_bad_saved_mappings_fail_even_with_matching_hash(self):
        for mode in ('duplicate','missing','bool_index','flags','sector_name','sector_ref','wrong_count','epoch','asserted_runtime'):
            with self.subTest(mode=mode):
                data=make_census_witness();a=data['sectors'][0]['witnesses'][0]
                if mode=='duplicate':data['sectors'][1]['witnesses'][0]['index']=a['index']
                elif mode=='missing':data['sectors'][0]['witnesses'].pop(0)
                elif mode=='bool_index':a['index']=True
                elif mode=='flags':a['saved_under_dockingbay']=0
                elif mode=='sector_name':data['sectors'][1]['sector_macro']=data['sectors'][0]['sector_macro'].upper()
                elif mode=='sector_ref':data['sectors'][1]['sector_saved_component_ref']=data['sectors'][0]['sector_saved_component_ref']
                elif mode=='wrong_count':data['saved_registry_count']=True
                elif mode=='epoch':data['registry_epoch']='f'*32
                elif mode=='asserted_runtime':data['runtime_binding_proven']=True
                with self.assertRaises(ValueError):self.load(data)

    def test_witness_hash_and_symlink_fail(self):
        path=self.root/'witness.json';path.write_text(json.dumps(make_census_witness()))
        with self.assertRaisesRegex(ValueError,'differs'):m.load_witness(path)
        link=self.root/'link.json';link.symlink_to(path)
        with self.assertRaises(OSError):m.load_witness(link)

    def test_provenance_and_witness_are_each_loaded_once(self):
        store=self.root/'checkpoints';source=store/m.archive.CHECKPOINT_ID/'save.xml.gz'
        source.parent.mkdir(parents=True);source.write_bytes(b'x');fp=m._stat_tuple(source.lstat());fps={str(source):fp}
        evidence={'checkpoint_id':m.archive.CHECKPOINT_ID,'sha256':m.archive.SHA256,'registry_epoch':m.archive.REGISTRY_EPOCH,
            'registry_entries':m.archive.COUNT,'current_equals_confirmed':True,'saved_marker_matches':True,
            'forward_reverse_match':True,'runtime_binding_proven':False,'exact_loaded_file_proven':False}
        with patch.object(m.archive,'DEFAULT_STORE',store),patch.object(m.archive,'fingerprints',return_value=fps), \
             patch.object(m.archive,'verify',return_value=evidence) as verify, \
             patch.object(m,'load_witness',return_value={'sector_a':(1,)}) as witness:
            result=m.verify_once(store,self.root/'witness.json')
        verify.assert_called_once_with(store);witness.assert_called_once_with(self.root/'witness.json')
        self.assertEqual(result.source_path,source);self.assertEqual(result.source_fingerprint,fp)

    def setup_staging(self):
        store=self.root/'old_experiment'/'checkpoints';source=store/'checkpoint'/'save.xml.gz';source.parent.mkdir(parents=True)
        content=b'synthetic compressed bytes';source.write_bytes(content);sha=hashlib.sha256(content).hexdigest()
        fp=m._stat_tuple(source.lstat());fingerprints={str(source):fp}
        verified=m.VerifiedInputs(source,fp,fingerprints,'a'*64,{})
        profiles={}
        for role in ('server','client'):
            profile=self.root/'new_experiment'/role/'profile';(profile/'save').mkdir(parents=True);profiles[role]=profile
        return store,source,content,sha,fingerprints,verified,profiles

    def test_one_source_read_produces_two_independent_verified_stages(self):
        store,source,content,sha,fps,verified,profiles=self.setup_staging()
        with patch.object(m.archive,'SIZE',len(content)),patch.object(m.archive,'SHA256',sha), \
             patch.object(m.archive,'fingerprints',return_value=fps):
            result=m.stage_pair(verified,profiles)
        self.assertEqual(set(result),{'server','client'})
        for target,fp,digest in result.values():
            self.assertEqual(target.read_bytes(),content);self.assertEqual(digest,sha);self.assertEqual(fp[2],len(content))
        self.assertNotEqual(result['server'][1][:2],result['client'][1][:2])
        self.assertEqual(source.read_bytes(),content)

    def test_changed_source_existing_target_and_shared_profile_fail(self):
        store,source,content,sha,fps,verified,profiles=self.setup_staging()
        with patch.object(m.archive,'SIZE',len(content)),patch.object(m.archive,'SHA256',sha), \
             patch.object(m.archive,'fingerprints',return_value=fps):
            with self.assertRaises(ValueError):m.stage_pair(verified,{'server':profiles['server'],'client':profiles['server']})
            (profiles['client']/'save'/'already').write_bytes(b'x')
            with self.assertRaises(ValueError):m.stage_pair(verified,profiles)
        # A separate fresh staging destination cannot accept changed source bytes.
        for role in profiles:
            profile=self.root/'fresh'/role/'profile';(profile/'save').mkdir(parents=True);profiles[role]=profile
        source.write_bytes(b'changed source')
        with patch.object(m.archive,'SIZE',len(content)),patch.object(m.archive,'SHA256',sha), \
             patch.object(m.archive,'fingerprints',return_value=fps):
            with self.assertRaisesRegex(ValueError,'identity changed'):m.stage_pair(verified,profiles)

    def test_three_profiles_are_independent_and_share_no_inode(self):
        store,source,content,sha,fps,verified,old=self.setup_staging()
        profiles={'server':old['server'],'client1':old['client']}
        profile=self.root/'new_experiment'/'client2'/'profile';(profile/'save').mkdir(parents=True)
        profiles['client2']=profile
        with patch.object(m.archive,'SIZE',len(content)),patch.object(m.archive,'SHA256',sha), \
             patch.object(m.archive,'fingerprints',return_value=fps):
            with self.assertRaises(ValueError):m.stage_three(verified,{**profiles,'client2':profiles['client1']})
            result=m.stage_three(verified,profiles)
        self.assertEqual(set(result),{'server','client1','client2'})
        self.assertEqual(len({row[1][:2] for row in result.values()}),3)
        self.assertTrue(all(row[0].read_bytes()==content and row[2]==sha for row in result.values()))
        self.assertEqual(source.read_bytes(),content)

if __name__=='__main__':unittest.main()
