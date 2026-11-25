# Stock Undock lock in a Company cockpit

In private Xvfb on Bazzite, the stock Docked menu and the underlying
undock controller refused to start while the trial's player undock lock
was set. After a matching simulated server confirmation, the stock
controller accepted the request and X4 reported the undock maneuver and
its completion. The stricter physical disengagement check timed out.

The trial used a disposable read-only copy of confirmed checkpoint
`checkpoint-1dac0e4056eb41739aeecd30dbb1f1ca`, SHA-256
`cec4cedd6c3aa39ae189163f9baae2fe3d03e7fc7fa09923eb33ce34d32914d5`.
The stock captain relief path put the player at the physical helm of
WRN-302 (7354) while the same assigned Company captain stayed aboard.
Before setting the lock, the probe confirmed the original external
trading bay at BIM-169 and stock `UndockPlayerShip(true)` returned
`granted`.

| X4 game age (s) | Observation |
| ---: | --- |
| 334.10 | Player helm verified. Stock check granted and Docked menu Undock predicate true. |
| 334.53 | Stock test lock set. Stock check and direct controller request both returned `impossible`. The Docked menu predicate became false. Calling its normal handler produced no departure event. |
| 339.74 | Twelve native checks kept the same ship in the same external bay during the wait. |
| 340.15 | Simulated server confirmation matched the request, ship, station and bay. The paired test lock cleared and the stock check returned `granted`. |
| 346.35 | The normal controller accepted the post-confirmation undock request. |
| 346.72–352.36 | Stock UI reported `playerUndock` and `playerUndocked`. Native MD reported undock start and end correlated to the original bay. |
| 376.43 | The strict departure predicate had still not passed. |

The strict predicate required a later observation with no physical dock,
assigned dock, parking link or internal storage and no membership in the
old bay. The harness did not record those post-undock terms individually.
The native events establish that the stock maneuver ran after confirmation.
Physical disengagement in the stricter sense remains unverified.

Stock action 175 reported the label
`\x1b[keyboard_INPUT_KEYCODE_LSHIFT]+D`, where `\x1b` is the escape byte.
The trial's input verifier accepted only a plain label at launch, so it
sent no keyboard input. A focused parser check subsequently verified the
exact escaped format without rerunning X4. The keyboard's behavior under
lock remains unverified.

The trial could read the controller's effective permission. It found no
stock lock-owner readback. The initial `granted` result and exact private
request bounded the paired set and clear to this fixture. The run opened
no network session, made no checkpoint and released all owned processes.
