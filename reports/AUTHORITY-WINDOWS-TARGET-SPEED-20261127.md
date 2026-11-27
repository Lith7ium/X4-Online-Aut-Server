# Windows Target-Speed and Native Velocity Investigation (2026-11-27)

- **Branch:** `work/neon-windows-target-speed` (base `trial/velocity-aware-playback-20261126` @ `7c5e6417`)
- **Contributor:** Neon (James Molander / `von-Benette`)
- **Host & X4 Build:** Windows 11, AMD Radeon RX 9070 XT (Vulkan `1.4.359`), Python `3.12.10`, X4 Foundations **`9.00 (611726)`** (`X4.exe` PE `9.0.0.0`, SHA-256 `19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`), packaged `x4lan_net_64.dll` (`6ad97e04fd804c031b673af7259e4010b0afb2ad095e8821647998a69e2809e0`, exporting `udp.monotonic()`).
- **Scope:** Wire protocol, shared server, and default client behavior are **unchanged** (zero edits to existing `src/` files). All new code is opt-in and default-off.

## Key Findings (Native Windows 11 X4 `9.00 (611726)`)

1. **Exact stock HUD target-speed path & resolution of the Linux lateral/connection-name open question:**
   - Selecting the observed unoccupied Company Pulsar Vanguard (`FSL-392`, actor `0x176fab`, `cluster_14_sector001_macro`, `basis_error = 0.0`) via `C.SetSofttarget(actor, '')` resolves `C.GetSofttarget2().softtargetConnectionName = "connectionui"` (`messageID = 6477`).
   - Across **every** test stage, `C.GetComponentDetails(actor, "connectionui").speed` and `C.GetComponentDetails(actor, "").speed` returned **identical integer values** on every sample.
   - X4's stock target-bracket speed text (`ui/core/lua/targetsystem.lua:updateSideText2`) renders **`trunc(v_sector · ship_forward)`** (the signed forward-axis velocity projection, truncated toward zero — e.g. `60.0` float32 reads `59 m/s`, `60.341` reads `60 m/s`, `89.914` reads `89 m/s`, `-60.0` reads `-59 m/s`):
     - **Pure lateral `+60 m/s`** (`172.292 m` sideways displacement over `2.8715 s`): both `"connectionui"` and `""` returned **`0`**, and the live cockpit HUD literally displayed **`0 m/s`** — *even while X4's weapon aim-lead pip visually offset to the right of the ship*, proving the engine sees the full 3D lateral velocity while the target-speed field intentionally reports only the forward projection. This resolves the open Linux lateral question in [`AUTHORITY-HUMAN-PLAYTEST-20261126.md`](AUTHORITY-HUMAN-PLAYTEST-20261126.md#what-x4-reports-as-target-speed): Linux's `0` during lateral motion was not caused by an empty connection name.
     - **Diagonal `+60 m/s` forward + `+60 m/s` lateral** (`|v| = 84.8528 m/s`): HUD displayed **`59 m/s`** (the forward projection, not total speed).
     - **Reverse `-60 m/s` forward:** HUD displayed **`-59 m/s`** (signed).
   - `GetPlayerSpeed()` returned `ch_tgt_spd = 0` / `match_spd = nil` throughout because the player's own Match-Speed mode was inactive; the right-hand Target Monitor displays ship name/ID (`Pulsar Vanguard (FSL-392)`), Commander (`None`), Hull (`100%`), Shield (`100%`), Storage, and Crew (speed is shown only on the target bracket).

2. **`constantvel="true"` vs. `constantvel="false"` on an unoccupied Company ship:**
   - Both `<set_object_velocity constantvel="true">` (LAN native velocity approach) and `constantvel="false"` produced identical steady `60.0000 m/s` flight (`174.268 m / 2.9045 s` and `144.038 m / 2.4007 s`) with zero decay on the unoccupied ship and identical HUD display (`59 m/s`).

3. **Turning while moving (uncoupled vs. coupled) & attitude alignment requirement:**
   - Rotating the ship via `SetObjectSectorPos` (`+1.5 m` lateral shift, yaw `+12°`, `+30°`, `+90°`) while keeping the original sector velocity `(0, 0, 60)` left the sector velocity intact (`(0, 0, 60.0000)`), and the stock HUD tracked `trunc(60 * cos(yaw))`: **`58 m/s`** at `12°` (`58.689 m/s`), **`51 m/s`** at `30°` (`51.962 m/s`), **`0 m/s`** at `90°` (`0.000 m/s`).
   - Implication for multiplayer playback: keeping **V4 attitude (`yaw/pitch/roll`) aligned with the native linear velocity vector** is mandatory for an accurate HUD target-speed readout during turns; unaligned attitude attenuates the displayed target speed by `cos(heading_error)`.

