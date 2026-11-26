# Dynamic first-contact shield observation

A single native Bazzite X4 9.00 (611726) instance loaded the sealed Company
combat source in a disposable profile. The controlled HVC-420 cockpit fired
one 0.9-second stock Bolt Repeater Mk2 burst on KHI-630, a separate empty
registered hull in the same zone. The original save stayed byte-identical
and the owned X4 process exited cleanly.

The diagnostic received only the armed shooter’s epoch and registry index.
Its first stock direct contact carried the exact target object and a supported
weapon owned by the shooter. The observer checked both target registry
directions and subscribed to target 1074. A separate fixture-only reference
had subscribed before firing and supplied an independent comparison.

| Event | Game time | Observation |
| --- | --- | --- |
| First direct contact, engine log line 42 | 5.25376 s | Source `0x40ace`, target `0x40add`, target shield 2,366 HP. |
| Reference first root shield debit, line 43 | 5.25376 s | 26 HP, remaining shield 2,340. |
| Dynamic first root shield debit, line 44 | 5.25376 s | Same actor, component, loss, last attacker and remaining shield. |
| Remaining debits | Through 5.84358 s | All 37 further shield debits matched the independent reference. X4 emitted no further direct contact. |

All 38 shield hits removed exactly 26 HP each, reaching 1,378 shield HP.
Hull remained at 3,600. Neither trace recorded a collision or a hull
callback. Damage observations include the root or component identity,
event order, amount, and native last attacker. The shared game-time value
alone cannot establish the subscription order. The original engine line
order puts the first direct callback before the first positive root debit.

This test establishes the subscription order for this fixture. The log has
no per-projectile identity for later shield callbacks. Last attacker and
equal game time cannot safely distinguish simultaneous shooters, collision
damage or shield-to-hull overflow in a general fight. Late callbacks,
subsystem damage, hull impacts and the Authority forwarding path remain
unqualified by this run.

## Repeat the diagnostic

Add [the observer XML](../tools/dev/authority_dynamic_contact_watch.xml) only
to the Mission Director directory of an isolated test extension. The
existing `X4AuthorityCompanyGunSource` Arm and Disarm events control it.
Arm uses the active shooter's validated epoch and registry index. The watcher
uses one active target, expires after ten game seconds and emits at most
128 records per arm. It performs no ship search, damage operation or
forwarding. The normal client and server overlays do not stage it.

Run `python3 tools/dev/authority_dynamic_contact_evidence.py ENGINE_LOG` on the
closed engine log. It verifies the exact actors, target index and event
sequence and reports the first contact separately from later debits.
An isolated reference observer may emit matching `DYNAMIC_REFERENCE`
rows before firing. The report identifies a missing or ambiguous first debit
where comparison allows it. The reference observer, firing setup and save
remain private.

| Retained evidence | SHA-256 |
| --- | --- |
| Native engine log | `f8b664def958fa996e6b81b41d1f07872be6c1968cc2a7abb65deab31e884b7e` |
| Closed run result | `6829a4624370df5520a5bbc55c7c0c98f0cedc71d9e62a271e65e68254d2dafd` |
| Exact observer XML | `607ee0a9d0837cb6d9e3a4d57ba7898f6a18853d153d8de4ec1d27a52e66e01e` |
