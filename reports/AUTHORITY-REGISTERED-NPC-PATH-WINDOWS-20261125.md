# Opt-In Registered-NPC End-to-End Path on Native Windows X4 9.00 (25 Nov 2026)

- **Author:** Neon (James Molander / `von-Benette`)
- **Branch:** `work/neon-registered-npc-path` (base `test/base-game-two-pilots-20261124` @ `4e587712`)
- **Host / GPU:** Windows 11 PC (separate machine from Cobalt and Lith7ium), Python `3.12.10` (`.venv`), `'AMD Radeon RX 9070 XT' AMD v2.0.416 (Vulkan 1.4.359) check=1`, X4 `9.00` pinned `X4.exe` (`SHA-256 19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`).
- **Scope (per Astra 16:00 CET check-in):** Own an opt-in registered-NPC path end to end on Neon's branch — sample one live authority NPC, deliver it only with verified cockpit radar or map interest, show native receiver displacement and withdrawal/reacquisition, preserve the player-ship gate, and verify a nearby unrelated ship stays untouched in one live session while labeling all remaining fixture links.

---

## 1. Opt-In Registered-NPC Modules (Default-Off; Player-Ship Gate Preserved)

All existing Company player-ship modules (`company_hull_playback_x4.lua`, `company_cockpit_observer.{lua,xml}`, `company_cockpit_playback.lua`, `company_hull_playback.lua`, `company_observer_stream.lua`, `motion_buffer.lua`, `motion_playback.lua`, `company_motion_service.py`, `company_motion_client.py`, `company_motion_observer.py`, `company_motion_source.py`) remain **100% unmodified**. Four new default-off opt-in modules implement the registered-NPC path:

1. **`src/authority/npc_motion_source.py` (`NpcMotionSource`, `enabled=True` required):**
   - Authority-side loopback UDP receiver (`N|<lease>|<generation>|SAMPLE3|...` + `GAP`) validated via `WindowsMotionPeer` (or platform peer validator), checking V3 monotonicity via `check_motion_progress` and feeding standard V3 `motion_value` samples directly into unmodified `CompanyMotionClient` → `CompanyMotionService`.
2. **`src/authority/md/npc_cockpit_observer.xml` + `src/authority/lua/npc_cockpit_observer.lua` (`X4LAN_NPC_COCKPIT_OBSERVER`, `enabled=True` required):**
   - Read-only registered-NPC interest observer strictly complementing `company_cockpit_observer`: accepts **only** operational undocked non-player AI-piloted ships (`owner ~= 'player'` and `pilot/assignedpilot/assignedaipilot` present) enrolled in `X4AuthorityShipRegistry`.
   - **`mode='radar'`:** enforces same sector, `$cand.attention ge attention.visible`, `$cand.isphysicsready`, and **`$cand.distanceto.{player.controlled} le player.controlled.currentradarrange`** (re-verified in Lua via `GetDistanceBetween(cockpit, actor) <= maxradarrange`).
   - **`mode='map'`:** enforces verified map-open interest (`MapMenu.shown` or caller predicate).
   - Withdrawal immediately clears observation so unmodified `X4LAN_COMPANY_OBSERVER_STREAM` sends `VIEW|...|epoch|-|0|-|-` and `CompanyMotionObserver` clears the view on `CompanyMotionService`.
3. **`src/authority/lua/npc_hull_playback_x4.lua` (`X4LAN_NPC_HULL_PLAYBACK_X4`, `enabled=True` required):**
   - Native X4 sector-space pose adapter strictly mutually exclusive with `company_hull_playback_x4.lua` (`owner ~= 'player'` and pilot present), enforcing `<= 50 m` ordinary writes while preserving native attitude in degrees (`SetObjectSectorPos`) and exposing guarded `session:align(target)` for strictly one authorized post-withdrawal/post-loss alignment via unmodified `X4LAN_COMPANY_COCKPIT_PLAYBACK`.

