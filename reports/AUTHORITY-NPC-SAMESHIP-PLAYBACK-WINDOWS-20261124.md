# Native capture-to-same-ship NPC playback through an admitted Windows client (24 Nov 2026)

Author: Neon (James Molander / `von-Benette`).
Branch: `work/neon-npc-ship-motion` (continuing from Milestone 1 `74e0ed3b`, preserving `work/neon-npc-reacquisition` @ `8416b817` intact).
Host: Windows 11 PC (separate machine from Cobalt and Lith7ium), Python 3.12.10 (`.venv`), AMD Radeon RX 9070 XT, X4 9.00 pinned executable (`SHA-256 19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`).

## Summary of Milestone 2 (per Astra's 19:00 Berlin directive)

Milestone 1 (`74e0ed3b`) captured live AI-flying NPCs through `CompanyMotionService` but played back a different ship with a clamped single-shot velocity. Milestone 2 closes that gap end-to-end on **the same registered NPC ship** across a real cold reload, admitted movement client, local UDP bridge, and continuous frame-by-frame playback controller:

1. **Opt-in NPC hull playback controller (`src/authority/lua/npc_hull_playback.lua`):**
   - Default-off (`enabled == true` required), zero wire APIs, reuses the repository's existing `X4LAN_MOTION_BUFFER` (`motion_buffer.lua`) and `X4LAN_MOTION_PLAYBACK` (`motion_playback.lua`) with Lith7ium's **5.0 s** connection-loss prediction window, **50 m** active step limit, arrival-time extrapolation clamp, and delayed later-update readbacks (`elapsed > 0`, `<= 3 m` tolerance).
   - Enforces explicit withdrawal (`withdrawn` stops playback and discards offscreen samples), transport loss (`transport_lost` keeps the 5.0 s local prediction window running, then freezes cleanly at 5.0 s with zero further writes), active `> 50 m` refusal, and controlled post-loss/post-withdrawal reacquisition (direct ordinary resumption when `<= 50 m`, or strictly **at most one authorized alignment** per loss/withdrawal episode verified by an independent later readback before resuming ordinary `<= 50 m` continuous playback). Also includes a multi-NPC pool (`M.new_pool`) and keeps all player-ship playback files (`company_hull_playback*.lua`, `company_hull_trial.lua`) untouched.
2. **Authenticated local UDP bridge (`NpcLocalPlaybackBridge` in `src/authority/npc_motion_observer.py`):**
   - Mirrors `CompanyMotionObserver`'s loopback lease/epoch prefix and peer-ownership validation discipline (verified against the spawned X4 child PID via Windows `GetExtendedUdpTable`), carrying `VIEW`, `REMOTE`, `LOSS`, `END`, `SEEN`, `READBACK`, and `EVENT` between the admitted `CompanyMotionClient` + `NpcMotionObserver` and native X4 Lua (`x4lan_net_64.dll`).
3. **Two-phase native Windows X4 9.00 verification (`tools/dev/authority_npc_motion.{xml,lua,py}`):**
   - **Phase 1 (Authoritative Capture X4):** Enrolls the live universe into `X4AuthorityShipRegistry` (`Initialize` + `PopulateUniverse`), saves disposable checkpoint `save_009.xml.gz` in disposable profile `45850`, and captures ~16 s of genuine live AI flight at ~10 Hz on Primary & Secondary NPCs.
   - **Phase 2 (Observing Windows Client X4, fresh process):** Cold-reloads `save_009.xml.gz`, resolves the **exact same registered NPC** directly from the saved registry (zero re-population), connects via authenticated local UDP to Pilot A's admitted `CompanyMotionClient` (`trial_index`, `on_transport_lost`) + `NpcMotionObserver` + concurrent Pilot B on `CompanyMotionService`, and drives **that same NPC hull** through live playback, 5 s loss cutoff, offscreen withdrawal, `> 50 m` guard refusal, single authorized alignment + later readback, resumed ordinary `<= 50 m` playback, independent final MD+FFI readback, and `pause`/`save`/`resume`.

## Honest separation of evidence

- **Native Windows X4 9.00 (`neon-npc-sameship-win-04`):** Two separate real native Windows X4 9.00 processes (disposable profile `45850`, isolated `x4ls` view, `800x600`, `fast=True`, `HWND_BOTTOM`), real 10,100-ship saved `X4AuthorityShipRegistry`, real cold reload of `save_009.xml.gz`, real captured AI flight of **`LGE-861` (`index=4554`)** played back onto **`LGE-861` (`index=4554`) itself** through real loopback TCP `CompanyMotionService` -> admitted `CompanyMotionClient` -> `NpcMotionObserver` -> real Windows UDP (`x4lan_net_64.dll`, PID-validated) -> `X4LAN_NPC_HULL_PLAYBACK` (`motion_buffer.lua` + `motion_playback.lua`). Local loopback server/clients on one Windows host (no second physical host or WAN RTT yet; synthetic pilot seat IDs).
- **Offline LuaJIT 2.1 unit suite (`tests/test_authority_npc_hull_playback_lua.py` + `tests/test_authority_npc_motion_windows.py`):** Real LuaJIT 2.1 (`lupa.luajit21`) executing `motion_buffer.lua`, `motion_playback.lua`, `npc_hull_playback.lua`, and harness checks with synthetic clocks and callbacks.
- **Loopback TCP + UDP concurrency suite (`tests/test_authority_npc_motion_service.py` + company motion regressions):** Real loopback TCP & UDP sockets with synthetic NPC sample feeds.

