# Current handover for Neon and Cobalt

**24 November 2026, 15:00 Berlin check-in.** Astra is active and leads Lith7ium's
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
   with Astra first. The station parking and cold-reload runs have finished.

If your account can only read Lith7ium’s fork, push the work branch to your
own fork and open a PR targeting `experiment/authoritative-server-20261115`,
or share a commit/patch for review at the hourly check.

For a fresh contributor checkout:

```sh
git clone --branch experiment/authoritative-server-20261115 https://github.com/Lith7ium/X4-Online-Aut-Server.git x4-neon
cd x4-neon
git switch -c work/neon-npc-reacquisition
git diff --stat 58c07235..origin/review/neon-npc-reentry-20261120
```

Neon acknowledged the assignment from a separate Windows PC. His port is on
[work/neon-npc-reacquisition at 05e68bf7](https://github.com/Lith7ium/X4-Online-Aut-Server/commit/05e68bf7),
based on `519cd0f1`. Astra ran
`EnhancedNpc870ReacquisitionLoopback.test_enh_870_reacquisition_cancel_and_fresh_alignment_with_other_observer`
once on Linux. It passed with real loopback TCP/UDP, a synthetic NPC source and
simulated visibility and game callbacks. Neon subsequently published his Windows
harness and report at `4b181f5f`. His native game used newly enrolled YDA-317
in a private creative start. After reacquisition, a requested 20 m/s yielded
31.766 m in 1.588 s. ENH-870 remained the loopback case.
[Neon's Windows measurements](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/4b181f5f/reports/AUTHORITY-NPC-REACQUISITION-WINDOWS-20261124.md)

For the next check, Neon owns the delayed or absent fresh sample after
preparation. Test two tracks on the same admitted participant and record
control, pause/save/resume and continued delivery to the other track.
Cobalt owns the ordinary Windows company-player preflight on the observer
branch ahead of a coordinated Internet test. The matching Linux and Windows
observer builds use code `cf57d0b1`. Exchange session settings privately.
[15:00 Slack check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795528846850779)

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
  synthetic sources. In a later Linux flight the pilot strafed WRN-302 by
  45.17 m. The provider adopted the position and a fresh GPU-free X4 read it
  from the confirmed last-departure save within 0.52 mm. The software client
  sampled too slowly to report velocity during this flight. Remote rendering
  and direct Windows Internet gameplay with two pilots remain open.
  [Flight and cold-reload measurements](AUTHORITY-COMPANY-FLIGHT-20261124.md)
  [Native cockpit results](AUTHORITY-COMPANY-MOTION-20261124.md)
- Astra owns server-avatar parking. Lith7ium chose the walkable player HQ
  when available, with a well-protected station until then. The current seed
  has no HQ. Its preferred fallback is the Argon defence platform XPJ-169 in
  Black Hole Sun V, with trading station JZJ-119 as backup. A native server parked on XPJ-169, saved the world and stopped. A fresh
  server loaded the same docking bay with both Company ships free. There were
  10,082 issued entries and 17 were unavailable after relocation. An initial
  preflight failure had stopped before any write. [Parking and cold-reload evidence](AUTHORITY-SERVER-PARKING-20261124.md)
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

## Internal operator kit and validation

The owner has the private archive
`X4-Authority-GPU-Free-Company-Trial-20261124.zip` (30,185,896 bytes,
SHA-256 `e4ccf955f2bafa800d534141dce45b0b1e17f24f4916fc186b6ab9b59bec4294`).
It pairs the Linux provider and Linux/Windows players from `519cd0f1`
with the cold-verified parked world. The packaged restore reproduced the
expected save digest. The handover repository contains the project code
and reports. The operator supplies a private connection file and a licensed
matching game installation separately.

The strict Linux check at the package revision collected 5,480 cases. Its
first run had one packaging-test error and 56 environment skips. The test
had omitted the defence evidence required by the real parking installer.
Its fixture was corrected in `4022460d`, and the 12 focused parking/package
tests passed. Of the skipped cases, 30 passed using the existing local
validation environment and two more passed against the locally resolved
stock XML. Across these runs 5,456 distinct cases passed. The remaining
24 require Windows (12), external reference data (11), or the released
lane runner (1). A completely passing strict suite remains outstanding.
