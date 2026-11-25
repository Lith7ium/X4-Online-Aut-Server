# Indexed Company cockpit playback on Bazzite

The regular cockpit observer in `bc7e9150` resolved WRN-302 on radar
while a different Company ship, YRD-027, was selected in the native X4
HUD. The route was withdrawn before the local fixture supplied its
first replay sample. Native position playback remains unverified.

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

The fixture next reported `cockpit-view-lost`. The log gives no cause
for the withdrawal. It contains no `LOCAL_REPLAY` or
`FOLLOWER_READBACK` records. There are no final positions for WOO-831,
YRD-027 or WRN-302 in this run, so displacement cannot be measured for
any of the three ships. No further run was made.

The game exited with no remaining owned processes and the source save
retained its SHA-256. The private result-file digest is
`df70ee95bc7b760e18c224e9e63ffbdbf9d72a894090238bc01d62a8ef949745`.
