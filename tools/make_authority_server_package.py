#!/usr/bin/env python3
"""Package the committed Linux authority test-server sources."""
import argparse
import ast
import gzip
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'x4-authority-test-server/'
REQUIRED = {
    'LICENSE.md', 'LICENSING.md', 'NOTICE.txt', 'VERSION.txt',
    'docs/AUTHORITY-SERVER-PACKAGE.md',
    'src/native/net.c', 'src/lockstep/base/ui/sha256.lua',
    'src/authority/md/checkpoint_marker.xml',
    'tools/dev/authority_connection_check.py',
    'tools/dev/authority_identity_fixture.xml',
    'tools/dev/authority_loopback_simulated_client.py',
    'tools/dev/authority_provider_server.py',
    'tools/dev/authority_portable_seed.py',
    'tools/dev/authority_software_vulkan.py',
    'tools/run_authority_server.sh',
    'tools/dev/authority_recovered_loopback_server.py',
    'tools/dev/authority_recovered_tls_gateway.py',
    'tools/dev/authority_checkpoint_marker_probe.lua',
    'tools/dev/authority_recovery_probe.lua',
    'tools/dev/authority_reuse_probe.lua',
    'tools/dev/authority_world_bridge_probe.lua',
    'tools/dev/authority_ship_registry.xml',
    'tools/dev/authority_registry_availability_snapshot.lua',
    'tools/dev/authority_registry_availability_snapshot.xml',
    'tools/dev/native_gate_battle.xml',
    'tools/dev/native_gate_snapshot.lua',
}
SUPPORT = {
    'tools/make_authority_server_package.py',
    'tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md',
    'tools/dev/AUTHORITY-WINDOWS-FIXTURE-CLIENT.md',
}
RUNTIME_DIRS = ('src/authority/', 'src/lockstep/launcher/')
SOURCE_SUFFIXES = {'.py', '.lua', '.xml'}


def included(name):
    return (name in REQUIRED or name in SUPPORT or name == 'src/lockstep/__init__.py' or
            (name.startswith(RUNTIME_DIRS) and PurePosixPath(name).suffix in SOURCE_SUFFIXES))


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def source_imports(source, path):
    for node in ast.walk(ast.parse(source, filename=path)):
        if isinstance(node, ast.Import):
            yield from (name.name for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module
            yield from (node.module + '.' + name.name for name in node.names)


def select_paths(commit):
    records = {}
    for entry in filter(None, git('ls-tree', '-r', '-z', commit).split(b'\0')):
        record, raw_path = entry.split(b'\t', 1)
        records[raw_path.decode('utf-8')] = record
    missing = REQUIRED.difference(records)
    if missing:
        raise ValueError('revision lacks server package files: ' + ', '.join(sorted(missing)))

    paths = {path for path in records if included(path)}
    dev = 'tools/dev/'
    modules = {path.removeprefix(dev).removesuffix('.py').replace('/', '.'): path
               for path in records if path.startswith(dev) and path.endswith('.py')}
    pending = sorted(path for path in paths if path.endswith('.py'))
    while pending:
        path = pending.pop()
        if not records[path].startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError(f'package requires a regular tracked file: {path}')
        for module in source_imports(git('show', f'{commit}:{path}'), path):
            relative = modules.get(module)
            if relative is not None and relative not in paths:
                paths.add(relative)
                pending.append(relative)
    for path in paths:
        if not records[path].startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError(f'package requires a regular tracked file: {path}')
    return sorted(paths)


def build(revision, output):
    commit = git('rev-parse', '--verify', '--end-of-options', revision + '^{commit}').decode('ascii').strip()
    paths = select_paths(commit)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f'output already exists: {output}')

    with tempfile.NamedTemporaryFile(prefix='.authority-package-', dir=output.parent, delete=False) as target:
        temporary = Path(target.name)
        try:
            with subprocess.Popen(['git', 'archive', '--format=tar', '--prefix=' + PREFIX,
                                   commit, '--', *paths], cwd=ROOT, stdout=subprocess.PIPE) as archive:
                with gzip.GzipFile(filename='', mode='wb', compresslevel=1, fileobj=target, mtime=0) as compressed:
                    shutil.copyfileobj(archive.stdout, compressed)
                if archive.wait() != 0:
                    raise RuntimeError('git archive failed')
            target.flush()
            os.fsync(target.fileno())
            os.link(temporary, output)
        finally:
            temporary.unlink()
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return commit, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='commit or ref to package')
    parser.add_argument('--out', type=Path, required=True, help='new .tar.gz file in an existing directory')
    args = parser.parse_args()
    try:
        commit, digest = build(args.ref, args.out)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')
    print(f'Commit {commit}\nSHA-256 {digest}\n{args.out}')


if __name__ == '__main__':
    main()
