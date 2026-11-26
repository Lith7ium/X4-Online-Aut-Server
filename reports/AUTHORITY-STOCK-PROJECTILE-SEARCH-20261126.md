# Native stock projectile search positive control

On 26 November 2026, a single Linux X4 instance loaded a disposable
copy of the sealed Company test world on Bazzite. HVC-420 (1073) was
under local control and fired one native 0.9-second stock burst from
six Bolt Repeater Mk2 mounts. The empty KHI-630 (1074) was at an 850 m
separation in the prepared Black Hole Sun V lane. Damage forwarding was
disabled throughout.

## Query result

The independent test cue obtained exact projectile handles from stock
weapon-fired callbacks. All 66 distinct handles, 11 from each mount,
were subsequently observed alive and within range. Thirty-five
milliseconds was the controller's requested scan interval. The 191
native samples covered game ages 4.44829–12.17850 s. The largest gap
was 125.43 ms overall and 41.42 ms while live projectiles were observed.

The 50 positive samples contained 1,095 live, in-range observations
with as many as 49 distinct live rounds in one sample. Their
simultaneously read distances were 23.36–2,742.50 m from HVC and
1.23–1,892.25 m from KHI.

| Stock `find_object` bullet query | Total returned | Known live rounds found |
| --- | ---: | ---: |
| Sector, within 10 km of HVC | 0 | 0 |
| Sector, within 10 km of KHI | 0 | 0 |
| Source zone, within 10 km of HVC | 0 | 0 |
| Source zone, unfiltered and recursive | 0 | 0 |

These are false negatives for the sampled rounds. The observer read
each projectile's existence and distance in the same cue execution as
the search. Births ran from 8.08162 to 8.82295 game seconds and live
reads from 8.11430 to 10.09600 s. The installed stock round has a
1.3-second lifetime and speed of 2,100 m/s. Short lifetimes or a missed
sampling interval cannot explain a zero result for a simultaneously
live in-range handle. The nearby ship query returned two in every scan.
Nearby station and missile queries returned zero. There was no positive
missile control, so missile-query coverage remains unverified.

## Contact and cooldown

The exact observer from `6713a641` refused to arm at 4.45 s with
`range_or_fixture_refused`. The controller recorded that status and
then ran the single independent, read-only positive control at the
7.94 s firing trigger. The candidate emitted no watermark. The
refusal reason does not identify the individual failed guard.

The independent cue saw two direct contacts from the exact HVC actor
to the exact KHI actor. At 8.63467 s, the root direct callback preceded
three root shield callbacks at the same displayed age. The second
direct callback, at 9.06493 s, named a KHI subcomponent and preceded
five root shield callbacks at that displayed age. Those groupings
establish callback order and identities. They do not identify which
round produced each debit.

Forty-six root shield callbacks recorded 26 points each, changing
shield from 2,366 to 1,170, with hull stable at 3,600. The last debit
was at 9.20899 s. The query observer continued for another 2.97 game
seconds after that debit and 2.08 s after the last live-round read.
It observed no further health change. The independent trace contained
no hull-damage or collision callbacks. The sandbox exited with status
0, reverified the sealed original, and released the native slot.
Its game, private display and owned scopes had exited.

The documented Mission Director `event_weapon_fired` supplies the
projectile handle for each watched weapon. It can track known sources
and gives no census of rounds from unwatched sources. The installed
schema also documents `event_player_ship_hit` with a target, attacker
and projectile, scoped to the player ship or ship context. That event
is a contact witness to test at a receiving cockpit. It supplies no
range exclusion. The measured search cannot attest exclusivity for
damage forwarding on this shared lane.

## Evidence fingerprints

| Artifact | SHA-256 |
| --- | --- |
| Sealed Company checkpoint | `1ceb756b1fc6080c6a6752e51d146cfc17c72762b8af9f9c1aed7bce20fa4958` |
| Installed Linux X4 executable | `113f73d74570084f6ce18a659b365009eccf579fb66dbabb5dad2b1718045f20` |
| Exact candidate MD | `45f1f6989d374757bb9f4451bb35b79646b46f7ef08f0def7f23970e966c9e94` |
| Independent census MD | `f6d804b7eeb2a589c83964d662d1219c0fb4e85a5f3e812c4fb45e4a9a5e618d` |
| Controller Lua | `87782e85972c1b53c5dcbe29b8002ab6e8f1b99e1eb378d2b5088d00e14eac67` |
| Stock firing fixture MD | `6344de4354df55c25c277d9dda6eedb58b9edda41664c3fd625c5172e8a8c163` |
| Offline verifier | `d38da610540a796ad53fcb44b51e277ad802594a30bd3434903dd6ed9b75365a` |
| Raw engine log | `206a3f2c5e0b24fadd61f565385ac379ee77fe24761df97c5cfa05b4033182e6` |
