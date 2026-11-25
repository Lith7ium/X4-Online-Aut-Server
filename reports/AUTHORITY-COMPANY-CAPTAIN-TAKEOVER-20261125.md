# Native Company captain relief and player takeover

An isolated Bazzite X4 9.00 client took the physical helm of WRN-302 while
the original Company captain stayed assigned and aboard. The stock Get Up
call then released the player from the helm. The captain was back in the
chair at the final sample, but the complete restoration check timed out.

The trial loaded a read-only copy of confirmed checkpoint
`checkpoint-1dac0e4056eb41739aeecd30dbb1f1ca`, SHA-256
`cec4cedd6c3aa39ae189163f9baae2fe3d03e7fc7fa09923eb33ce34d32914d5`.
Registry index 7354 mapped to WRN-302 externally docked at BIM-169. The
saved Company NPC is component `[0x15d9df]` with seed
`2211373338772438693` and the attached AI pilot post. The probe checked the
seed from the save and retained the exact runtime actor reference. Each
stage checked the registry in both directions, ship and NPC ownership,
`ship.assignedaipilot == captain`, and
`captain.assignedcontrolled == ship`. From boarding onward, the probe also
checked the captain's presence aboard through X4's ship and context reads.

| X4 game age (s) | Native observation |
| ---: | --- |
| 321.992 | Registry and assigned pilot matched. The stock teleport placed the player aboard on foot. |
| 325.085 | Player aboard and out of control. The captain occupied the chair. The probe sent the stock conversation's `npc__leave_post` signal. |
| 331.159 | Chair clear. The same assigned captain remained aboard in the busy state. The probe requested control through stock teleport. |
| 333.279 | Player occupied the chair. X4 reported the target ship as controlled, occupied and containing the player. The original captain remained aboard and assigned. |
| 334.242 | The stock Docked menu's `GetUp()` call cleared the player's control and chair. The player and the same assigned captain remained aboard. The probe sent `npc__return_to_post`. |
| 365.474 | The original captain was in the chair again and no longer busy. The player remained aboard on foot. |

Both stock teleport calls used the private research allowance. All steps ran
after X4 resumed. In particular, the captain's walk off the chair,
the later helm readback and the return check advanced simulation time.
This run leaves the paused client admission path unverified. The existing
paused native admission evidence applies to a captain-less ship in space.

The final sample came after the 30-second restoration deadline and the
harness returned exit code 1 with `failed_restore`. The receipt records
chair occupancy and the busy state. It does not expose `ship.pilot`, so
the evidence cannot establish when or whether X4 restored active AI control.
The earlier launch stopped before boarding because NPCs do not expose the
ship-only `idcode` property to Mission Director. The corrected run used
the saved NPC seed and exact runtime reference.

The isolated run opened no network session and wrote no checkpoint. The
source SHA-256 matched after cleanup, and no owned X4 processes remained.
