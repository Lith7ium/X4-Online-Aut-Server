"""Bounded initial fixture placement and owner fencing; fake ABI, no game."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SavedInitialAlignmentRuntimeTests(unittest.TestCase):
    def test_native_initial_alignment_and_later_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = ROOT/'tests/fixtures/saved_motion_apply_abi.c'
            library = Path(directory)/'abi.so'
            subprocess.run(['cc','-shared','-fPIC','-O2',str(fixture),'-o',str(library)],check=True,capture_output=True)
            for case in ('success','default_off','stale','future','unprepared','profile','actor','frame',
                         'fixture','docked','player','invalid_prewrite_pose','stale_during_inspection','readback','identity_after_write',
                         'record_failure','running_limit','v3_success','v3_bad_capture','v3_bad_game',
                         'v3_bad_rate','v3_bad_rate_type','v3_bad_rate_key','v3_mixed_fields','v3_mutate_capture',
                         'v3_mutate_version','v3_mutate_rate','v3_mutate_rate_reference',
                         'v3_mutate_game','v3_mutate_pose','v3_mutate_pose_reference',
                         'v3_mutate_snapshot_reference','v3_mutate_arrival','v3_mutate_key',
                         'v3_postwrite_mutation', 'mfq_success', 'mfq_wrong_epoch', 'mfq_wrong_index',
                         'mfq_wrong_code', 'mfq_wrong_macro', 'mfq_player', 'mfq_guard', 'mfq_unprepared',
                         'mfq_stale', 'mfq_no_alignment', 'inspector_enh', 'inspector_mfq',
                         'inspector_wrong_code', 'inspector_default', 'inspector_epoch'):
                with self.subTest(case=case):
                    result = subprocess.run(['luajit','tests/authority_saved_initial_alignment_native.lua'],
                        cwd=ROOT,env=dict(os.environ,LD_PRELOAD=str(library),ALIGNMENT_CASE=case),
                        capture_output=True,text=True,timeout=5)
                    self.assertEqual(result.returncode,0,result.stderr)

    def test_owner_once_only_and_factory_uncertainty(self):
        for case in ('success','uncertain_factory','reentrant_stop','missing_rate','timeout','stop_before_alignment','identity_before_alignment'):
            with self.subTest(case=case):
                result = subprocess.run(['luajit','tests/authority_saved_initial_alignment_owner.lua'],cwd=ROOT,
                    env=dict(os.environ,ALIGNMENT_OWNER_CASE=case),capture_output=True,text=True,timeout=5)
                self.assertEqual(result.returncode,0,result.stderr)

if __name__ == '__main__':
    unittest.main()