---

## 2. Offline Verification (`44 / 44` Passing, Zero Skips)

Command: `.venv\Scripts\python.exe -m unittest -v tests/test_authority_registered_npc_path.py tests/test_authority_company_cockpit_playback.py tests/test_authority_company_motion_service.py`

- **`tests/test_authority_registered_npc_path.py` (`5/5` pass, real LuaJIT 2.1 + real loopback TCP/UDP):**
  - Default-off refusal on `NpcMotionSource`, `npc_cockpit_observer.lua`, and `npc_hull_playback_x4.lua`.
  - Mutual exclusion & player-ship gate preservation (`company_hull_playback_x4` accepts uncrewed player ship and refuses non-player AI NPC; `npc_hull_playback_x4` accepts non-player AI NPC and refuses player-owned/uncrewed ship).
  - Verified radar-range & map-open interest gating (`out_of_radar` refused, `map_closed` refused).
  - Full LuaJIT 2.1 playback lifecycle via unmodified `company_observer_stream.lua` + `company_cockpit_playback.lua` + `company_hull_playback.lua` + `motion_buffer.lua` + `motion_playback.lua`: pre-interest `0` writes → radar-interest displacement → withdrawal (`0` writes) → `>50 m` reacquisition (`1` authorized `alignment_readback`) → resumed `<=50 m` velocity playback.
  - Real loopback TCP + UDP integration (`NpcMotionSource` → `CompanyMotionClient` Grant 1 → `CompanyMotionService` → `CompanyMotionClient` Grant 2 → `CompanyMotionObserver`) proving pre-interest `0` delivery, radar delivery + exact velocity `SEEN`, withdrawal `0` delivery while authority keeps streaming, reacquisition, independent `map` slot, and unrelated ship isolation.
- **Unmodified Company playback & service regressions (`39/39` pass):** `tests/test_authority_company_cockpit_playback.py` (`12/12`) and `tests/test_authority_company_motion_service.py` (`27/27`).

---

## 3. Live Single-Session Native Windows 11 X4 9.00 Trial (`neon-registered-npc-live-win-01`)

### Environment, Loaded Packaged DLL & Dual `WindowsMotionPeer` UDP Validation

- **Live `X4.exe` (`PID 5484`), disposable profile `45850`, `800×600` windowed `fast=True`, `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`, `10,046` enrolled ships in `X4AuthorityShipRegistry` (`cluster_14_sector001_macro`, `radar_range=40,000.0 m`).**
- **OS-verified loaded DLLs (`TH32CS_SNAPMODULE`, `PID 5484`):**
  - `x4lan_net_64.dll` (`169,984` B, **SHA-256 `6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0`**, base `0x7ff80b560000`) loaded via unmodified `local_udp_module.lua`.
  - `lua51_64.dll` (`973,312` B, SHA-256 `e0712ac93f0d7f3a28236d3153b82e85d35e6bcf8e606af250f664580eaf3b63`, base `0x7fffdb900000`).
  - Live `udp.monotonic()` QPC check: `8248.847087200 → 8248.847087300` (`+100 ns`), bracketed by Python `time.perf_counter()` (`8184.2209048 < mono < 8248.8967258`).
