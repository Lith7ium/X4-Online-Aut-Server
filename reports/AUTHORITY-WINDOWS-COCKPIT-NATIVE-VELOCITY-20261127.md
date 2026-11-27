# Windows X4 9.00 Opt-In Receiving-Cockpit Native Velocity Integration (`constantvel=false`)

- **Date:** 2026-11-27 (CET)
- **Author / Agent:** Neon (James Molander / `von-Benette`)
- **Branch:** `work/neon-cockpit-native-velocity`
- **Base revision:** `origin/trial/velocity-aware-playback-20261126` @ `6a9cc6ccdad752a0738688570ad1b395d9fa16ea`
- **Directive:** Astra `03:00 CET` Slack directive (`#x4-authorative-server`, `ts=1795744863.152949`)
- **Scope constraint honored:** Wire protocol (`REMOTE4`/`SEEN4`), shared server (`CompanyMotionService`), `motion_buffer.lua`, `motion_playback.lua`, `company_observer_stream.lua`, `company_cockpit_observer.lua`, `company_motion_source.lua`, clock policy, and normal client launch scripts/defaults are **unchanged**. The integration is strictly opt-in (`cockpit_native_velocity=False` by default) via a small delta across five existing `src/authority/` files (`+141/-12` lines total) plus two new opt-in MD/Lua files.

---

## 1. Host, Build, and Explicit Test Input Provenance

