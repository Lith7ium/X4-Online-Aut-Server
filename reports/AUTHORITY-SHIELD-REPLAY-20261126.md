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

The replay save marker is `ab981dd0b380ee54d199152d644f0211`.

## Cold native readback

A fresh GPU-free X4 process on `f03e7f8a` loaded the confirmed replay checkpoint through the regular recovery path. Before the first resume, it verified KHI-630 in both directions of registry entry 1074 and read its health once. The native log recorded `READ|3.511|0x40b4a|2340|2366|3600|3600`. The shield loss survived a cold restart at the original game age, with the hull still full. The provider confirmed the load marker, exited paused, and left its output checkpoint store empty. The independent verification receipt has SHA-256 `7388b48a29afae4d2c7ad90494b92d6cd08cbd762281026f50d9cf3d2835b5ca`.

On 26 November Lith7ium selected the firing player's native X4 confirmation
for player shots and the assigned simulation for NPC shots. The server
records each hit once. [Combat authority decision and upstream comparison](AUTHORITY-SILVER-REUSE-20261124.md)

## Two-machine attempt at live shield transfer

The Fedora laptop ran the GPU-free provider. Bazzite ran X4 with hardware
graphics. Both used disposable directories and the original vanilla world.
The provider verified its marker and 5,048 registered ships. The private
setup used ordinary authenticated TLS admission through an SSH tunnel.
A separate diagnostic channel was prepared to deliver one native hit and
its exact retry to the provider during play.

| Observation | Fedora provider | Bazzite pilot |
| --- | --- | --- |
| Final attempt | Published and confirmed the offered save | Downloaded the same 13,829,351 bytes |
| Lowest available RAM | 4.73 GiB | 17.01 GiB |
| Peak full memory PSI, avg10 | 0.00% | 0.59% |
| OOM counter change | 0 | 0 |
| End state | Paused, clean exit | Supervision stopped during native load |

Both hosts verified SHA-256
`111686d447896cd1cd0f6f0c8995bf7adc3b467d4634a9a78d236b5494a6caa5`
for `checkpoint-bc14b0ed07cf44fb88df3cf740c91458`. The first split run
had stopped on a single host-wide pressure sample. The final split run used
15 seconds of continuous pressure at or above 0.20% on Bazzite. That condition
held for 15.026 seconds across 26 samples during native loading. Fedora
remained below the pressure threshold. The pilot never reached admission
or took the helm. The bridge delivered zero hits and performed zero writes.
The persisted single-hit result above comes from the earlier offline replay.

The original save digest remained unchanged. Both games, process locks and
the tunnel were released. Private local evidence is in
`.local/live-shield-bridge/split-native-02/{terminal.json,memory.jsonl}`.
For another flight, measure the loading process's own pressure alongside
host-wide PSI. The observed stop establishes the test supervisor's reason
for stopping. It does not determine the minimum RAM for playing or hosting X4.

## Server orders two hits on the same target

The Fedora laptop ran one GPU-free X4 instance paused at game age 3.511 s.
Two scripted confirmations named distinct registered ships as attackers,
the Company Pulsar HVC-420 (1073) and the Antigone freighter MFQ-419 (943).
The target was the existing Company Pulsar KHI-630 (1074). Its disposable
starting state was 30 shield and 3,600 hull. For each new confirmation,
the provider called X4's stock `apply_attackstrength` with the attacker,
target and 26 points of normal shield-or-hull damage.

| Server input, in order | Shield | Hull | Native damage calls so far |
| --- | ---: | ---: | ---: |
| Initial state | 30 | 3,600 | 0 |
| HVC-420 hit (A) | 4 | 3,600 | 1 |
| A again | 4 | 3,600 | 1 |
| MFQ-419 hit (B) | 0 | 3,578 | 2 |
| Late repeat of A | 0 | 3,578 | 2 |