4. **Five-second packet gap & stop behavior (snap-back reproduced and eliminated):**
   - **Control without pose anchor (Stage 4b):** Releasing velocity (`constantvel="false"`, `0,0,0`) without first anchoring the moved pose caused `FSL-392` to snap backwards **`185.093 m`** instantaneously to its last explicit `SetObjectSectorPos` coordinate (`1506.510 -> 1326.422`), confirming the same engine snap-back hazard observed on Linux (`252.41 m` / `492.561 m`).
   - **Safe stop via opt-in `X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY` (Stage 5):** Driven by real unmodified loopback `CompanyMotionService` V4 traffic (`REMOTE4`/`SEEN4`, `14/14` samples confirmed, OS PID-validated `WindowsMotionPeer` `0.0.0.0:61608` PID `6456`, HUD target locked in `mode='hud'`), `FSL-392` executed a coupled `0° -> 30°` turn while ramping `60 -> 90 m/s`, then coasted **`433.874 m`** on native physics during a real 5.0-second packet gap (`114` steady readbacks: median prediction error `3.28 mm`, max `8.43 mm`; HUD displayed `89 m/s`). At `5.0 s` monotonic real time (`stale_5s`), **`anchor() -> zero -> stop -> release_readback`** froze the ship cleanly at `(262.872, 0, 1767.122)` with **`0.00000 m` snap-back** on both `zero` and `stop`, `observed_near_zero = true` (`0.000 m/s`), **`0.00000 m` post-stop drift** across `5.66 s / 6.47 s / 7.28 s` probes and final MD readback (`+2.80 s` later), HUD displaying **`0 m/s`**, and own ship `WFX-661` at **`0.00000 m`** displacement throughout. *(A `1.501 m` forward advance occurred between Lua `anchor()` and MD `zero` executing ~16.7 ms later at `90 m/s`; `zero` and `stop` then held the final reached coordinate with `0.00000 m` snap-back.)*

## Native Run Evidence (`neon-target-speed-win-03`)

