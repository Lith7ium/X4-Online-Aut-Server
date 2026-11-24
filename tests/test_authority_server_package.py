"""Exercise the source-only package in a small Git repository."""
import importlib.util
from pathlib import Path
import subprocess
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
                        'src/lockstep/base/native/runtime.dll', '.local/checkpoints/save.xml.gz'}
            for name in PACKAGE.REQUIRED | extra_source | excluded:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('committed\n')
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
