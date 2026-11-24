"""Join a pinned-TLS X4 authority server from a private Linux X4 profile."""
import argparse
import json
import os
from pathlib import Path
import sys

import authority_client_native as native
from authority_client_options import prompt_company_ship, read_connection_settings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--connection-file', type=Path, required=True,
                        help='owner-only connection.json supplied by the server operator')
    parser.add_argument('--game', type=Path, required=True, help='pinned native X4 installation')
    parser.add_argument('--out', type=Path, required=True, help='new run directory under .local')
    parser.add_argument('--display', default=os.environ.get('DISPLAY'),
                        help='local X11 or Xwayland display, defaults to DISPLAY')
    parser.add_argument('--company-ship-index', type=int,
                        help='choose a specific free index instead of prompting')
    parser.add_argument('--timeout', type=float, default=3600,
                        help='total run budget in seconds, including join and play (maximum 3600)')
    args = parser.parse_args(argv)
    if args.company_ship_index is not None and not 1 <= args.company_ship_index <= 200000:
        parser.error('--company-ship-index must be between 1 and 200000')
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
        selector = prompt_company_ship if args.company_ship_index is None else selected_index
        result = native.run(args, player_settings=settings, select_ship=selector, on_admitted=joined)
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
    print(json.dumps(result))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
