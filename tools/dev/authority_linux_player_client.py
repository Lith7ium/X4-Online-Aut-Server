"""Join a pinned-TLS X4 authority server from a private Linux X4 profile."""
import argparse
import json
import os
from pathlib import Path
import sys

import authority_client_native as native
from authority.experimental_remote_load_client import SessionFull
from authority_client_options import (
    company_channel_options, company_trade_options, company_view_options,
    read_connection_settings, terminal_company_ship_selector,
)


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
    parser.add_argument('--company-combat', action='store_true',
                        help='report throttled stock gun contacts (requires --company-motion)')
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
    hold_npc = args.trial_registered_npc_hold or args.trial_registered_npc_align
    try:
        channel_options = company_channel_options(args)
        trade_options = company_trade_options(args)
        if args.company_ship_index is not None and not 1 <= args.company_ship_index <= 200000:
            raise ValueError('--company-ship-index must be between 1 and 200000')
        company_options = {**channel_options, **trade_options, **company_view_options(args, hold_npc=hold_npc)}
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
        if hold_npc:
            company_options['registered_npc_hold_trial'] = True
        if args.trial_registered_npc_align:
            company_options['registered_npc_alignment_trial'] = True
        result = native.run(args, player_settings=settings, select_ship=selector, on_admitted=joined,
                            **company_options)
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
