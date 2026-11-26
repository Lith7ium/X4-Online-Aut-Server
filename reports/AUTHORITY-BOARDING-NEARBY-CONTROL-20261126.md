# Native boarding nearby-observer control

One private Bazzite provider ran at
`ba6f325a925e7bbae21f646807563f91c0564959` on a disposable copy of
confirmed checkpoint `checkpoint-bf225d9dab2e4f9c8da03240e8b75ad1`.
The input SHA-256 was
`b80e43e31242758bdd1b422d157c87b46a3c327399fcc58d810957763feb1bd4`.

The stock fixture resolved ZNG-430 at saved index 1666 and ARU-541
at temporary runtime index 5049. At the pre-start readback X4 reported
two operational main weapons, 30% hull, 100% shields, zero operational
turrets, and no defending marines or service crew on ZNG-430.
ARU-541 retained eight marines.

The new observer scout spawned 2,999.5 m from ZNG-430 and 18,468 m
from ARU-541. X4 fitted two operational weapons to the scout even though
the creation cue supplied no loadout. It had no pilot, active orders,
or operational turrets. The inert-observer guard refused helm entry
at game time 568.97 s. The preflight at 571.459 s found no occupied helm.
Both boarding ships remained at native attention `incluster` with
physics readiness false. The harness refused before stock operation
creation. There was no pod or high-attention outcome to compare with
the positive control.

The provider opened no client endpoint, wrote no checkpoint, and
left no owned processes. The source and its disposable copy retained
their original confirmed save. The private engine log is under
`.local/native-boarding-crew-seed/nearby-observer/run-01/` and has
SHA-256 `231627bd9e5922ccf37ac22c830b731a19a1bf64dc7512626bc37d7e01cfed2e`.
