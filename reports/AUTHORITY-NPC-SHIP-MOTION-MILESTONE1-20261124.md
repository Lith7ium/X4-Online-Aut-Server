# Authoritative NPC ship movement — Milestone 1 evidence (Windows, 24 Nov 2026)

Author: Neon (James Molander / `von-Benette`).
Branch: `work/neon-npc-ship-motion` (base `51723877`, preserving `work/neon-npc-reacquisition` @ `8416b817` intact for reference).
Host: Windows 11 PC (separate machine from Cobalt and Lith7ium), Python 3.12.10 (`.venv`), AMD Radeon RX 9070 XT, X4 9.00 pinned executable (`SHA-256 19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`).

## Scope delivered in Milestone 1

1. **Authoritative server-side NPC publishing on the normal client movement service (`src/authority/company_motion_service.py`):**
   - Added opt-in `publish_npc_sample(raw_sample)`, `retire_npc_track(index, generation=None, *, transport_lost=False)`, and `subscribed_npc_targets()` on `CompanyMotionService` using the **existing** V3 wire frames (`motion_view`, `motion_views`, `motion_snapshot`, `motion_end`, `motion_received`) — zero wire-protocol changes and zero changes to player-ship authority grants.
   - Enforces V3 validation, initial/post-resume baseline `real_velocity is None`, monotone sequence + `check_motion_progress`, sector-change retirement, `MAX_SAMPLE_AGE` expiry, refusal on player-owned indices, automatic NPC retirement when `admit()` grants a seat to a player, and `MAX_NOTICES = max(2 * MAX_PEERS, 2 * MAX_INDICES)` so multi-ship retirements do not falsely drop observers.
2. **Two-view multi-NPC observer (`src/authority/npc_motion_observer.py`):**
   - Coordinates independent `high_attention` and `map` slots across up to 2 sectors and 256 subscribed indices over `CompanyMotionClient`, retaining both `last_confirmed` and `last_nonzero_velocity_confirmed` per track and explicitly recording interest withdrawal, reacquisition, disappearance, and world-reset transitions.
3. **Real native Windows X4 9.00 end-to-end verification (`tools/dev/authority_npc_motion.{xml,lua,py}`):**
   - Enrolls the live universe into `X4AuthorityShipRegistry`, captures genuine AI-flying NPC motion (`source_time`, monotonic `capture_time`, finite-difference `real_velocity`), streams through `CompanyMotionService` to two admitted `CompanyMotionClient` + `NpcMotionObserver` peers, exercises offscreen withdrawal and reacquisition, drives a registered local NPC hull with guarded `SetObjectSectorPos` + `constantvel` (`<= 50 m` limit + same-zone check + `> 50 m` refusal), confirms displacement via an independent later X4 readback, cycles `pause`/`save`/`resume`, and profiles multi-ship scaling.

## Honest separation of evidence

- **Native Windows X4 9.00 (`neon-npc-motion-win-01`):** Real live X4 9.00 universe (`custom_creative`, disposable profile `45850`, **9,926 real operational ships** enrolled in 23.0 ms), real moving NPC source captures, real local X4 hull playback + independent later readback, paired with local loopback `CompanyMotionService` and two admitted `CompanyMotionClient` observers (synthetic pilot seat IDs; no second physical machine or private 10,082-entry checkpoint on this PC).
- **Loopback unit & concurrency suite (`tests/test_authority_npc_motion_service.py`):** Real loopback TCP `CompanyMotionService` + `CompanyMotionClient` + `NpcMotionObserver`, synthetic NPC sample feeds.
- **Scalability sweep (`benchmark_10082_scalability`):** Synthetic load over real loopback TCP sockets across a 10,082-entry index space (`N = 1, 16, 64, 256` visible ships across 2 sectors); no second physical host or WAN RTT yet.

## 1. Real native Windows X4 9.00 run (`neon-npc-motion-win-01`)

### Command

```powershell
.venv\Scripts\python.exe -B tools\dev\authority_npc_motion_windows.py --out .local\neon-npc-motion-win-01
```

