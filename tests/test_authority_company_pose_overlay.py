"""Check the optional server pose overlay with the real recovery installers."""
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dev'))
import authority_company_pose_overlay as pose
import authority_load_live as load
import authority_recovery_bridge_live as recovery
import authority_registry_checkpoint_install as registry
import linux_snapshot_capture as snapshot

EPOCH, LEASE = 'a' * 32, 'b' * 32


class CompanyPoseOverlayTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.out = Path(work.name)
        self.case, self.profile, _, _ = snapshot.prepare_case(self.out, self.out / 'game', self.out / 'home')
        self.lab = self.case / 'extensions/x4_lan_lab'
        # Start from the known base extension and its ordinary recovery hooks.
        recovery.install_probe(self.case, self.profile, 23001, 'd' * 32, 'e' * 32)
        registry.install(self.case, 'c' * 32, {
            'format': 3, 'epoch': EPOCH, 'issued_count': 1, 'run_id': 'f' * 32,
            'availability_bits': '1', 'operational_bits': '1',
        })

    def install(self, writable):
        return pose.install(self.case, self.profile, port=23002, epoch=EPOCH, lease=LEASE,
                            writable=writable)

    def test_normal_extension_has_no_pose_owner_and_writable_build_reuses_update(self):
        probe = self.lab / 'ui/authority_world_bridge_probe.lua'
        original = probe.read_text()
        self.assertNotIn('company_pose', original)
        self.assertFalse((self.lab / 'md/company_ship_pose.xml').exists())
        paths = self.install(True)
        code = probe.read_text()
        self.assertEqual(code.count("SetScript('onUpdate'"), 1)
        self.assertIn('allow_apply=true', code)
        self.assertIn(load.lua_literal(str(self.profile / 'save')), code)
        self.assertLess(code.index('not registry_checkpoint:ready()'), code.index('company_pose:tick()'))
        manifest = ET.parse(self.lab / 'ui.xml')
        names = [node.get('name') for node in manifest.iter('file')]
        self.assertLess(names.index('ui/company_ship_pose.lua'), names.index('ui/authority_world_bridge_probe.lua'))
        self.assertIn(self.lab / 'ui.xml', paths)
        self.assertIn(probe, paths)
        self.assertIsNotNone(ET.parse(self.lab / 'md/company_ship_pose.xml').find('.//warp'))

    def test_cold_inspection_omits_warp_and_archives_only_its_own_cue_without_overwrite(self):
        self.install(False)
        self.assertIn('allow_apply=false', (self.lab / 'ui/authority_world_bridge_probe.lua').read_text())
        cue = self.lab / 'md/company_ship_pose.xml'
        patched = ET.parse(cue)
        self.assertIsNone(patched.find('.//warp'))
        action = patched.find("./cues/library[@name='ApplyPose']/actions")
        self.assertEqual([(node.get('name'), node.get('exact')) for node in action],
                         [('$actor', "'0'"), ('$status', "'WRITE_DISABLED'")])
        other = {path.name: path.read_bytes() for path in (self.lab / 'md').iterdir() if path != cue}
        target = self.out / cue.name
        target.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            pose.archive_cue(self.case, self.out)
        self.assertTrue(cue.is_file())
        self.assertEqual(target.read_bytes(), b'existing')
        target.unlink()
        before = cue.read_bytes()
        self.assertEqual(pose.archive_cue(self.case, self.out), target)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual({path.name: path.read_bytes() for path in (self.lab / 'md').iterdir()}, other)

    def test_rejects_different_profile_and_registry_without_editing_probe(self):
        probe = self.lab / 'ui/authority_world_bridge_probe.lua'
        original = probe.read_bytes()
        for profile, epoch in ((self.profile.parent / '12345', EPOCH), (self.profile, '1' * 32)):
            with self.subTest(profile=profile.name, epoch=epoch), self.assertRaises(ValueError):
                pose.install(self.case, profile, port=23002, epoch=epoch, lease=LEASE, writable=True)
        self.assertEqual(probe.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
