# Native Company fleet order through the Linux pilot

On Bazzite with X4 9.00, one regular Linux pilot controlled WOO-831 (1970)
and submitted **Fly and Wait** for the unreserved NPC-captained WRN-302
(7354). The CPU Vulkan provider and pilot used isolated profiles, private
displays and pinned loopback TLS. The launchers ran from the checked-out
source with `--company-purchases --company-fleet-orders`.

Both attempts recovered a fresh private import of the same confirmed
checkpoint, `checkpoint-17f23f30eed444989f5f22cc65bbaaf5`. The import
preserved the original save, manifest, supervisor result and sealed
purchase ledger byte-for-byte. It archived two prior trial cues alongside
the extension after verifying their definitions and the completed undock
request. Ordinary provider source verification passed.

The driver opened the native MapMenu, selected WRN-302 using its stock
selection callback and supplied a programmatic sector point to the stock
right-click context-menu helper. It verified the mounted `flyto` button
and invoked its registered click callback once. This exercised the native
stock action and authority bridge. Physical pointer input, flight
arrival, Windows and WAN operation remain unverified.

## Corrected run at `494b470b`

| Observation | Result |
| --- | --- |
| Request | `75755c7bf98a84a959bd5add00000001`, `cluster_01_sector001_macro`, (−166488.468750, −3648.029053, 46850.625000) m |
| Native provider | One verified `MoveWait` for ship `0x371e6` and captain `0x371ec` at game age 496.085 s |
| Regular pilot | Matching accepted receipt at its game age 496.37 s. Native local queue held zero entries before the click, immediately after it and at receipt. |
| Last departure | Pilot exited cooperatively. The provider paused and published `checkpoint-76697f7e4d3b4110937567d8cca42a85`. Both games and private displays stopped, and the host game lock was released. |
| Confirmed save | WRN-302 kept captain seed `2211373338772438693`. Its queue contained the original default `Wait` and exactly one started `MoveWait`. The saved journal held the exact request key with value 1 and count 1. |

The confirmed departure SHA-256 is
`37d0f0299c247c99be7edba0e17fa30e1b1b32384600f9dd794ed35cc6bfc3d4`.
The ship still had its original zone-local position at the time of that
save.

## Earlier run at `3f109634`

The earlier run reached the same stock action, registry ship and
destination. X4 rejected the native journal write because the string key
lacked its required dollar prefix. The provider returned a matching
`unknown` receipt before reaching `create_order`. The local pilot queue
remained empty. The independently parsed confirmed departure save held the
same position, captain and original default `Wait`, with an empty
attempted-ID table. That save was
`checkpoint-4ef2a51580294cd3815e1af2058a6c6e` with SHA-256
`b63db9f575ea489593238450a0a09c1014196e01abca55fca2685d07a07610d0`.

Raw evidence is retained privately on the test host under
`.local/native-stock-fleet-pair/`.
