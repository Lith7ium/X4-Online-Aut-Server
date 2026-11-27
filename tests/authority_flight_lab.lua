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
