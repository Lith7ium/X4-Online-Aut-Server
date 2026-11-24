# Authority Company stock trade probe

Run only on the isolated native server fixture with the recovered registry. Copy
`authority_company_stock_trade.xml` into the disposable overlay's `md/`
directory before launching X4. The cue resolves Company ship WRN-302 at
registry index 7354, requires a captain in a stock trading bay at Teladi station
BIM-169 and checks that the avatar is on foot at XPJ-169. It buys one unit of
water from the station's one ordinary sell offer, at the live quoted price.

Register for `X4AuthorityCompanyStockTrade.Result` and
`X4AuthorityCompanyStockTrade.Observed` before asking for a quote. Pass the
saved epoch from the verified registry checkpoint.

```lua
local request = {
    request_id = "trade-1",
    epoch = savedRegistryEpoch,
}
AddUITriggeredEvent("X4AuthorityCompanyStockTrade", "Quote", request)
-- Wait for trade-1|quote|<cents> and compare it with the price approved for the run.
request.price_cents = agreedCents
AddUITriggeredEvent("X4AuthorityCompanyStockTrade", "Buy", request)
```

`Result` reports request ID, status and price in cents. A matching current
price and sufficient stock, cargo capacity and funds are checked before creating
the one stock deal. `submitted` means X4 created the deal. Any further Buy in
the same validated fixture returns `already_attempted`. Keep the one-shot fence if the result or settlement is uncertain. Never retry by reloading the
fixture after a potentially submitted purchase.

`Observed` reports request ID, quoted cents, ship water delta, station water
delta, ship money delta in cents, station money delta in cents, avatar money
delta in cents, docked and parked. It requires an engine completion event
matching the submitted deal. Compare these snapshots with the independent
observer. A separate Company account is established only if the account can be
identified independently and shows the agreed debit. A shared player wallet can
instead establish that the parked avatar's stock faction paid.

The MD schema defines `create_trade_order` with a unit price and an
`immediate` option that starts or queues the order. The stock
`order.trade.perform` recognizes an existing trading berth, checks available
wares and buyer funds, and hands the deal to the blocking engine
`execute_trade` action with the fixed price. The stock script models a short
transfer interval on S/M ships. Timing, the account selected by the native
engine, the final deltas and the absence of side effects still need a native
fixture result. This probe makes no direct changes to money or cargo.
