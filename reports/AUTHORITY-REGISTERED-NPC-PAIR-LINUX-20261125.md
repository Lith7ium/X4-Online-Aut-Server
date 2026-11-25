# Registered NPC observations across two Linux X4 instances

A GPU-free Bazzite provider and a regular Linux pilot loaded independent
copies of one vanilla world. The pilot flew HVC-420 (registry index 1073).
The observation target was MFQ-419 (index 943), an Antigone freighter with
a native docking order. The input checkpoint was
`checkpoint-39a02fb17cc24fad896b3b578f9fdd08`, SHA-256
`972b596cebcac38396321477287b84e5bf8da0a4b60ec705717be061257f6c01`.
The test used the regular authenticated pilot connection and checked the
local cockpit's native radar visibility. A pilot acknowledgement records
which server pose reached that cockpit. It does not move the pilot's
copy of the freighter or measure where X4 rendered it.

| Fork revision | Provider | Observing cockpit | Confirmed last-pilot checkpoint |
| --- | --- | --- | --- |
| `5d2299f3` | Resolved the NPC after load. The motion guard rejected the running world before publication. | A nonempty view and 272 own-flight acknowledgements. Zero NPC poses. | `checkpoint-a69fd5e1661d41a18fc0137fff549907` |
| `1e8b5157` | Published the first pose. The source retired with `source_expired`. | Native radar saw MFQ-419 at 13,364 m with a 40,000 m range. Confirmed one identical pose and no velocity. | `checkpoint-00228a267c5a45ce8222b6903e403b92` |
| `3e005330` | Captured the second pose 2.199 s after the first. The source had expired 182 ms earlier. | Confirmed sequence 1 with matching actor binding and pose. | `checkpoint-c607719d2dd8470cb7fb984c79af31aa` |
| `24284592` | Published 25 native positions. No velocity met the rate interval. | 18 exact confirmations, 7 distinct positions, 17.65 km displacement. | `checkpoint-5208c84259934e7fbcb9d6e31abdf62f` |
| `0a4c4e08` | 48 native poses, 40 with a real-time rate, including two nonzero vectors. | 35 confirmations spanning 18.67 km. One nonzero rate reached the cockpit. | `checkpoint-66b943f469274265b0c9e45028768774` |
| `5f83cb22` | 48 direct native velocity observations alongside the existing V3 stream. | 53 exact confirmations. Briefly held and then restored the freighter's original order. | `checkpoint-a519b7cc16854fb08ae6e539f823992c` |
| `e1c6a7bd` | 51 provider poses matched native cockpit confirmations. No provider pose setter. | Aligned the held freighter once by 975.93 m to a fresh pose, with zero endpoint or rotation error. Restored its captain's order. | `checkpoint-840bf3138d414eeeadabb735e00b3519` |

In the first run the provider checked the identifier for a pending join
offer after resume. That identifier expires by design. The corrected
guard checks the coordinator's running checkpoint and its marker against
the independently scanned join save and current seat reservations.

In the second run the saved reference changed from `0x418ee` in the
original world to `0x41962` in the join offer. Native X4 resolved
`0x41962` for MFQ-419 after loading the offer. The cockpit confirmed
sequence 1 at (175818.84375, 305.3389892578125, 5660.52490234375) metres.
Registry epoch, index, generation, sector and both capture clocks matched.
The first frame contains no velocity. The two-second source expiry
occurred before either X4's sector-loss observation. The old receiver
checked expiry before draining its socket. The record cannot establish
whether another packet had already arrived.

The final save hashes for these runs were
`a1d1e33eab198a85d9eba287489e86af69cfee3c731889f39d864038841cd660`
and `87c2197bf1de6feb14649a79b73484b334e9d937141b94662b23f94212b90bfd`,
respectively. Both providers paused and saved after the pilot left.

In the third run the receiver drained queued packets before evaluating expiry.
The longest interval between running polls was 131 ms and there were zero stale
discards. X4 reported captures at host monotonic times 67609.074052945 and
67611.273421193. Python withdrew the source at 67611.091890567. The second
capture therefore did not exist when the source expired. Neither of the first
two samples had a velocity, and the native source logged no first derivative
before the freighter exited the sector. Its rate calculation accepts at most
one second between poses. This measurement locates the bottleneck on the
capture side for this run. The new final save SHA-256 was
`b1cb46f15266182a3106e4616738d35ccdaa4eb7b4c58e610c5b106977df2c07`.

## Reduced software rendering cost

