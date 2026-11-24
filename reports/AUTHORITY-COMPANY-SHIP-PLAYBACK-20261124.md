# Applying a pilot's motion to the existing ship in a second cockpit

A native Linux X4 instance controlled WOO-831 and observed WRN-302 in the
same sector. An isolated replay of WRN's earlier flight moved the existing
WRN ship 16.59375 m. X4 returned the recorded final position on a later
update. WOO's position stayed fixed throughout playback.

## Native trajectory replay

The trial at `e9d5d265` loaded a read-only copy of the confirmed flight
save. X4 reported the expected load marker. In this disposable world, a
stock Mission Director action moved the unoccupied WOO into WRN's sector.
The measured separation was 800 m. The test then took WOO's cockpit and
selected WRN with X4's stock HUD. The regular cockpit observer resolved
the target to registry index 7354 and independently checked its identity.

The source was ten recorded V3 samples, sequences 30–39, from a real
strafe in a separate session. The local fixture delivered them at their
recorded intervals through the observer parser and playback controller.
The receiving X4 read its actual position on later updates. All coordinates
are metres in `cluster_01_sector001_macro`.

| Measurement | Result |
| --- | --- |
| Initial WRN position | (−332311.1875, 24156.005859375, 380247.625) |
| First position correction | 16.4375 m |
| Additional displacement to the stationary position | 0.15625 m |
| Final WRN position | (−332327.78125, 24156.005859375, 380247.625) |
| Error against recorded final position | 0 m |
| WOO displacement during playback | 0 m |
| Confirmed local observations / later position reads | 10 / 22 |

For example, a later X4 read for source sequence 32 measured 0.001203 m
error against the controller's target. The game reported successful
completion. All test processes exited and the original save retained its
SHA-256. The experiment used one game and a local recorded feed. A live
Windows pilot, a network interruption between actual players and subjective
flight smoothness still require a distributed test.

Private result SHA-256 `bd753ee745b2420f4a7b3f2d36b4d3f1a4ae928a4595a48be43f7b68999b64bc`.
[Original pilot flight and sample receipts](AUTHORITY-COMPANY-OBSERVER-20261124.md)

## Five seconds without further motion samples

Lith7ium chose a five-second prediction window for connection loss. A
second native trial at `d5e27be0` delivered exactly one moving sample
(sequence 30, velocity X = −5.7614447 m/s) and kept the HUD target visible.
It measured time from reception on the observer's own monotonic clock.
The pilot's game and capture clocks remained separate.

| Seconds since the last sample | Actual WRN sector X (m) | Cumulative position writes |
| ---: | ---: | ---: |
| 1.053899 | −332332.84375 | 87 |
| 4.707748 | −332353.87500 | 451 |
| 5.053877 | −332355.53125 | 482 |
| 5.801446 | −332355.53125 | 482 |

The last write occurred at 4.995624 s. The next engine read at 5.004499 s
returned a position 0.01155 m from its target and the controller stopped.
WRN moved 21.03125 m between the first two probes, then remained at its
last position across the two post-cutoff probes. WOO stayed in its original
position. The first attempt accidentally delivered sequences 31–39 as well.
It failed the one-sample condition. The corrected fixture held just sequence
30 and an offline check drained its actual feed through 6.1 s before the
successful native run. Both runs cleaned up all owned processes and kept
the source save byte-identical.

This tests actual engine behavior under deliberate local sample loss.
Internet loss and recovery with two live pilots remain open. Private
result SHA-256 `7aa433e3936dc06b37e508ccaa27e6ce426253492b2fa70cdf668cfd37fe0701`.

## Ordinary client and gateway at `e38cfbea`

The opt-in cockpit trial runs on the ordinary Linux and Windows client paths.
The operator selects a second pilot's ship by registry index. Its cockpit
target, sector, actor and pilot generation must still agree when X4 applies
the movement. The map remains available to inspect a separate sector.
[Windows invocation](../docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md#try-playback-in-the-same-sector)

In the TLS regression, two clients claimed distinct seats. Dropping the
publisher's real control socket immediately invalidated its movement ticket.
The second pilot remained admitted, received the temporary-loss notice and
continued publishing. During this alpha, closing a running client normally
also closes the control socket, so that case follows the same prediction rule.
When the world pauses, the target leaves view or the seat changes hands,
the old track ends. After simple sample loss, playback can resume on a newer
complete sample received after the deadline. It rechecks the original
identity, the ordering of both samples and the 50 m correction limit before
moving the ship again. The network tests used real sockets and simulated
game callbacks. Reacquisition after five seconds still needs a live flight.

For the host network preflight, the router reported one active IPv4 TCP
forward for the chosen port. The host route to the Internet used Ethernet.
The production gateway and client exchanged random challenges over the
control, file and motion services, first on the LAN address and then on the
router's public address from the same LAN. Each service used certificate
pinning and the shared key with a temporary local responder. The gateway
closed after all six exchanges. A connection from an independent Internet
uplink and a Windows game admission remain outstanding.

Both internal archives are built from the same commit, `e38cfbea13bc5016cb4f8e5a06beab0f36c60a18`.
Their archived source files matched that revision. The Windows archive
also contains the previously verified project network DLL. Each tester
supplies a licensed installation of X4 and private session settings.

| Archive | Bytes | SHA-256 |
| --- | ---: | --- |
| X4-Linux-Provider-and-Player.tar.gz | 1,372,186 | `5a6b1b6846cf59fb082f72a1439d6db100e4aada49b161631590a21568d2b596` |
| X4-Windows-Player.zip | 2,593,593 | `42db7d089110aca376e360eeade82ad5e866a1be9d58f8e579eae0b19dbfd85b` |
