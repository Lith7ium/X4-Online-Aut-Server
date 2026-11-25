# A Company purchase with the server avatar on another station

The isolated native Linux provider loaded confirmed checkpoint
`checkpoint-12aba53907ec43a6becfc7dde919d309`, SHA-256
`1c7117f327df18df402295755968a19591cc238b9f38519b43348bfce3ee2ff1`.
The intended purchase is one unit of water for WRN-302 at an ordinary Teladi
station. The server avatar stays on foot at XPJ-169 in Black Hole Sun V.
Each attempt starts from the original private checkpoint. These trials run
without a public listener or a connected pilot.

## What the game reported at load

| Measurement | Value |
| --- | --- |
| Issued ship registry entries | 10,082 |
| Company faction account and player account | 1,000,000 cents each (10,000 Cr) |
| WRN-302 (index 7354), Grand Exchange I | In space, 0 units of water aboard, space for 25 |
| WOO-831 (index 1970), in another sector | In space, 0 units of water aboard, space for 90 |
| WRN to BIM-169 | 374,528 m |
| BIM-169 water stock / units for sale to WRN | 44,668 / 31,475 |
| Initial quoted price | 5,877 cents per unit (58.77 Cr) |

The saved faction account had raw amount `10000`. X4 reported that
account and the player's wallet separately. The provider asks for a fresh
quote after docking because the station continues to trade while WRN flies.

## Docking diagnosis

The first attempt stopped during fixture identity checks, before assigning
a captain. The second assigned a Company captain to the empty ship. An
ordinary quicksave in the private display captured the world at game age
761.89 s. The docking preparation had completed at 302.52 s, but the next
Mission Director cue was still waiting for its parent's completion event.
The saved ship still had only its default Wait order. No purchase had
been submitted. The quicksave notified the provider's normal save guard,
which ended the trial. No test processes remained. This diagnostic file
was excluded from checkpoint recovery.

Diagnostic quicksave SHA-256
`ea5f8da291c8ddc2cadf1dd37a05964dcd388b47ae8e3c2c8987dcb4cf425bc1`.
The corrected fixture checks the saved preparation status when its child
cue becomes active and issues the order once. In the next native run the
captain was assigned at game age 302.514 s. At 303.981 s X4 returned a
`DockAt` order for WRN with one entry in the ship's order queue.
[Test fixture and stock trade event](../tools/dev/AUTHORITY-COMPANY-STOCK-TRADE.md)

## First completed purchase

At game ages 446.486 s and 447.644 s the provider found WRN at the
same external trading bay, with its captain and an empty order queue.
The server avatar was still at XPJ-169. The live unit quote had changed
to 5,819 cents. The provider submitted one stock order. X4 raised its
trade-completed event at 455.66 s. WRN gained one unit of water and the
player balance decreased by 5,800 cents. Repeating the same confirmation
returned `already_attempted`.

The aggregate water stock at BIM-169 dropped by 646 units over the
observation interval. Other economic activity continued during the
purchase. The first probe also narrowed the per-container monetary deltas
to signed 32-bit integers. Those measurements cannot establish the seller's
proceeds or the settled unit price. The strict check rejected the run and
published no checkpoint. Both the stock deal and a separately identifiable
account entry are needed to reconcile the offered and charged amounts.

## Independent six-unit purchase

A fresh copy of the same marked world at `70bba0ca` received a different
request to buy six units. X4 reported the same unit price, a completed deal
with the correct buyer and seller, six transferred units and zero destroyed.
All six arrived on WRN. Station stock decreased by six during this interval.
The server again refused a duplicate confirmation.

| Native evidence | Result |
| --- | ---: |
| Stock offer and completed deal, unit price | 58.19 Cr |
| Deal's nominal total | 349.14 Cr |
| Completed purchase entry in WRN's transaction log | −349.00 Cr |
| Company faction balance before / after | 10,000.00 / 9,651.00 Cr |
| Final player balance | 9,651.00 Cr |

The installed stock 9.00 map trade menu also multiplies the unit price
by the selected quantity before calling `RoundTotalTradePrice` for each
ware line (`08.cat`, `ui/addons/ego_detailmonitor/menu_map.lua`, lines
21715 and 21718).

Rounding six units separately to 58 Cr would have yielded 348 Cr.
This purchase settled at 349 Cr. These amounts establish the result for
this purchase. They do not distinguish rounding the total to the nearest
whole credit from truncating its fraction. A client can show the charge
from the native transaction log without implementing its own rounding rule.

The first version of the completion cue reused a variable name in an
inherited MD namespace. Its field for the player's starting balance
therefore contained the debit instead. The independently read absolute
Company balances and the matched journal entry agree. The strict check
refused to publish the trial as a confirmed host checkpoint. Commit
`37f0f480` gives the starting balances, final balances and deltas
distinct names. The original log remains available for inspection.

