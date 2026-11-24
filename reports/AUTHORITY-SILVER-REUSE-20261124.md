# Silver's lockstep build and the authority fork

Reviewed upstream main at `cea737b1` on 24 November. The latest successful
installer workflow built `d4b964a6` and produced one 43,578,083-byte artifact.
The latest main commit passed its Linux suite and Windows gate build. The
GitHub releases list had no published release. The game runs below are
Silver's recorded results; this review did not rerun his multiplayer suite.
[Installer build](https://github.com/Unofficially-Online/X4-Online/actions/runs/45324840981)
· [Main revision](https://github.com/Unofficially-Online/X4-Online/commit/cea737b1)

| Capability | Evidence on Silver's branch | Use on the authority fork |
| --- | --- | --- |
| Windows onboarding | Two physical Windows PCs at one site used the CI MSI, the game's Multiplayer menu and a join code. They loaded together and stayed in sync past tick 2,154 over a VPS relay. | Reuse Steam discovery and the user-facing flow. Keep our pinned, direct Linux-host connection. [Product run](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/NETPLAY.md#L647-L689) |
| Mid-session join | A second X4 instance on one Windows laptop joined at tick 3,324. The 188 MB join package compressed to 27 MB. The host paused for about two minutes. One controller drift is fixed; another is open. | Publish a verified temporary save into the final slot. Our host can pause during joins, and its checkpoint remains the source of truth. [Drop-in](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/NETPLAY.md#L691-L703) · [Controller findings](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/reports/LOCKSTEP-FAST-JOIN-20261123.txt) |
| Trade and fleet orders | Both test pilots bought and sold 20 food rations through stock menus. Holds and account agreed for 8,174 ticks. A separate test issued remote fleet trades over 9,287 ticks. The orders tests checked actual ship behaviour after Move, Follow, Protect, Attack and Dock. | Use stock menu callbacks and physical end-state checks. Submit each accepted financial or fleet command once to the authoritative X4 world. [Playtest results](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/PLAYTEST.md#L1462-L1508) · [Scenario code](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/tools/dev/action_tests.py#L304-L431) |
| Combat and boarding | A wing destroyed a Xenon ship. Marines claimed an ownerless scout. Hostile boarding remains listed among the unrun actions. In a separate Windows/Linux battle, the world census agreed through tick 1,500 and differed on eight objects at tick 2,400. | Reuse target, shield, hull, crew and ownership observations. Two-human PvP and actual hostile boarding still need independent authority-server tests. [Combat and claim](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/PLAYTEST.md#L1495-L1536) · [Remaining actions](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/PLAYTEST.md#L1778-L1785) · [Mixed-platform run](https://github.com/Unofficially-Online/X4-Online/commit/2af3958c) |

The sector and movement work offers a targeted test for our next handover.
Silver exercised players separating into different physics scenes and
rejoining. His join report traced a medium ship's drift to the *starting
rotation of its movement controller* even though the positions initially
agreed. For our server-led motion, compare orientation, angular velocity
and the first frames after an ownership change alongside position and
velocity. The lockstep replay and process-local controller records depend
on a shared simulation tick. Adopting those records would require a separate
architecture decision.
[Sector observations](https://github.com/Unofficially-Online/X4-Online/blob/cea737b1/docs/PLAYTEST.md#L1115-L1139)
· [Join fix](https://github.com/Unofficially-Online/X4-Online/commit/cea737b1)
