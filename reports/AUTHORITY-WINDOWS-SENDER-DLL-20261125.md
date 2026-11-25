# Packaged Windows Company Motion Sender, DLL Selection, and Clock Verification

## Summary

We inspected the exact shared `X4-Authority-Windows-d0756d51.zip` (`5462d99f…`) and stock `-CompanyMotion` startup path, reproduced `X4-Authority-Windows-2ad3fc46.zip` (`1a1cd0f0…`) byte for byte on native Windows 11, and executed one native Windows 11 X4 9.00 controlled-ship flight (`neon-windows-sender-win-01`) loading the **exact packaged `network/x4lan_net_64.dll`** (`6ad97e04…`) through the unmodified `7ae7a5a0` sender stack (`local_udp_module.lua` → `company_motion_source.lua` → `source_motion_sampler.lua` → `WindowsMotionPeer` → `CompanyMotionSource` → `ReconnectingCompanyMotionClient` → `CompanyMotionService`).

All 37 emitted `SAMPLE3` frames were validated by `WindowsMotionPeer` (matching `0.0.0.0:58303`, `PID 22988`), accepted by `CompanyMotionService`, and acknowledged back to `ReconnectingCompanyMotionClient` with **zero sender faults, zero gaps, and zero clock regressions**. `6ad97e04…`'s `udp.monotonic()` inside live Windows `X4.exe` matches Python's `time.perf_counter()` QPC domain to **+0.612 ms min / +3.871 ms median / +24.261 ms max** across 37 samples. During stock `<set_playership_throttle value="0.35"/>` → `0` flight, the locally controlled ship (`HDC-566`) moved **`152.925415 m`** at up to **`82.097822 m/s`**, with `last_acknowledged_sample` and `last_nonzero_velocity_acknowledged` correlating identically with X4's emitted `SAMPLE3` (`seq=37`) and `CompanyMotionService` traffic.

## 1. Packaged ZIP, DLL Selection, and Binary Inspection

### Package & DLL Digests

| Artifact | Bytes | SHA-256 | Notes |
| --- | ---: | --- | --- |
| Shared `X4-Authority-Windows-d0756d51.zip` (`F0CLKT744UT`) | 2,843,665 | `5462d99f533f8af0abc53a8183a356331030cd9c6faf9413faaa316163974b9b` | Exact match with 05:00 CET digest; `BUILD.json` pins `commit=d0756d51…`, `network/x4lan_net_64.dll`, `source_revision=d8bfeda3`, `zig=0.15.2` |
| Rebuilt `X4-Authority-Windows-2ad3fc46.zip` (native Windows 11) | 2,893,154 | `1a1cd0f00833aa9ad099198057464b3362826385c372533e8c41fcb37118afab` | Exact byte-for-byte match with 13:00 CET handover table |
| Packaged `network/x4lan_net_64.dll` | 169,984 | `6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0` | Exports `luaopen_x4lan_net`; imports `KERNEL32!QueryPerformanceCounter`, `KERNEL32!QueryPerformanceFrequency`, `WS2_32!select`; resolves `lua51_64.dll`; exposes `open` and `monotonic` |
| Older checked-in `src/lockstep/base/native/x4lan_net_64.dll` | 168,960 | `0fc98dba4d6844ffe2f59e0d827f73e5641b1c6aa5d71071641cabb4f80b6ab6` | Lacks `QueryPerformanceCounter`, `QueryPerformanceFrequency`, `select`, `pending`, and `monotonic` (fails closed under `local_udp_module.lua`) |

### Stock `-CompanyMotion` DLL & Clock Selection Chain (`d0756d51` → `7ae7a5a0`)

1. `tools/run_authority_windows_client.ps1` reads `BUILD.json`, selects `network/x4lan_net_64.dll`, verifies its SHA-256 (`6ad97e04…`), and passes `--network-dll` + `--network-dll-sha256` to `tools/dev/authority_windows_fixture_client.py`.
2. `run_fixture_session()` (`src/authority/windows_fixture_session.py`) calls `install_fixture_overlay()` (`src/authority/experimental_windows_fixture_overlay.py`), installing the verified DLL to `extensions/x4_lan_lab/native/x4lan_net_64.dll` and pinning its SHA-256 in the workspace manifest (`WindowsFixtureWorkspace.register_overlay()`).
3. Inside X4, `tools/dev/authority_two_x4_participant_probe.lua` calls `X4LAN_LOCAL_UDP_MODULE.load(ffi.os, package.loadlib)` (`src/authority/lua/local_udp_module.lua`), which loads `./extensions/x4_lan_lab/native/x4lan_net_64.dll` via `luaopen_x4lan_net` and fails closed unless both `module.open` and `module.monotonic` are functions.
4. `X4LAN_COMPANY_MOTION_SOURCE.new_x4(...)` passes `capture_clock = udp.monotonic` (`src/native/net.c`: Win32 `QueryPerformanceCounter / QueryPerformanceFrequency`) into `X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER.new_x4(...)` with `real_time_motion = true`.
5. Note on `d0756d51` vs. `2ad3fc46` / `7ae7a5a0`: the DLL selection and QPC clock path are unchanged; `2ad3fc46` / `7ae7a5a0` additionally retain `company_motion.last_acknowledged_sample` and `company_motion.last_nonzero_velocity_acknowledged` in `CompanyMotionClient.stats()`, add descriptive sampler fault labels + `allow_superhighway_gap`, and register LuaJIT FFI `cdef` declarations once per module.

