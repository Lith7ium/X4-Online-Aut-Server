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
                with archive.extractfile(entry) as stream:
                    self.assertEqual(stream.read(), b'committed\n')

    def test_extracted_seed_and_child_gate_entrypoints(self):
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
