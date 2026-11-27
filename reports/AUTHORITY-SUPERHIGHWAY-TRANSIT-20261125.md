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

## Native HVC-420 rejoin after the two-pilot departure

At `b418a409`, a fresh GPU-free provider restored a verified copy of the
two-pilot departure checkpoint. One regular Linux pilot joined through pinned
loopback TLS on Bazzite and took the helm of HVC-420 (registry index 1073)
in a fresh base-game profile. The provider ran the Company motion relay with
position writing disabled. Provider X4 reported the restored marker
`79da2ab15eea6e3aeb46221bc17a94f1`. Client X4 reported the new offer's marker
`2a877d6c04b546b0b7b758708197979c` and confirmed actual control of ship 1073.

The first native HVC sample arrived at game age 562.981 s in Black Hole
Sun IV (`cluster_06_sector001_macro`) at (168051.171875, 1049.878052,
−5185.678711) m, 0.384 mm from the offered save position. Its first returned
acknowledgement was sequence 2. All nine acknowledgements received by the
pilot had the same position, and the eight samples carrying velocity
reported zero. The provider accepted ten samples through sequence 11 and
recorded zero position change. The pilot left cooperatively without flight
input. The provider paused and confirmed the new departure save. Both
processes exited with no owned processes remaining.

| Verified save | Checkpoint | Game age, s | Save SHA-256 |
| --- | --- | ---: | --- |
| Two-pilot departure restored by the provider | `checkpoint-04007b4314214624a09ce825893fc160` | 562.952 | `82fcded5ce876e812a2700ebf0c7dc322af60a77bcbf02e34e9f14205412df90` |
| Fresh offer loaded by the returning pilot | `checkpoint-caf20d2b5fd645968e97a2d80018900b` | 562.952 | `bb541ebb85d811d7bc83bb68adc3e9f47e817ccd9198e89788881a45ba3ca863` |
| Confirmed after the rejoin pilot left | `checkpoint-39a02fb17cc24fad896b3b578f9fdd08` | 568.114 | `972b596cebcac38396321477287b84e5bf8da0a4b60ec705717be061257f6c01` |

Independent save parsing found the same indices, ship codes, sectors and
positions for HVC-420 and KHI-630 across all three saves, with 0 m change in
either saved position. The registry epoch and issued count of 5,048 were
unchanged. Between the source and fresh offer, the saved component ID
changed at all 5,046 available indices. The code, class, macro and owner
were identical at every index. The two unavailable indices (49 and
2430) stayed unbound. The mapping digest hashes each index and its saved
component ID, which accounts for the digest change. All 5,046 component
IDs stayed the same from the offer to the final save. The offer
preserved the source game age. The next departure save advanced game
time by 5.162 s.

The rejoin covered one cockpit. KHI-630's persistence was checked in the
saved bytes. Simultaneous cold rejoin, further flight, Windows and Internet
connectivity were outside this test. The private verification result
`.local/two-pilot-cold-rejoin/run-02/verification.json` has SHA-256
`922aa2a27cd738a60b1f9e96211c3a7f61889ade7be61bd712599c9aedb65e51`.

## Confirmed control loss during native resume

The original rejoin diagnostic stopped its control connection when its private
recorder requested a motion acknowledgement before any had arrived. At
`b418a409` that exposed a provider fault after the engine had resumed. The
provider made a compensating pause but did not publish a departure save.

A deliberate reproduction on the Company provider at `96bb4851` closed
the admitted Linux pilot's control socket just after it received resume
request 1 and before native dispatch or any receipt. It used the same
two-pilot departure source and a separate private profile on Bazzite.
The same CPU Vulkan X4 process paused, saved, and remained available.
The fresh confirmed save had game age 563.894 s, up from 562.952 s.
Confirmation was observed 94.124 wall seconds after the socket closed,
including the full save and registry checks on this host.

The next authenticated connection received that exact checkpoint and could
reserve HVC-420 again under a fresh handle. It stopped at ship selection.
The independently parsed save retained both registered ships and 5,048
issued identities. The pilot and provider shut down with no owned processes
remaining. This used one native cockpit and pinned loopback TLS.

