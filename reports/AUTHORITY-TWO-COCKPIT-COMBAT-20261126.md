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

## Stock keyboard input on an isolated range

A later experiment used one admitted HVC pilot on Bazzite and the native Fedora provider. KHI's ship had no connected pilot. The test verified an empty private sector, placed the ships 850 m apart, and checked the six standard Bolt Repeaters before pressing and releasing Space on the private X4 display. X4 measured 0.376 game seconds between the key observations.

The range recorded 36 firing events, one direct contact and one 26-point shield debit. The ordinary combat exchange confirmed impact 1 against KHI at authority order 1, leaving 2,340 shield and 3,600 hull. There were no supplemental hit submissions. Before the capture finished, the kernel reported a segmentation fault in the local X4 movement worker. There was no recorded scope OOM. The evidence does not identify the cause of the crash.

Fedora published the post-hit checkpoint as `CURRENT` at 19:46:16 CET on 26 November 2026 and promoted it to `CONFIRMED` at 19:47:44 CET. The provider reported a successful paused shutdown, and both pointers matched its final checkpoint `checkpoint-8c570bb740fe42d78cb1ea3d5d629b17`. Offline reading of the compressed native save's XML found 2,340 root shield on KHI-630. The compressed save matched SHA-256 `3f2fbe93e9c3d1bd506d20635b63c055c5f619738c2a0fb098beeb001f2eef4d`. Both hosts released their X4 processes and preserved the original input. This run demonstrates one normal hit from stock keyboard input. No native reload or live human verification was performed, and the sustained-fire forwarding trial remains open. The evening candidate does not include the private range, scripted input or supplemental-hit experiment.

## The hit in a returning pilot's downloaded save

An isolated Linux pilot received this exact confirmed Fedora checkpoint
through the regular file service and a pinned loopback TLS connection.
The probe copied the four source-store files from Fedora and supplied the
control offer locally. The client downloaded 14,105,763 bytes and staged a
new `save_009.xml.gz` using its ordinary join code. The staged file matched
the SHA-256 above. Its saved X4 marker and registry independently identified
the Company ship KHI-630 at index 1074 with 2,340 root shield. The ship had
no serialized root hull damage.

The client stopped after staging the slot. The first fixture
allowed only one relay for both control and the file transfer and timed out.
The successful run used the regular Company connection limits.

## Cold-loading the downloaded slot in X4

A new Linux X4 9.00 process on Bazzite loaded a private, read-only copy of
the downloaded slot using software Vulkan and the base-game profile. X4
reported the saved marker after loading and paused. A read-only probe
checked the forward and reverse registry entries for index 1074 and
identified the operational player-owned ship KHI-630. At paused game
time 27.45333 s, X4 reported **2,340 / 2,366 shield and 3,600 / 3,600 hull**.
The original keyboard hit, Fedora's confirmed save and the returning
pilot's downloaded world therefore agree on the 26 missing shield points.

The first observer timed out after three seconds. Its closed engine log
contained the correct marker, pause and native health reading. After the
observer adopted the recovery supervisor's deadline, a second isolated load
confirmed the same reading while X4 was running and completed normally.
Both supervisors reported zero remaining owned processes. The successful
engine log has SHA-256 `1141863b8cc47e202fce64e7971ad33dfc6e7953f6833114016e388bfb07b8ef`;
the supervisor result has SHA-256
`62dfe04e9850e31681dce9316f3f0da58740919e4e0b9c983c5c697ff2097ed8`.

This load ended while paused, before multiplayer admission, with no pilot
at KHI's controls. Live reciprocal cockpit damage over the Internet
remains an acceptance test.

## Both pilots fire on the same range

On 27 November, two native Bazzite cockpits and one GPU-free local X4 provider ran `673db290` on copies of a base-game world. Both pilots occupied Company Pulsars at a measured separation of 849.875 m. Each used ordinary Space input with six stock Bolt Repeater Mk2 guns.

| Shooter → target | Native projectiles | Locally confirmed shield contacts | Target cockpit |
| --- | ---: | ---: | --- |
| HVC-420 → KHI-630 | 24 | 1 | The provider applied hit 1 for 26 damage. KHI's own cockpit changed from 2,366 to 2,340.00003 shield. |
| KHI-630 → HVC-420 | 18 | 0 | HVC stayed at 2,366 shield. The shooting client submitted no damage for this burst. |

Both ships kept 3,600 hull. KHI's cockpit later read 2,366 shield again at the same server damage revision, confirming that regeneration reached the pilot. The range did not record aim for the reverse burst. It provides no evidence of a lost hit on that leg. The provider verified the final checkpoint `checkpoint-170e81a5a77b4206b5cead0cf3d44945`. The two pilots and provider exited normally and preserved the input save. The private result has SHA-256 `edc204b92e798002e531eedd155f7dfd5764d4307228cb25f89b52fefec48966`.

A subsequent aim check captured an unpaused frame from each cockpit showing the other ship selected under the stock reticle. The stock lead point lay within 0.38 pixels of the center in both images. The test driver lost its initial readiness record from its log window before delivering the first keypress. Neither pilot fired in that run.
