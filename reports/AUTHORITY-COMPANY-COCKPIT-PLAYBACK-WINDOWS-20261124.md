# Receiving-cockpit Company ship playback — HUD target and untargeted radar-visible pilot (24 Nov 2026)

Author: Neon (James Molander / `von-Benette`).
Branch: `work/neon-company-cockpit-playback` (branched cleanly from `d77755be`).
Host: Windows 11 PC (separate machine from Cobalt and Lith7ium), Python 3.12.10 (`.venv`), AMD Radeon RX 9070 XT, X4 9.00 pinned executable (`SHA-256 19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`).

## Scope delivered (per 23:00 CET directive)

1. **Opt-in cockpit Company observer (`src/authority/md/company_cockpit_observer.xml`, `src/authority/lua/company_cockpit_observer.lua`):**
   - Strictly read-only MD & Lua observer preserving the exact `tick()`, `current()`, `matches(...)`, `close()` interface of `X4LAN_COMPANY_OBSERVER_TARGET` (existing `company_observer_target.*` files and tests remain untouched).
   - **Selected HUD target first (`mode='hud'` / `'hudmap'`):** Resolves `@player.target` in two-way `X4AuthorityShipRegistry` (`$ships` / `$byship`) verifying operational, undocked, non-stored, unoccupied (`not pilot.exists and not assignedpilot.exists`), `owner == faction.player`, `!= player.controlled/occupiedship/container`, same sector, `attention >= attention.visible`, `isphysicsready`.
   - **Nearby radar-visible Company pilot without a target lock (`mode='radar'`):** When no eligible Company ship is locked on HUD (no target or unrelated target), falls back to resolving an eligible nearby unoccupied Company ship in `player.sector` strictly within `player.controlled.currentradarrange` (checked both in MD and Lua FFI `GetDistanceBetween <= maxradarrange` + `isradarvisible == true`).
   - **Seamless `hud` <-> `radar` handoff:** When the player unlocks a target while the same Company pilot remains within cockpit radar range, `observed` stays valid while re-confirming the new mode — zero view serial churn and zero playback interruption.
   - **Independent map interest preserved:** Leaves `X4LAN_COMPANY_OBSERVER_MAP` and the `map` slot completely independent.
2. **Opt-in reacquisition-capable cockpit playback controller (`src/authority/lua/company_cockpit_playback.lua`, `src/authority/lua/company_hull_playback_x4.lua`):**
   - Default-off (`enabled == true` required), reuses `X4LAN_COMPANY_HULL_PLAYBACK` (`X4LAN_MOTION_BUFFER` + `X4LAN_MOTION_PLAYBACK`) and `X4LAN_COMPANY_HULL_PLAYBACK_X4` (strictly keeping own-ship control local: `index ~= own_index`, `actor ~= cockpit`, preserving orientation, same sector & same zone).
   - Preserves Lith7ium's **5.0 s** connection-loss prediction limit (`stale` cutoff with zero post-cutoff writes) and **50 m** active step bound (`correction_out_of_bounds`).
   - Enforces **strict generation checks:** rejects mismatched generations while active (`generation_mismatch`), permanently revokes generations on `END` (`revoked_generation`), and accepts a fresh pilot grant generation after revocation/cutoff.
   - Supports **clean reacquisition** after loss, revocation, or withdrawal: direct ordinary resumption when `<= 50 m`, or strictly **at most one authorized alignment** (`session:align`) per loss/withdrawal episode verified by a delayed later-update `alignment_readback` (`elapsed > 0`, `<= 3 m` tolerance) before resuming ordinary `<= 50 m` playback.
   - **Real Windows same-tick fix (discovered during native run `win-01`):** Skips redundant `tick()` calls when `now <= last_tick_now` (prevents false `readback_not_later` faults when X4 invokes `onUpdate` twice inside one `GetCurRealTime()` frame after `RemoveSofttarget()` or at >64 FPS on Windows 15.6 ms `GetTickCount64` ticks).
3. **Opt-in client wiring (`src/authority/company_motion_source.py`, `src/authority/client_seat_overlay.py`, `src/authority/lua/company_motion_source.lua`, `src/authority/experimental_windows_fixture_overlay.py`, `tools/dev/authority_linux_company_seat.py`):**
   - Added default-off `cockpit_radar_playback=False` on `CompanyMotionSource` (requires `playback_index`). When `False`, existing overlays and configs stay byte-identical; when `True`, stages `company_cockpit_observer.{xml,lua}` and `company_cockpit_playback.lua` on both Windows and Linux overlays.