### Code-Audit Finding in `src/authority/windows_x4_process.py`

In `src/authority/windows_x4_process.py` (present at both `d0756d51` and `7ae7a5a0`), `def exit_code(self):` at lines 186–191 is indented inside `_Win32Job` (shadowing `_Win32Job.exit_code` at lines 171–176) instead of `WindowsX4Process`. As a result, `WindowsX4Process` has no `exit_code()` attribute, so `windows_fixture_session.py:267` (`if play_until_exit and admission_complete and process.exit_code() == 0:`) will raise `AttributeError` when a `--play-until-exit` Windows player exits X4 cleanly. Not modified in this investigation branch.

## 2. Native Windows 11 X4 9.00 Controlled-Ship Sender Flight (`neon-windows-sender-win-01`)

### Environment & Evidence Boundary

- **Base commit:** `7ae7a5a0` (unmodified production Lua/MD/Python sender and transport modules; zero production edits).
- **Host / GPU:** Windows 11, Python `3.12.10` (`.venv`), X4 `9.00` (`X4.exe` SHA-256 `19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`), `'AMD Radeon RX 9070 XT' AMD v2.0.416 (Vulkan 1.4.359) check=1`.
- **Disposable isolation:** Launcher profile `45850`, isolated `x4ls` view (`R:\x4ls-lanes\neon-sender-neon-windows-sender-win-01`), `800×600` windowed `fast=True`, `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`, shared lockfile enforced.
- **Evidence source separation:**
  - **Real native Windows X4 9.00:** Packaged `x4lan_net_64.dll` (`6ad97e04…`) at `extensions/x4_lan_lab/native/x4lan_net_64.dll`, unmodified `local_udp_module.lua`, `company_motion_source.lua`, `source_motion_sampler.lua`, `authority_job_descriptor_probe.{lua,xml}`, `replica_motion_binding.lua`, `WindowsMotionPeer`, `CompanyMotionSource`, `ReconnectingCompanyMotionClient`, and `CompanyMotionService`. Locally controlled ship `HDC-566` (`index=1`, `0xafeef`, `ship_arg_s_scout_01_a_macro`, `cluster_14_sector001_macro`) flew under real X4 engine physics via stock MD `<set_playership_throttle value="0.35"/>` → `<set_playership_throttle value="0"/>`.
  - **Private local loopback peer:** `CompanyMotionService` ran on the same Windows host over loopback (no remote WAN/Fedora server and no TLS download/load bootstrap — `WindowsFixtureWorkspace` / `WindowsX4Process` launches a `1280×720` non-muted visible window, so our unattended run used the `800×600` `fast=True` `HWND_BOTTOM` launcher lane with the exact packaged DLL and production sender stack).

### OS-Verified Loaded Modules Inside Live `X4.exe` (`PID 22988`, `TH32CS_SNAPMODULE`)

| Module | Loaded Path in `X4.exe` (`PID 22988`) | Base Addr | File Bytes | On-Disk SHA-256 |
| --- | --- | --- | ---: | --- |
| `x4lan_net_64.dll` | `R:\x4ls-lanes\neon-sender-neon-windows-sender-win-01\extensions\x4_lan_lab\native\x4lan_net_64.dll` | `0x7ff80b860000` | 169,984 | `6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0` |
| `lua51_64.dll` | `R:\x4ls-lanes\neon-sender-neon-windows-sender-win-01\lua51_64.dll` | `0x7fffe1d00000` | 973,312 | `e0712ac93f0d7f3a28236d3153b82e85d35e6bcf8e606af250f664580eaf3b63` |

### Live `udp.monotonic()` Clock vs. Windows `time.perf_counter()`

