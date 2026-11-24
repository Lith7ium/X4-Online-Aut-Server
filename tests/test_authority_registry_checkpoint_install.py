"""Exercise the generated recovery bridge with its real Lua control owners."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools/dev')]
import authority_recovery_bridge_live as recovery
import authority_registry_checkpoint_install as installer


class RegistryCheckpointInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.case = Path(temporary.name) / 'case'
        self.ui = self.case / 'extensions/x4_lan_lab/ui'
        self.ui.mkdir(parents=True)
        (self.ui.parent / 'md').mkdir()
        self.profile = self.case / 'home/.config/EgoSoft/X4/45841'

        def install_base(*_args, **_kwargs):
            shutil.copyfile(ROOT / 'tools/dev/authority_world_bridge_probe.lua',
                            self.ui / 'authority_world_bridge_probe.lua')

        self.install_base = install_base
        with patch.object(recovery.bridge_install, 'install_probe', side_effect=install_base):
            recovery.install_probe(self.case, self.profile, 1234, 'a' * 32, 'b' * 32)
        self.evidence = {'format': 3, 'epoch': 'c' * 32, 'run_id': 'd' * 32,
                         'issued_count': 3, 'availability_bits': '110',
                         'operational_bits': '100'}

    def test_generated_bridge_inspects_then_captures_each_save(self):
        installed = installer.install(self.case, 'e' * 32, self.evidence)
        self.assertEqual(len(installed), 5)
        for scenario in ('two_saves', 'bad_reload', 'pause_lost'):
            with self.subTest(scenario=scenario):
                result = subprocess.run(['luajit', 'tests/authority_registry_checkpoint_bridge.lua',
                                         str(self.ui), scenario], cwd=ROOT,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_provider_timeout_overrides_are_bounded_and_generated(self):
        with patch.object(recovery.bridge_install, 'install_probe', side_effect=self.install_base):
            with self.assertRaises(ValueError):
                recovery.install_probe(self.case, self.profile, 1234, 'a' * 32, 'b' * 32,
                                       marker_timeout=331)
            recovery.install_probe(self.case, self.profile, 1234, 'a' * 32, 'b' * 32,
                                   marker_timeout=330)
        with self.assertRaises(ValueError):
            installer.install(self.case, 'e' * 32, self.evidence, timeout=301)
        installer.install(self.case, 'e' * 32, self.evidence, timeout=300)
        probe = self.ui / 'authority_world_bridge_probe.lua'
        self.assertIn('timeout=300,', probe.read_text())
        self.assertIn('marker_timeout_seconds=330,', probe.read_text())
        result = subprocess.run(['luajit', 'tests/authority_registry_checkpoint_bridge.lua',
                                 str(self.ui), 'two_saves'], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bad_hooks_and_configuration_do_not_modify_case(self):
        before = {path: path.read_bytes() for path in self.case.rglob('*') if path.is_file()}
        for change in ({'operational_bits': '101'}, {'availability_bits': '11'},
                       {'issued_count': True}, {'run_id': 'c' * 32}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                installer.install(self.case, 'e' * 32, {**self.evidence, **change})
        self.assertEqual(before, {path: path.read_bytes() for path in self.case.rglob('*') if path.is_file()})
        probe = self.ui / 'authority_world_bridge_probe.lua'
        probe.write_text(probe.read_text().replace('local activated=false', 'local activated = false'))
        changed = {path: path.read_bytes() for path in self.case.rglob('*') if path.is_file()}
        with self.assertRaises(ValueError):
            installer.install(self.case, 'e' * 32, self.evidence)
        self.assertEqual(changed, {path: path.read_bytes() for path in self.case.rglob('*') if path.is_file()})

    def test_generated_first_capture_initializes_before_first_save(self):
        installed = installer.install_first_capture(
            self.case, 'e' * 32, source_marker='d' * 32,
            epoch='c' * 32, issued_count=3)
        self.assertEqual(len(installed), 5)
        for scenario in ('first_capture', 'first_capture_refused'):
            with self.subTest(scenario=scenario):
                result = subprocess.run(['luajit', 'tests/authority_registry_checkpoint_bridge.lua',
                                         str(self.ui), scenario], cwd=ROOT,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_capture_rejects_bad_source_before_install(self):
        before = {path: path.read_bytes() for path in self.case.rglob('*') if path.is_file()}
        for source_marker, epoch, count in (('e' * 32, 'c' * 32, 3),
                                            ('c' * 32, 'c' * 32, 3),
                                            ('d' * 32, 'c' * 32, True),
                                            ('d' * 32, 'c' * 32, 0)):
            with self.subTest(source_marker=source_marker, count=count), self.assertRaises(ValueError):
                installer.install_first_capture(self.case, 'e' * 32,
                    source_marker=source_marker, epoch=epoch, issued_count=count)
        self.assertEqual(before, {path: path.read_bytes() for path in self.case.rglob('*') if path.is_file()})


if __name__ == '__main__':
    unittest.main()
