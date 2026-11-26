# Shield hits from an admitted Company pilot

At `0e7c9900`, HVC-420 joined a GPU-free provider through the regular
Linux player launcher on Bazzite. The pilot took the helm of registry
ship 1073 and the provider acknowledged its motion. The observer armed
against the offered world's existing registry, epoch
`26a1a365c66788aa06f0aeb9e18ff62e`, and watched HVC-420 and KHI-630
(1074). The target had no pilot. Both ships had the vanilla Pulsar loadout.

The initial 3 km approach timed out before firing. On a fresh client
copy the fixture placed the existing KHI-630 850 m ahead of HVC-420.
Native readbacks confirmed their positions and full health before enabling
a separate stock `shoot_at` stimulus on HVC-420 for 0.9 seconds.

| Measured on the firing client | Result |
| --- | --- |
| Shield damage callbacks on KHI-630 | 49 × 26 HP |
| KHI-630 shield, before volley → after last hit | 2,366 → 1,092 HP |
| KHI-630 hull | 3,600 HP throughout |
| Attacker in the shield callback itself | Unspecified on all 49 hits |
| Ship in the victim's `lastattacker` field | HVC-420 on all 49 hits |

The first hit was callback 49. Its victim and last attacker matched the
registry epoch, indices and ship codes in the pilot's offered checkpoint
`checkpoint-4a7f393a06f8410fbaf1dc08e0961684`. Local component handles
can differ across X4 processes. The match uses the save's registry.
With multiple attackers, the last attacker alone cannot settle which
ship fired each projectile.

The provider's departure checkpoint retained the starting health fields
for both ships and the world paused. This run measured local damage and
save identity. Damage replication and flight with a second human remain open.

The private artifacts are in `.local/registered-combat/native-02`. From
the checkout, reproduce the first-hit lookup using the save digest from
the pilot's offer:

```sh
run=.local/registered-combat/native-02
python3 tools/dev/authority_combat_hit_evidence.py \
  --store "$run/server/checkpoints" --checkpoint checkpoint-4a7f393a06f8410fbaf1dc08e0961684 \
  --sha256 ace5a0a0e0fd9e7d3e659b40be496e5efee0b8029dafe8d0e67a4418a88b83e4 \
  --log "$run/cockpit-1073/client/case/home/.config/EgoSoft/X4/45841/engine-output.log" --sequence 49
```

For a trial that requires the pilot to be hit, pass `--piloted-ship-index`
with the registry index from that pilot's admission. The verifier checks
the victim's code and Company ownership in the offered save, and the live
ownership, occupancy and control at the instant of the damage event.
Replaying the archived sequence 49 with target index 1074 rejected
KHI-630 because nobody occupied or controlled it.

### Outgoing gun contact verified on 26 November

The saved log also contains the firing ship's own `object_attacked_object`
notification, sequence 48 at game age 5.09576. The verifier checked the
checkpoint recorded in the pilot's load log, marker and registry. X4
reported HVC-420 (1073) at the controls firing on KHI-630 (1074), with
the target's root component and firing weapon in the same notification.
On this process the weapon handle was `0x40b1e`. The notification arrived
before shield subtraction. The following callback recorded 26 shield HP
lost. The direct notification occurred once in that volley of 49 hits.
These two events cannot account individually for hits by overlapping shooters.

All 49 shield callbacks on KHI-630 reduced its shield by exactly 26 points,
from 2,366 to 1,092. The hull stayed at 3,600. Each callback identified
HVC-420 as the last attacker, but the last-attack time stayed at 5.09576
through the final callback at 5.74137. The 49 impacts span 27 distinct
game times, with up to four at the same time. Six guns produced 66 firing
notifications. Neither a timestamp nor a firing notification identifies
one projectile across this burst. The direct contact and the first shield
debit describe the same hit and must only be counted once.

To inspect the archived notification, use the same store, checkpoint,
digest and log above with `--shooter-ship-index 1073 --sequence 48`.
The opt-in pilot capture also records outgoing bullet contacts while the
admitted pilot occupies and controls their ship. It checks the helm in
the native event callback. The local damage receipt keeps its existing
incoming-only format.

### Occupied victim on Windows

