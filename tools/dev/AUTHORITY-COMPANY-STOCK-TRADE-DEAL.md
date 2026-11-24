# Stock trade deal evidence for the private Company fixture

Use an independent copy of the verified two-ship world and a fresh request
nonce. For the six-unit diagnostic, add `trial = "six_unit"` to both Quote
and Buy. Omitting the field preserves the one-unit trial. Quote and Buy
must agree on the nonce, registry epoch, mode and quoted unit price. The
MD fixture resolves the same fixed ship, station, ware and parked avatar.

The `X4AuthorityCompanyStockTrade.Deal` event precedes `Observed` when
`event.param` is the exact submitted engine deal and its buyer, seller
and ware match. Fields are pipe-delimited, in this order.

```text
request_id|registry_epoch|trial|requested_qty|quote_unit_ct|quote_game_time|complete_game_time|deal_buyer_component|deal_seller_component|deal_ware|transferred_qty|destroyed_qty|deal_unit_ct|deal_nominal_ct|player_before_ct|player_after_ct|faction_hasown_before|faction_hasown_after|faction_before_ct|faction_after_ct|ship_ware_before|ship_ware_after
```

Times are seconds of `player.age`. Balances are absolute account samples
taken before submission and at completion. The faction account flags are
0 or 1 and the corresponding balance is `unknown` if no own account
exists. Prices and balances use 64-bit cents. The deal nominal total is
the engine unit price multiplied by the actual transferred quantity.
The absolute ship ware counts are stock reads before and after; their difference
must match the transferred quantity. Older six-unit records omit these fields.
The engine price and nominal total describe the deal. An independently
matched account transaction is needed to prove the actual debit. The
existing `Observed` event still reports the original event-window fields
for compatibility. Station cargo and ship/station money can change for
reasons unrelated to the trade during that interval.

Stock `md/rml_trade_wares.xml` records `event.param2` at trade start and
compares it with `event.param` at completion. It then uses
`event.param.transferredamount`. Stock `md/notifications.xml` uses the
deal unit price multiplied by transferred quantity for trade achievements.
Neither script establishes settlement rounding rules.
