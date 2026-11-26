# One stock gun contact between two native Linux cockpits

In this private X4 9.00 run, the shooter’s local simulation recorded **47 shield hits** in one burst. The production sender saw **one direct gun notification** and forwarded that contact. The authority applied 26 shield points of damage, and the passive target’s own X4 process read the reduced shield back.

## Setup and observations

Two automated Linux cockpits ran simultaneously in separate disposable profiles and systemd scopes on Bazzite. A separate Fedora host ran the native X4 authority from a copy of the base-game checkpoint. The ordinary launchers used Company motion and combat at `56a00bcc3a5c5c4e763cf2d7b0c071372513ce7f`. The clients connected to the provider’s pinned TLS endpoint through an SSH forward on the private LAN. They authenticated separately, loaded distinct marked offers, and shared motion window 3.

- HVC-420 (1073) fired a 0.90-second stock S Bolt Repeater Mk2 burst at KHI-630 (1074). The private stimulus placed the target in the shooter’s local scene. The native witnesses recorded 849.875 m throughout 50 position and visibility samples, with both actors stationary. They observed 66 projectiles and 47 root shield callbacks of 26 points each. The shooter’s local copy of KHI-630 went from 2,366 to 1,144 shield points. Its hull stayed at 3,600.
- The single direct gun notification coincided with the first local shield callback. The production sender forwarded impact ID 1 once. The authority’s native X4 call confirmed global order 1 and shield 2,340, down from 2,366. Hull stayed at 3,600. The native dispatch and receipt were 1.045 seconds apart on Fedora.
- Fedora then made a separate native read of the target. The passive KHI-630 cockpit applied the received revision 1 and independently read back 2,340.00003 shield points through X4. The FFI readback matched the authority’s 2,340 within the cockpit receiver’s numeric tolerance. KHI-630 fired no shots.

The run proves delivery of one directly observed stock contact to a live target cockpit. Full-burst accounting, other weapons, native NPC damage reporting, and human Linux/Windows flight and combat over the internet remain open. The earlier short-burst control admitted both cockpits and recorded 12 projectiles without an observed shield debit. [Short-burst control and prior shield tests](AUTHORITY-SHIELD-REPLAY-20261126.md)

## Save, shutdown, and evidence

Both cockpit launchers exited normally. The authority recorded two admissions, paused when both pilots left, and confirmed the marked departure checkpoint `checkpoint-a6648f13b870460eb020bee14abfa6c5`. Neither host retained an X4 process or test lock. The original checkpoint was preserved.

The provider journal, two cockpit event journals, and native engine logs are retained privately. One intermediate source health read returned an error. Later source and target baselines and the separately observed post-hit target health are the reads used here. The original trial summary rejected the final correlation because its comparison used a tighter tolerance than the product receiver. A corrected audit of the same raw records compared the target response to the exact provider read and checked the independent cockpit FFI value within the product tolerance. The product launchers and normal save reported success on that run.
