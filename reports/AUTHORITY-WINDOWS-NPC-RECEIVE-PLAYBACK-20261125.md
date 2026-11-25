# Native Windows Registered-NPC Receive & Playback Check (25 Nov 2026)

- **Author:** Neon (James Molander / `von-Benette`)
- **Branch:** `work/neon-windows-npc-receive-playback` (base `test/base-game-two-pilots-20261124` @ `b418a409`)
- **Host / GPU:** Windows 11 PC (separate machine from Cobalt and Lith7ium), Python `3.12.10` (`.venv`), `'AMD Radeon RX 9070 XT' AMD v2.0.416 (Vulkan 1.4.359) check=1`, X4 `9.00` pinned `X4.exe` (`SHA-256 19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`).
- **Scope (per Astra 15:00 CET check-in):** Exercise the ordinary Windows receive/playback path with a registered moving NPC from the authority and one unrelated visible ship; compare sender identity, velocity, and observer readback; explicitly label any recorded authority input.

---

## 1. Explicit Authority-Input Label & `b418a409` Eligibility Gate Audit

### Authority Input Source (Explicitly Labeled)

- **Authority input source:** **`recorded_phase1_native_x4_ai_flight`** — a real **90-sample live AI flight trajectory** captured in **Phase 1** on native Windows X4 9.00 (`PID 8260`) from registered Argon container transport **`SRA-116`** (`index=7272`, `ship_arg_m_trans_container_02_a_macro`, `cluster_14_sector001_macro`) immediately after saving disposable checkpoint `save_009.xml.gz` (`9,982` enrolled ships, `25,491,612` B, SHA-256 `6a0f5707ff691c52a5e8bc444d20c01d55ce60e285e32b841b2ab23decb3ade5`).
- **Replay method in Phase 2:** Replayed into an unmodified local loopback `CompanyMotionService` via a real `CompanyMotionClient` (`Grant 1`, `ship_index=7272`) at the exact recorded inter-sample `dt`, positions, `source_time`, and `real_velocity`, with `capture_time` monotonically re-anchored to Phase 2's Windows QPC (`time.perf_counter()`) timeline so the live service freshness check (`MAX_SAMPLE_AGE = 3.0 s`) and `motion_playback.lua` ran natively without fabricating motion.

### Code Audit of Existing `b418a409` Windows Receive/Playback Gates (Zero Production Edits)

Production files under `src/authority/` were left **100% unmodified**:

1. **Company cockpit receive/playback path** (`src/authority/md/company_cockpit_observer.xml`, `src/authority/lua/company_cockpit_observer.lua`, `src/authority/lua/company_hull_playback_x4.lua`): enforces `owner == 'player'` and rejects any ship with `pilot`, `assignedpilot`, or `assignedaipilot`. In Phase 2 inside native Windows X4 9.00, we invoked unmodified `X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4(...):bind(...)` against `SRA-116` and verified empirically that it refused the NPC (`AUTH_NPC_COMPANY_GATE_CHECK refused=true index=7272 code=SRA-116 owner=argon`).
2. **Recovered-save NPC receive/playback path** (`src/authority/lua/saved_motion_apply.lua`, `saved_replica_control.lua`, `src/authority/participant_motion.py`, `windows_fixture_session.py`): fenced exclusively to the private Linux recovery fixture `recovery_enh_870` (`ENH-870`, `index=1318`, `cluster_06_sector001_macro`, `test_profile='45841'`).
3. **Unmodified ordinary Windows receive/playback stack exercised in Phase 2:**
   - Packaged `network/x4lan_net_64.dll` (`169,984` B, SHA-256 `6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0`, Win32 QPC `udp.monotonic()`) installed at `extensions/x4_lan_lab/native/x4lan_net_64.dll` and loaded through unmodified `src/authority/lua/local_udp_module.lua` (no clock-offset workaround),
   - Unmodified `CompanyMotionService` → `CompanyMotionClient` (Grant 1 authority sender + Grant 2 observing Windows client) → `CompanyMotionSource` → `WindowsMotionPeer` (`GetExtendedUdpTable` child-PID validation) → `CompanyMotionObserver`,
   - Unmodified `company_observer_stream.lua` → `company_cockpit_playback.lua` (`allow_reacquire_align=false`) → `company_hull_playback.lua` → `motion_buffer.lua` + `motion_playback.lua` (5 s prediction window, 50 m step limit, delayed later-update readbacks),
   - Paired only with a thin opt-in NPC observer/pose adapter in `tools/dev/authority_windows_npc_playback_{probe.xml,x4.lua,windows.py}` that guards `owner ~= 'player'` and holds local AI linear velocity (`Wait` + `set_object_velocity constantvel="true" 0,0,0`) on the target NPC only during playback and releases it cleanly before exit.

