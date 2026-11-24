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