The fourth provider used the unattended graphics preset and a matching
640×360 Xvfb viewport. Its pilot continued using the ordinary graphics profile.
During 20.146 s of server captures, the stock cockpit confirmed 18 poses
over the authenticated motion connection, including seven distinct positions.
All confirmed positions, sequences, sector, identity, generation, game times,
capture times and null velocities matched the provider. X4's cockpit radar
first saw the freighter at 13,366 m and later reported a sector loss.
This verifies that changing server positions can reach a second instance
through genuine radar interest. The pilot had no playback writer.

Successive captures confirmed by the cockpit were 1.044–1.918 s apart.
The second took 640 ms from previous capture to dispatch and a further
1,277 ms waiting for the MD reply. Dispatch itself took 0.192 ms and the
pose was read 0.152 ms after the reply. The CPU provider eventually
expired the source 2.032 s after its last capture. The normal final
checkpoint SHA-256 was
`ad051ecbb828856f8a8a0ec7231a1b5b86988b7713b713ed8ada6db0c8708413`.

During a 12.33 s sample immediately after admission, the provider X4 process
used 4.68 logical CPU cores on average and peaked at 6.78 GiB RSS / 6.70 GiB PSS.
The concurrent cockpit used 2.30 cores and 3.56 GiB RSS / 3.49 GiB PSS.
The busiest continuously observed provider thread was a Mesa llvmpipe worker
at 0.56 core. Both X4 processes ran on the same Ryzen 7 7800X3D with
overlapping CPU affinities. These measurements cover this scene and the
two X4 processes. They are insufficient to size a dedicated rental server
or establish its performance with four pilots.

## Per-position registry checks and velocity

At `0a4c4e08` the sampler can start its next eligible registry request
directly after the previous reply. Every capture still has a separate
forward and reverse registry check. Between the first two poses, the
new dispatch began 0.255 ms after the first capture, and its MD reply
arrived about 680 ms later. The 48 captures were 0.542–1.278 s apart.

All 35 native cockpit receipts matched their original provider captures,
including positions, rates and both clocks. Twenty-eight contained a
velocity. One was nonzero at (1117.540, 2.709, −255.852) m/s. The other
nonzero provider rate belonged to sequence 44, after the cockpit's last
visible sequence 35. Five native position jumps since the prior capture
would have implied 5,517–6,841 m/s along X. The existing 5,000 m/s/axis
limit suppressed those derivatives. This trial has not shown the four
different positions with nonzero velocity required by the internal test.
No cockpit position writer or physical smoothing was active.

HVC-420's cockpit stopped acknowledging the freighter after its stock radar
lost the sector. The provider paused and saved after HVC-420 left, and the
saved marker and byte hash agreed. Final checkpoint SHA-256 was
`f27e93322df588eaa27d19ae1f06d2cfc518f603f1a7195c778c6a5318b9e5e4`.

## Stock velocity and restoration of the captain's order

At `5f83cb22` HVC-420 briefly held the local MFQ-419 after receiving its
first server pose. X4 reported the temporary Wait as started. Once the
linear hold was checked, the pilot released it and cancelled its own Wait.
X4 then twice reported the previously captured order running, at separate
game times. This sequence completed once. The offered save's active order
was DockAt in a TradeRoutine. The provider kept simulating the freighter
throughout. No pilot pose setter ran. Its only local save remained
byte-identical to the original join offer.

The provider also measured the stock MD ship-local `velocity` vector and
the ship's sector-relative right, up and forward directions. All 48
native speeds were nonzero at 1,032.9–1,147.6 m/s. Of the corresponding
transmitted finite-difference rates, 36 were zero and ten were absent.
The pilot received 53 poses in total and confirmed three nonzero rates.
The original threshold of four distinct moving positions with nonzero
velocity remains open.

The three basis vectors had maximum norm error below 0.000001 and
pairwise absolute dot product below 0.000001. Projecting the native
velocity onto sector axes gave flight directions within 0.003° of
seven displacements between successive changes in MD position. Three
such intervals longer than three seconds had integrated game-time
distance errors of +82 m, −316 m and +92 m over 3.37–3.81 km. Intervals
ending between simulation steps could differ by over 3 km. Nine of the
48 same-sequence UI poses already showed the next position, despite
game timestamps differing by less than 0.48 ms. A wire implementation
must specify when it took the velocity and how it relates game time to
elapsed real seconds. This run recorded it locally.

