"""Explicit fixture ownership contracts; fake engine only, no X4."""
from pathlib import Path
import os
import subprocess
import unittest
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

class SavedReplicaControlTests(unittest.TestCase):
    def test_controller_contracts(self):
        for case in ('success','disabled','invalid_fixture','pre_guard','duplicate','wrong_request',
                     'mfq','mfq_mismatched','wrong_order','timeout','backward_game','identity','expiry','revalidate','late'):
            with self.subTest(case=case):
                result = subprocess.run(['luajit', 'tests/authority_saved_replica_control.lua'],
                    cwd=ROOT, env=dict(os.environ, CONTROL_CASE=case), capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_fixed_fixture_fences_and_exact_hex_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)/'native.so'
            subprocess.run(['cc','-shared','-fPIC','-O2',str(ROOT/'tests/fixtures/saved_motion_apply_abi.c'),
                '-o',str(library)],check=True,capture_output=True)
            for case in ('success','fixture','pause','docked','wrong_actor','profile','collision','duplicate'):
                with self.subTest(case=case):
                    result = subprocess.run(['luajit','tests/authority_saved_replica_control_native.lua'],
                        cwd=ROOT,env=dict(os.environ,LD_PRELOAD=str(library),CONTROL_NATIVE_CASE=case),
                        capture_output=True,text=True,timeout=5)
                    self.assertEqual(result.returncode,0,result.stderr)

    def test_separate_md_owns_only_captured_order(self):
        root = ET.parse(ROOT/'tools/dev/authority_saved_replica_control.xml').getroot()
        self.assertEqual(root.get('name'), 'X4AuthoritySavedReplicaControl')
        self.assertEqual(len(root.findall('.//create_order')), 1)
        self.assertEqual(root.find('.//create_order').get('name'), 'State.$owned_wait')
        self.assertEqual(root.find('.//cancel_order').get('order'), 'State.$owned_wait')
        text = ET.tostring(root, encoding='unicode')
        for required in ('ENH-870','antigone','State.$byship','State.$prior_order','orderstate.started',
                         '30s','vector.[0m, 0m, 0m]','player.container'):
            self.assertIn(required, text)
        self.assertIn("$request.$epoch == '26a1a365c66788aa06f0aeb9e18ff62e' and $request.$index == 943", text)
        prepare = root.find(".//cue[@name='Prepare']//create_order/..")
        self.assertIn("$ship.order.state == orderstate.started", prepare.get('value'))
        self.assertIn('$ship.pilot == @State.$pilot', text)
        self.assertFalse(root.findall('.//create_ship'))
        self.assertFalse(root.findall('.//set_object_position'))

if __name__ == '__main__':
    unittest.main()