The controlled native save retained the six-unit deal and the player
faction's account amount of 9,651 Cr. Its SHA-256 is
`91cc4875213273715f2dc53c63b36a115c54f73ac788a88b5de7c356feefd879`
and its load marker is `667dc29dbeab841d88bc769c00b31783`. This is a
private diagnostic save.

## Native cold restart after the purchase

The saved-state comparison found a debit of 34,900 cents and six units of
water in the buyer's ship storage. A fresh GPU-free X4 process then loaded
the retained diagnostic world in a private profile. Its load event returned
the expected marker and the process paused. The first native observations
at game age 455.683 s were:

| Observation | X4 cold readback |
| --- | --- |
| Company account and player balance | 9,651 Cr each |
| WRN-302, Grand Exchange I | Docked externally at BIM-169, six water, room for another 19 |
| WOO-831 | Undocked in its original sector, zero water, room for 90 |
| Server avatar | On foot at XPJ-169, Black Hole Sun V |
| Ship registry | 10,082 entries |

The audit made no purchase, admitted no client and left no test processes.
The original failed supervisor result and unpublished save remain available
for diagnosis. The opt-in client adapter can display the actual booked
amount from a receipt; this display was tested with LuaJIT. A purchase
submitted from a connected cockpit still needs an end-to-end test.

## Native offer read from the purchasing ship's perspective

A private X4 process loaded the same six-unit save with the server avatar
still on XPJ-169. The stock Lua query used by the trade menu returned 32,241
water at 58.20 Cr each for WRN-302 at BIM-169, with a minimum order of one.
The native eligibility check accepted a quantity of six. The independent
Mission Director station offer matched the unit price and quantity. This
was a fresh quote after reloading the world; the completed purchase in the
save was booked earlier at an internal price of 58.19 Cr per unit.

The ship reported both `isdocked=true` and `isdocking=true` while the native
Mission Director found an external trading bay in its default state. The
first client-side check rejected this combination before asking for the
seat. The capture adapter now requires a positive external docking state,
station dock-list membership and the stock immediate-trade context. It
also checks the controlled ship and the selected station before accepting
a purchase. A LuaJIT regression covers the two simultaneous flags, an
approach without completed docking, and a ship absent from the dock list.

With the corrected dock check, the native stock dock list contained two
ships and included WRN at the same station reported by the Mission Director.
The normal teleport query returned `research`; the private test allowed
the same research exemption used in Company-seat diagnostics, and X4
returned `granted`. X4 dispatched the cockpit transfer. Over the following
eight real seconds the controlled, occupied and container IDs did not all
match WRN. The target had an assigned NPC captain before dispatch. This
run did not establish why control was missing. No purchase or player
confirmation was attempted, and the test left no processes running.

## Flight from the original confirmed world

This single-process Linux diagnostic used the confirmed checkpoint at the
top of this report. WRN-302 had no assigned pilot, no water, a Mk2 docking
computer and a Mk3 travel engine. The private client completed the full
10,082-entry inspection while paused and obtained the ship using the same
seat code as a joining player. The controlled, occupied and container IDs
all matched WRN. Original game age was 302.118 s and the distance to
BIM-169 was 374,528 m.

The stock autopilot flew WRN toward BIM. At game age 521.79 s the stock
range check succeeded, the pilot stopped autopilot, targeted the station
and the docking computer accepted QuickDock. Across 66 periodic samples
the closest recorded distance to the station centre was 2,208 m. The ship
then held at 2,488 m. A HUD capture showed 0 m/s. One additional Shift+D
input was sent to the isolated X4 window during this wait. At the
120-second docking deadline, WRN was still in space. Its physical bay was
absent, all three control IDs still matched WRN, and the account and cargo
were unchanged at 10,000 Cr and zero water. No purchase intent was sent.
Distance to the station centre does not measure alignment with a landing pad.

The original launch using an additional network namespace ended before
the world loaded. One remaining X4 descendant was verified against the
private executable, command line, output and session and stopped. The
completed flight used the working isolated Steam runtime, opened no
session listener, wrote no new save and left no test processes. Its
diagnostic result SHA-256 is
`0537c4c307b43e6870dd7d226836e4a26a9f8d4058bba8f058929ccc834a581d`.

## Purchase selected in the docked pilot's stock trade menu

In the first private berth attempt, X4 rejected a request for a specific
trading bay from 374,528 m away. It returned neither queued nor granted.
The run ended before any docking or purchase. A second private run loaded
the original 10,082-ship checkpoint, took the same empty WRN-302 and
placed it near an external trading platform at BIM-169. This avoided
repeating the long flight. The starting platform and the bay actually
allocated by the station were different.

