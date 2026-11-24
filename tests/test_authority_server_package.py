"""Check source archive boundaries and exercise extracted entry points."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    'make_authority_server_package',
    Path(__file__).resolve().parents[1] / 'tools/make_authority_server_package.py',
)
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)

COMPANY_OVERLAY = '''
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path.cwd() / 'tools/dev'))
import authority_client_native as native
from authority.company_motion_source import CompanyMotionSource
from authority.load_messages import Offer
from authority.trusted_marker_load_session import MarkerBoundOffer

offer = Offer(world_epoch='a'*32, connection_handle=1, baseline_id=1, load_generation=1,
              checkpoint_id='checkpoint-' + 'b'*32, sha256='c'*64, attempt_id='d'*32,
              size=123, name='j1.xml.gz')
bound = MarkerBoundOffer(offer, 'e'*32)
snapshot = {
    'format': 3, 'source_registry_checked': True, 'epoch': 'f'*32, 'run_id': bound.marker,
    'evidence_scope': 'saved checkpoint bytes only', 'issued_count': 2, 'mapping_sha256': '1'*64,
    'availability_bits': '11', 'operational_bits': '11', 'available_count': 2,
    'operational_count': 2, 'unavailable_count': 0,
}
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    case, profile, _, _ = native.snapshot.prepare_case(root / 'client', root / 'game', root / 'home')
    # The participant installer copies the caller's prebuilt native library.
    library = root / 'test.so'
    library.write_bytes(b'synthetic library')
    participant = native.Participant()
    source = CompanyMotionSource()
    try:
        native.download.install_probe(case, profile, native.ticket_for(bound))
        participant.server_library = library
        participant.install(case, include_identity=False)
        native.marked.install_marked_load_probe(case)
        with patch.object(native.company_seat, 'verify_join_snapshot', return_value=snapshot):
            paths, _ = native.company_seat.install(case, profile, bound,
                profile / 'save/save_009.xml.gz', 2, lambda: None, company_motion=source)
        lab = case / 'extensions/x4_lan_lab'
        modules = {node.attrib['name'] for node in ET.parse(lab / 'ui.xml').iter('file')}
        assert 'ui/company_motion_source.lua' in modules
        assert all((lab / name).is_file() for name in modules)
        assert lab / 'md/client_seat.xml' in paths
        assert lab / 'md/authority_job_descriptor_probe.xml' in paths
        assert (lab / 'libraries/factions.xml').is_file()
        assert (lab / 'libraries/icons.xml').is_file()
        assert (lab / 'assets/textures/ui/x4lan/x4lan_player16.dds').is_file()
        assert source.registry == ('f'*32, 2)
    finally:
        participant.sock.close()
        source.close()
'''


PARKING_OVERLAY = r'''
from pathlib import Path
import sys
import tempfile
sys.path[:0] = ['src', 'tools/dev']
import authority_station_parking as parking

with tempfile.TemporaryDirectory() as scratch:
    for mode in ('prepare', 'verify'):
        case = Path(scratch) / mode
        lab = case / 'extensions/x4_lan_lab'
        ui = lab / 'ui'
        ui.mkdir(parents=True)
        (lab / 'md').mkdir()
        (ui / 'authority_world_bridge_probe.lua').write_text(
            parking.DECLARATION + parking.ACTIVATION, encoding='ascii')
        (lab / 'ui.xml').write_text(
            '    <file name="ui/authority_world_bridge_probe.lua" />\n', encoding='ascii')
        location = dict(kind='FALLBACK', code=parking.FALLBACK_CODES[0],
                        sector=parking.FALLBACK_SECTOR, room='room_macro')
        sites = [dict(code=code, source_entry=entry, sector_macro=parking.FALLBACK_SECTOR)
                 for code, entry in zip(parking.FALLBACK_CODES, parking.FALLBACK_ENTRIES, strict=True)]
        paths = parking.install(case, 'a' * 32, mode, location if mode == 'verify' else None,
                                fallback_sites=sites)
        assert len(paths) == 6 and all(path.is_file() for path in paths)
        station = (lab / 'md' / parking.STATION_SCRIPT).read_bytes()
        assert (b'<teleport_player ' in station) == (mode == 'prepare')
'''


class AuthorityServerPackageTest(unittest.TestCase):
    def test_reproducible_archive_contains_only_committed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            extra_source = {'tools/dev/dependency.py', 'src/authority/lua/dependency.lua'}
            excluded = {'third_party/example.c', 'tools/dev/connection.json',
                        'src/lockstep/base/native/runtime.dll', '.local/checkpoints/save.xml.gz',
                        'tools/dev/unrelated_experiment.py', 'tools/dev/unrelated_probe.lua'}
            for name in PACKAGE.REQUIRED | extra_source | excluded:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('committed\n')
            (root / 'tools/dev/authority_provider_server.py').write_text(
                'def start():\n    import dependency\n')
            launcher = root / 'tools/run_authority_server.sh'
            launcher.chmod(0o755)
            (root / 'tools/run_authority_client.sh').chmod(0o755)
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test', '-c',
                            'user.email=test@example.invalid', 'commit', '-qm', 'package fixture'], check=True)
            launcher.write_text('working tree edit\n')
            (root / 'tools/dev/untracked.py').write_text('untracked\n')
            first, second = root/'first.tar.gz', root/'second.tar.gz'
            with patch.object(PACKAGE, 'ROOT', root):
                self.assertEqual(PACKAGE.build('HEAD', first), PACKAGE.build('HEAD', second))
                with self.assertRaises(FileExistsError):
                    PACKAGE.build('HEAD', first)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(first, 'r:gz') as archive:
                files = {member.name.removeprefix(PACKAGE.PREFIX) for member in archive if member.isfile()}
                self.assertEqual(files, PACKAGE.REQUIRED | extra_source)
                entry = archive.getmember(PACKAGE.PREFIX + 'tools/run_authority_server.sh')
                self.assertTrue(entry.mode & 0o111)
                player = archive.getmember(PACKAGE.PREFIX + 'tools/run_authority_client.sh')
                self.assertTrue(player.mode & 0o111)
                with archive.extractfile(entry) as stream:
                    self.assertEqual(stream.read(), b'committed\n')

    def test_extracted_entrypoints_and_company_player_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'provider.tar.gz'
            PACKAGE.build('HEAD', output)
            with tarfile.open(output, 'r:gz') as archive:
                archive.extractall(root, filter='data')
            extracted = root / PACKAGE.PREFIX
            tools = extracted / 'tools/dev'
            # The extracted tools must find their imports without the checkout
            # paths used by the test suite.
            environment = dict(os.environ, PYTHONNOUSERSITE='1')
            environment.pop('PYTHONPATH', None)

            with self.subTest(entrypoint='portable seed'):
                result = subprocess.run(
                    [sys.executable, str(tools/'authority_portable_seed.py'), '--help'],
                    cwd=extracted, env=environment, capture_output=True, text=True,
                    check=True, timeout=10,
                )
                self.assertIn('{export,restore,verify}', result.stdout)

            for role in ('server', 'client'):
                with self.subTest(entrypoint=role):
                    result = subprocess.run(
                        [str(extracted / f'tools/run_authority_{role}.sh'), '--help'],
                        cwd=extracted, env=environment, capture_output=True, text=True,
                        check=True, timeout=10,
                    )
                    self.assertIn('--company-motion', result.stdout)
                    if role == 'server':
                        self.assertIn('--park-server-avatar {prepare,verify}', result.stdout)

            # Compose both parking variants using only extracted source assets.
            subprocess.run([sys.executable, '-c', PARKING_OVERLAY],
                           cwd=extracted, env=environment, check=True, timeout=10)

            # Assemble the normal Linux player overlay from extracted assets.
            # Only the save-byte evidence is synthetic. No X4 binary is needed.
            subprocess.run([sys.executable, '-c', COMPANY_OVERLAY],
                           cwd=extracted, env=environment, check=True, timeout=10)

            for permission, status in ((b'', 1), (b'G', 0)):
                with self.subTest(entrypoint='child gate', permission=permission):
                    read_fd, write_fd = os.pipe()
                    try:
                        os.write(write_fd, permission)
                    finally:
                        os.close(write_fd)
                    try:
                        result = subprocess.run(
                            [sys.executable, str(tools/'authority_exec_gate.py'), str(read_fd),
                             sys.executable, '-c', 'print("child executed")'],
                            cwd=extracted, env=environment, pass_fds=(read_fd,),
                            capture_output=True, text=True, timeout=10,
                        )
                    finally:
                        os.close(read_fd)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(result.stdout, 'child executed\n' if permission else '')
