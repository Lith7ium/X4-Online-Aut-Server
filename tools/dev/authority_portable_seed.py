#!/usr/bin/env python3
"""Export or restore one private, confirmed format-3 recovery seed on Linux.

Portable ZIPs accept purchase ledgers up to 64 MiB. Ordinary checkpoints and
local recovery support larger ledgers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from zipfile import ZIP_STORED, ZipFile

from authority_recovered_loopback_server import read_evidence, verify_previous_source
from authority_recovered_registry_snapshot import receipt
from authority.checkpoints import CHUNK, MAX_COMPRESSED, CheckpointStore
from authority.load_messages import _pairs, _reject_constant
from authority.company_purchase_ledger import PURCHASE_LEDGER_NAME, verify_purchase_ledger
from authority.marker_evidence import verify_saved_marker

CONTENT = 'case/home/.config/EgoSoft/X4/45841/content.xml'
MD = 'case/extensions/x4_lan_lab/md'
SCRIPTS = ('authority_checkpoint_marker.xml', 'authority_identity_fixture.xml',
           'authority_ship_registry.xml', 'authority_registry_availability_snapshot.xml')
PACKAGE = 'seed-manifest.json'
# The portable ZIP has a transfer size bound. Live checkpoints do not share it.
PURCHASE_LEDGER_ARCHIVE_LIMIT = 64 * 1024 * 1024
_CHECKPOINT_ID = re.compile(r'checkpoint-[0-9a-f]{32}')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_CONTENT = re.compile(rb'<\?xml version="1\.0"\?><content>'
                      rb'(?:<extension id="ego_dlc_[a-z0-9_]+" enabled="true" />)*'
                      rb'<extension id="x4_lan_lab" enabled="true" /></content>\n')


def _file_limits(checkpoint_id, *, purchase_ledger=False):
    generation = f'checkpoints/{checkpoint_id}'
    optional = {f'{generation}/{PURCHASE_LEDGER_NAME}': PURCHASE_LEDGER_ARCHIVE_LIMIT} if purchase_ledger else {}
    return {
        **optional, 'result.json': 65536, CONTENT: 65536,
        **{f'{MD}/{name}': 65536 for name in SCRIPTS},
        'checkpoints/CURRENT': 4096, 'checkpoints/CONFIRMED': 4096,
        f'{generation}/manifest.json': 4096, f'{generation}/save.xml.gz': MAX_COMPRESSED,
    }


def _json(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_reject_constant)


def _encoded(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def _provenance(result, checkpoint, evidence):
    snapshot = result['registry_snapshot']
    if snapshot.get('required') is True and snapshot.get('initial_inspect_verified') is True:
        mode = {'required': True, 'initial_inspect_verified': True}
    else:
        mode = {'bootstrap': True, 'first_capture_verified': True}
    return {
        **{key: result[key] for key in (
            'supervisor_version', 'success', 'final_world_state', 'owned_processes_remaining',
            'final_checkpoint_id', 'final_checkpoint_sha256')},
        'registry_snapshot': {**mode, 'final_receipt': receipt(checkpoint, evidence)},
        **({'company_purchase_ledger': result['company_purchase_ledger']}
           if result.get('company_purchase_ledger') is not None else {}),
    }


def _verify(source, profile=None):
    source = Path(source)
    store = CheckpointStore(source / 'checkpoints')
    checkpoint = store.latest_marked_confirmed()
    content = source / CONTENT
    if not _CONTENT.fullmatch(read_evidence(content)):
        raise ValueError('source content is not the portable test extension configuration')
    original = read_evidence(source / 'result.json', 1 << 20)
    _, evidence = verify_previous_source(store, checkpoint, profile or content.parent,
                                         require_registry_snapshot=True)
    verify_saved_marker(checkpoint, checkpoint.marker,
                        script_name='X4AuthorityCheckpointMarker', cue_name='MarkerState')
    if read_evidence(source / 'result.json', 1 << 20) != original:
        raise ValueError('source result changed during validation')
    result = _json(original)
    ledger = result.get('company_purchase_ledger')
    verify_purchase_ledger(checkpoint, ledger, registry_epoch=evidence['epoch'])
    return checkpoint, _encoded(_provenance(result, checkpoint, evidence)), ledger


def verify(source, profile=None):
    """Validate a restored directory using the supervisor's recovery guards."""
    checkpoint, normalized, _ = _verify(source, profile)
    if read_evidence(Path(source) / 'result.json') != normalized:
        raise ValueError('restored provenance contains additional or noncanonical data')
    return checkpoint


def _copy(incoming, outgoing, limit):
    digest, size = hashlib.sha256(), 0
    while block := incoming.read(CHUNK):
        size += len(block)
        if size > limit:
            raise ValueError('seed member exceeds its size limit')
        digest.update(block)
        outgoing.write(block)
    return {'sha256': digest.hexdigest(), 'size': size}


