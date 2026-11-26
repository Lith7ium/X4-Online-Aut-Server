"""Join a pinned-TLS X4 authority server from a private Linux X4 profile."""
import argparse
import json
import os
from pathlib import Path
import sys

import authority_client_native as native
from authority.company_npc_measurement import validate_measurement_trial
from authority.company_motion_source import validate_npc_observation, validate_playback_index
from authority.experimental_remote_load_client import SessionFull
from authority_client_options import read_connection_settings, terminal_company_ship_selector


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--connection-file', type=Path, required=True,
                        help='owner-only connection.json supplied by the server operator')
    parser.add_argument('--game', type=Path, required=True, help='pinned native X4 installation')
    parser.add_argument('--out', type=Path, required=True, help='new run directory under .local')
    parser.add_argument('--display', default=os.environ.get('DISPLAY'),
                        help='local X11 or Xwayland display, defaults to DISPLAY')
    parser.add_argument('--base-game-only', action='store_true',
                        help='exclude official DLC from the private game view and profile')
    parser.add_argument('--company-ship-index', type=int,
                        help='choose a specific free index instead of prompting')
    parser.add_argument('--company-motion', action='store_true',
                        help='opt into the server’s separate authenticated company motion channel')
    parser.add_argument('--combat-capture', action='store_true',
                        help='record shield and hull hits locally while piloting the reserved ship')
    parser.add_argument('--company-purchases', action='store_true',
                        help='opt in to authority-confirmed Company cockpit purchases')
    parser.add_argument('--company-fleet-orders', action='store_true',
                        help='request stock Company fleet order control from the provider')
    parser.add_argument('--company-trade-view', action='store_true',
                        help='load current server offers and funds in the docked Company trade window')
    parser.add_argument('--company-sales', action='store_true',
                        help='opt in to authority-confirmed sales in the Company trade window')
    parser.add_argument('--company-undock-test', action='store_true',
                        help='isolated test: require authority confirmation before stock undock')
    playback = parser.add_mutually_exclusive_group()
    playback.add_argument('--observe-registered-npc', nargs=2, metavar=('INDEX', 'CODE'),
                          help='read one visible registered NPC through the Company motion channel')
    playback.add_argument('--company-cockpit-playback', action='store_true',
                          help='follow up to three visible other pilots by HUD target or nearby cockpit radar (requires --company-motion)')
    playback.add_argument('--company-cockpit-playback-index', type=int,
                          help='follow the other pilot by HUD target or nearby cockpit radar (requires --company-motion)')
    playback.add_argument('--company-hull-playback-index', type=int,
                          help='isolated test only: follow another company ship in the same cockpit sector')
    npc_trial = parser.add_mutually_exclusive_group()
    npc_trial.add_argument('--trial-registered-npc-measurement', action='store_true',
                           help='send read-only native radar samples of MFQ-419 from the vanilla HVC-420 fixture')
    npc_trial.add_argument('--trial-registered-npc-align', action='store_true',
                           help='isolated vanilla HVC-420 pilot only: align MFQ-419 once after its verified hold')
    npc_trial.add_argument('--trial-registered-npc-hold', action='store_true',
                        help='isolated vanilla HVC-420 pilot only: briefly hold and release MFQ-419 on radar')
    parser.add_argument('--timeout', type=float,
                        help='optional total run budget in seconds, including join and play (maximum 3600)')
    args = parser.parse_args(argv)
    if args.company_fleet_orders and not args.company_purchases:
        parser.error('--company-fleet-orders requires --company-purchases')
    if args.company_sales and not (args.company_purchases and args.company_trade_view):
        parser.error('--company-sales requires --company-purchases and --company-trade-view')
    if args.company_trade_view and not args.company_purchases:
        parser.error('--company-trade-view requires --company-purchases')
    if args.company_undock_test and not args.company_purchases:
        parser.error('--company-undock-test requires --company-purchases')
    if args.company_ship_index is not None and not 1 <= args.company_ship_index <= 200000:
        parser.error('--company-ship-index must be between 1 and 200000')
    cockpit_radar_playback = args.company_cockpit_playback or args.company_cockpit_playback_index is not None
    playback_index = args.company_cockpit_playback_index if args.company_cockpit_playback_index is not None else args.company_hull_playback_index
    try:
        observed_npc = validate_npc_observation(getattr(args, 'observe_registered_npc', None),
                                                company_motion=args.company_motion, own_index=args.company_ship_index)
        hold_npc = args.trial_registered_npc_hold or args.trial_registered_npc_align
        validate_measurement_trial(args.trial_registered_npc_measurement, base_game_only=args.base_game_only,
                                   observed_npc=observed_npc, pilot_index=args.company_ship_index)
        if hold_npc and (not args.base_game_only or args.company_ship_index != 1073
                                                or observed_npc != (943, 'MFQ-419')):
            raise ValueError('NPC hold trial requires the vanilla HVC-420 pilot and observation of MFQ-419')
        validate_playback_index(playback_index, company_motion=args.company_motion,
                                own_index=args.company_ship_index, cockpit_radar_playback=cockpit_radar_playback)
    except ValueError as error:
        parser.error(str(error))
    if not args.display:
        parser.error('a local X11 or Xwayland DISPLAY is required')

    def selected_index(_ships, _poll):
        return args.company_ship_index

    def joined(index):
        print(f'Joined with company ship {index}. Close X4 or press Ctrl-C to leave.',
              file=sys.stderr, flush=True)

    args.internal_native_client = True
    args.seed = 276944566
    args.keepalive_interval = 5
    args.archived_identity_fixture = None
    args.archived_identity_fixture_sha256 = None
    args.expected_content_sha256 = None
    try:
        settings = read_connection_settings(args.connection_file, require_private=True)
        selector = terminal_company_ship_selector if args.company_ship_index is None else selected_index
        motion_options = {'company_motion': True} if args.company_motion else {}
        if args.combat_capture:
            motion_options['combat_capture'] = True
        if args.company_purchases:
            motion_options['company_purchases'] = True
        if args.company_fleet_orders:
            motion_options['company_fleet_orders'] = True
        if args.company_trade_view:
            motion_options['company_trade_view'] = True
        if args.company_sales:
            motion_options['company_sales'] = True
        if args.company_undock_test:
            motion_options['company_undock_test'] = True
        if observed_npc:
            motion_options['observed_npc'] = observed_npc
        if args.trial_registered_npc_measurement:
            motion_options['registered_npc_local_measurement'] = True
        if hold_npc:
            motion_options['registered_npc_hold_trial'] = True
        if args.trial_registered_npc_align:
            motion_options['registered_npc_alignment_trial'] = True
        if playback_index is not None:
            motion_options['company_hull_playback_index'] = playback_index
        if cockpit_radar_playback:
            motion_options['cockpit_radar_playback'] = True
        result = native.run(args, player_settings=settings, select_ship=selector, on_admitted=joined,
                            **motion_options)
    except (OSError, ValueError, RuntimeError, RecursionError, KeyboardInterrupt) as error:
        known = {'explicit_unprivileged_linux_opt_in_required', 'native_main_thread_required',
                 'pinned_game_or_namespace_unavailable', 'local_x11_display_required',
                 'readable_xauthority_required', 'native_udp_compiler_unavailable', 'invalid_native_budget'}
        message = str(error)
        if error.args == (message,) and message in known:
            reason = message
        else:
            reason = 'invalid_connection_file_or_player_options'
        result = dict(success=False, error_class='player_client_refused', error_code=reason)
    if not result['success'] and result.get('error_code') == SessionFull.code:
        print(SessionFull.message, file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
