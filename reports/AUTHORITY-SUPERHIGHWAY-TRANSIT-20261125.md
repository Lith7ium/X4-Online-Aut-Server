# Company superhighway passage and authoritative sector transfer

## Two pilots, verified transfer and final save

At `1d0c4e4b`, HVC-420 flew the stock superhighway from Black Hole Sun V
to Black Hole Sun IV while KHI-630 remained connected in IV. Both
ordinary Linux cockpits and the GPU-free provider ran on Bazzite over
loopback TLS. The test observed the ordinary flight without delaying any
reply. The provider's read-only verification requests shared one four-second
budget after each position write.

| Observed in the session | Result |
| --- | --- |
| HVC sender at the sector boundary | Origin sample 261, destination position 262, destination velocity by sample 266 |
| KHI after HVC's exit | Stationary destination sample 176 acknowledged under KHI's existing grant |
| Provider | 1,167 accepted motion samples, 15 verified writes, one verified sector transfer for HVC at sequence 275 |
| Independent inspection of the final save | HVC and KHI both in Black Hole Sun IV, respectively 0.580 mm and 0.391 mm from their paused readbacks |

The longest interval measured between a verification request and its accepted
readback was 1.599 s. The 2.01597 s case from the earlier run was covered in
the bridge regression tests. The passive flight did not need the extra time.
The provider saved on last departure and all three processes exited normally.
The verified checkpoint was `checkpoint-04007b4314214624a09ce825893fc160`,
save SHA-256 `82fcded5ce876e812a2700ebf0c7dc322af60a77bcbf02e34e9f14205412df90`.
The private evidence `.local/native-authority-transit-pair-md-readback/native-01/verified.json`
has SHA-256 `2ad59ab75bb0b2f5ce4c91437dfab25df1c73f407d625045d92739a8d588d137`.
This checkpoint was inspected offline. Cold recovery was exercised separately
in the earlier one-pilot flight.

## Interrupted sender with two admitted pilots

At `7ae7a5a0` two ordinary Linux cockpits joined the GPU-free Bazzite
provider. HVC-420 entered SuperHighway002 at game age 497.627 s and
exited in Black Hole Sun IV at 500.769 s. The stock game kept the pilot
at the helm. Its motion sampler failed at entry with the generic reason
`sample`. HVC's last acknowledged position was origin-sector sample 267.
KHI-630 stayed admitted in Black Hole Sun IV and the provider kept
acknowledging its stationary samples after HVC's exit.

The provider received no destination sample from HVC. Its marked departure
save kept HVC in Black Hole Sun V within 0.29 mm of its last verified
origin write, and KHI in Black Hole Sun IV within 0.40 mm of the paused
target. The save SHA-256 was
`8351bf9934b4d2f161aa706ab249b2bc5337ad47c0b4247ce5caef5ea123260e`.
The sampler log does not identify which validation failed in this run.

## Native recovery of a delayed sector reply

At `68fd061f` one regular Linux pilot flew the same stock route. A private
fixture held one genuine origin-sector Mission Director reply and delivered
it unchanged when X4 reported that the controlled ship had entered the
highway and had no sector. The reply reached the ordinary reader 91.5 ms
after the original request, inside its existing timeout.

The reader discarded the stale coordinates and requested a fresh sample.
The provider acknowledged the first destination position at sequence 143,
with velocity absent, and velocity at 145. It transferred HVC-420 once,
at sequence 143. On final departure it saved HVC in Black Hole Sun IV
within 0.61 mm of the paused readback. Both processes exited cleanly.
This run reproduced the timing condition deliberately with a single pilot.

| Private evidence | SHA-256 |
| --- | --- |
| Two-pilot save inspection, `.local/native-authority-transit-pair-calibrated/native-01/saved-position-verification.json` | `1fc8cc6fa6c8bc7ff7aa3b1f4539763f8730b28cd4a9b61b6b44710cf011071e` |
| Delayed reply and flight samples, `.local/native-authority-transit-sender-race/native-01/transition-source.json` | `08d507bbc7cf0c5da9556bf0b72e3281c19c2d9af6a85f618fffbd177d3f5bff` |
| Independently verified destination save in the same directory, `verified.json` | `2a472707db001eb7893202633ad49a4556ee66e336a8da0d454ce7a97edbb36f` |

## Two pilots through a natural crossing on the repaired sender

