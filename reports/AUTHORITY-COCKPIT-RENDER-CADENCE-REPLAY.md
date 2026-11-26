# Cockpit playback at native gate-flight speeds

The offline replay used the production Lua cockpit controller, hull adapter,
motion buffer and playback from `0026960b`. The input was the same 44
acknowledged V3 wire samples from HVC-420 in the origin sector of ordinary
gate Run 07, sequences 143–192. They span 5.601 s and 8.366 km at recorded
speeds of 1,179–1,863 m/s (median 1,524 m/s). Incoming samples retain their
capture intervals with constant transport latency and no loss. The receiver
starts at the first XYZ with a stable observer, unchanged sector and zone,
and no local drift. Mocked native writes use float32 coordinates and become
visible on the following update. No X4 process was launched.

| Receiver updates | First refusal | Coverage |
| --- | --- | --- |
| 60 Hz | None. The largest requested correction was 31.060 m. | 44/44 accepted, 338 writes and 337 next-update readbacks. The controller rendered through sequence 192 and remained active. |
| 30 Hz | At 2.700 s, sequence 166 requested 50.344 m in the distance guard. Its recorded velocity was 1,510 m/s. | 18/44 accepted and 76 writes. The next sample (167) could not bind within 50 m. |
| 20 Hz | At 0.200 s, the first moving correction requested 58.937 m on sequence 144 (1,179 m/s). | 2/44 accepted and zero writes. The next available sample (146) could not bind within 50 m. |
| 60 Hz with one measured late update | At 2.756 s, sequence 167 requested 83.753 m after inserting a 55.713 ms interval at 2.700 s. | 19/44 accepted and 153 writes. The next sample (168) could not bind within 50 m and playback stayed stopped. |

Increasing the update rate to 60 Hz immediately after either constant-rate
failure did not restart the track. The mocked replica remained at its last
accepted pose, too far behind new samples to pass the initial 50 m check.
The first row begins freshly aligned. A 50 m cap gives an ideal
straight-line budget of 3,000 m/s at 60 Hz, 1,500 m/s at 30 Hz and
1,000 m/s at 20 Hz. This replay covers only the recorded speed range.
Local drift, timing jitter and variable correction intervals can use that
distance budget earlier.

The inserted interval comes from a real Linux dual-flight receiver
(`.local/dual-native-flight-v2/native-01/cockpit-1074`, native engine log's
`AUTH_COMPANY_COCKPIT_READBACK elapsed`). Its six retained intervals ranged
from 14.367 to 55.713 ms. The observer records the first three readbacks
and then throttles to one every 0.5 s, so the files do not give a continuous
render-frame trace. The 55.713 ms row applies one observed interval to the
different high-speed source segment at a chosen point. It does not show that
this exact high-speed failure occurred in live X4.

Raw replay output, the selected V3 input and the runner are in
`.local/gate-v3-receiver-replay/`.

## Velocity-based guard in a private replay

A separate copy of the receiver compared the requested displacement vector
with the previous update's known velocity multiplied by the elapsed time.
It admitted larger steps only on an established track with two complete
samples, an interval up to 100 ms, at most 250 m of total displacement
and at most 50 m of unexplained displacement. All 44 source samples passed
at 20, 30 and 60 Hz and with the inserted 55.713 ms update. The largest
accepted step was 93.168 m at 20 Hz.

Adding 120 m perpendicular to flight at 20 Hz produced a 120.004 m
unexplained displacement and the write was refused. The first bind also
refused a 120 m offset. After the last usable sample, the private receiver
issued no write beyond the five-second cutoff. These cases use a simulated
native adapter. The production guard at `f03e7f8a` still limits each step
to 50 m.


## Reproduce the tracked regression

The [44-sample trace](../tests/fixtures/company_gate_flight.tsv) keeps the
recorded sequences and velocities with relative capture times, game times,
and XYZ offsets. From the checkout root, run
`python -m unittest discover -s tests -p test_authority_company_hull_playback_x4.py`
with LuaJIT available on PATH. At both 20 and 30 Hz, the test uses the
production controller and verifies a write over 50 m and the final
sequence through next-update pose readbacks from a simulated engine.

## Bazzite native playback with recorded flight data

The receiver at `f3191645` ran pinned native X4 9.00 with RADV in a
disposable Bazzite profile. The ordinary Fedora provider at `6f401cae`
admitted KHI-630 (Company index 1074) over pinned TLS. The private
launcher supplied HVC-420 (1073) as the only recorded source in the
automatic cockpit roster. It delivered the 44 previously recorded V3
samples through the normal process-owned receiver socket. The source
coordinates were translated once to the sealed save's parked HVC
position. Sample order, relative times, displacement and velocity were
preserved. The receiver stayed in the same native radar binding.

| Measurement | Native result |
| --- | ---: |
| Source samples accepted and seen in X4 | 44 / 44 |
| Guarded stock writes / later stock XYZ readbacks | 115 / 114 |
| Later readbacks confirming a step over 50 m | 113 |
| Largest independently read step | 100.478 m |
| Largest unexplained displacement | 8.364 m |
| Largest XYZ readback error | 0.003964 m |
| Mean interval between receiver updates | 49.932 ms |

