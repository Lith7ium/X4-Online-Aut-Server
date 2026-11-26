# First Native Windows X4 9.00 Combat Observation (Read-Only)

- **Date:** 2026-11-25
- **Author:** Neon (James Molander / `von-Benette`)
- **Branch:** `work/neon-combat-observation` (based on `64bb6679`, `origin/test/base-game-two-pilots-20261124`)
- **Scope:** Strictly **read-only** native combat observation on an isolated Windows X4 9.00 world — zero damage writes, zero health restores, zero min-hull changes, and zero damage-authority decisions.
- **Host:** Windows 11, Python 3.12.10 (`.venv`), Steam X4 9.00 (`X4.exe` SHA-256 `19750a6563889a970f434b5566eb396c6b2dc29ff814bd3e336f838176ad6891`), disposable profile `45850`, isolated `x4ls` view, windowed `800x600`, `fast=True`, `SW_SHOWNOACTIVATE` + `HWND_BOTTOM`.

---

## 1. Reusable Read-Only Instrumentation Delivered

Reusing the proven read-only patterns from the archived 7.5 LAN damage diagnostics (`ab004bea`: per-instance MD cue namespaces from `Host01`, synchronous `lastattacker`/`lastattacktime` readback inside damage callbacks from `LAST-ATTACKER-NATIVE.txt`, same-age `event_object_collided` pair tracking from `damage-dedup-06`, and read-only `GetLibraryEntry`/`GetInstalledWeaponMod`/`GetWeaponLaunchedMacro` weapon inspection from `shot_game.lua`) without any lockstep binary patching:

- **`src/authority/md/combat_observer.xml`** (`X4AuthorityCombatObserver`): Default-off / inert until explicitly armed via `X4AuthorityCombatObserver/Arm`. Watches arbitrary target ships (`State.$targets`), source ships (`State.$sources`), and optional weapons (`State.$weapons`) by component reference or `X4AuthorityShipRegistry` index. Observes all 10 native MD cues with `instantiate="true" namespace="this"`:
  - `event_object_shield_damaged`, `event_object_hull_damaged`
  - `event_object_attacked`, `event_object_attacked_object`
  - `event_player_owned_attacked`, `event_player_owned_attacked_object`
  - `event_player_ship_hit`
  - `event_object_collided`, `event_object_destroyed`, `event_weapon_fired`
  - Plus on-demand `Snapshot` readbacks. Contains zero mutating MD tags (enforced by static test).
- **`src/authority/lua/combat_observer.lua`** (`X4LAN_COMBAT_OBSERVER`): Strictly read-only Lua counterpart that parses `X4AuthorityCombatObserver.Event` payloads, classifies hits (`shield_absorbed`, `shield_depleted`, `hull_overflow_same_age`, `hull_zero_shield`, `subcomponent_*`, `collision_flagged`), performs immediate independent `GetComponentData` health readbacks (`hullpercent`, `shieldpercent`, `hull`, `hullmax`, `shield`, `shieldmax`, `owner`, `macro`), and provides `inspect_weapon_profile` (stock macro, bullet macro, `hullshielddpshot`, `bulletspeed`, beam/mod/secondary-channel flags).
- **`src/authority/combat_observation.py`**: Reusable Python read-only contract validator (`validate_md_read_only`, `validate_lua_read_only`), structured `[X4LAN] AUTH_COMBAT_OBS` / `[X4LAN] AUTH_COMBAT_LUA` log parser, and per-phase combat evidence analyzer.
- **`tests/test_authority_combat_observation.py`**: Offline static contract, LuaJIT 2.1 (`lupa.luajit21`), and analyzer unit tests (**7/7 PASS**).
- **Disposable Windows X4 9.00 trial harness:** `tools/dev/authority_combat_observation_{windows.py,x4.lua,probe.xml,shoot_at.xml}` (strictly separate from the reusable read-only observer; spawns isolated ships in clear space `y = 100 km` using the exact human-flight `tools/dev/fixtures/vanilla-flight/libraries/loadouts.xml` `x4lan_company_pulsar` loadout and triggers real in-game primary weapon fire via `<shoot_at fixedtarget="true"/>` and stock AI `Attack` orders).

