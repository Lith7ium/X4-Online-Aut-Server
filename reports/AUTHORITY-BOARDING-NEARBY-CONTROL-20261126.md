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

## Run-03 stopped before the deferred disarm readback

The same confirmed source and provider revision passed the narrower
primary-weapon precondition. The new scout spawned 2,999.5 m from ZNG-430
and 18,468 m from ARU-541. The fixture matched two distinct weapons on
that scout and dispatched their removal once. The readiness cue
evaluated in the same game frame at 569.015 s, before the one-game-second
disarm readback. It treated pending setup as a refusal. Both target
primary handles matched, its shields were at 100%, and the two boarding
ships were still at `incluster` without physics readiness. The trace
does not establish deferred disarm, helm entry, high-attention play,
a stock operation, or pod behaviour.

The private cue now waits for the delayed equipment readback to resolve
before evaluating helm readiness. A refused disarm can terminate
immediately. Successful zero-weapon readback dispatches the one helm
transfer and starts a 12-game-second readiness window with 250 ms
checks. The terminal check still requires the exact occupied helm and
both actual actors to be visible and physics-ready. The offline ordering
check used the 569.015 s run-03 trace and synthetic delayed success,
disarm refusal, and deadline cases. No further native run tested this
correction.

No checkpoint or client endpoint was created. The source and copy were
unchanged, and all owned processes exited. The private
`nearby-observer/run-03/` engine log SHA-256 is
`09156a1596b063663f29b00853cc7ac673f42e5ec9c9cdcc6dfc1127d57ee25e`.

## Run-04 observed stock approach with a nearby observer

One Bazzite provider at `0026960b3230df0f854f357e3f0e0bcff8b46a18`
reopened the same disposable source. The provider bridge, cold restore,
boarding witness and parking helpers matched the earlier revision. At
570.65 game seconds, the new scout had zero operational weapons or
turrets after deferred removal. At 572.422 s the avatar occupied and
controlled that scout. ZNG-430 was at `nearby` and ARU-541 at `inzone`,
both physics-ready. ZNG kept both original operational primary handles,
30% hull, 100% shields, zero turrets and zero defending crew. The stock
exports created, assigned eight marines and started one operation at
100/100.

The two stock pods from operation `0x430b` were physics-ready at
`inzone` at the 592.997 s pod-start events. Separate stock operation
reads identified the same pods with five and three marines. Across
1,192 samples per pod both remained at least `visible` and
physics-ready. The first 231 approach samples per pod, from 593.546 to
727.518 s, also met those conditions for both ships. At 728.176 s ZNG
first measured `adjacentzone` with physics readiness false. At
813.521 s ARU first measured the same lower state.

At 1293.44 s the observer reached its 720.018 s game-time deadline and
X4 confirmed pause. The last reported stock phase was approach. The
trace contained no pod attachment or destruction, infiltration,
fighting, removal, or ownership transition. This contrasts with the
earlier paired low-attention pod losses, but only the sampled initial
approach had all four objects physics-ready at visible attention or
higher. The operation was still active at the observation deadline.

A private legacy `POD_STARTED` print supplied its five intended
values with a sixth format placeholder, leaving a literal `|%s`.
The strict archive parser rejected that record and did not produce
a provider final-state verdict. The unmodified dedicated attention
records, separate operation pod records and native pause line support
the observations above. No endpoint or checkpoint was created, both
source copies stayed unchanged, and owned processes exited. The
private `nearby-observer/run-04/` engine log SHA-256 is
`99439ddad179f39a4c80954caca1e4034fb1f240fefb415af4392628de6e391a`.
