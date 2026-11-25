# X4 9.00 boarding baseline

Installed vanilla X4 reports version 900. The inspected boarding code and map
menu came from the local 08 catalog and data pair. The 09 catalog contains no
replacement for either file.

1. At operation creation, retain the operation reference and its boardee.
   The stock map menu creates an operation for the target and player faction,
   adds or updates its attackers and starts it. Capture the attacker ship
   references at start if the authority needs to attribute the action to a
   Company participant. Vanilla's involvement flag checks whether any attacker
   belongs to the player faction. A provider avatar elsewhere does not suppress
   either success signal or the takeover in the inspected code.
2. Listen for the target-directed `boarded` object signal. Its object is the
   actual boardee and its second parameter is the winning faction. On the same
   ship reference, confirm that the ship remains operational and the true owner
   changed to the player faction. The stock cue emits success before invoking
   the ownership transfer, so verify ownership separately from signal delivery.
   Removal is generic cleanup and also follows defeat and stalemate. A true-owner
   change alone can include docked vessels captured by the recursive transfer.
3. Resolve the target through both directions of the authority ship registry.
   Record the registry epoch and index, actual component reference and macro,
   and checkpoint SHA-256. Check the saved association and the unique ship
   component, then verify the same owner after reload. Read the assigned AI
   pilot separately. Stock transfer dismisses the former hostile controllers,
   reinitialises control entities and transfers the surviving marines. A
   captain-dependent action requires a player-owned NPC assigned to control that
   exact ship. The display ID is useful for diagnostics.

## Native probe

In a disposable provider profile, observe a stock boarding attempt with the
provider avatar outside the boarding ships. Compare a victory and a canceled
operation on the same setup. Record creation, start, target-directed signal,
true-owner transition and removal by operation and ship reference. On victory,
checkpoint and reload, and compare the target registry binding, owner and
captain state. Count only the original boardee if docked vessels also change
owner. This baseline used static inspection. Native signal delivery, the
captain's state immediately after victory, checkpoint recovery and remote
participant attribution remain unverified.

## Evidence

| Source | Reference points | SHA-256 |
| --- | --- | --- |
| 08 catalog, `md/boarding.xml` | Creation 5–17, start 263–286, failure and stalemate 1440–1479, victory 1490–1534, removal 1546–1571, transfer 2658–2745 | `c3811f36723031eb21f1f9c4dc93a6953c75104a6b8a0288392922a291a331b7` |
| 08 catalog, `ui/addons/ego_detailmonitor/menu_map.lua` | Creation and start 2284–2303, attacker assignments 29331–29350 | `6c56ba3881495ad8ee7db3a144c16079f2b2eaad56a3a1e5efe171f899fd6865` |
| 08 catalog and matching project schema, `libraries/common.xsd` | Operation events 12120–12166, true-owner event 13241, object signals 14614 and 37149 | `8eec878511d06dd2be88a7054eb54556b29e481bf0feba20fe2cf3c6eabd3452` |
| 08 catalog, `libraries/scriptproperties.xml` | True owner 73, display ID 198, assigned AI pilot 305, controlled object 1401, boardee and attackers 1598–1599 | `1b641b638c8892284bbf5943f9ed283e0adf79f696341a2f00a2361f16485a8f` |

Fork revision `ba9cc1715c4e478cdbc7cfcecf19fd999fc2fafa`. Existing registry
and captain checks are in `src/authority/md/client_seat.xml` at 91–100 and
116–124. Saved registry and ship component verification is in
`src/authority/registry_evidence.py` at 180–214.