- **Host:** Windows 11 desktop, AMD Radeon RX 9070 XT (Vulkan `1.4.359`), side-by-side Python `3.12.10` (`.venv\Scripts\python.exe`).
- **Real X4 installation & build:** `X4 Foundations` **`9.00 (611726)`** (`X4.exe` PE file version `9.0.0.0`, SHA-256 `19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`).
- **Network DLL:** Packaged `x4lan_net_64.dll` (SHA-256 `6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0`).
- **Isolation & hygiene (`neon-cockpit-vel-win-04`):** Isolated `x4ls` view (`R:\x4ls-lanes\neon-cv-neon-cockpit-vel-win-04`), disposable launcher profile `45850`, windowed `800x600`, `fast=True`, `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`, local lockfile `C:\Users\Observer\Documents\Gemini Projects\.x4-native-test.lock`. Verified post-run: `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, `lockfile_removed=true`, `project_error_count=0`.
- **Explicit Input Provenance:**
  - **What ran natively in X4 9.00:** A single-machine Windows 11 isolated trial on disposable `custom_creative` save `save_009.xml.gz` (SHA-256 `0ab3a4261069919ae1a4d31a0f4d0f377f9ac6930bd42a33e8ccc06b292d3960`, sector `cluster_14_sector001_macro`, own ship `PBS-021` `0xae11d` at `(0,0,0)`, unoccupied Company `Pulsar Vanguard (GJS-263)` `0xb60a2` at `(0,0,1200)`, `basis_error=0.0`, stock target HUD locked `softtargetConnectionName="connectionui"`, `mode="hud"`, `is_active_pilot=true`).
  - **Traffic path:** Unmodified loopback `CompanyMotionService` + unmodified `ReconnectingCompanyMotionClient.observing` + OS PID-validated `WindowsMotionPeer` + admitted peer `CompanyMotionClient` streaming a **scripted V4 trajectory** (`REMOTE4`/`SEEN4`, `87/87` samples confirmed, `85` with velocity) into the **genuine opt-in receiving cockpit** (`X4LAN_COMPANY_COCKPIT_OBSERVER.new_multi_x4`, `X4LAN_COMPANY_COCKPIT_PLAYBACK.new_multi_x4` with `cockpit_continuous_playback=true, cockpit_native_velocity=true`, and `X4LAN_COMPANY_OBSERVER_STREAM.new`).
  - **What this is NOT:** This is **not** a live two-human or WAN flight, and the trajectory is scripted deterministic V4 traffic over authenticated local loopback rather than live keyboard/joystick input from a second player.

---

## 2. Small Opt-In Integration Delta

### Existing `src/authority/` files (`5 files, +141/-12`)
1. `src/authority/company_motion_source.py` (`+10/-1`): Added default-off `cockpit_native_velocity: bool = False` (requires `cockpit_radar_playback=True`) to `CompanyMotionConfig`; emits `cockpit_native_velocity = true` in Lua config only when explicitly enabled.
2. `src/authority/client_seat_overlay.py` (`+15/-1`): Added `COMPANY_COCKPIT_VELOCITY_UI` (`company_cockpit_velocity.lua`) and `COMPANY_COCKPIT_VELOCITY_MD` (`company_cockpit_velocity.xml`), packaged into `company_motion_ui` / `company_motion_md` **only** when `cockpit_native_velocity=True`.
3. `src/authority/lua/company_hull_playback.lua` (`+61/-4`): Added default-off `cfg.native_velocity == true` mode:
   - Passes `rendered.linear_velocity` from the existing `motion_buffer.lua` output (no new clock policy; falls back to `newest.rate` when `continuous=true`, `{0,0,0}` on `frozen` or held-without-rate) as an optional 6th argument to `cfg.write(...)`.
   - Keeps readback verification exact under native physics integration: evaluates error against the 1-frame segment `[target + prev_vel * dt_game, target + cur_vel * dt_game]` (using `dt_game = now - pending.at`), while preserving `raw_error` and `applied_velocity` in telemetry. Default `native_velocity=false` behavior is unchanged.
4. `src/authority/lua/company_hull_playback_x4.lua` (`+32/-2`): Added optional `options.velocity` hook:
   - After guarded `engine.write(...)` succeeds, calls `velocity:apply(binding, linear_velocity)`.
   - Inside `session:close(...)` (and on post-write guard failure), calls `velocity:stop(anchor_live_pose)` where `anchor_live_pose()` reads the ship's live pose and writes it via `engine.write` (same-sector, operational, non-own-ship guarded) immediately before dispatching MD `op="stop"`.
5. `src/authority/lua/company_cockpit_playback.lua` (`+23/-4`): Forwards `native_velocity`, passes optional 6th `linear_velocity` argument, logs `native_velocity_mps` and `raw_error` when present, and instantiates per-slot `X4LAN_COMPANY_COCKPIT_VELOCITY.new_x4(...)` inside `M.new_x4` and `M.new_multi_x4` **only** when `config.cockpit_native_velocity == true`.

### New opt-in production files (2 files)
1. `src/authority/md/company_cockpit_velocity.xml`: Cue `X4AuthorityCompanyCockpitVelocity` using **`constantvel="false"` exclusively** (zero `constantvel="true"`), independent `State.$cockpit` / `State.$cockpit2` / `State.$cockpit3` slot bindings (avoiding X4 MD table-key string assignment pitfalls found in `win-03`), registry/owner/unoccupied/aligned-basis/speed-bound (`5000 m/s`) guards on `op="set"`, and radar-independent `op="stop"` zeroing `<linear x="0" y="0" z="0"/>` so out-of-radar withdrawal still zeroes velocity.
2. `src/authority/lua/company_cockpit_velocity.lua`: `X4LAN_COMPANY_COCKPIT_VELOCITY` adapter with shared `RegisterEvent` receipt router, `CHANGE_EPS = 0.25 m/s` + `MIN_INTERVAL = 0.05 s` command throttling (exact zero never throttled), and idempotent `stop(anchor_fn)` emitting `AUTH_COMPANY_COCKPIT_VELOCITY_STOP` with `snapback`, `anchor_drift`, and post-stop `speed`.

### New tests & Windows trial harness (4 files)
- `tests/test_authority_company_cockpit_native_velocity_integration.py` — Offline MD XML contract, Python default-off config/overlay gating, and LuaJIT 2.1 integration suite.
- `tools/dev/authority_windows_cockpit_velocity_probe.xml` — Disposable save setup + read-only MD probe (`X4AuthorityCockpitVelProbe`).
- `tools/dev/authority_windows_cockpit_velocity_x4.lua` — Native X4 9.00 driver wiring the genuine opt-in receiving cockpit.
- `tools/dev/authority_windows_cockpit_velocity_trial.py` — Isolated Windows runner with DWM `PrintWindow` HUD capture.

---

## 3. Native Windows X4 `9.00 (611726)` Verification (`neon-cockpit-vel-win-04`)

- **Command:** `.venv\Scripts\python.exe tools\dev\authority_windows_cockpit_velocity_trial.py --out .local\neon-cockpit-vel-win-04 --network-dll .local\pkg-d0756d51\x4-authority-windows-diagnostic\network\x4lan_net_64.dll`
- **Outcome:** Exit `0`, `outcome="done"`, **`project_error_count=0`**, `87/87` `REMOTE4`/`SEEN4` confirmed (`85` with velocity), `850` playback writes / `849` readbacks, **`cockpit_position_deviation_count=0`**, max logged velocity-compensated readback error **`0.00259 m` (2.59 mm)** across all 31 throttled readback logs (while raw 1-frame native advance was `0.63–1.50 m`), **`vel_refusals=[]`**, **`own_ship_displacement_m=0.000000 m`**.
- **Local evidence hashes (uncommitted under `.local/neon-cockpit-vel-win-04/`):**
  - `result.json`: `2acf33af438ff2806be5108c00dcb12408320341aced1dde024ff29d2b9c9451`
  - `neon-cockpit-vel.log`: `453c2f7798253b083be7e868f7d901b808465a276712d62b45c100e959a17198`
  - Disposable save `save_009.xml.gz`: `0ab3a4261069919ae1a4d31a0f4d0f377f9ac6930bd42a33e8ccc06b292d3960`

### Stage-by-Stage Native Telemetry & Visually Verified Stock HUD Screenshots (`GJS-263`)

All 7/7 live DWM `PrintWindow` PNG frames (`1296x759`, `printwindow_ok=true`) were visually inspected directly:

| Stage | Game Time (s) | FFI Position `(x, y, z)` / Yaw | Requested vs Actual Displacement (Double-Move Check) | MD Forward Speed (`md_fwd_mps`) | `speed_conn` / `speed_empty` | Visually Confirmed Stock HUD (`GJS-263`) | Screenshot SHA-256 (prefix) |
| --- | ---: | --- | --- | ---: | --- | --- | --- |
| **0. Baseline stationary** | `3.07` | `(0.00, 0, 1200.00)` / `0.000°` | Stationary baseline | `0.0000 m/s` | `0` / `0` | **`1.2 km / 0 m/s`** (no lead pip) | `00_baseline_stationary.png` (`05461d0b84ecc939`) |
| **1. Forward `+60 m/s` (`3.0 s`)** | `6.30` | `(0.00, 0, 1379.15)` / `0.000°` | `180.000 m` req vs **`179.146 m`** actual (**`0.9953×`**, no double movement; `223` writes / `222` readbacks) | `59.7996 m/s` | `59` / `59` | **`1.3 km / 59 m/s`** | `01_forward_60mps.png` (`93e7721ececd9f82`) |
| **2a. Mid coupled turn (`0°→35°`, `60→90 m/s`)** | `7.97` | `(25.45, 0, 1492.74)` / `22.40°` (`0.390954 rad`) | Turning + ramping smoothly (`323` writes / `322` readbacks) | `78.9354 m/s` (lat `1.9e-6`) | `78` / `78` | **`1.5 km / 79 m/s`** + right-offset aim-lead circle | `02a_turn_mid.png` (`8c444af5b3d08019`) |
| **2b. End coupled turn (`35.0°`, `90 m/s`)** | `9.85` | `(115.08, 0, 1631.81)` / `35.000°` (`0.610865 rad`) | Stage 2: `273.863 m` req vs `276.857 m`; Stages 1+2 total: `444.021 m` req vs **`446.879 m`** actual (**`1.0064×`**, no double movement; `436` writes / `435` readbacks) | `89.6989 m/s` (lat `0.000`) | `89` / `89` | **`1.6 km / 89 m/s`** + live aim-lead circle | `02b_turn_90mps.png` (`546327152bb12fce`) |
| **3a. 5 s silent packet gap (mid-coast `+2.4 s`)** | `12.39` | `(246.10, 0, 1818.93)` / `35.000°` | Coasting cleanly (`588` writes / `587` readbacks) | `89.6995 m/s` | `89` / `89` | **`1.8 km / 89 m/s`** + live aim-lead circle | `03a_coasting_5s_gap.png` (`6c7d1237d33b920e`) |
| **3b. Post-`stale_5s` cutoff (`14.74 s` stop + `3` probes `15.65–17.83 s`)** | `15.65`–`17.83` | `(367.54, 0, 1992.37)` / `35.000°` | Total coast **`440.164 m`** (`~4.9 s × 90 m/s`, `1×`, `730` writes / `730` readbacks); **`anchor_drift=0.000492 m` (0.49 mm)**, **`post_cutoff_drift=0.000000 m`** | `0.0000 m/s` | `0` / `0` | **`2.0 km / 0 m/s`** (lead pip gone) | `03b_stopped_after_5s_cutoff.png` (`32363160c25210e8`) |
| **4a. Reacquired `gen2` forward `+60 m/s` (`2.0 s`)** | `19.92`–`20.42` | `(434.92, 0, 2088.59)` / `35.000°` | `reacquired_direct` (`dist=6.00 m`, `mode=hud`); `120.000 m` req vs **`117.459 m`** actual (**`0.9788×`**, no double movement; `848` writes / `847` readbacks) | `59.7996 m/s` | `59` / `59` | **`2.1 km / 59 m/s`** | `04a_reacquired_60mps.png` (`dcdf3e047d06a0e2`) |
| **4b. Explicit `END` withdrawal (`service.revoke(3)` at `20.45 s` + `3` probes `21.09–23.24 s`)** | `21.09`–`23.24` | `(436.05, 0, 2090.21)` / `35.000°` | `generation_revoked` -> stop (`850` writes / `849` readbacks); **`anchor_drift=0.000367 m` (0.37 mm)**, **`post_withdraw_drift=0.000000 m`** | `0.0000 m/s` | `0` / `0` | **`2.1 km / 0 m/s`** (lead pip gone) | `04b_stopped_after_withdrawal.png` (`1b200038700285fa`) |

### Key Native X4 Physics & Stop Observations

1. **No double movement (`0.9953×` forward, `1.0064×` coupled turn, `440.16 m` 5 s coast, `0.9788×` post-reacquire):**
   - Because `company_hull_playback.lua` writes the authoritative continuous trajectory coordinate via `SetObjectSectorPos` every `onUpdate` frame, and `constantvel="false"` only integrates physics across the single ~`12–17 ms` interval between frames before the next frame resets position to the continuous target, displacement stays strictly **1×** while X4's physics state holds the real velocity vector (`md_fwd_mps`, stock HUD speed readout, and weapon aim-lead pip).
   - Velocity-compensated readback error stayed within **`2.59 mm`** across all logged readbacks (vs. `0.63–1.50 m` raw 1-frame native advance), with `0` `position_deviation` alarms across `850` writes / `849` readbacks.
2. **No snap-back on stop — why `anchor_live_pose()` matters (`anchor_drift = 0.49 mm` / `0.37 mm`):**
   - At `14.74 s` (`stale_5s`) and `20.45 s` (`generation_revoked`), `session:close()` calls `anchor_live_pose()` (`SetObjectSectorPos` to the ship's live pose) immediately before signaling MD `op="stop"` (`constantvel="false" linear=(0,0,0)`).
   - Between Lua `anchor_live_pose()` and async MD `op="stop"` executing 1 frame (~`16.7 ms`) later (`14.76 s` / `20.47 s`), X4 physics advanced `1.499 m` (`90 m/s × 0.0167 s`) / `1.000 m` (`60 m/s × 0.0167 s`), and `constantvel="false" (0,0,0)` then returned the ship to within **`0.000492 m` (0.49 mm)** / **`0.000367 m` (0.37 mm)** of the anchored stop coordinate — with **`0.000000 m`** post-stop drift across the subsequent `~2.2 s` of probes. Without anchoring, as proved in our previous Windows target-speed trial (`75bc03eb`), releasing `constantvel="false"` snaps the ship back hundreds of meters to the last `SetObjectSectorPos`.
3. **MD bug caught and fixed on real X4 (`win-03` → `win-04`):**
   - On `win-03`, `State.$active.{$slot}` triggered X4 MD errors (`Failed to set table[].cockpit to value component.{...}`), causing `op="stop"` to refuse (`project_error_count=31`). Replacing the table lookup with explicit `State.$cockpit`, `State.$cockpit2`, `State.$cockpit3` variables resolved it completely (`project_error_count=0` in `win-04`).
4. **Pre-existing interaction noted for joint review (`win-03` observation, untouched by this patch):**
   - In `win-03`, calling `service.transport_lost(2)` immediately cleared the participant's grant and broadcast an empty `ROSTER` while `continuous_playback=true` was actively writing every frame. On the next frame (`~0.05 s` after `transport_lost`), `motion_playback.lua` marked the slot `uncertain=true` and `company_hull_playback_x4.lua:140` (`tracks_active_pilot()`) returned `false`, triggering `closed_uncertain` and permanently closing the slot for that session instead of coasting 5 s to `stale_5s`.
   - Per the directive to keep a small integration delta and leave policy changes for joint review, we left existing `closed_uncertain` / `tracks_active_pilot()` semantics completely untouched and tested Stage 3 via a genuine silent 5-second packet gap (peer stops streaming while still admitted -> full 5 s coast + `stale_5s`), plus Stage 4 via explicit `service.revoke(3)` (`END` -> `generation_revoked`). Whether an observer-side `ROSTER` drop during active continuous coasting should trigger immediate `closed_uncertain` or coast/clean-stop is flagged for joint review.

---

## 4. Offline Unit & LuaJIT Checks (Synthetic / Mocked MD — Not Native X4 Evidence)

All offline checks below ran on the final committed code with zero edits afterward:

1. **Python & LuaJIT (`lupa`) unit + integration suite:**
   - Command: `$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest -v tests/test_authority_company_cockpit_native_velocity_integration.py tests/test_authority_company_cockpit_playback.py tests/test_authority_company_motion_source.py tests/test_authority_company_motion_service.py`
   - Result: **`63/63` passed (`0` failures, `0` errors, `0` skips, `1.226 s`)**:
     - `tests/test_authority_company_cockpit_native_velocity_integration.py` (`5/5`): MD XML `constantvel="false"`-only contract, Python default-off config/overlay packaging, and LuaJIT 2.1 integration with realistic 1-frame physics advance + async MD delay + unanchored snap-back model covering forward motion, coupled turn, 0.4 s render gap, 5 s stale cutoff, explicit `END` withdrawal, pause, multi-pilot slot independence, and negative detection of artificial `2×` double movement.
     - `tests/test_authority_company_cockpit_playback.py` (`12/12`): Existing receiving-cockpit playback regressions.
     - `tests/test_authority_company_motion_source.py` (`17/17`): Existing motion config & observer stream regressions.
     - `tests/test_authority_company_motion_service.py` (`29/29`): Existing shared-service regressions.
2. **Standalone LuaJIT acceptance scripts (executed via `lupa` LuaJIT 2.1):**
   - `tests/authority_company_cockpit_continuous_playback.lua` (`v3` and `v4`) -> **PASSED (exit 0)**.
   - `tests/authority_company_hull_playback_x4.lua` -> **PASSED (exit 0)**.

---

## 5. Remaining Limits & Items Left for Joint Review

- **Not yet tested in this isolated run:** Live two-human or two-client WAN flight, travel-drive speeds (`> 500 m/s`), simultaneous 2–3 moving pilots on screen, and normal client launch flags/defaults (`cockpit_native_velocity` remains default-off and is not yet exposed in normal launcher scripts).
- **Clock / buffer policy:** Untouched (`motion_buffer.lua` and `motion_playback.lua` unchanged).
- **Joint-review item:** Behavior of `tracks_active_pilot()` -> `closed_uncertain` when `ROSTER` drops an active pilot mid-coast under `continuous_playback=true` (observed in `win-03`).

---

## 6. Hardening Follow-Up (`04:00 CET` Astra Review + Thread Reply)

In response to Astra's `04:00 CET` code review (`ts=1795748470.647449`) and `04:01 CET` thread follow-up (`ts=1795748518.630089`), the opt-in receiving-cockpit native velocity integration on `work/neon-cockpit-native-velocity` was hardened while keeping wire protocol, shared server, clock policy, and normal launch defaults **unchanged**:

### Hardening Changes
1. **Guarded `anchor_live_pose` & no snapback-prone raw stops (`company_hull_playback_x4.lua`, `company_cockpit_velocity.lua`):**
   - `anchor_live_pose()` now enforces `options.guard() == true`, `observer:matches(epoch, frame, index, actor_text, code)`, exclusive actor/slot binding (`exclusive_claims`), original local cockpit identity/sector/zone, original target identity/sector/macro/code/zone (`confirmed_zone` or valid `pending_zone`), finite live pose read, `destination_zone(sector, live) == target.zone`, and a pre-write recheck before calling `engine.write(...)` — including during stop after view loss.
   - `company_cockpit_velocity.lua:stop(anchor_fn)` refuses to dispatch an unanchored MD `stop` whenever `anchor_fn` is missing or fails; instead it retains an explicit uncertain cleanup outcome (`cleanup_uncertain=true`, `uncertain_reason`, `AUTH_COMPANY_COCKPIT_VELOCITY_UNCERTAIN`, and `closed_uncertain` in `company_cockpit_playback.lua`).
2. **Lease/generation receipt fencing, native target/zone/near-zero stop proof, and bounded pending receipts (`company_cockpit_velocity.xml`, `company_cockpit_velocity.lua`, `company_cockpit_playback.lua`):**
   - `company_cockpit_velocity.xml` tracks per-slot `$cockpit_lease`/`$cockpit_stopped` (`cockpit`, `cockpit2`, `cockpit3`), checks `$lease gt $stopped_lease`, `not @$active_ship.exists or ($active_ship == $ship and $active_lease == $lease)` on `set`, `$active_ship == $ship and $active_lease == $lease` on `stop`, `$ship.sector.macro.id == $args.$sector`, and `'%s'.[$ship.parent] == @$args.$zone`, and echoes `request|status|lease|...` in receipts (still strictly `constantvel="false"` only).
   - `company_cockpit_velocity.lua` fences delayed `set` and `stop` receipts across generation/slot reuse (ignoring stale receipts for new leases, while surfacing `late_set_after_stop` if an idle stopped ship unexpectedly reports nonzero velocity), checks native `actor`, `sector`, `zone`, `total_speed_mps <= 0.05 m/s`, and `anchor_drift_m <= 3.0 m` before confirming a stop, bounds pending receipts (`MAX_PENDING_PER_SLOT = 16`, `MAX_GLOBAL_PENDING = 64`, `RECEIPT_TIMEOUT = 1.5 s`), and surfaces explicit `closed_uncertain` when cleanup cannot be proved (timeout, refusal, mismatch, nonzero residual speed, or anchor failure).
   - Pure-Lua 64-bit decimal-to-hex `zone_text()` in `company_hull_playback_x4.lua` avoids `bit.tohex` and `ffi.new` quirks across LuaJIT environments (caught during intermediate `win-05`).
3. **Valid `3,000 m/s` continuous position preservation & acknowledged-only readback compensation (`company_cockpit_velocity.lua`, `company_hull_playback_x4.lua`, `company_hull_playback.lua`):**
   - Above `MAX_SPEED` (`2,500 m/s`, e.g., `3,000 m/s` travel drive), `velocity:apply()` skips cleanly if already stopped or performs a guarded stop if previously moving, keeping the continuous position session open and tracking positions cleanly.
   - `company_hull_playback.lua` credits native displacement compensation **strictly** from `acknowledged_velocity()` (MD-acknowledged receipts whose native `actor`/`sector`/`zone` and observed `sector_velocity` match the active lease); queued, skipped, throttled, or refused writes are never credited.
   - Explicit `END` revocation vs. 5-second silent packet gap semantics are preserved unchanged.

### Updated Offline Verification (Synthetic / Mocked MD — Not Native X4 Evidence)
- `$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest -v tests/test_authority_company_cockpit_native_velocity_integration.py tests/test_authority_company_cockpit_playback.py tests/test_authority_company_motion_source.py tests/test_authority_company_motion_service.py` → **`65/65` passed (`0` failures, `0` errors, `0` skips, `1.386 s`)**, adding dedicated LuaJIT suites for:
  - Ownership/view/cockpit/code/zone/exclusivity/pause/missing-anchor failures refusing raw unanchored stops and surfacing `closed_uncertain`,
  - Delayed `set`/`stop` receipt fencing across generation reuse, stop target/zone/near-zero/timeout/refusal verification, and bounded pending receipt capping + expiry pruning,
  - Valid `3,000 m/s` trajectories (both starting cleanly stopped and accelerating `600 → 3,000 → 400 m/s` with clean auxiliary stop + resume) preserving continuous position tracking, and queued/refused velocity writes never crediting displacement compensation.
- Standalone LuaJIT (`lupa`) acceptance scripts `tests/authority_company_cockpit_continuous_playback.lua` (`v3`/`v4`) and `tests/authority_company_hull_playback_x4.lua` → **PASSED**.

### Isolated Native Windows X4 `9.00 (611726)` Verification of Hardened MD/Lua (`neon-cockpit-vel-win-06`)
- **Command:** `.venv\Scripts\python.exe tools\dev\authority_windows_cockpit_velocity_trial.py --out .local\neon-cockpit-vel-win-06 --network-dll .local\pkg-d0756d51\x4-authority-windows-diagnostic\network\x4lan_net_64.dll --timeout 220`
- **Input provenance:** Identical single-machine isolated disposable `custom_creative` `save_009.xml.gz` (SHA-256 `df4638c38a63e102cfbb3d58faff233534bd9594978087f53985e92da184a66d`, own ship `BGV-797`, observed unoccupied Company `Pulsar Vanguard (VBU-536)`, zone `0xb07c0`, `basis_error=0.0`), authenticated loopback `CompanyMotionService` streaming scripted V4 traffic (`87/87` confirmed, `85` with velocity) into the genuine opt-in receiving cockpit. Not a two-human WAN flight.
- **Outcome:** Exit `0`, `outcome="done"`, **`project_error_count=0`**, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, `lockfile_removed=true`.
- **Local evidence hashes (`.local/neon-cockpit-vel-win-06/`):**
  - `result.json`: `f256cacbb2b37da1a8841976480311e6150153b2483706a28b7243b7c739e978`
  - `neon-cockpit-vel.log`: `da867241c34f3f1c1247450e93e0520d4cb9f58eedb1aee22b91b3ba44216ad2`
- **Verified results (all 7/7 live DWM `PrintWindow` HUD PNGs visually inspected):**
  - **Forward `+60 m/s` (`3.0 s`):** `178.041 m / 180.000 m` (`0.9891×`), `md_fwd=59.8632 m/s`, HUD visually **`1.3 km / 59 m/s`** (`01_forward_60mps.png`, `cb4e9b3d66bb4266`).
  - **Coupled turn (`0°→35°`, `60→90 m/s`):** yaw `35.000°`, `md_fwd=78.9355 → 89.6996 m/s`, cumulative **`444.605 m / 444.021 m`** (`1.0013×`), HUD visually **`1.5 km / 79 m/s` → `1.6 km / 89 m/s`** + live aim-lead circle (`02a_turn_mid.png` `d602508a5be9d95b`, `02b_turn_90mps.png` `5bd325d550b3a42b`).
  - **5-second silent gap & `stale_5s` cutoff (`lease=1`, `zone=0xb07c0`):** coasted **`440.489 m`** (`1×`, HUD **`1.8 km / 89 m/s`** + lead pip, `03a_coasting_5s_gap.png` `25cb31487a3694e2`), confirmed zero stop with **`anchor_drift=0.000372 m` (0.37 mm)** (`snapback=1.500587 m` 1-frame return to anchor), **`speed=0.000000 m/s`**, **`post_cutoff_drift=0.000000 m`** across `+2.2 s`, HUD visually **`2.0 km / 0 m/s`** (`03b_stopped_after_5s_cutoff.png`, `ff174e6042478c75`).
  - **`gen2` reacquisition & explicit `END` withdrawal (`lease=2`, `zone=0xb07c0`):** `reacquired_direct` (`dist=6.00 m`, `mode=hud`), forward `+60 m/s` **`117.649 m / 120.000 m`** (`0.9804×`, HUD **`2.1 km / 59 m/s`**, `04a_reacquired_60mps.png` `f90d18d50473690d`), `generation_revoked` confirmed zero stop with **`anchor_drift=0.000367 m` (0.37 mm)** (`snapback=0.999545 m` 1-frame return to anchor), **`speed=0.000000 m/s`**, **`post_withdraw_drift=0.000000 m`**, HUD visually **`2.1 km / 0 m/s`** (`04b_stopped_after_withdrawal.png`, `eaaca8a6d7734839`).
  - **Totals:** `899` writes / `898` readbacks under acknowledged-only velocity compensation, max readback error **`0.00251 m` (2.51 mm)** across all 31 logged readbacks, `cockpit_position_deviation_count=0`, `vel_refusals=[]`, `vel_fenced=[]`, `vel_uncertain=[]`, `own_ship_displacement_m=0.000000 m`, `project_error_count=0`.
  - *(Note on intermediate `win-05`: `win-05` hit MD refusals before replacing `bit.tohex` with pure-Lua `u64_decimal_to_hex` and `not @$active_ship.exists`, simultaneously exercising fail-closed behavior in real X4: continuous position tracking kept flying with `716` writes, `0` deviations, `max_err=0.053 mm`, zero unacknowledged velocity credited, and `stop_refused` sealed the slot via `closed_uncertain`.)*


---

## 7. Reconciliation onto `3154ae2f`, Review Fixes, Unequal-Clock Offline Check & Control-Loss Roster Native Trial (`05:00 CET` Follow-Up)

- **Branch:** `work/neon-cockpit-native-velocity-3154ae2f` (branched cleanly from `origin/trial/velocity-aware-playback-20261126` @ `3154ae2f45e155c905ef85467b319c3d696e016d`; `work/neon-cockpit-native-velocity` left untouched at `842266e5`).
- **Scope constraint honored:** Wire protocol (`REMOTE4`/`SEEN4`), shared server (`CompanyMotionService`), `motion_buffer.lua`, `motion_playback.lua`, `company_observer_stream.lua`, `company_cockpit_observer.lua`, `company_motion_source.lua`, clock policy, and normal launch/package defaults remain **unchanged** (strictly opt-in, default-off `cockpit_native_velocity=False`).

### Safeguard Preservation & Review Fixes
1. **Preserved `3154ae2f` trial safeguards (`73174762`, `9ced39e8`, `e2346578`):**
   - Preserved `roster_with_samples=self._cockpit_continuous_playback` in `company_motion_source.py` (`73174762`).
   - Preserved pre-dispatch refusal (`'not_dispatched'`) return and propagation across `company_hull_playback_x4.lua`, `company_cockpit_playback.lua`, and `company_hull_playback.lua` (`9ced39e8`), including `if continuous and (not recheck or not self:tracks_active_pilot()) then return false, nil, 'not_dispatched' end`, so an actual control-loss roster drop cleanly retires (`track_stopped`), anchors + zeroes auxiliary velocity, and leaves the slot reusable (`closed=false`) instead of sealing it uncertain.
   - Preserved `if pending_zone then return false end` without `'not_dispatched'` (`e2346578`) and gated `retire()` / `end_track()` / `paused` cleanup so any velocity cleanup failure seals `closed_uncertain` without emitting spurious recovery events.
2. **Review Case 1 (`company_cockpit_velocity.lua` — preserve possible prior physical motion across zero replies):**
   - Replaced dispatch-time clearing of `may_be_moving` with `active.possible_motion` + `active.last_nonzero_request`: dispatching an ordinary `0` velocity `set` never clears `possible_motion`, and expiring pending `set` receipts never clears `possible_motion`.
   - `active.possible_motion` clears **only** when a matching, independently confirmed near-zero receipt (`actor`/`sector`/`zone` match, `total_speed_mps <= 0.05 m/s`) arrives with `request_id > active.last_nonzero_request` (or on confirmed MD `stop`).
   - Verified offline for refused zero replies, missing (timed-out) zero replies, delayed zero replies (stopped before reply, confirmed before stop, superseded by newer nonzero `set`, and mismatched nonzero observed speed).
   - Confirmed conservative ownership checks on radar/view loss while moving: refuses unanchored `SetObjectSectorPos` writes (`0` writes, `0` raw MD stops), records explicit unresolved cleanup (`closed_uncertain`, `velocity_cleanup_uncertain=true`, `velocity_uncertain_reason="anchor_observer_mismatch"`) while the modeled target keeps coasting, and rejects subsequent samples/reacquisition with no broadening of authority.
3. **Review Case 2 (`company_hull_playback.lua` + `company_cockpit_velocity.lua` — established-duration readback certification):**
   - `velocity:acknowledged_velocity(binding, expected_zone)` now exposes `{x, y, z, game_time, request}` instead of only the vector.
   - Removed `segment_distance` over `[v0 * dt, v1 * dt]` in `company_hull_playback.lua`.
   - At pose write time (`t_w`), `write_acked_velocity` is recorded only if an independently acknowledged velocity already exists with `acked.game_time <= t_w + clock_eps` (bounded at `min(0.005 s, 5e-6 * max(1, t_w))` solely for MD `%s` 6-significant-digit `player.age` formatting).
   - At readback (`t_r`), when `write_acked_velocity == nil`, **zero** velocity displacement is credited even if a first native receipt arrived during `(t_w, t_r]`: Astra's synthetic timing repro (first `100 m/s` receipt stamped at `t_r = t_w + 0.2 s`, injected `20 m` offset) is left unconfirmed (`position_deviation` in continuous mode, `readback_out_of_bounds` in non-continuous mode). When `write_acked_velocity` is established and unchanged (`same_request`), readback evaluates single-point `dist3(actual, target + v0 * dt_game)`; when the rate changes from `v0` to `v1` during `(t_w, t_r]`, readback evaluates `max(dist3(actual, target + v0 * dt_game), dist3(actual, target + v1 * dt_game))` so any transition span exceeding tolerance remains unconfirmed while subsequent stable independently acknowledged motion confirms cleanly.

### Offline Unequal-Clock Measurement (Real Seconds vs. Game Seconds — Policy Unchanged)
Measured offline in LuaJIT 2.1 (`test_unequal_real_and_game_clocks_measurement_without_policy_change`, synthetic/mocked physics — not native X4 evidence) with `v = 100 m/s`, `dt_real = 0.05 s` (`20 Hz` render, `10 Hz` samples), `dt_game = k * dt_real`, without changing clock policy:

| Clock ratio `k = dt_game / dt_real` | Readback `max_err` (`m`) | `position_deviation` | Mean per-frame physics→setter handoff jump $\Delta_{\text{frame}}$ (`m`) | Analytical $\|v\|\cdot\|k-1\|\cdot dt_{\text{real}}$ (`m`) | Net disp. over `2.0 s` real (`m`) | `stale_5s` cutoff (`real s` / `game s`) | Confirmed zero stop |
|---|---:|---:|---:|---:|---:|---:|---|
| `0.0×` (frozen game clock) | `0.000000` | `0` | `5.000` | `5.000` | `190.000` | `4.95 s` / `0.00 s` | `yes` (`closed=false`) |
| `0.5×` (half-speed game clock) | `2.3e-13` | `0` | `2.500` | `2.500` | `190.000` | `4.95 s` / `2.475 s` | `yes` (`closed=false`) |
| `1.0×` (equal clocks) | `3.4e-13` | `0` | `0.00025` | `0.000` | `190.000` | `4.95 s` / `4.95 s` | `yes` (`closed=false`) |
| `2.0×` (2× game clock) | `2.3e-13` | `0` | `5.000` | `5.000` | `190.000` | `4.95 s` / `9.90 s` | `yes` (`closed=false`) |
| `6.0×` (SETA 6× game clock) | `4.5e-13` | `0` | `25.000` | `25.000` | `190.000` | `4.95 s` / `29.70 s` | `yes` (`closed=false`) |

- **Measured difference:** `company_hull_playback.lua` continues to pace cumulative trajectory (`190.000 m` across `2.0 s` real after interpolation delay) and the `5.0 s` prediction cutoff strictly in **real monotonic seconds**, while X4 `<set_object_velocity>` physics and readback verification (`target + v0 * dt_game`) integrate in **game seconds**. Under `k != 1.0`, readback certification remains exact (`~0 m` error, `0` deviations), but every render frame incurs a sawtooth handoff jump $\Delta_{\text{frame}} = |v| \cdot |k - 1| \cdot dt_{\text{real}}$ (forward overshoot + pull-back for `k > 1`, under-advance + catch-up for `k < 1`), and the 1-frame async MD stop return scales with $|v| \cdot k \cdot dt_{\text{real}}$. Clock policy was left unchanged for joint review.

### Offline Unit & LuaJIT Verification (Synthetic / Mocked MD — Not Native X4 Evidence)
- `$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest -v tests/test_authority_company_cockpit_native_velocity_integration.py tests/test_authority_company_cockpit_playback.py tests/test_authority_company_motion_source.py tests/test_authority_company_motion_service.py` → **`72/72` passed (`0` failures, `0` errors, `0` skips, `2.125 s`)**.
- Standalone LuaJIT (`lupa`) acceptance scripts `tests/authority_company_cockpit_continuous_playback.lua` (`v3`/`v4`), `tests/authority_company_hull_playback_x4.lua`, and `tests/authority_company_observer_stream.lua` → **PASSED (`3/3`)**.

### Isolated Native Windows X4 `9.00 (611726)` Verification (`neon-cockpit-vel-win-07`)
- **Command:** `.venv\Scripts\python.exe tools\dev\authority_windows_cockpit_velocity_trial.py --out .local\neon-cockpit-vel-win-07 --network-dll .local\pkg-d0756d51\x4-authority-windows-diagnostic\network\x4lan_net_64.dll --timeout 220`
- **Input provenance:** Single-machine Windows 11 isolated trial on disposable `custom_creative` `save_009.xml.gz` (SHA-256 `f9508e426f367bd4b937a989842359148c50c2c9b3e20d521e2cf43cddee066e`, own ship `GCY-933` `0xafae4`, observed unoccupied Company `Pulsar Vanguard (BIF-530)` `0x11666e`, zone `0xafae9`, `basis_error=0.0`), authenticated loopback `CompanyMotionService` streaming scripted V4 traffic (`108/108` `REMOTE4`/`SEEN4` confirmed across `gen1`/`gen2`/`gen3`, `105` with velocity) into the genuine opt-in receiving cockpit (`cockpit_continuous_playback=true`, `cockpit_native_velocity=true`, `constantvel="false"`). Not a two-human WAN flight.
- **Outcome & hygiene:** Exit `0`, `outcome="done"`, **`project_error_count=0`**, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, `lockfile_removed=true`.
- **Local evidence hashes (`.local/neon-cockpit-vel-win-07/`):**
  - `result.json`: `2bc90bdf03bca44479e9572de96649b089cc0eb2c7aab9497e4787a4db764b04`
  - `neon-cockpit-vel.log`: `34832156f41deb2f73b4cc27081cec5ed06b9420133dccfa7bf01547608c0799`
- **Verified native results (all 10/10 live DWM `PrintWindow` HUD PNGs visually inspected):**
  1. **Baseline & Forward `+60 m/s` (`3.0 s`):** Stationary `1.2 km / 0 m/s` (`00_baseline_stationary.png` `036f8dbb`); forward `178.92 m / 180.00 m` (`0.9940×`, `md_fwd=59.80 m/s`, HUD **`1.3 km / 59 m/s`**, `01_forward_60mps.png` `96baa721`).
  2. **Coupled turn (`0°→35°`, `60→90 m/s`):** Yaw `35.000°`, `md_fwd=79.01 → 89.70 m/s`, cumulative `446.95 m / 444.02 m` (`1.0066×`, HUD **`1.5 km / 79 m/s` → `1.6 km / 89 m/s`** + live aim-lead circle, `02a_turn_mid.png` `0610859b`, `02b_turn_90mps.png` `c624af95`).
  3. **Silent motion-loss (5-second packet gap while pilot remains admitted in `ROSTER`):** Coasted **`440.56 m`** at `89.70 m/s` (`1×`, HUD **`1.8 km / 89 m/s`** + lead pip, `03a_coasting_5s_gap.png` `3f76ddbb`); at `5.0 s` (`14.79 s`), `stale_5s` dispatched guarded `anchor_live_pose` + MD `stop` (`lease=1`, `zone=0xafae9`), settling at **`anchor_drift=0.000525 m` (0.53 mm)** (`snapback=1.500 m` 1-frame return to anchor), **`speed=0.000000 m/s`**, **`post_cutoff_drift=0.000000 m`** across `+2.2 s`, `pb_active=false`, `pb_closed=false`, `pb_vel_uncertain=false`, `is_active_pilot=true`, HUD **`2.0 km / 0 m/s`** (`03b_stopped_after_5s_cutoff.png` `e70cfc16`).
  4. **Actual control-loss roster (`gen2` `+60 m/s` active velocity → `service.transport_lost(3)`, NO `END` packet):** `gen2` `reacquired_direct` (`dist=6.00 m`), flew `118.36 m / 120.00 m` (`0.9863×`, `md_fwd=59.80 m/s`, HUD **`2.1 km / 59 m/s`**, `04a_gen2_moving_60mps.png` `bf07a9ba`). On `service.transport_lost(3)`, server sent `motion_hidden` + empty `ROSTER` (no `END`); X4 dropped `is_active_pilot=false` and within `~0.02 s` (`20.60 → 20.61 s`, without waiting 5 s) `9ced39e8` pre-dispatch refusal (`'not_dispatched'`) retired cleanly via `track_stopped` (`playback_failed`), running guarded `anchor_live_pose` + MD `stop` (`lease=2`, `zone=0xafae9`): **`anchor_drift=0.000419 m` (0.42 mm)** (`snapback=0.999 m` 1-frame return to anchor), **`speed=0.000000 m/s`**, **`post_closs_drift=0.000000 m`** across `+2.2 s`, HUD **`2.1 km / 0 m/s`** (`04b_stopped_after_control_loss_roster.png` `ca05e02a`), and **`pb_closed=false`, `pb_vel_uncertain=false` (slot remained reusable, zero `closed_uncertain`)**.
  5. **Slot-reuse proof (`gen3` reacquisition after control-loss roster) + explicit `END` withdrawal (`service.revoke(4)`):** `gen3` `reacquired_direct` (`dist=6.00 m`, `pb_reacq=2`), flew `118.37 m / 120.00 m` (`0.9864×`, `md_fwd=59.80 m/s`, HUD **`2.2 km / 59 m/s`**, `05a_gen3_reacquired_60mps.png` `c5ae4ce6`). On `service.revoke(4)` (`generation_revoked` at `26.02 s`), guarded `anchor_live_pose` + MD `stop` (`lease=3`, `zone=0xafae9`) halted with **`anchor_drift=0.000300 m` (0.30 mm)** (`snapback=1.000 m` 1-frame return to anchor), **`speed=0.000000 m/s`**, **`post_withdraw_drift=0.000000 m`** across `+2.2 s`, HUD **`2.2 km / 0 m/s`** (`05b_stopped_after_end_withdrawal.png` `51adb103`).
  - **Totals:** `1,021` writes / `1,020` readbacks, **`cockpit_position_deviation_count=0`**, `34/34` established-velocity logged readbacks `max_err=0.0319 m` (and the `3` unestablished gen-start frames honestly evaluated uncompensated at `0.054 mm`, `1.001 m`, `0.998 m`), `vel_refusals=[]`, `vel_fenced=[]`, `vel_uncertain=[]`, `own_ship_displacement_m=0.000000 m`, `project_error_count=0`.


---

## 8. Callback-Time Receipt Expiry & Pending-STOP Handoff Serialization (`06:00 CET` Follow-Up)

In response to Astra's `06:00 CET` review of `cea083fb` (`#x4-authorative-server`, `ts=1795755673.337229` + Reply 1 `ts=1795755728.481269`), two additional edge cases were fixed on `work/neon-cockpit-native-velocity-3154ae2f` while keeping wire protocol, shared server, clock policy, and normal launch/package defaults **unchanged** (strictly opt-in, default-off `cockpit_native_velocity=False`):

