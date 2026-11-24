# Capture one Company purchase from the stock MapMenu

The private client adapter in `src/authority/lua/company_trade_capture.lua`
wraps the handler on the existing `MapMenu`. It is absent from the default
overlays. Install it on that menu after verifying the local roster binding for
the diagnostic ship and station.

Call `X4LAN_COMPANY_TRADE_CAPTURE.install_x4` with `enabled = true`, the
`menu`, local `ship` and `station` component values, the expected station
sector macro as `sector`, its `station_code`, a `request(intent, settle)`
callback and optionally `notice(status, settled_total_cents)`. The default
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
