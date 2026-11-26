"""Exercise the extracted diagnostic with synthetic paths and an inert DLL."""
import hashlib
import json
import os
from pathlib import Path
import shutil
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
from authority.client_seat_overlay import COMPANY_PLAYBACK_UI
from authority.combat_capture import capture_config
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

    for playback_index, radar in ((None, False), (1317, False), (1317, True), (None, True)):
        private = view / f'playback-{playback_index}-{radar}'
        (private / 'extensions').mkdir(parents=True)
        (private / '.x4ls-view').touch()
        source = CompanyMotionSource(playback_index=playback_index, cockpit_radar_playback=radar)
        profile_id = 45850 if playback_index is not None and radar else 45841
        try:
            assets = install_fixture_overlay(
                private, new_test_view=True, bound_offer=MarkerBoundOffer(offer, 'e' * 32),
                profile_id=profile_id, save_path=rf'C:\private\{profile_id}\save', participant_port=20123,
                participant_lease='f' * 32, windows_dll=dll, dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest(),
                registry_status=ready, registry_nonce='3' * 32, registry_snapshot=snapshot,
                seat_index=1318, company_motion=source,
            )
        finally:
            source.close()
        lab = private / 'extensions/x4_lan_lab'
        config = (lab / 'ui/config.lua').read_text()
        assert f'test_profile="{profile_id}"' in config
        assert ('authority_windows_company_profile=true' in config) == (profile_id != 45841)
        order = (lab / 'ui.xml').read_text()
        has_playback = playback_index is not None or radar
        for name in COMPANY_PLAYBACK_UI:
            assert ('ui/' + name in assets) == has_playback
            assert (lab / 'ui' / name).is_file() == has_playback
            if has_playback:
                assert order.index('ui/' + name) < order.index('ui/company_motion_source.lua')
        assert ('playback_index=' in config) == (playback_index is not None)
        assert ('cockpit_radar_playback=true' in config) == radar
        if radar:
            expected_max = 1 if playback_index is not None else 3
            assert f'cockpit_max_pilots={expected_max}' in config
        assert ('md/company_cockpit_observer.xml' in assets) == radar
        for name in ('company_cockpit_observer.lua', 'company_cockpit_playback.lua'):
            assert ('ui/' + name in assets) == radar
            assert (lab / 'ui' / name).is_file() == radar
            if radar:
                assert order.index('ui/' + name) < order.index('ui/company_motion_source.lua')
        if has_playback:
            assert 'X4LAN_MOTION_BUFFER=M' in (lab / 'ui/motion_buffer.lua').read_text()
        if playback_index is not None:
            assert 'playback_index=1317' in config

    if 'combat_capture' in signature(install_fixture_overlay).parameters:
        for capture in (False, True):
            private = view / f'combat-{capture}'
            (private / 'extensions').mkdir(parents=True)
            (private / '.x4ls-view').touch()
            assets = install_fixture_overlay(
                private, new_test_view=True, bound_offer=MarkerBoundOffer(offer, 'e' * 32),
                profile_id=45850, save_path=r'C:\private\45850\save', participant_port=20123,
                participant_lease='f' * 32, windows_dll=dll, dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest(),
                registry_status=ready, registry_nonce='3' * 32, registry_snapshot=snapshot,
                seat_index=1318, combat_capture=capture,
            )
            lab = private / 'extensions/x4_lan_lab'
            for name in ('md/client_combat_capture.xml', 'ui/client_combat_capture.lua'):
                assert (name in assets) == capture
                assert (lab / name).is_file() == capture
                if capture:
                    assert assets[name] == hashlib.sha256((lab / name).read_bytes()).hexdigest()
            config = (lab / 'ui/config.lua').read_text()
            assert ('authority_combat_capture={epoch=' in config) == capture
            assert f'run_id="{snapshot["run_id"]}"' in config
            if capture:
                assert capture_config(snapshot, 1318, offer.checkpoint_id, offer.sha256) in config
                observer = (lab / 'md/client_combat_capture.xml').read_text()
                assert '<mdscript name="X4AuthorityClientCombatCapture"' in observer
                assert '[X4AuthorityClientCombatCapture] [X4LAN] AUTH_COMBAT_OBS ' in observer
                ui = (lab / 'ui.xml').read_text()
                assert ui.index('ui/client_combat_capture.lua') < ui.index('ui/authority_two_x4_participant_probe.lua')
"""

# The launcher's -Python receives a .cmd that runs this probe instead of a session.
LAUNCHER_PROBE = r"""
import importlib.util
import json
import sys

from pathlib import Path