| Evidence | Value |
| --- | --- |
| Confirmed checkpoint | `checkpoint-5c24bcfedb94445f9168121f5c791b76` |
| Save SHA-256 | `d751935442376566ab97ac8e40acf4a86f6ac7ab312c5b1e3b96ba01347a9fb1` |
| Marker read from the saved XML | `b9fc128a7ae5b6835ce0baf6d8788c1c` |
| Private `.local/two-pilot-cold-rejoin/run-03/verified.json` SHA-256 | `333536db97f5651b386abd654194ad92cf57765f4391349ff5d420cf218c27d0` |

At `11f10348` the same policy also covers loss during a partial resume
request or receipt. A four-connection TCP regression closed two pilots'
connections during one resume. The other two continued, received one
resume each, and their subsequent departures produced one new save.
These tests used local TCP peers and a test engine.

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

## Ordinary jump gate staging attempt

A separate Bazzite attempt at `f444e5f0ef13d4ef6aa89bf2006d62550cbdbf12`
targeted the reciprocal stock gates ZGH-239 and NEL-546 from
`cluster_06_sector002_macro` to `cluster_01_sector002_macro`. The
private seed was intended to stage HVC-420 near the entrance and the
unoccupied KHI-630 in the far sector before a later stock cockpit flight.

One GPU-free provider reloaded a disposable copy of
`checkpoint-c3a43fc74180427bac0ef75df94a3479`, observed its marker
`0ac65001d40474528e22ade9a383f7d4`, and completed live inspection of
5,048 registry entries. The private seed cue returned neither a ready
receipt nor a refusal during roughly four minutes after inspection.
The harness stopped the provider. The result reports `startup cancelled`,
zero engine operations, and zero remaining owned processes. No staging
checkpoint was published. Whether any private fixture placement occurred
in RAM remains unknown.

The original and copied input hashes remained
`ef811ab5d7ee212c0d38230fbc48944261b096be3c2d3d34b9baa24ffb033676`.
Both locks cleared. The cockpit phase did not run, so this attempt
establishes no ordinary jump-gate passage. Private evidence is under
`.local/ordinary-jump-gate/run-01/`. The `staged/result.json` digest is
`f0469eeea70d243192c97bb7932ee4a20ad3b12af93b61f8857de1e94336d907`.

### Corrected paused jump-gate staging

Run 01's engine log gives the reason the readback could not run. During
load, X4 rejected `prepare_route.xml:67` with
`Found non-event condition 'check_value', event condition required!`.
The child used that condition alone. XML schema validation had accepted
the file. Run 01 did not establish whether its setup event was dispatched.

Run 02 at `d3fc032d54a0e1fa5f90fdcc9ecb0fe16a206b7f` used separate
UI-triggered Prepare and ReadBack cues. In a fresh private copy of the
same source, X4 completed the 5,048-entry registry inspection. The
Lua dispatch, Mission Director cue entry and nonce-matched reply were
recorded for both calls at unchanged game age 465.97 s. Prepare returned
`STAGED`. The read-only ReadBack then returned `REFUSED` with
`saved_actor_range_or_known_path`. This combined check covers both ship
sectors and ranges, discovery flags, and the first gate on the known
path. The log does not identify which clause failed.

The harness stopped the provider at that refusal. No starting checkpoint
was published and no cockpit launched. The original and copied save
retained SHA-256 `ef811ab5d7ee212c0d38230fbc48944261b096be3c2d3d34b9baa24ffb033676`.
The provider recorded zero engine operations and zero remaining owned
processes. Both locks cleared. Private evidence is under
`.local/ordinary-jump-gate/run-02/`. Ordinary gate passage remains unverified.

### Instrumented staging readback

Run 03 at `43939939f314e5568d00780fada5e903c1ce65ff` used the same
source and staging bounds. Prepare returned `STAGED` at paused game age
465.97 s. The separate readback measured HVC-420 in
`cluster_06_sector002_macro`, 273,280 m from ZGH-239 (required 8–16 km).
It measured KHI-630 still in `cluster_06_sector001_macro`, 11,386,880 m
from NEL-546 (required 25–45 km in `cluster_01_sector002_macro`).
All four discovery checks passed. The two-component known path started
at RVA-280, a superhighway entrance, instead of ZGH-239.