The 100.478 m step used the recorded sample 192 and a 56.301 ms render
interval. Its unexplained displacement was 0.599 m. An independent stock
read measured the new position 47.980 ms later with 0.003311 m of XYZ
error. The ship moved up to 8.508 km over the run, including the final
bounded extrapolation. The final write was still awaiting its later
readback when the cockpit closed and is excluded from the readback
counts. Every stock destination-zone query stayed in the same native
zone. This run covers Bazzite receiving-cockpit playback of a recorded
source with a real Fedora provider. A live second pilot and a native
zone crossing require separate runs.

Fedora acknowledged 50 motion samples from the receiving cockpit and
confirmed the last-pilot departure checkpoint
`checkpoint-7148cac1053f4a9dada3b3a544fba941`. The sealed source checksum
remained unchanged. Both native processes exited successfully, and the
dedicated Bazzite workload scope and both host locks cleared. The Bazzite
scope recorded zero full memory pressure and zero OOM events.

The private verification is in
`.local/velocity-cockpit-native/native-03/verified.json` (SHA-256
`a5078c3378645f88fefc7799fa967e61c71d7bca1d4b7a59e1e2bcfb38f00f6f`).

## Crossing an internal zone in the receiving cockpit

Cobalt's Windows X4 run on `a9d1b477` followed a synthetic trajectory for
754 m before the destination lookup returned a different zone in the same
sector. The receiver stopped before dispatching the crossing. The result
isolated a second cause of a stationary ship independently of render cadence.

The trial allows the next ordinary movement write across that boundary after
a verified movement readback. The observer must still identify the same
visible ship and the server roster must still list its pilot. X4 must resolve
the destination zone inside the original sector. On the following update the
receiver checks the ship in that zone, compares its actual sector-space XYZ
with the requested position (3 m tolerance), and then permits another write.
The cockpit and observed ship may occupy different zones.