### Setup and outcome

- **Window & safety:** `800x600`, `fast=True`, `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`, `KILL_ON_JOB_CLOSE` job object, disposable profile `45850`, isolated view `R:\x4ls-lanes\neon-npc-motion-neon-npc-motion-win-01`.
- **Outcome:** exit code `0`, `outcome="done"`, `accepted=true`, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, **0 MD/Lua errors** in `neon-npc-motion.log`.
- **Universe registry:** **9,926** operational ships enrolled into `X4AuthorityShipRegistry` in **23.0 ms** (48 stride-sampled undocked candidates scouted).
- **Live NPCs selected automatically from real AI flight:**
  - **Primary:** `QNQ-601` (`index=7417`, `actor_key=478760`, `cluster_14_sector001_macro`, scout speed **243.03 m/s**)
  - **Secondary:** `KSE-003` (`index=3915`, `actor_key=658389`, `cluster_418_sector001_macro`, scout speed **241.69 m/s**)
  - **Playback hull:** `DHQ-917` (`index=619`, `actor_key=592054`, `cluster_37_sector001_macro`)

### Stage-by-stage native evidence table

| Stage | Observed in live Windows X4 9.00 + admitted loopback observers | Result |
| --- | --- | --- |
| `0_registry` | `PopulateUniverse` across `player.galaxy` | **9,926** operational ships enrolled in **23.0 ms** |
| `1_moving_retention` | Pilot A (`high_attention=7417` in `cluster_14`, `map=3915` in `cluster_418`) + Pilot B (`high_attention=7417`) receive live V3 captures `seq 1..4` | Pilot A & B confirmed and retained `QNQ-601` seq `4` (`real_velocity=(130.23, -87.22, 115.43) m/s`, `source_time=4.4934`, `capture_time=47.952`) |
| `2_offscreen_withdrawal` | Pilot A clears `high_attention` (`withdrawal_count=1`); live captures `seq 5..6` continue | **0 deliveries** of `QNQ-601` to Pilot A while offscreen (stayed at seq `4`); Pilot A's second track (`KSE-003`) AND Pilot B (`QNQ-601`) continued uninterrupted (`seq 5, 6`) |
| `3a_reacquisition` | Pilot A re-subscribes `high_attention` to `QNQ-601` (`7417`); fresh live capture `seq 7` published | Pilot A reacquired `QNQ-601` (`reacquisition_count=1`, `reacquired_after_withdrawal=true`, `last_sequence=7`) |
| `3b_hull_playback` | Registered hull `DHQ-917` (`index=619`, `actor_key=592054`, `sector=929`, `zone=936`) driven along `QNQ-601`'s retained velocity direction clamped to `20.0 m/s` (`(15.3956, -11.9997, -4.3571) m/s`) | `SetObjectSectorPos` applied `step_dist=8.8069 m` with **`err_m=0.0005 m`**; `118.7705 m > 50.0 m` probe cleanly **refused** (`refused_over_50m`) |
| `3c_later_readback` | Independent MD+FFI `Readback` on `DHQ-917` (`index=619`) `+1.3226 s` later (`gt 5.7516 -> 7.0742`) | Same operational actor (`592054`/`DHQ-917`, `sector=929`, `zone=936`); measured displacement **26.452 m** vs expected **26.452 m** (`0.000 m` error at 1 mm precision) |
| `4_pause_save_resume` | `service.set_running(False)` -> `set_running(True)` (`world_resets=2` on both pilots) | Both Pilot A & Pilot B stayed admitted, auto-re-synced views, and confirmed fresh post-resume moving captures (`seq=9`) on `QNQ-601` & `KSE-003` (`18` total NPC samples accepted, `14` with velocity) |
| `5_native_ffi_capture` | `profile_capture` inside live X4 9.00 (`N=1, 5, 16, 32` enrolled ships × 20 iters = up to 640 FFI reads) | All batches completed inside a single frame (`getElapsedTime()` delta `0.0000 ms`, `< 16.6 ms`; follow-up will switch timer to `GetCurRealTime()` for sub-ms resolution) |

