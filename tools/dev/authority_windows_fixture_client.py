"""Explicit Windows fixture join diagnostic; not a playable client launcher."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


from authority_client_options import _read_settings, prompt_company_ship, read_connection_settings
from authority.experimental_remote_load_client import SessionFull


def read_motion_request(path):
    """Read an explicit source stream for the optional one-attempt diagnostic."""
    from authority.windows_fixture_session import validate_motion_request
    request = _read_settings(path)
    validate_motion_request(request, participant_timeout=30)
    if request is None:
        raise ValueError('motion settings object required')
    return request


class ObservationWindow:
    """Start the diagnostic observation window at its first admitted poll."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.started = None

    def __call__(self):
        now = time.monotonic()
        if self.started is None:
            self.started = now
        return now - self.started >= self.seconds


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--isolated-fixture-test', action='store_true',
                        help='opt into a private fixture test with no other X4 running')
    result.add_argument('--connection-file', type=Path, required=True,
                        help='private JSON containing the verified endpoint and credentials')
    result.add_argument('--game', type=Path, required=True)
    result.add_argument('--view-container', type=Path, required=True,
                        help='absent container on the game drive; a game view is created inside')
    result.add_argument('--out', type=Path, required=True, help='absent run directory')
    result.add_argument('--network-dll', type=Path, required=True)
    result.add_argument('--network-dll-sha256', required=True,
                        help='independently verified SHA-256 of the Windows network DLL')
    motion = result.add_mutually_exclusive_group()
    motion.add_argument('--motion-config', type=Path,
                        help='optional private JSON for an existing server motion stream; one attempt only')
    motion.add_argument('--company-motion', action='store_true',
                        help='choose a company ship and use the separate authenticated motion channel')
    result.add_argument('--company-purchases', action='store_true',
                        help='opt in to authority-confirmed Company cockpit purchases')
    result.add_argument('--company-hull-playback-index', type=int,
                        help='isolated test only: follow another company ship in the same cockpit sector')
    result.add_argument('--base-game-only', action='store_true',
                        help='exclude official DLC from the private game view and profile')
    result.add_argument('--require-registry-snapshot', action='store_true',
                        help='require a marked format-3 registry and check all ship entries before joining')
    result.add_argument('--timeout', type=float, default=600, help='overall deadline in seconds (max 3600)')
    selection = result.add_mutually_exclusive_group()
    selection.add_argument('--choose-company-ship', action='store_true',
                           help='list free company ships and choose one in the terminal before joining')
    selection.add_argument('--company-ship-index', type=int,
                           help='select this exact free registry index before joining')
    observe = result.add_mutually_exclusive_group()
    observe.add_argument('--play-until-exit', action='store_true',
                         help='keep playing after a bounded Company join until X4 exits')
    observe.add_argument('--observe-seconds', type=float, default=30,
                        help='time to poll after admission; does not request a server resume')
    result.add_argument('--keepalive-seconds', type=float, default=5,
                        help='must fit the matching server keepalive configuration')
    return result


