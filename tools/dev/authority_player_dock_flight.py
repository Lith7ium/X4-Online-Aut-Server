"""Observe stock cockpit flight and docking in one isolated recovery process."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
from unittest.mock import patch

import authority_recovery_live as recovery
import authority_software_vulkan as software_vulkan
from authority.checkpoints import CheckpointStore
from authority.client_registry_overlay import validate_full_snapshot
from authority_provider_server import private_display
from authority_recovered_loopback_server import read_evidence, verify_previous_source
from authority_trade_cockpit import _insert
import linux_gate_live as live


SOURCE_DIGEST = '1c7117f327df18df402295755968a19591cc238b9f38519b43348bfce3ee2ff1'
REGISTRY_EPOCH = '4ba7be438ebb04ec5bb85fdb95b5a488'
SHIP_INDEX = 7354
ROOT = recovery.ROOT
PROFILE_SAVE = Path('case/home/.config/EgoSoft/X4/45841/save')
MD = (
    'tools/dev/authority_ship_registry.xml',
    'tools/dev/authority_registry_availability_snapshot.xml',
    'tools/dev/authority_identity_fixture.xml',
    'src/authority/md/client_seat.xml',
    'tools/dev/authority_player_dock_observer.xml',
)
UI = (
    'tools/dev/authority_registry_availability_snapshot.lua',
    'src/authority/lua/registry_checkpoint_gate.lua',
    'src/authority/lua/client_registry_inspect.lua',
    'tools/dev/authority_registry_resolve_string.lua',
    'src/authority/lua/client_seat.lua',
    'src/authority/lua/company_trade_capture.lua',
    'tools/dev/authority_trade_cockpit_probe.lua',
    'tools/dev/authority_player_dock_flight.lua',
)

# The full-inspect owner enforces a live pause and a stable engine time. Once
# it has reported ready, flight owns the resulting observation of that pause.
RECOVERY_HOOK = """ if state=='done' then
  if not registry_inspected then
   if not registry_inspect then
    local engine = {
     is_paused=function() return C.IsGamePaused() end,
     game_time=function() return tonumber(C.GetCurrentGameTime()) end,
    }
    registry_inspect = assert(X4LAN_AUTHORITY_CLIENT_REGISTRY_INSPECT.new_x4(
     cfg.authority_registry_snapshot, engine, function() failed=true end, 300))
   end
   assert(registry_inspect:tick())
   if not registry_inspect:ready() then return end
   registry_inspected = true
  end
  X4LAN_RECOVERY_ON_PAUSED(registry_inspect)
  return
 end"""


def install(case, full, *, prepare_berth=False):
    lab = case / 'extensions/x4_lan_lab'
    installed = []
    md_sources = (*MD, 'tools/dev/authority_player_trade_berth.xml') if prepare_berth else MD
    for folder, modules in (('md', md_sources), ('ui', UI)):
        for source in modules:
            target = lab / folder / Path(source).name
            with target.open('xb') as stream:
                stream.write(read_evidence(ROOT / source, 1 << 20))
            installed.append(target)
    manifest = lab / 'ui.xml'
    anchor = '    <file name="ui/authority_recovery_probe.lua" />'
    additions = ''.join(f'    <file name="ui/{Path(source).name}" />\n' for source in UI)
    _insert(manifest, anchor, additions + anchor)
    sha = lab / 'ui/sha256.lua'
    stock_sha = ROOT / 'src/lockstep/base/ui/sha256.lua'
    if read_evidence(sha, 1 << 20) != read_evidence(stock_sha, 1 << 20):
        raise ValueError('private SHA module differs')

    config = lab / 'ui/config.lua'
    pinned = ','.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in full.items())
    with config.open('a', encoding='ascii') as stream:
        stream.write('X4LAN_CONFIG.authority_recovery_early_pause = true\n'
                     'X4LAN_CONFIG.authority_player_dock_flight = true\n'
                     f'X4LAN_CONFIG.authority_player_trade_berth = {str(prepare_berth).lower()}\n'
                     f'X4LAN_CONFIG.authority_player_dock_flight_nonce = "{full["nonce"]}"\n'
                     f'X4LAN_CONFIG.authority_player_dock_flight_epoch = "{full["epoch"]}"\n'
                     f'X4LAN_CONFIG.authority_player_dock_flight_index = {SHIP_INDEX}\n'
                     f'X4LAN_CONFIG.authority_registry_snapshot = {{{pinned}}}\n')
    probe = lab / 'ui/authority_recovery_probe.lua'
    _insert(probe, 'local C=ffi.C', 'ffi.cdef[[double GetCurrentGameTime(void);]]\nlocal C=ffi.C')
    _insert(probe, 'local state=', 'local registry_inspect;local registry_inspected=false\nlocal state=')
    _insert(probe, " if state=='done' then return end", RECOVERY_HOOK)
    return [*installed, sha, manifest, config, probe]


def run(args):
    # Import before staging. The parser is owned by the observation harness.
    from authority_player_dock_observation import observe_player_flight

    store_dir = recovery.base.local_directory(args.store)
    store = CheckpointStore(store_dir)
    source_root = store.directory.parent
    out = args.out.resolve()
    if out.is_relative_to(source_root) or source_root.is_relative_to(out):
        raise ValueError('flight output and the confirmed source must be separate')
    nonce = secrets.token_hex(16)
    full = None
    original_command = recovery.base.readonly_save_command

    def verify_source(path, checkpoint, profile):
        nonlocal full
        if path != store.directory or checkpoint.sha256 != SOURCE_DIGEST:
            raise ValueError('flight requires the pinned original confirmed checkpoint')
        _, saved = verify_previous_source(store, checkpoint, profile, require_registry_snapshot=True)
        status = ('ready', saved['epoch'], saved['issued_count'], saved['mapping_sha256'])
        full = validate_full_snapshot(saved, status, nonce, checkpoint.marker)
        index = SHIP_INDEX - 1
        if (full['epoch'] != REGISTRY_EPOCH or full['issued_count'] <= index
                or full['binding_bits'][index] != '1' or full['operational_bits'][index] != '1'):
            raise ValueError('flight Company ship is unavailable in the pinned registry')

    def protected(case, _profile):
        if full is None:
            raise ValueError('unverified flight source')
        return install(case, full, prepare_berth=args.prepare_berth)

    def command(case, game, binds, lock, display, auth, username, seed, target):
        launch = original_command(case, game, binds, lock, display, auth, username, seed, target)
        pos = launch.index('--die-with-parent')
        launch[pos:pos] = ['--ro-bind', str(target.parent), str(target.parent)]
        launch = software_vulkan.constrain(launch, display=display, authorization=auth, icd=args.vulkan_icd)
        software_vulkan.verify(launch, args.out / 'software-vulkan.log')
        return launch

    def observe(*, profile, guard, deadline, **_):
        return observe_player_flight(profile / 'engine-output.log', deadline, guard, prepare_berth=args.prepare_berth)

    def finalize(result):
        save = args.out / PROFILE_SAVE
        if save.is_dir() and any(path.name != 'save_008.xml.gz' for path in save.glob('*.xml.gz')):
            raise ValueError('flight wrote an unexpected save')
        scope = 'private assigned-berth cockpit diagnostic' if args.prepare_berth else 'private stock cockpit flight and docking diagnostic'
        result.update(scope=scope, trial_nonce=nonce,
                      ship_index=SHIP_INDEX, session_listener=False, saved=False, published=False)

    with (patch.multiple(recovery, verify_source=verify_source, extra_protected_files=protected,
                         observation_callback=observe, result_finalizer=finalize),
          patch.object(recovery.base, 'readonly_save_command', command)):
        return recovery.run(args)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for flag in ('game', 'store', 'out'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--prepare-berth', action='store_true',
                        help='After a piloted approach and stock docking request, place the ship on its verified assigned trading bay.')
    parser.add_argument('--xvfb', default='Xvfb')
    parser.add_argument('--vulkan-icd', type=Path)
    parser.add_argument('--timeout', type=float, default=1200)
    args = parser.parse_args(argv)
    args.seed = 276944566
    args.out = args.out.absolute()
    tools = [shutil.which(name) for name in (args.xvfb, 'xauth', 'xdpyinfo')]
    icd = args.vulkan_icd or live.lavapipe_icd()
    if not all(tools) or icd is None:
        parser.error('private Xvfb, xauth, xdpyinfo and lavapipe are required')
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
