# Capture one Company purchase from the stock MapMenu

The private client adapter in `src/authority/lua/company_trade_capture.lua`
wraps the handler on the existing `MapMenu`. It is absent from the default
overlays. Install it on that menu after verifying the local roster binding for
the diagnostic ship and station.

Call `X4LAN_COMPANY_TRADE_CAPTURE.install_x4` with `enabled = true`, the
`menu`, local `ship` and `station` component values, the expected station
sector macro as `sector`, its `station_code`, a `request(intent, settle)`
callback and optionally `notice(status, settled_total_cents, snapshot)`. The default
notice uses the stock info line. The intent contains the sector
macro, station code, ware ID, quantity, and quote in integer cents. Component
IDs stay local. The request callback must return `true` to acknowledge
dispatch or `false` if it can prove nothing was dispatched. Any other
dispatch result is uncertain. This adapter sends no packets.

Call `settle` once with the original intent fields and an `outcome` of
`confirmed` or `refused` after checking the authoritative result. A confirmed
receipt must carry the actual booked debit as a positive, exactly represented
integer `settled_total_cents`. The info line displays this amount. For example,
six units quoted at 5819 cents and booked at 34900 cents display 349 Cr. An actual fractional credit would retain its cents.
`price_cents` remains the original unit quote used to match the receipt.
The request owner may include the authoritative post-purchase state as
`company_balance_cents`, `ship_code`, and `cargo_units`. The balance must
be a nonnegative exact Lua integer in cents. The ship code must match the
locally bound hull's stock ID code. `cargo_units` is the absolute count for
the receipt's matched ware and must be an integer at least as large as the
confirmed purchase quantity. Include all three fields or omit them together.
A complete valid snapshot appears in the stock info line and as the optional
custom notice argument with the matched `ware`. An incomplete or invalid
snapshot leaves the paid-only notice. The snapshot only affects display.

For a matching refused receipt, `reason = "price_changed"` and a valid
`current_price_cents` different from the original quote enable a notice with
both unit prices. The custom notice still receives status `refused`, with
no settled total and an optional third argument carrying the reason,
`price_cents` and `current_price_cents`. The stock notice asks the player to
review the fresh offer. A missing or invalid price uses the generic refusal.

A missing, malformed or mismatched receipt is uncertain. Call `settle(nil)`
if the request owner reaches its deadline. Pending or refused notices have
no settled total. The adapter leaves the menu open and suppresses local stock
booking for the covered selection.
It suppresses further confirmations after dispatch even if the outcome is
uncertain. Close the menu before removing the hook. The returned `detach`
function restores the stock handler when no dispatch or reply is outstanding.

The earlier native Linux LAN experiment verified `MapMenu.buttonConfirmTrade`
for docked purchases and sales. Its wrapper used `contextMenuData.component`,
`currentShip`, `wareexchange`, and `orders`. A negative order amount is a
purchase. Its menu looked up the stock ware and price in credits through
`getTradeOfferByID`. The current experiment requires a single negative
integer order and a unit quote representable as integer cents. It checks the
active export for the controlled player-owned ship, the station's stock list of
docked ships, the live sector and station identities, and the offer's station
from `GetTradeData`. Ship or station choices outside the armed pair retain the
native behavior.

The installed stock 9.00 source in catalog `08.cat`, entry
`ui/addons/ego_detailmonitor/menu_map.lua`, defines the instant decision at
lines 21312–21329 and consumes it at lines 3632–3656. The wrapper requires
both `contextMenuData.immediate == true` and an `immediateObject` matching
the selected ship. This admits only the stock ship's immediate purchase
path and suppresses its local order and trade-computer signal. It still
checks live physical docking, control, and ownership. The exact Lua
record and export behavior together still need a native test. The focused
LuaJIT test exercises the adapter against synthetic X4 UI and dock data.

## Isolated stock UI capture

Use the Trade button in the docked menu with the Company ship occupied and
controlled in an external bay at the chosen ordinary station. The stock button
calls the MapMenu `tradecontext` for the current container. Select the occupied
ship and move a single ordinary Water slider to the desired purchase amount.
The slider edits the ship's virtual cargo plan. Before using the stock Confirm
button, check that the hooked menu points to the pinned station and ship and that
`immediate` is true with the ship as `immediateObject`. For a capture-only
probe, inject a request callback that records the intent and returns `false`.
That callback starts no trade. Do not use a live request handler in this probe.

The installed 9.00 catalog `08.cat` contains the original docked button
(`menu_docked.lua` lines 1106–1109 and 1473–1476), the MapMenu initialization
(`menu_map.lua` lines 6463–6468), and the offer reads and virtual slider
(`menu_map.lua` lines 4594–4620, 21359–21414 and 21763–21779). The stock code
keeps C `UniverseID` values in the context and order keys. It converts them to
Lua IDs with `ConvertStringToLuaID(tostring(id))` when calling the Lua trade API.

To inspect an offer without opening or changing the menu, convert the exact
station and ship IDs to Lua IDs and call `GetTradeList(station, ship)`. The
flat result contains the stock `id`, string `ware`, `isselloffer`,
`isbuyoffer`, `ismissionoffer`, `amount`, `minamount` and `price`. Select
`ware == 'water'` with a regular station sell offer. The menu sets its cached
`active` flag using `CanTradeWith` for that ship and appends the row to
`contextMenuData.selloffers`. Once the menu is open, `getTradeOfferByID` reads
the exact cached row using `IsSameTrade`. `GetTradeData` on the same Lua offer
ID supplies the station identity. The stock code converts that station field
with `ConvertIDTo64Bit` at line 27990.

The quote comes from the ship-context list row in credits before the menu
rounds the extended line total. The earlier 5819-cent fixture value was a
provider MD quote at its own time. A capture from a pilot's current menu must
read the actual row price and may differ from the logged fixture quote.