- **Input source (explicitly labeled):** Disposable local save `save_009.xml.gz` (SHA-256 `a4e2428c86c38ff66143860303fea5a3394f91dd674f26bfb7244c86fa28c4c2`) created from `custom_creative` in disposable profile `45850` inside isolated `x4ls` view (`800x600`, `fast=True`, `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`). Stages 1–4 issue direct MD/Lua velocity/pose inputs on the selected target; Stage 5 streams a synthetic V4 turning/ramping trajectory through unmodified loopback `CompanyMotionService` -> `CompanyMotionClient` -> `CompanyMotionObserver` -> `company_observer_stream.lua` -> `X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY`. This is a single-machine Windows X4 9.00 investigation, not a two-human WAN flight.
- **Local artifacts (uncommitted under `.local/neon-target-speed-win-03/`):**
  - `result.json` SHA-256 `520879a8d6d27edf40b260cfed11a6af8ed428f435b1e6d38b959b81ca263d75`
  - `neon-target-speed.log` SHA-256 `38acabd518d84a49bdeb7efcfa1f343be46c10a89f859fa13c8a94725bedddb7`
  - Hygiene: `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, `lockfile_removed=true`, `project_error_count=0`.

| Stage | Input / Velocity | Measured Displacement & Native Velocity | `GetComponentDetails.speed` (`connectionui` / `""`) | Visually Verified Cockpit HUD Display (DWM `PrintWindow` Frame SHA-256) |
| --- | --- | --- | --- | --- |
| 0. Baseline | Stationary, HUD locked (`SetSofttarget`) | `0.000 m`; `loc=(0, 0, 0)` | `0` / `0` | **`1.2 km` / `0 m/s`** (`00_baseline_stationary.png`, `fd1aba2e269337ab`) |
| 1. Position-only control | `SetObjectSectorPos` `36.0 m` fwd, no velocity | `36.000 m`; `loc=(0, 0, 0)` | `0` / `0` | **`1.2 km` / `0 m/s`**, no lead pip (`01_position_only_control.png`, `33c730b57347179d`) |
| 2a. Forward (`constantvel=true`) | `+60 * forward` | `174.268 m` in `2.9045 s`; `fwd=60.000 m/s` | `59` / `59` | **`1.3 km` / `59 m/s`** (`02a_forward_60mps_constvel_true.png`, `1e09a257f80e433f`) |
| 2b. Forward (`constantvel=false`) | `+60 * forward` impulse | `144.038 m` in `2.4007 s`; `fwd=60.000 m/s` (no decay) | `59` / `59` | **`1.5 km` / `59 m/s`** (`02b_forward_60mps_constvel_false.png`, `d4384c5b1da166a4`) |
| 3a. Pure lateral | `+60 * right` (`constantvel=true`) | `172.292 m` in `2.8715 s`; `lat=60.000`, `fwd=0.000` | `0` / `0` | **`1.6 km` / `0 m/s`** + **live right-offset aim-lead pip** (`03a_lateral_60mps.png`, `07ebcae6714a0cf1`) |
| 3b. Diagonal | `+60*fwd + 60*right` (`|v|=84.853 m/s`) | `loc=(60, 0, 60)`; `fwd=60.000 m/s` | `59` / `59` | **`1.7 km` / `59 m/s`** + offset lead pip (`03b_diagonal_60fwd_60lat.png`, `f70e64040d7f23dc`) |
| 3c. Reverse | `-60 * forward` | `loc=(0, 0, -60)`; `fwd=-60.000 m/s` | `-59` / `-59` | **`1.7 km` / `-59 m/s`** (`03c_reverse_minus60mps.png`, `9d5ca40877524aa7`) |
| 4a. Uncoupled turns | `+1.5 m` shift + yaw `+12°`, `+30°`, `+90°` at `v=(0,0,60)` | `sec_vel=(0,0,60)` retained; `fwd=58.689 / 51.962 / 0.000` | `58` / `58`, `51` / `51`, `0` / `0` | **`1.3 km` / `58 m/s`** (`0784070f2e1b1fb1`), **`1.3 km` / `51 m/s`** (`5afef4b7111d666c`) |
| 4b. Unsafe stop control | Release `constantvel=false` **without** pose anchor | **Snapped back `185.093 m`** (`1506.510 -> 1326.422`) | `0` / `0` | Confirmed Windows snap-back parity with Linux |
| 5a. V4 loopback coupled turn | Unmodified `REMOTE4`/`SEEN4` (`14/14`), `0°->30°`, `60->90 m/s` | `fwd` tracked `60.34 -> 89.91 m/s` across `13` dispatches | `60 -> 86 -> 89` (both) | **`1.4 km` / `86 m/s`** (`05a_v4_turn_90mps.png`, `d1bbe398761a95bf`) |
| 5b. 5-second packet gap coast | `transport_lost(2)` after `seq=14` | Coasted `433.874 m`; `114` steady readbacks max drift `8.43 mm` | `89` / `89` | **`1.6 km` / `89 m/s`** (`05b_coasting_in_5s_gap.png`, `aa7d52d52c18eb16`) |
| 5c. Safe stop at 5.0 s (`stale_5s`) | `anchor -> zero -> stop -> release_readback` | `snapback=0.00000 m`, `tot=0.000 m/s`, `post_drift=0.00000 m`, `own_disp=0.00000 m` | `0` / `0` | **`1.8 km` / `0 m/s`** (`05c_stopped_after_5s_gap.png`, `08966b1c3174f79d`) |

*(An earlier shake-out run `neon-target-speed-win-02` on `ENT-396` produced identical behavior across all stages, including `182.138 m` snap-back without anchor vs. `0.00000 m` snap-back and `90 m/s -> 0 m/s` across a `431.812 m` 5 s coast with `117` steady readbacks max drift `8.45 mm`, before tightening the error-log filter and post-save banner delay for `win-03`.)*

## Focused Opt-In Patch & Offline Verification

New default-off files added on `work/neon-windows-target-speed` (zero modifications to existing wire/server/default files):

- `src/authority/md/company_cockpit_native_velocity.xml` — default-off MD cue `X4AuthorityCompanyCockpitVelocity` supporting `baseline`, `apply` (`constantvel=true` default, optional `false`), `readback`, `zero`, `stop`, `release_readback`, plus the `0.31 s` watchdog release.
- `src/authority/lua/company_cockpit_native_velocity.lua` — default-off `X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY` companion supporting V3 and V4 samples, speed-change transition frame classification, stock HUD target-speed inspection (`read_hud_target_speed_x4` + `format_hud_speed`), `make_x4_anchor`, and enforced `anchor -> zero -> stop -> release_readback` ordering on **all** stop reasons (`stale_5s`, `explicit_loss`, `paused`, `view_or_roster_lost`, `binding_changed`, `clock_ratio_out_of_range`).
- `tests/test_authority_company_cockpit_native_velocity.py` — offline LuaJIT 2.1 (`lupa`) unit tests (synthetic/mocked MD, not native X4 evidence).
- `tools/dev/authority_windows_target_speed_{probe.xml,x4.lua,windows.py}` — isolated Windows X4 9.00 investigation harness.

### Offline Unit Checks (Synthetic / Mocked MD)

```powershell
$env:PYTHONPATH='src'
.venv\Scripts\python.exe -m unittest -v tests/test_authority_company_cockpit_native_velocity.py tests/test_authority_company_cockpit_playback.py tests/test_authority_company_motion_service.py
```

- **Result:** `45/45` passed (`0` skips, `0.959 s`) — `4` new native-velocity LuaJIT suites (including 3 subtests for non-stale stop anchor enforcement) + `12` Company cockpit playback regressions + `29` Company motion service regressions.

## Remaining Limits

- Tested on a single local Windows 11 host with a disposable `custom_creative` save and loopback V4 traffic; live two-pilot WAN flight, cross-zone transitions during active native velocity hold, and continuous integration into the default `-CompanyCockpitContinuousPlayback` production overlay remain separate review/integration steps.
- `anchor()` executes in Lua while `zero` executes asynchronously in MD (~1 frame later), adding ~1.2–1.5 m of forward coast at `90 m/s` before the stop freezes; moving pose anchoring into the same MD cue or re-anchoring after `zero` can eliminate that 1-frame gap if desired.