## Honest separation of evidence

- **Native Windows X4 9.00 (`neon-cockpit-win-02`):** Real native Windows X4 9.00 game (`custom_creative`, disposable profile `45850`, isolated `x4ls` view, `800x600`, `fast=True`, `HWND_BOTTOM`), running the **real production Lua & MD modules** (`company_cockpit_observer.{xml,lua}`, `company_cockpit_playback.lua`, `company_hull_playback*.lua`, `company_observer_stream.lua`, `motion_buffer.lua`, `motion_playback.lua`) over real Windows PID-validated loopback UDP (`x4lan_net_64.dll`, `PID 24820`, `0.0.0.0:63683`) connected to a real loopback `CompanyMotionService` + admitted `CompanyMotionClient` + `CompanyMotionSource` + `CompanyMotionObserver`. **Remote pilot source note:** the remote Company pilot's 22.0 m/s V3 trajectory (`(18, 4, 12) m/s` at 10 Hz) is a **recorded/synthetic loopback pilot feed** on the same Windows machine driving real native X4 hull writes and readbacks (not a second physical machine over WAN).
- **Offline LuaJIT 2.1 + Python loopback suite (`tests/test_authority_company_cockpit_playback.py` + existing regressions):** Real LuaJIT 2.1 (`lupa.luajit21`) and real loopback TCP sockets with simulated game callbacks.

## 1. Real native Windows X4 9.00 verification (`neon-cockpit-win-02`)

### Command

```powershell
.venv\Scripts\python.exe -B tools\dev\authority_company_cockpit_windows.py --out .local\neon-cockpit-win-02
```

### Setup and safety

