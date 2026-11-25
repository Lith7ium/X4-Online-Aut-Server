# Indexed Company cockpit playback on Bazzite

The regular cockpit observer in `bc7e9150` resolved WRN-302 on radar
while a different Company ship, YRD-027, was selected in the native X4
HUD. A follow-up using the ordinary transport clock confirmed the same
route with a freshly spawned HUD target and supplied one replay sample.
The route expired before a later position readback. Native position
playback remains unverified.

Each attempt used one Bazzite X4 9.00 process, a private Xvfb display,
Mesa llvmpipe and disposable profile 45841. Each started from a fresh
copy of the confirmed save from the [24 November playback
rehearsal](AUTHORITY-COMPANY-SHIP-PLAYBACK-20261124.md). The planned feed
was an archived flight replay through the regular receiving modules.

## First two setup attempts

The first attempt loaded the expected checkpoint and confirmed the
800 m setup. The current seat module returned `PAUSE_OR_REGISTRY_LOST`
because the fixture tried to take the seat after unpausing. The second
attempt paused after setup, confirmed control of WOO-831 through the
current seat module, resumed and selected the newly created, uncrewed
YRD-027 through the stock HUD. X4 then returned these sector positions
before playback, in metres.

| Ship | X | Y | Z |
| --- | ---: | ---: | ---: |
| WOO-831, locally controlled | −333111.1875 | 24156.005859375 | 380247.625 |
| WRN-302, configured pilot | −332311.1875 | 24156.005859375 | 380247.625 |
| YRD-027, HUD target | −332191.1875 | 24156.005859375 | 380247.625 |

The private overlay omitted `company_observer_target.lua`, which the
unchanged map observer requires during initialization. The production
overlay includes it alongside the cockpit observer. An offline Lua
check reproduced the map constructor's missing global. The native log
only recorded the fixture's generic initialization error. There were
no post-playback positions to compare.

The changed cockpit MD and the private setup MD passed schema
validation. These two launches exited with no remaining owned processes and
each preserved the source save's SHA-256. The result-file digests are
`8f1b1dc0c3d5ce36aca437f5ac5fbf9550167e473f1a3207db2effeca0234c8d`
for the first launch and
`defaaa144429b5c5db6b7690d403f1e502d4550c686b7a090b0eaef4cd113a0b`
for the corrected setup.

## Native radar route with the full production module set

The revised private fixture assembled its Lua and MD from the ordinary
player overlay's module lists. The staged modules matched the production
sources, with the production buffer export applied. The assembled
offline preflight kept YRD-027 on the simulated HUD, resolved WRN-302
through the cockpit slot and replayed sequences 30–39. Only that ship
received position writes in the preflight.

The next native run confirmed control of WOO-831 and selected YRD-027
with the stock HUD. X4 returned the same three starting positions shown
in the table. The regular observer then published cockpit serial 2 for
WRN-302, registry index 7354, in `cluster_01_sector001_macro`. Its route
mode was `radar` while YRD-027 remained the native HUD target.

The engine log timestamps the radar observation at 57.99 and the next
`cockpit-view-lost` at 58.99. The fixture stops on the first subsequent
cockpit view that does not identify WRN-302. It recorded neither that
view's fields nor the observer's reason for withdrawing it. The observer
requires a fresh observation within 450 ms of issuing the request.
The log lacks the request and poll times needed to establish expiry.
The private replay used `GetCurRealTime`. Normal cockpit playback uses
the network module's monotonic clock. The run contains no `LOCAL_REPLAY` or
`FOLLOWER_READBACK` records. There are no final positions for WOO-831,
YRD-027 or WRN-302 in this run, so displacement cannot be measured for
any of the three ships.

The game exited with no remaining owned processes and the source save
retained its SHA-256. The private result-file digest is
`df70ee95bc7b760e18c224e9e63ffbdbf9d72a894090238bc01d62a8ef949745`.

Starting with `837a2680`, the indexed cockpit observer records the first
occurrence of each withdrawal reason after a successful binding. For a
live flight, retain `AUTH_COMPANY_COCKPIT_WITHDRAWAL` and
`AUTH_COMPANY_COCKPIT_READBACK` from the observing game alongside the
flying pilot's acknowledged samples. A LuaJIT regression exercises the
diagnostics. The original native run predates them.

## Native follow-up using the bundled transport clock

The follow-up at `ed9a45b0` used the ordinary Linux UDP module's monotonic
clock for observer freshness, replay scheduling, local arrivals and
playback. It loaded the bundled module without opening a socket. The
production 450 ms observation freshness limit, 50 m correction bound
and five-second prediction limit stayed in place. The assembled offline
preflight forced one expiry before feeding, then reacquired the correct
cockpit route and produced follower readbacks for the archived replay.

X4 assigned the fresh idle hull the code PVF-022 in this run. The fixture
verified that exact hull in the stock HUD. WOO-831, WRN-302 and the idle
hull had the same starting positions as in the table above. The native
observer published the route for WRN-302 while PVF-022 was selected.
The fixture supplied archived sample 30 in the same X4 update that
confirmed the radar route.

| Cockpit event | View serial | Registry index | Mode | Bundled monotonic delta (s) | X4 real-time delta (s) |
| --- | ---: | ---: | --- | ---: | ---: |
| Initial empty view | 1 | 0 | None | 0.012476 | 0 |
| Route published | 2 | 7354 | Awaiting confirmation | 0.306745 | 0.327866 |
| Route confirmed | 2 | 7354 | Radar | 0.000250 | 0 |
| Route withdrawn | 3 | 0 | None | 1.563027 | 1.000000 |

Each delta is measured from the preceding transition, with initial
values measured from fixture arming for the view. The production reader
reported `expired` before the empty view was published. The fixture
stopped after that withdrawal, with no follower readback or final
position for any of the three ships. The measured gap describes this
isolated run. The logs do not establish why the next update was late.

The source save retained its SHA-256 and no owned process remained.
Private result-file SHA-256
`a812af0148717e925915f222d81831aa19aa4f9a16bc17995b95fcde73d06c17`.
