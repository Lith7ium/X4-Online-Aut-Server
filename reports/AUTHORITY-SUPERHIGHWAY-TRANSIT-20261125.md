# Native Company sender resumed after a stock superhighway passage

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
write. Six focused tests passed. There was no further writer-enabled
native run. The private approach and timeout reports had SHA-256
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
