# Linux authority test-server package

This is the source package for an isolated recovered X4 internal test. The
package contains the server and pinned TLS gateway, the Lua and MD files they
install into a disposable profile, and the C source of the small UDP module.
The supervisor compiles that module on the host. The archive contains no X4
executable, game data, saves, Steam state, credentials or vendored runtime.
It uses the project's noncommercial terms and notices in the archive root.

## Prepare the host

Use an unprivileged Linux x86-64 account with Python 3.11+, Bubblewrap with
user namespaces, `cc` and libc development headers. Install `Xvfb`, `xauth`,
`xdpyinfo`, `vulkaninfo`, `/usr/bin/find`, Mesa lavapipe and the Linux runtime
libraries required by X4. Keep the native Steam client running and authenticated
under the same account. The supervisor checks that `~/.steam/steam.pid` names
that account's live `steam` process. Close existing X4 instances before the test.

Use the licensed Linux X4 9.00 installation from Steam build 23660954. The
supervisor checks the executable SHA-256
`113f73d74570084f6ce18a659b365009eccf579fb66dbabb5dad2b1718045f20`.
It mounts the game and compatible installed DLC read-only. Keep the host's
normal Steam and X4 profile in place.

Extract into a directory owned by the operator and create the private working
directory.

```sh
tar -xzf /path/to/x4-authority-test-server.tar.gz
cd x4-authority-test-server
mkdir -m 700 -p .local
```

## Restore the disposable test world

Obtain the separate private format-3 seed ZIP from the test owner. Restore
it into a fresh directory under `.local`. The tool checks the seed's file
hashes, checkpoint marker and saved registry evidence.

```sh
python3 tools/dev/authority_portable_seed.py restore \
  --archive /path/to/private-format3-seed.zip --out .local/source-run
```

If the test owner supplies an ordinary confirmed supervisor fixture instead
of the format-3 ZIP, stage its source files with this layout.

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
  --out .local/provider-run-01 --timeout 1800 --require-registry-snapshot
```

Use `--require-registry-snapshot` for the portable format-3 seed. Omit it for
the ordinary fixture shown above. The saved DLC selection must match the
licensed game installation.

The wrapper starts a private Xvfb display. Before launching X4 it checks the
device list and Mesa software Vulkan inside the game's Bubblewrap namespace
and records the Vulkan probe in `.local/provider-run-01/software-vulkan.log`.
It detects Xvfb on `PATH` and the system lavapipe ICD. To specify installed
alternatives, pass `--xvfb /absolute/path/to/Xvfb` and
`--vulkan-icd /absolute/path/to/lvp_icd.json`.

The default publishes a private loopback `endpoint.json`. For a remote
fixture client, append `--tls-bind`, `--tls-port`, `--tls-advertise`,
`--tls-certificate`, `--tls-private-key` and `--tls-shared-key-file` to the
same provider command. Use a client-reachable advertised address and keep
the generated `connection.json` private.
See the [TLS settings](../tools/dev/AUTHORITY-RECOVERED-LOOPBACK-SERVER.md#optional-tls-gateway-for-the-windows-fixture-client).

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

Use Ctrl+C, SIGTERM or the configured timeout to stop the trial. Give the
supervisor time to settle participant departures and finish the final save.
In the server run's `result.json`, check `success: true`,
`final_world_state: "paused"`, an empty `owned_processes_remaining` and a
non-null `final_checkpoint_id`. The checkpoint should match the store's
`CONFIRMED` pointer.

## Build the archive again

From a Git checkout with the provider launcher and packaging commit:

```sh
python3 tools/make_authority_server_package.py --ref HEAD \
  --out /path/to/x4-authority-test-server.tar.gz
```

The packager reads committed source blobs. It includes Python imports of the
recovered supervisor and its helpers, including imports inside functions,
plus explicit Lua and MD assets for the provider path. Some shared
development modules are transitively imported by the supervisor. The
standalone development experiments remain outside the provider workflow.
The archive uses an ordered Git tar and a fixed gzip timestamp. The printed
commit ID and SHA-256 identify it.