---

## 2. Native Windows 11 X4 9.00 Run (`neon-windows-npc-playback-win-05`)

### Loaded Packaged DLL (`TH32CS_SNAPMODULE`), QPC Clock & `WindowsMotionPeer`

| Phase | Live `X4.exe` PID | Loaded `x4lan_net_64.dll` Path | Base Addr | File Bytes | On-Disk SHA-256 | `udp.monotonic()` Load Check |
| --- | ---: | --- | --- | ---: | --- | --- |
| Phase 1 (Capture) | `8260` | `R:\x4ls-lanes\neon-npc-p1-neon-windows-npc-playback-win-05\extensions\x4_lan_lab\native\x4lan_net_64.dll` | `0x7ff811d90000` | 169,984 | `6ad97e04…e2809e0` | `6008.843157300 → 6008.843157400` (`+100 ns`) |
| Phase 2 (Playback) | `15396` | `R:\x4ls-lanes\neon-npc-p2-neon-windows-npc-playback-win-05\extensions\x4_lan_lab\native\x4lan_net_64.dll` | `0x7ffff5730000` | 169,984 | `6ad97e04…e2809e0` | `6062.143137400 → 6062.143137500` (`+100 ns`, bracketed by Python `time.perf_counter()` `6022.4650411 < mono < 6062.1528452`) |

- **`lua51_64.dll` in live `X4.exe` (`PID 15396`):** `973,312` B, SHA-256 `e0712ac93f0d7f3a28236d3153b82e85d35e6bcf8e606af250f664580eaf3b63` (base `0x7fffdbfe0000`).
- **`WindowsMotionPeer` (`GetExtendedUdpTable`):** Matched a single unique IPv4 row `local_ip=0.0.0.0`, `local_port=59367`, `pid=15396`.
- **`CompanyMotionSource` stages:** `first_local_motion_datagram_matched` (`6076.5272484`) → `first_local_motion_peer_verified` (`6076.5291045`, `+1.86 ms`).

### Sender Identity vs. Observer `SEEN` vs. Native X4 Delayed Readback

| Field | Authority Sender (`CompanyMotionClient` ACK, Grant 1) | `CompanyMotionService` Traffic | Observing Client (`CompanyMotionObserver` `SEEN`, `cockpit`) | Native X4 Delayed Readback (`n=898`) | Match |
| --- | --- | --- | --- | --- | --- |
| `epoch` | `4ba7be438ebb04ec5bb85fdb95b5a488` | `4ba7be438ebb04ec5bb85fdb95b5a488` | `4ba7be438ebb04ec5bb85fdb95b5a488` | `4ba7be438ebb04ec5bb85fdb95b5a488` | Exact |
| `ship_index` | `7272` | `7272` (`last_received_ship_index=7272` on Grant 2) | `7272` (`serial=1`) | `7272` (`serial=1`) | Exact |
| `ship_code` | `SRA-116` (Phase 1 capture) | — | `SRA-116` | `SRA-116` | Exact |
| `actor_hex` | `0x80cd3` (Phase 1 process) | — | `0x5b6db` (Phase 2 cold-reloaded process) | `0x5b6db` (Phase 2 resolved) | Remapped cleanly via saved registry `7272` |
| `generation` | `1e4d072e8a47915177d07929416ac723` | `1e4d072e8a47915177d07929416ac723` | `1e4d072e8a47915177d07929416ac723` | `1e4d072e8a47915177d07929416ac723` | Exact |
| `sector_macro` | `cluster_14_sector001_macro` | `cluster_14_sector001_macro` | `cluster_14_sector001_macro` | `cluster_14_sector001_macro` | Exact |
| Sample count | `90 / 90` acknowledged (`89` with velocity) | `90` accepted (`89` with velocity), `90` snapshot receipts | `90 / 90` `SEEN` confirmed (`89` with velocity) | `899` writes, `898` delayed readbacks | Exact (`0` dropped) |
| Final `sequence` | `90` | `last_source_sequence=90` | `90` | `90` (`source_time=13.5297685`, `capture_time=6085.4501865`) | Exact |

### Velocity & Extrapolation Comparison

