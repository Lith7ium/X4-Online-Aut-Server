"""Read one quarantined six-unit trade save in an isolated native X4 load."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from unittest.mock import patch

import authority_recovery_live as recovery
import authority_software_vulkan as software_vulkan
from authority.checkpoints import Checkpoint, _file_digest
from authority.marker_evidence import verify_saved_marker
from authority_company_trade_trial import loaded_observation
from authority_provider_server import private_display
from authority_recovered_loopback_server import read_evidence
import linux_gate_live as live


PROFILE = Path('case/home/.config/EgoSoft/X4/45841')
MARKER_SCRIPT = Path('src/authority/md/checkpoint_marker.xml')
REGISTRY = 'authority_ship_registry.xml'
OBSERVER = 'authority_trade_observer.xml'
END_ROW = b"""    <debug_text text="'[X4LAN] AUTH_TRADE_READ END'"/>"""
AVATAR_ROW = b"""    <debug_text text="'[X4LAN] AUTH_TRADE_READ AVATAR station=%s sector=%s room=%s occupied=%s controlled=%s'.[@player.station.idcode,@player.station.sector.macro.id,@player.room.macro.id,(if player.occupiedship then 1 else 0),(if player.controlled then 1 else 0)]"/>"""


def verify_observation(rows):
    expected = (
        r'ACCOUNT faction=player faction_ct=965100 player_ct=965100',
        r'SHIP index=7354 code=WRN-302 status=observed sector=cluster_01_sector001_macro '
        r'docked=1 internal=0 dock_state=\S+ dock_host=BIM-169 free_units=\d+ cargo_units=6',
        r'AVATAR station=XPJ-169 sector=cluster_06_sector002_macro room=dockingbay_arg_s_01_macro '
        r'occupied=0 controlled=0',
    )
    for prefix, pattern in zip(('ACCOUNT ', 'SHIP index=7354 ', 'AVATAR '), expected, strict=True):
        candidates = [row for row in rows if row.startswith(prefix)]
        if len(candidates) != 1 or not re.fullmatch(pattern, candidates[0]):
            raise ValueError(f'quarantined native readback differs: {candidates!r}')


def run(args):
    source = recovery.base.local_directory(args.source_run)
    result_file = source / 'result.json'
    prior_bytes = read_evidence(result_file, 1 << 20)
    prior = json.loads(prior_bytes)
    trade = prior.get('company_trade_trial', {})
    receipt = trade.get('native_save')
    if (prior.get('success') is not False or prior.get('owned_processes_remaining') != []
            or prior.get('error') != 'trade accounting unverified: account_balances_disagree'
            or prior.get('cleanup_errors') or prior.get('cleanup_error')
            or prior.get('final_checkpoint_id') is not None
            or trade.get('mode') != 'six-unit'
            or trade.get('settlement', {}).get('verified') is not False
            or trade.get('settlement', {}).get('reason') != 'account_balances_disagree'
            or receipt != {'name': 'save_009.xml.gz', 'sha256': args.sha256, 'marker': args.marker}):
        raise ValueError('source is not the stopped six-unit account-measurement failure')
    raw = source / PROFILE / 'save/save_009.xml.gz'
    if raw.resolve(strict=True) != raw or _file_digest(raw)[0] != args.sha256:
        raise ValueError('quarantined source save differs')
    registry = recovery.ROOT / 'tools/dev' / REGISTRY
    marker = recovery.ROOT / MARKER_SCRIPT
    source_md = source / 'case/extensions/x4_lan_lab/md'
    for name, current in ((REGISTRY, registry), ('authority_checkpoint_marker.xml', marker)):
        if read_evidence(source_md / name, 1 << 20) != read_evidence(current, 1 << 20):
            raise ValueError(f'quarantined source script differs: {name}')
    readonly = read_evidence(recovery.ROOT / 'tools/dev' / OBSERVER, 1 << 20)
    if readonly.count(END_ROW) != 1:
        raise ValueError('expected native trade observer boundary missing')
    args.store = source / 'checkpoints'

    def stage(_store, stage_profile):
        data = read_evidence(raw, 64 << 20)
        if hashlib.sha256(data).hexdigest() != args.sha256:
            raise ValueError('quarantined source changed during staging')
        save = stage_profile / 'save'
        save.mkdir(mode=0o700)
        target = save / 'save_008.xml.gz'
        with target.open('xb') as output:
            output.write(data)
        selected = Checkpoint('quarantined-' + args.sha256[:32], target, args.sha256,
                              len(data), args.marker)
        verify_saved_marker(selected, args.marker, script_name='X4AuthorityCheckpointMarker',
                            cue_name='MarkerState')
        return selected, target

    def verify_source(_store, _selected, profile):
        if (read_evidence(source / PROFILE / 'content.xml') !=
                read_evidence(profile / 'content.xml')):
            raise ValueError('quarantine and recovery extension content differ')

    def protected(case, _profile):
        md = case / 'extensions/x4_lan_lab/md'
        (md / REGISTRY).write_bytes(read_evidence(registry, 1 << 20))
        (md / OBSERVER).write_bytes(readonly.replace(END_ROW, AVATAR_ROW + b'\n' + END_ROW))
        return [md / REGISTRY, md / OBSERVER]

    def observe(*, profile, guard, **_):
        guard()
        rows = loaded_observation(profile / 'engine-output.log', read_evidence)
        verify_observation(rows)
        return {'read_only_native_observation': rows}

    def finalize(result):
        if (_file_digest(raw)[0] != args.sha256
                or read_evidence(result_file, 1 << 20) != prior_bytes):
            raise ValueError('quarantined source or failure record changed')
        result.update(scope='quarantined native readback only', quarantined=True,
                      source_sha256=args.sha256, source_marker=args.marker,
                      source_supervisor_success=False, settlement_verified=False,
                      admitted=False, published=False)

    original_command = recovery.base.readonly_save_command

    def command(case, game, binds, lock, display, auth, username, seed, target):
        launch = original_command(case, game, binds, lock, display, auth, username, seed, target)
        launch = software_vulkan.constrain(launch, display=display, authorization=auth,
                                           icd=args.vulkan_icd)
        software_vulkan.verify(launch, args.out / 'software-vulkan.log')
        return launch

    with (patch.multiple(recovery, stage_confirmed_recovery=stage, verify_source=verify_source,
                         extra_protected_files=protected, observation_callback=observe,
                         result_finalizer=finalize),
          patch.object(recovery.base, 'readonly_save_command', command)):
        return recovery.run(args)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for flag in ('game', 'source-run', 'out'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--marker', required=True)
    parser.add_argument('--xvfb', default='Xvfb')
    parser.add_argument('--vulkan-icd', type=Path)
    parser.add_argument('--timeout', type=float, default=300)
    args = parser.parse_args(argv)
    args.seed = 276944566
    args.out = args.out.absolute()
    tools = [shutil.which(name) for name in (args.xvfb, 'xauth', 'xdpyinfo')]
    icd = args.vulkan_icd or live.lavapipe_icd()
    if (not all(tools) or icd is None or not re.fullmatch(r'[0-9a-f]{64}', args.sha256)
            or not re.fullmatch(r'[0-9a-f]{32}', args.marker)):
        parser.error('private Xvfb, xauth, xdpyinfo, lavapipe and exact SHA-256/marker required')
    args.vulkan_icd = icd.resolve(strict=True)
    try:
        with private_display(*tools) as (display, auth):
            args.display = display
            with patch.dict(os.environ, {'DISPLAY': display, 'XAUTHORITY': str(auth)}):
                result = run(args)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'REFUSED: {error}\n')
    print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
