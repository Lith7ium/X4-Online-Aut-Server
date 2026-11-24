#!/usr/bin/env python3
"""Package the committed Windows authority fixture diagnostic as a ZIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from zipfile import ZipFile, ZipInfo

from make_authority_server_package import git, source_imports

PREFIX = 'x4-authority-windows-diagnostic/'
DLL = 'network/x4lan_net_64.dll'
DLL_SHA256 = '6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0'
DLL_SOURCE = {
    'src/native/net.c': '143185e4e0ef5e32aca4b000814b420ea71c374faa6dc3a193dcc3ca2e813191',
    'src/native/build.py': 'a1b65d0484a1481bce09809346cd91d7cfa021f2d54c94988378eb0bb4c66059',
}
REQUIRED = {
    'LICENSE.md', 'LICENSING.md', 'NOTICE.txt', 'VERSION.txt',
    'docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md',
    'reports/AUTHORITY-WINDOWS-NETWORK-BUILD-20261118.md',
    'tools/run_authority_windows_client.ps1',
    'tools/dev/AUTHORITY-WINDOWS-FIXTURE-CLIENT.md',
    'tools/dev/authority_connection_check.py',
    'tools/dev/authority_windows_fixture_client.py',
    'tools/dev/authority_checkpoint_marker_probe.lua',
    'tools/dev/authority_download_load_probe.lua',
    'tools/dev/authority_identity_fixture.xml',
    'tools/dev/authority_identity_probe.lua',
    'tools/dev/authority_player_sector_interest.xml',
    'tools/dev/authority_recovered_client_motion.lua',
    'tools/dev/authority_registry_availability_snapshot.lua',
    'tools/dev/authority_registry_availability_snapshot.xml',
    'tools/dev/authority_registry_resolve_string.lua',
    'tools/dev/authority_replica_motion_bind.xml',
    'tools/dev/authority_saved_baseline_motion_bind.xml',
    'tools/dev/authority_saved_motion_apply.xml',
    'tools/dev/authority_saved_replica_control.xml',
    'tools/dev/authority_ship_registry.xml',
    'tools/dev/authority_two_x4_participant_probe.lua',
    'src/authority/md/checkpoint_marker.xml',
    *(f'src/authority/lua/{name}.lua' for name in (
        'client_load_barrier', 'client_motion_event_router', 'client_registry_inspect',
        'control_state', 'local_udp_module', 'motion_buffer', 'motion_client_owner',
        'motion_playback', 'player_sector_interest', 'recovery_registry_status',
        'registry_checkpoint_gate', 'replica_motion_binding', 'saved_baseline_motion_binding',
        'saved_motion_apply', 'saved_replica_control', 'x4_client_load_engine', 'x4_control_engine',
    )),
    'src/lockstep/__init__.py', 'src/lockstep/base/ui/sha256.lua',
    *(f'src/lockstep/launcher/{name}.py' for name in (
        '__init__', 'extension', 'game', 'joinfiles', 'profile', 'steam',
    )),
    'src/lockstep/base/libraries/factions.xml', 'src/lockstep/base/libraries/icons.xml',
    *(f'src/lockstep/base/assets/textures/ui/x4lan/x4lan_player{slot}.dds' for slot in range(1, 17)),
    *DLL_SOURCE,
}
COMPANY_SEAT = {'src/authority/lua/client_seat.lua', 'src/authority/md/client_seat.xml'}


def select_paths(commit):
    records = {}
    for entry in filter(None, git('ls-tree', '-r', '-z', commit).split(b'\0')):
        record, raw_path = entry.split(b'\t', 1)
        records[raw_path.decode('utf-8')] = record

    dev = 'tools/dev/'
    modules = {path.removeprefix(dev).removesuffix('.py').replace('/', '.'): path
               for path in records if path.startswith(dev) and path.endswith('.py')}
    paths, pending = set(), []

    def include(path):
        if not records[path].startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError(f'package requires a regular tracked file: {path}')
        paths.add(path)
        if path.startswith(dev) and path.endswith('.py'):
            pending.append(path)

    for path in records:
        if path in REQUIRED or path in COMPANY_SEAT or (
                path.startswith('src/authority/') and path.endswith('.py')):
            include(path)
    if missing := REQUIRED - paths:
        raise ValueError('revision lacks Windows diagnostic files: ' + ', '.join(sorted(missing)))
    while pending:
        path = pending.pop()
        for module in source_imports(git('show', f'{commit}:{path}'), path):
            dependency = modules.get(module)
            if dependency is not None and dependency not in paths:
                include(dependency)
    return sorted(paths)


def recorded_dll(path, commit):
    # Accept only the retained November 18 source build. Other builds stay
    # external and are supplied by the operator with an independently known hash.
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 8 << 20
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('recorded Windows network DLL must be an ordinary file')
    with path.open('rb') as stream:
        data = stream.read((8 << 20) + 1)
    if hashlib.sha256(data).hexdigest() != DLL_SHA256:
        raise ValueError('DLL differs from the recorded November 18 Windows network build')
    for name, expected in DLL_SOURCE.items():
        if hashlib.sha256(git('show', f'{commit}:{name}')).hexdigest() != expected:
            raise ValueError(f'{name} differs from the recorded DLL build input')
    return data


def write_entry(archive, name, data):
    info = ZipInfo(PREFIX + name)
    info.create_system = 0  # Use the same ZIP metadata on Windows and Linux.
    archive.writestr(info, data)


def build(revision, output, network_dll=None):
    commit = git('rev-parse', '--verify', '--end-of-options', revision + '^{commit}').decode('ascii').strip()
    paths = select_paths(commit)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f'output already exists: {output}')
    dll = recorded_dll(network_dll, commit) if network_dll is not None else None
    build_info = {'commit': commit, 'network_dll': (
        {'path': DLL, 'sha256': DLL_SHA256, 'source_revision': 'd8bfeda3', 'zig': '0.15.2'}
        if dll is not None else None
    )}
    with tempfile.NamedTemporaryFile(prefix='.authority-client-', dir=output.parent, delete=False) as target:
        temporary = Path(target.name)
    try:
        # Stored entries and fixed metadata make identical inputs byte-reproducible.
        with ZipFile(temporary, 'w') as archive:
            for path in paths:
                write_entry(archive, path, git('show', f'{commit}:{path}'))
            if dll is not None:
                write_entry(archive, DLL, dll)
            write_entry(archive, 'BUILD.json', json.dumps(build_info, indent=2, sort_keys=True) + '\n')
        os.link(temporary, output)
    finally:
        temporary.unlink()
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return commit, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='committed client source to package')
    parser.add_argument('--out', type=Path, required=True, help='new .zip file in an existing directory')
    parser.add_argument('--recorded-network-dll', type=Path,
                        help='optional retained November 18 source-built Windows UDP DLL')
    args = parser.parse_args()
    try:
        commit, digest = build(args.ref, args.out, args.recorded_network_dll)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')
    print(f'Commit {commit}\nSHA-256 {digest}\n{args.out}')


if __name__ == '__main__':
    main()