A separate Lua health readback confirmed each row. X4 also recorded MFQ-419
as the most recent attacker after B, including after the late repeat of A.
Both stock calls reported a surviving target and no hit to a separate module
or surface element. The damage inputs and repeats were scripted. This proves
native shield overflow and duplicate handling in this running test only.
Receiving confirmed shots through normal multiplayer transport, preserving
their identities across reconnect, and showing the server result in two
human cockpits remain open.

## First combat TLS hit in the running Fedora world

At `992e77ea` two protocol pilots on Bazzite joined the GPU-free Fedora
provider through the regular TLS service. They reserved HVC-420 (1073) and
KHI-630 (1074) and received 36 movement acknowledgements in total. Their
cockpits and the firing pilot's local gun record were simulated.

The first pilot reported a 26-point hit on MFQ-419 (943). A separate native
read before the shot measured 29.999999702 shield and 6,400 hull. X4 executed
the damage call once. The sender retried twice and received an unknown
outcome. The service suspended further damage, so the second pilot held fire.
On last departure the native save contained 4 shield on the same MFQ-419.
The in-session receipt did not establish the immediate hull value or identify
which native observation failed to match.

The confirmed departure checkpoint was `checkpoint-3af79e50271445f5893c073649ff1757`,
with SHA-256 `610b678903fdf0961b15339c7309903180e6264c2eb572980c5a2679d1a3bccf`.
The private verification is in
`.local/live-shield-bridge/integrated-native-01/fedora-evidence/departure-verified.json`.

## Two pilots read their own live ship health

At `9188e518` HVC-420 (1073) and KHI-630 (1074) held separate
admissions in the running Fedora world. Both non-graphical pilot endpoints
requested their own ship on the normal combat TLS connection in movement
window 3. Server X4 resolved each ship from the saved registry in both
directions. The two independently requested replies each contained 2,366
shield of 2,366 and 3,600 hull of 3,600. The clients had received 37 and
36 movement acknowledgements respectively. This verifies the native source
of both replies. Neither endpoint ran a player cockpit.

The subsequent 26-point report on MFQ-419 exposed a receipt precision
error. Before the hit the independent read was 29.999999702 shield.
Afterwards the independent Lua read was 3.9999997019768 shield and 6,400
hull. The individual Mission Director numeric events arrived in Lua as
29 and 3. The display-formatted Mission Director witness rounded the
post-hit value to 4. Comparing the truncated event with the independent
readback returned an unknown outcome. The provider stopped further writes.
Private evidence is in
`.local/live-shield-bridge/pinned-9188/checkout/.local/live-shield-bridge/integrated-native-03/terminal.json`.

## Fractional shield and hull receipts in X4

On `4e7dd6c8` the Fedora fixture loaded one GPU-free X4 with 5,048
registered ships and held game age at 3.511 s. The private test supplied
the two requests locally and allowed the production damage driver to run
while paused. New receipts carry the integer and fractional parts of the
Mission Director values together. Lua still returns its independent
native health readback.

| Input on MFQ-419 | Native shield | Native hull |
| --- | ---: | ---: |
| Before either hit | 29.999999701976776 | 6,400 |
| HVC-420 hit, 26 points | 3.999999701976776 | 6,400 |
| KHI-630 hit, 26 points | 0 | 6,377.999999701977 |
| Old HVC-420 request repeated | 0 | 6,377.999999701977 |

Three packet deliveries caused two Mission Director damage calls and two
product receipts. The Python validator accepted both receipts. The maximum
observed difference between the encoded MD reading and the independent
Lua reading was 0.000000656 HP. The raw final read also matched the
second receipt after the old request.

The private supervisor exited 1 because its completion marker appeared
twice. Both markers recorded the same counts. Independent verification
read the untouched native log, checked every damage dispatch and receipt
and accounted for that repeated terminal line. There was no game save
or pilot TCP connection in this codec test. The source save remained
intact and the server process and lock were released.
The private raw witness is `.local/live-shield-bridge/codec-native-02/native-witness.txt`
(SHA-256 `ee8f0a80cf5230d0ec0f5c6bd4277054cb3bc988aac75302286f06967945a13d`).
