# Linux authority test-server package

This is the source package for an isolated recovered X4 internal test. The
package contains the server and pinned TLS gateway, the Lua and MD files they
install into a disposable profile, and the C source of the small UDP module.
The supervisor compiles that module on the host. The archive contains no X4
executable, game data, saves, Steam state, credentials or vendored runtime.
It uses the project's noncommercial terms and notices in the archive root.

## Prepare the host

Use an unprivileged Linux x86-64 account with Python 3.11 or newer, Bubblewrap
with user namespaces, a C compiler and standard libc development files. Install
Xvfb, xauth, xdpyinfo, Vulkan tools, Mesa lavapipe and the Linux libraries
required by X4. Install and authenticate Steam under that same account and leave
it running. Set the game argument to the operator's existing licensed Linux
X4 9.00 installation with the pinned executable from Steam build 23660954.
The game and its compatible DLC remain in that installation. The launch
namespace mounts them read-only.

A clean archive extraction does not have a recovery world. Obtain a separately
authorized, compatible, confirmed disposable fixture from the test owner.
Stage the verified source under the extraction's `.local` directory with the
fixture's original layout. The ordinary previous-supervisor source requires:

```text
.local/source-run/
  result.json
  checkpoints/
    CURRENT
    CONFIRMED
    checkpoint-<id>/
      manifest.json
      save.xml.gz
  case/
    home/.config/EgoSoft/X4/45841/content.xml
    extensions/x4_lan_lab/md/
      authority_checkpoint_marker.xml
      authority_identity_fixture.xml
```

The saved marker, proof, script bytes and installed DLC selection must pass the
existing recovery checks. Keep source writers stopped. A normal player save or
a bare save file cannot bootstrap this supervisor. If the test owner supplies
the original pinned experiment fixture, preserve
`.local/authority-tcp-recovery-01/checkpoints` and its original proof layout.

## Launch the provider test

Run from the extracted `x4-authority-test-server` directory after staging the
verified fixture. Use a new output path for each run.

```sh
tools/run_authority_server.sh \
  --game '/path/to/X4 Foundations' \
  --store "$PWD/.local/source-run/checkpoints" \
  --out .local/provider-run-01 --timeout 1800
```

The provider launcher uses a private Xvfb display and Mesa software Vulkan.
The trial still uses the game's graphics libraries. Rendering performance and
native operation on a provider host require measurement. GPU-free refers to
device access and the software driver selection. The launcher detects Xvfb on
`PATH` and the system lavapipe ICD. For alternate installed paths, use
`--xvfb /absolute/path/to/Xvfb` and `--vulkan-icd /absolute/path/to/lvp_icd.json`.

For a remote fixture client, add the explicit TLS options from the
[recovered-server guide](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#optional-tls-gateway-for-the-windows-fixture-client).
Keep the generated `connection.json` private. With no TLS flags, server
endpoints are loopback only. No router or firewall is configured by the package.

## Check the deployment from the host

Start a loopback run as above. Once its private `endpoint.json` appears, use a
second shell in the extracted directory:

```sh
python3 tools/dev/authority_loopback_simulated_client.py \
  --endpoint .local/provider-run-01/endpoint.json \
  --out .local/provider-probe-01 --count 2 --timeout 600
```

This downloads two actual checkpoints and compares their markers. The probe
simulates both client loads and the participant receipts, so it exercises the
server and transfer path without launching a second X4. Run it only against
the disposable server and check its final `result.json` after shutdown.

In TLS mode the server publishes `connection.json`. The separate connection
check verifies the TLS pin and authenticates to both backend services without
claiming a participant or launching a client:

```sh
python3 tools/dev/authority_connection_check.py \
  --connection-file .local/provider-run-01/connection.json
```

The normal launch compiles `src/native/net.c` into the disposable extension
automatically, using `cc` from the host's `PATH`. To check compiler and libc
availability independently, build a copy outside the extraction with the
same arguments:

```sh
build_dir=$(mktemp -d)
cc -std=c11 -O2 -Wall -Wextra -Werror -shared -fPIC \
  src/native/net.c -ldl -o "$build_dir/x4lan_net.so"
```

The independent output is for compiler verification. The provider still builds
the module into each fresh run, and the archive contains the source only.

Use SIGTERM or the configured timeout to stop the trial. The supervisor
processes participant departure and confirms its final checkpoint when it can.
Check the private run's `result.json`; successful process startup alone does not
verify a saved world. The clean-extraction smoke test builds the disposable
overlay and native UDP module without launching X4.

## Build the archive again

From a Git checkout with the provider launcher and packaging commit:

```sh
python3 tools/make_authority_server_package.py --ref HEAD \
  --out /path/to/x4-authority-test-server.tar.gz
```

The packager reads committed source blobs. It uses an ordered Git tar and a
fixed gzip timestamp. The printed commit ID and SHA-256 identify the archive.
