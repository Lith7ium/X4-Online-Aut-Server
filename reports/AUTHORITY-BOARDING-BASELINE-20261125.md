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

## Saved marine transport for the first operation

An isolated GPU-free Linux provider at `063d3033` loaded a copy of
`checkpoint-39a02fb17cc24fad896b3b578f9fdd08`. It created one vanilla
Argon M transporter, ARU-541, with a Company NPC captain and eight marines.
It was 18,544 m from ZNG-430 in `cluster_06_sector001_macro`.
ZNG-430 occupied registry index 1666. Its displayed faction was Antigone
and its true owner was Scale Plate. The fixture checked the target and its
assigned captain before and after creating the transport.

The regular checkpoint completed and the provider exited. An independent
read of the saved components found the transport, its captain and eight
crew members with the marine role. ZNG-430 retained its registry
association and Scale Plate ownership. The transport had no index at
creation or in the sealed 5,048-entry registry. It still needs an
association before a boarding witness can attribute an operation to it.
Boarding eligibility and operation outcome remain open.

Checkpoint `checkpoint-bf225d9dab2e4f9c8da03240e8b75ad1` has SHA-256
`b80e43e31242758bdd1b422d157c87b46a3c327399fcc58d810957763feb1bd4`.
The private provider result is in `.local/native-boarding-crew-seed/run-01/server/result.json`.

## Can the parked server avatar select these marines?

At `6eaf62ae` a fresh Linux X4 process loaded that checkpoint. The server
avatar was aboard UPV-940 in the adjacent sector, as recorded in the
source save. The regular registry Enroll assigned ARU-541 index 5049 in
memory. The subsequent correlated Resolve returned the same X4 object.
MD independently found its captain and eight marines. ZNG-430 still
occupied index 1666. The distance between the ships was 18,544 m.

The same process read the stock map menu's getters. X4 reported Board as
selectable for ARU-541 and ZNG-430 as capturable. It counted eight marines,
44 places on the target and boarding resistance 558. The initial allocation
limit was eight. The two ships shared a sector and each was in a different
sector from the avatar. These observations cover selection eligibility;
boarding start, combat resolution and takeover are still untested.

The earlier run at `a9d3a211` reached the same in-memory registry result
but the engine rejected a C integer passed to Lua's GetComponentData.
The corrected adapter checks the conversion back to the original runtime
ID before each reference is used. Neither run saved the in-memory
enrollment. The original 5,048 entries remain the last sealed registry.

## Disposable stock start trials

Run01 at `3b5f00d4` loaded the confirmed eight-marine ARU-541 copy.
Cold inspection verified the sealed 5,048-entry registry and the existing
ZNG-430/index 1666 target. The ordinary in-memory Enroll and Resolve
returned ARU-541/index 5049, runtime reference `0x4204c`. The stock
read-only preflight still reported Board selectable, ZNG capturable,
eight available marines and an allocation limit of eight. The stock
boarding witness armed on the sealed ZNG-430 reference `0x41f83`.

The private driver passed the nested boarding payload directly to
`OpenMenu`. The engine reported a ScriptValue conversion error at
parameter 7 and the map received an empty target. The private driver
repaused and exited. The recorded sequence ends before a stock click,
operation start or marine assignment. The output checkpoint directory
stayed empty. The confirmed original and staged source retained save
SHA-256 `b80e43e31242758bdd1b422d157c87b46a3c327399fcc58d810957763feb1bd4`.

The corrected private driver opens the ordinary MapMenu and uses its
local `onInteractMenuCallback("boardingcontext", ...)` path. A LuaJIT
test executed the original 08 catalog callback with renderer and
native getter doubles. Both precise IDs reached context creation.
A fresh wrapper for the same attacker retained the proper stock map
key. The eight marine assignments triggered one mounted callback in
the double. The wrong-actor case triggered zero callbacks.

Run02 at `bdec1390` again loaded the sealed target, resolved ARU-541 at
temporary index 5049 and armed the observer. Its fresh runtime ship
references were `0x4204a` and `0x41f81`. The plain MapMenu became visible
with a native holomap. The private assertion before the boarding callback
failed. That assertion checked for both an empty context menu and a Lua
callback function, so the trace leaves the failing subcondition open.
The first run's ScriptValue error did not recur. Both attempts recorded
zero Start clicks and zero native operation starts. No allocated marine
readback, launch of boarding pods or takeover was observed.

Both provider processes exited with no owned game process remaining.
Both checkpoint output directories stayed empty, and the original and
each staged source retained the exact confirmed hash above. Private
evidence is in `.local/native-boarding-crew-seed/stock-start-01/` and
`stock-start-02/`. The runtime index 5049 did not enter a saved registry.

## Native start through stock exports