client_path, argv = sys.argv[1], sys.argv[2:]
sys.path[:0] = [str(Path(client_path).parent), str(Path(client_path).parents[2] / 'src')]
import authority.windows_fixture_session as session
import authority.windows_fixture_workspace as workspace
spec = importlib.util.spec_from_file_location('packaged_client', client_path)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
calls, places = [], []
session.run_fixture_session = lambda **options: calls.append(options) or {}
# Record the requested view and run directories; nothing is prepared or created.
workspace.WindowsFixtureWorkspace = lambda *args, **options: places.append(args) or object()
client.read_connection_settings = lambda path: {}
client.run(client.parser().parse_args(argv))
options = calls[0]
game, view, run = places[0][:3]
print(json.dumps({'flag': '--combat-capture' in argv, 'combat_capture': options.get('combat_capture'),
                  'registry': options['require_registry_snapshot'], 'game': str(game),
                  'work_root': str(Path(view).parent), 'run_root': str(Path(run).parent)}))
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
                    if entry == 'authority_windows_fixture_client.py':
                        self.assertIn('--company-hull-playback-index', result.stdout)
                        self.assertIn('--company-cockpit-playback-index', result.stdout)
                        self.assertIn('--company-cockpit-playback', result.stdout)
                        self.assertIn('--fresh-company-profile', result.stdout)
            subprocess.run([sys.executable, '-c', OVERLAY_SMOKE], cwd=extracted, env=environment,
                           check=True, timeout=10)
            self.assertTrue(package.COMBAT_CAPTURE <= names)
            # A recorded pilot log produces a receipt with the packaged read-only utility.
            receipt = root / 'combat-capture-receipt.json'
            command = [sys.executable, str(extracted / 'tools/dev/authority_combat_capture_receipt.py'),
                       '--checkpoint', 'checkpoint-' + 'c' * 32, '--registry-epoch', '5e2b7c1d9a4f4e0b8c6d2a1f3e5b7d90',
                       '--piloted-ship-index', '1074', '--log',
                       str(ROOT / 'tests/fixtures/windows-combat-capture-pilot.log')]
            result = subprocess.run([*command, '--sha256', 'd' * 64, '--out', str(receipt)],
                cwd=extracted, env=environment, capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(json.loads(result.stdout)['sequences'], [2, 4, 5])
            stored = json.loads(receipt.read_text(encoding='utf-8'))
            self.assertEqual((stored['checkpoint_id'], stored['checkpoint_sha256'], stored['registry_epoch'],
                              stored['piloted_ship_index'], [hit['sequence'] for hit in stored['hits']]),
                             ('checkpoint-' + 'c' * 32, 'd' * 64, '5e2b7c1d9a4f4e0b8c6d2a1f3e5b7d90',
                              1074, [2, 4, 5]))
            invalid = root / 'mismatched-receipt.json'
            mismatch = subprocess.run([*command, '--sha256', 'e' * 64, '--out', str(invalid)],
                cwd=extracted, env=environment, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn('no active pilot capture for the offered checkpoint and seat', mismatch.stderr)
            self.assertFalse(invalid.exists())

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell'), 'Windows PowerShell launcher')
    def test_extracted_launcher_resolves_work_root_and_passes_combat_capture_only_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / 'client.zip'
            package.build('HEAD', archive_path)
            with ZipFile(archive_path) as archive:
                archive.extractall(root)
            extracted = root / package.PREFIX
            game = root / 'Steam Library' / 'steamapps' / 'common' / 'X4 Foundations'
            work = root / 'work root'
            connection, dll = root / 'connection.json', root / 'inert.dll'
            game.mkdir(parents=True)
            work.mkdir()
            connection.write_text('{}')
            dll.write_bytes(b'MZ synthetic test data')
            probe = root / 'probe.py'
            probe.write_text(LAUNCHER_PROBE)
            stub = root / 'python-probe.cmd'
            stub.write_text(f'@"{sys.executable}" "{probe}" %*\r\n')
            environment = dict(os.environ, PYTHONNOUSERSITE='1')
            environment.pop('PYTHONPATH', None)

            def launch(*options):
                return subprocess.run(
                    ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                     str(extracted / 'tools/run_authority_windows_client.ps1'), '-Python', str(stub),
                     '-Game', str(game), '-ConnectionFile', str(connection), '-NetworkDll', str(dll),
                     '-NetworkDllSha256', hashlib.sha256(dll.read_bytes()).hexdigest(), *options],
                    cwd=extracted, env=environment, capture_output=True, text=True, timeout=60)

            def accepted(*options):
                result = launch(*options)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout.strip().splitlines()[-1])
                for key in ('game', 'work_root', 'run_root'):
                    report[key] = os.path.normcase(report[key])
                return report

            def expected(work_root, **values):
                return {'flag': False, 'combat_capture': None, 'registry': True,
                        'game': os.path.normcase(str(game)), 'work_root': os.path.normcase(str(work_root)),
                        'run_root': os.path.normcase(str(work_root)), **values}

            seat = ('-FreshCompanyProfile', '-CompanyShipIndex', '1074')
            # The documented command omits -WorkRoot: use the game's parent on Windows PowerShell 5.1.
            self.assertEqual(accepted(*seat), expected(game.parent))
            self.assertEqual(accepted(*seat, '-WorkRoot', str(work)), expected(work))
            self.assertEqual(accepted(*seat, '-CombatCapture'),
                             expected(game.parent, flag=True, combat_capture=True))
            for options, message in (
                    ((*seat, '-WorkRoot', str(root / 'missing')), 'WorkRoot must be an existing directory'),
                    ((*seat, '-WorkRoot', str(connection)), 'WorkRoot must be an existing directory'),
                    (('-CombatCapture',), 'CombatCapture requires a Company ship selection')):
                with self.subTest(options=options):
                    refused = launch(*options)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn(message, refused.stdout + refused.stderr)
                    self.assertNotIn('"work_root"', refused.stdout)  # stopped before the client ran
            # The launcher only names the view and run directories; it creates nothing.
            self.assertFalse([child for folder in (game.parent, work) for child in folder.iterdir()
                              if child.name.startswith('x4-authority-')])

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