The LuaJIT regression exercises the receiving controller, the roster query
and the native adapter with simulated X4 calls. It covers successful
continuation, a simultaneous zone change by the observer, lost visibility,
roster removal, incorrect XYZ or zone, and rejection of a first-write
crossing, an unknown zone and a zone from another sector. The Windows
baseline above predates this change. The [later Windows synthetic run](#windows-receiver-crossing-an-internal-zone-on-a-synthetic-route)
exercised the updated receiver in native X4. Two independently flown
cockpits have yet to exercise this crossing.

## First contact with a fast pilot

The isolated receiver can opt in with `trial_first_contact = true` in its
cockpit playback configuration. A velocity-less first packet must identify
a visible, roster-listed pilot within 50 m of the local ship position. The
next packet must identify the same ship, view and pilot grant. Both its
capture interval and the age of the first packet at dispatch must be at most
250 ms. The newer velocity must explain the displacement between packets to
within 50 m. Both the current and requested positions must be inside the
receiving cockpit’s radar range and the native move must stay in the original
zone and sector.

The receiver can then align the ship once over a distance up to 250 m. It
reads back identity and XYZ on a later update. After that check it holds
the aligned pose until the next velocity sample, then starts playing from
that pose and returns to its ordinary interpolation delay. This avoids
extrapolating a high-speed step from a single reported velocity.

The LuaJIT regression reproduces a 171 m first contact at 1,500 m/s with a
simulated engine. It also replays the complete 44-sample recorded trajectory
with its first velocity removed at 20 and 30 Hz, through sequence 192.
Loss of the pilot, inconsistent direction, stale packets, an out-of-range
destination and incorrect native readback are covered. The earlier native
8.5 km run started with velocity available.

### First contact in a native Bazzite cockpit

A separate run used the receiver at `f328ea20` and the copied two-ship
save on Fedora. The observer identified HVC-420 on its radar. The fixture
enrolled the recorded pilot and delivered 44 movement reports. Before each
of the first two deliveries the observer confirmed the actor, sector,
pilot membership and a positive current radar range.

The first report had no velocity. The second arrived 106.44 ms later,
124.62 ms apart in recorded capture time. It authorized one 146.899 m
alignment. On a later update, 5.13 ms after the write, native X4 reported
the same actor, sector and zone with 0.00281 m of position error.
All 44 reports were accepted through sequence 192. The following 116
ordinary playback readbacks had a maximum position error of 0.00391 m
and a maximum gap of 57.16 ms. The route remained in one internal zone.
Flight between independently flown Linux and Windows cockpits over the
internet still needs verification with this change.

Fedora paused and confirmed departure checkpoint
`checkpoint-ff729fab4a66408b9c88e6b135152b26` with SHA-256
`da1df283f79ec981a0e8b1e71ecf2fa9980ab8ac5eace7440e90d02f3d613776`.
Both processes exited successfully. The original save hashes were preserved.

### Windows receiver with the recorded flight

Cobalt ran `f328ea20` on native Windows X4 9.00 with the same 44 input
reports and a 40 km radar. Cobalt reports one first alignment on XPD-490
at sequence 144. A later X4 read 12 ms after the alignment differed by
0.000022 m. The last rendered sequence was 192. The receiver recorded
567 writes, zero refused writes and at most 0.0005 m of readback error.
All writes stayed in one internal zone.

**The recorded-flight run failed its overall check.** The occupied ship was
20.9799 m from its unlogged READY position, against a 0.01 m limit. Cobalt
recovered the stream-start XYZ of (-11.750, -0.050, -49.410) m from the raw
trace. The older logs contain no final XYZ for the occupied ship. The
20.9799 m displacement was the same in the post-stream probe and final
readback, so the logs cannot establish when the full displacement occurred.
This run replayed the recorded remote pilot over loopback.

### Windows receiver crossing an internal zone on a synthetic route

Cobalt reports a separate successful native Windows X4 9.00 run on
`1e6802c5` with `--trial-first-contact --synthetic --flip 1,1,-1`.
The own ship's XYZ was (0, 0, 0) m at READY, stream start, the first and
last writes and the final readback. The reported spawn, playback and
post-playback displacements were each 0.0000 m.

The receiver confirmed first contact at sequence 144 on XDE-842, actor
`0x177049`, zone `713624`. An independent native read 0.010 s later
showed 0.000020 m of position error. At sequence 148 it requested a write to
zone `2647` in sector `2645`, at (757.1167, 0.0000, 811.0773) m.
The same actor read back in zone `2647` after 0.011 s at
(757.1172, 0.0000, 811.0771) m, about 0.00054 m from the request based
on the printed coordinates.

The run recorded one crossing, 44 writes in the original zone and 501 in the
destination zone, with zero refusals and a maximum readback error of
0.0022 m. Playback reached sequence 192. The largest step was 33.2 m.
The synthetic trajectory drove the native receiver over loopback. This
result and the earlier recorded-flight failure describe separate runs.
[Cobalt's report](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795702005880359)

### Reproduce on a Windows receiver

From this experiment branch, use a built Windows UDP module that exports
`monotonic` and a fresh output path. The isolated runner admits two loopback
protocol pilots. The native cockpit receives its radar roster through the
movement service. The other pilot supplies the recorded trajectory.

```powershell
py tools/dev/authority_velocity_receiver_windows.py --trial-first-contact --trajectory tests/fixtures/company_gate_flight.tsv --network-dll $NetworkDll --out $FreshOutput
```

The runner places sample zero at the resting ship. It requires a later
first-contact readback from the same X4 ship, then ordinary playback
through the last sequence. Its results list separately any crossings for
which a subsequent native read confirmed the requested zone and position.

Preflight and the run receipt report position error for the input trajectory.
For each successive pair with a previous velocity, they score holding the
previous XYZ and projecting it with that sample's velocity over the capture
interval. Both errors use the same pairs. The receipt also scores the input
with the position-only baseline the client sends on admission and records
the first capture interval and displacement separately. These measurements
use no receiving cockpit readbacks.

## Native velocity and stopping after five seconds

A single native Linux X4 loaded a copied Company save, seated the observer in
KHI-630 and found HVC-420 on its 40 km radar. The fixture supplied the other
pilot's lateral motion at 100 m/s in one aligned zone. The ordinary Company
client does not enable these diagnostic velocity writes.

| Movement and stop procedure | Observed result |
| --- | --- |
| Set native velocity, then release it after five seconds without input | X4 reported 100 m/s during flight and 0 m/s after release. Across the release the position jumped backwards 492.561 m in 48.6 ms. The later stationary read alone had incorrectly made the first verifier pass. A second check of the same log rejected the discontinuity. |
| Keep the visible pose and native velocity together, record the reached position at cutoff, set velocity to zero, then release it on a later update | The last source report was 5.014662 real seconds before cutoff. X4 returned the same position just before and after zeroing and after release. A further read 0.651 s later agreed within 0.00035 m. KHI-630 remained stationary. |

The combined run issued 126 bounded position writes, 103 during source
silence. The largest correction from the already moving ship was 0.00175 m.
The final position write requested the current measured position and moved
the ship 0 m. All later observations were reads. Of the 126 immediate
next-render reads, the largest distance from the last write was 0.835 m.
Independent native reads about 56 ms apart recorded up to 5.773 m of
physical travel. A 50 ms read on a ship moving at 100 m/s must account
for about 5 m of expected travel before evaluating a 3 m correction
tolerance. Integrating that rule into an admitted cockpit is separate work.

The published probe differs from the successful native run in one log
label. The original also printed a pre-operation position under the name of
a dispatched velocity on zero and release. The audit used the independent
native speed and position fields. The source save hash was preserved and
X4 exited. The complete engine log SHA-256 was
`9b19aa97bb97756b670709e41d7371086262cfe0b92dd3520a753039a66667e7`.

The standalone Lua regression checks the order of the cutoff, zero,
release and readback operations:

```sh
luajit tests/check_authority_company_native_velocity.lua tools/dev/authority_company_native_velocity.lua
```
