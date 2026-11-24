# Company ship observation on the cockpit and sector map

Build `cf57d0b1` supports two independently changing views on an admitted
pilot's existing movement connection. The first native LAN attempt on
24 November 2026 admitted one Linux pilot. A second physical Linux machine
downloaded the shared world and started X4, but exited before entering it.
Two-pilot movement and Windows Internet gameplay remain open.

## What the client observes

The cockpit observer follows the current on-screen company target. It can
retain a verified nearby target when the pilot opens the map. The map
observer follows one selected company ship that X4's renderer actually lists
on the open map, including another sector. Each slot resolves its local hull
through the saved ship register and excludes the pilot's own ship. The
launcher combines both subscriptions and checks the identity, generation,
sequence, position, optional real-time velocity and both source timestamps
on a local game receipt. Receiving a sample leaves the hull's pose untouched.

The present native implementation covers one target per view. The transport
accepts up to 256 subscribed indices across two sectors. Smooth rendering
capacity still needs to be measured in X4. Loopback tests used
real TLS, control and movement sockets, and synthetic sources to exercise
independent view changes, acknowledgements and reconnection.

## Two physical Linux machines

| Stage | Evidence |
| --- | --- |
| GPU-free authority | The provider loaded a copy of the previous flight checkpoint with 10,082 registry entries. Its source save SHA-256 was `341d9aada6a0d764eec94c36ca11f6e5fb011111411b4b289cc330314ca0f47e`. |
| Pilot on Bazzite | Native X4 controlled WRN-302 (7354) on the Radeon GPU. It recorded 1,960 position samples, including 1,957 with a measured velocity. The authority acknowledged 1,762 samples, including 1,759 with velocity. The median active capture interval was 0.117 s. The ship stayed at rest. |
| Fedora receiver | The licensed installations matched on 88 executable/content metadata checks. Two successive pinned LAN TLS transfers delivered checkpoints of 28,813,471 and 30,084,063 bytes. The first attempt identified a missing private test asset before starting X4. The second reached the X4 menu on the RTX 2060. |
| Fedora admission | The game reported that Steam shared memory belonged to a different PID namespace, then a broken cross-process pipe. The receiver exited before a load receipt, seat admission or map observation. The log records two automatic dump replies marked `Discarded=1`. |
| Last departure | The WRN client reached its wait deadline with zero input attempts and exited. The provider confirmed the paused checkpoint `checkpoint-71983b6c531543fbbba886ae879abb4e`, save SHA-256 `9cb5535437ffeb92bda6280c359e804b95250ed1d55ee0cdf4ea3f6bf29c8f15`. |

The authorized read-only check inside the running Fedora Steam Flatpak
found the expected Steam namespaces. The current isolated test launcher
requires a private mount setup unavailable in that runtime. Both clients
cleaned up their owned processes. Live delivery to a second pilot and remote
rendering still need a working receiver.
The provider previously adopted a moving ship's position and recovered it
after a restart. [Native flight and cold reload](AUTHORITY-COMPANY-FLIGHT-20261124.md)

## Native cross-sector map selection on Bazzite

A fresh native client joined the confirmed last-departure checkpoint above
and took WOO-831 (1970) in `cluster_14_sector001_macro`. Its stock map
focused and selected WRN-302 in `cluster_01_sector001_macro`. The same
actor appeared in the raw rendered component list. The ordinary observer
resolved that actor to registry index 7354 and reported map view serial 2.
The independently read UI coordinates were (−332311.188, 24156.0059,
380247.625) m, within 0.000502 m of the saved position.

Only WOO was piloted during this run. Its movement channel acknowledged
10 local samples and received zero remote snapshots. The client exited
successfully and its game and private display closed. This establishes
cross-sector selection of the saved hull. The two-pilot test still needs
to compare a moving WRN source with receipts at WOO.

After WOO's departure, the four processes owned by the provider accounted
for 9.39 GiB proportional resident memory (PSS). The X4 process had reached
a resident high-water mark of 9.42 GiB. Both checkpoint pointers agreed at
measurement time. This single sample excludes the player's GPU process,
Steam and operating system. [Earlier provider sizing run](AUTHORITY-PROVIDER-20261124.md)

## Motion and velocity from a native GPU pilot

WRN subsequently joined alone from the confirmed WOO leave checkpoint. The
operator applied one focus-checked 0.5 s strafe input and confirmed release.
The ordinary client received 41 source acknowledgements, including 40 for
samples with velocity. The table shows values from the native publisher
matched to the corresponding server receipt. Each receipt identifies its
running window and sequence. The client checked the window in memory and
retained the sequence for comparison.

| Sequence | Observation |
| --- | --- |
| 16 | At rest at sector X = −332311.1875 m. |
| 30 | X = −332327.625 m, velocity X = −5.76144 m/s. Game time 294.616166356 s and monotonic capture time 21035.384355277 s. |
| 31–32 | Measured speeds 1.07982 and 0.27487 m/s. |
| 33–57 | 23 acknowledged stationary samples over 2.726 real seconds at X = −332327.78125 m. |

Y and Z stayed fixed. The final displacement was 16.59375 m. The client
exited successfully. The provider's optional position writer was disabled
for this observation run. A second client still needs to receive these
motion values during a live flight.

The provider finished paused after three successive pilot admissions.
Across the session it recorded 1,816 source samples, including 1,811 with
velocity. The final confirmed save was 30,317,296 bytes, SHA-256
`1c7117f327df18df402295755968a19591cc238b9f38519b43348bfce3ee2ff1`.
The operator verified the file and both checkpoint pointers before stopping
the server. The supervisor exited successfully, removed its endpoint file
and reported zero owned processes. The original source save retained its digest.

## Repository checks

The strict Linux run at `b420c071` collected 5,517 tests. It reported 22
errors across 19 distinct methods and 27 skips. Eleven affected installer
tests needed the existing sector witness with pinned SHA-256
`006b07a39be4cff6a3fac7555b26d40ffe834594304c231dd659b23bfdc85349`.
Two groups of simulated process tests assumed a private archive directory
already existed. Commits `d9f0a9b2` and `fbdb6027` give those tests their
own temporary archive fixtures. All 19 affected methods passed focused
checks. Three previously skipped methods passed with the retained private
scope manifest and resolved stock XML.

Across the full run and targeted checks, **5,493 of 5,517 distinct tests
passed**. The remaining 24 need Windows (12), external reference files (11)
or the released lane tool (1). The aggregate includes checks run in separate
environments. A complete strict pass and a live Windows Internet flight
remain outstanding.