| Metric | Phase 1 Native Capture | Authority Sender ACK (`seq=90`) | `CompanyMotionService` | Observer `SEEN` (`seq=90`) | Native X4 Playback (`n=898`, `seq=90`) |
| --- | --- | --- | --- | --- | --- |
| `real_velocity` `(vx, vy, vz)` m/s | `(+116.101158, -19.798837, -110.951510)` | `(+116.10115831533633, -19.798836641476207, -110.95151016425287)` | — | `(+116.10115831533633, -19.798836641476207, -110.95151016425287)` | Applied continuously via `motion_buffer.lua` + `motion_playback.lua` |
| Speed at `seq=90` (m/s) | `161.807634` | `161.807634` | — | `161.807634` | Bit-exact match |
| Peak speed (m/s) | `164.442660` | — | `max_reported_speed_mps=164.442660` (`max_source_step_metres=16.741666 m`) | — | Continuous (`0` refusals `>50 m`) |
| Extrapolation check after `seq=90` | `seq=90` pose `(17515.164, 5253.216, 38416.906)` | Emitted before 0.8 s settle window | — | — | `target=(17589.072, 5240.612, 38346.276)` = `seq90_pose + 0.63658 s * seq90_velocity` across all 3 axes (`dt_x=0.63658 s`, `dt_y=0.63659 s`, `dt_z=0.63658 s`), readback `actual=(17589.070, 5240.612, 38346.277)`, **`error=0.002282 m` (`2.28 mm`)** |

### Observer Readback Comparison Across All Three Registered Ships

| Ship Role | Registry Index & Code | Owner / Actor (Phase 2) | Initial Pose (Post-Hold, m) | Final Pose (Independent MD+FFI, m) | Total Displacement (m) | Playback Writes / Readbacks | Notes |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| **Registered Moving NPC** | `7272` (`SRA-116`) | `argon` / `0x5b6db` | `(16747.383, 5376.644, 39150.766)` | `(17591.086, 5240.269, 38344.352)` | **`1,175.047606`** | `899` writes / `898` delayed readbacks | Cold-reload offset vs. Phase 1 pre-save: **`0.000488 m` (`0.49 mm`)**; `0` alignments, `0` `>50 m` refusals, `0` withdrawals, `0` stale cutoffs; delayed readback error min `0.000 m`, max **`0.006410 m` (`6.41 mm`)**, `elapsed` `0.986–24.677 ms`; `ticks=923`, `max_frame_gap_ms=109.567 ms` |
| **Unrelated Visible Ship** | `7405` (`XLT-744`) | `player` (uncrewed) / `0x5d65a` | `(0.000, 0.000, 650.000)` | `(0.000, 0.000, 650.000)` | **`0.000000`** | `0` / `0` | Same sector (`cluster_14_sector001_macro`), `649.875 m` directly ahead, `onscreen_initial=true`, `0` samples subscribed or delivered |
| **Local Own Ship** | `7404` (`BQJ-223`) | `player` (controlled) | `(0.000, 0.000, 0.000)` | `(0.000, 0.000, 0.000)` | **`0.000000`** | `0` / `0` | Same sector, untouched throughout |

### Selected Delayed Playback Readbacks (`AUTH_NPC_PLAYBACK_READBACK`)

| Readback `#` | `seq` | `serial` | `elapsed` (ms) | Target `(x, y, z)` (m) | Actual `(x, y, z)` (m) | Error (mm) | Displacement (m) |
| ---: | ---: | ---: | ---: | --- | --- | ---: | ---: |
| `1` | `2` | `1` | `0.986` | `(16760.570, 5374.596, 39138.164)` | `(16760.570, 5374.596, 39138.164)` | `0.00` | `18.355` |
| `2` | `3` | `1` | `4.068` | `(16760.620, 5374.588, 39138.117)` | `(16760.617, 5374.588, 39138.117)` | `2.42` | `18.422` |
| `844` | `88` | `1` | `17.257` | `(17484.463, 5258.451, 38446.251)` | `(17484.461, 5258.452, 38446.250)` | `1.92` | `1,026.448` |
| `888` | `90` | `1` | `17.306` | `(17569.663, 5243.922, 38364.824)` | `(17569.664, 5243.922, 38364.824)` | `0.72` | `1,145.192` |
| `898` (final) | `90` | `1` | `17.364` | `(17589.072, 5240.612, 38346.276)` | `(17589.070, 5240.612, 38346.277)` | `2.28` | `1,172.239` |

---