- **Two independent Win32 `GetExtendedUdpTable` (`WindowsMotionPeer`) bindings on `PID 5484`:**
  - Live authority NPC sampler (`NpcMotionSource` UDP #1): unique row `local_ip=0.0.0.0`, `local_port=63537`, `pid=5484` (`first_local_npc_datagram_matched` → `first_local_npc_peer_verified` → `first_local_npc_sample_validated` in `0.77 ms`).
  - Observing Windows client (`CompanyMotionSource` UDP #2): unique row `local_ip=0.0.0.0`, `local_port=63538`, `pid=5484` (`first_local_motion_datagram_matched` → `first_local_motion_peer_verified` in `0.29 ms`).

### Registered Ships in the Live Session

| Role | Registry Index & Code | Owner / Macro / Actor | Distance from Own Ship | Within Cockpit Radar (`40,000 m`) | Notes |
| --- | --- | --- | ---: | --- | --- |
| **Live Authority Source NPC `S`** | `7392` (`FVH-379`) | `argon` / `ship_arg_s_fighter_02_a_macro` / `0x8e076` | In sector (`cluster_14_sector001_macro`) | Natural live AI flight | Zero pre-recording; flew live from `27.686 m/s` up to `383.921 m/s` (`146` live samples, `0` gaps, `0` rejected) |
| **In-Radar Registered Receiving NPC `R`** | `7385` (`KKJ-450`) | `argon` (AI-piloted) / `ship_arg_m_trans_container_02_a_macro` / `0x17696d` | **`2,121.500 m`** (initial) | **`true`** | Enrolled in `X4AuthorityShipRegistry`; initial `(1500, 0, 1500)` |
| **Nearby Unrelated Visible Ship `U`** | `7480` (`MBT-139`) | `player` (uncrewed) / `ship_arg_s_fighter_03_a_macro` / `0x176925` | **`649.875 m`** | **`true`** (`onscreen=true`) | Enrolled; never subscribed, never targeted, **`0.000000 m`** displacement |
| **Local Own Ship** | `7479` (`SGA-086`) | `player` (controlled) | `0.000 m` | — | **`0.000000 m`** displacement |

### Stage-by-Stage Live Results (`generation=bb38103d7bb3161a75ef04a2d0902b82`, `epoch=4ba7be438ebb04ec5bb85fdb95b5a488`)

| Stage | Interest State | Live Authority (`Grant 1`, `NpcMotionSource`) | Observer Delivery (`Grant 2`, `CompanyMotionObserver`) | Native X4 Receiver (`KKJ-450`, `index=7385`) | Unrelated `MBT-139` & Own `SGA-086` |
| --- | --- | --- | --- | --- | --- |
| **0. Gate Check** | Pre-start | — | — | Unmodified `X4LAN_COMPANY_HULL_PLAYBACK_X4` **refused** `KKJ-450` (`argon` AI NPC); opt-in `X4LAN_NPC_HULL_PLAYBACK_X4` **refused** `MBT-139` (uncrewed player ship) | Both `0.000000 m` |
| **1. Pre-Interest** | Unsubscribed (`VIEW -\|0\|-\|-`) | `8` live samples acked (`FVH-379` moved `23.708 m`) | **`0` snapshots received** | **`0.000000 m`** displacement, `0` writes | Both `0.000000 m` |
| **2. Verified Cockpit Radar Interest** | `X4AuthorityNpcCockpitObserver` (`mode=radar`, `2,036.25 m <= 40,000 m`, `serial=2`) | `47` total acked (`FVH-379` `158.054 m`, peak `59.903 m/s`) | `39` snapshots received, **`39/39` `SEEN` confirmed** (`39` velocity, `seq=47` `v=(-35.384, -0.572, +19.727)` m/s) | **`152.223532 m`** continuous live displacement (`227` writes, `226` delayed readbacks, `0` alignments, `0` `>50 m` refusals, max error `1.934 mm`, `att_delta=0.000000°`) | Both `0.000000 m` |
| **3. Withdrawal** | `radar_obs:withdraw()` (`VIEW -\|0\|-\|-`) | **`+29` new live samples acked** (`FVH-379` flew **`+276.181 m`** further) | **`0` new snapshots received** | **`0.000000 m` drift**, `0` writes during withdrawal (`withdrawn` event logged) | Both `0.000000 m` |
| **4. Reacquisition** | `radar_obs:reacquire()` (`mode=radar`, `2,035.75 m <= 40,000 m`, `serial=4`) | `115` total acked (`FVH-379` `1,029.239 m`, peak `383.921 m/s`) | `76` snapshots received (`74` `cockpit` `SEEN`, `seq=114` `v=(+16.908, -4.013, +135.191)` m/s) | **`alignments=1`**, **`align_readbacks=1`** (`reacquire_aligned dist=384.131 m seq=80` → `reacquire_aligned_confirmed error=0.000000 m elapsed=16.411 ms`), **`reacquisitions=1`**, resumed `<=50 m` continuous playback (**`+978.615 m`** post-withdrawal, `415` delayed readbacks, max error **`2.005 mm`**, `elapsed` `1.978–25.500 ms`, `max_frame_gap_ms=25.494 ms`, `att_delta=0.000000°`) | Both `0.000000 m` |
| **5. Map Interest & Close** | Cockpit withdrawn; `map_obs` (`mode=map`, `serial=2`) open → closed | `146` total acked (`+12` new acks after map close) | **`16` `map` `SEEN` confirmed** (`16` velocity, `seq=131` `v=(+14.448, -3.430, +115.425)` m/s); **`0` new snapshots** after map close | Cockpit playback remained stopped while map-only slot was active | Both `0.000000 m` |
| **6. Final Independent MD+FFI Readback** | Stream closed, `Wait`/`constantvel` released | `146/146` acked (`145` velocity, `0` gaps, `0` rejected) | `93` total snapshots delivered ONLY during interest windows (`visibility_ends=3`) | **`KKJ-450` (`7385`):** final `(1403.199, -30.494, 2506.727)`, total displacement **`1,011.830081 m`**, attitude delta **`0.000000°`** | **`MBT-139` (`7480`):** **`0.000000 m`**; **`SGA-086` (`7479`):** **`0.000000 m`** |

---

## 4. Explicit Remaining Fixture Links & Evidence Boundaries

All remaining single-session fixture links are explicitly labeled here:

1. **Single-host local-origin translation:** Because one Windows PC runs one Steam `X4.exe` instance, live natural AI NPC `FVH-379` (`index=7392`) served as the real-time live flight source whose live displacement `Δp(t)` and live velocity `v(t)` were translated onto in-radar registered Argon NPC replica `KKJ-450` (`index=7385`, spawned at `2,121.5 m` and held stationary with `Wait + constantvel=0`) so one live X4 engine simultaneously acted as live AI authority source and receiving cockpit. Two-machine / two-process authority-to-client NPC streaming over TLS remains the next integration step once paired with a remote provider.
2. **Private local loopback service & harness interest triggers:** `CompanyMotionService` ran on local loopback on one Windows 11 PC (no remote WAN/Fedora server or TLS save-download bootstrap). Nearby unrelated visible ship `MBT-139` (`649.875 m`) and replica `KKJ-450` (`2,121.5 m`) were spawned & enrolled in `X4AuthorityShipRegistry` by the setup cue, and the radar withdrawal/reacquisition and map-open interest transitions were toggled by harness commands (with MD `X4AuthorityNpcCockpitObserver` verifying live `dist <= currentradarrange` and non-player AI-pilot eligibility on every observation).
3. **Unattended launcher lane:** Executed in disposable profile `45850`, `800×600` windowed `fast=True` `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`.

---

## 5. Hygiene & Retained Private Local Artifacts

- **Hygiene (all passed):** `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, `project_error_count=0`, shared lockfile `.x4-native-test.lock` removed.
- **Private local artifacts (`.local/neon-registered-npc-live-win-01/`, uncommitted):**
  - `result.json` (`35,981` B, SHA-256 `03c4f46a3f2566854ebae839c6954a41d1cf03530a5e8a9475d0309044e8bcc4`)
  - `neon-registered-npc-live.log` (`36,452` B, SHA-256 `7e2b71999387fd615f87eb3b1ae8963e0828ff699cff13f47776bef16d13ce6d`)
