# Stock "Fly and Wait" from the map to another Company ship: source trace

Quartz, 25 November 2026. Base `121419da`, branch `work/quartz-company-orders`.

**Evidence type: static inspection of the stock X4 9.00 Windows script files only.**
No X4 process was started for this report, so no runtime behaviour has been
observed. Statements below are what the stock UI Lua and AI scripts *request*,
not measured game results. The open points at the end are the ones a single
disposable native run would have to answer.

## Reproduction

```
.venv\Scripts\python tools\dev\authority_stock_source_extract.py
```

This copies the stock order scripts, map and interaction-menu Lua, MD,
libraries and English text out of the base and stock DLC catalogs into
`.local/stock-source/` (read-only on the installation, never committed). Paths
below are relative to `.local/stock-source/base/`. Line numbers refer to the
X4 9.00 files (Steam build 23660954) on this PC.

## 1. Which command is "Fly and Wait"

`t/0001-l044.xml`, page 1041: `251 = "Fly and Wait"`, `252 = "Fly to a position
and wait."`. These are the name and description of the order definition
`MoveWait` in `aiscripts/order.move.wait.xml:14`
(`<order id="MoveWait" name="{1041, 251}" ... category="navigation">`).

## 2. The two map paths that issue it

Right-clicking in the map (`ui/addons/ego_detailmonitor/menu_map.lua`, around
`28023-28076`) either opens the interaction menu or issues a default order:

| Path | Trigger | Code | Ships ordered | Order created |
|---|---|---|---|---|
| A. Interaction menu | Plain right-click (`modified == nil`) on empty sector space, then the menu entry "Fly and Wait" | `Helper.openInteractMenu(..., offsetcomponent = posrotcomponent, offset = posrot, playerships = ...)` → `menu_interactmenu.lua:5603` → `buttonMoveWait` `:2570` → `orderMoveWait` `:831` | each entry of `menu.selectedplayerships` | `MoveWait` if the clicked component is a sector, **otherwise `MoveToObject`** (`:840-849`) |
| B. Default interaction | Right-click with the queue or priority modifier held on sector space | `menu.defaultInteraction` `menu_map.lua:21082` → `:21113-21121` → `menu.orderMoveWait` `:3449` | each selected component that is not `C.GetPlayerOccupiedShipID()`, has an `assignedpilot` and `isplayerowned` | `MoveWait`; a clicked gate/highway passes `gate` instead of `destination` (`:21184-21187`, `isgate`) |

The legend at `menu_map.lua:1843-1844` names the two modifiers "Queued Order"
and "Priority Order" (text 1001/13317 and 13316). Which key is which follows
`Helper.useShiftToQueueOrders`.

### Selected ship

Neither path addresses the player's own ship by default. The order goes to
the map **selection**, not the player's ship:

- Path A requires `C.IsOrderSelectableFor("MoveWait", ship)` and
  `GetComponentData(ship, "assignedpilot")` (`menu_interactmenu.lua:832`).
  An NPC-captained Company ship therefore qualifies, and a ship without a pilot
  is skipped without error.
- Path B also excludes the occupied ship explicitly (`menu_map.lua:21117`).

So a stock "Fly and Wait" to a *different*, NPC-captained Company ship is the
ordinary case for both paths. The receiver is identified by its
`UniverseID` from the selection (`ConvertStringTo64Bit(id)`).

### Target sector and coordinates

Both paths build the same parameter:

```lua
-- menu_interactmenu.lua:846-849 (path A), menu_map.lua:3462 (path B)
destination = { ConvertStringToLuaID(tostring(sector)), { offset.x, offset.y, offset.z } }
```

- `sector` is the picked sector (`posrotcomponent`) and `offset` its
  sector-relative position from the map pick (`posrot`).
- `playerprecise = true` is added when exactly one ship is selected
  (path A `:2573`, path B `issingleship`). Per the order's own parameter
  comment (`order.move.wait.xml:26`) this "skips position randomization but does
  safepos". **The final position may therefore differ from the clicked point**;
  with several ships selected the position is also randomized.
- The order script converts a non-sector space into its sector
  (`order.move.wait.xml:184-194`) and, for a job ship with a null space, uses
  `jobmainsector` (`:163-172`). Neither applies to the stock map path, which
  always passes a sector.
- `timeout` is not set by either UI path, so it keeps its default `0s`, which
  the definition declares as `infinitevalue="0s"`: **an infinite wait**
  (`order.move.wait.xml:19`).

### Queue placement