| Native observation | Value |
| --- | --- |
| Measured distance to staging platform / station centre | 1,334 m / 3,222 m |
| Stock cockpit request | Granted |
| Physical dock | WRN in the stock station dock list at its assigned external bay; pilot retained control |
| Stock slider limit / selected quantity | 25 / 6 water |
| Available virtual cargo space before / after selection | 25 / 19 |
| Quoted unit price / basket total shown by the stock menu | 58.77 Cr / 352 Cr |
| Account and physical water before / after the confirmation callback | 10,000 Cr and 0 water both times |

The private fixture invoked X4's instant docking action after measuring the
actual station assignment. The unmodified stock trade screen created the
basket. The harness invoked the registered click callback of its active
Confirm button after rebinding the rendered widget. The capture adapter
received the station, sector, ware, quantity and quoted unit price. Its
test destination declined the order without dispatching it. The pilot's
end-to-end purchase, native input from a human and a second player's account
display remain open.

X4 reported an invalid optional lookup in the private placement guard
during load. The observed placement, native permission and dock observations
came from that run. Commit `f000e379` corrects the expression. The next
native trade run should check the opening log for this diagnostic.

The near-station result SHA-256 is
`7397fc5fca9be306bd66745668babe7f094c1f91c318492e84da432e06a09c9d`.
The run wrote no save and left no test processes. Both supported server
purchase quantities now check the transaction journal and the buying ship's
actual before/after ware counts. The revised provider verifier has focused
regression coverage for a 58.19 Cr quote and a 58 Cr debit.

## Authoritative check of the recorded pilot order

The GPU-free provider imported the nearby pilot's recorded request by its
capture ID and SHA-256. It rechecked the evidence, restored the same original
world and staged the remote Company ship at a real BIM-169 trading berth.
The server avatar stayed at XPJ-169. The recorded client's ship component
ID differed between these two processes; the request identified its target
by the Company ship, sector and station instead.

The server quote at game age 307.43 s matched the pilot's 58.77 Cr. By the
separate booking step at 307.96 s the price was 58.48 Cr. The native price
check refused the order. The readings on each side of the refusal both showed
10,000 Cr in the Company account and zero water aboard WRN. There was no
new order, save, client connection or surviving test process. The price
approval rule requires a fresh pilot confirmation for a lower or higher
price. This run used a local recording as the input to the real server.
Result SHA-256: `b1897e759fa9b565fad7c7346910f87482cef03486af1032c925812c0cf0e4dc`.

## Exact-price booking from a recorded stock confirmation

The next private provider processed the same recorded 58.77 Cr confirmation
in one Mission Director action. X4 reported the current unit price and
submitted the native order at game age 307.27 s. The deal transferred six
water, with zero destroyed. The ship journal recorded a debit of 352 Cr
(the unrounded total was 352.62 Cr). At completion both Company balances
were 9,648 Cr and the ship had six water. A duplicate received
`already_attempted`.

The supervisor initially rejected the journal because the Mission Director
printed the docking time as 306.823 s and the Lua callback measured
306.82299286 s. Revalidation now compares the docking lower bound at the
precision of the reported MD time. The full-precision ledger interval and
entry times, quantity, actual debit and absolute balances still undergo
the same checks. The original result file, SHA-256
`55a00099ce7f602fe39e7a105f4781ac2e1dc1320ce823c040caa9a2d1f25f98`,
remains a failed run with no save. Rechecking its native rows yields the
confirmed 352 Cr receipt. Later trials preserve a private native save once
a submission has occurred even if reconciliation fails.

## Direct berth placement on 25 November

An isolated Linux provider reloaded the original confirmed world and placed
the existing WRN-302 at an external S berth on BIM-169 using the stock MD
action `spawn_docked`. WRN began in space with no captain. The test selected
bay seed `507326418098681806` from the source save and passed it as text to
preserve its exact value. The second native lookup matched the station,
sector and available bay immediately before placement.

The action ran at game age 302.948 s. At 305.613 s a subsequent game update
reported the same registered ship occupying that bay. The Company account
and water aboard WRN were unchanged. The server avatar stayed at XPJ-169.
The experiment issued no docking order and added no crew.

The confirmed save placed WRN under that bay's `dock` connection and
preserved its cargo and crew. A fresh native X4 process loaded the checkpoint
and found WRN-302 externally docked at BIM-169 with an empty water hold.
The save provided the exact bay seed. The cold in-game observer independently
reported the station and dock state.