The integrated harness requires an unused disposable profile lane and a new output directory. The historical measurements below came from the original Windows run.

---

## 2. Native Windows X4 9.00 Evidence (`neon-combat-win-02`)

### Run Integrity & Artifacts
- **Outcome:** Exit `0`, `outcome=done`, `accepted=true`, `elapsed_s=80.5`, **`project_error_count=0`**, `driver_errors=[]`, **`protected_unchanged=true`**, **`view_removed=true`**, **`x4_running_after=false`**.
- **Totals:** `471` MD combat events (`0` dropped), `261` Lua `GetComponentData` readbacks (`0` failed), `0` collisions, `0` deaths.
- **Local artifacts (`.local/neon-combat-win-02/`):**
  - `neon-combat-obs.log`: `265,397` bytes, SHA-256 `76d6b47690503bce9274e81c80d4273b9b81c42bba8535c6ae4b7aebdde9861d`
  - `result.json`: `22,713` bytes, SHA-256 `15c425a4fdacd427b40159515ab3ce2d624a9e50c06828e8900b83ff2b522e24`
- **Prior exploratory run (`neon-combat-win-01`, retained locally, not relabelled as pass):** Completed all phases (`482` MD events) and exposed two exact X4 9.00 global-event parameter layouts (`event_player_ship_hit` passes victim/attacker/projectile in `param`/`param2`/`param3` with `event.object=null`; `event_player_owned_attacked_object` passes attacker/victim/method in `param`/`param2`/`param3` with `event.object=null`) plus missing `require('ffi')` in `combat_observer.lua`. All three were fixed before `neon-combat-win-02`.

### Scenario Actors & Inspected Native Weapon Profiles (`cluster_14_sector001_macro`, `y = 100 km`)
All 5 ships were enrolled in `X4AuthorityShipRegistry` (`epoch=c0ba70b54ba7be438ebb04ec5bb85fdb`):

| Reg # | Label | Code | Macro | Owner | Role / Loadout | Baseline Shield / Hull | Inspected Weapon Profile (`GetLibraryEntry`) |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | `company_player` | `AWE-552` | `ship_arg_s_fighter_03_a_macro` | `player` | Player seated (`ctrl=1`, `occ=1`), `x4lan_company_pulsar` | `2366 / 3600` | 6× `weapon_gen_s_gatling_01_mk2_macro` (`bullet_gen_s_gatling_01_mk2_macro`, unmodified, non-beam, **`hullshielddpshot=26`**, `speed=2100`) |
| 2 | `company_ff_target` | `HJZ-803` | `ship_arg_s_fighter_03_a_macro` | `player` | Unoccupied registered Company Pulsar (same-Company FF target) | `2366 / 3600` | — |
| 3 | `company_npc_target` | `LOB-514` | `ship_arg_s_fighter_03_a_macro` | `player` | Unoccupied registered Company Pulsar (NPC-attacked target) | `2366 / 3600` | — |
| 4 | `npc_target` | `GZB-492` | `ship_xen_s_fighter_01_a_macro` | `xenon` | AI-piloted NPC fighter (`Wait`, `noattackresponse=true`) | `1188 / 2500` | — |
| 5 | `npc_attacker` | `PTA-108` | `ship_xen_s_fighter_01_a_macro` | `xenon` | AI-piloted hostile NPC fighter (`Attack` order) | `1188 / 2500` | 2× `weapon_xen_s_laser_01_mk1_macro` (`bullet_xen_s_laser_01_mk1_macro`, unmodified, non-beam, **`hullshielddpshot=23`**, `speed=2300`) |

### Per-Phase Real-Weapon-Hit Measurements