- **Outcome:** exit code `0`, `outcome="done"`, `accepted=true`, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`, **0 MD/Lua errors** from project scripts (one stock X4 Saturn 2 resource-area warning during `NewGame` universe generation).
- **Enrolled ships (`X4AuthorityShipRegistry`, `epoch=4ba7be438ebb04ec5bb85fdb95b5a488`):**
  - Own controlled cockpit (`index=1`): `ZSR-158` (`cluster_14_sector001_macro`).
  - Remote unoccupied Company Pulsar Vanguard (`index=2`): `XFN-378` (`0x179cc0`, `owner=player`, `pilot=none`, `cluster_14_sector001_macro`, spawned `849.88 m` ahead at `(0, 0, 850)`).
  - Independent map-slot Company Pulsar (`index=3`): `DCR-695` (`0x179d08`, `cluster_409_sector001_macro`).

### Stage-by-stage native evidence

| Stage | Real native Windows X4 9.00 observation (recorded/loopback remote pilot feed) | Result |
| --- | --- | --- |
| `1_hud_target` | Real `C.SetSofttarget(remote, '')` (`locked=true`, `on_screen=true`, `dist=849.88 m`); Pilot B streams 25 samples (`22.0 m/s`) | `mode=hud`, `has_target=true`, **`219` writes / `218` delayed readbacks**, `XFN-378` moved **`64.322 m`** (`max_err=0.00003 m`, `min_elapsed=0.01020 s`), **`own_disp_m=0.0000 m`** |
| `2_untargeted_radar_and_map` | Real `RemoveSofttarget()` (`has_target=false`); independent `map` slot enabled on `DCR-695` (`cluster_409`); Pilot B streams 20 more samples | Seamless transition to **`mode=radar`** (`binds=1`, `serial=2`, no view churn); `XFN-378` moved **+`43.960 m`** with **no HUD lock** (`378` writes / `377` readbacks); independent `map` slot confirmed `SEEN|map` (`samples_confirmed=1`, `real_velocity=(25,0,0)`); mismatched active generation (`9999...`) **rejected** (`generation_mismatch`); **`own_disp_m=0.0000 m`** |
| `3_5s_loss_limit` | Remote pilot link dropped + `service.transport_lost(2)` (`motion_hidden`) while in `mode=radar` | Extrapolated **`70.611 m`** (`1.078 s` -> `4.250 s`), stopped cleanly at **5.0 s** (`stale_cutoffs=1`, `652` writes), then **`0` extra writes** and **`0.0000 m` drift** across `5.547 s` -> `6.375 s` |
| `4_generation_and_reacquisition` | Pilot B re-admitted on fresh `gen2` (`4f2c22e7...`), revoking `gen1` (`b6cfafce...`) via real `END|cockpit`; `gen2` resumes **`110.0 m` (`> 50 m`)** ahead | Late `gen1` packet **rejected** (`revoked_generation`, `gen_rejects=2`); **1 authorized alignment** (`mode=radar`) confirmed by later `ALIGN_READBACK` (`elapsed=0.016697 s`, **`error=0.000030 m`**); subsequent active `120.0 m > 50 m` probe **refused** (`correction_out_of_bounds`, `refusals_50m=1`); ordinary `<= 50 m` playback resumed **+`55.595 m`** in untargeted `mode=radar` |
| `5_independent_final_readback` | Independent later MD `Readback` + FFI pose (`+0.20 s` later update) | Same operational unoccupied Company Pulsar `XFN-378` (`0x179cc0`) at `(306.4139, 68.0919, 1054.2760)` -> total displacement **`374.506 m`**, **`810` writes / `810` readbacks** (`max_err=0.00006 m`, `min_elapsed=0.01020 s`), **`own_disp_m=0.0000 m`**, `66` total `SEEN` receipts (`65` cockpit / `1` map) |

### Five-second sample-loss probe table (`XFN-378`, `index=2`, untargeted `mode=radar`)

| Seconds since last sample | Mode / target lock | Actual `XFN-378` sector X (m) | Cumulative X4 writes / readbacks | Own-ship displacement (m) |
| ---: | --- | ---: | ---: | ---: |
| 1.078 | `radar` (`has_target=false`) | 108.4435 | 444 / 443 | 0.0000 |
| 4.250 | `radar` (`has_target=false`) | 166.2164 | 636 / 635 | 0.0000 |
| 5.547 | `radar` (`stale_cutoffs=1`) | 170.9268 | 652 / 652 | 0.0000 |
| 6.375 | `radar` (`stale_cutoffs=1`) | 170.9268 | 652 / 652 | 0.0000 |

## 2. Offline LuaJIT 2.1 & loopback verification (exact final code)

| Scope | Exact Windows command (`.venv`, Python 3.12.10) | Result |
| --- | --- | --- |
| New opt-in cockpit playback suite (MD contract, opt-in overlay config, LuaJIT 2.1 full lifecycle + same-tick regression, real loopback service) | `.venv\Scripts\python.exe -B -c "import sys, unittest; sys.path[:0]=['src','tests']; res=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('tests.test_authority_company_cockpit_playback')); raise SystemExit(0 if res.wasSuccessful() else 1)"` | **4/4 PASS** |
| Existing Company Lua suites under LuaJIT 2.1 (`company_hull_playback_x4`, `company_hull_stream_integration`, `company_motion_source`, `company_observer_stream`) + `company_observer_target` MD test | Run directly under `lupa.luajit21` + `unittest` | **5/5 PASS** (zero regressions) |
| Existing Python loopback motion & observer suites (`company_motion_service`, `reconnect`, `tls`, `observer_trial_end`, `company_motion_source` non-CLI) (`checkpoints._sync_dir` stubbed on Windows only) | Run via `.venv\Scripts\python.exe -B` | **45/45 PASS** (1 CLI test skipped/failed only due to no standalone `luajit.exe` on Windows; verified via `lupa.luajit21` above) |

## 3. Environment note & next step

- **Windows UDP peer ownership note (matches Cobalt's 23:03 finding):** On Windows 11, `x4lan_net_64.dll` bound wildcard `0.0.0.0:63683` (`PID 24820`). Our test harness validator accepts `0.0.0.0`/`127.0.0.1` for the owned child PID; production `WindowsMotionPeer` remains owned by Cobalt's outbound path task.
- **First missing receipt / remaining gap:** Two-machine live playback where both Bazzite and Windows pilots fly real ships over the network and each receiving cockpit reads back the other's live flight (once Cobalt's Windows outbound fix and Astra's two-pilot server application fix are merged with this branch).

## Local artifacts (kept in `.local/`, not committed)

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `.local/neon-cockpit-win-02/result.json` | 10,152 | `b6c9e6bf9117f801f117961bfd04518ebf4df391c3d7c44f28f957083eb69b0d` |
| `.local/neon-cockpit-win-02/neon-company-cockpit.log` | 12,983 | `9557ef3a505fef9c51b79dbe9a89b259b1dc636374f0e23381153d1d83560314` |
