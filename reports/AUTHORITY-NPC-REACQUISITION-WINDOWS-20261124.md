# Saved NPC sector-change reacquisition: Windows verification (24 Nov 2026)

Author: Neon (James Molander / `von-Benette`).
Branch: `work/neon-npc-reacquisition` (base `519cd0f1`, initial port `05e68bf7`).
Host: Windows 11 PC (separate machine from Cobalt and Lith7ium), Python 3.12.10 (`.venv`), AMD Radeon RX 9070 XT (Vulkan 1.4.359), X4 9.00 pinned executable (`SHA-256 19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`).

## Scope and honest separation of evidence

Two distinct levels of evidence are recorded on this branch:

1. **Offline / loopback tests (synthetic callbacks and loopback TCP/UDP):** verify the ported `allow_sector_reentry=True` transport, unsolicited prepare-receipt peer isolation, opt-in MD/Lua harness structure, and the `ENH-870` (`index=1318`, `epoch=4ba7be438ebb04ec5bb85fdb95b5a488`, `cluster_06_sector001_macro` -> `cluster_14_sector001_macro`) cancel-and-realign loopback scenario.
2. **Real native Windows X4 9.00 run (`custom_creative`, disposable profile `45850`):** wires the ported `ExperimentalLocalHost`, `ExperimentalTCPControl`, `LatestMotionPoll`, `ExperimentalTCPMotion(allow_sector_reentry=True)`, `ExperimentalSourceMotionIPC(latest=True)`, and two real `ExperimentalTCPParticipantClient` peers against a live X4 9.00 process running the opt-in `X4AuthorityNpcReacquisition` MD/Lua callback on a freshly enrolled operational NPC (`YDA-317`, registry `index=1`, `actor_key=690353`).

**Private checkpoint caveat:** Lith7ium's private recovered checkpoint containing `ENH-870` (`index=1318`) is not present on this Windows PC. `ENH-870` (`1318`) was exercised in loopback tests only; native X4 9.00 verified the same reentry transport and opt-in MD/Lua callback contract against a freshly enrolled `custom_creative` NPC (`YDA-317`).

## 1. Offline and loopback Windows test commands and results

All commands ran from repository root using `.venv\Scripts\python.exe` (Python 3.12.10 with `lupa.luajit21`):

| Scope | Exact Windows command | Result | Notes |
| --- | --- | --- | --- |
| Reentry transport + session guard + X4 harness offline checks (unshimmed) | `.venv\Scripts\python.exe -B -m unittest -v tests.test_authority_tcp_client_motion_reentry tests.test_authority_npc_reacquisition_windows tests.test_authority_observed_client_session.ObservedSessionTest.test_reentry_alignment_is_guarded_through_the_observed_session` | **19/19 PASS** | Native Windows, zero shims (9 reentry client + 9 harness offline + 1 observed-session guard) |
| Windows launcher path checks (unshimmed) | `.venv\Scripts\python.exe -B tools\dev\check_windows_launcher_paths.py` | **4/4 PASS** | Disposable profile / view path guards |
| Related motion / company / fixture unit tests (unshimmed) | `.venv\Scripts\python.exe -B -c "import sys, unittest; sys.path[:0]=['src','tests']; suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.test_authority_client_motion_overlay','tests.test_authority_participant_motion','tests.test_authority_windows_fixture_session','tests.test_authority_company_ships','tests.test_authority_company_motion_service','tests.test_authority_company_motion_reconnect','tests.test_authority_company_motion_tls']); res=unittest.TextTestRunner(verbosity=1).run(suite); raise SystemExit(0 if res.wasSuccessful() else 1)"` | **43/43 PASS** | Preserved all `519cd0f1` additions intact |
| Two-sector + `ENH-870` (`1318`) loopback + latest-motion suites (Windows test-runner stub on POSIX `checkpoints._sync_dir`) | `.venv\Scripts\python.exe -B -c "import os, sys, unittest; sys.path[:0]=['src','tests']; import authority.checkpoints as cp; cp._sync_dir = (lambda p: None) if os.name=='nt' else cp._sync_dir; suite = unittest.defaultTestLoader.loadTestsFromNames(['tests.test_authority_source_latest_two_sectors','tests.test_authority_latest_motion_poll','tests.test_authority_experimental_tcp_participant_client','tests.test_authority_experimental_tcp_motion']); res = unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(0 if res.wasSuccessful() else 1)"` | **64/64 PASS** | `source_latest_two_sectors` (8/8 incl. `EnhancedNpc870ReacquisitionLoopback` & `SourceLatestReentryLoopback`), `latest_motion_poll` (18/18 incl. `test_unsolicited_duplicate_prepare_receipt_drops_only_that_peer`), `participant_client` (8/8), `experimental_tcp_motion` (30/30). `src/authority/checkpoints.py` left untouched (POSIX `O_RDONLY` directory `fsync` raises `PermissionError` on Windows). |

## 2. Native Windows X4 9.00 run

### Command

With X4 closed and Steam registered, using disposable profile `45850` and an isolated `x4ls` view (`R:\x4ls-lanes\neon-reacq-neon-npc-reacq-win-03`):

```powershell
.venv\Scripts\python.exe -B tools\dev\authority_npc_reacquisition_windows.py --out .local\neon-npc-reacq-win-03
```

### Run setup and enrolled NPC