| Phase (Case) | Fire Trigger | Shots (`weapon_fired`) | `shield_damaged` (total MJ) | `hull_damaged` (total HP) | `object_attacked` / `_object` | `player_owned_attacked` / `_object` | `player_ship_hit` | `lastattacktime` lagged / total | Stationary Snapshot MD-Lua Abs Error |
| --- | --- | ---: | --- | --- | --- | --- | ---: | --- | --- |
| **1a. `ff_shield_only`** (`AWE-552` -> `HJZ-803`, same-Company FF) | `shoot_at fixedtarget=true` | 22 | **13** (`338.0`: `13 × 26.0`, `2366 -> 2028`) | **0** (`3600 -> 3600`) | `1` / `1` | **`1` / `1`** | `0` | `12 / 13` (max `0.1386 s`) | `0.0` / `0.0` |
| **1b. `ff_hull_overflow`** (`AWE-552` -> `HJZ-803`, same-Company FF) | `shoot_at fixedtarget=true` | 96 | **78** (`2028.0`: `78 × 26.0`, `2028 -> 0`) | **15** (`390.0`: `15 × 26.0`, `3600 -> 3210`) | `2` / `2` (`1.0013 s` apart) | **`2` / `2`** | `0` | `87 / 93` (max `0.93456 s`) | `0.0` / `0.0` |
| **2. `npc_to_company`** (`PTA-108` -> `LOB-514`, hostile NPC -> Company) | Stock AI `Attack` order | 22 | **22** (`506.0`: `22 × 23.0`, `2366 -> 1860`) | **0** (`3600 -> 3600`) | `2` / `2` (`1.0684 s` apart) | **`2` / `0`** | `0` | `19 / 22` (max `0.96836 s`) | `0.0` / `0.0` |
| **3a. `npc_shield_only`** (`AWE-552` -> `GZB-492`, Company -> NPC) | `shoot_at fixedtarget=true` | 12 | **11** (`286.0`: `11 × 26.0`, `1188 -> 902`) | **0** (`2500 -> 2500`) | `1` / `1` | `0` / **`1`** | `0` | `6 / 11` (max `0.0834 s`) | `0.0` / `0.0` |
| **3b. `npc_hull_overflow`** (`AWE-552` -> `GZB-492`, Company -> NPC) | `shoot_at fixedtarget=true` | 48 | **35** (`902.0`: `34 × 26.0 + 18.0`, `902 -> 0`) | **12** (`294.0`: `8.0 + 11 × 26.0`, `2500 -> 2206`) | `1` / `1` | `0` / **`1`** | `0` | `44 / 47` (max `0.5174 s`) | `0.0` / `0.0` |
| **4. `npc_to_player_occupied`** (`PTA-108` -> `AWE-552`, NPC -> player seat) | Stock AI `Attack` order | 10 | **8** (`184.0`: `8 × 23.0`, `2366 -> 2182`) | **0** (`3600 -> 3600`) | `1` / `1` | **`1` / `0`** | **`8`** | `6 / 8` (max `0.2503 s`) | `0.0` / `0.0` |

---

## 3. Exact Engine Findings (Direct Answers to the 01:00 CET Questions)

### 3.1 Who X4 Reports as Attacker
- **Always the firing SHIP component (`isclass.ship = 1`), not the pilot entity or `player.entity`:**
  - Same-Company friendly fire (`AWE-552` -> `HJZ-803`): X4 reported `AWE-552` (`0x1782da`, `owner=player`, `reg=1`, `player.controlled=1`, `isclass.ship=1`, pilot `0x120ac`) identically in `event.param` (`object_attacked`, `player_owned_attacked`), `event.object` (`object_attacked_object`), `event.param` (`player_owned_attacked_object`), AND `$victim.lastattacker` inside all `91` `shield_damaged` + `15` `hull_damaged` callbacks.
  - Hostile NPC -> Company (`PTA-108` -> `LOB-514` and `AWE-552`): X4 reported `PTA-108` (`0x178460`, `owner=xenon`, `reg=5`, `player.controlled=0`, `isclass.ship=1`, pilot `0x178462`) consistently across all callbacks and `event_player_ship_hit` (`event.param2`).
  - Company -> NPC (`AWE-552` -> `GZB-492`): X4 reported `AWE-552` (`owner=player`, `reg=1`, `player.controlled=1`, `isclass.ship=1`) consistently across all `46` `shield_damaged` + `12` `hull_damaged` callbacks and attack notifications.
