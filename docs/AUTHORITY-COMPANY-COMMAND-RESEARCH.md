# Company commands and trade: existing evidence

All players initially share one Company and account.

## Trade authority agreed on 24 November 2026

When a pilot confirms a purchase, the client sends the request to the
server. The server checks the current offer, performs the transaction in
its X4 world and returns the confirmed result. Two pilots spending the
Company's last credits must each see the outcome of the server's decision.
A lost reply must allow the player to discover whether a purchase went
through without buying the goods a second time.

The first isolated trial uses one unit of water, the Company ship WRN-302
and a normal Teladi station with its own stock and sell offer. The server
avatar stays on a different station. A successful stock order submission,
a completed purchase, the balance and cargo after a reload, and a receipt
at a second player's cockpit are separate observations.
[Native trade measurements](../reports/AUTHORITY-COMPANY-STOCK-TRADE-20261124.md)

## What exists

- The experimental authority participant accepts pause/resume, ship-creation
  and motion messages; it does not expose a Company order, trade, combat or
  boarding command. See `src/authority/experimental_tcp_participant_client.py`
  and `src/authority/experimental_tcp_job_delivery.py`. The latter's *job*
  events describe NPC ship creation, not player-issued orders.
- `tools/dev/authority_npc_flight_fixture.xml` requests a `MoveWait` order on
  one guarded NPC fixture. The isolated Linux/Linux observation in
  `tools/dev/AUTHORITY-NPC-FLIGHT.md` recorded a pilot/order receipt, advancing
  source samples and guest position readbacks. It did not establish order
  completion, a Company-issued order, client admission, persistence or gameplay.
- `tools/dev/authority_player_sector_fixture.xml` prepares two ships owned by
  `faction.player`, but creates no pilot. Its companion
  `tools/dev/AUTHORITY-PLAYER-SECTOR-FIXTURE.md` describes offline preparation,
  not a demonstrated native fixture or usable player seat. The save path is
  deliberately disabled pending live fixture readback.
- The older, default-off **lockstep** code in
  `tools/dev/native_gate_player_orders_core.lua` and
  `tools/dev/native_gate_player_orders.xml` describes `MoveWait`, `Attack`,
  `SingleBuy`, `SingleSell` and other orders. Its report,
  `reports/LOCKSTEP-PLAYER-ORDERS-20261029.txt`, describes offline tests only;
  this is not an authority-server command implementation. Its faction guard
  rejects attacking an own-faction target, so it cannot be copied unchanged for
  the requested player-versus-player behavior.
- `src/native/engine/lockstep_ui_trade.h` and
  `reports/LOCKSTEP-UI-TRADE-20261101.txt` describe earlier binary/script
  research and synthetic lockstep tests for trade and faction accounts. The
  report's later section 14 records native lockstep run T3d: slot 1 in co-op
  docked and made two immediate purchases, each of two plasma conductors. Both
  clients recorded the same player-faction balance changes, from 10,000 to
  8,195 and then 6,309 credits at matching ticks. The report does not claim a
  docked trade by slot 2 or in factions mode. This is evidence for that specific
  lockstep run, not for Authority-server replication, Windows, direct Internet
  play or a validated shared account in the new architecture.

## Small isolated observation to prepare

In one disposable Linux X4 world, first identify one non-player-controlled,
operational Company ship with a verified assigned pilot and sector. Record its
local owner, pilot, current order, sector and position; distinguish missing or
ambiguous observations from a valid empty value. On an explicitly selected safe
fixture, request a single `MoveWait` order and observe the order receipt, later
order state and positions while game time advances. Reject changed owner,
pilot, sector or uncertain process state; retain the original save and do not
retry an uncertain mutation. An order-creation receipt alone is not evidence
that the ship reached its destination. This is a proposed native experiment,
not a completed test or a multiplayer command path.

Separately, a later trade experiment must observe the Company balance and
relevant cargo before and after a real trade and verify the saved result; a
queued `SingleBuy`/`SingleSell` order is not a completed transaction. It must
not assume that observations in independent X4 instances synchronize accounts.

## Combat and boarding boundary

`docs/AUTHORITY-INVESTIGATION.md` records a static X4 9.00 script inspection:
the normal interaction menu and boarding AI include own-faction restrictions.
This does not establish the result of direct weapon hits or modified UI/AI
paths. No authority implementation or native shared-faction PvP/boarding
result follows from that inspection. Combat, boarding, distinct player
sectors, Windows and direct Internet play remain unproven here.