## 1. Real native Windows X4 9.00 run (`neon-npc-sameship-win-04`)

### Command

```powershell
.venv\Scripts\python.exe -B tools\dev\authority_npc_motion_windows.py --out .local\neon-npc-sameship-win-04
```

### Setup, cold reload, and safety checks

- **Safety & cleanup:** exit code `0`, `outcome="done"`, `accepted=true`, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, **0 MD/Lua errors** in both `neon-npc-phase1-capture.log` and `neon-npc-phase2-observe.log`.
- **Phase 1 universe & capture:** **10,100** real operational ships enrolled in `92.55 ms`; saved `save_009.xml.gz` in disposable profile `45850`; captured **152** live AI-flight samples (~10 Hz) for:
  - **Primary (same-ship capture -> playback target):** `LGE-861` (`index=4554`, `cluster_25_sector002_macro`, scout speed **20.03 m/s**, saved pose `(162387.4688, -5236.0654, 162668.4688)`).
  - **Secondary (concurrent map track):** `AGW-335` (`index=1`, `cluster_409_sector001_macro`, scout speed **228.30 m/s**).
- **Phase 2 cold reload & peer validation:** Fresh X4 process (`PID 17332`) loaded `save_009.xml.gz`, resolved **the exact same `LGE-861` (`index=4554`, `cluster_25_sector002_macro`)** and **`AGW-335` (`index=1`)** from the saved **10,100-entry** `X4AuthorityShipRegistry` with **`cold_reload_offset_m = 0.0005 m` (0.5 mm)** against Phase 1's saved pose. Windows `GetExtendedUdpTable` verified X4's loopback UDP peer (`port 49346`, `PID 17332`).

### Stage-by-stage native same-ship evidence

| Stage | Real native Windows X4 9.00 + admitted client observation | Result |
| --- | --- | --- |
| `0_cold_reload` | Fresh X4 loads `save_009.xml.gz` and runs `ResolveSavedShip` on saved `X4AuthorityShipRegistry` | `10,100` ships persisted; `LGE-861` (`4554`) resolved in `cluster_25_sector002_macro` within **`0.0005 m`** of Phase 1 save |
| `A_capture_to_same_ship` | Admitted Pilot A streams real captured `LGE-861` `seq 1..26` over TCP + UDP into `X4LAN_NPC_HULL_PLAYBACK` on **`LGE-861`** | **`496` native writes**, **`495` delayed later-update readbacks** (`elapsed > 0`); `LGE-861` moved **`115.012 m`** along its real captured path; `player_disp_m = 0.0000 m` |
| `B_sample_loss_5s_cutoff` | Authority stops publishing `LGE-861`; `MAX_SAMPLE_AGE` (3.0 s) triggers real `motion_hidden` -> `on_transport_lost` -> UDP `LOSS` | Smoothly extrapolated **`153.592 m`** between `1.078 s` (`614` writes) and `4.250 s` (`881` writes), stopped at **5.0 s** (`stale_cutoffs=1`, `914` writes), then **`0` extra writes** and **`0.0000 m` drift** between `5.453 s` and `6.250 s` while Pilot A's `AGW-335` (`map`) continued |
| `C1_offscreen_withdrawal` | X4 clears `high_attention` (`withdrawal_count=1`); authority publishes `LGE-861` `seq 27..38` while offscreen | **0 deliveries** of `LGE-861` to Pilot A while withdrawn (held at `seq 26`); Pilot B received `seq 27..38` and Pilot A's `AGW-335` continued uninterrupted |
| `C2_reacquisition_and_guard` | X4 re-subscribes `high_attention` (`reacquisition_count=1`); `LGE-861` is **`118.563 m` (`> 50 m`)** ahead of its frozen 5 s hull pose | **1 authorized alignment** at `seq 38` confirmed by later `alignment_readback` (`elapsed=0.02689 s`, **`error=0.00000016 m`**); subsequent **`120.0 m > 50.0 m`** probe **refused** (`refusals_50m=1`, `correction_out_of_bounds`); **ordinary `<= 50 m` playback resumed across `112` real captured samples (`seq 39..150`)** |
| `D_independent_readback_and_resume` | Independent later MD `GuardShip` + FFI `Readback` (`+0.25 s` later update) + `pause`/`save`/`resume` (`world_resets=2`) | Same operational ship `LGE-861` (`4554`, `actor_key=243144`, `zone_key=241550`) at **`(161448.4531, -5065.8799, 163232.6875)`** matching captured `seq 150` `(161448.453125, -5065.879883, 163232.6875)` to **`0.0000 m`** (total displacement **`1,108.628 m`**, **`1,994` writes / `1,994` readbacks**, max UDP readback error **`0.01084 m`**, min elapsed **`0.00478 s`**, `player_disp_m = 0.0000 m`); post-resume `seq 152` confirmed (`error=0.00000006 m`) |

