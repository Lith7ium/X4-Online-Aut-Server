# Flight Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure, on one PC and without X4, how well a receiving cockpit follows another pilot's ship over a poor internet connection, then prove fixes against that score.

**Architecture:** A LuaJIT lab feeds scripted flights through a seeded TCP-like link model into the real cockpit playback modules, using a fake engine. It scores every flight on every link profile. A Python wrapper prints the table and keeps a baseline. A Python byte relay applies the same link model to the real movement service and client to check the model. Fixes live in the playback modules behind one opt-in `wan` option table and one client flag.

**Tech Stack:** LuaJIT 2.1 (the `luajit` command, run through WSL Ubuntu-24.04 on this PC), Python 3.12 standard library, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-27-flight-lab-design.md`

## Global Constraints

- Branch `contrib/cobalt-flight-lab-20261127`, made from `trial/velocity-aware-playback-20261126` at `61d0d0fc`. Push only to `origin` (Lith7ium fork), never force, never `main`.
- No X4 launch and no live connection. Never load, write or delete the player's normal X4 profile, saves or settings.
- Write original code. Do not modify or redistribute Egosoft files. Keep `.local/` out of commits.
- Stay out of target speed and native velocity writes (Neon's `work/neon-cockpit-native-velocity*`).
- No new dependencies: LuaJIT 2.1 and the Python standard library only. No Lua 5.3 integer operators.
- Every result is seeded and the same on every run.
- Fixes are off by default. With every fix off, the scorecard equals the baseline byte for byte.
- Reports and docs say that lab evidence is synthetic.
- Pilot sample interval: 0.10 s (`src/authority/lua/company_motion_source.lua:301`).
- Link profiles, copied from the spec:

  | Profile | Delay | Jitter | Loss |
  | --- | --- | --- | --- |
  | loopback | 0 | 0 | 0 |
  | good | 0.030 s | 0.005 s | 0 |
  | home | 0.060 s | 0.020 s | 0.5% |
  | poor | 0.120 s | 0.040 s | 2%, in bursts of 3 |

- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**Running commands.** Windows has no `luajit`. Run every Lua and Python test of this plan in WSL against the worktree:

```sh
wsl.exe -d Ubuntu-24.04 -u x4test -- bash -lc 'cd "/mnt/c/Users/jensv/Documents/Projects/Claude does things/X4-cobalt-flightlab" && <command>'
```

`<command>` is shown in each step. `PY` below stands for `PYTHONPATH=src:tools/dev:tests ~/x4/.venv/bin/python`; write it out in full. From Git Bash, prefix the command with `MSYS_NO_PATHCONV=1`.

## Review Focus

1. **Long receiver frames and stalled clocks.** A 1 s frame or a repeated frame time must be scored as a freeze, not crash the run. Owned by Task 3 (`long_frame_is_scored`).
2. **A ship that leaves radar range.** The fast flight must stay countable. Leaving radar is counted in `out_of_radar`, never silently scored as a freeze. Owned by Task 3 (`out_of_radar_is_counted`).
3. **A fix that only wins on one seed.** Each fix's gain must hold on seeds 1, 2 and 3. Owned by Tasks 6–8 (`gain_holds_on_three_seeds`).
4. **Older version 3 samples (no attitude).** Fixes must not fault on version 3. Owned by Tasks 6–8 (`version3_has_no_faults`).
5. **Fixes switched off.** The scorecard must equal the baseline exactly. Owned by Tasks 6–8 (`off_equals_baseline`).

---

### Task 1: Seeded random numbers and the link model

**Files:**
- Create: `tools/dev/authority_flight_lab.lua`
- Create: `tests/authority_flight_lab.lua`
- Create: `tests/test_authority_flight_lab.py`

**Interfaces:**
- Produces, in the module table `L` returned by `dofile('tools/dev/authority_flight_lab.lua')`:
  - `L.PROFILES[name] = {delay=, jitter=, loss=, burst=}` for `loopback`, `good`, `home`, `poor` (values from Global Constraints; `burst` is 1 except `poor` = 3).
  - `L.rng(seed) -> function() -> number` in [0, 1). Park–Miller: `state = state * 16807 % 2147483647`, return `state / 2147483647`. Exact in doubles, the same on every platform.
  - `L.link(profile_name, seed) -> link`, with `link:arrival(departure) -> number`.
  - `L.json(value) -> string`: sorted keys, integers as integers, other numbers as `%.3f`, `nil` fields omitted.
  - CLI: `luajit tools/dev/authority_flight_lab.lua --arrivals PROFILE SEED N` prints a JSON list of N arrival times for departures 0, 0.1, …, (N−1)·0.1.

Link model. For each packet, draw `u1`, then `u2`, always in this order, from the link's own `L.rng(seed)`:
1. `own = departure + delay + jitter * (-log(1 - u1))` (exponential tail, mean = jitter).
2. The packet is lost if a burst is in progress, or if `u2 < loss`, which starts a burst covering this packet and the next `burst - 1`. A lost packet adds `rto = 0.2 + 2 * delay`. Then draw `u3` repeatedly: while `u3 < loss`, add the doubled previous wait.
3. `arrival = max(previous_arrival, own + lost_time)`. TCP delivers in order.

The Python model in Task 5 makes exactly the same draws in the same order.

- [ ] **Step 1: Write the failing Lua test** `tests/authority_flight_lab.lua` (run as a script, like `tests/authority_company_cockpit_continuous_playback.lua`):

```lua
local L = dofile('tools/dev/authority_flight_lab.lua')
local function arrivals(profile, seed, n)
    local link, out = L.link(profile, seed), {}
    for i = 0, n - 1 do out[#out + 1] = link:arrival(i * .1) end
    return out
end
-- loopback_is_exact
for i, a in ipairs(arrivals('loopback', 1, 50)) do assert(a == (i - 1) * .1) end
-- in_order_and_never_early
for _, p in ipairs({'good', 'home', 'poor'}) do
    local a = arrivals(p, 7, 2000)
    for i = 1, #a do
        assert(a[i] >= (i - 1) * .1 + L.PROFILES[p].delay)
        if i > 1 then assert(a[i] >= a[i - 1]) end
    end
end
-- seeded
local a, b, c = arrivals('poor', 3, 500), arrivals('poor', 3, 500), arrivals('poor', 4, 500)
local same, differ = true, false
for i = 1, 500 do same = same and a[i] == b[i]; differ = differ or a[i] ~= c[i] end
assert(same and differ)
-- poor_has_stalls: at least one gap between consecutive arrivals of 0.2 + 2*0.120 s or more
local p, stall = arrivals('poor', 1, 3000), false
for i = 2, #p do stall = stall or p[i] - p[i - 1] >= .44 end
assert(stall, 'poor profile produced no retransmission stall')
-- good_has_no_stalls
local g = arrivals('good', 1, 3000)
for i = 2, #g do assert(g[i] - g[i - 1] < .2) end
print('flight lab link ok')
```

- [ ] **Step 2: Hook it into the suite** in `tests/test_authority_flight_lab.py`: class `FlightLabLuaTests`, `@unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')`, method `test_lab_lua_checks` running `['luajit', 'tests/authority_flight_lab.lua']` with `cwd` = repo root, `timeout=120`, `check=True`. Copy the pattern from `tests/test_authority_company_hull_playback_x4.py`.

- [ ] **Step 3: Run and see it fail**

Run: `luajit tests/authority_flight_lab.lua`
Expected: error `cannot open tools/dev/authority_flight_lab.lua`.

- [ ] **Step 4: Implement** `L.PROFILES`, `L.rng`, `L.link`, `L.json` and the `--arrivals` CLI in `tools/dev/authority_flight_lab.lua`. The CLI runs only when the file is the main script: `if arg and arg[0] and arg[0]:match('authority_flight_lab%.lua$') then ... end`.

- [ ] **Step 5: Run and see it pass**

Run: `luajit tests/authority_flight_lab.lua && PY -m unittest test_authority_flight_lab -v`
Expected: `flight lab link ok`, then `OK`.

- [ ] **Step 6: Commit** `git add tools/dev/authority_flight_lab.lua tests/authority_flight_lab.lua tests/test_authority_flight_lab.py` with message "Add a seeded TCP-like link model for the flight lab".

### Task 2: Flights and the sender

**Files:**
- Modify: `tools/dev/authority_flight_lab.lua`
- Modify: `tests/authority_flight_lab.lua`

**Interfaces:**
- Consumes: `L.rng` (Task 1).
- Produces:
  - `L.FLIGHTS = {'close_turns', 'steady_345', 'travel_brake', 'turn_while_moving', 'fast_travel'}`.
  - `L.flight(name) -> {name=, duration=, brake_end=(number or nil), at=function(t) -> pose}`, where `pose = {x,y,z, vx,vy,vz, yaw,pitch,roll, speed}`. Metres, metres per second, radians. Yaw 0 faces +z and positive yaw turns towards +x: `vx = speed*sin(yaw)`, `vz = speed*cos(yaw)`. Integrate at 1 ms when the flight is built, store every 10 ms, and interpolate linearly in `at`.
  - `L.sender(flight, opts) -> list of {departure=, sample=}`. `opts = {interval=.10, version=4, source_offset=100, capture_offset=200}`. `sample` has the fields `company_cockpit_playback.lua` accepts (see `f:sample` in `tests/authority_company_cockpit_continuous_playback.lua`): `version, epoch, index, generation, sector_macro, sequence, source_time, capture_time, x, y, z, real_velocity, attitude`. `real_velocity` is nil when any component's absolute value exceeds 5000. `attitude` is present only for version 4.

Flights. The own ship sits at (0, 0, 3000). Every flight starts within 5 km of it.

| Name | Script |
| --- | --- |
| close_turns | 100 m/s; yaw = 30°·sin(2πt/8); 30 s; starts at (0, 0, 2500) |
| steady_345 | 345 m/s; yaw 90° (+x); 20 s; starts at (−3000, 0, 2000) |
| travel_brake | rest for 1 s, then +100 m/s² to 650 m/s, cruise, start braking at 3,345.84 m along the path at −129.3 m/s² to rest, then 5 s at rest; yaw 90°; starts at (−2000, 0, 2000); `brake_end` = the time it reaches rest |
| turn_while_moving | 150 m/s; yaw 0 for 3 s, then turn at 30°/s for 3 s (to 90°), then straight for 9 s; starts at (−1000, 0, 1000) |
| fast_travel | +500 m/s² to 3,649 m/s, cruise 5 s, −500 m/s² to rest, then 3 s at rest; yaw 90°; starts at (−20000, 0, 2000); `brake_end` set |

- [ ] **Step 1: Add failing tests** to `tests/authority_flight_lab.lua`:

```lua
-- flights_are_continuous_and_match_their_speeds
for _, name in ipairs(L.FLIGHTS) do
    local f = L.flight(name)
    local prev = f.at(0)
    for t = .01, f.duration, .01 do
        local p = f.at(t)
        local step = math.sqrt((p.x - prev.x)^2 + (p.y - prev.y)^2 + (p.z - prev.z)^2)
        assert(step <= p.speed * .01 + 0.6, name .. ' jumps at ' .. t)
        prev = p
    end
end
assert(math.abs(L.flight('steady_345').at(10).speed - 345) < 1e-6)
local tb = L.flight('travel_brake')
local peak = 0
for t = 0, tb.duration, .01 do peak = math.max(peak, tb.at(t).speed) end
assert(math.abs(peak - 650) < 1e-6 and tb.at(tb.duration).speed == 0)
local rest = tb.at(tb.duration)
assert(math.abs((rest.x + 2000) - 4979) < 5, 'travel_brake should stop near 4,980 m')
-- sender_rate_and_velocity_rule
local s = L.sender(L.flight('fast_travel'), {})
assert(#s >= 10 and math.abs(s[2].departure - s[1].departure - .1) < 1e-9)
for _, e in ipairs(s) do assert(e.sample.real_velocity ~= nil) end   -- 3,649 < 5,000 on every axis
for _, e in ipairs(L.sender(L.flight('steady_345'), {version = 3})) do assert(e.sample.attitude == nil) end
print('flight lab flights ok')
```

- [ ] **Step 2: Run and see it fail**

Run: `luajit tests/authority_flight_lab.lua`
Expected: error `attempt to index field 'FLIGHTS'` or similar.

- [ ] **Step 3: Implement** `L.FLIGHTS`, `L.flight` and `L.sender`. Velocity in samples is the flight's true velocity.

- [ ] **Step 4: Run and see it pass**

Run: `luajit tests/authority_flight_lab.lua`
Expected: `flight lab link ok` and `flight lab flights ok`.

- [ ] **Step 5: Commit** with message "Add scripted flights and a 10 Hz sender to the flight lab".

### Task 3: Receiver, score and scorecard

**Files:**
- Modify: `tools/dev/authority_flight_lab.lua`
- Modify: `tests/authority_flight_lab.lua`

**Interfaces:**
- Consumes: `L.link`, `L.flight`, `L.sender`, `L.json`.
- Produces:
  - `L.run(spec) -> result`. `spec = {flight=, profile=, seed=1, mode='ordinary'|'continuous', version=4, wan=nil, link=nil}`. `spec.link`, when given, is a `function(departure) -> arrival` that replaces the profile (Task 8 uses it).
  - `result = {key='<flight>/<profile>/<mode>/v<version>/s<seed>', position_error_m={median=, p95=, max=}, heading_error_deg={median=, p95=}, longest_freeze_s=, refusals=, stale_cutoffs=, faults=, jumps=, backwards=, out_of_radar=, overshoot_m=(only for flights with brake_end)}`.
  - `L.modules = {buffer=, playback=, hull=, native=, cockpit=}`: the five real modules, loaded once when the lab loads. `hull` is `X4LAN_COMPANY_HULL_PLAYBACK` after `dofile`.
  - `L.scorecard(opts) -> list of results` over all `L.FLIGHTS` × profiles (loopback, good, home, poor) × modes (ordinary, continuous). `opts = {seeds={1}, version=4, wan=nil}`. Sorted by `key`.
  - CLI: `luajit tools/dev/authority_flight_lab.lua --scorecard [--seeds 1,2,3] [--version 3|4] [--wan name,name]` prints `L.json(scorecard)`.

Receiver. Build it exactly like `fixture()` in `tests/authority_company_cockpit_continuous_playback.lua`, using the same five real modules, `cockpit.new_multi` with `allow_reacquire_align = true` (as `new_multi_x4` does), `slot_names = {'cockpit3'}`, `continuous_playback = (mode == 'continuous')` and `wan = spec.wan`. `current_radar_range` returns 100000. The fake engine applies a pending write on the next frame. Pass `record` and `fault` callbacks that count events.

Clock. Lab time t is the same on sender and receiver. A sample is delivered when `arrival <= t` at the start of a frame, in arrival order, with `arrival` as the 4th argument of `controller:sample`. Frames run every 1/60 s. One 0.25 s frame happens at a seeded random time in each 10 s of flight.

Score, at every frame after the pending write is applied:
- position error = distance between the shown pose (`f.actual`) and `flight.at(t)`;
- heading error = the angle between the forward vectors `(sin y·cos p, −sin p, cos y·cos p)` of the shown and true attitudes, in degrees;
- a frame is *stuck* when the true ship moved more than 0.5 m during the frame and the shown ship moved less than 10% of that. `longest_freeze_s` is the longest run of stuck frames;
- a *jump* is a frame in which the shown ship moved more than `max(100, 2 * true_speed * dt)`;
- `backwards` = frames in which the shown ship's movement points against the true velocity (negative dot product, true speed above 1 m/s);
- `refusals` = `stats.refusals_over_50m` and `stale_cutoffs` from `controller:slot_stats('cockpit3')`;
- `out_of_radar` = frames in which the true ship is more than 100 km from the own ship;
- `overshoot_m` = after `brake_end`, the largest distance of the shown ship beyond the true rest position, measured along the final direction of travel (0 if it never passes it).

Percentiles use the nearest-rank method over all frames from the first applied write to the end of the flight.

- [ ] **Step 1: Add failing tests**:

```lua
-- loopback_follows_closely
local r = L.run{flight = 'steady_345', profile = 'loopback', mode = 'continuous'}
-- 0.15 s of interpolation delay plus one engine frame at 345 m/s is about 58 m of lag
assert(r.position_error_m.p95 < 80 and r.faults == 0 and r.refusals == 0, L.json(r))
-- deterministic
assert(L.json(L.scorecard{}) == L.json(L.scorecard{}))
-- long_frame_is_scored: a 1 s gap in frames cannot crash a run, and is counted as a freeze
local lf = L.run{flight = 'steady_345', profile = 'good', mode = 'ordinary', frames = {long = {{at = 8, length = 1}}}}
assert(lf.longest_freeze_s >= 0.99, L.json(lf))
-- out_of_radar_is_counted
local far = L.run{flight = 'fast_travel', profile = 'loopback', mode = 'continuous', radar_m = 1000}
assert(far.out_of_radar > 0)
print('flight lab receiver ok')
```

`spec.frames.long` (a list of `{at=, length=}`) replaces the seeded long frames, and `spec.radar_m` overrides the 100 km range. Both exist only for tests.

- [ ] **Step 2: Run and see it fail**

Run: `luajit tests/authority_flight_lab.lua`
Expected: error at `L.run`.

- [ ] **Step 3: Implement** the receiver, the score, `L.run`, `L.scorecard` and the `--scorecard` CLI.

- [ ] **Step 4: Run and see it pass**

Run: `luajit tests/authority_flight_lab.lua && luajit tools/dev/authority_flight_lab.lua --scorecard | head -c 400`
Expected: all four `ok` lines, then the start of a JSON list.

- [ ] **Step 5: Commit** with message "Score real cockpit playback against scripted flights in the flight lab".

### Task 4: Python wrapper, baseline and the reproduction gate

**Files:**
- Create: `tools/dev/authority_flight_lab.py`
- Create: `tests/fixtures/flight_lab_baseline.json`
- Modify: `tests/test_authority_flight_lab.py`

**Interfaces:**
- Consumes: the `--scorecard` CLI (Task 3).
- Produces, in `authority_flight_lab.py`:
  - `run_lab(root: Path, *, seeds=(1,), version=4, wan=()) -> list[dict]`. It runs `luajit tools/dev/authority_flight_lab.lua --scorecard ...` with `cwd=root`, `timeout=600`, and parses the JSON.
  - `format_table(results: list[dict]) -> str`: one row per key, with columns p95 m, max m, heading p95°, freeze s, refusals, jumps and overshoot m.
  - `compare(baseline: list[dict], results: list[dict]) -> list[str]`: one line per key whose fields differ.
  - `main(argv=None) -> int`, with flags `--seeds`, `--version`, `--wan`, `--write-baseline` and `--compare`. `--compare` returns 1 when there are differences.
  - `BASELINE = Path('tests/fixtures/flight_lab_baseline.json')`.

- [ ] **Step 1: Add a failing test** to `tests/test_authority_flight_lab.py`: class `FlightLabScorecardTests`, skipped without luajit. `test_scorecard_matches_baseline` asserts `compare(json.loads(BASELINE.read_text()), run_lab(root)) == []`.

- [ ] **Step 2: Implement** `tools/dev/authority_flight_lab.py`. Write the baseline with `PY tools/dev/authority_flight_lab.py --write-baseline`.

- [ ] **Step 3: Run and see it pass**

Run: `PY -m unittest test_authority_flight_lab -v && PY tools/dev/authority_flight_lab.py`
Expected: `OK`, then the table.

- [ ] **Step 4: Commit** with message "Keep a baseline flight lab scorecard for the current playback".

- [ ] **Step 5: Gate. Read the table.** Write down, for `travel_brake` and `steady_345` under `poor`, in both modes: refusals, longest freeze and p95 error.
  - If either flight shows refusals > 0 or a freeze of 1 s or more in either mode, the 26 November freeze is reproduced. Note the numbers for the report and go on.
  - Otherwise, stop. Tell the user the lab did not reproduce the freeze, with the numbers, and redo the plan before Task 6. Task 5 may still run.

### Task 5: Real transport through an impaired relay

**Files:**
- Create: `tools/dev/authority_flight_lab_relay.py`
- Create: `tests/test_authority_flight_lab_relay.py`

**Interfaces:**
- Produces, in `authority_flight_lab_relay.py`:
  - `PROFILES: dict[str, dict]`, equal to `L.PROFILES`.
  - `class LinkModel(profile: str, seed: int)` with `arrival(departure: float) -> float`. Same algorithm and the same Park–Miller draws in the same order as Task 1.
  - `class ImpairedRelay(target_port: int, profile: str, seed: int)`. It listens on 127.0.0.1, with `.port`, `.close()` and `.log`. Each accepted connection forwards both directions. Each received chunk is released at `start + model.arrival(received_at - start)` on `time.monotonic()`, in order, with each direction having its own `LinkModel(profile, seed + direction)` (direction 0 is client to server, 1 is server to client). `.log` is a list of `(direction, received_at, released_at, nbytes)`.
  - A sample's model delay is the sum, over both relays, of `released_at - received_at` for the first chunk each relay received in the sample's direction after the sample was queued.

- [ ] **Step 1: Write failing tests** in `tests/test_authority_flight_lab_relay.py`:
  - `test_python_and_lua_models_agree` (skipped without luajit): for each profile at seed 5, the CLI `--arrivals PROFILE 5 2000` equals `[LinkModel(p, 5).arrival(i * .1) for i in range(2000)]` within 1e-9.
  - `test_relay_keeps_bytes_and_order`: an echo server behind `ImpairedRelay(port, 'home', 1)`. Send 300 numbered 64-byte chunks 5 ms apart; the bytes come back identical and in order.
  - `test_relay_timing_follows_model`: through `ImpairedRelay(port, 'good', 1)` to a sink server, the median of (measured arrival − model arrival) over 200 chunks is under 0.010 s.
  - `test_motion_path_adds_little_beyond_link`: build the loopback service and clients as `test_cockpit_radar_view_and_independent_map_over_real_loopback_service` in `tests/test_authority_company_cockpit_playback.py` does, but connect both `CompanyMotionClient`s through their own `ImpairedRelay(service.port, 'home', seed)`. The sender queues one version 3 sample every 0.1 s for 10 s. Record when each `REMOTE|cockpit|` message reaches the patched `observer._send`. Assert: every sequence the models deliver arrives, in order. The extra delay beyond the two relays' combined model has a median under 0.020 s and a p95 under 0.050 s. Print the numbers for the report.

- [ ] **Step 2: Run and see it fail**

Run: `PY -m unittest test_authority_flight_lab_relay -v`
Expected: `ModuleNotFoundError: authority_flight_lab_relay`.

- [ ] **Step 3: Implement** `tools/dev/authority_flight_lab_relay.py`, using one thread per direction and a plain FIFO queue, since arrivals are already in order.

- [ ] **Step 4: Run and see it pass**

Run: `PY -m unittest test_authority_flight_lab_relay -v`
Expected: `OK`. Keep the printed extra-delay numbers.

- [ ] **Step 5: Commit** with message "Check the flight lab link model against the real movement transport".

If `test_motion_path_adds_little_beyond_link` fails on its delay bounds, the Python path adds delay of its own. Record the measured numbers, keep the test as a measurement with bounds just above them, and name it in the report as a finding. Do not change the movement service in this branch.

### Task 6: Fix 1, a speed-aware correction limit

**Files:**
- Modify: `src/authority/lua/company_hull_playback.lua` (`MAX_STEP` checks at lines 93, 127, 135 and `teleport_distance` at 234)
- Modify: `src/authority/lua/company_cockpit_playback.lua` (`M.new` options; `retire` at 203–218; `M.new_multi` passes `options.wan` to each slot)
- Modify: `tests/authority_flight_lab.lua`, `tests/test_authority_flight_lab.py`

**Interfaces:**
- Produces: the playback option `wan = {speed_limit=bool, adaptive_delay=bool, newest_wins=bool}`, accepted by `cockpit.new`, `cockpit.new_multi` and the hull playback factory's `new(cfg)` as `cfg.wan`. Missing means all off. In the lab: `L.run{..., wan = {speed_limit = true}}`, and on the CLI `--wan speed_limit`.

Behaviour with `wan.speed_limit`:
- The correction limit becomes `min(5000, max(50, speed * (gap + 0.25)))`, where `speed` is the magnitude of the newest sample's `real_velocity` (0 when absent) and `gap` is the seconds since the last confirmed write. It applies where `MAX_STEP` is used for corrections and as the ordinary-mode `teleport_distance`.
- `retire('correction_out_of_bounds')` also sets `reacquire_by_index[idx] = true`, so the next sample aligns once instead of being refused for good.

- [ ] **Step 1: Add failing tests**:

```lua
-- speed_limit_ends_the_travel_freeze
for _, mode in ipairs({'ordinary', 'continuous'}) do
    local base = L.run{flight = 'travel_brake', profile = 'poor', mode = mode}
    local fixed = L.run{flight = 'travel_brake', profile = 'poor', mode = mode, wan = {speed_limit = true}}
    assert(fixed.longest_freeze_s < 0.5 and fixed.refusals <= 1, L.json(fixed))
    assert(fixed.position_error_m.p95 <= base.position_error_m.p95, mode)
end
-- version3_has_no_faults
assert(L.run{flight = 'steady_345', profile = 'poor', mode = 'ordinary', version = 3, wan = {speed_limit = true}}.faults == 0)
```

Python, in `FlightLabScorecardTests`:
  - `test_off_equals_baseline`: `run_lab(root)` with no `wan` equals the baseline (this already holds; it must keep holding).
  - `test_gain_holds_on_three_seeds`: with `seeds=(1, 2, 3)` and `wan=('speed_limit',)`, every `travel_brake/poor/*` and `steady_345/poor/*` result has `longest_freeze_s < 0.5`.
  - `test_loopback_not_worse`: every `*/loopback/*` result's p95 is at most the baseline's + 1.0 m.

- [ ] **Step 2: Run and see them fail**

Run: `luajit tests/authority_flight_lab.lua`
Expected: the `speed_limit_ends_the_travel_freeze` assertion fails, with the JSON of the result.

- [ ] **Step 3: Implement** the option in both modules. With `wan` nil, run exactly the old code path.

- [ ] **Step 4: Run all playback tests**

Run: `luajit tests/authority_flight_lab.lua && for t in tests/authority_company_cockpit_continuous_playback.lua tests/authority_company_hull_playback_x4.lua; do luajit $t || exit 1; done && PY -m unittest test_authority_flight_lab test_authority_company_cockpit_playback test_authority_company_hull_playback_x4 -v`
Expected: all pass.

- [ ] **Step 5: Commit** with message "Scale the cockpit correction limit with ship speed behind an opt-in option".

### Task 7: Fix 2, a delay that follows the connection

**Files:**
- Modify: `src/authority/lua/motion_buffer.lua` (add `set_delay`)
- Modify: `src/authority/lua/company_hull_playback.lua`
- Modify: `tests/authority_flight_lab.lua`, `tests/test_authority_flight_lab.py`

**Interfaces:**
- Consumes: the `wan` option (Task 6).
- Produces:
  - `buffer:set_delay(seconds)` on buffers made by `motion_buffer.new`. It asserts `0 <= seconds <= 60`. The existing slew moves the render lag towards the new `offset + delay`.
  - `M.adaptive_delay(transits) -> number` exported from `company_hull_playback.lua`. `transits` holds up to the last 50 values of `arrival - capture_time`. It returns `min(.50, max(.10, .10 + (p95(transits) - min(transits))))`, with p95 by nearest rank, or `.15` with fewer than 10 values.
  - With `wan.adaptive_delay`, each accepted sample updates the transit window and calls `set_delay(M.adaptive_delay(window))`.

- [ ] **Step 1: Add failing tests**:

```lua
local hull = L.modules.hull
local function rep(v, n) local t = {} for i = 1, n do t[i] = v end return t end
assert(hull.adaptive_delay(rep(5, 9)) == .15)
assert(math.abs(hull.adaptive_delay(rep(5, 30)) - .10) < 1e-9)
local spread = {} for i = 1, 50 do spread[i] = 5 + (i % 10) * .02 end   -- p95 - min = 0.18
assert(math.abs(hull.adaptive_delay(spread) - .28) < 1e-9)
local wide = {} for i = 1, 50 do wide[i] = 5 + (i % 10) end
assert(hull.adaptive_delay(wide) == .50)
-- adaptive_delay_helps_on_poor
local b = L.run{flight = 'close_turns', profile = 'poor', mode = 'continuous', wan = {speed_limit = true}}
local a = L.run{flight = 'close_turns', profile = 'poor', mode = 'continuous', wan = {speed_limit = true, adaptive_delay = true}}
assert(a.jumps <= b.jumps and a.longest_freeze_s <= b.longest_freeze_s, L.json(a))
-- version3_has_no_faults
assert(L.run{flight = 'close_turns', profile = 'poor', mode = 'ordinary', version = 3, wan = {adaptive_delay = true}}.faults == 0)
```

Python: extend `test_gain_holds_on_three_seeds` with `wan=('speed_limit', 'adaptive_delay')`: across seeds 1–3, the median of `close_turns/poor/continuous` p95 is not above the Task 6 value.

- [ ] **Step 2: Run and see them fail**

Run: `luajit tests/authority_flight_lab.lua`
Expected: an error at `hull.adaptive_delay`.

- [ ] **Step 3: Implement** `set_delay`, `adaptive_delay` and the wiring.

- [ ] **Step 4: Run the same command as Task 6 Step 4.** Expected: all pass.

- [ ] **Step 5: Commit** with message "Let the cockpit interpolation delay follow measured arrival jitter behind an opt-in option".

If the gain does not hold (`adaptive_delay` gives no better `close_turns`/`home`/`poor` score), revert the wiring and keep only `M.adaptive_delay` and its unit test. Record the numbers for the report and drop it from the client flag in Task 9.

### Task 8: Fix 3, the newest sample wins after a stall

**Files:**
- Modify: `src/authority/lua/motion_buffer.lua`
- Modify: `src/authority/lua/company_hull_playback.lua`
- Modify: `tests/authority_flight_lab.lua`

**Interfaces:**
- Consumes: the `wan` option, and `spec.link` in `L.run` (Task 3).
- Produces: buffer option `catch_up = true` (set by hull playback when `wan.newest_wins`). When a sample arrives more than 0.3 s after the previous arrival, the render lag target resets so the newest sample is shown after the current delay. The slew towards it completes within 0.25 s. No frame moves the shown ship more than the Task 6 limit.

- [ ] **Step 1: Add failing tests**:

```lua
-- stall_then_burst: arrivals normal, then 0.6 s with nothing, then the backlog at once
local function stall(departure)
    if departure >= 8 and departure < 8.6 then return 8.6 + .06 end
    return departure + .06
end
local base = L.run{flight = 'steady_345', profile = 'good', mode = 'continuous', link = stall, wan = {speed_limit = true}}
local fixed = L.run{flight = 'steady_345', profile = 'good', mode = 'continuous', link = stall,
                    wan = {speed_limit = true, newest_wins = true}}
assert(fixed.position_error_m.max < base.position_error_m.max, L.json(fixed))
assert(fixed.jumps == 0)
-- version3_has_no_faults
assert(L.run{flight = 'steady_345', profile = 'poor', mode = 'ordinary', version = 3, wan = {newest_wins = true}}.faults == 0)
```

Also assert `fixed.backwards == 0`. Python: extend `test_gain_holds_on_three_seeds` with `wan=('speed_limit', 'adaptive_delay', 'newest_wins')`. Across seeds 1–3, every `*/home/*` and `*/poor/*` result has `longest_freeze_s < 0.5` and `backwards == 0`.

- [ ] **Step 2: Run and see them fail**

Run: `luajit tests/authority_flight_lab.lua`
Expected: the `fixed.position_error_m.max` assertion fails.

- [ ] **Step 3: Implement** `catch_up` in `motion_buffer.lua` and pass it from hull playback.

- [ ] **Step 4: Run the same command as Task 6 Step 4**, plus `PY tools/dev/authority_flight_lab.py --seeds 1,2,3 --wan speed_limit,adaptive_delay,newest_wins`. Expected: all pass, and the table shows no freeze of 0.5 s or more under `home` and `poor`.

- [ ] **Step 5: Commit** with message "Catch up to the newest movement sample after a stall behind an opt-in option".

**Heading (spec fix 4).** In the baseline, `turn_while_moving/loopback/continuous/v4` has a heading p95 under 5°. If so, version 4 playback already follows heading, so skip this fix and say so in the report. If not, stop and add a task to the plan with the user before going on.

### Task 9: One client flag for the fixes

**Files** (follow the existing `company_cockpit_continuous_playback` path through each one):
- Modify: `tools/dev/authority_client_options.py`
- Modify: `tools/dev/authority_client_native.py`
- Modify: `src/authority/company_motion_source.py`
- Modify: `src/authority/lua/company_cockpit_playback.lua` (`M.new_x4`, `M.new_multi_x4`)
- Modify: `tools/dev/authority_linux_player_client.py`, `tools/dev/authority_windows_fixture_client.py`, `tools/run_authority_windows_client.ps1`
- Test: next to the existing continuous-playback option tests (find them with a search for `continuous_playback` under `tests/`)

**Interfaces:**
- Produces:
  - Flag `--company-cockpit-wan-playback` (PowerShell switch `-CompanyCockpitWanPlayback`). Validation: it requires `--company-motion` and `--company-cockpit-playback`, the same rule as continuous playback, with the error `--company-cockpit-wan-playback requires --company-motion and --company-cockpit-playback`.
  - Options key `cockpit_wan_playback = True`, then the source parameter `cockpit_wan_playback=False`, then the Lua config `config.cockpit_wan_playback == true`. In `new_x4` and `new_multi_x4` that becomes `wan = {speed_limit = true, adaptive_delay = <kept by Task 7>, newest_wins = true}`.

- [ ] **Step 1: Write failing tests**, mirroring the continuous-playback ones: default off; on with both requirements; refused without `--company-motion`; refused without `--company-cockpit-playback`; the source passes `cockpit_wan_playback=true` in its Lua config only when set.
- [ ] **Step 2: Run them and see them fail.** `PY -m unittest <those modules> -v`; expected: an unknown-argument or missing-key failure.
- [ ] **Step 3: Implement** the flag along the path above.
- [ ] **Step 4: Run them and see them pass**, plus Task 6 Step 4's command. Expected: `OK`.
- [ ] **Step 5: Commit** with message "Add an opt-in client flag for internet cockpit playback".

### Task 10: Docs, report, full suite, push and result

**Files:**
- Create: `docs/AUTHORITY-FLIGHT-LAB.md` (how to run it, what each score means, profiles, the flag, that the evidence is synthetic)
- Create: `reports/AUTHORITY-FLIGHT-LAB-20261128.md` (date of writing; covers the Task 4 gate numbers, the Task 5 transport numbers, the baseline table against all fixes for seeds 1–3, and what still needs a real flight)
- Modify: `docs/AUTHORITY-CLIENT-PLAYBACK.md` (one paragraph and a link)

- [ ] **Step 1: Write the doc and the report.** Use plain short sentences, like the existing reports. Generate the tables with `PY tools/dev/authority_flight_lab.py`.
- [ ] **Step 2: Run the full suite** on a native Linux copy (NTFS breaks the file-mode tests). Bundle the branch, fetch it into `~/x4` and run `tools/dev/check.py`, as in the scratch `wsl_check.sh BRANCH OUTNAME`. Expected: the same ERROR/FAIL set as base `61d0d0fc` on Ubuntu 24.04 (39 entries, all environment causes) and nothing new.
- [ ] **Step 3: Commit** with message "Document the flight lab and report baseline against fixes".
- [ ] **Step 4: Push** `git push origin contrib/cobalt-flight-lab-20261127`.
- [ ] **Step 5: Post one result** to Astra in `C0C9ZV35VSB`, compact `X4/6 | from=Cobalt | to=Astra | kind=RESULT` format: the branch head, the flag, the poor-link score before and after, that the evidence is synthetic, and that a real internet flight is the next check. Update memory `x4-repo-move-2026-11.md`.