A separate one-provider run at `bdec1390` invoked the stock X4 exports
on a fresh disposable copy of the same checkpoint. The private driver
read ARU-541's eight available marines with `GetRoleTiers2` and selected
the same default behaviour and approach/insertion thresholds as the
stock map (30 and 80). It called `CreateBoardingOperation`,
`AddAttackerToBoardingOperation` and `StartBoardingOperation` once each.
All three returned true. The driver used the software-rendered provider
without opening its map or attaching any player connection.

The sealed-target witness recorded one native operation `0x42d9` at
game time 568.53 s, targeting ZNG-430 `0x41f82` at saved index 1666.
A separate MD listener confirmed ARU-541 `0x4204b` as the sole
attacker with eight assigned marines and resolved its temporary
index 5049 in the live registry. The stock engine getters independently
returned that attacker, eight assigned marines and the matching
per-tier allocation. The observed stock default was `targetengines`.
The provider paused again at game time 568.97 s.

The provider exited normally with an empty checkpoint output directory
and no remaining X4 process. An independent SHA-256 check confirmed the
original and staged source bytes above. Only the loaded world held
ARU-541's temporary registry association. The trial ended before pods,
combat or ownership transfer. Connecting a real client menu action to
this provider remains separate work. Raw logs and
`independent-start.json` are in
`.local/native-boarding-crew-seed/stock-exports-01/`.

## Native follow-up at the highest stock thresholds

A single disposable Linux provider at `6d4062fd` repeated the stock export
sequence with the map menu's `veryhigh` option for both approach and
insertion. Native getters returned 100/100. The provider started operation
`0x42dc` once, with eight marines on ARU-541 (`0x4204c`, temporary index
5049) and ZNG-430 (`0x41f83`, sealed index 1666) as the target. The MD
start witness independently confirmed both actors and the allocation.
At start ZNG-430 had 100% hull, 100% shields and 11 of 11 operational
turrets by the native component query. The simulation stayed at 1×.
The driver made no changes to hull, shields or equipment and issued no
further boarding or combat command.

| Game time | Native observation |
| --- | --- |
| 568.779–569.219 s | Setup advanced through pre-approach to approach. |
| 590.413–591.209 s | Stock requested launch for eight marines. MD then observed two operational boarding pods in the approach list, `0xb766e` with five marines and `0xb767c` with three. |
| 612.285 s | The operation entered infiltration. X4 emitted `boarding_failed` for ZNG-430 and removed the operation at the same recorded time. The target's true owner was still Scale Plate at removal. |
| 614.353 s | The provider paused again, 45.134 game seconds into the 360-second observation window. |

The witness recorded no pod attachment, `boarded` signal or ownership
transfer. Stock `md/boarding.xml` lines 560–571 allow the transition to
infiltration once marines have been dispatched and no pods remain in
approach. The transition alone does not establish that a pod reached the
hull. The trace did not identify the failure branch or the fate of either
pod.

The provider exited successfully with no owned X4 processes remaining
and no output checkpoint. An independent read verified identical
confirmed pointers and SHA-256
`b80e43e31242758bdd1b422d157c87b46a3c327399fcc58d810957763feb1bd4`
for the original and staged source saves. The load marker and initial
registry inspection confirmed the 5,048-entry source association, and
index 5049 stayed in memory. Raw logs, the provider result and
`independent-follow.json` are in
`.local/native-boarding-crew-seed/stock-follow-01/`.

## Native map trial with the first-use queue prompt

Run03 at `f444e5f0` used one disposable software-rendered Linux provider
with the same confirmed source. The live registry resolved ARU-541 as
`0x4204b` at temporary index 5049, and the witness armed on ZNG-430
`0x41f82` at sealed index 1666. The first rendered map reported
`orderqueuesetting` and a callable stock boarding callback. The private
driver invoked the mounted Keep action once (frame 74, cell 82).
The dialog closed and the stock setting read back as `keep`.

The driver then passed raw FFI actor values into the stock map callback.
The engine rejected the target in `GetComponentData` and the attacker
in `GetSubordinates`, both with `got cdata, expected component ID`.
The rejected hull read at `menu_map.lua:25268` left the hull percentage
missing, and the stock risk calculation failed at line 25354. The stock
InteractMenu converts the target with `ConvertStringTo64Bit` before
returning to the map. Its attacker list comes from the map's stock
selection code, which uses that converter too. No Start widget mounted.
The trace records zero Start clicks and zero native operation starts.

Five offline LuaJIT cases exercised the stock callback, Keep widget and
Start handler with renderer and engine doubles. The native run exposed
the component type mismatch those doubles missed. The provider repaused
and exited with a failed start receipt and no surviving X4 process. The
output checkpoint store stayed empty. Independent SHA-256 checks matched
the confirmed original and staged source bytes above. The raw engine
log and `independent-ui.json` are in
`.local/native-boarding-crew-seed/stock-start-03/`.

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