- **No attacker parameter on per-hit damage callbacks:** `event_object_shield_damaged` and `event_object_hull_damaged` carry **no** attacker parameter; attacker identity is read synchronously via `$victim.lastattacker`.
- **`lastattacktime` is throttled and NOT a per-hit freshness timestamp:** Across all `194` damage callbacks, **`174`** retained an older `lastattacktime` (up to `0.96836 s` older than `player.age`), refreshing on the same ~1.0 s throttle window as `event_object_attacked` (reproducing the earlier Linux `LAST-ATTACKER-NATIVE.txt` finding on Windows X4 9.00).
- **Firing weapon / projectile attribution:**
  - `event_object_attacked_object` carries `event.param3 = [damaged_component, firing_weapon]` (e.g. `[component.{0x178352L}, component.{0x1782e2L}]`), but is throttled (~1/s).
  - `event_player_ship_hit` carries the exact per-hit **projectile handle** in `event.param3` (all `8` hits in Case 4 resolved distinct handles with `macro = bullet_xen_s_laser_01_mk1_macro`), **only** when the victim is `player.occupiedship`.

### 3.2 Where Shield & Hull Change (and Exact Synchronous Event Ordering)
- **Root hull/shield component:** On all `167` `shield_damaged` and `27` `hull_damaged` S-fighter impacts, `event.param == event.object` (`is_root = 1`, `comp_macro` = victim ship macro — no surface-element subcomponent hits on these S-class targets).
- **Exact callback parameter meanings confirmed natively on Windows X4 9.00:**
  - `event_object_shield_damaged`: `event.object` = victim ship, `event.param` = damaged component, **`event.param2` = post-hit remaining shield HP** (exact match to `$victim.shield`), **`event.param3` = exact shield damage applied** (`26.0` or `23.0`, or remainder `18.0`).
  - `event_object_hull_damaged`: `event.object` = victim ship, `event.param` = damaged component, **`event.param2` = post-hit remaining hull HP** (exact match to `$victim.hull`), **`event.param3` = exact hull damage applied** (`26.0`, or overflow `8.0`).
- **Exact synchronous ordering on a bullet impact (verified on `age = 3.00088`, `4.71346`, `5.71478`, `15.8626`):**
  1. Throttled attack notifications (`player_owned_attacked`, `object_attacked`, `player_owned_attacked_object`, `object_attacked_object`) fire **BEFORE** shield/hull subtraction — inside those callbacks `$victim.shield` and `$victim.hull` still read their **pre-hit** values (e.g. `2366/2366` at `age=15.8626`).
  2. `event_object_shield_damaged` fires **AFTER** shield subtraction (`shield = 2343/2366`, `amount = 23.0`).
  3. On shield-to-hull overflow, `event_object_hull_damaged` fires synchronously at the **exact same `player.age`** right after `event_object_shield_damaged`. In Case 3b (`age = 13.5259`), one `26.0`-damage Bolt Repeater round against `18.0` remaining shield split cleanly into `shield_damaged(amount=18.0, shield=0, hull=2500)` followed immediately by `hull_damaged(amount=8.0, shield=0, hull=2492)` (`18.0 + 8.0 = 26.0` exact conservation). Counting both events without deduplication would double-count overflow impacts.
  4. For `player.occupiedship` only (Case 4), `event_player_ship_hit` fires synchronously **after** `event_object_shield_damaged` at the exact same `player.age`.
- **MD vs Lua `GetComponentData` health readbacks:**
  - Windows X4 9.00 supports **all 6** tested Lua `GetComponentData` health keys (`hullpercent`, `shieldpercent`, `hull`, `hullmax`, `shield`, `shieldmax`) — `261/261` non-nil.
  - On stationary `snapshot` cues, Lua absolute `shield` / `hull` matched MD `$victim.shield` / `$victim.hull` to **`0.0` HP** across all 7 snapshots.
  - **Two critical Lua readback nuances:** (a) `GetComponentData(..., 'shieldpercent', 'hullpercent')` returns **truncated integers** (e.g., `85` vs MD `85.7143`), whereas `'shield'`/`'hull'` return exact absolute HP; and (b) `raise_lua_event` delivers queued MD events asynchronously on the UI frame pass **after** all same-frame impacts have already settled, so during multi-bullet frames Lua reads the end-of-frame post-burst HP for every queued event of that frame (causing up to `11.85%` shield / `5.0%` hull delta against earlier mid-frame MD callback values).

