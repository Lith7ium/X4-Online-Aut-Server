"""Check both authority TLS service connections without launching X4."""
import argparse
import asyncio
import json
from pathlib import Path

from authority_windows_fixture_client import read_connection_settings
from authority.experimental_tls_tunnel import check_services


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connection-file', type=Path, required=True,
                        help='private JSON used by the Windows fixture client')
    args = parser.parse_args(argv)
    try:
        settings = read_connection_settings(args.connection_file)
    except (OSError, ValueError, RecursionError):
        print(json.dumps({'status': 'connection_file_invalid'}))
        return 1
    try:
        services = asyncio.run(check_services(
            server_host=settings['server_host'], server_port=settings['server_port'],
            server_fingerprint=settings['server_fingerprint'], shared_key=settings['shared_key']))
    except KeyboardInterrupt:
        return 130
    ready = all(phase == 'ready' for phase in services.values())
    print(json.dumps({'status': 'ready' if ready else 'failed', **services}))
    return 0 if ready else 1


if __name__ == '__main__':
    raise SystemExit(main())
