# Indexed Company Cockpit Playback on Windows GPU (`d0756d51`)

## Summary

One bounded native Windows 11 X4 9.00 run (`neon-indexed-hud-win-02`) exercised the unmodified `d0756d51` indexed cockpit observer (`src/authority/lua/company_cockpit_observer.lua`, `src/authority/md/company_cockpit_observer.xml`) and production cockpit playback (`src/authority/lua/company_cockpit_playback.lua`, `src/authority/lua/company_observer_stream.lua`) on a discrete GPU (`AMD Radeon RX 9070 XT`, Vulkan `1.4.359`), reusing the existing disposable Windows flight fixture (`tools/dev/authority_company_cockpit_multipilot_probe.xml`).

While a **third Company ship** (`MHK-947`, registry index `3`, `0x178233`) remained continuously locked on the stock X4 HUD, the indexed cockpit observer (`target_index=2`, `radar_fallback=true`) bound and tracked the **reserved remote Company ship** (`OGE-507`, registry index `2`, `0x1781b5`) in `mode=radar` at view `serial=2` with **`0` withdrawals** (`AUTH_COMPANY_COCKPIT_WITHDRAWAL` never fired; max inter-tick gap `25.3 ms` across `380` ticks vs. the `450 ms` freshness limit). `OGE-507` moved **`88.710605 m`** across `364` writes / `363` delayed readbacks (`11` logged `AUTH_COMPANY_COCKPIT_READBACK` lines, max position error `0.001640 m`), while both the local ship (`GOH-291`, index `1`) and the HUD-targeted third ship (`MHK-947`, index `3`) remained at **`0.000000 m`** displacement.

## Environment & Test Boundary

- **Base commit:** `0e94cec4` (contains unmodified `d0756d51` runtime modules; zero production source files modified).
- **Host / GPU:** Windows 11, Python `3.12.10` (`.venv`), X4 `9.00` (`X4.exe` SHA-256 `19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`), `'AMD Radeon RX 9070 XT' AMD v2.0.416 (Vulkan 1.4.359) check=1`.
- **Disposable isolation:** Launcher profile `45850`, isolated `x4ls` view (`R:\x4ls-lanes\neon-indexed-hud-neon-indexed-hud-win-02`), `800×600` windowed `fast=True`, `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`, shared lockfile enforced.
- **Evidence source separation:**
  - **Real native Windows X4 9.00:** Stock `SetSofttarget()` HUD lock, full production `d0756d51` observer + playback + `AUTH_COMPANY_COCKPIT_READBACK` + `AUTH_COMPANY_COCKPIT_WITHDRAWAL` + map observer Lua/MD modules, PID-validated loopback UDP (`0.0.0.0:55214`, `PID 3568`), real hull writes, delayed readbacks, and independent MD+FFI final readbacks.
  - **Recorded / loopback remote pilot:** Pilot `2` (`OGE-507`) was fed a synthetic constant-velocity `(18.0, 4.0, 12.0)` m/s (`22.0 m/s`) trajectory at `10 Hz` (`30` samples, `3.0 s`) through real `CompanyMotionService` + `ReconnectingCompanyMotionClient.observing(...)` + `CompanyMotionSource(playback_index=2, cockpit_radar_playback=True)` on the same machine. This is **not** a two-machine live Internet flight.
  - **Clock note:** Checked-in `src/lockstep/base/native/x4lan_net_64.dll` (168,960 B) does not export `udp.monotonic`, so the harness aligned `GetCurRealTime()` once at stream start to Python's QPC (`time.perf_counter()`, `clock_source=GetCurRealTime_qpc_aligned`), while Python `CompanyMotionObserver` stamped receipts with `time.perf_counter()`. A prior failed attempt (`neon-indexed-hud-win-01`, retained locally, `result.json` SHA-256 `4256f9921d236734aa7806261d3c7cea8bffdcb60b18f451eeff6341bbca89bd`) verified that X4's LuaJIT FFI sandbox restricts direct `QueryPerformanceFrequency` calls.

## Measurements (`neon-indexed-hud-win-02`)

Sector for all three ships: `cluster_14_sector001_macro` (`epoch=4ba7be438ebb04ec5bb85fdb95b5a488`, remote `generation=c4d187eb16186a4a565d78635a8f6311`).

### 1. Ships, HUD Lock, and Displacements

