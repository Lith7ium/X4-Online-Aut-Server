# First cross-instance shield-state replay

A separate, GPU-free X4 9.00 process at `0026960b3230df0f854f357e3f0e0bcff8b46a18` loaded a disposable copy of the actual offer from an admitted Linux pilot's run. The source log recorded shield callback 49 for KHI-630, Company registry index 1074. It applied 26 points of shield damage, leaving 2,340 of 2,366 shields and 3,600 of 3,600 hull. The callback named no event attacker. HVC-420 was the victim's recorded last attacker. The saved registry confirmed both ship identities. See the [source hit](AUTHORITY-REGISTERED-PILOT-COMBAT-20261125.md#shield-hits-from-an-admitted-company-pilot).

The fixture verified the offered checkpoint's marker, registry and SHA-256 against the pilot's downloaded bytes. In the new paused process, it resolved KHI-630 by registry index and required the reverse mapping, ship code, Company ownership and exact baseline health. A single stock `set_object_shield` call used the recorded post-hit value as a percentage of the live maximum. Three separate Mission Director receipts recorded the same game age of 3.511 seconds.

| Receipt | Shields | Hull |
| --- | ---: | ---: |
| Baseline | 2,366 / 2,366 | 3,600 / 3,600 |
| After one write | 2,340 / 2,366 | 3,600 / 3,600 |
| Independent readback | 2,340 / 2,366 | 3,600 / 3,600 |

The normal save bridge published a confirmed marked checkpoint while paused. Independent XML inspection resolved KHI-630 in the saved registry and read 2,340 in its root shield group at game age 3.511. The original offer and downloaded copy retained their digest. The isolated provider closed with no client endpoint or remaining owned process.

| Evidence | ID or path | SHA-256 |
| --- | --- | --- |
| Actual Linux offer | `checkpoint-4a7f393a06f8410fbaf1dc08e0961684` | `ace5a0a0e0fd9e7d3e659b40be496e5efee0b8029dafe8d0e67a4418a88b83e4` |
| Confirmed replay save | `checkpoint-404d570ecc5c4ed4a6d70f028e3bc022` | `80ea840d714dc12ac99265ee3daf7f8060255f26f1269c3fc7a520d0d3e59132` |
| Executed private MD script | `.local/shield-playback/run-01/server/private_shield_playback.xml` | `48da94fb4abe99ed9e2cac855fb1a5330be7569f33c114c1f5e7f91b5ab5e9e1` |
| Independent verification receipt | `.local/shield-playback/run-01/verified.json` | `8e7e7dde5c0a2b9500fb759c0ec9c08df11f1329fa0a28b381102544795e1b9c` |

The replay save marker is `ab981dd0b380ee54d199152d644f0211`. This run establishes one-shot playback of an observed shield state and its persistence in the saved bytes. Live damage transport, the decision about which simulation settles a hit, and a cold native reload of the replay save remain open.
