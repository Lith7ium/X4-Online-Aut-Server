# Isolated native NPC destruction witness

Native Bazzite X4 9.00 (611726) confirmed one stock lethal attack and the subsequent wreck state of the same registered NPC. The trial used one disposable base-game instance, the copied Company checkpoint, and the unmodified Mission Director `Impact` cue from `fa32012e`. A private fixture submitted the command directly to that cue. This trial covers the native cue and independent local observations. Authenticated pilot transport, provider scheduling, later combat requests, and a save/reload after the kill were outside its scope.

The fixture resolved HVC-420 (index 1073, actor `0x40acd`) and Antigone freighter MFQ-419 (index 943, actor `0x23811`) in both registry directions, excluded all player contexts for the target, and primed the target to 0 shield and 10 hull HP. It issued one 26-point `apply_attackstrength` command through the stock cue.

| Game time | Native result |
| --- | --- |
| 4.03568 s | Baseline: 0 shield, 10 hull, 6,400 maximum hull. |
| 4.04348 s | Stock cue returned `applied` and `killed=1`. Its health witness and the separate Lua getter in the receipt callback both read 0 shield and 0 hull, with maximum shield 5,750 and maximum hull 6,400. |
| 4.05126 s | Stock destruction event named the same victim and attacker, with method `lowattentionattack`, 7.78 ms after the impact. |
| 4.19431, 4.55034, 5.05145, 6.04399 s | All four later reads resolved the same actor at index 943 and matched its reverse binding. The object existed as a real ship class, was inoperable, and was marked as a wreck. Both the MD and Lua reads returned 0 hull and 0 maximum hull. Lua also read 0 shield and 0 maximum shield. |

The immediate and later observations describe different stages of the same actor’s lifecycle. The separate Lua getter could still read the original maxima in the immediate receipt callback. Once the target was a wreck, the maxima were zero. The present parser requires a positive maximum hull and agreement between the immediate MD witness and the Lua health observation. This run demonstrates that the immediate values can agree and that a later zero maximum can coexist with exact identity and a verified kill. Whether the normal provider’s Lua observation can arrive after the transition under its production scheduling remains unmeasured.

The original launcher closed the native instance cleanly and returned exit 1 because its verifier expected textual booleans and X4 emitted `1` and `0`. The original result and log are preserved. The corrected offline verifier accepts both encodings, checks the exact actor and registry identities, and accepted all four wreck observations without another game run. The original sealed Company checkpoint retained SHA-256 `1ceb756b1fc6080c6a6752e51d146cfc17c72762b8af9f9c1aed7bce20fa4958`. No owned X4 processes or host engine lock remained, and the shared Bazzite launch lock was independently acquired and released after teardown.

| Retained private evidence | SHA-256 |
| --- | --- |
| Native `engine-output.log` | `bfab2fbd0378703d350d4067404f4660e93a4b8c839330818b673b2a1291f49e` |
| Original `result.json` | `4cc92e1f12938c981991b83d42a44218f62cf136afc4fb5814c40214c4d83da0` |
| Corrected `independent-verification.json` | `b18fe64c4ce8295fca23789a3d343d7d61a90415333279ea53a9363cdfa970fb` |