### 3.3 What Events Remain Observable When Shields Fully Absorb a Hit
Across all four 100%-shield-absorbed phases (`1a`, `2`, `3a`, `4`, totaling `54` real shield-absorbed impacts and `0` hull callbacks):
- **Per-hit (unthrottled):**
  - **`event_object_shield_damaged`**: fires **1-to-1 for every shield-absorbed impact** (`13/13` in 1a, `22/22` in 2, `11/11` in 3a, `8/8` in 4), carrying exact `amount` and post-hit remaining `shield`, with `$victim.lastattacker` set to the firing ship.
  - **`event_player_ship_hit`**: fires **1-to-1 for every shield-absorbed impact** (`8/8` in Case 4) with victim, attacker, and projectile handle, **ONLY** when the victim is `player.occupiedship` (never fires for unoccupied Company ships or NPCs).
- **Throttled (~1.0 s per attacker + killmethod):**
  - **`event_object_attacked`** and **`event_object_attacked_object`**: fire on shield-absorbed hits (including same-Company friendly fire!) with `method = hitbybullet`, throttled to ~1 Hz (`1.0013 s` interval in Case 1b across 93 damage callbacks; `1.0684 s` interval in Case 2 across 22 damage callbacks).
  - **`event_player_owned_attacked`**: fires on shield-absorbed hits whenever the victim is `faction.player`, **including same-Company friendly fire** (`AWE-552` -> `HJZ-803`), throttled to ~1 Hz.
  - **`event_player_owned_attacked_object`**: fires on shield-absorbed hits whenever the attacker is `faction.player` (`1a`, `1b`, `3a`, `3b`), throttled to ~1 Hz.
- **Silent on 100% shield-absorbed hits:** `event_object_hull_damaged` (`0` across all 4 shield-only phases).

### 3.4 Same-Company Friendly Fire (Human-Flight Scenario)
- Using the exact base-game human-flight Pulsar loadout (`x4lan_company_pulsar`, both ships `owner=faction.player`), real projectile hits from `player.controlled` (`AWE-552`) onto unoccupied registered Company Pulsar `HJZ-803` dealt **full unmodified stock damage** (`26.0` per Bolt Repeater hit — `338.0` MJ in Phase 1a, `2028.0` MJ shield + `390.0` HP hull in Phase 1b).
- Same-Company friendly fire emitted **every** normal combat signal (`shield_damaged`, `hull_damaged`, `object_attacked`, `object_attacked_object`, `player_owned_attacked`, `player_owned_attacked_object`), with `AWE-552` reported as attacker — confirming why the Bazzite pilot in the 24 Nov human flight saw real local damage on the other player's Company Pulsar while Windows (without combat replication) saw none.
- Notably, `<shoot_at object="$p1" target="$p2" fixedtarget="true"/>` fired directly at the same-faction Company ship without refusing friendly fire (zero fallback to mindless `<shoot/>` needed), whereas stock player UI/AI attack orders still exclude `faction.player` targets.

---

## 4. Verification Summary (Explicitly Separated)

- **Offline / synthetic / static tests (`tests/test_authority_combat_observation.py`):**
  - **7/7 PASS** in `0.007 s` under `.venv\Scripts\python.exe` (Python 3.12.10 + `lupa.luajit21`): MD XML read-only & `namespace="this"` static enforcement, Lua read-only enforcement, LuaJIT 2.1 event parsing / damage classification / `GetComponentData` readback / weapon library profile inspection, and Python multi-phase analyzer checks.
