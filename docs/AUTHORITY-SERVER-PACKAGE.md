# Linux authority server and player source package

This is the source package for an isolated recovered X4 internal test. The
package contains the server, Linux player and pinned TLS gateway, plus the Lua
and MD files they install into disposable profiles. The package also contains
the C source for the small UDP module, which each launcher compiles on its host. The archive contains no X4
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
normal Steam and X4 profile in place. The current two-Pulsar seed uses the
base game. Start the provider and both clients with their base-game-only
option. The older format-3 test seed used these official extension directories:

```text
ego_dlc_boron     ego_dlc_mini_01   ego_dlc_mini_02   ego_dlc_pirate
ego_dlc_split     ego_dlc_terran    ego_dlc_timelines  ego_dlc_ventures
```

The local Steam manifest reports 34,930,397,255 bytes for the game installation
used to build this seed. Provision a complete matching Linux installation
including its game data and bundled libraries. The provider package and seed
are separate downloads.

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

Company sessions accept up to four concurrent pilots, including a host who
plays. A dedicated server avatar uses no pilot slot. A fifth pilot sees
`Session Full` before admission can pause the world. A pilot who leaves
releases their slot and ship.

## Server positions for reserved Company ships

Use `--company-ship-positions` (also accepted as `--company-pose-trial`) to
apply a pilot's flight samples to their reserved ship. Admission and movement
use the same Company ship catalogue from the confirmed checkpoint. This also
works with the catalogue from a subsequent save. Before writing a position,
the server checks the current reservation, registry binding, ownership, seat
and sector in the live game. It reads the resulting position back through MD
and the native UI. Ships must already be in the reported sector. A later
`--verify-company-pose` checks the saved positions against the recorded
movement after a cold start. The ordinary `--company-motion` option alone runs the relay. The human
flight used that relay. [Two-pilot flight report](../reports/AUTHORITY-BASE-GAME-HUMAN-FLIGHT-20261124.md)

In a separate native Linux trial, two synthetic pilots each sent one position
sample through the normal admission and motion channels. The provider
acknowledged both. HVC-420 moved 120 m along the sector X axis. After a cold
load, the native MD and UI readers both returned the requested position with
0 m error. KHI-630 remained at its original saved position after a requested
160 m move in the opposite direction. The run recorded no cause for the
missing second move.

For new pose trials, `company_pose_trial.application_drops` in `result.json`
counts observed discards and keeps the latest ship index, source sequence and
seconds since server acceptance under `by_reason`. The `last_reason` field
identifies the most recent discard across all reasons. `queued_expired` records
samples that reached the three-second freshness limit while waiting.
`queued_reassigned` and `queued_evicted` record removal on ship reassignment
and queue capacity eviction. Reasons beginning with `before_inspect_` or
`after_inspect_` distinguish expiry from a batch or running-window
invalidation at the named check. A disconnect can leave an already accepted
sample eligible until expiry or reassignment. Routine replacement of a
pending pose with a newer sample from the same ship is excluded.

## Starting ships for human flight tests

The base-game test starts two **Pulsar Vanguards** 3 km apart in Black Hole
Sun V. Each has six Bolt Repeaters Mk2, three Paranid combat engines Mk3,
a Teladi shield Mk3 and combat thrusters Mk3. The server avatar stays on
a station. The pilots choose separate ships from their shared Company.
Use the verified private Pulsar seed for this setup. Both Linux launchers
take `--base-game-only`. The Windows PowerShell launcher takes `-BaseGameOnly`.
[Fighter equipment definition](../tools/dev/fixtures/vanilla-flight/libraries/loadouts.xml)

### Earlier Timelines test world

This older fixture requires Timelines and uses
`ship_ter_s_xperimental_01_a_macro` for both seats.
For seed preparation, the [shared shuttle loadout](../tools/dev/fixtures/human-flight/libraries/loadouts.xml)
is the fixed equipment definition from the LAN flight tests. It fills all
six gun mounts with Gamma HEPTs, both engine mounts with Frontier engines
and the shield mount with a Frontier Mk5 shield. It includes Mk3 combat
thrusters, Mk2 docking and scanner software, flight assist, targeting and
trade software.

Start with the private parked format-3 archive from the November 24
WOO-831 / WRN-302 trial. Use a fresh directory for each run and keep the
source stopped. Set `game` to the pinned installation and `parked_archive`
to the archive's absolute path.

```sh
mkdir -p .local
python3 tools/dev/authority_portable_seed.py restore \
  --archive "$parked_archive" --out "$PWD/.local/shuttle-source"
tools/run_authority_server.sh --game "$game" \
  --store "$PWD/.local/shuttle-source/checkpoints" \
  --out "$PWD/.local/shuttle-prepare" --company-shuttle-pair prepare
tools/run_authority_server.sh --game "$game" \
  --store "$PWD/.local/shuttle-prepare/checkpoints" \
  --out "$PWD/.local/shuttle-verify" --company-shuttle-pair verify
```

