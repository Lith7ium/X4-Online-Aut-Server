"""Exercise the extracted diagnostic with synthetic paths and an inert DLL."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import make_authority_windows_client_package as package


OVERLAY_SMOKE = r"""
import hashlib
from inspect import signature
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path.cwd() / 'src'))
from authority.client_motion_overlay import RECOVERY_APPLY_EPOCH, RECOVERY_APPLY_FIXTURE
from authority.company_motion_source import CompanyMotionSource
from authority.experimental_windows_fixture_overlay import install_fixture_overlay
from authority.load_messages import Offer
from authority.trusted_marker_load_session import MarkerBoundOffer
from authority.windows_fixture_session import run_fixture_session
from authority.windows_fixture_workspace import WindowsFixtureWorkspace

ready = ('ready', RECOVERY_APPLY_EPOCH, 1318, '2' * 64)
snapshot = dict(
    format=3, source_registry_checked=True, evidence_scope='saved checkpoint bytes only',
    run_id='e' * 32, epoch=ready[1], issued_count=ready[2], mapping_sha256=ready[3],
    availability_bits='1' * 1318, operational_bits='1' * 1318, available_count=1318,
    unavailable_count=0, operational_count=1318,
)
motion = dict(
    port=20124, lease='1' * 32, attempt='4' * 32, generation='5' * 32,
    nonce='6' * 32, router_generation='7' * 32, timeout=5, index=1318,
    saved_playback_fixture=RECOVERY_APPLY_FIXTURE,
    saved_replica_control_fixture=RECOVERY_APPLY_FIXTURE,
    player_sector={'incarnation': '8' * 32, 'nonce': '9' * 32},
)
with tempfile.TemporaryDirectory() as directory:
    view = Path(directory)
    (view / 'extensions').mkdir()
    (view / '.x4ls-view').touch()
    dll = view / 'inert.dll'
    dll.write_bytes(b'MZ synthetic test data')
    offer = Offer(
        world_epoch='a' * 32, connection_handle=1, baseline_id=2, load_generation=3,
        checkpoint_id='checkpoint-' + 'b' * 32, sha256='c' * 64,
        attempt_id='d' * 32, size=100, name='j1.xml.gz',
    )
    seat = {'seat_index': 1318} if 'seat_index' in signature(install_fixture_overlay).parameters else {}
    company_motion = CompanyMotionSource()
    try:
        manifest = install_fixture_overlay(
            view, new_test_view=True, bound_offer=MarkerBoundOffer(offer, 'e' * 32),
            save_path=r'C:\private\45841\save', participant_port=20123, participant_lease='f' * 32,
            windows_dll=dll, dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest(),
            registry_status=ready, registry_nonce='3' * 32, registry_snapshot=snapshot,
            motion_settings=motion, company_motion=company_motion, **seat,
        )
        assert company_motion.registry == (ready[1], 1318)
    finally:
        company_motion.close()
    assert len([file for file in manifest if file.endswith('.dds')]) == 16
    assert 'ui/client_registry_inspect.lua' in manifest
    assert 'ui/authority_recovered_client_motion.lua' in manifest
    assert 'md/authority_player_sector_interest.xml' in manifest
    if seat:
        assert 'ui/client_seat.lua' in manifest
        assert 'md/client_seat.xml' in manifest
    assert 'ui/company_motion_source.lua' in manifest
    assert 'md/authority_job_descriptor_probe.xml' in manifest
"""


class WindowsClientPackageTest(unittest.TestCase):
    def test_collects_tracked_developer_imports_from_packaged_cli(self):
        options = 'tools/dev/authority_client_options.py'
        helper = 'tools/dev/authority_prompt.py'
        unreferenced = 'tools/dev/unreferenced_experiment.py'
        paths = package.REQUIRED | {options, helper, unreferenced}
        tree = b''.join(f'100644 blob object\t{path}\0'.encode() for path in sorted(paths))
        scripts = {
            'tools/dev/authority_windows_fixture_client.py':
                b'from authority_client_options import choose_ship\n',
            options: b'def choose_ship():\n    import authority_prompt\n',
        }

        def git(command, *args):
            if command == 'ls-tree':
                return tree
            return scripts.get(args[0].split(':', 1)[1], b'')

        with patch.object(package, 'git', side_effect=git):
            selected = set(package.select_paths('revision'))
        self.assertEqual(selected, package.REQUIRED | {options, helper})

    def test_reproducible_sources_and_extracted_entry_points(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'first.zip', root / 'second.zip'
            commit, digest = package.build('HEAD', first)
            self.assertEqual((commit, digest), package.build('HEAD', second))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with self.assertRaises(FileExistsError):
                package.build('HEAD', first)
            with ZipFile(first) as archive:
                self.assertIsNone(archive.testzip())
                names = {name.removeprefix(package.PREFIX) for name in archive.namelist()}
                self.assertTrue(package.REQUIRED <= names)
                self.assertFalse(any(name.endswith(('.dll', '.exe', '.gz')) for name in names))
                self.assertNotIn('connection.json', {Path(name).name for name in names})
                self.assertEqual(json.loads(archive.read(package.PREFIX + 'BUILD.json')),
                                 {'commit': commit, 'network_dll': None})
                archive.extractall(root)
            environment = dict(os.environ, PYTHONNOUSERSITE='1')
            environment.pop('PYTHONPATH', None)
            extracted = root / package.PREFIX
            for entry in ('authority_connection_check.py', 'authority_windows_fixture_client.py'):
                with self.subTest(entry=entry):
                    result = subprocess.run(
                        [sys.executable, str(extracted / 'tools/dev' / entry), '--help'],
                        cwd=extracted, env=environment, capture_output=True, text=True,
                        check=True, timeout=10,
                    )
                    self.assertIn('--connection-file', result.stdout)
            subprocess.run([sys.executable, '-c', OVERLAY_SMOKE], cwd=extracted, env=environment,
                           check=True, timeout=10)

    def test_unverified_dll_is_rejected_before_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / 'x4lan_net_64.dll'
            wrong.write_bytes(b'MZ not the recorded project build')
            output = root / 'client.zip'
            with self.assertRaisesRegex(ValueError, 'recorded November 18'):
                package.build('HEAD', output, wrong)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