| Role | Reg. Index | ID Code | Actor Hex | Initial Pose `(x, y, z)` m | Final Pose `(x, y, z)` m | Displacement (m) |
| --- | ---: | --- | --- | --- | --- | ---: |
| Local ship (`player.controlled`) | 1 | `GOH-291` | `0xaf260` | `(0.000000, 0.000000, 0.000000)` | `(0.000000, 0.000000, 0.000000)` | **0.000000** |
| Reserved remote ship (observed on radar) | 2 | `OGE-507` | `0x1781b5` | `(-160.000000, 0.000000, 820.000000)` | `(-87.835938, 16.036255, 868.108398)` (MD+FFI) / `(-87.41796875, 16.1290283203125, 868.38671875)` (last `READBACK`) | **88.199951** (MD+FFI) / **88.710605** (last `READBACK`) |
| Selected HUD target (3rd Company ship) | 3 | `MHK-947` | `0x178233` | `(220.000000, 40.000000, 900.000000)` | `(220.000000, 40.000000, 900.000000)` (MD+FFI) | **0.000000** |

- **Selected HUD ship verification (`GetPlayerTarget()`):**
  - Pre-stream lock (`seq=1`): `locked=true`, `hud_index=3`, `hud_code=MHK-947`, `hud_hex=0x178233`, `on_screen=true`, `dist_m=927.250`.
  - Stream start (`seq=2`), subscription (`seq=3`), mid-stream (`seq=4`, `seq 15`), post-stream (`seq=5`), and both final MD readbacks (`seq=6`, `seq=7`): continuously remained `hud_index=3`, `hud_code=MHK-947`, `hud_hex=0x178233`.
- **Remote ship range:** `OGE-507` moved from `835.375 m` (`seq=3`) through `849.500 m` (`seq=4`) to `870.875 m` (`seq=5`) from `GOH-291`, well within radar range.

### 2. First and Last `AUTH_COMPANY_COCKPIT_READBACK` Lines & View Serial

Production `d0756d51` (`make_x4_record`) logged **11** `AUTH_COMPANY_COCKPIT_READBACK` lines (across `364` total writes and `363` delayed readbacks; `30/30` `SEEN` receipts confirmed, `29` with nonzero velocity). Every line reported `slot=cockpit`, `kind=readback`, `mode=radar`, `ship=OGE-507`, `index=2`, `serial=2`.

| Readback | View Serial | Mode | Seq | Source / Capture Time (s) | Elapsed (s) | Target `(x, y, z)` m | Actual `(x, y, z)` m | Error (m) | Logged Displacement (m) |
| --- | ---: | --- | ---: | --- | ---: | --- | --- | ---: | ---: |
| **First** (#1 of 11) | **2** | `radar` | 2 | `100.2` / `500.2` | `0.015784` | `(-156.4, 0.8, 822.4)` | `(-156.3984375, 0.800048828125, 822.400390625)` | `0.001611` | `4.401500` |
| **Last** (#11 of 11) | **2** | `radar` | 30 | `103.0` / `503.0` | `0.013498` | `(-87.419527, 16.128994, 868.386982)` | `(-87.41796875, 16.1290283203125, 868.38671875)` | `0.001581` | `88.710605` |

*(The last logged `AUTH_COMPANY_COCKPIT_READBACK` occurred `~0.22 s` after `rb_p2` while waiting for `rb_p3`, as `OGE-507` continued smooth velocity extrapolation inside its 5.0 s prediction window.)*

### 3. Withdrawals & Frame Timing

- **`AUTH_COMPANY_COCKPIT_WITHDRAWAL` count:** **`0`** (`first_withdrawal_reason = null`).
- **`AUTH_COMPANY_COCKPIT_STOP` / `AUTH_COMPANY_COCKPIT_EVENT` count:** `0` / `0`.
- **Playback stats at `seq=5`:** `binds=1`, `writes=364`, `readbacks=363`, `withdrawals=0`, `stale=0`, `alignments=0`.
- **Inter-tick gap on hardware GPU:** `ticks=380`, `max_frame_gap_s=0.025259 s` (`25.3 ms` max gap, well inside the `450 ms` freshness limit; by contrast, Bazzite software Vulkan hit a `1.563027 s` gap and triggered `expired`).

## Hygiene & Local Artifacts

- Exit code `0`, `outcome=done`, `project_error_count=0`, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`.
- Retained locally under `.local/neon-indexed-hud-win-02/` (uncommitted):
  - `result.json` (`16,823` B, SHA-256 `796b1ec5b2b43abb6f4dfc33ab5432b3efdaf9f94d747608d80216b203a98c3b`)
  - `neon-indexed-hud.log` (`15,938` B, SHA-256 `eca2954939503d6a620b5664a7f5572b552b239d340855cc47f0a0d270c70714`)

## Reproduction & Limits

- Runner: `.\.venv\Scripts\python.exe tools/dev/authority_company_indexed_hud_windows.py --out .local/neon-indexed-hud-win-02`
- Limits: single-machine isolated Windows run with recorded/loopback remote motion (`index=2`), checked-in lockstep `x4lan_net_64.dll` using `GetCurRealTime` QPC-aligned clock fallback rather than `udp.monotonic`, and closed map (`map.samples_confirmed=0`). Does not establish multi-machine Internet transport or live human flight behavior.