Both regular players joined the same private Bazzite provider on
`68fd061f`. HVC took stock autopilot and crossed naturally. KHI
remained stationary and connected in Black Hole Sun IV. The passive
observer added no delay. HVC's last acknowledged origin sample was
331. The first destination sample was 332 with a fresh position, and
334 included velocity. KHI's sequence 243 was captured after HVC's
first destination sample and acknowledged under KHI's existing grant.

The provider received HVC's motion in the destination sector. Its
position writer was still verifying an earlier write for origin sample
328. A reply carrying the matching request prefix reached the game
bridge after 2.01597 s and crossed its 2 s deadline. The bridge rejected
it before validating the payload. The last independently verified HVC
write was origin sample 287. The provider stopped with zero verified
sector transfers and no new departure checkpoint. Both store pointers
retained the second join offer. The authority's final ship position
after the unverified write remains unknown.
During this run sampled available memory stayed above 9 GiB and sampled
full memory pressure remained zero.

Private evidence is in `.local/native-authority-transit-pair-sender-repair/native-01`.
The SHA-256 of `transition-source.json` is
`1d84e452a95441a3818394e5b47f13a9474a35d522cd0d9287dc6084dd2503cc`.
The provider's `server/result.json` has SHA-256
`3d7f8445c5698810393e6ca944f2efd65dcaa59f82d1ff61c202643192ddadc1`.
The timeout diagnostic is in its private `engine-output.log` at line 769.

## Pilot flight with a native provider position writer

At `f20dc23a`, one Linux pilot flew HVC-420 (registry index 1073) from
Black Hole Sun V to Black Hole Sun IV using stock autopilot. The player
and a GPU-free Linux provider ran on Bazzite over pinned loopback TLS.
The provider started from a copy of the near-entrance checkpoint identified
below and enabled `--company-ship-positions`.

The pilot's last accepted origin sample was 142. Sample 143 opened the
destination stream with a fresh position, and sample 145 carried velocity.
The provider moved the same HVC-420 into the destination sector once, at
sample 161, and read its position back from X4. Across the flight the
provider accepted 558 samples and verified eight position writes. By the
last accepted sample (671) X4 already held the requested destination
position. When the only pilot left, the provider paused the world and
confirmed `checkpoint-5efd90adfaa04dd49dc37b0b48de99d7` (save SHA-256
`86810d09532cfcad018b1170f265b5f995282cca52d71632a735f4d4919f7d19`).
The remaining difference between the saved position and the paused target
was 0.48 mm. Both processes exited normally. A fresh GPU-free provider
then loaded that checkpoint, confirmed its marker and read HVC-420 back
in Black Hole Sun IV within 0.47 mm of the recorded position. This run
used one pilot.

Private evidence under `.local/native-authority-transit/native-01`
includes `transition-source.json`, `server/result.json`, and `verified.json`
(verification SHA-256 `9feb7c3b7076b1e5e3f6a4ead4993cb4a3a0651f60f15263a68319ef554d351e`).

## Earlier sender-only flight

On 25 November 2026, one regular native Linux player joined a regular
recovered Linux provider on Bazzite at `c8b46a13` through pinned
loopback TLS. The test recovered the private near-entrance Company
checkpoint `checkpoint-c3a43fc74180427bac0ef75df94a3479`, save SHA-256
`ef811ab5d7ee212c0d38230fbc48944261b096be3c2d3d34b9baa24ffb033676`.
HVC-420 used registry index 1073. KHI-630 was the saved Company ship
used as the stock guidance target. This attempt had one admitted player.

The normal provider enabled `--company-motion` and required the saved
format-3 registry. Its optional native position writer stayed disabled.
The launcher rejected every position-writer flag and checked the final
provider report for normal motion counters and absence of a pose
trial. Both private profiles used base-game-only content.

## Native stock passage

The same controlled HVC actor `0x42501` was armed in the passive
Mission Director watcher before input. The live known path contained
the genuine RVA-280–SuperHighway002–GNY-706 path to the saved KHI.
Stock guidance was confirmed, and the player autopilot accepted at
game age 467.342 s. No exploration, client warp, supplied motion
sample or second autopilot start occurred.

