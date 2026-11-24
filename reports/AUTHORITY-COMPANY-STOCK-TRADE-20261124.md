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
