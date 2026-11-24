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
