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
separate provider position cue also rejects ships with an active or
assigned pilot.
[Standard reader](../src/authority/lua/company_observer_target.lua) ·
[Cockpit reader](../src/authority/lua/company_cockpit_observer.lua) ·
[Cockpit MD](../src/authority/md/company_cockpit_observer.xml) ·
[Hull adapter](../src/authority/lua/company_hull_playback_x4.lua) ·
[Provider pose cue](../src/authority/md/company_ship_pose.xml)

A private server trial moved one ship with its idle NPC captain under
additional identity and order checks. The retained-captain berth trial
also confirmed physical docking with the original captain. Those
experiments leave regular flight playback unchanged. A focused
read-only receiver check could compare the three local crew fields
and a standard HUD receipt against the indexed route for the same
copied ship.
[Private pose trial](AUTHORITY-COMPANY-STOCK-TRADE-20261124.md#one-server-pose-after-departure-with-the-original-npc-captain) ·
[Berth evidence](AUTHORITY-COMPANY-BERTH-RETAINED-CAPTAIN-20261125.md)
