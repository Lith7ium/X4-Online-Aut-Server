# NPC destruction through the legacy authenticated provider

One native Bazzite X4 9.00 instance ran an ordinary recovered Authority provider with loopback TLS and two lightweight protocol pilots. The pilots downloaded and verified their offered checkpoints, took separate Company seats, and held current motion receipts. The fixture primed the registered Antigone freighter MFQ-419 (943) to 0 shield and 10 hull. HVC-420 (1073) then sent one 26-point contact over the normal combat client. The same pilot sent a separate 26-point contact against the surviving Company ship KHI-630 (1074).

| Order | Target | Native operation | Authenticated reply |
| --- | --- | --- | --- |
| 1 | MFQ-419 | At game time 4.18117 s, the stock cue returned `applied`, `killed=1`, 0 shield, 0 hull and maxima of 5,750 shield and 6,400 hull. | `confirmed`, 0 shield, 0 hull. |
| 2 | KHI-630 | At game time 4.86852 s, the stock cue returned `applied`, `killed=0`. Shield fell from 2,366 to 2,340 and hull remained 3,600. | `confirmed`, 2,340 shield, 3,600 hull. |

At 4.18865 s the native destruction event identified attacker `0x40acb` and victim `0x2380f` with method `lowattentionattack`. At 5.09893 s the same actor was still registry entry 943 in both directions, existed as a real ship class, was inoperable and was a wreck. Hull and maximum hull then read zero. The provider performed both native operations once and served both ordinary confirmations. The exact timestamp of its private Lua health getter was unrecorded. The legacy Lua confirmation required that getter to agree with both immediate MD health values and both maxima.

The final pilot departure saved the paused world normally. The marked checkpoint `checkpoint-7cb589ba647f40f2982578b922228b2f` has SHA-256 `8a203cdc5d55e8c6ce7def03167af08df41ac6bf97366cbb127a238c104cc5dc`. Independent inspection of the saved XML resolved the event actors by exact ID and ship code. MFQ-419 was serialized with `state="wreck"`, shield 0, the same attacker and attack time, and registry bits 1/0. KHI-630 was serialized with shield 2,340 and registry bits 1/1. Its root hull was omitted in the saved XML. The value 3,600 comes from the earlier native hit confirmation. The original sealed input stayed byte-identical. The provider exited successfully with no owned process or engine lock remaining.

## Provenance

The later development checkout at `7e248d7c` read this confirmed save with its ordinary marker and join-registry validators. Across 5,048 issued indices, MFQ-419 at 943 was bound and inoperable. The operational-ship observer excluded the wreck and still resolved HVC-420 at 1073 and KHI-630 at 1074. This check used the retained save on disk. A cold native X4 load of this wreck remains open.

Importing an archived pilot helper prepended its checkout paths before the provider imported the stock modules. Installed stock Lua matched `56a00bcc3a5c5c4e763cf2d7b0c071372513ce7f` byte for byte and installed MD matched after normalizing only the runtime lease fields. The wrapper intended to timestamp the stock getter did not attach to the older function signature. The test therefore establishes ordinary authenticated behavior on 56a00bcc. The corrected harness separates the archived pilot imports from the provider worker, asserts the worker module paths and requires an exact getter hook. Those changes were checked offline.

| File | 56a00bcc | fa32012e and e9e1dafa |
| --- | --- | --- |
| Native Lua receipt adapter | `4e9a7294` | `5b25de56` |
| Native MD stock cue | `ee09141a` | `1b2bcf91` |
| Native Python receipt parser | `6ef2a868` | `8878a207` |
| Company combat service | `10b463bb` | `b17bb560` |

The two newer revisions share the exact four blobs. They move the stock single-hit operation into `ApplyStock` and add an optional group path. Potentially lethal groups are fenced out of the grouped native operation. Both versions retain the single-hit requirements for the stock `killed` flag, terminal root HP and independent numeric health agreement, including the maxima. Timing and end-to-end lethal behavior on the two newer revisions remain unmeasured.
