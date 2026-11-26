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

## Hit on a reserved pilot over TLS

At `4e7dd6c8` two scripted protocol pilots on Bazzite joined one running
GPU-free Fedora X4 world through the regular pinned TLS service over the
private SSH tunnel. They verified their separate checkpoint offers and held
active movement grants for HVC-420 (1073) and KHI-630 (1074). B kept its combat
connection open for the initial and post-hit health reads. A's regular combat
sender read a scripted gun contact for KHI-630 and submitted a 26-point hit.

| KHI-630 observation | Revision or receipt order | Shield | Hull |
| --- | ---: | ---: | ---: |
| B's first native read | 0 | 2,366 / 2,366 | 3,600 / 3,600 |
| A's confirmed hit receipt | 1 | 2,340 | 3,600 |
| B's fresh read on the same combat connection | 1 | 2,340 / 2,366 | 3,600 / 3,600 |
| B's fresh read after reopening its combat connection | 1 | 2,340 / 2,366 | 3,600 / 3,600 |

A replayed the exact first report and received the original receipt with
order 1. B's intervening request with revision 1 reported no update. The
native adapter recorded one damage call against KHI-630 and three fresh
health reads for B. The two post-hit native reads completed 1.743 and
3.774 seconds after the server settled A's hit.

In the same session A and B then submitted one 26-point report each against
the registered NPC freighter MFQ-419 (943). The disposable baseline measured
29.999999702 shield and 6,400 hull. The first receipt carried
3.999999701976776 shield and 6,400 hull. The second carried zero shield
and 6,377.999999701977 hull. A separate native read confirmed the final
health to the precision of the private log. The adapter recorded three
damage calls for the entire session.

The regular last-pilot departure saved
`checkpoint-062684a012974e12830d7ecd85c6076d` with marker
`26754b3dd6c1a417cf7f38f759438cd5` and SHA-256
`4dfbcc819a4f1a326206ed8b1f7604ba5e86ca063337158f8740fcefbab41882`.
Independent inspection read 2,340 root shield on KHI-630 and zero shield
with 6,378 hull on MFQ-419. The provider exited 0 with the world paused
after two admissions and 142 movement samples. The sealed input retained
its checksum. Fedora closed the TLS listeners and released the engine lock.
Fedora's available RAM bottomed at 4.219 GiB. Full memory pressure and OOM
counter increments stayed at zero.

The private runner exited 1 during its optional NPC comparison. It used
exact equality between the production hull receipt and a private line
formatted to nine decimal places. The values differed by about
`2.36e-11` HP. An offline recheck of the original evidence passed using
the log's precision. The test exercised the product sender, TLS, native
damage and native health reads with scripted contacts and protocol pilots.
Native client gun capture, the result in two human cockpits and a public
WAN run remain open.

Private artifacts are under
`.local/live-shield-bridge/pinned-4e7dd6c8/checkout/.local/live-shield-bridge/reserved-victim-native-01/`.
`terminal.json` is the original driver result and
`independent-verification.json` records the offline recheck.
`fedora-evidence/` contains the raw adapter journal, native witness,
provider/server results, memory samples and their checksum manifest.

## Windows cockpit correction with fixture server values

Cobalt ran the opt-in receiver in X4 9.00 on Windows with a living,
occupied scout, ZPO-868. Independent Mission Director reads confirmed
shield/hull changed from 1,196/1,400 to 478.4/770 and then to
1,076.4/1,190. A deliberately delayed pre-gap update produced no
command or receipt and the hull stayed at 1,190. A fresh baseline after
the gap set 777.4/980. Three updates were confirmed by the native receiver.

