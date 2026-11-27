-- Flight lab: scripted flights pass through a seeded TCP-like link into the
-- real cockpit playback modules and are scored against the true flight.
-- It needs no X4. Its results are synthetic evidence.
--
-- Run from the source root:
--   luajit tools/dev/authority_flight_lab.lua --arrivals PROFILE SEED N
local L = {}

-- Link profiles: one-way delay and mean extra jitter in seconds, loss rate,
-- and how many packets one loss event covers.
L.PROFILES = {
    loopback = {delay = 0, jitter = 0, loss = 0, burst = 1},
    good = {delay = .030, jitter = .005, loss = 0, burst = 1},
    home = {delay = .060, jitter = .020, loss = .005, burst = 1},
    poor = {delay = .120, jitter = .040, loss = .02, burst = 3},
}
L.PROFILE_NAMES = {'loopback', 'good', 'home', 'poor'}

-- Park-Miller minimal standard generator. Every step is exact in doubles, so
-- the same seed gives the same numbers on every platform and in Python.
function L.rng(seed)
    assert(type(seed) == 'number' and seed % 1 == 0 and seed >= 1 and seed < 2147483647,
           'seed must be an integer from 1 to 2147483646')
    local state = seed
    return function()
        state = state * 16807 % 2147483647
        return state / 2147483647
    end
end

-- A link delivers packets in order, as TCP does. A lost packet is resent
-- after a retransmission timeout, and every later packet waits behind it.
function L.link(profile, seed)
    local p = assert(L.PROFILES[profile], 'unknown link profile')
    local draw = L.rng(seed)
    local previous, last_departure, burst_left = -math.huge, -math.huge, 0
    local link = {profile = profile}
    function link:arrival(departure)
        assert(type(departure) == 'number' and departure >= last_departure, 'departures must not go back')
        last_departure = departure
        local u1, u2 = draw(), draw()
        local own = departure + p.delay + p.jitter * (-math.log(1 - u1))
        local lost = burst_left > 0
        if lost then
            burst_left = burst_left - 1
        elseif u2 < p.loss then
            lost, burst_left = true, p.burst - 1
        end
        if lost then
            local wait = .2 + 2 * p.delay
            local total = wait
            while draw() < p.loss do
                wait = wait * 2
                total = total + wait
            end
            own = own + total
        end
        previous = math.max(previous, own)
        return previous
    end
    return link
end

local function json_string(s)
    return '"' .. s:gsub('[%c"\\]', function(ch)
        if ch == '"' then return '\\"' end
        if ch == '\\' then return '\\\\' end
        return string.format('\\u%04x', ch:byte())
    end) .. '"'
end

local function encode(value, number_format, out)
    local kind = type(value)
    if kind == 'boolean' then
        out[#out + 1] = tostring(value)
    elseif kind == 'number' then
        assert(value == value and value ~= math.huge and value ~= -math.huge, 'json numbers must be finite')
        if value % 1 == 0 and math.abs(value) < 2^53 then
            out[#out + 1] = string.format('%.0f', value)
        else
            out[#out + 1] = string.format(number_format, value)
        end
    elseif kind == 'string' then
        out[#out + 1] = json_string(value)
    elseif kind == 'table' then
        if #value > 0 or next(value) == nil then
            out[#out + 1] = '['
            for i, item in ipairs(value) do
                if i > 1 then out[#out + 1] = ',' end
                encode(item, number_format, out)
            end
            out[#out + 1] = ']'
        else
            local keys = {}
            for key in pairs(value) do
                assert(type(key) == 'string', 'json object keys must be strings')
                keys[#keys + 1] = key
            end
            table.sort(keys)
            out[#out + 1] = '{'
            for i, key in ipairs(keys) do
                if i > 1 then out[#out + 1] = ',' end
                out[#out + 1] = json_string(key) .. ':'
                encode(value[key], number_format, out)
            end
            out[#out + 1] = '}'
        end
    else
        error('json cannot encode ' .. kind)
    end
end

-- Sorted keys, integers as integers, other numbers as %.3f unless a format
-- is given. Fields that are nil do not appear.
function L.json(value, number_format)
    local out = {}
    encode(value, number_format or '%.3f', out)
    return table.concat(out)
end

local function main(argv)
    if argv[1] == '--arrivals' and #argv == 4 then
        local n = assert(tonumber(argv[4]), 'N must be a number')
        local link, out = L.link(argv[2], assert(tonumber(argv[3]), 'SEED must be a number')), {}
        for i = 0, n - 1 do out[#out + 1] = link:arrival(i * .1) end
        io.write(L.json(out, '%.17g'), '\n')
        return 0
    end
    io.stderr:write('usage: luajit tools/dev/authority_flight_lab.lua --arrivals PROFILE SEED N\n')
    return 2
end

-- Run the command line only when this file is the main script, not when a
-- test loads it with dofile.
if arg and arg[0] and debug.getinfo(1, 'S').source == '@' .. arg[0] then
    os.exit(main(arg))
end

return L