- **Real native Windows X4 9.00 trial (`neon-combat-win-02`):**
  - **PASS** (`accepted=true`, `project_error_count=0`, `protected_unchanged=true`, `view_removed=true`, `x4_running_after=false`), exercising real in-game primary weapon projectiles on an isolated single-machine Windows X4 world. No multi-machine network transport or damage replication was exercised.

---

## 5. Precise Limits & Open Questions (No Damage Decisions Made)

1. **Single-machine isolated world only:** All observations occurred in one isolated Windows X4 9.00 process (`attention.visible`, high-attention clear space). Low-attention / out-of-sector combat (`GetAttackStrength` / `ApplyAttackStrengthAction`), cross-machine network latency, and Linux/Windows parity were not exercised here.
2. **Weapon & target class bounds:** Exercised only uniform unmodified stock ballistic/laser primary guns (`weapon_gen_s_gatling_01_mk2_macro`, `weapon_xen_s_laser_01_mk1_macro`, pure `hullshield` damage) against S-class fighters (`ship_arg_s_fighter_03_a_macro`, `ship_xen_s_fighter_01_a_macro`). Beams, missiles, turrets, area/splash damage, shield-piercing / hull-only weapons, weapon mods, M/L/XL surface elements (shield generators / turrets / engines where `event.param != event.object`), collisions/ramming damage, environmental/hazard regions, repair beams, and lethal destruction transitions remain unobserved in this pass.
3. **Attacker identity edge cases:** Every phase had a single active shooter per victim; simultaneous multi-attacker frames, destroyed/despawned attackers whose bullets land after death, and concurrent collision + projectile impact at the same `player.age` (conservatively ambiguous in collision-pair filtering) were not exercised natively here.
   Same-age shield and hull records are overflow candidates. The analyzer cannot uniquely pair them or attribute them to one projectile when several shots land in the same game update.
4. **Lazy shield recharge side effect:** Reading `$victim.shield` / `GetComponentData(..., 'shield')` materializes X4's lazy shield recharge calculation at `player.age`; continuous high-rate polling outside damage callbacks should account for that engine behavior.
5. **No mutations or authority policy:** `X4AuthorityCombatObserver` and `X4LAN_COMBAT_OBSERVER` make zero writes (`set_object_hull`, `set_object_shield`, `apply_attackstrength`, etc. are absent and forbidden by tests). Choosing damage authority (server vs shooter vs victim), replication wire format, or friendly-fire policy remains open for Astra/Lith7ium.

## 6. Controlled mixed-weapon follow-up on Windows

Cobalt reported 79 of 79 damage assignments across two disposable Windows runs using solo controls for 26- and 36-point primary-gun hits. Nine assignments had a preceding direct attack notification at the same recorded game age. The runs contained one game age with both inferred sources and no observed A–B–A alternation. The raw Windows logs remain external, so the counts are contributor-reported. The source experiment is recorded at commit `997a3183`.

The overlap runner now accepts a separate stock gun for the second fixture ship, a distance adjustment and an optional full-health reset of the registered Xenon target between phases. The reset requires the target's verified registry binding and that the traced projectiles have cleared. The ordinary single-shooter runner and the default overlap loadout keep their previous settings.

The supplemental analyzer cross-checks solo shield amounts against the two gun profiles and the projectiles observed in each phase. It assigns overflow only when a unique adjacent shield and hull pair at the same reported game age sums to one calibrated amount and the hull piece cannot itself be a full shot. Sequence gaps, lethal clipping and competing pairs remain unresolved wherever they affect attribution. The analyzer compares assignments with `lastattacker` and preceding same-age notifications after classifying them. Overlap acceptance still requires direct, registered witnesses from both shooters at the same recorded game age and the existing miss and damage checks.


The offline analyzer marks a target's damage as `collision_ambiguous` when
a damage callback carries a collision flag or a collision involving that
target appears at the same recorded game age, even after the damage rows.
The phase summary preserves its raw damage counts and reports the affected
ages and callback count. Those rows supply neither gun calibration nor
overlap acceptance. Unaffected targets and game ages keep their existing
checks. The synthetic cases exercise both event orders and unrelated
collisions. The historical Windows trials were not rerun.
