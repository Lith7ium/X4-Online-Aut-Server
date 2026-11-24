# Current handover for Neon and Cobalt

**24 November 2026, 13:03 Berlin.** Astra is active and leads Lith7ium's
authoritative fork. Silver runs his separate fork. Lith7ium approves major
architecture changes. Compare results and allocate further work in
`#x4-authorative-server` at HH:00 Berlin time. Between checks, continue the
current task. This document supersedes the [20 November handover](HANDOVER-NEON-20261120.md).

## Neon can start now

Continue the previously assigned NPC reacquisition task. When the same NPC
returns to view after an unseen sector change, the observing client has
permission to align once from a fresh server sample. Use the existing
ENH-870 fixture (registry index 1318). Keep its lifetime, prepare receipt,
fresh capture, sector/zone identity, position and real-time velocity explicit.
A view that disappears during preparation must cancel that attempt. Keep
other observers receiving their latest samples.

1. Branch from `experiment/authoritative-server-20261115` on
   [the authoritative fork](https://github.com/Lith7ium/X4-Online-Aut-Server).
   The movement/parking code snapshot at this handoff is `08ef8f92`.
   The older review branch diverged at `58c07235`. Inspect its changes before
   porting the NPC behavior. A merge of the entire review tree would import
   assumptions from the old control path.
2. The reference is `review/neon-npc-reentry-20261120` at `05d9aa28`.
   Its short cancellation/fresh-alignment test already passed. Port that test
   to your current-base branch, run that one case, then exercise the new
   behavior. Read the existing [fixture steps](HANDOVER-NEON-20261120.md#neon--reacquire-the-same-npc-after-a-sector-change).
   `CompanyMotionClient` handles admitted **player** ships. The saved NPC
   experiment uses its own registry-backed binding.
3. Deliver a branch, commit, test command/result and an evidence table with
   the NPC leaving view, cancellation where applicable and the fresh alignment
   after return. Label simulator, native Linux, Windows under Proton and
   native Windows observations separately. Coordinate any shared-host X4 use
   with Astra first. Astra currently owns the station parking test slot.

For a fresh contributor checkout:

```sh
git clone --branch experiment/authoritative-server-20261115 https://github.com/Lith7ium/X4-Online-Aut-Server.git x4-neon
cd x4-neon
git switch -c work/neon-npc-reacquisition
git diff --stat 58c07235..origin/review/neon-npc-reentry-20261120
```

## What changed since the previous handover

- The GPU-free provider runs the full licensed Linux X4 9.00 build with
  CPU Vulkan (lavapipe) and Xvfb. A native save, last departure and cold restart
  passed. A first local sizing run used about 3.72 CPU cores and up to 8.60 GiB
  summed process RSS. Provider hardware has yet to be measured.
  [Provider evidence](AUTHORITY-PROVIDER-20261124.md)
- Players select an unoccupied ship of one company. Admission verifies their
  actual cockpit against the offered full registry. The test world contains
  WOO-831 (1970) and WRN-302 (7354) in different sectors.
- An admitted player gets a separate TLS movement connection over the same
  public TCP port as control and save. A Linux X4 client took control of 7354,
  and the GPU-free server accepted 326 position samples, 325 with measured
  velocity. There was one real client. Its departure paused and saved the world.
  Two-way delivery and movement-only reconnect were tested on loopback with
  synthetic sources. A second real pilot, remote rendering, server-side pose
  persistence and direct native Windows Internet gameplay remain open.
  [Native cockpit results](AUTHORITY-COMPANY-MOTION-20261124.md)
- Astra owns server-avatar parking. Lith7ium chose the walkable player HQ
  when available, with a well-protected station until then. The current seed
  has no HQ. Its preferred fallback is the Argon defence platform XPJ-169 in
  Black Hole Sun V, with trading station JZJ-119 as backup. The first MD
  preflight failed before any teleport/save. The include-action fix is in
  `08ef8f92`. The native retry and cold reload were pending at handoff.
- Cobalt delivered the Windows map pan observations. Two runs showed a
  listed NPC onscreen, absent after panning and clearing focus, and present
  after returning. Focus changed together with the camera. Cobalt retains
  map visibility and any fixed-focus control. These results alone do not
  establish subscription behavior for every sector or zoom.
  [Cobalt's report](AUTHORITY-MAP-VISIBILITY-WINDOWS-20261120.md)

Major design decisions still go to Lith7ium. The shared account, independently
occupied sectors, one high-attention owner per area, visibility-based movement
updates and pause/save on last departure remain the working constraints.
Saves, connection files, TLS credentials and native logs stay private.