The requested HVC point was (92111.055, 0, 100038.875) m and the requested
KHI point was (−147781.594, 0, −98076.977) m. The HVC point was about
3.12 km from the stock save gate origin, also outside the staging envelope.
The paused readback did not show either intended placement. It could not
distinguish a deferred or rejected relocation. No staging checkpoint or
cockpit run followed. The original and copied source hashes matched,
the provider owned no remaining processes, and both locks cleared.
Private evidence is under `.local/ordinary-jump-gate/run-03/`.

### Staging with native gate centers and an ordinary resume window

Run 04 at `720c63bca34abcc555a8a810e158cf82ee83bd3a` used the
same isolated input. Native sector-frame reads put ZGH-239 at
(82218.750, 0, 93172.852) m and NEL-546 at
(−117970.703, 0, −79710.938) m. The planned safe points were 11.850 km
and 35.077 km from those centers. Both fell within the original
staging bounds before placement was requested.

The stock local adapter resumed the isolated provider. X4 executed
the explicit-coordinate placement cue at game age 467.04 s and
returned `STAGED`. The independent running readback at 467.51 s
still found HVC-420 at 273,280 m from ZGH-239 and KHI-630 in its
original `cluster_06_sector001_macro`, at 11,386,880 m from NEL-546.
All four known flags were true. RVA-280 remained the first route
component. The plan passed, but the observed ships did not reach it
within this readback window. The dispatch receipt does not say
whether either warp was deferred or rejected.

The harness stopped on that refusal. There was no paused placement
receipt, staged checkpoint or cockpit launch. The original and copied
source hashes matched. The owned provider exited and both locks cleared.
Private evidence is under `.local/ordinary-jump-gate/run-04/`.
The `staged/result.json` digest is
`d896f569ac878b86217709804ae0b9a581df12d9922e7d10a0afdabf0aaa0b89`.

### Bounded placement verification and candidate save

Run 05 at `28b36a12ede63660b6e6c66a5940cf618a90b875` dispatched
Place once. Its first readback at game age 467.58 s still saw both
original positions and returned `PENDING`. Within the four-second
real-time readback budget, X4 returned `READY_RUNNING` at 469.06 s.
After pause, `READY_PAUSED` at 469.63 s confirmed the same ships,
positions, discovery flags and ZGH-239 as the first known gate.

The resulting marked candidate
`checkpoint-46e883582e6a4d70bb72dd8765a40d11`, marker
`e4f7dab6a19247c58fc0f36a2d1c1d25`, has save SHA-256
`006d6acc6127534d521e33cc0bbfc8a71d757835d865383de41ab99259f40496`.
Independent format-3 parsing placed HVC-420 and KHI-630 in their intended
sectors and within 0.44 mm and 0.60 mm of the paused native positions.
The gate child translations from the installed stock zone catalogue
reproduce the native gate centers. Distances from the saved ship
positions to those centers are 11.958 km and 35.658 km.

The staging verifier rejected the candidate because NEL-546 lacked
object-level `knownto="player"`. Its component carries `known="1"`
and `read="0"`. The destination sector and KHI-630 carry both
`knownto="player"` and `known="1"`. The earlier distance verifier
also used the stock zone origin for gates with default offsets,
omitting their stock child translations. The candidate remains
unconfirmed. Cold runtime discovery and routing remain unverified.

The original and working source hashes matched. The provider ended
with zero owned processes, both locks cleared, and no cockpit launched.
The candidate and logs are retained under
`.local/ordinary-jump-gate/run-05/`. The `staged/result.json` digest is
`cb3e82ea902fb0d5c447e35a8704a6723de3647f97af978eed914c3cb9bec1a3`.

The private parser now reads pinned installed stock zone transforms and
accepts the two observed gate discovery encodings. ZGH-239 carries both
`knownto="player"` and `known="1"`. NEL-546 carries `known="1"` without
`knownto`. The stock child translations are (−7437.5, 0, −4935.546875) m
for ZGH-239 and (−11427.734375, 0, 5250.9765625) m for NEL-546.
These translations reproduce the previously measured native centers
and keep both saved distances inside the original envelopes.