- Path A: `CreateOrder(component, "MoveWait", params, false, false, menu.priorityOrderMode, nil, nil, nil, true)`;
  "clear other orders" calls `C.RemoveAllOrders2` first (`:836-838`).
- Path B: `CreateOrder(component, "MoveWait", params, false, false, <priority modifier or "both">)`,
  with `C.RemoveAllOrders2` first when `clear` is set (`menu_map.lua:3454-3467`).

`CreateOrder` with a parameter table is a global provided by the engine; its
definition is not in the stock addon Lua, so the meaning of its positional
arguments is inferred from the call sites only. The FFI forms declared in
`menu_map.lua:537-538` are `CreateOrder(controllable, orderid, default)` and
`CreateOrder3(controllable, orderid, default, isoverride, istemp)`, both
returning a queue index.

## 3. How X4 reports this order

### Lua (UI) side

`menu_map.lua` declares `GetNumOrders(UniverseID)` (`:715`) and
`GetOrders(Order*, len, UniverseID)` (`:747`). Each `Order` record contains
`queueidx`, `state`, `statename`, `orderdef`, `actualparams`, `enabled`,
`isinfinite`, `issyncpointreached` and `istemporder` (`Order2` adds
`isoverride`, `Order3` `ispriority`). The state strings the stock UI compares
are `"setup"`, `"disabled"`, `"started"` and `"critical"`. A read of the
receiving ship's queue therefore shows whether `MoveWait` is queued, which
index it has and whether it is current.

### MD side

`libraries/common.xsd:14498-14586` defines object order events with
`object` = the ship and `param` = the order:

- `event_object_order_ready`: an order is enabled or should start.
- `event_object_order_finished`: the running order finished.
- `event_object_order_cancelled`: a running order was cancelled
  (`param2` = cancelled immediately).
- `event_object_order_param_edited`: a parameter of a running critical order changed.
- `event_object_order_syncpoint_released`: an infinite order was told to finish.

### What "completed" means for this order

`order.move.wait.xml` runs in two phases:

1. **Travel**: `set_command command.movetozone` with the target sector
   (`:242`), then `move.generic` with destination and position (`:249-260`).
   If movement fails, `set_order_failed` with text 1045/101 "Unable to reach
   destination." is set on the running order (`:261-267`).
2. **Arrival and wait**: `set_command command.wait`,
   `commandaction.standingby` (`:272-274`), `stop_moving`, and the
   "Awaiting orders." notification for player-owned ships without a next order
   (`:288-298`). It then calls `order.wait` with the zero timeout (`:301-306`).
   `order.wait.xml:153-156` immediately sets
   `set_order_syncpoint_reached` on the current order ("Required for all
   infinite orders"), and with no timeout waits indefinitely (`:247-264`).

So with the stock map parameters, **a successful "Fly and Wait" does not
finish by itself**. The source-defined arrival signal is the transition to
`command.wait` / `standingby` together with `issyncpointreached = true` on
the still-current `MoveWait` order. The order finishes, and
`event_object_order_finished` can be expected, only after a later order
releases the sync point or the order is cancelled. A queue entry or an
`order_ready` receipt is not evidence of arrival.

## 4. Reusable diagnostics in this repository

- `tools/dev/authority_npc_flight_fixture.xml` and
  `authority_npc_flight_source.lua` already create a `MoveWait` on one
  guarded fixture and fail on a missing `order_ready`. They create the order
  from MD and do not go through the map UI.
- `tools/dev/native_gate_player_orders_core.lua` (lockstep, default off)
  describes `MoveWait` parameters for replay; it is not an authority path.

## 5. Open points for one native run

The source does not establish:

1. The actual `state`/`statename` sequence of the new `MoveWait` entry in
   `GetOrders` (for example, whether it passes `setup` or `ready` before
   `started`/`critical`) and in which frame it appears after the click.
2. That `event_object_order_ready` fires once for the map order, and that
   `issyncpointreached` flips exactly at arrival.
3. How far the safepos-adjusted final position lies from the clicked
   `offset`, for a single selected ship.
4. Queue placement with and without the priority modifier and "clear other
   orders", and the global `CreateOrder` argument meaning.
5. Behaviour for a docked receiving ship.

The run would use profile `45851` and the shared native lockfile. A read-only
MD/Lua observer would record the selected ship's `UniverseID`, owner, pilot and
sector, its `GetOrders` rows each frame, the order events above, its command and
command action, and its position, while the order is issued once through the
stock map. It would not write orders or game state beyond that one click.
Multiplayer dispatch of fleet orders is out of scope.