| Isolated checkpoint | SHA-256 of the saved world |
| --- | --- |
| `checkpoint-25fd1e920d914e87b9ec18147c8f1cf9` | `9a3c6b8d5953c768e7a634f09626b417a271e87e65e3b64462cb5576ecc6260e` |

In a separate native client run, X4 loaded the same source bytes, let the
pilot take control of WRN-302 and assigned the ship a berth through its
stock docking path. The client read the persistent seed from the actual
occupied bay. The sector, station code, dock-area macro, bay macro and all
18 digits of the seed matched the server's saved berth. The controlled
ship remained the occupant through the stock trade confirmation callback.
The client captured an intent, spent zero credits and exited cleanly.
Its private result SHA-256 was
`8f240ee0c8d45ef132a6ea21b150d82e5102910e0dc68ea88e6121868981b252`.

The provider used a descriptor derived from the source save and the client
measured its own berth independently. Delivery of that descriptor over the
pilot's live multiplayer connection still requires a separate test.

### Occupied berth at the same station

In a fresh native run, a fixture ship occupied the requested berth on
BIM-169. X4 rejected an unknown berth identifier without moving either ship.
For the valid request the provider selected another free, compatible
external trading berth on BIM-169. X4 then reported the original ship on
the requested bay and WRN-302 on the other bay. Repeating the request
returned `already_docked` and preserved both assignments. Account, cargo and
server avatar measurements stayed unchanged. The development cue reports
the requested and actual bay to its caller. [Native berth adoption cue](../tools/dev/authority_company_berth_adoption.xml)

X4 formatted the fallback seed as signed decimal `-8269418508059026910`.
Its unsigned 64-bit representation is `10177325565650524706`. The native
descriptor retains the exact text. The first launch exposed a missing
polling interval in a Mission Director cue and reached no placement. The
corrected run completed successfully. Its fixture world was discarded.

### Purchase from an unoccupied server ship

A test participant reserved WRN-302 through its pinned TLS control connection
and requested two units of water from BIM-169. The provider loaded the
confirmed docked world. It refused the first price and returned 58.48 Cr
per unit. A separate synthetic confirmation then accepted that exact price.
X4 reported `No trade entity on object Elite Vanguard (WRN-302)`. The ship
had no captain. Its order queue was empty and X4 returned no trade deal.

The participant received `accepted_pending_statement`. The provider saved
the world and kept the purchase unresolved. The original and final saves
both contained 10,000 Cr in the Company account and zero water on WRN.
The ordinary provider requires an assigned Company NPC pilot before placing
a trade order. The player-side test above establishes which berth a human
pilot occupies and what they selected in the stock menu.

### Purchase with an assigned Company captain

A separate native Linux provider loaded a copy of the same docked world. A
test fixture assigned Company captain PYN-740 to WRN-302 at its existing berth
on BIM-169. A simulated pilot used the authenticated control connection to
request two units of water. The server rejected the first price. The pilot
then confirmed a new request at X4's quoted unit price of 58.48 Cr.

The stock trade journal recorded a debit of 116 Cr. Both the authenticated
receipt and the confirmed world saved after disconnect contained 9,884 Cr
and two units of water on WRN. The source world contained 10,000 Cr and
zero water. The receipt reports the observed debit separately from the
116.96 Cr obtained by multiplying the quote by the quantity. This run
establishes the observed settlement for this purchase.

Checkpoint `checkpoint-1dac0e4056eb41739aeecd30dbb1f1ca` has SHA-256
`cec4cedd6c3aa39ae189163f9baae2fe3d03e7fc7fa09923eb33ce34d32914d5`.
It retains PYN-740 as the assigned AI pilot on WRN, at the same trading
berth. A purchase initiated in a real player's cockpit and settled in a
second running X4 instance remains to be verified.

### Missing receipt on a saved world with a previous completed purchase

The next native run reloaded the saved two-water world above. The original
captain stayed aboard. The previous stock order left the queue between
game ages 322.044 and 323.605 s. A simulated pilot then submitted a new
purchase for one unit of water. X4 accepted the order. The experimental
receipt monitor failed to activate on the reloaded world, and the client
received `accepted_pending_statement`. The provider saved on disconnect.

The source and confirmed final save show exactly one additional unit of
water on WRN (two to three) and a 58 Cr debit (9,884 to 9,826 Cr).
The ship remained docked at BIM-169. The live transaction still has an
unresolved receipt. No one resubmitted it or placed the planned second
order. Final checkpoint `checkpoint-2738d4d619db40fa8486479494793925`
has SHA-256 `f5f0adf0279a6b54ea8503aedd4d738dd88ada75f0fce4d89411b7dfee333b26`.

The corrected bridge requires the monitor to acknowledge the current
request before X4 can receive a trade order. A native run with two
confirmed orders in one session remains open.
