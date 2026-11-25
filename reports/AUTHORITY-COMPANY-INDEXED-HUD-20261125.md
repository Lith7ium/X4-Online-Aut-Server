# Indexed Company cockpit playback on Bazzite

X4 9.00 selected the unrelated Company ship YRD-027 in the HUD while
WOO-831 was under local control and the configured ship WRN-302 was
800 m away in the same sector. The private fixture stopped before it
received a radar route or supplied a motion sample. Native movement
under the new indexed HUD fallback in `bc7e9150` remains unverified.

Both attempts used one Bazzite X4 process at a time, a private Xvfb
display, Mesa llvmpipe and disposable profile 45841. Each started from
a fresh copy of the confirmed save from the [24 November playback
rehearsal](AUTHORITY-COMPANY-SHIP-PLAYBACK-20261124.md). The planned feed
was an archived flight replay through the regular receiving modules.

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
validation. Both launches exited with no remaining owned processes and
each preserved the source save's SHA-256. The result-file digests are
`8f1b1dc0c3d5ce36aca437f5ac5fbf9550167e473f1a3207db2effeca0234c8d`
for the first launch and
`defaaa144429b5c5db6b7690d403f1e502d4550c686b7a090b0eaef4cd113a0b`
for the corrected setup.
