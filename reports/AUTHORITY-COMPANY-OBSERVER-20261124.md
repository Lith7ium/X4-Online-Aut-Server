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
cleaned up their owned processes. Live delivery to a second pilot, measured
velocity during flight and remote rendering still need a working receiver.
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