The read-only quarantine loader's initial preflight refused the default
DLC configuration before starting X4, then the private adapter selected
the matching base-game-only content. One network-isolated X4 launch
used the candidate's exact bytes and marker and a read-only private
profile with a writable diagnostic log. The launcher stopped before
any save-load receipt with `multiple X4 processes in owned group`.
The engine log is empty. The marker, positions and first known gate
were therefore not observed in a cold X4 process. The launcher
proved zero remaining owned processes and released both locks.
The candidate and original hashes, manifests and pointers were
unchanged. No confirmation, admission or publication occurred.

Private quarantine evidence is in
`.local/ordinary-jump-gate/run-05-cold-native/`. The diagnostic
`result.json` digest is
`d84396d81987fe21d041f5a22255d5c9e77a974882afda7adc24187d7a371415`.

The final quarantine diagnostic at `d3038126` held a private load
permit until two scans found PID 810559 with the same start identity.
The stock uniqueness check passed and the permit was issued at 0.234 s.
A subsequent strict check found multiple matching X4 processes before
any load request was logged. The engine log is empty. Cleanup found
PID 810618 and stopped it, leaving no owned processes or locks.
The candidate stayed CURRENT-only and unchanged. This diagnostic
loader was retired from the experiment.

### Ordinary cockpit gate crossing from a newly confirmed seed

Run 06 at `d3038126da9dcd86479dc11829d12fe2ebd23e68` staged a fresh
copy of the original source. The four-second readback verified both
placements while running and after pause. The corrected stock child
transforms and saved discovery fields passed the independent check.
The normal controller confirmed
`checkpoint-c29a9eace29a4f1bad82cae606032ccb`, SHA-256
`0d91040cefc4a31ed60f0ddcd9592f7a74600a5d5e3fa7d1fa0ac178f75d0be3`.

The normal GPU-free Bazzite provider cold-loaded that checkpoint.
A separate regular Linux cockpit received and loaded the fresh
`checkpoint-8a71e13bf5d945c787e873548753fcf7` join offer and took
control of HVC-420 (1073). Its read-only stock route check succeeded
at game age 470.03 s. Stock guidance to KHI-630 and autopilot were
confirmed once at 470.334 s. At 490.542 s X4 logged
`event_object_entered_gate` for the same actor, `0x42657`, via
ZGH-239 to NEL-546. Independent cockpit readback at 490.651 s showed
that actor still occupied and controlled in
`cluster_01_sector002_macro`. The pilot fixture supplied no poses
or relocations.

The motion publisher stopped with `motion_local_or_protocol_failure`
around the boundary. The cockpit acknowledged origin sequence 186;
the provider received and verified origin sequence 187. The provider
accepted 156 samples and verified seven position writes, all before
the sector transfer. No destination sample was acknowledged and no
provider sector transfer was verified.

The independent final save check found HVC-420 in the original
`cluster_06_sector002_macro` and KHI-630 in the destination sector.
The provider exited successfully and paused at marked
`checkpoint-d4e158bbc8c043c0873815c1bd31883b`, marker
`d52ef3eb4aa9fbf4051968b584fbce42`, SHA-256
`38492c1d05f0a29008d7a596ad35bc4324d6199e767b8327bef729a47e2e2ca2`.
The pilot exited with failure. Both processes and locks cleared.
The original source and working copy remained unchanged. This
establishes ordinary stock cockpit gate passage. The movement path
across that boundary remains unverified.

Private evidence is under `.local/ordinary-jump-gate/run-06/`.
The cockpit engine log digest is
`31ef6e4d60e411a67b63074e7cada9c25f37de73ba1ad07970beadcb73ed4c51`.
The provider result digest is
`7bedf1deeb9a571b8eb5c21ed5f9208a3ff808c1dc31243f5beabe42e98bd9a3`.

### Verified ordinary gate movement and departure save

