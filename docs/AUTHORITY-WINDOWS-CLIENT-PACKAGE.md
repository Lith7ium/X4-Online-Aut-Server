# Internal Windows authority client diagnostic

This package runs the Windows fixture diagnostic with a matching authority test
server. A run observes the join, checkpoint, and participant control. Company
ship selection requires a matching server and client revision. The package is
for internal diagnostic trials.

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
current-source build when running the package. The ZIP records the source
commit and the DLL provenance when bundled. Share the printed ZIP SHA-256 and
the server's private `connection.json` with the tester through a trusted
channel. The connection file is an external input. The package uses the
tester's installed X4, Steam, and Python.

## Run on Windows

Extract the ZIP and open PowerShell in `x4-authority-windows-diagnostic`.
Use Python 3.11+, running Steam, the pinned Windows X4 9.00 build, and the
server's private `connection.json`. Close X4 first. The existing Windows
workspace checks the game executable and requires the actual
`Documents\Egosoft\X4` root to exist with profile `45841` absent. Retained
artifacts from an earlier trial need to be handled separately before repeating
the diagnostic.

Compare the archive SHA-256 with the build operator's value. The optional
connection check contacts both authenticated server services with the supplied
file. It can run before opening X4.

```powershell
python .\tools\dev\authority_connection_check.py --connection-file C:\X4Test\connection.json

.\tools\run_authority_windows_client.ps1 `
  -ConnectionFile 'C:\X4Test\connection.json'
```

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
terminal so the Python client can read the choice. This mode holds the client
for 15 minutes after admission by default, allowing time for a second player
to join. Use `-ObserveSeconds` and `-TimeoutSeconds` to set the test duration
and the overall limit (up to one hour). Omit the flag for the
ordinary fixture join. For a source-only package, add `-NetworkDll` with the
trusted Windows UDP module path and `-NetworkDllSha256` with the hash supplied
by its trusted builder. The [fixture guide](../tools/dev/AUTHORITY-WINDOWS-FIXTURE-CLIENT.md)
covers the current-source DLL build, connection file, and retained artifacts.


For servers with company motion enabled, add `-CompanyMotion` to the same
PowerShell invocation. This opens the terminal ship selection and uses the
separate stream through the same pinned TLS endpoint once the ship is admitted.
With matching server and player builds, target a company ship in the cockpit
or select a rendered company ship on the open map. Both views can stay active
while the map shows another sector. The player result records sent movement
counts and a `company_observer` section with native confirmations by view.
A confirmation identifies the remote pilot's sample and the local hull that
X4 matched. Native Windows flight and visible playback require separate
gameplay verification. The temporary motion credential stays
in memory.

### Try the opt-in Company water purchase

Use a matching Windows package and a private `connection.json` from a provider
started with `--company-purchases`. Add `-CompanyPurchases` to the PowerShell
command above and choose WRN-302 (7354). The experiment supports buying six
units of water in one immediate purchase while WRN-302 is externally docked
at Teladi station BIM-169 in Grand Exchange I. Open Trade from the stock
Docked menu, set the quantity to six, review the offer, and confirm once.
Reopen and review the stock offer before confirming again after any price
change, including a lower price.

If the game says it is awaiting reconciliation, leave the request pending.
With the original local basket record, the same Windows account and device
can answer fresh server checks even after reconnecting in another Company
ship. Use the same server certificate and Company registry. A pilot-reported
notice records the basket amount you saw. Native confirmation is required to
verify the actual debit, balance and cargo. A missing local record leaves the
purchase unresolved. The server retains supported purchase decisions with its
verified checkpoints. This route has offline integration coverage. A live
two-player Windows purchase awaits verification.

### Observe a short cross-sector flight

For the two-ship test world, the Windows observer chooses WOO-831 (1970)
and the Linux pilot chooses WRN-302 (7354). After both cockpits are admitted
and the session resumes, the observer opens WRN's sector on the stock map
and selects WRN. Keep that selection while the pilot flies briefly and stops.
Allow the player sessions to finish normally.

In the Windows result, `company_observer.slots.map.last_confirmed` shows
the latest confirmed sample, which may already be stationary. The same
slot's `last_nonzero_velocity_confirmed` preserves the latest confirmed
sample that carried a nonzero velocity, including its ship, sector,
generation, sequence, clocks, position and velocity. It is `null` until a
matching sample is confirmed in that slot. Compare these fields with the
Linux pilot's source capture. This measures delivery to the selected local
hull. Verifying visible motion also requires a later read of its actual pose.

### Try playback in the same sector

Use an isolated world and matching playback-capable packages. Get both pilots
into the same sector and within cockpit sensor range. The observer flying
WOO-831 can add `-CompanyHullPlaybackIndex 7354` to the `-CompanyMotion`
invocation above, then select WRN-302 in the cockpit HUD. For other saves,
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