The source was a local fixture that supplied the movement window and
health replies. The run exercised the real Windows client bridge, the
network DLL and the game's health receiver. The authenticated server
connection and combat worker were outside this run. Cobalt retained the
raw logs on Windows and published the test harness. [Windows experiment and tests](https://github.com/Lith7ium/X4-Online-Aut-Server/commit/1bb2eace5c574f827700624ddf023773e95b58a8)

## Two Linux cockpits connected to the Fedora server

At `56a00bcc`, two automated X4 cockpits on Bazzite joined a GPU-free Fedora laptop over pinned TLS through a private LAN tunnel. Both pilots controlled separate Company ships in the same sector. After the second join, each cockpit independently read 2,366 shield and 3,600 hull in running window 3. Fedora recorded two admissions and 1,527 accepted motion samples across the session.

HVC-420 fired for 0.25 game seconds at its local copy of KHI-630 from a verified 849.875 m. The shooter recorded twelve identified stock S Bolt Repeater Mk2 projectiles and completion of the fire command. During the following 90 real seconds the shooter's observer recorded zero shield callbacks on KHI-630 and zero outgoing direct hit reports. The passive target cockpit recorded its full-health baseline. Fedora's adapter recorded six health reads and zero damage dispatches. This run completed the two-cockpit admission and firing checks. A hit travelling from one native cockpit through Fedora to the other cockpit remains open.

Both clients exited normally. After last departure Fedora paused and saved `checkpoint-f725898d3caf484aac94e03d70670c9d`, SHA-256 `41b632b4f67174fa7317f411f9c2afe33f0fc0104eee76658c486848963d2a2f`. The original save was preserved. The private run is `.local/company-duel-native/native-03/`. The native shooter log SHA-256 is `5ff67c9445b34cf3cf298c7cab58f44253804a528612b6214216009d9207ada9`. The target log SHA-256 is `c5e898380cf50f175cb24d248534090e041da7f52b5ef1bd33bbe2eceb55f437`.

## Three ordered stock impacts in one provider update

At `56a00bcc`, two scripted protocol pilots on Bazzite held ordinary
Company reservations on the native Fedora X4 9.00 provider over pinned TLS.
A private fixture set the NPC freighter's shield to 30 and submitted a
fixed impact order to one Mission Director cue. Stock
`apply_attackstrength` ran once per row. The cue checked that the game age
before and after each call matched its entry age, reported as 8.01344 s.

| Order | Source | Target | Shield / hull before | Shield / hull after |
| --- | --- | --- | --- | --- |
| 1 | HVC-420 | MFQ-419, NPC | 30 / 6,400 | 4 / 6,400 |
| 2 | HVC-420 | KHI-630, reserved | 2,366 / 3,600 | 2,340 / 3,600 |
| 3 | KHI-630 | MFQ-419, NPC | 4 / 6,400 | 0 / 6,378 |

The fixture first resent the exact ordered payload under the same ID.
It then resent that ID with the first two impacts swapped. X4 reported
`DUPLICATE` and `CONFLICT` on later updates. The stock damage call count
remained three. Independent Lua reads confirmed both targets at game age
9.92108 s. The UI log recorded monotonic dispatch-to-response intervals
of 0.988 s for the original request, 1.000 s for the duplicate, and
0.908 s for the conflict.

The regular last departure saved
`checkpoint-36c32bf5ac6a4f82826722f925c4f4e3` with SHA-256
`0d571dacfc285b13a7677676568a909598ad705c29918bd034ec0a9ca4489eb5`.
Independent save inspection found 0 shield and 6,378 hull on MFQ-419,
and 2,340 shield and 3,600 hull on KHI-630. The source save was preserved.
The provider exited normally with the world paused after two admissions
and 33 accepted motion samples. Both hosts were clear of game processes
and engine locks after cleanup.

The fixture exercised ordered writes in one native provider update
during real protocol admissions. Player-fired volleys, the product combat
batch path and end-to-end cockpit updates remain unverified by this run.
The first attempt ended before admission after X4 required explicit zeroes
for the unused stock damage channels. The corrected attempt ran in a fresh
disposable profile. The private evidence is under
`.local/ordered-gun-trial/ordered-native-02/fedora-evidence/`. The filtered
native witness SHA-256 is
`4edc269622e4dafc20907220671a5a0b30c942c03c0e596167fee164af48876c`.
