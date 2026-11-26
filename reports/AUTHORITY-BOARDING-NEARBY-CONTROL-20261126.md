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

## Run-02 stopped at the target equipment check

The single corrected attempt used the same source and provider revision.
X4 returned 13 operational components for the broad `class.weapon`
query and 11 for the turret query at the untouched target. The fixture
had required two results from the broad query and refused before
changing the target or creating the scout. A deferred diagnostic then
tried to read the uncreated observer and recorded an unset property.
The terminal readback still showed 100% hull and shields, 11 turrets,
13 defending marines, and 11 service crew. No stock operation started.

An offline parse of the original save found exactly two primary
`weapon_tel_l_destroyer_01_mk1_macro` children of ZNG-430 at
`con_weapon_01` and `con_weapon_02`, separate from the eight turret
and three missile-turret children. The private fixture now filters the
operational search by the primary macro, direct ship context, and
non-turret class before comparing the same two runtime objects. It
guards observer diagnostics on the existence of that observer. That
revision has only been checked offline. Deferred observer disarm, helm
entry, high-attention physics readiness, pod attention and the stock
boarding outcome remain unmeasured.

The provider opened no client endpoint and wrote no checkpoint. The
original and cloned source remained unchanged and owned processes were
reaped. The private `nearby-observer/run-02/` engine log SHA-256 is
`c81ceb599a64a916269c7e86a4d0eb5692d591fe7eb55f6c5f33a9713975f554`.
