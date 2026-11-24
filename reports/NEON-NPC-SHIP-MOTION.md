# Neon owns NPC ship movement from authority to observer

The desired outcome is a moving NPC on a Windows pilot's screen whose identity,
sector and trajectory agree with the central X4 authority. Own the implementation,
integration and evidence for this subsystem. Astra owns the provider, player
ship movement and the shared session. Cobalt owns map-visibility research and
Windows player launch preflight. Lith7ium approves major architecture changes.

Work on a dedicated branch on the authoritative fork. Your current
`work/neon-npc-reacquisition` at `4b181f5f` contains the verified recovery
experiment. The matching player and two-view transport code is at `cf57d0b1`.
Use these as references when bringing the behavior to the normal client. Keep
the working source and test branches available for comparison.

## Scope

1. **Wire a real NPC into an admitted session.** Resolve an existing operational
   NPC through the saved registry on the authoritative game and the observing
   game. Carry sector, identity and lifetime, sequence, position and real-time
   velocity from a moving source. Preserve game time and monotonic capture time.
   Drive the existing local hull and read its position back on a later game
   update. Record the source sample used. Begin with one ship, then run with
   multiple visible NPCs. Coordinate one two-machine run with Astra when the
   normal path is ready.
2. **Own visibility, recovery and concurrency for those ships.** The local
   high-attention area and the open map can cover different sectors at the
   same time. Subscribe to visible ships in those views. Exercise loss of view
   mid-prepare, return after an unseen sector change, disappearance, stale
   lifetime, delayed capture, a second track on the same pilot and another
   pilot receiving updates. Verify that an optional movement track cannot
   hold up pause, save or another pilot's updates. Show what is discarded,
   when one fresh alignment is allowed and when ordinary playback resumes.
   The previously assigned delayed-prepare check belongs here.
3. **Measure scalability.** Use the existing world with about 10,000 registry
   entries. Report total registry size, actual visible/active tracks, capture
   frequency, bytes per second and frame or CPU cost on the machines measured.
   Vary the number of visible ships on a bounded workload. Identify the first
   measured bottleneck and improve it if it blocks playable movement.
   Explicitly label synthetic load, native local gameplay and a distributed run.

Use the approved interest rules and one high-attention owner per area.
A change in authority ownership, persistent identity, network architecture or
world/save semantics goes to Astra and Lith7ium for a decision before adoption.
While a decision is pending, the existing reference path, measurements and
regression cases can advance independently.

At each hourly check, report a commit, a concise result, the next independent
milestone and any decision or shared test resource you need. Push technical
evidence with the branch. A native Windows replay and a live Windows receiver
fed by a real authority answer different questions. Record both where available.