- **Gamestart / window:** `custom_creative`, `800x600`, `fast=True` (muted / lowest graphics), windowed, `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`.
- **Enrolled NPC:** `index=1`, `idcode=YDA-317`, `ship_macro=ship_arg_s_heavyfighter_02_a_macro`, `owner=argon`, `actor_key=690353`, `epoch=4ba7be438ebb04ec5bb85fdb95b5a488`.
- **Sector transition:** `cluster_14_sector001_macro` (`sector_key=2630`, `zone_key=2632`) -> `cluster_01_sector001_macro` (`sector_key=1902`, destination `zone_key=1530716`).
- **Outcome:** exit code `0`, `outcome="done"`, `accepted=true`, `peer_errors=[]`, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, `neon-npc-reacq.log` contained zero MD/Lua errors.

### Evidence table (`neon-npc-reacq-win-03`)

| Step | Game time (s) | X4 callback status | Registry / actor | Sector (`sector_key` / `zone_key`) | Position `(x, y, z)` m | Transport & verification result |
| --- | ---: | --- | --- | --- | --- | --- |
| `1_initial` | 2.4602 | `bound` | `1` / `690353` (`YDA-317`) | `cluster_14_sector001_macro` (`2630` / `2632`) | `(20984.06, -4408.62, 15884.16)` | Peer B frame bound to `cluster_14_sector001_macro` |
| `2_unseen_sector_change` | — | *(not sent to Peer B)* | — | *(Peer B excluded)* | — | Unseen sector change dropped for Peer B (frame unchanged); Observer A received seq `1` |
| `3_prepare` | 2.5762 | `prepared` | `1` / `690353` | `cluster_14_sector001_macro` (`2630` / `2632`) | `(21000.93, -4405.71, 15885.87)` | Resolved `target_sector_key=1902` with **zero movement**; `real_velocity` stripped (`None`) |
| `4_cancel_on_view_loss` | 2.6861 | `canceled` | `1` / `690353` | `cluster_14_sector001_macro` (`2630` / `2632`) | `(21016.90, -4402.94, 15887.48)` | View lost during prepare -> clean `cancel`, **zero movement**, no disconnect; Observer A received seq `2` |
| `5a_reprepare` | 2.7936 | `prepared` | `1` / `690353` | `cluster_14_sector001_macro` (`2630` / `2632`) | `(21032.54, -4400.24, 15889.06)` | Fresh `prepare` re-confirmed `target_sector_key=1902` |
| `5b_fresh_align` | 2.9205 | `aligned` (`settle_polls=1`) | `1` / `690353` | `cluster_01_sector001_macro` (`1902` / `1530716`) | `(15000.17, 2500.00, -4000.00)` | Post-prepare PULL seq `6` warped same actor to target sector + `yaw=0.6098 rad` (~34.94 deg vs 35 deg) + `constantvel=(20, 0, 0)` |
| `6_later_x4_readback` | 4.5087 | `readback` (`+1.5882 s`) | `1` / `690353` (`YDA-317`) | `cluster_01_sector001_macro` (`1902` / `1530716`) | `(15031.94, 2500.00, -4000.00)` | Same operational actor in target sector/zone; displacement `31.766 m` vs `31.764 m` expected at `20 m/s` |
| `7a_resumed_snapshot_within_50m` | 4.6255 | `step_applied` | `1` / `690353` | `cluster_01_sector001_macro` (`1902` / `1530716`) | `(15043.94, 2500.00, -4000.00)` | Resumed normal `motion_snapshot` seq `7` (`step_dist=9.6641 m <= 50 m`) applied via `SetObjectSectorPos` (`err_m=0.0000`) |
| `7b_50m_limit_check` | 4.7260 | `refused_over_50m` | `1` / `690353` | `cluster_01_sector001_macro` (`1902` / `1530716`) | `(15045.95, 2500.00, -4000.00)` | `117.9902 m > 50.0 m` correction refused cleanly; ship kept smooth `20 m/s` motion; Observer A received `[1, 2, 3]` |

### Implementation notes from native X4 9.00 runs

- **Cross-sector `<warp>` settle (discovered in `win-02`, resolved in `win-03`):** On the immediate MD cue line following `<warp object="$ship" sector="$target">`, `$ship.sector` still reported the origin sector (`cluster_14_sector001_macro`). Emitting `AlignDispatched` and polling correlated `Readback` + FFI `GetContextByClass` confirmed the destination sector (`1902`) and newly assigned destination zone (`1530716`) on the first poll (`settle_polls=1`).
- **Low-footprint Windows window policy (measured in `neon-env-probe-01`):** At `800x600` with `fast=True` (`SW_SHOWNOACTIVATE` + `HWND_BOTTOM`), X4 9.00 ran unfocused at **60 FPS** using **2,246.8 MB (~2.19 GiB) dedicated VRAM** and **5,835.7 MB working set**. Minimizing (`SW_SHOWMINNOACTIVE`, ~9.5 s) stalled `onUpdate` completely (0 ticks; `game_time` froze until restored), so unfocused `HWND_BOTTOM` windowed mode is required to keep X4 ticking.

## Local artifacts (kept in `.local/`, not committed)

| Artifact | SHA-256 |
| --- | --- |
| `.local/neon-npc-reacq-win-03/result.json` | `7425ccaf49c69296250c2e25ea85d7c4512fc25f9788c8c89de849ec71bbed68` |
| `.local/neon-npc-reacq-win-03/neon-npc-reacq.log` | `2af24477102b54bcc396669093b61b0a12de38defadb060ab89674271db3e144` |
| `.local/neon-env-probe-01/result.json` | `941044d386d5fa7270e52804851b275bb8d68af650ac06b461f1bd57d5383b1e` |
