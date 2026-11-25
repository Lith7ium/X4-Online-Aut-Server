# Stock Company fleet order on Linux

On 25 November 2026, one disposable X4 9.00 run on Bazzite exercised the
stock `MapMenu.orderMoveWait` handler directly. The observer verified the
native destination sector ID and all three coordinates before forwarding
one `CreateOrder` call. The run recorded the queue and flight in the
original sector. Mouse input and multiplayer replication were outside
its scope.

At revision `92a10651e18e008764c9896e2c7876798770b4c4`, the trial selected
the undocked, idle Company ship WRN-302 (native ID 225772) with its assigned
NPC captain (225778). The player occupied no ship. The stock handler issued
one `MoveWait` for `cluster_01_sector001_macro` (native ID 223476). Queue
entry 1 went from `ready` to `started` and reached the stock sync point
11.68 game seconds after dispatch. The last sampled command action was
still `flying`. The observer stopped at that sample because its 500 m
destination tolerance was exceeded.

| Sample | Sector position (m) |
| --- | --- |
| Before issuance | -168488.468750, -3648.029053, 46850.625000 |
| Exact stock destination | -166488.468750, -3648.029053, 46850.625000 |
| First sync-point position | -164548.484375, -2763.395020, 46049.417969 |

WRN-302 displaced 4,116.793 m and was 2,277.729 m from the requested point
at the sync point. The run confirmed native acceptance, movement and the
order's sync point. It did not establish a position within 500 m, a later
settled position, or the cause of the offset.

The first launch at `abf70d48` stopped before forwarding the order at the
destination assertion, which compared Lua component wrappers directly. The stock map
reads the order's destination using `ConvertIDTo64Bit`; the second
observer used the same conversion and logged matching native IDs and
coordinates. Both launches used fresh copies of
`checkpoint-17f23f30eed444989f5f22cc65bbaaf5`. The original save retained
SHA-256 `afec7163aa862c9989199eea26d3f88c3ce3fe92168a9869d6da17235015d9be`.
Owned processes stopped, the shared lock was released and Steam identity
remained stable. Second-run private evidence SHA-256 was
`1b5eee490b61ff04334f4eef1e77521429e93c8d1187918ab263842972c1ee8b`.

## Passive follow-up through a stationary hold

Stock `order.move.wait` calls `move.generic`, requests `command.wait` and
`stop_moving`, then invokes `order.wait` with the same order still active.
The `order.wait` initializer sets the sync point without checking position
or velocity. The `playerprecise` branch of `move.generic` still passes the
requested position through `get_safe_pos`. The source permits an adjusted
destination. The runtime observations do not identify its cause.

A final fresh copy at `3c17a447efd2b9587015ed6a29fdf6272211645f` issued
one identical order to WRN-302 (native ID 225765, captain 225771).
The passive observer sampled position every 0.5 game seconds and
calculated speed from consecutive positions. At its first sync-point
sample the ship was 2,199.228 m from the requested point and still
averaged 172.402 m/s over the last sample interval. It then held
`(-168482.843750, -4315.040039, 46101.707031)` m at 0.000 m/s across
seven samples spanning 3.015 game seconds. The UI action string remained
`flying` during that hold. The ship displaced 1,002.902 m from its start
and held 2,232.334 m from the requested point, failing the unchanged 500 m
criterion. The queued order remained at its sync point throughout.

Owned processes stopped, the shared lock was released and the original
save and Steam identity were verified unchanged. Final private evidence
SHA-256 was
`ac74342f568eed991e26ad08a092a5c6857b6ca389ea715cbab24ea677f27ab6`.