### Fixes
1. **Callback-time receipt expiry check (`src/authority/lua/company_cockpit_velocity.lua`):**
   - `meta.handler(text)` now checks `now_cb = clock()` against `meta.sent_at` and `receipt_timeout` (`not finite(now_cb) or now_cb < meta.sent_at or (now_cb - meta.sent_at) >= receipt_timeout`) **inside the callback itself** before accepting ANY `SET`, `STOP`, ordinary `0` `SET`, or out-of-order reply — even when no housekeeping (`tick()`/`apply()`/`stop()`) has run yet.
   - Expired `STOP` replies (`+2.0 s` past `1.5 s` timeout) clear `pending_stops[meta.lease_id]`, set `ok=false, expired=true, fenced=true, confirmed_zero=false, fail_reason='stop_receipt_timeout'`, call `mark_uncertain('stop_receipt_timeout', meta)`, and settle stop waiters as failed (never reporting clean confirmed cleanup).
   - Expired `SET` replies (nonzero, ordinary `0`, or out-of-order) increment `stats.timeouts` and `stats.fenced_receipts`, never update `active.acked_velocity` or `active.last_acked_request`, preserve `active.possible_motion=true` (so subsequent `stop()` still anchors & dispatches `STOP`), and still surface `late_set_after_stop` if an expired nonzero `SET` arrives after `active == nil`.
   - `prune_pending(now)` now uses the same regressed-clock guard (`now < entry.sent_at or (now - entry.sent_at) >= receipt_timeout`) and settles pending-stop waiters on expiry; `pending_stops[lease.id]` is also registered before `pcall(issue, args)` so synchronous stop callbacks decrement `pending_stop_count` cleanly.
