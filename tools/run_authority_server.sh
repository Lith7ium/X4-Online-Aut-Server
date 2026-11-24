#!/bin/sh
set -eu
tools_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$tools_dir/dev/authority_provider_server.py" "$@"
