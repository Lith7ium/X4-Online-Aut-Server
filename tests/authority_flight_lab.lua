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
