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