### Five-second sample-loss probe table (`LGE-861`, `index=4554`, `cluster_25_sector002_macro`)

| Seconds since last sample | Controller state | Actual `LGE-861` sector Z (m) | Cumulative X4 writes / readbacks | Player displacement (m) |
| ---: | --- | ---: | ---: | ---: |
| 1.078 | `active` (extrapolating) | 162832.0469 | 614 / 613 | 0.0000 |
| 4.250 | `active` (after `motion_hidden`) | 162985.3906 | 881 / 880 | 0.0000 |
| 5.453 | `stale` (post-5.0 s cutoff) | 163006.6563 | 914 / 914 | 0.0000 |
| 6.250 | `stale` (frozen hold) | 163006.6563 | 914 / 914 | 0.0000 |

### Native FFI capture profiling note (`profile_capture`)

- Inside X4's LuaJIT sandbox, `ffi.load('kernel32')` fell back to `GetCurRealTime()`, which is frame-quantized within a single `onUpdate` callback (while advancing with sub-ms precision across frames, e.g. `4.78–16.70 ms`). All five native FFI batches (`N = 1, 5, 16, 32, 48` enrolled ships × `40` iterations = up to **1,920 FFI reads**) completed within a single frame (`0.0000 ms` intra-frame delta). True intra-frame microsecond profiling requires exposing a monotonic clock helper in the bundled Windows DLL (`x4lan_net_64.dll` currently exports `open`/`send`/`receive` without `.monotonic`).

## 2. Offline LuaJIT & loopback test verification (exact final code)

| Scope | Exact Windows command (`.venv`, Python 3.12.10) | Result |
| --- | --- | --- |
| Offline LuaJIT 2.1 NPC playback controller + MD/Lua harness checks | `.venv\Scripts\python.exe -B -m unittest -v tests.test_authority_npc_hull_playback_lua tests.test_authority_npc_motion_windows` | **8/8 PASS** |
| Real loopback TCP + UDP NPC & company motion suites (`checkpoints._sync_dir` stubbed on Windows only) | `.venv\Scripts\python.exe -B -c "import os, sys, unittest; sys.path[:0]=['src','tests']; import authority.checkpoints as cp; cp._sync_dir = (lambda p: None) if os.name=='nt' else cp._sync_dir; names=['tests.test_authority_npc_motion_service','tests.test_authority_company_motion_service','tests.test_authority_company_motion_reconnect','tests.test_authority_company_motion_tls']; suite=unittest.defaultTestLoader.loadTestsFromNames(names); res=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(0 if res.wasSuccessful() else 1)"` | **38/38 PASS** (`npc_motion_service` 5, `company_motion_service` 16, `reconnect` 4, `tls` 13) |

## 3. Open decisions & next milestone

- **Still awaiting Lith7ium/Astra decision (escalated in Milestone 1):** `CompanyMotionService._write()` 1-in-flight `peer.awaiting_ack` serialization across all tracks caps WAN throughput at `1 / RTT` snapshots/s (e.g. `25 snaps/s` at 40 ms RTT = `1.56 Hz` for 16 NPCs). Holding wire-protocol changes pending approval of bounded multi-ship batching or sliding-window acks.
- **Note on `x4lan_net_64.dll`:** The repository copy at `src/lockstep/base/native/x4lan_net_64.dll` exports `open`/`send`/`receive` without `.monotonic` (unlike Astra's newer Linux `.so` and matched Windows ZIP DLL). Our harness falls back cleanly to `GetCurRealTime()`; matching the Windows DLL with `.monotonic` will also give direct intra-frame microsecond profiling.
- **Next independent milestone (ready once compared at 20:00 check-in):** Multi-NPC simultaneous local playback in native Windows X4 (`M.new_pool`, 2–8 visible NPCs concurrently across high-attention + map views, plus unseen sector-change recovery and wiring into the ordinary Windows client package path ready for a coordinated two-machine run with Astra).

## Local artifacts (kept in `.local/`, not committed)

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `.local/neon-npc-sameship-win-04/result.json` | 19,544 | `73650204617789adbe436e28278f26ba2bf9e0314b04fef3d0e487e5453b2bae` |
| `.local/neon-npc-sameship-win-04/captured-trace.json` | 81,447 | `99d2094394a75e6f5c2a2994e981feaef04d0d05d7be0731377db24e05a2ed21` |
| `.local/neon-npc-sameship-win-04/neon-npc-phase1-capture.log` | 105,065 | `2106fd493bace5f225b775dda70fa741b8c92b1d12b2f952f3b72370718154e2` |
| `.local/neon-npc-sameship-win-04/neon-npc-phase2-observe.log` | 7,136 | `ed7657fa3399c0b187c4a9ca4c73604c4f76c8b6827e55dad07569beed952b39` |