Cobalt reported a native Windows 11 run at `28b36a12` with X4 9.00
and CPython 3.12.10. Xenon ZUT-626 fired 12 shots at NMY-676. X4 recorded
six shield hits of 23 each, dropping shields from 2,366 to 2,228 while
hull stayed at 3,600. Every victim record identified the same occupied
and controlled Company ship, index 1. The shield events had no event
attacker. The six `player_ship_hit` events identified ZUT-626, index 5,
and the projectile macro. These identities belong to the creative
fixture's in-memory registry. A provider offer and saved registry are
still needed to verify an admitted Windows pilot's hit.
[Cobalt's native result](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795651241349479)

### Windows capture for a resumed pilot

Cobalt contributed an opt-in Windows capture at `2bde57b9` and reported
91 passing Windows fixture tests. The first integration installed the
observer during the load barrier. An integration fixture reproduced the
missing follow-up tick after the barrier closed. The integrated observer
now receives updates from the regular participant loop once the session
has resumed and the pilot controls the reserved ship. On loss of that
binding it disarms its own observer. A passenger can still join while
waiting for the helm. [03:00 development check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795658448865639)

The local receipt reader streams X4's log. A capture window identifies
the offered checkpoint ID and SHA-256, registry epoch, seat index and
ship code. Only the private observer's damage callbacks for that
occupied and controlled ship within the window can be listed. A later
save can share the epoch and ship index with the first one. Supplying
the other save's ID or digest to the reader fails. The verifier resolves
each selected native sequence against the actual offered save.

At `4e40a317`, 67 tests passed on Linux across the Windows CLI, session,
full registry overlay, capture and combat observer. The LuaJIT cases
exercise admission, delayed acknowledgement, pause, regained control
and loss of the selected helm. The next native result requires a hit on
an admitted pilot on Windows. Shared damage and a two-player fight remain open.

## Occupied target attempt on Linux

One further isolated attempt at `3b5f00d4331f0aad32e4b5017fbe00e098635239`
used a fresh copy of the confirmed Run 02 source. The pilot fixture was
prepared to assign a native Company NPC captain to registry entry 1074
(KHI-630) locally, confirm both registry directions and captain assignment,
then request one 0.25 s stock burst at HVC-420 (1073) only while HVC was
occupied and controlled by the admitted pilot. Health reads were passive.

The provider confirmed and offered `checkpoint-8588ae0463304a26be1430e7c5e70dd8`
with SHA-256 `cfdc798c41169f2bcc04279e39faf97c503620807f1622aea42ada9f757683c2`.
The supervisor stopped on one host memory pressure reading. At 221.48 s
the kernel's `full avg10` reached 0.60%, above the 0.20% stop threshold.
15.43 GiB of host RAM was available. The provider and pilot used 6.51
and 1.44 GiB of proportional set size at that point. The earlier successful
Run 02 recorded 0.00% on all 385 samples, with a minimum of 12.30 GiB
available. The logs did not record the cumulative stall time.
The cockpit was in `load_receipts` and had yet to attempt admission.
Its engine log also reported `Failed to create file for writing:
config.xml` before reaching the start menu. The relationship between
that warning and the interrupted load is unverified. The captain cue and
shot never ran. This attempt produced no new hit, health or projectile evidence.

The provider exited successfully, paused at the offered checkpoint with
zero admissions. No owned processes or locks remained. The original and
staged source save hashes matched
`1ceb756b1fc6080c6a6752e51d146cfc17c72762b8af9f9c1aed7bce20fa4958`
after the run. Private evidence is in
`.local/registered-combat-occupied/native-03/`. The terminal summary
SHA-256 is `bef5ed9f53c09f50276bd412c4da5775b92871e2db85e81fc3bb390049865d90`.

## Occupied pilot hit through the regular Linux capture

At `f03e7f8a`, two native X4 9.00 instances ran on Bazzite with separate
disposable profiles. The software-rendered provider offered a fresh
checkpoint over pinned loopback TLS. The ordinary RADV cockpit used
`--combat-capture`, verified the downloaded digest, observed the native
load marker and confirmed one server resume. The pilot was admitted in
control of HVC-420 (index 1073). The product capture armed on that seat,
registry epoch and exact offered checkpoint.

The private cockpit stimulus positioned KHI-630 (1074) 849.875 m from
HVC-420 with both ships' starting health intact. After verifying its
temporary NPC captain, the stimulus started one 0.25 s stock burst
and acknowledged the native weapon release. The product observer
recorded 21 shield damage callbacks on the occupied and controlled
HVC-420. Each was 26 shield HP. Shields fell from 2,366 to 1,820 and hull
stayed at 3,600. Every damage callback had an unspecified event attacker
and reported KHI-630 as the victim's last attacker.

Separately, 21 native `player_ship_hit` records identified KHI-630 as
the event attacker. Each had a distinct projectile handle and the macro
`bullet_gen_s_gatling_01_mk2_macro`. All 21 correlated to shield callbacks
by game age and the post-hit shield value. The scoped batch verifier
matched the complete ordered damage receipt to the actual offered save
in one registry scan. The projectile records are separate from the
damage receipt.

| Checkpoint | ID | Save SHA-256 |
| --- | --- | --- |
| Confirmed source | `checkpoint-6e5948ba473f4b399c01fcac9ba2f7bc` | `1ceb756b1fc6080c6a6752e51d146cfc17c72762b8af9f9c1aed7bce20fa4958` |
| Pilot offer | `checkpoint-3994b4737d1e41cdb796f7541ea18e54` | `11a866431b3d5f39dad426cc238732e04409d0ca7413b9ab56d5ee3db0565fc1` |
| Confirmed departure | `checkpoint-c1adcfb36995455b8a545d0fa78b873f` | `0f4e93198f720f357a27a69d0dc90260e18a4d9ce729934d1a88c3a1d146132d` |

The original and staged source bytes matched before and after the run.
The provider paused after the pilot left and confirmed the departure
save. Independent inspection found the same serialized health fields
for both ships across the source, offer and departure. Damage capture
was local to the pilot. Shared damage and a second human combatant
remain untested.

Both launchers returned status 0. The X4 processes ended and the host
locks cleared. The existing resource gates stayed active. Across 381 samples,
available memory stayed above 13.86 GiB, full memory-pressure
`avg10` peaked at 0.00%, and both sampled cgroups recorded zero OOM
events. Private artifacts are under
`.local/registered-combat-capture-linux/native-01/` in
`terminal.json`, `combat-capture.json` and
`raw-projectile-observations.json`.
