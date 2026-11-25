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
