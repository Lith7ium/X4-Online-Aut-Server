-- Run from the source root with: luajit tests/authority_flight_lab.lua
-- Checks the flight lab's link model, flights, sender and receiver score.
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

-- poor_has_stalls: at least one gap between consecutive arrivals of 0.2 + 2 * 0.120 s or more
local p, stall = arrivals('poor', 1, 3000), false
for i = 2, #p do stall = stall or p[i] - p[i - 1] >= .44 end
assert(stall, 'poor profile produced no retransmission stall')

-- good_has_no_stalls
local g = arrivals('good', 1, 3000)
for i = 2, #g do assert(g[i] - g[i - 1] < .2) end

print('flight lab link ok')

-- path_two_legs_one_sample_per_round_trip_newest_wins
local departures = {}
for i = 0, 99 do departures[#departures + 1] = i * .1 end
local function path(profile, seed) return L.path(profile, seed, departures) end
for i, e in ipairs(path('loopback', 1)) do assert(e.k == i and e.arrival == departures[i]) end
assert(#path('good', 1) == 100, 'a 60 ms round trip keeps up with 10 samples a second')
for _, profile in ipairs({'home', 'poor'}) do
    local got = path(profile, 1)
    assert(#got < 100, profile .. ' should skip samples while waiting for acknowledgements')
    for i, e in ipairs(got) do
        assert(e.arrival >= departures[e.k] + 2 * L.PROFILES[profile].delay)
        if i > 1 then assert(e.k > got[i - 1].k and e.arrival >= got[i - 1].arrival) end
    end
end
assert(#path('poor', 1) < #path('home', 1))

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

-- loopback_follows_closely
-- 0.15 s of interpolation delay plus one engine frame at 345 m/s is about 58 m of lag
local r = L.run{flight = 'steady_345', profile = 'loopback', mode = 'continuous'}
assert(r.position_error_m.p95 < 80 and r.faults == 0 and r.refusals == 0, L.json(r))

-- deterministic
assert(L.json(L.scorecard{}) == L.json(L.scorecard{}))

-- long_frame_is_scored: a 1 s gap in frames cannot crash a run, and is counted as a freeze
local lf = L.run{flight = 'steady_345', profile = 'good', mode = 'ordinary', frames = {long = {{at = 8, length = 1}}}}
assert(lf.longest_freeze_s >= 0.99, L.json(lf))

-- out_of_radar_is_counted
local far = L.run{flight = 'fast_travel', profile = 'loopback', mode = 'continuous', radar_m = 1000}
assert(far.out_of_radar > 0)

-- trace_sees_receiver_events
local kinds = {}
L.run{flight = 'close_turns', profile = 'loopback', mode = 'continuous', trace = function(_, kind) kinds[kind] = true end}
assert(kinds.readback, 'trace saw no readback')

print('flight lab receiver ok')

-- speed_limit_ends_the_travel_freeze
for _, mode in ipairs({'ordinary', 'continuous'}) do
    local base = L.run{flight = 'travel_brake', profile = 'poor', mode = mode}
    local fixed = L.run{flight = 'travel_brake', profile = 'poor', mode = mode, wan = {speed_limit = true}}
    assert(fixed.longest_freeze_s < 0.5 and fixed.refusals <= 1, L.json(fixed))
    assert(fixed.position_error_m.p95 <= base.position_error_m.p95, mode)
end
-- version3_has_no_faults
assert(L.run{flight = 'steady_345', profile = 'poor', mode = 'ordinary', version = 3, wan = {speed_limit = true}}.faults == 0)

print('flight lab speed limit ok')