| Native witness | Game age, s | Sector and zone |
| --- | ---: | --- |
| Last independent pre-entry cockpit witness | 482.897 | Sector002, SHCon8 |
| Actual entry by the same MD actor | 482.970 | Sector002 → no sector, SHCon8 → SuperHighway002 |
| Ordinary sender reported one pose-free superhighway gap | 483.07 | SuperHighway002 |
| Last independent in-tube cockpit witness | 485.733 | No sector, SuperHighway002 |
| Actual exit by the same MD actor | 486.125 | No sector → Sector001, SuperHighway002 → SHCon12 |
| First independent post-exit cockpit witness | 486.167 | Sector001, SHCon12 |
| Stock autopilot stop | 486.93 | Sector001 |

Native FFI readbacks kept HVC actor `271617` as the controlled ship,
occupied ship and container across all three frames. The player, pilot
and assigned pilot were all `274684` before, in and after the tube.

## Authenticated motion resumed

The normal provider acknowledged the following actual wire samples on
the same registry epoch, index and admitted generation. The first
destination sample carried no velocity and established the new frame.

| Sequence | Game age, s | Sector | Wire velocity, m/s |
| --- | ---: | --- | --- |
| 145 | 482.949 | Sector002 | (−1282.961, −36.622, +634.079) |
| 146 | 486.200 | Sector001 | Absent |
| 150 | 486.671 | Sector001 | (−4134.685, −583.506, +521.181) |

The provider accepted 189 samples, 187 with rates. Its final accepted
sample was HVC sequence 211 in Sector001. The normal last departure
paused and confirmed `checkpoint-770948fd2fa448188709ec3a70bd2466`,
SHA-256
`14abe53a6dd5b4107cf3a366d4690d75b98a74dbf17d2d885791a2edad626b86`.
The client and provider exited successfully with no owned processes or
host lock left. Both source checkpoints and the earlier
writer-enabled failure evidence were preserved.

Independent parsing of the marked departure save confirmed
that HVC remained in the authority's original Sector002 gate zone
CYA-453 at (-191035.312, 2327.111, 99025.171375) m. KHI remained
in Sector001 at (31516.100, 115000, -3960) m. Both positions matched
the recovered source bytes as expected with the writer disabled.

The earlier writer-enabled retest terminated on `BRIDGE_MD_TIMEOUT`
before corridor entry. This native run establishes stock passage and
authenticated sender recovery. Application to the authority world,
map observation and a live second pilot were outside this run's scope.

## Earlier writer-enabled runs

At `3ad06b66` two regular pilots were connected. HVC-420 followed stock
guidance for about 92 game seconds. The last acknowledged sample was
465 m from entrance RVA-280. X4 then returned an unusable sector and
the original sender stopped publishing. The provider confirmed nineteen
position writes in the original sector and saved a checkpoint 10.26 km
from the entrance. That checkpoint supplied the source for the later runs.

A two-pilot retest at `c8b46a13` stopped before HVC entered the corridor.
Its last source acknowledgement placed it 2.01 km from RVA-280. The
provider had issued a position request for sample 330 and received
`BRIDGE_MD_TIMEOUT`. Sample 291 was its last independently verified HVC
position. The existing trace cannot establish whether X4 applied 330;
the provider ended the session and both store pointers still referenced
the second join offer. The stock map received a request to focus KHI.
Neither a map subscription nor a map receipt was verified.

The later diagnostic in `b7ab1d33` records whether a waiting request
expired in the update callback or its request prefix returned past the
deadline. It retains the existing timeout and stops on an uncertain
write. Six focused tests passed. The private approach and timeout reports had SHA-256
`e36494d27a61a7b556cada543b01946ba07284ceb62ff72ab8a7d2c7e4128c4c` and
`f892fb0e4c002eb06c51975986c9f6de3bbc80cc2350b0aa51a8211d44539b95`.

## Evidence digests

| Private artifact | SHA-256 |
| --- | --- |
| Same-grant transition plus native witness | `4cae6f23027fbe7042382ad582d9b911de5933731af0597f50856a787e22514c` |
| HVC native log | `bb8bc6846c140bd4353a8b04d13d8c3d40683d616cde4c44f8da878d83905e65` |
| HVC source and wire acknowledgements | `0f20306177b5d21a4fc0c9a2bcbab2f4f603dc367ba8b73ec4301cdf73619548` |
| Provider result | `23021fc09cc6c832ea00c0c6eed8332bb62d07134b564d845edfc16307b69c7c` |
| Supervisor result | `5cc89a52584169a62da41aed476b7b59ceb688df5c7bc84528516598e2126737` |
| Independent save verification | `0406e84f95aa5cb2e9baa6c8f0786aac09100d695706f60466737aa1622326ce` |
