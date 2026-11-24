# Capture one Company purchase from the stock MapMenu

The private client adapter in `src/authority/lua/company_trade_capture.lua`
wraps the handler on the existing `MapMenu`. It is absent from the default
overlays. Install it on that menu after verifying the local roster binding for
the diagnostic ship and station.

Call `X4LAN_COMPANY_TRADE_CAPTURE.install_x4` with `enabled = true`, the
`menu`, local `ship` and `station` component values, the expected station
sector macro as `sector`, its `station_code`, a `request(intent, settle)`
callback, and a `notice(status)` callback. The intent contains the sector
macro, station code, ware ID, quantity, and quote in integer cents. Component
IDs stay local. The request callback must return `true` to acknowledge
dispatch or `false` if it can prove nothing was dispatched. Any other
dispatch result is uncertain. This adapter sends no packets.

Call `settle` once with the original intent fields and an `outcome` of
`confirmed` or `refused` after checking the authoritative result. A missing,
mismatched or unclear result is uncertain. Call `settle(nil)` if the request
owner reaches its deadline. A pending receipt is never displayed as payment.
The integration should render the notice status to the player. The adapter
leaves the menu open and never invokes stock booking for the covered selection.
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

No stock callback implementation was available for inspection in this
checkout. In particular, the instant-versus-queued decision inside the stock
handler has not been observed in this adapter. Its scope requires the chosen
ship to be at the chosen station, controlled, physically docked, and outside
internal storage, and redirects that confirmation to the authoritative
immediate-purchase path. The exact Lua record and export behavior together
still need a native test. The focused LuaJIT test exercises the adapter
against synthetic X4 UI and dock data.
