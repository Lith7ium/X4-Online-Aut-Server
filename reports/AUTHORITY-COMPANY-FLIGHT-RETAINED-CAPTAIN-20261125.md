# Retained Company captains and remote flight

The 24 November human flight used two uncrewed Pulsar Vanguards,
HVC-420 and KHI-630. Bazzite selected the Windows ship and saw no
movement. Fedora's 392 accepted samples all came from Bazzite, and
Bazzite received zero snapshots. The captain exclusion discussed here
does not explain that earlier result. [Human flight evidence](AUTHORITY-BASE-GAME-HUMAN-FLIGHT-20261124.md)

## Current client behavior

The sender samples its own ship once X4 confirms the player at the
physical helm. A native takeover showed that the original NPC captain
can stay assigned and aboard while the human holds the chair. Motion
frames carry the ship identity, grant and motion data. The observer
checks the ship and any captain in its own world copy. The receiver's
crew state during live human flight remains unmeasured.
[Sender](../src/authority/lua/company_motion_source.lua) ·
[Native takeover](AUTHORITY-COMPANY-CAPTAIN-TAKEOVER-20261125.md)

The standard HUD and map readers can match a crewed ship and return a
`SEEN` receipt. That receipt confirms the local ship identity. The
indexed and multi-pilot HUD and radar readers exclude ships with a
locally active or assigned captain. The native hull adapter rechecks
`pilot`, `assignedpilot` and `assignedaipilot` before writing. The
opt-in provider position cue can now follow a ship in its current sector
after verifying its original idle NPC captain against the join checkpoint.
[Standard reader](../src/authority/lua/company_observer_target.lua) ·
[Cockpit reader](../src/authority/lua/company_cockpit_observer.lua) ·
[Cockpit MD](../src/authority/md/company_cockpit_observer.xml) ·
[Hull adapter](../src/authority/lua/company_hull_playback_x4.lua) ·
[Provider pose cue](../src/authority/md/company_ship_pose.xml)

In a private server experiment X4 applied two position updates to the same
ship after undocking, kept its captain aboard, and saved the second position.
A separate experiment confirmed physical docking with the original captain.
[Two server moves and saved position](AUTHORITY-TWO-IDLE-NPC-POSES-20261125.md) ·
[Berth evidence](AUTHORITY-COMPANY-BERTH-RETAINED-CAPTAIN-20261125.md)

## Native observer with a nearby Company NPC captain

A private Bazzite X4 9.00 cockpit controlled WOO-831 and selected WRN-302
in the stock HUD. The disposable setup put the ships in the same sector
and assigned a Company NPC to WRN-302 through the stock AI-pilot post.
The original checkpoint contained an uncrewed WRN-302. The observer used
the HUD and cockpit MD from `34646441` and the ordinary Lua readers.

| Native check | Result |
| --- | --- |
| Physical eligibility | 800 m separation within 40,000 m radar range. WRN-302 was visible, on screen and physics-ready. Stock Lua reported the ship known and radar-visible, with docked, docking and stored all false. |
| MD crew fields | `pilot`, `assignedpilot` and `assignedaipilot` were all `0x15776c`. |
| Stock Lua crew fields | All three fields resolved to decimal actor `1406828`, the same NPC. |
| Ordinary HUD route | WRN-302 resolved to index 7354, and X4 emitted a local cockpit `SEEN` receipt for archived sample 30. |
| Indexed cockpit route | The Lua HUD candidate failed the local crew check. The indexed reader requested radar for 7354. Native MD returned `UNAVAILABLE`, index 0, mode `none`. |
| Smaller issued prefix | With a live count of 10,082, a separate ordinary HUD query carrying count 10,081 returned `OBSERVED` for 7354. The run did not create a new registry entry. |

The observation fixture had no pose writer or network peer. WOO-831
stayed at its measured position while WRN-302 moved 46.4375 m between
the two native reads. The `SEEN` receipt establishes local identity
matching for the replay sample. In-session movement of a remote human
pilot's captained ship remains outside this test.

The first private launch reached the stock HUD lock but stopped on a
fixture FFI declaration before recording the crew or route evidence.
The corrected launch recorded the table above. Both launches preserved
the input checkpoint and exited with no owned processes remaining.
Private result SHA-256 values were
`77b1e2516ddf00de40cb833e2b25fe1c9d6813c1c3dc7d12a118350b79ecfc0a`
and
`e0e58f24c4aeca329fa339fcc129ae2c461707a737e54ba627d33251da747fa7`.
