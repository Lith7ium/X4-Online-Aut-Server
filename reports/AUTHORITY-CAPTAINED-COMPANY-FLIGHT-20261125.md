# Native Company flight with the server's original captain

On 25 November 2026, a regular Linux Company pilot flew WRN-302 in
`cluster_01_sector001_macro`. The regular recovered provider moved the
same Company hull 17.4375 m to the pilot's reported position and confirmed
a new checkpoint after the pilot left. Both X4 9.00 (611726) instances ran
on Bazzite with isolated profiles and private displays at revision
`ee4ed8f5`. The provider used software Vulkan, the pilot used AMD RADV,
and the connection used pinned TLS on loopback. The provider enabled
`--company-motion --company-pose-trial` and required a format-3 registry
snapshot.

The provider recovered `checkpoint-39bf46000be4427e8d0d59898e06ef11` from
the private staged source described in the two-pose report. The staging
preserved the original save and sealed ledger and archived the original
development cues outside the live extension. The provider issued a fresh
paused offer. The client matched the downloaded bytes to the offer,
observed the native load marker, joined the admitted session and
observed the transition from aboard to control of WRN-302, registry index 7354.
The private observer supplied one stock Strafe Left input of 0.5 s after
a neutral baseline and independently confirmed key release. It observed
the normal publisher's samples and acknowledgements.

## Flight and provider readbacks

The positions are in sector metres. Y and Z remained -3685.429932 and
46904.652344 for the cockpit and UI observations.

| Observation | Cockpit sequence | X (m) |
| --- | --- | ---: |
| Neutral cockpit baseline | 16 | -168384.156250 |
| Acknowledged moving sample | 31 | -168401.578125 |
| Acknowledged settled sample and provider UI readback | 38 | -168401.593750 |
| Provider Mission Director readback | 38 | -168401.594000 |
| Final accepted sample and paused provider UI readback | 56 | -168401.593750 |

The client received 41 sample acknowledgements. Forty of those samples
carried a velocity vector, including two with nonzero velocity. Sequence
31 carried (-1.425624, 0, 0) m/s. The provider accepted 42 samples, 41
with velocity. It applied the settled sequence 38 and independently
verified the target through Mission Director and the game UI. The UI
matched exactly and the Mission Director read was within 0.000431 m.
The provider's applied sample was stationary. Sequence 56 matched the
same target and was checked again after the provider paused.

## Confirmed saves

| Checkpoint | Marker | Save SHA-256 |
| --- | --- | --- |
| Recovered source `checkpoint-39bf46000be4427e8d0d59898e06ef11` | `7bf15f0bb48a08ea6e3019d79fea10e1` | `48ef4719eddf1385dea4ccf2b0f4487cf5b77381b64fc5022f8d6b053008774b` |
| Joining offer `checkpoint-cb3fe45c1ac04127a84644c84be37f7c` | `2e8fa38b04bec9416f863f72ae9b0e8e` | `25b7bbb157d40d71433220b85b623819d2c6177e2a65f8ade1e1bf92cac26b92` |
| Last departure `checkpoint-34afeefebcc94c688e795efae42025cf` | `018017da8ad55bc4e349ab10bcda30e8` | `9ef3a0e89f896579faea01ea7364b117cae28aba9d32ce9a59efbee94ec45f50` |

The source, offer and final save identify the same undocked WRN-302 and
the same assigned AI pilot, PYN-740 (seed `2211373338772438693`). The
server avatar stayed at XPJ-169. The faction balance remained 9,884.00 Cr,
WRN-302 kept its two water, and the sealed purchase and challenge rows
matched across all three checkpoints.

The terminal save recorded the ship at a zone-local offset. Combining
it with the pinned stock zone origin gave (-168401.593500, -3685.430000,
46904.652781) m. This was 0.000509 m from the paused provider target.

Both native runs exited successfully with no owned processes remaining
and released their temporary lock. The original checkpoint was
preserved. This run covers one Linux pilot, the same-sector application
and one last-departure save. Independent second-cockpit rendering, live
velocity application, and Windows and WAN operation were outside the
run's scope.

## Evidence digests

| Private artifact | SHA-256 |
| --- | --- |
| Pilot wire samples, acknowledgements and input receipts | `81b06a9eb1d35fa9af2c1d0b9f909dac873df852e2bd65cfa8d440755590fde1` |
| Pilot native result | `a924d1c2e7651e6890996f11a02578e461d3adb27f7bf39456250160a50f4abc` |
| Pilot engine log | `a289ee3cd4abfafd58133e1b866b5bdc6df7491a96690766fb710971c3441e43` |
| Provider native result | `7c932b7dd1037fd18692ba9142aaf9bf9e80a038667ff55b39d0a5cc8c0a42d9` |
| Provider engine log | `d5b31442f51fd3d6bbc8b9c77cf9a3dbe7364078dcdb9661cd6ab97b5fb472e1` |
| Saved-byte and motion comparison | `797303a59b9328b11de2a92810007f54ccfa576bffa53bce803186697f583553` |
| Read-only ledger comparison | `5d26155d16ff92f0727ae34f7afc4a2c9ad835a9b1fabc075f577b467a92c48d` |
| Staged source provenance | `873ce70ab5cc14d2b1d371655218cbaa891f5b2eb1cb58c50cf9581b9204b2a0` |
| Private cockpit observer | `038069b696c7098503f8512874a91a3019b0286980b483894cd229f21cc5b842` |

## Cross-sector transfer with the saved idle captain

At `2f1578fd152a8bdd0f43eb97f7c40c7ccf5b9913` a GPU-free native
X4 provider on Bazzite admitted one headless TLS participant for
BBS-493 (1077). The participant simulated the cockpit confirmations
and supplied scripted poses through the regular motion grant and TLS
movement channel. The provider acknowledged 69 V3 samples, including
24 before the scripted sector change.

The native pose path verified exactly one transfer on sequence 30,
from `cluster_06_sector002_macro` to `cluster_06_sector001_macro`.
Native Mission Director and UI readbacks both measured
(30000, 100000, 55000) m immediately after transfer and again after
pause. Independent parsing of the confirmed last-departure save
found BBS-493 at the same registry index in the destination sector,
with the original captain seed `-97168325741795052` and only its
started default `Wait`.

The provider exited paused at `checkpoint-15e7b01029a94d1f8a7f84ceeeb65361`,
marker `9468c4e17ba51505b0772503dc78af06`, SHA-256
`53419833e3122ebf7421d1857dc0c0eea3e718d2bcd5c6c8c29219710e591a5d`.
The portable source and restored copy were unchanged. No owned
processes or locks remained. Private verification evidence is under
`.local/headless-captain-sector/run-01/`. Its `verified.json` SHA-256
is `ea0fb97aa5dec27ad6b2e04b108999d3d3600f44444aa9377954e4747e8c5eec`.

This run verifies provider-side movement and crew persistence.
An actual cockpit gate flight with the retained captain path remains open.
