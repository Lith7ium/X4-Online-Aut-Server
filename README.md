# X4 Online — the authoritative-server fork

We are developing co-op for X4: Foundations. The idea is to share one Company and one persistent universe while each pilot flies their own ship, even in a different sector. A Linux server runs X4, saves when the last pilot leaves and restores that world for the next session. This is an independent fan project in internal testing.

**[Open the active development branch](https://github.com/Lith7ium/X4-Online-Aut-Server/tree/test/base-game-two-pilots-20261124)** for the current code, contributor guide and test reports. The files on this default branch preserve an earlier development checkpoint.

## What players have seen

Two people met in Black Hole Sun V on the same Fedora-hosted, base-game world. One joined from Linux on the LAN, the other from Windows over the internet. They saw each other's ships and the server saved when they left. At that session the other ship appeared stationary to the Linux pilot, and damage seen by the shooter did not appear on the target's screen. [Human test report](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/test/base-game-two-pilots-20261124/reports/AUTHORITY-BASE-GAME-HUMAN-FLIGHT-20261124.md)

Later automated tests measured reciprocal movement in two Linux cockpits and a hit that took a reserved pilot's ship from 2,366 to 2,340 shield points on the server. A separate Windows cockpit test confirmed health corrections in both directions. A human Linux/Windows combat retest is still outstanding. [Current gameplay status and evidence](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/test/base-game-two-pilots-20261124/docs/AUTHORITATIVE-STATUS.md)

## Two multiplayer approaches

This fork puts the shared world under one server's control. Silver develops a parallel lockstep implementation that coordinates inputs across X4 simulations. Both approaches are active. [Authoritative-server source](https://github.com/Lith7ium/X4-Online-Aut-Server/tree/test/base-game-two-pilots-20261124) · [Silver's lockstep project](https://github.com/Unofficially-Online/X4-Online)

The project code is available under the [PolyForm Noncommercial License 1.0.0](LICENSE.md). See [licensing](LICENSING.md) and [notices](NOTICE.txt).
