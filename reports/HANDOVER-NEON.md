# Current handover for Neon and Cobalt

**24 November 2026. Last team check-in at 15:00 Berlin time.** Astra leads Lith7ium's
authoritative fork. Silver runs his separate fork. Lith7ium approves major
architecture changes. Compare results and allocate further work in
`#x4-authorative-server` at HH:00 Berlin time. Between checks, continue the
current task. This document supersedes the [20 November handover](HANDOVER-NEON-20261120.md).

## Current assignments

**Neon owns NPC movement from the server to a Windows pilot's screen.**
Build on your native reacquisition branch at `4b181f5f`. Bring gameplay
capture and playback into an admitted session, exercise multiple visible
ships, and measure the cost against the 10,082-entry world. Your next
concurrency case is a delayed or missing fresh sample after preparation
while another track continues on the same participant. Coordinate a shared
native run with Astra when the ordinary player path is ready. [Scope and acceptance evidence](NEON-NPC-SHIP-MOTION.md)

**Cobalt owns map-visibility research and the ordinary Windows player
preflight** ahead of the coordinated Internet test. The initial observer
build was `cf57d0b1`. For the joint flight, use the matching Linux and
Windows packages at `2a2b569d`. They retain a confirmed moving sample when
the observed pilot comes to rest. Exchange session settings privately.
[Windows flight procedure](../docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md#observe-a-short-cross-sector-flight)
[15:00 Slack check-in](https://x4multiplayeraicoms.slack.com/archives/C0C9ZV35VSB/p1795528846850779)

Astra owns movement of player ships, the provider and integration on
`experiment/authoritative-server-20261115`. For read-only contributor
accounts, a PR from a personal fork or a patch with a source commit works.
Bring the result, outstanding decisions and next substantive milestone to
the hourly check.

## NPC work already verified

The Linux loopback on Neon’s port `05e68bf7` exercised ENH-870 with real TCP
and UDP, simulated visibility and a synthetic source. Neon then enrolled a
new YDA-317 in a private native Windows game. After reacquisition, a requested
20 m/s yielded 31.766 m in 1.588 s. This Windows run supplies the first
component for Neon's current assignment. [Windows measurements](https://github.com/Lith7ium/X4-Online-Aut-Server/blob/4b181f5f/reports/AUTHORITY-NPC-REACQUISITION-WINDOWS-20261124.md)

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
- The ordinary company client now supports a nearby cockpit target and one
  selected ship on a map of another sector at the same time. Native Bazzite
  sampled a stationary player ship every 0.117 s at the median. Fedora
  downloaded the world over LAN TLS and reached the X4 menu, then its
  Steam IPC failed before admission. In a separate native session WOO's
  stock map selected WRN in the other sector. Its position agreed with the
  saved world within 0.51 mm. On a subsequent solo WRN flight the authority
  acknowledged native samples with nonzero velocity, including 5.76144 m/s.
  Live flight between two pilots remains open.
  [Observer implementation and native results](AUTHORITY-COMPANY-OBSERVER-20261124.md)
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

The observer-build check collected 5,517 tests. Across the strict Linux
run and targeted follow-ups, 5,493 distinct tests passed. The remaining
24 require Windows (12), external reference files (11), or the released
lane tool (1). The strict suite and live Internet gameplay each have an
outstanding acceptance check. [Test and native evidence](AUTHORITY-COMPANY-OBSERVER-20261124.md)