- **Direct load check (`seq=1`):** Python `time.perf_counter()` bracketed X4's `lib.monotonic()` cleanly: `py_before=1755.0875776` < `mono_t1=1755.109479600` < `mono_t2=1755.109479700` (+100 ns) < `dll_monotonic=1755.109768000` < `py_after=1755.1525464`.
- **Across all 37 `SAMPLE3` packets (`seq 1..37`):** `capture_time` advanced strictly monotonically from `1755.1250587 s` to `1759.2373908 s` (`0` regressions across `408` `onUpdate` ticks).
- **Host receipt skew (`py_perf_counter - sample.capture_time`):** min **`+0.612 ms`**, median **`+3.871 ms`**, max **`+24.261 ms`**.

### `WindowsMotionPeer` Binding & Stage Progression

- `GetExtendedUdpTable` matched a single unique row: `local_ip=0.0.0.0`, `local_port=58303`, `pid=22988` (loopback peer `127.0.0.1:58303`), validating Cobalt's wildcard owner fix live against `6ad97e04…`.
- Stages fired in sequence within `6.3 ms`:
  1. `first_local_motion_datagram_matched` (`1755.1462784`)
  2. `first_local_motion_peer_verified` (`1755.1472794`)
  3. `first_local_own_pose_validated` (`1755.1474182`)
  4. `first_own_sample_acknowledged` (`1755.1525410`)

### Controlled-Ship Flight & End-to-End Acknowledgement Correlation

Epoch: `4ba7be438ebb04ec5bb85fdb95b5a488`, ship `HDC-566` (`index=1`, `0xafeef`, `cluster_14_sector001_macro`). Local sender generation: `ecfd0d80bf84f8f71c8e58497fd54116`; service grant generation: `763006169466a3f0f2bdf08c952bdcbd`.

| Phase / Event | Seq | `source_time` (s) | `capture_time` (s) | `py - capture` (ms) | Position `(x, y, z)` m | `real_velocity` `(vx, vy, vz)` m/s | Client ACK / Service Match |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| Stationary baseline | 1 | `2.133976` | `1755.1250587` | `+22.388` | `(0.0, 0.0, 0.0)` | `null` | Accepted & ACK'd |
| Stationary checkpoint | 5 | `2.576610` | `1755.5673239` | — | `(0.0, 0.0, 0.0)` | `(0.0, 0.0, 0.0)` | `client_stats_stationary` (`5/5` ACK'd) |
| First nonzero velocity | 7 | `2.797503` | `1755.7886351` | `+0.612` | `(0.0, 0.0, 0.038103)` | `(0.0, 0.0, +0.341657)` | Accepted & ACK'd |
| Mid-flight checkpoint | 25 | `4.843671` | `1757.8350113` | — | `(0.0, 0.0, 95.846695)` | `(0.0, 0.0, +80.229273)` | `client_stats_mid_flight` (`25/25` ACK'd) |
| Peak speed sample | 27 | `5.077290` | `1758.0688540` | `+1.562` | `(0.0, 0.0, 114.847954)` | `(0.0, 0.0, +82.097822)` | Matches `service_traffic.max_reported_speed_mps=82.097822` & MD `speed=82.1875` |
| **Final correlated sample** | **37** | `6.245526` | `1759.2373908` | `+24.261` | `(0.0, 0.0, 152.9254150390625)` | `(0.0, 0.0, +4.778852)` | **Exact match** across X4 `SAMPLE3`, `CompanyMotionService` (`37/37` accepted, `36` velocity, max step `9.769302 m`), `last_acknowledged_sample`, `last_nonzero_velocity_acknowledged`, and final FFI pose (`152.925415 m`, `0.000000 m` delta) |

## 3. Hygiene & Local Artifacts

- Exit code `0`, `outcome=done`, `project_error_count=0`, `sender_faults=[]`, `sender_gaps=[]`, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, shared lockfile removed.
- Local artifacts retained under `.local/neon-windows-sender-win-01/` (uncommitted):
  - `result.json` (`13,308` B, SHA-256 `01e6418b3edfe7d2da963c52aec75b0f4635912ae4b3a250f61c468d10b5730d`)
  - `neon-windows-sender.log` (`7,716` B, SHA-256 `882cd6498912a2aaf57168c2bf4d7e75872e355869fe30cf87d855f14b058f15`)
- Runner: `.\.venv\Scripts\python.exe tools/dev/authority_windows_sender_windows.py --dll .local/pkg-d0756d51/x4-authority-windows-diagnostic/network/x4lan_net_64.dll --out .local/neon-windows-sender-win-01`
- Limits: Single-machine loopback `CompanyMotionService` peer (no remote WAN/Fedora host, no TLS save-download/admission bootstrap, and no remote observer cockpit on a second machine). Demonstrates that the packaged Windows DLL (`6ad97e04…`), its QPC `udp.monotonic()` clock, `WindowsMotionPeer`, `company_motion_source.lua`, `source_motion_sampler.lua`, and `ReconnectingCompanyMotionClient` operate cleanly on native Windows 11 X4 9.00 with zero sender-side faults.