The private Run 06 cockpit observer still treated the old superhighway
sector `cluster_06_sector001_macro` as its only permitted destination.
That check rejected the ordinary jump-gate destination before the
observer could log or send the first destination sample. The private
ordinary gate wrapper now pins `cluster_01_sector002_macro` and logs
a bounded reason if a sample uses an unexpected sector or returns to
the origin. No product movement checks changed. An offline replay
used the recorded origin sample and simulated destination samples
to confirm the normal boundary velocity reset and both refusals.

One Run 07 flight at `d3038126da9dcd86479dc11829d12fe2ebd23e68`
used a disposable copy of the confirmed Run 06 starting checkpoint.
There was no additional staging. The real Linux cockpit verified
the stock route after admission, then used stock autopilot once.
X4's gate event recorded the same controlled HVC-420 actor
`0x4266e` crossing ZGH-239 to NEL-546 at game age 491.022 s.
The cockpit acknowledged origin sample 192, first destination sample
193 with velocity absent, and destination sample 195 with velocity.

The provider accepted 206 samples, verified nine position writes,
and independently verified its one sector transfer for index 1073
on sample 195. Both native readback paths and the target agreed within
1 mm at approximately (−116681.414, −359.175, −79029.375) m in
`cluster_01_sector002_macro`. The pilot received 205 acknowledgements
and finished through ordinary user departure.

Independent parsing of the marked save confirmed HVC-420 (1073)
and KHI-630 (1074) in the destination sector. The provider ended
paused at `checkpoint-ab4e77fb08fe477ca55e990817eec479`, SHA-256
`d5b74c0d7d720f5c0ee74bf043399a3173bfe5dfc5879402defa5e57a4acba06`.
Both processes and locks cleared, and the confirmed source and its
copy matched their starting bytes and pointers. The private proof
under `.local/ordinary-jump-gate/run-07-measured/flight/verified.json`
has SHA-256
`bc2db43854be3ff7a9313474fea3c8fe0477ae74c4c48a9edd76d6583f9af0ff`.
The run used private same-host Linux TLS with one cockpit.

### Ordinary cold rejoin after the gate flight

At `f9f5bc369ce80f620b9840458a9ed6678499b353`, the regular GPU-free
Bazzite provider cold-loaded a disposable copy of the Run 07 departure
save, `checkpoint-ab4e77fb08fe477ca55e990817eec479`. Both its load marker
and the ordinary pilot's fresh offer marker were observed independently.
The fresh offer was `checkpoint-624dc3b3abc44d5ca4bd8d656240ee8a`,
marker `31dad283a1fc0bbf19450461d43dc3b1`. The normal Linux cockpit
claimed and took the helm of HVC-420 (1073) in
`cluster_01_sector002_macro`. The provider's position writer was disabled
and the pilot supplied no flight input or autopilot action.

The first normal acknowledgment was sequence 2 at
(−115193.3046875, −360.1820068359375, −78050.046875) m, **0.198 mm**
from the independently parsed final position in the Run 07 save.
The provider received nine samples. The pilot left cooperatively,
and the provider paused and confirmed
`checkpoint-726c6baf1a4844469df8804476c65783`, marker
`2b0f00de770c746b6cf19f0644432625`, SHA-256
`fc3091f770e00186a567fc333fcf65d10d590a744425ffdd4b88520df38f0666`.

HVC-420 and KHI-630 (1074) had identical saved sector coordinates
across the restored, offered and departure saves. KHI-630 remained
at (−147749.01575, −228.114, −98034.0160625) m. Both processes and
locks cleared. The original and disposable source fingerprints were
unchanged. This was a stationary rejoin after the gate crossing.
Private evidence in
`.local/two-pilot-cold-rejoin/run-ordinary-gate/verified.json`
has SHA-256 `a516c7ae0ebfba6df6152a3f4715375c9d1347e203972211e9764072cc453c8b`.

## First contact with a pilot waiting at the exit

Two native Linux cockpits joined one local GPU-free provider at `673db290` on 27 November. KHI-630 waited 8 km above the Superhighway002 exit in Black Hole Sun IV. HVC-420 used stock autopilot to fly there from Black Hole Sun V. The waiting pilot's stock radar had a 40 km range. The test used the regular pilot roster and cockpit movement subscription.

