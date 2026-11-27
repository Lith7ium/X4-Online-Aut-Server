# Queued Second Hit After Registered NPC Destruction — Source Trace & Low-Risk Fix

- **Date:** 2026-11-27
- **Author:** Neon (`James Molander` / `von-Benette`)
- **Branch:** `work/neon-queued-hit-after-npc-death`
- **Base commit:** `trial/velocity-aware-playback-20261126` @ `61d0d0fcc141d8d74091c2e5b38693c0aa2bccb1` (`61d0d0fc`)
- **Scope:** Source/offline trace, comparison against the 2026-11-26 NPC death reports, and a concrete low-risk two-file fix with offline regression tests. **No live X4 run, new game build, or invitation was executed for this report.**

---

## 1. Comparison With Existing NPC Death Reports

1. **`reports/AUTHORITY-LEGACY-TLS-NPC-DEATH-20261126.md`**
   - Ran legacy serial-only `56a00bcc` (`apply_impacts=None`, before `GUN_GROUP` / `single_fallback`).
   - Sent Order 1 to `MFQ-419` (index `943`, lethal) and Order 2 to **surviving `KHI-630` (index `1074`)**.
   - **Uncovered:** Never sent a second already-queued hit to the destroyed NPC `943`, and never exercised the newer `GUN_GROUP` -> `single_fallback` path (`fa32012e` / `61d0d0fc`).

2. **`reports/AUTHORITY-NPC-DEATH-WITNESS-20261126.md`**
   - Isolated single-hit MD witness (no `CompanyCombatService`, no UDP/Lua bridge, no queued follow-up hit).
   - Proved two distinct native X4 9.00 post-kill stages on `MFQ-419`:
     - **Immediate (`4.04348 s`):** Inside the cue immediately after `<apply_attackstrength>`, X4 reported `killed=1, shield=0, hull=0, maxshield=5750, maxhull=6400`; immediate Lua readback matched.
     - **`+7.48` to `+7.78 ms` later (`4.18865 s` / `4.05126 s`):** X4 fired the `lowattentionattack` destruction event and transitioned `MFQ-419` into `state="wreck"` (`isclass.ship=true, isoperational=false, iswreck=true`, still present in `$ships` and `$byship` at index `943`), where **both MD and Lua `GetComponentData` reported `shield=0, hull=0, maxshield=0, maxhull=0`**.

---

## 2. End-to-End Trace of a Second Already-Queued Hit on `61d0d0fc`

Client-side `src/authority/md/company_gun_source.xml` only observes local client weapon contacts (server NPC damage/death is not yet replicated back to clients), so bursts routinely queue multiple hits for the same NPC in one `CompanyCombatSender` batch (up to `MAX_BATCH=32`), or a second pilot already has a batch queued in `CompanyCombatService._batches` while Hit 1 is in flight.

### Path A — Hit 1's Lua callback completes *before* X4's `~7.5–7.8 ms` wreck transition (already handled cleanly)

| Stage | Serial (`apply_impacts=None`) | Grouped (`ordered_hits=True`, `begin_impacts`) |
| --- | --- | --- |
| **Dispatch** | Hit 1 (`GUN_IMPACT`) kills `943`; Hit 2 waits in `batch`. | MD `Group` preflight (`tools/dev/authority_company_gun_damage.xml:122-136`) requires `$target.isoperational`, `$target.hull gt 0`, and `$total lt $target.shield + $target.hull`. Any single-lethal, cumulative-lethal, or already-dead NPC row fails preflight with **zero** `ApplyStock` writes and returns `GUN_GROUP_RESULT\|...\|refused`. `CompanyCombatService._advance_impacts` catches `CombatImpactRejected`, keeps reserved orders in `batch.single_fallback`, and replays serially via `GUN_IMPACT`. |
| **Hit 1 settle** | Immediate Lua readback matches `(0, 0, 5750, 6400)` -> `status='confirmed', shield=0.0, hull=0.0`. | Same via `single_fallback`. |
| **Queued Hit 2 on destroyed `943`** | MD `Impact` (`authority_company_gun_damage.xml:66-76`) rejects Hit 2 on `@$target.hull gt 0` (even pre-wreck), `$target.isoperational` (post-wreck), or `@$target.isclass.ship` (if despawned). `State.$last_serial` is not bumped; `ApplyStock` is not called; MD emits `refused\|<source>\|<target>\|false\|-\|-` (note: `<source>`/`<target>` are resolved non-null hex actor IDs while the wreck remains registered, or `null` if despawned). Lua (`authority_company_gun_damage.lua:109`) forwards `GUN_RESULT\|...\|refused`; `NativeGunDamageProvider._poll_impact` (`authority_company_gun_damage.py:513-517`) raises `CombatImpactRejected` **without** setting `_fenced`; `CompanyCombatService` settles Hit 2 as `status='rejected', shield=None, hull=None`, advances `_settled_order`, and keeps `poisoned=False`. | Same via `single_fallback`. A disconnect + exact retry mid-`single_fallback` replays cached receipts without redispatching MD. |
| **Queued Hit 3 / second pilot on surviving ship** | Dispatched normally and settles `status='confirmed'`. `CompanyCombatSender` increments `rejected` for Hit 2 and continues live fire cleanly (`failure=None`). | Same. |