The last-pilot checkpoint hash was
`13a262385827f8d26522cb4d3232c904ee36fecd6d660224f29e4b89cd7e7f89`.

## One native position correction on the observing pilot

At `e1c6a7bd` the Linux pilot prepared the temporary Wait on sequence 1,
then used a fresh sequence 2 from the same generation after X4 confirmed
the hold. The provider captured that pose 199.785 ms after the hold check.
The pilot transport received it 94.173 ms after capture. Both instances
ran on the same host for these latency measurements. The freshness check
itself used the receiving host's monotonic clock.

The local MFQ-419 moved 975.927 m to (175818.84375, 305.3389892578125,
5660.52490234375) m. X4 read back zero endpoint error and zero angular
error. The target, sequence, generation and timestamps matched the original
provider publication. The captain's previous order resumed. HVC-420's
first and last native samples had the same identity and coordinates.
Only one alignment was issued. The later real radar loss ended receipt.
These are native position readbacks from two Linux processes and cover
one correction. Continuous cockpit playback and the Windows WAN path
remain to be tested.

All 51 native receipts matched. The disposable client saved no new world.
The provider confirmed the final marker and SHA-256
`3cbf3252978fbc9f8ec121ca5866d4d9e1d5cc0619ca7120556159fd3a9181a4`.

### One-step velocity prediction against native low-attention positions

The alignment run also retained 48 native MD velocity/basis samples from
MFQ-419. All 48 had a corresponding provider publication and pilot receipt.
For each adjacent pair we projected the **earlier** velocity into sector
axes and advanced the earlier MD position by the elapsed **game** seconds.
The elapsed times were 0.515–1.000 s. The reference endpoint was the next
native MD position. The projected velocities were never sent to the pilot.

| Adjacent positions | Within 50 m | Median error | 90th percentile | Maximum |
| --- | ---: | ---: | ---: | ---: |
| All | 0/47 | 623 m | 2,493 m | 3,221 m |
| Unchanged | 0/39 | 616 m | 695 m | 1,133 m |
| Changed | 0/8 | 2,495 m | 3,221 m | 3,221 m |

Percentiles use nearest rank. The shortest error on a changed step was
157 m. The velocity gave a plausible direction over several kilometres,
but advancing it continuously did not predict the next low-attention
sample within the existing 50 m correction budget. A cockpit-side native
trajectory in high attention still needs separate measurement.

### Simultaneous pilot and provider trajectories

At `4deb8ecf`, two Linux X4 processes loaded the same world. The pilot's
stock radar identified MFQ-419. While the pilot's own engine simulated the
freighter, a read-only observer sampled its local position, attention and
velocity. The provider independently sampled its version of the same ship.
Their host monotonic clocks allowed the capture windows to be compared.

| Over the pilot's capture window | Pilot | Provider |
| --- | ---: | ---: |
| Position samples | 160 | 34 |
| Distinct positions | 160 | 7 |
| Consecutive samples at the same position | 0/159 | 27/33 |
| Median elapsed time between samples | 126 ms | 547 ms |

The pilot reported 40 stock attention readings of `inzone` and 120 of
`visible`. Every sample met the stock visible threshold. All were below
the nearby threshold. The pilot's engine reported 1,132–1,148 m/s.
Projecting the previous stock velocity across one interval predicted
the next local position with a median error of 0.017 m using game time
(90th percentile 0.032 m). Using real elapsed time gave 2.59 m and
8.03 m. Holding the previous position gave a median error of 144.49 m.
These errors compare successive positions in the same simulation.
They do not measure agreement between the two worlds.

The bounded measurement finished before the target naturally left radar.
The provider then saved `checkpoint-533ef92a740e4121bf67ac54a9734705`,
SHA-256 `99e9abcd124cf8101f1048946d324d2766654021ef2e1be8b1c40ae29cd3b4c2`.
Both game processes stopped. This run measured a candidate pilot source.
Run 10 later verified read-only server receipt.

### Run 08 absolute sector positions

Both processes resolved MFQ-419 at registry index 943 in
`cluster_06_sector001_macro` under the same saved registry epoch. Their
runtime component IDs were local to each process. For this comparison,
the retained `run-08/client/process.log` contains 157 complete
`AUTH_NPC_LOCAL_POSE` rows. Row 158 ends partway through the record and
rows 159–160 are absent there. The preceding trajectory counts and
one-step statistics use the 160 retained points in
`run-08/native-cockpit-measurement.json`.

