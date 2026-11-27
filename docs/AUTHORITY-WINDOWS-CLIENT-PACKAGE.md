# Internal Windows authority client diagnostic

This package runs the Windows fixture diagnostic with a matching authority test
server. A run observes the join, checkpoint, and participant control. Company
ship selection requires a matching server and client revision. The package is
for internal diagnostic trials.

## Start from a fresh clone

Use Git, Python 3.11 or later, and Zig 0.15.2. In PowerShell, clone the
active branch and run the offline native-builder checks.

```powershell
git clone --branch test/base-game-two-pilots-20261124 https://github.com/Lith7ium/X4-Online-Aut-Server.git
Set-Location X4-Online-Aut-Server
python -m unittest discover -s tests -p test_native_build_output.py
```

Follow the [current DLL build steps](../tools/dev/AUTHORITY-WINDOWS-FIXTURE-CLIENT.md#build-the-current-network-dll)
to produce the two inputs for `-NetworkDll` and `-NetworkDllSha256`.
Use the same source revision as the server operator for a shared game test.

## Build the ZIP

Run this from the diagnostic checkout with Git and Python 3.11 or later.
Choose a committed revision containing the desired Windows client features.

```bash
python tools/make_authority_windows_client_package.py --ref HEAD \
  --out /path/to/new/x4-authority-windows-diagnostic.zip
```

To include the project-built UDP module, add `--recorded-network-dll` pointing
to the retained `x4lan_net_64.dll` from the
[November 18 build](../reports/AUTHORITY-WINDOWS-NETWORK-BUILD-20261118.md).
The builder checks that DLL's recorded SHA-256 and the selected revision's
two corresponding source digests. Otherwise the operator supplies a verified
current-source build when running the package. The ZIP reads committed Git
blobs, so checkout line-ending conversion does not change its inputs. The ZIP
records the source commit and the DLL provenance when bundled. Share the
printed ZIP SHA-256 and the server's private `connection.json` with the tester
through a trusted channel. The connection file is an external input.
The package uses the tester's installed X4, Steam, and Python.

## Run on Windows

Extract the ZIP and open PowerShell in `x4-authority-windows-diagnostic`.
Use Python 3.11+, running Steam, the pinned Windows X4 9.00 build, and the
server's private `connection.json`. Close X4 first. The launcher checks the
game executable and requires the actual `Documents\Egosoft\X4` root to exist.
By default it creates profile `45841` and stops if that path already exists.

For a Company pilot with an existing test profile, invoke the packaged
`tools\run_authority_windows_client.ps1` directly. Add `-FreshCompanyProfile`
to your normal Company flight command. It creates the first unused launcher
profile, starting with `45841` and then checking the reserved Company test
IDs. Occupied profiles can stay in place. The run report records the selected
`profile_id`. If every reserved slot is occupied, setup stops before joining.

An interactive Company launch reads the active Steam account's X4 graphics
settings into the new test profile. You can also pass the full path to your
normal `config.xml` with `-GraphicsConfig`.

Compare the archive SHA-256 with the build operator's value. The optional
connection check contacts both authenticated server services with the supplied
file. It can run before opening X4.

```powershell
python .\tools\dev\authority_connection_check.py --connection-file C:\X4Test\connection.json

.\tools\run_authority_windows_client.ps1 `
  -ConnectionFile 'C:\X4Test\connection.json'
```

During the join, the console emits one `pilot_progress` line per confirmed event.
A ship reservation identifies the selected registry index. The world marker
and cockpit control each have a separate native receipt. The server's accepted
reply confirms `participant_admitted` and can grant Company movement for the
selected ship. `first_local_resume_confirmed` records X4's first confirmed
resume after the final session check. The local motion stages distinguish
a datagram with the expected prefix, an X4-owned sender, and a valid pose
for the selected ship. `first_own_sample_acknowledged` records the server's first
acknowledgment of a local movement sample. `first_remote_pose_received`
records an incoming pose for another ship in the selected view. The final
JSON includes `confirmed_stages` on success and on failure.

Without `-Game`, the launcher checks Steam's configured libraries for
X4 Foundations. Pass `-Game 'D:\SteamLibrary\steamapps\common\X4 Foundations'`
to select a specific installation. The same pinned executable check applies
in both cases.

`-WorkRoot` is an existing writable directory on the game drive. The default
is the parent of the X4 installation. Each invocation picks fresh sibling
view and run paths there. The Python workspace creates them with private ACLs
and preserves them after the run.

Add `-ChooseCompanyShip` when the operator confirms that the packaged client
and server both support company ship selection. Use an interactive PowerShell
terminal so the Python client can read the choice. After admission, a Company pilot stays connected until they exit X4.
The join and individual network operations still have time limits.
For a timed diagnostic, pass `-ObserveSeconds` and `-TimeoutSeconds`
to set the observation and total run durations (up to one hour). Omit the flag for the
ordinary fixture join. For a source-only package, add `-NetworkDll` with the
trusted Windows UDP module path and `-NetworkDllSha256` with the hash supplied
by its trusted builder. The [fixture guide](../tools/dev/AUTHORITY-WINDOWS-FIXTURE-CLIENT.md)
covers the current-source DLL build, connection file, and retained artifacts.


For an internal base-game flight with visible remote cockpit movement, use a
matching package and a server with Company motion enabled.

```powershell
.\tools\run_authority_windows_client.ps1 `
  -ConnectionFile 'C:\X4Test\connection.json' `
  -BaseGameOnly -CompanyMotion -CompanyCockpitPlayback
```

Select a free ship at the terminal prompt. The same command applies to the
two- and four-Pulsar worlds. With matching builds, automatic cockpit
playback gives visible pilot ships priority over idle Company ships in
its three slots. The server sends the active pilots' ship indices and
your cockpit checks local visibility. Older providers use HUD and proximity.
After admission, motion uses a separate stream through the pinned TLS endpoint.
Target a Company ship in the cockpit or select a rendered Company ship on
the open map. Both views can stay active
while the map shows another sector. The player result records sent movement
counts and a `company_observer` section with native confirmations by view.
A confirmation identifies the remote pilot's sample and the local hull that
X4 matched. Native Windows flight and visible playback require separate
gameplay verification. The temporary motion credential stays
in memory.

For an internal flight tracking active pilots automatically, the development
branch supports `-CompanyMotion -CompanyCockpitPlayback -CompanyCockpitContinuousPlayback`.
The [continuous cockpit playback guide](AUTHORITY-SERVER-PACKAGE.md#continuous-cockpit-playback-on-the-development-branch)
covers acquisition, travel speed and the five-second prediction deadline.

### Try an opt-in Company purchase

Use a matching Windows package and a private `connection.json` from a provider
started with `--company-purchases`. Add `-CompanyPurchases` to the PowerShell
command above and select your reserved Company ship. Open Trade from the
stock Docked menu, choose a single ware and quantity, review the offer, and
confirm once. The server must also see the ship at that station's external
trading berth with its Company NPC pilot aboard and an empty order queue.
Isolated native tests verified individual water purchases by WRN-302 (7354)
at BIM-169. The provider processes purchases serially and waits for native
confirmation and a committed ledger entry before a following order. The
Windows cockpit flow and consecutive orders still need live verification.
Reopen and review the stock offer before confirming again after any price
change, including a lower price. If the server refuses the booking because
it cannot verify the berth, needs a captain, or is busy, resolve the issue
and reopen Trade before submitting a fresh basket.

Add `-CompanyTradeView` alongside `-CompanyPurchases` to see the server's
offers, shared balance and available cargo space in the stock trade window.
For a sale, the host also enables `--company-sales` and you add
`-CompanySales` on Windows. Dock at a station buying a ware you carry,
choose one ware and confirm the quantity once. Reopen Trade to refresh
an expired sell offer. The sale and the refreshed balance have been
observed on Linux. This Windows flow still needs an in-game test.

If the game says it is awaiting reconciliation, leave the request pending.
With the original local basket record, the same Windows account and device
can answer fresh server checks even after reconnecting in another Company
ship. Use the same server certificate and Company registry. A pilot-reported
notice records the basket amount you saw. Native confirmation is required to
verify the actual debit, balance and cargo. A missing local record leaves the
purchase unresolved. The server retains supported purchase decisions with its
verified checkpoints. This route has offline integration coverage. A live
two-player Windows purchase awaits verification.

If you disconnect while waiting for a receipt, reopen the Company trade
window after rejoining. Check the balance and cargo before ordering again.
A confirmed purchase can be saved even if its receipt missed your cockpit.

For the separate server-confirmed departure experiment, use a matching
build that lists `-CompanyUndockTest` in addition to `-CompanyPurchases`.
The previously distributed `d0756d51` Windows package predates this
option. Native Windows departure still needs verification.
[Departure test instructions](AUTHORITY-SERVER-PACKAGE.md#try-server-confirmed-undocking-after-a-purchase)

### Observe a short cross-sector flight

For the older parked-ship fixture, the Windows observer chooses WOO-831 (1970)
and the Linux pilot chooses WRN-302 (7354). After both cockpits are admitted
and the session resumes, the observer opens WRN's sector on the stock map
and selects WRN. Keep that selection while the pilot flies briefly and stops.
Allow the player sessions to finish normally.

In the Windows result, `company_observer.slots.map.last_confirmed` shows
the latest confirmed sample, which may already be stationary. The same
slot's `last_nonzero_velocity_confirmed` preserves the latest confirmed
sample that carried a nonzero velocity, including its ship, sector,
generation, sequence, clocks, position and velocity. It is `null` until a
matching sample is confirmed in that slot. In builds with sender receipts,
the pilot's result records the last sample acknowledged by the movement
service under `company_motion.last_acknowledged_sample`. The field
`last_nonzero_velocity_acknowledged` retains the last acknowledged sample
with a nonzero velocity even after the pilot stops. Each field first appears
after its corresponding acknowledgement. Match the registry epoch, ship
index, generation and sequence when comparing a sender receipt with a
confirmation from the observer. This measures delivery to the selected local
hull. Verifying visible motion also requires a later read of its actual pose.

### Two-pilot cockpit playback

Use this invocation with the original two-Pulsar seed to select KHI-630
(1074) and follow the other pilot in HVC-420 (1073). For the four-Pulsar
world or another checkpoint, use the two pilots' indices from the offered
ship list.

```powershell
.\tools\run_authority_windows_client.ps1 `
  -ConnectionFile 'C:\X4Test\connection.json' `
  -BaseGameOnly -CompanyMotion -CompanyShipIndex 1074 -CompanyCockpitPlaybackIndex 1073
```

Start with both ships near their saved positions in the same sector. Target
the other pilot on the HUD, then clear the target once movement is visible
and check that playback continues within radar range. The map remains
separately selectable. The game log records `AUTH_COMPANY_COCKPIT_READBACK`
with the active HUD or radar mode. If the other pilot disconnects during
steady flight, check that the visible ship stops receiving corrections within
five real seconds of the last usable sample. Two-machine validation is pending.

The provider report lists each reserved ship under `company_motion_participants`.
During a flight, compare that ship's `max_source_step_metres` (largest change
between consecutive positions in the same sector) and `max_reported_speed_mps`
with the observing game's `AUTH_COMPANY_COCKPIT_READBACK` entries.
A zero measures a stationary sample or pair. A null means the provider had
no eligible pair of positions or no reported velocity for that measurement.

### Capture native hits on the admitted ship (diagnostic)

`-CombatCapture` records hits while you control the Company ship you selected.
In the example below, replace the work directory with an existing directory
on your game's drive and use a ship index from your own world.

```powershell
.\tools\run_authority_windows_client.ps1 `
  -ConnectionFile 'C:\X4Test\connection.json' `
  -WorkRoot 'D:\SteamLibrary\steamapps\common' `
  -FreshCompanyProfile -CompanyShipIndex 1074 -CombatCapture
```

After X4 stops, the launcher reads the verified pilot log and writes
`combat-capture-receipt.json` in the private run directory. Its
`combat_capture` result names the checkpoint ID, SHA-256, registry
epoch, reserved ship index, receipt path and captured damage sequences.
If the diagnostic fails, `receipt_error` records the error type.

Keep the receipt and the original pilot log together. On the provider,
`tools/dev/authority_combat_hit_evidence.py` checks the capture window
and the ship identities in the offered save. Pass `--capture-epoch`
and `--piloted-ship-index` to verify all hits in one scan.

To rebuild a receipt from a retained log, run the packaged utility. Take the
four values from the `combat_capture` block. The utility never overwrites an
existing receipt:

```powershell
python .\tools\dev\authority_combat_capture_receipt.py --checkpoint checkpoint-... `
  --sha256 ... --registry-epoch ... --piloted-ship-index 1074 `
  --log "$env:USERPROFILE\Documents\Egosoft\X4\45851\x4authority.log" --out C:\X4Test\receipt.json
```

### Run the isolated HUD playback diagnostic

Use an isolated world and matching playback-capable packages. Get both pilots
into the same sector and within cockpit sensor range. The observer flying
WOO-831 uses `-CompanyMotion -CompanyHullPlaybackIndex 7354` as the motion
and playback options, then selects WRN-302 in the cockpit HUD. For other saves,
use the registry index offered for the other pilot's company ship.

The trial follows that existing ship and reads its position back on a
later game update. It excludes the locally controlled ship and map-only
selections. If motion data stops arriving while the ship remains visible,
the observer can predict for up to five real seconds from the last complete
sample using its measured velocity. The experiment limits each position
correction to 50 m. After five seconds the ship receives no more position
corrections until a newer complete sample from the same pilot passes the
cockpit and distance checks again. An ordinary socket close during play
uses the same local timeout as an interrupted link. The server releases
the departing pilot's seat immediately. A pause, a lost HUD binding or a
confirmed change of pilot ends the trial. The native log records the first
position readback and the first readback showing at least one metre of displacement under
`AUTH_COMPANY_SHIP_PLAYBACK_READBACK`. The Windows game path awaits a
two-machine trial.