- The provider acknowledged 216 destination positions from HVC during 28.96 seconds. Both pilots reported Black Hole Sun IV. Their separation was 6.41–8.18 km. The longest gap between those acknowledgements was 0.259 seconds.
- KHI knew HVC's registered ship identity throughout this interval. On all 186 cockpit radar checks KHI's own X4 still placed that ship in Black Hole Sun V, outside the local physics simulation. The radar returned no contact. KHI therefore opened no movement subscription for HVC and received no cockpit positions for the arriving ship.
- A comparison after the session paired each HVC acknowledgement with the latest accepted KHI position. Both poses were at most 0.545 seconds old for all 216 comparisons. These positions and KHI's measured radar range would have identified a nearby pilot throughout the interval. The server did not send an arrival notification during the test.

A pilot can cross this superhighway while another remains in the destination sector. On this run the waiting pilot could not acquire the arrival on radar. The observing client needs a way to recognize this first contact when its local X4 still places the other ship in the previous sector. The November 26 human travel-drive freeze happened within a single sector.

This test enabled the movement service and cockpit observer. It left the optional server position writer disabled. The saved provider copy therefore retained HVC in its starting sector. In a separate writer-enabled test the provider transferred and saved the ship. The current writer reads back the change inside the provider process. The movement service sends new positions to clients with an existing subscription for that ship in the destination sector. [Provider position writer](../tools/dev/authority_company_pose_trial.py) · [Movement subscriptions](../src/authority/company_motion_service.py) · [Cockpit radar lookup](../src/authority/md/company_cockpit_observer.xml)

The provider paused and saved `checkpoint-2f95822aab8140c0b8179a8bb451b4ec`, independently verified at SHA-256 `5a8f6b2a121236e1b3a800d3078d43dcaaef9d883096a9887a39c6bd1bb8de5b`. Both pilot processes and the provider exited normally. The first offline save verifier lacked its Python import path. It completed on the same save after the path was corrected. The private paired observations are archived with SHA-256 `cc41a6faae4fc6e1f81bdd3b48962348e0f994fc5749e186e0da745d3df2fea1`.

### Starting cockpit playback at travel speed

The first accepted position in Black Hole Sun IV had no velocity. The next arrived 112.5 ms later, 531.7 m away, with a speed of 4,727 m/s. The source supplied a further 214 positions from that sector. In an offline replay on `3fd731ff`, the observer started exactly at the first position and already had the correct ship on radar. Playback still rejected all 215 subsequent samples at its initial distance check. The final discrepancy was 9.96 km. This isolates a playback problem from the missing radar contact in the live transit.

A private Linux receiver experiment used the same 216 samples, translated by a fixed offset into a known 40 km radar scene. The observer independently read the correct ship at the initial position. It used the next sample's velocity to check the travelled distance, then interpolated along that segment. Direct realignment was disabled. The first implementation stopped after a 92.7 ms render gap called for a 376.1 m initial step. A revised implementation carries unfinished distance into later frames under the existing 250 m startup limit. Replaying the first run's actual frame times offline exercised this limit at 249.99 m.

The revised path ran in a single native Linux X4 receiver. It recorded 4,641 setter/readback pairs, a maximum applied step of 63.25 m and a maximum readback error of 3.94 mm. The end position was 1.68 mm from the destination. The ship stopped 5.004 s after the final sample and stayed still through 5.75 s. At startup it lagged more than 250 m behind the time-matched source for approximately 2.48 s, reaching 725.4 m of lag. The frames in this second native run were shorter, so the 250 m cap was exercised only in the recorded-cadence replay. Both native runs released all owned processes. The private results have SHA-256 `0a0241778422b18f7e85f8465e7802cb7c7d8959f8b2ed1b685e726d0eb9549b` and `fba049d506f0f91ff533f6fbe26c6c350a2f1583f21029178ce78811a48f45a6`.

The source trajectory came from two live pilots. The receiver experiment used one local X4 instance and a pre-identified target. Live arrival discovery and Windows/Linux internet flight still need separate verification.