For each provider sample inside the complete cockpit rows' clock range,
pair it with the nearest cockpit sample on that clock and compute the
Euclidean distance between the two absolute sector XYZ positions. Use the
published native position and its game and capture times from
`run-08/server/result.json`. For the cockpit, use the game time and the
midpoint of the monotonic capture bracket in each complete log row. The
independent MD check uses `AUTH_REGISTERED_NPC_VELOCITY` rows from
`run-08/server/process.log` and matches on MD `age_s`. All three
comparisons use the observed points directly, with no interpolation.

| Provider position and matching clock | Pairs | Absolute clock gap | XYZ distance, min / median / P90 / max |
| --- | ---: | ---: | ---: |
| Native publication, game time | 32 | 0.7–63.8 ms | 1.35 / 5.60 / 7.00 / 7.54 km |
| MD position, game time | 32 | 0.3–64.2 ms | 1.35 / 6.29 / 7.57 / 8.14 km |
| Native publication, same-host monotonic time | 33 | 0.7–60.2 ms | 0.47 / 5.00 / 6.36 / 6.97 km |

P90 uses nearest rank. In game-time pairs, the cockpit captures occurred
0.14–0.79 real seconds after their provider partners. In real-time pairs,
the cockpit game clock was 0.065–0.797 seconds behind. The first and last
game-time pairs of native publications differed by 1.35 and 4.43 km.
These are discrepancies between observed simulation positions. They
do not estimate the provider position between its low-attention jumps
or establish the effect of a playback correction.


### Read-only pilot-to-provider measurement

Run 09 at `df53039d` admitted HVC-420 on Linux and recorded 160 local
MFQ-419 measurements. The normal reconnect wrapper omitted the NPC
binding metadata, so the movement connection never bound and the
provider received zero pilot measurements. The private overlay also
added an unexpected option to the credential table, which failed
redaction. The provider still confirmed its last-departure save
`checkpoint-0ec0f62faa4b457aa55e34f1582caa3d` and both processes stopped.
The source bytes remained unchanged.

Run 10 at `46fc471a213aebd36d38954122dd29bb2e91547c` used the same source
on one private Linux provider and one Linux cockpit. The provider
received **155 of 160** autonomous MFQ-419 observations from the admitted
HVC-420 movement grant. Every received record matched the native
cockpit record for identity, sector, XYZ, game time, capture time,
stock attention, and sector velocity in metres per game second.
The cockpit recorded 39 `inzone` and 121 `visible` observations,
all at or above the stock `visible` threshold and below `nearby`.
The received subset included 35 `inzone` and 120 `visible` records.

The observed capture-to-receiver delay was 22.7–255.0 ms, with a
123.0 ms median. This subtracts the cockpit's native monotonic
capture timestamp from the provider's monotonic receipt timestamp
on the same host. Game time describes the simulation sample and
the velocity basis. The delay includes the private live-log reader
and the loopback movement channel. The normal own-ship stream
acknowledged 148 HVC-420 samples in the same session.

Radar ended naturally at cockpit game time 589.78. The provider
recorded `view_lost` and cleared its last sample while the pilot
remained connected. The private receiver only recorded the pilot's
report. It issued no world writes or onward snapshots. The open-map
radar-END case has separate socket-test evidence.

The offered checkpoint was `checkpoint-4d5f23378a164e9396e2318bc5c9aae4`.
Normal departure produced `checkpoint-6608d311fc70408badf45da1761d5c02`,
SHA-256 `50abd251197206efc2832581353485b694ef041441eb63350711e3bbe2ff64b0`.
Both saved markers were verified. The original and staged input retained
SHA-256 `972b596cebcac38396321477287b84e5bf8da0a4b60ec705717be061257f6c01`.
Credential redaction succeeded, both owned process lists were empty,
and the native and private slot locks were released. Evidence covers
read-only receipt on Linux loopback. Authority handoff, remote gameplay
application and Windows require separate trials.

Private evidence is under `.local/native-registered-npc-pair/`. The
`run-09/findings.md` digest is
`26db9903558903d9738cfd6c816d767d2f92adca95c0eb29b007dc63339e5ddd`.
The `run-10/native-measurement-ingress-verified.json` digest is
`597aaf871f40f8069abe087a644d4de6f8803b4aa4cda9c303e4df414b7a618a`
and the corresponding raw receiver log `run-10/server-native-measurements.jsonl`
has digest `9d99a4d914c76f3b8852b9a3ef66cbb34c35ba1df59051764f55a9bb0e2e2b75`.