def run(args, *, on_stage=None):
    if not args.isolated_fixture_test or os.name != 'nt' or sys.version_info < (3, 11):
        raise ValueError('explicit isolated Windows fixture test with Python 3.11+ required')
    from authority.company_motion_source import validate_playback_index
    validate_playback_index(args.company_hull_playback_index,
                            company_motion=args.company_motion, own_index=args.company_ship_index)
    durations = [(args.timeout, 3600), (args.keepalive_seconds, 300)]
    if not args.play_until_exit:
        durations.append((args.observe_seconds, args.timeout))
    for value, maximum in durations:
        if not math.isfinite(value) or not 0 < value <= maximum:
            raise ValueError('invalid diagnostic duration')
    if not re.fullmatch(r'[0-9a-f]{64}', args.network_dll_sha256):
        raise ValueError('invalid trusted DLL digest')
    for path in (args.game, args.view_container, args.out, args.network_dll, args.connection_file):
        if not path.is_absolute():
            raise ValueError('absolute diagnostic paths required')
    settings = read_connection_settings(args.connection_file)
    motion_request = None
    if args.motion_config is not None:
        if not args.motion_config.is_absolute():
            raise ValueError('absolute motion settings path required')
        motion_request = read_motion_request(args.motion_config)

    # Import only for a requested Windows run, so --help and config checks do
    # not require platform-specific APIs or touch game resources.
    from authority.windows_fixture_session import run_fixture_session
    from authority.windows_fixture_workspace import WindowsFixtureWorkspace

    select_ship = prompt_company_ship if args.choose_company_ship or args.company_motion or args.company_purchases else None
    if args.company_ship_index is not None:
        if not 1 <= args.company_ship_index <= 200000:
            raise ValueError('company ship index outside the registry range')
        def select_requested_index(_ships, _keepalive):
            return args.company_ship_index
        select_ship = select_requested_index

    view_options = {'base_game_only': True} if getattr(args, 'base_game_only', False) else {}
    workspace = WindowsFixtureWorkspace(args.game, args.view_container, args.out, **view_options)
    motion_options = {'company_motion': True} if args.company_motion else {}
    if args.company_purchases:
        motion_options['company_purchases'] = True
    if args.company_hull_playback_index is not None:
        motion_options['company_hull_playback_index'] = args.company_hull_playback_index
    if args.play_until_exit:
        motion_options['play_until_exit'] = True
    return run_fixture_session(
        enabled=True, workspace=workspace, **settings,
        windows_dll=args.network_dll, dll_sha256=args.network_dll_sha256,
        deadline=time.monotonic() + args.timeout,
        keepalive_interval=args.keepalive_seconds,
        stop_requested=(lambda: False) if args.play_until_exit else ObservationWindow(args.observe_seconds),
        motion_request=motion_request,
        require_registry_snapshot=args.require_registry_snapshot or select_ship is not None,
        select_ship=select_ship, on_stage=on_stage, **motion_options,
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ['--find-game']:
        from lockstep.launcher.game import find_install
        try:
            game = find_install().resolve(strict=True)
        except (OSError, ImportError, SystemExit):
            print('Could not find X4 Foundations in Steam libraries. Pass -Game with its installed folder.',
                  file=sys.stderr)
            return 1
        print(json.dumps({'game': str(game)}))
        return 0
    args = parser().parse_args(argv)
    confirmed = {}

    def report_stage(stage, ship_index=None):
        confirmed[stage] = True if ship_index is None else ship_index
        progress = {'status': 'pilot_progress', 'stage': stage}
        if ship_index is not None:
            progress['ship_index'] = ship_index
        try:
            print(json.dumps(progress), flush=True)
        except (OSError, ValueError):
            pass  # Losing the console must not terminate the pilot session.

    try:
        result = run(args, on_stage=report_stage)
    except SessionFull:
        print(SessionFull.message, file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt) as error:
        # Exception text can contain credentials supplied by a peer or file.
        # Report only its type and source location, never locals or arguments.
        frames = traceback.extract_tb(error.__traceback__)
        site = frames[-1] if frames else None
        report = {'status': 'failed', 'error_type': type(error).__name__,
                  'failure_site': f'{Path(site.filename).name}:{site.lineno}' if site else None,
                  'confirmed_stages': confirmed, 'run_directory': str(args.out)}
        from authority.experimental_client_owner import CompanyShipChoiceDenied
        if isinstance(error, CompanyShipChoiceDenied) and error.reason in (
                'no_free_company_ships', 'company_ship_unavailable', 'stale_ship_choice',
                'company_ship_catalog_unavailable'):
            report['reason'] = error.reason
        print(json.dumps(report), file=sys.stderr)
        return 1
    print(json.dumps({'status': 'fixture_observation_complete', **result,
                      'confirmed_stages': confirmed, 'run_directory': str(args.out)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