## 3. Technical Observations & Honest Evidence Boundaries

1. **NPC Angular Rate Under `set_object_velocity(constantvel="true")`:** `PreparePlayback` held `SRA-116` with `Wait(skipalignment=true, fidget=false)` and `<set_object_velocity constantvel="true"><linear value="vector.[0m, 0m, 0m]"/></set_object_velocity>`. Because `<angular>` was not locked, `SRA-116` kept its saved turn rate (~22°/s yaw across the ~10 s playback window, rotating smoothly from `-105.40°` through `+33.76°`). Our `SetObjectSectorPos` pose write preserved the live `yaw/pitch/roll` every frame without snapping to zero while holding linear position accuracy within `6.41 mm` across all `898` delayed readbacks. Future authority NPC replication should either transmit authority orientation or also lock `<angular>` during replica hold.
2. **Evidence boundaries & limits:**
   - **Recorded authority trace on private loopback:** Phase 2 replayed a **recorded Phase 1 native Windows X4 9.00 AI trace** (`recorded_phase1_native_x4_ai_flight`) through a local loopback `CompanyMotionService` on one Windows 11 PC (not a remote WAN/Fedora server or two simultaneous X4 processes).
   - **Thin opt-in NPC observer/adapter in `tools/dev/`:** Because `b418a409`'s production `company_cockpit_observer` / `company_hull_playback_x4` reject NPCs (`owner == player`, no pilot) and `saved_motion_apply` is fenced to `recovery_enh_870`, the NPC target inspection and `SetObjectSectorPos` adapter lived in `tools/dev/authority_windows_npc_playback_{probe.xml,x4.lua}`, while all core transport, DLL, QPC clock, observer stream, playback controller, buffer, and motion playback modules (`local_udp_module.lua`, `company_observer_stream.lua`, `company_cockpit_playback.lua`, `company_hull_playback.lua`, `motion_buffer.lua`, `motion_playback.lua`, `CompanyMotionService`, `CompanyMotionClient`, `CompanyMotionSource`, `CompanyMotionObserver`, `WindowsMotionPeer`) were unmodified `b418a409` production code.
   - **Visibility details:** `SRA-116` was in the same sector (`cluster_14_sector001_macro`) at `42,928 m` (`within_radar=false`, just outside the `40 km` radar radius). Unrelated visible ship `XLT-744` was in the same sector at `649.875 m` directly ahead (`onscreen_initial=true`); `SetSofttarget` did not establish an active HUD target lock in the unfocused `SW_SHOWNOACTIVATE` window (`hud_locked_before=false`), though `XLT-744` remained visible onscreen and untouched (`0.000000 m` displacement, `0` writes/samples).
   - **Unattended launcher lane:** Executed in disposable profile `45850`, `800×600` windowed `fast=True` `SW_SHOWNOACTIVATE` + `HWND_BOTTOM` (not the interactive `1280×720` `WindowsFixtureWorkspace` launcher or TLS save-download bootstrap).

---

## 4. Hygiene & Private Local Artifacts

- **Hygiene assertions (all passed):** `protected_unchanged=true`, `phase1_view_removed=true`, `phase2_view_removed=true`, `x4_running_after=false`, `phase1_project_error_count=0`, `phase2_project_error_count=0`, shared lockfile `.x4-native-test.lock` cleanly removed.
- **Offline verification:** `py_compile`, XML `ElementTree` parse, LuaJIT 2.1 `loadfile`, and `git diff --check` clean on the committed harness files.
- **Retained private local artifacts (`.local/neon-windows-npc-playback-win-05/`, uncommitted):**
  - `result.json` (`48,443` B, SHA-256 `4c93a9069b1ea3fe72f9714327b00e5345a33f90df17b0874865bebbce7afc56`)
  - `captured-npc-trace.json` (`55,980` B, SHA-256 `5159c8903aa1ede9d850c522476a67381f85d84700af26daffc609bc1844605e`)
  - `neon-npc-phase1-capture.log` (`50,472` B, SHA-256 `a503fc82b2d72d7dc3c34a26d424b0c25538a9e2feb58ba65d459739b6da8663`)
  - `neon-npc-phase2-playback.log` (`25,617` B, SHA-256 `276545852b501a7b40a2693abd5964db21e17c484d91f320c3f685d0e425fce6`)
  - `save_009.xml.gz` (`25,491,612` B, SHA-256 `6a0f5707ff691c52a5e8bc444d20c01d55ce60e285e32b841b2ab23decb3ade5`)