## 2. Offline & loopback test commands and results

| Scope | Exact Windows command (`.venv`, Python 3.12.10) | Result |
| --- | --- | --- |
| Offline NPC harness checks (MD XML, LuaJIT syntax/opt-in, compose, velocity) | `.venv\Scripts\python.exe -B -m unittest -v tests.test_authority_npc_motion_windows` | **5/5 PASS** |
| Authoritative NPC loopback + company motion regression (`checkpoints._sync_dir` stubbed on Windows only) | `.venv\Scripts\python.exe -B -c "import os, sys, unittest; sys.path[:0]=['src','tests']; import authority.checkpoints as cp; cp._sync_dir = (lambda p: None) if os.name=='nt' else cp._sync_dir; names=['tests.test_authority_npc_motion_service','tests.test_authority_company_motion_service','tests.test_authority_company_motion_reconnect','tests.test_authority_company_motion_tls']; suite=unittest.defaultTestLoader.loadTestsFromNames(names); res=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(0 if res.wasSuccessful() else 1)"` | **37/37 PASS** (`npc_motion_service` 4, `company_motion_service` 16, `reconnect` 4, `tls` 13) |

## 3. Scalability profile (10,082-entry index space, synthetic loopback load)

Command: `.venv\Scripts\python.exe -B -c "import json, sys; sys.path[:0]=['src','tools/dev']; import authority_npc_motion_windows as m; print(json.dumps(m.benchmark_10082_scalability(counts=(1, 16, 64, 256), rounds=20), indent=2))"`

| Registry size | Visible tracks (`N`, 2 sectors) | Rounds | Snapshots delivered | `ms / round` (loopback) | Effective Hz (all `N` tracks) | Snapshots / sec | Est. payload (bytes/s) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10,082 | 1 | 20 | 20 | 0.104 | 9,644.6 Hz | 9,644.6 | 3.32 MB/s |
| 10,082 | 16 | 20 | 320 | 1.368 | 731.1 Hz | 11,697.7 | 4.06 MB/s |
| 10,082 | 64 | 20 | 1,280 | 5.476 | 182.6 Hz | 11,686.6 | 4.06 MB/s |
| 10,082 | 256 | 20 | 5,120 | 23.113 | 43.27 Hz | 11,076.1 | 3.84 MB/s |

### First measured bottleneck (decision needed before changing wire protocol)

- **Python / loopback CPU scaling is linear (`~0.09 ms / snapshot`, `~344 B / snapshot`)** and easily sustains 256 visible ships at >43 Hz locally (and `subscribed_npc_targets()` stays strictly O(visible), unaffected by the 10,082 total registry size).
- **First WAN / Internet bottleneck is the 1-in-flight `peer.awaiting_ack` serialization in `CompanyMotionService._write()`:** because only one `motion_snapshot` can be in flight per peer across ALL tracks at a time, a 40 ms Internet RTT caps a single observer to **25 snapshots/s total** — i.e., **1.56 Hz per ship for 16 visible NPCs** and **0.39 Hz for 64**, well below smooth 10 Hz movement once >2–3 NPCs are visible.
- **Proposed architecture decision for Lith7ium & Astra (held for approval per `NEON-NPC-SHIP-MOTION.md`):** allow either a bounded per-peer **multi-ship batch frame** (e.g., up to 16–32 samples per RTT) or a bounded **sliding window** on the existing authenticated TLS movement stream so 16–64 visible NPCs stay at 10 Hz over real Internet RTT.

## Local artifacts (kept in `.local/`, not committed)

| Artifact | SHA-256 |
| --- | --- |
| `.local/neon-npc-motion-win-01/result.json` | `7cecfd27d3025a83d6988818d1b3ad8b52bebc19338a86120f75f7cf8d3cb2a5` |
| `.local/neon-npc-motion-win-01/neon-npc-motion.log` | `db7d0e0ab055d2dc88191ffed610f3ae25944539c73eba51acefa3e250f00ec6` |
