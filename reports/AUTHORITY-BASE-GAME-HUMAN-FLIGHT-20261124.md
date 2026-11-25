# Two human pilots in Black Hole Sun V, 24 November 2026

The pilots saw one another's ships. The Bazzite pilot explicitly selected
the Windows pilot's ship as the HUD target and still saw no movement.
The Bazzite pilot fired on the Windows pilot's ship and saw damage locally.
The Windows pilot saw no corresponding damage. The pilots did not distinguish
shield loss from hull damage in their report.

## Session

Fedora hosted the base-game-only world on a third machine. Bazzite connected
over the LAN and Windows connected over the internet. The two Company Pulsar
Vanguards started about 3 km apart in Black Hole Sun V. The run used the
human-test integration branch through `9e431c7a`.

| Evidence from run 02 | Bazzite | Windows |
| --- | --- | --- |
| Native game | World marker, registry of 5,048 ships, seat HVC-420 (1073) | Same marker and registry, seat KHI-630 (1074), initial pause observed |
| Control admission | Accepted | Final acceptance unconfirmed in the available log |
| Outbound movement | 392 server acknowledgements, including 391 samples with velocity | No confirmed publications |
| Inbound movement | Zero snapshots | No confirmed receipt |

Fedora accepted five control connections. Two distinct control handles reached
server-side admission after a successful load claim. The host report did not
identify the seat for each handle or show that Windows received its acceptance.
Bazzite recorded one resume acknowledgement. Its launcher selected ship 1074
for cockpit playback. The delivered Windows starter selects 1073 for playback.

Fedora independently recorded 392 accepted movement samples, 391 with velocity.
Its last accepted sample belonged to ship 1073. Its totals match Bazzite's
outbound acknowledgements. Acceptance records arrival at the server. The human
test provides no measurement of movement applied on the other pilot's machine.
Both pilots deliberately closed X4. After the last departure Fedora paused
and saved the world. Both checkpoint pointers identified
`checkpoint-22475d6fe7534ccb8d198d931e9855da` at shutdown. The provider
exited with no owned process remaining.

## Saved state

The confirmed starting checkpoint and final checkpoint were 70 simulation
seconds apart. Both still resolved the two registry indices to the expected
fighters, undocked in the same zone. Each retained its starting position;
their separation was 3,000 m in both saves. Neither ship's saved component
tree contained an explicit current hull or shield value. Those endpoints
cannot tell us whether a temporary shield hit occurred during the session.
The provider used the motion relay for this run. The optional native server
position writer had previously been exercised on a different two-ship world.

## Findings for the next automated tests

* The cockpit observer in this build reads the selected HUD target and checks
  its visibility. An untargeted ship can be visible in the world without gaining
  a motion subscription. The earlier native playback test explicitly selected
  the other ship. The Bazzite pilot's follow-up rules out failure to select
  the target on Bazzite as an explanation for this result. A selection does
  not prove that X4 resolved it to the registered ship or subscribed to it.
  [Cockpit reader](../src/authority/lua/company_observer_target.lua) ·
  [Earlier native replay](AUTHORITY-COMPANY-SHIP-PLAYBACK-20261124.md)
* Trace the Windows source first. Fedora's complete movement count is already
  accounted for by Bazzite. Record admission, channel binding, source samples, view
  subscriptions and remote receipts per pilot. To pass the next flight test,
  both pilots' X4 instances must independently read back the other ship's
  displacement and velocity-based playback while their own ship remains under
  local control. Include a nearby ship without a selected HUD target.
* The Company movement message contains position, clocks and optional
  velocity. There is no shield, hull or hit replication on this path. The old
  LAN implementation offers separate health and hit mechanisms to investigate.
  The test did not measure the target's authoritative health on Fedora.
  [Movement message format](../src/authority/experimental_tcp_motion.py)

## Private four-Pulsar trade preparation, 25 November 2026

The provider-only preparation started from the confirmed Black Hole Sun V
checkpoint `checkpoint-4184bc8dbb414f188d96b7e7caf08918`, save SHA-256
`88f9f8ffba964a1da47761dac1a1ddd6e319ecd0d3d3d5b5afca813bc76d1f19`.
The original private archive `.local/base-four-fighters-seed.zip` retained
SHA-256 `2139fdb7ba217f8fb9cf8d8739be018c69c26c3474d8f8a86751a4a8c4143f47`.
The Company catalogue held the existing scout YWK-071 (1076) and four
Pulsar Vanguards, BBS-493 (1077), EST-929 (1078), OVO-739 (1079) and
XNA-002 (1080). The Pulsars started on a 3 km square in one zone.

Both runs assigned one Company NPC captain to BBS-493 and added four
energy cells. The four loadouts and all five catalogue entries passed
the save checks. BBS-493 moved 5 m along the same zone's positive Z axis
during engine updates. The other three Pulsars held their exact saved
positions. Run01 on `04f409f6` wrote a marked candidate and stopped at
the original exact-position check. Run02 on `561dc9db` started from a
fresh copy of the original and passed the revised 5 m limit for
BBS-493. It paused and confirmed
`checkpoint-924ab73caa75491d97598b73b85de645`, save SHA-256
`2966ae73ef1c0779684fb9dbd88cb031e89f44d31f874907d8648f9e809cae28`.
The saved captain seed matched the native readback
`-97168325741795052`.

DMU-594 reported a free external S berth and an energy-cell bid of
15.80 Cr per unit, with 9,507 units requested and a minimum of one.
The ships stayed in space and the Company balance stayed at 10,000 Cr.
This world now has one prepared seller. The trials booked zero sales.
Their private evidence is under `.local/vanilla-four-trade-seed/`.

The portable exporter wrote `.local/base-four-fighters-trade-seed.zip`,
SHA-256 `290b8cd2308acfbd6bfa0d2ffd0eaaccbdf9d436c95e5d02cd16233fbb0e55cf`.
An offline restore matched all ten payload files byte for byte and verified
the save marker, captain, registry indices, loadouts and positions. The
private archive includes only the seed manifest and the allowed recovery
files. Operator logs and connection credentials stayed outside it.