2. **Serialized same-actor/slot handoff across pending `STOP` cleanup (`src/authority/lua/company_cockpit_velocity.lua`, `src/authority/lua/company_hull_playback_x4.lua`, `src/authority/lua/company_cockpit_playback.lua`):**
   - `company_cockpit_velocity.lua:apply()` refuses new `set` dispatches with `false, 'stop_pending'` whenever `pending_stop_count > 0`, and exposes `on_pending_stops_settled(cb)`.
   - `company_hull_playback_x4.lua` keeps `claims.by_actor[actor_text]` and `claims.by_slot[slot]` held with `claim.pending_stop = true` across `session:close()` until the dispatched `STOP` settles:
     - On confirmed zero cleanup within timeout: `release_claim()` runs and the next generation cleanly binds and dispatches pose + velocity.
     - On failed, expired (in-callback or cross-slot `claim.tick()`), or unanchored (view-loss / ownership-loss) cleanup: `fence_actor(reason)` permanently records `claims.fenced_actors[actor_text] = reason`, blocking BOTH the original slot (`velocity_cleanup_uncertain`) AND any other slot sharing `exclusive_claims` (`actor_fenced`) from binding or dispatching pose/velocity to that ambiguous actor.
     - `session:write()` and `session:align()` also guard against writing a conflicting pose while `velocity:has_pending_stop()` is active.
   - `company_cockpit_playback.lua:sample()` defers cleanly (`false, 'stop_pending'`, emitting `handoff_deferred_stop_pending`) without faulting, writing pose/velocity, or consuming `reacquire_by_index` while a prior guarded `STOP` is awaiting confirmation.

