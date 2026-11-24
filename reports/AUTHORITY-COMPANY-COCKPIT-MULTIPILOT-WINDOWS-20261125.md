# Multi-pilot receiving-cockpit Company ship playback — up to three other pilots in one cockpit (25 Nov 2026)

Author: Neon (James Molander / `von-Benette`).
Branch: `work/neon-company-cockpit-multipilot` (built directly on Astra's `1611dbe1` integration baseline, carrying `346bd5f1` launcher/QPC wiring and `1611dbe1` next-update alignment readback).
Host: Windows 11 PC (separate machine from Cobalt and Lith7ium), Python 3.12.10 (`.venv`), AMD Radeon RX 9070 XT, X4 9.00 pinned executable (`SHA-256 19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`).

## Scope delivered (per 00:00 CET directive + 00:13 CET baseline alignment)

1. **Up to three sticky cockpit pilot slots (`cockpit`, `cockpit2`, `cockpit3`) with preserved independent `map` interest:**
   - `src/authority/md/company_cockpit_observer.xml` + `src/authority/lua/company_cockpit_observer.lua`: added read-only `mode='multi'` (`max_pilots` in `1..3`) prioritizing the selected HUD target (`hud`/`hudmap`) plus up to three distinct nearby unoccupied Company ships within `player.controlled.currentradarrange`, verified two-way against `X4AuthorityShipRegistry` and Lua FFI.
   - **Sticky slot assignment + real LuaJIT `uint64_t` `cdata` bug fix (discovered on native run `win-01`):** Native run `win-01` revealed that LuaJIT hashes FFI `uint64_t` (`UniverseID`) `cdata` table keys by pointer identity rather than numeric value, causing slots to swap whenever ships' relative distances crossed. Fixed by keying sticky matching on Lua integer `index` (and `seen_actor` on `tostring(actor_id)`) while checking exact `hull.actor == existing.hull.actor` value equality, plus an explicit `ffi.new('uint64_t', ...)` regression test.
   - `src/authority/lua/company_observer_stream.lua`, `src/authority/lua/company_hull_playback.lua`, `src/authority/lua/company_hull_playback_x4.lua`: dynamically enable `cockpit2`/`cockpit3` slots alongside `cockpit` while keeping `'map'` strictly read-only and rejected by playback adapters.
2. **Independent per-ship loss, generation, revocation, and reacquisition state (`src/authority/lua/company_cockpit_playback.lua`, `src/authority/company_motion_observer.py`, `src/authority/company_motion_service.py`, `src/authority/company_motion_client.py`, `src/authority/company_motion_reconnect.py`):**
   - `X4LAN_COMPANY_COCKPIT_PLAYBACK.new_multi` / `new_multi_x4` runs an independent `X4LAN_COMPANY_HULL_PLAYBACK` controller per cockpit slot with shared per-ship `revoked_by_index`, `reacquire_by_index`, and `last_by_index_gen` ledgers (preserving Astra's `1611dbe1` next-update `update_started_at` alignment readback check and `346bd5f1` Windows QPC `time.perf_counter()` timestamp alignment).
   - `CompanyMotionService` / `CompanyMotionClient` / `ReconnectingCompanyMotionClient` support opt-in `cockpit_playback_limit` (`1..3`) with per-ship `motion_hidden` / `motion_end` / `resume_routes` while preserving all existing single-ship `trial_index` contracts unchanged.
   - Own-ship control remains strictly local (`index ~= own_index`, `target.actor ~= cockpit.actor`).
3. **Exposed through normal Linux and Windows player launchers:**
   - Added `--company-cockpit-playback` to `tools/dev/authority_linux_player_client.py` (invoked by `tools/run_authority_client.sh`) and `tools/dev/authority_windows_fixture_client.py`, and `-CompanyCockpitPlayback` to `tools/run_authority_windows_client.ps1`, alongside Astra's existing single-ship `--company-cockpit-playback-index` / `-CompanyCockpitPlaybackIndex` and `--company-hull-playback-index` / `-CompanyHullPlaybackIndex` (mutually exclusive, requiring `--company-motion` / `-CompanyMotion`).
   - Updated `docs/AUTHORITY-SERVER-PACKAGE.md` and `docs/AUTHORITY-WINDOWS-CLIENT-PACKAGE.md`; verified `tools/make_authority_windows_client_package.py` packages and exercises the multi-pilot overlay cleanly.

## Honest separation of evidence

- **Native Windows X4 9.00 (`neon-multipilot-win-03`):** Real native Windows X4 9.00 game (`custom_creative`, disposable profile `45850`, isolated `x4ls` view, `800x600`, `fast=True`, `HWND_BOTTOM`), running the **real production Lua & MD modules** (`company_cockpit_observer.{xml,lua}`, `company_cockpit_playback.lua`, `company_hull_playback*.lua`, `company_observer_stream.lua`, `motion_buffer.lua`, `motion_playback.lua`) over PID-validated loopback UDP (`x4lan_net_64.dll`) connected to real loopback `CompanyMotionService` + `ReconnectingCompanyMotionClient.observing(...)` + `CompanyMotionSource(cockpit_radar_playback=True)` + `CompanyMotionObserver`. **Remote pilot source note:** the three remote Company pilots' trajectories and the remote map ship's trajectory are **recorded/synthetic loopback pilot feeds** on the same Windows machine driving real native X4 hull writes and delayed readbacks (not four physical machines over WAN).
- **Offline LuaJIT 2.1 + Python loopback + CLI/package suites:** Real LuaJIT 2.1 (`lupa.luajit21`) and real loopback TCP/TLS sockets with simulated game callbacks.

## 1. Real native Windows X4 9.00 verification (`neon-multipilot-win-03`)

### Command

```powershell
.venv\Scripts\python.exe -B tools\dev\authority_company_cockpit_multipilot_windows.py --out .local\neon-multipilot-win-03
```

### Setup and safety

- **Outcome:** exit code `0`, `outcome="done"`, `accepted=true`, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, **`project_error_count=0`** (zero project MD/Lua errors).
- **Enrolled ships (`X4AuthorityShipRegistry`, `epoch=4ba7be438ebb04ec5bb85fdb95b5a488`):**
  - Own controlled cockpit (`index=1`): `DVL-744` (`cluster_14_sector001_macro`).
  - Pilot B (`index=2`, `slot=cockpit`): `VLY-685` (`0x177649`, unoccupied Pulsar, start `(-160, 0, 820)`, velocity **`(18, 4, 12)` m/s = `22.00 m/s`**).
  - Pilot C (`index=3`, `slot=cockpit3`): `YTB-731` (`0x177691`, unoccupied Pulsar, start `(220, 40, 900)`, velocity **`(-14, 10, -24)` m/s = `29.53 m/s`**).
  - Pilot D (`index=4`, `slot=cockpit2`): `GTK-109` (`0x1776d9`, unoccupied Pulsar, start `(0, -120, 750)`, velocity **`(0, -15, 20)` m/s = `25.00 m/s`**).
  - Map Ship E (`index=5`, `slot=map`): `LLH-995` (`0x177721`, unoccupied Pulsar in `cluster_409_sector001_macro`, velocity `(25, 0, 0)` m/s).

### Stage-by-stage native evidence

| Stage | Real native Windows X4 9.00 observation (recorded/loopback remote pilot feeds) | Result |
| --- | --- | --- |
| `1_three_pilots_plus_map` | `SetSofttarget` locks Pilot B (`mode=hud`) while Pilot C & Pilot D are untargeted on cockpit radar (`mode=radar`) + `map` enabled on Ship E; all 3 cockpit pilots stream concurrently at different velocities | Pilot B (`cockpit`, `hud`): **`57.709 m`**; Pilot C (`cockpit3`, `radar`): **`77.290 m`**; Pilot D (`cockpit2`, `radar`): **`65.282 m`**; `map` confirmed (`samples_confirmed=1`); **`own_disp_m=0.0000 m`** |
| `2_hud_to_radar_handoff` | `RemoveSofttarget()` (`has_target=false`) while all 3 pilots stream continuously | Pilot B transitioned seamlessly to `mode=radar`; **all 3 slots unchanged (`slots_unchanged=true`, `0` withdrawals)**; **`own_disp_m=0.0000 m`** |
| `3_independent_5s_loss` | `service.transport_lost(2)` drops Pilot B ONLY while Pilot C & Pilot D keep streaming live | Pilot B extrapolated `-73.2345 -> -15.9247` (`1.093 s -> 4.265 s`), hit `stale_5s` (`473` writes, `stale=1`), then **`0` writes / `0.0000 m` drift** (`5.578 s -> 6.359 s`). Simultaneously **Pilot C kept moving uninterrupted** (`279 -> 581` writes, `x: 158.746 -> 90.9211`, `stale=0`) |
| `4_remove_pilot_b` | Pilot B re-admitted on `gen2` (real `END|cockpit` revokes `gen1`) AND warped to `cluster_409_sector001_macro` out of cockpit radar; Pilot C, D, and Map Ship E keep streaming | Pilot B slot cleared (`p2_slot=none`, `wd=1`); **Pilot C stayed sticky in `cockpit3` with `serial=2` unchanged (`wd=0`) and moved +`65.731 m`** while Pilot B was absent; independent `map` slot confirmed sample 3 (`samples_confirmed=2`); **`own_disp_m=0.0000 m`** |
| `5_reacquisition_and_guards` | Pilot B returns in-sector; late revoked `gen1` injected; fresh `gen2` resumes **`112.446 m` (`> 50 m`)** ahead; active `120 m > 50 m` guard probed | Revoked `gen1` **rejected** (`revoked_generation`, `gen_rej=1`); **1 authorized alignment** on `gen2` verified on the **subsequent update** (`ALIGN_READBACK elapsed=0.022744 s`, **`error=0.000019 m`**); `120 m > 50 m` probe **refused** (`correction_out_of_bounds`, `ref50=1`); ordinary `<= 50 m` playback resumed alongside Pilot C |
| `6_independent_final_readback` | Independent final MD `Readback` + FFI pose on all 3 cockpit ships (`+0.15 s` later updates) | Pilot B (`VLY-685`, `cockpit`): **`337.700 m`**, `603` writes / `603` readbacks (`max_err=0.00003 m`, `min_el=0.01510 s`); Pilot C (`YTB-731`, `cockpit3`): **`397.597 m`**, `937` writes / `936` readbacks (`max_err=0.00003 m`, `min_el=0.01234 s`); Pilot D (`GTK-109`, `cockpit2`): **`376.679 m`**, `931` writes / `930` readbacks (`max_err=0.00006 m`, `min_el=0.01234 s`). Totals: **`2,471` writes / `2,469` readbacks**, **`289` `SEEN` receipts** (`50` cockpit / `111` cockpit2 / `126` cockpit3 / `2` map), **`own_disp_m=0.0000 m`** |

### Independent 5-second sample-loss probe table (`neon-multipilot-win-03`)

| Elapsed since Pilot B drop (s) | Pilot B (`VLY-685`, `cockpit`) X (m) / writes / stale | Pilot C (`YTB-731`, `cockpit3`) X (m) / writes / stale | Own ship (`DVL-744`) disp (m) |
| ---: | --- | --- | ---: |
| 1.093 | `-73.2345` / 279 / `0` | `158.7460` / 279 / `0` | 0.0000 |
| 4.265 | `-15.9247` / 451 / `0` | `118.0028` / 451 / `0` | 0.0000 |
| 5.578 | `-8.9190` / 473 / `1` | `101.2784` / 530 / `0` | 0.0000 |
| 6.359 | `-8.9190` / 473 / `1` | `90.9211` / 581 / `0` | 0.0000 |

## 2. Offline LuaJIT 2.1, loopback, CLI & package verification (exact final code)

| Scope | Result |
| --- | --- |
| Existing 4 Company Lua suites (`company_hull_playback_x4`, `company_hull_stream_integration`, `company_motion_source`, `company_observer_stream`) under `lupa.luajit21` | **4/4 PASS** |
| Offline unit suite (`CompanyCockpitObserverMdTests`, `CompanyCockpitPlaybackLuaTests` incl. 3-slot sticky + `uint64_t` cdata regression + `1611dbe1` next-update alignment, `company_observer_target` MD test, `test_authority_windows_fixture_cli`, `test_authority_windows_fixture_session`) | **38/38 PASS** |
| Loopback TCP/TLS & service suite (`test_authority_company_cockpit_playback` incl. multi-pilot loopback, `company_motion_service` incl. `application_drops`, `company_motion_reconnect`, `company_motion_tls` incl. 4-pilot TLS test, `company_observer_trial_end` incl. Windows QPC test, `company_motion_source` observer/loopback) (`checkpoints._sync_dir` stubbed on Windows only) | **58/58 PASS** |
| PowerShell AST syntax check on `tools/run_authority_windows_client.ps1` | **PASS** |
| Windows client package build & extracted overlay smoke (`tests/test_authority_windows_client_package.py`) | Run on committed `HEAD` |

## 3. First missing receipt & environment note

- **First missing receipt:** Multi-machine live human flight through the normal player launchers (Linux + Windows pilots flying live ships into the same sector and verifying live-source cockpit readbacks across machines).
- **Environment:** Windows 11, Python 3.12.10 (`.venv`), X4 9.00 (`19750a65...`). Standalone `luajit.exe` CLI binary is not installed on Windows; all LuaJIT 2.1 suites were run directly via `lupa.luajit21`.

## Local artifacts (kept in `.local/`, not committed)

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `.local/neon-multipilot-win-03/result.json` | 15,678 | `cd9c3b795c471daa1efcadc467b45ee399a6a4d7f77bbdb214bf96883f18e85f` |
| `.local/neon-multipilot-win-03/neon-company-cockpit-multi.log` | 26,125 | `b326df2cd3786c84bd8bf902b4f6589f2587fc0a7c173f0c534926960335cf65` |