def export(source, archive):
    """Create an exclusive mode-0600 ZIP containing the latest confirmed generation."""
    source, archive = Path(source).resolve(strict=True), Path(archive)
    if archive.resolve().is_relative_to(source):
        raise ValueError('archive must be outside the read-only source')
    checkpoint, provenance, ledger = _verify(source)
    files = {}
    fd = os.open(archive, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w+b') as stream, ZipFile(stream, 'w', compression=ZIP_STORED) as package:
            for name, limit in _file_limits(checkpoint.checkpoint_id, purchase_ledger=ledger is not None).items():
                if name.endswith('/save.xml.gz') or name.endswith('/' + PURCHASE_LEDGER_NAME):
                    is_save = name.endswith('/save.xml.gz')
                    source_fd = os.open(source / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(source_fd, 'rb') as incoming, package.open(name, 'w') as outgoing:
                        if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                            raise ValueError('seed source member is not a regular file')
                        files[name] = _copy(incoming, outgoing, limit)
                    expected = (dict(sha256=checkpoint.sha256, size=checkpoint.size) if is_save else
                                {key: ledger[key] for key in ('sha256', 'size')})
                    if files[name] != expected:
                        raise ValueError(f'source member changed during export: {name}')
                    continue
                data = provenance if name == 'result.json' else read_evidence(source / name, limit)
                package.writestr(name, data)
                files[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
            package.writestr(PACKAGE, _encoded({'version': 1, 'checkpoint_id': checkpoint.checkpoint_id,
                                                'files': files}))
    except BaseException:
        archive.unlink()
        raise
    return checkpoint


def _manifest(package):
    members = {item.filename: item for item in package.infolist()}
    if len(members) != len(package.infolist()) or PACKAGE not in members:
        raise ValueError('missing seed manifest or duplicate members')
    info = members[PACKAGE]
    if info.compress_type != ZIP_STORED or not 0 < info.file_size <= 65536:
        raise ValueError('invalid seed manifest member')
    manifest = _json(package.read(info))
    if (type(manifest) is not dict or set(manifest) != {'version', 'checkpoint_id', 'files'}
            or type(manifest['version']) is not int or manifest['version'] != 1
            or type(manifest['checkpoint_id']) is not str or not _CHECKPOINT_ID.fullmatch(manifest['checkpoint_id'])
            or type(manifest['files']) is not dict):
        raise ValueError('invalid seed manifest')
    ledger_name = f"checkpoints/{manifest['checkpoint_id']}/{PURCHASE_LEDGER_NAME}"
    limits = _file_limits(manifest['checkpoint_id'], purchase_ledger=ledger_name in members)
    if members.keys() != limits.keys() | {PACKAGE} or manifest['files'].keys() != limits.keys():
        raise ValueError('seed member names differ from the allowlist')
    for name, limit in limits.items():
        entry, item = manifest['files'][name], members[name]
        if (type(entry) is not dict or set(entry) != {'sha256', 'size'}
                or type(entry['sha256']) is not str or not _SHA256.fullmatch(entry['sha256'])
                or type(entry['size']) is not int or not 0 < entry['size'] <= limit
                or item.compress_type != ZIP_STORED or item.file_size != entry['size']
                or item.compress_size != item.file_size):
            raise ValueError(f'invalid seed member metadata: {name}')
    return manifest, limits


def restore(archive, out, profile=None):
    """Extract only allowed members to a new mode-0700 source, then run the source guard."""
    out = Path(out)
    with ZipFile(archive) as package:
        manifest, limits = _manifest(package)
        out.mkdir(mode=0o700)
        try:
            for name, limit in limits.items():
                path = out / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with package.open(name) as incoming, path.open('xb') as outgoing:
                    observed = _copy(incoming, outgoing, limit)
                if observed != manifest['files'][name]:
                    raise ValueError(f'seed member hash differs: {name}')
            checkpoint = verify(out, profile)
            if checkpoint.checkpoint_id != manifest['checkpoint_id']:
                raise ValueError('seed manifest identifies a different confirmed checkpoint')
        except BaseException:
            shutil.rmtree(out)
            raise
    return checkpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    exporter = commands.add_parser('export')
    exporter.add_argument('--source', type=Path, required=True)
    exporter.add_argument('--archive', type=Path, required=True)
    restorer = commands.add_parser('restore')
    restorer.add_argument('--archive', type=Path, required=True)
    restorer.add_argument('--out', type=Path, required=True)
    restorer.add_argument('--profile', type=Path)
    checker = commands.add_parser('verify')
    checker.add_argument('--source', type=Path, required=True)
    checker.add_argument('--profile', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'export':
        checkpoint = export(args.source, args.archive)
    elif args.command == 'restore':
        checkpoint = restore(args.archive, args.out, args.profile)
    else:
        checkpoint = verify(args.source, args.profile)
    print(json.dumps({'checkpoint_id': checkpoint.checkpoint_id, 'sha256': checkpoint.sha256,
                      'size': checkpoint.size}))


if __name__ == '__main__':
    main()