### Offline Verification (Synthetic / Mocked MD — Not Native X4 Evidence; No Native X4 Re-Run Required Per Directive)
- `$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest -v tests/test_authority_company_cockpit_native_velocity_integration.py tests/test_authority_company_cockpit_playback.py tests/test_authority_company_motion_source.py tests/test_authority_company_motion_service.py` → **`74/74` passed (`0` failures, `0` errors, `0` skips, `2.229 s`)**, including two new LuaJIT 2.1 regression suites:
  - `test_callback_expiry_for_set_stop_zero_and_out_of_order_without_housekeeping`: verifies `+2.0 s` expiry inside the callback before housekeeping for nonzero `SET`, `STOP`, ordinary `0` `SET`, in-window and expired out-of-order replies, and expired late nonzero `SET` after `STOP`.
  - `test_pending_stop_serializes_same_actor_handoff_and_fences_ambiguous_actor`: proves zero pose (`SetObjectSectorPos`) and zero velocity (`MD set`) dispatches for a new generation on both the same slot and a second slot while `gen1` `STOP` is unacknowledged; clean handoff once `STOP` confirms; and permanent cross-slot actor fencing (`actor_fenced` + `closed_uncertain`) when `STOP` expires in-callback (`+2.0 s`), expires silently before slot 1 ticks, or fails synchronously on view loss.
- Standalone LuaJIT (`lupa`) acceptance scripts `tests/authority_company_cockpit_continuous_playback.lua`, `tests/authority_company_hull_playback_x4.lua`, and `tests/authority_company_observer_stream.lua` → **PASSED (`3/3`)**.