### Path B — Uncovered Critical Timing Race (Hit 1's Lua callback runs *after* X4's `~7.5–7.8 ms` wreck transition)

Because `raise_lua_event('X4AuthorityCompanyGunDamage.Result')` is delivered on the UI thread (16.7 ms cadence at 60 Hz, and slower on GPU-free software-Vulkan Linux hosts), Hit 1's Lua callback can readily run after X4's `~7.5–7.8 ms` wreck transition observed in `AUTHORITY-NPC-DEATH-WITNESS-20261126.md`:

1. Inside MD `ApplyStock` (`authority_company_gun_damage.xml:15-37`), `MFQ-419` has not transitioned to a wreck yet, so MD legitimately encodes `killed=1`, `AfterShield=0, AfterHull=0, MaxShield=5750, MaxHull=6400`.
2. When `X4AuthorityCompanyGunDamage.Result` fires in Lua (`>= 7.8 ms` later), `X4LAN_COMBAT_OBSERVER.read_lua_health(target)` reads the wreck and returns `{ok=true, shield=0, hull=0, shieldmax=0, hullmax=0}`.
3. **Double rejection on `61d0d0fc`:**
   - `tools/dev/authority_company_gun_damage.lua:134` required `near(hp.MaxShield, observed.shieldmax)` and `near(hp.MaxHull, observed.hullmax)` unconditionally, logging `AUTH_COMPANY_GUN_DAMAGE_UNKNOWN ... reason=health_readback_mismatch` and replying `GUN_RESULT|...|unknown`.
   - `tools/dev/authority_company_gun_damage.py:133` (`_read_health`) likewise required `_same_health(after, observed)` unconditionally and raised `NativeGunDamageUncertain('native impact health readbacks disagree')`.
4. **Cascade onto queued Hit 2 and all surviving targets:**
   - `NativeGunDamageProvider._fenced` flips to `True` and `CompanyCombatService._poisoned` flips to `True`.
   - Hit 1 (which actually killed `MFQ-419` in server X4) is reported to the client as `status='unknown'`.
   - Queued Hit 2 is rejected purely by the poisoned short-circuit without consulting X4.
   - **All queued or subsequent hits from any pilot against surviving ships (`KHI-630`, etc.) are permanently rejected for the rest of the service lifetime.**

### Additional Boundaries Noted (Kept Intentionally Fail-Closed)

- **Existing Lua test gap:** `tests/authority_company_gun_damage.lua` previously had zero `killed=1` coverage and only tested `refused|null|null`, not `refused|0x...|0x...` emitted for a still-registered wreck.
- **Vanished target before Hit 1's Lua readback (`ok=false` / `nil` fields):** If a destroyed object leaves no wreck and disappears before Hit 1's Lua readback, Lua still logs `lua_readback_missing` / `health_readback_mismatch` and fails closed (`unknown`), because an independent Lua confirmation was not obtained.
- **Synchronous zero `maxhull` inside `apply_attackstrength`:** Did **not** happen on `MFQ-419` in `AUTHORITY-NPC-DEATH-WITNESS-20261126.md` (`maxhull` stayed `6400` immediately after `apply_attackstrength`), so `authority_company_gun_damage.xml:15` (`@$target.maxhull gt 0`) is left untouched and fail-closed.

---

## 3. Concrete Low-Risk Fix

Changed only the two readback verification checks; **MD cue XML, wire framing, `CompanyCombatService`, client sender, and health-read scheduling are untouched**:

1. **`tools/dev/authority_company_gun_damage.lua` (`X4AuthorityCompanyGunDamage.Result`):**
   - Accept exact all-zero wreck Lua readback (`observed.shield == 0 and observed.hull == 0 and observed.shieldmax == 0 and observed.hullmax == 0`) **only** when `killed` is `'1'`/`'true'`, `hp.BeforeHull > 0`, `hp.MaxHull > 0`, `hp.AfterShield == 0`, and `hp.AfterHull == 0`.
   - Continues sending MD's immediate values alongside Lua's `0|0|0|0` readback on the wire (exact existing 15-field `applied` format).
2. **`tools/dev/authority_company_gun_damage.py` (`_read_health`):**
   - After `_stock_impact()` validates `killed == (hull == 0)`, `before_hull > 0`, `max_hull > 0`, and `after[:2] == (0, 0)`, accept `wreck_readback = after[:2] == (0, 0) and observed == (0, 0, 0, 0)`.
   - All invalid cases stay strictly fail-closed (`NativeGunDamageUncertain` / `unknown`): non-lethal `0,0,0,0`, partial zero maxima (`5750, 0` or `0, 6400`), mismatched nonzero maxima, live Lua shield/hull on a kill, `killed=0` with zero hull, `ok=false`/`nil` Lua readback, and `GUN_GROUP` (`AfterHull > 0` required).

---

## 4. Offline Verification (Synthetic / Mocked MD — Not Live X4)

All tests below ran offline on Windows (`Python 3.12.10` `.venv` + `lupa.luajit21`) against synthetic/mocked X4 MD state modeled from `tools/dev/authority_company_gun_damage.xml` and the Bazzite observations; **none of these are live X4 runs**:

- **New end-to-end offline suite (`tests/test_authority_company_combat_npc_death.py`):** **8/8 passed** (exercising real `CompanyCombatService`, `CompanyCombatSender`, `NativeGunDamageProvider`, and real `tools/dev/authority_company_gun_damage.lua` via `lupa.luajit21`):
  - `_read_health` parser checks for immediate kill, post-wreck `(0,0,0,0)` kill (shielded & shieldless), and 8 invalid/partial negative controls.
  - Same-batch `[kill 943, queued hit 2 on 943, hit 3 on surviving 1074]` across both `ordered_hits=False` and `ordered_hits=True` for all 4 timing/lifecycle states (`immediate_lua`, `post_wreck_lua`, `pre_wreck_second_hit`, `despawned_after_kill`) plus exact-retry cache replay.
  - Cumulative-lethal `GUN_GROUP` (`30` hull vs `26+26+26` + survivor) -> zero-write group refusal -> `single_fallback` -> Hit 1 `(0,4)`, Hit 2 `(0,0)` post-wreck confirm, Hit 3 `rejected`, Hit 4 `confirmed`.
  - Two-pilot simultaneous queue (Pilot 1 killing `943` while Pilot 2 queues `[943, 1700]`) in both serial and grouped modes.
  - Client disconnect + exact retry mid-`single_fallback` after NPC kill (zero duplicate MD dispatches).
  - Full `CompanyCombatSender` log-driven burst through `943` kill + rejected second hit + surviving `1074` hit (`observed=3, confirmed=2, rejected=1, unknown=0, failure=None`).
  - Negative controls: corrupted partial wreck readback `(0,0,5750,0)` and vanished target before Hit 1 Lua readback (`ok=False`) both fail closed (`unknown`, `_fenced=True`, `poisoned=True`, Hit 2 & 3 never dispatched to MD).
- **Existing Python combat suites:** **52/52 passed** (`test_authority_company_combat_service.py` 24/24, `test_authority_company_combat_tls.py` 8/8, `test_authority_company_combat_source.py` 14/14, `test_authority_company_combat_client.py` 6/6) -> **60/60 total Python tests passed**.
- **Standalone LuaJIT driver test (`tests/authority_company_gun_damage.lua` via `lupa.luajit21`):** **Passed** (including new post-wreck `0,0,0,0` kill, `refused|0x102|0x103` registered-wreck refusal, non-lethal `0,0,0,0` fail-closed, and partial zero-maxima fail-closed cases).
- **Linux-only provider test (`tests/test_authority_company_gun_damage.py`):** Updated `test_lethal_root_readbacks_must_independently_agree` with post-wreck confirm, follow-up `refused` without fencing, and partial zero-maxima fail-closed checks; verified syntax via `py_compile` on Windows (full execution requires Linux `/proc/self/stat` and `luajit` CLI).