Check `success` in both runs' `result.json`. The prepare run creates and
saves the pair. The verify run loads that checkpoint in a fresh X4 process
and reads the two hulls. Both runs use disposable profiles. The cold result
records each ship's code and index under `company_shuttle_pair.pair.ships`.
Copy the two verified indices into the Linux and Windows invitations, with
each observer targeting the other pilot's index. Check both seat choices and
mutual cockpit targeting with the actual clients.

Export the stopped preparation after the cold run succeeds. Use this private
archive for ordinary hosting.

```sh
python3 tools/dev/authority_portable_seed.py export \
  --source "$PWD/.local/shuttle-prepare" --archive "$PWD/.local/shuttle-pair.zip"
```

## Prepare the parked server avatar for the two-ship test

Use the specific confirmed two-ship format-3 seed supplied for this experiment.
The supervisor verifies its checkpoint identity before preparing a fresh copy.
Both modes use the provider's private software-rendered display. They run once
without opening a game-session listener or publishing `connection.json`.

Prepare from the restored seed, then run the read-only check from the stopped
preparation output. Keep the preparation directory until verification finishes.

```sh
game='/path/to/X4 Foundations'
tools/run_authority_server.sh --game "$game" \
  --store "$PWD/.local/source-run/checkpoints" \
  --out .local/station-park-prepare --park-server-avatar prepare
tools/run_authority_server.sh --game "$game" \
  --store "$PWD/.local/station-park-prepare/checkpoints" \
  --out .local/station-park-verify --park-server-avatar verify
```

Check both `result.json` files for success. The verify run reloads the parked
avatar and checks that the two Company ships remain available. It leaves the
prepared checkpoint unchanged. Use the prepare directory for the separate
portable export. Restore that archive normally and host without
`--park-server-avatar`.

```sh
python3 tools/dev/authority_portable_seed.py export \
  --source "$PWD/.local/station-park-prepare" \
  --archive "$PWD/.local/parked-two-ship-seed.zip"
```

## Join the two-ship Company test

Use matching server and client sources and the separate private format-3 seed
with two Company ships. Start the provider with `--require-registry-snapshot`,
the TLS settings above and `--company-motion`. The control, file and
authenticated movement channels use the same advertised TCP port. Supply
`connection.json` to the testers over a private channel.

On the Linux player machine, use the pinned X4 installation and matching DLC.
Start from a local X11 or Xwayland display with readable Xauthority. The
launcher creates a disposable profile and keeps the game window on that
display. Use a different output path for each player:

```sh
tools/run_authority_client.sh --company-motion \
  --connection-file /path/to/connection.json \
  --game '/path/to/X4 Foundations' --out .local/player-run-01
```

Keep the connection file owner-readable only (`chmod 600`). Select a free ship
at the prompt, or use `--company-ship-index` to name one explicitly. For the
private two-ship fixture, select WOO-831 (1970) and WRN-302 (7354) on separate
players. The separate Windows diagnostic kit joins the same provider with its
PowerShell `-CompanyMotion` option.

With matching observer builds, the player can target a company ship in the
cockpit and select another company ship on the open map, including in another
sector. The cockpit target has to remain within sensor range and high
attention. The map selection has to appear in the native rendered list.
When a remote pilot occupies an observed hull, the movement channel forwards
its current sample. The receiver records a confirmation after X4 matches the
same hull again. On exit, `company_observer.slots.cockpit` and
`company_observer.slots.map` in the player result give the counts and last
confirmed sample for each view. Each slot also retains
`last_nonzero_velocity_confirmed` after a later stationary sample arrives.
This stage reads the local hull. See the
[two-pilot flight procedure](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/experiment/authoritative-server-20261115/docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md#observe-a-short-cross-sector-flight).

For the isolated same-sector playback trial, the Linux observer can also
pass `--company-hull-playback-index 7354` with `--company-motion`. Choose
WOO-831 as the local ship and target WRN-302 in the cockpit. Match the index
to your own world if using another test save. The ordinary map observer
continues to cover the selected remote sector. Both the trial receiver and
the provider must support the opt-in loss notifications.

## Check the deployment from the host

Start a loopback run as above. Once its private `endpoint.json` appears, use a
second shell in the extracted directory:

```sh
python3 tools/dev/authority_loopback_simulated_client.py \
  --endpoint .local/provider-run-01/endpoint.json \
  --out .local/provider-probe-01 --count 1 --hold-seconds 60 --timeout 1200
```

This downloads and verifies the first offered save. The probe simulates the
client load and its participant receipts, stays connected for 60 seconds after
the resume receipt, then disconnects. The server runs a real X4 throughout.
Allow time for the final native save and registry check. Use the server's
`checkpoints/CONFIRMED` pointer to see when the new checkpoint has been confirmed.
For an additional rejoin test, use a fresh probe output path.

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
recovered supervisor and Linux player, including imports inside functions,
plus the Lua, MD and extension assets used to assemble their private overlays.
Some shared development modules are transitively imported by the supervisor. The
standalone development experiments remain outside the provider workflow.
The archive uses an ordered Git tar and a fixed gzip timestamp. The printed
commit ID and SHA-256 identify it.
