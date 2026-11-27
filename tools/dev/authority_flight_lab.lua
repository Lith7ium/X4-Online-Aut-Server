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

-- Identity of the observed pilot in every sample and in the receiver.
L.EPOCH, L.GENERATION = ('a'):rep(32), ('b'):rep(32)
L.OWN_INDEX, L.TARGET_INDEX, L.SECTOR = 1, 2, 'sector_a'
L.OWN_POSE = {x = 0, y = 0, z = 3000, yaw = 0, pitch = 0, roll = 0}

local STEP, STORE = .001, .01
local rad = math.rad

-- Each flight sets the true speed and yaw for the next integration step and
-- returns true once the flight has ended. Yaw 0 faces +z; positive yaw turns
-- towards +x.
local FLIGHT_SCRIPTS = {
    close_turns = {start = {0, 0, 2500}, update = function(t, _, s)
        s.v, s.yaw = 100, rad(30) * math.sin(2 * math.pi * t / 8)
        return t >= 30
    end},
    steady_345 = {start = {-3000, 0, 2000}, update = function(t, _, s)
        s.v, s.yaw = 345, math.pi / 2
        return t >= 20
    end},
    travel_brake = {start = {-2000, 0, 2000}, update = function(t, dt, s)
        s.yaw = math.pi / 2
        if s.rest_since then return t >= s.rest_since + 5 end
        if t < 1 then
            s.v = 0
        elseif not s.braking and s.dist >= 3345.84 then
            s.braking = true
        end
        if s.braking then
            s.v = math.max(0, s.v - 129.3 * dt)
            if s.v == 0 then s.rest_since, s.brake_end = t, t end
        elseif t >= 1 then
            s.v = math.min(650, 100 * (t - 1))
        end
        return false
    end},
    turn_while_moving = {start = {-1000, 0, 1000}, update = function(t, _, s)
        s.v = 150
        s.yaw = t < 3 and 0 or (t < 6 and rad(30) * (t - 3) or math.pi / 2)
        return t >= 15
    end},
    fast_travel = {start = {-20000, 0, 2000}, update = function(t, dt, s)
        s.yaw = math.pi / 2
        if s.rest_since then return t >= s.rest_since + 3 end
        if not s.cruise_since then
            s.v = math.min(3649, 500 * t)
            if s.v == 3649 then s.cruise_since = t end
        elseif t < s.cruise_since + 5 then
            s.v = 3649
        else
            s.v = math.max(0, s.v - 500 * dt)
            if s.v == 0 then s.rest_since, s.brake_end = t, t end
        end
        return false
    end},
}
L.FLIGHTS = {'close_turns', 'steady_345', 'travel_brake', 'turn_while_moving', 'fast_travel'}

local POSE_FIELDS = {'x', 'y', 'z', 'vx', 'vy', 'vz', 'yaw', 'pitch', 'roll', 'speed'}

-- Integrates the script at 1 ms, keeps every 10 ms and interpolates between.
function L.flight(name)
    local script = assert(FLIGHT_SCRIPTS[name], 'unknown flight')
    local s = {v = 0, yaw = 0, dist = 0}
    local x, y, z = script.start[1], script.start[2], script.start[3]
    local rows, t, step, done = {}, 0, 0, false
    local per_store = math.floor(STORE / STEP + .5)
    while true do
        done = script.update(t, STEP, s)
        local vx, vz = s.v * math.sin(s.yaw), s.v * math.cos(s.yaw)
        if step % per_store == 0 then
            rows[#rows + 1] = {x = x, y = y, z = z, vx = vx, vy = 0, vz = vz,
                               yaw = s.yaw, pitch = 0, roll = 0, speed = s.v}
        end
        if done and step % per_store == 0 then break end
        x, z = x + vx * STEP, z + vz * STEP
        s.dist = s.dist + s.v * STEP
        step = step + 1
        t = step * STEP
    end
    local duration = (#rows - 1) * STORE
    local function at(time)
        time = math.min(math.max(time, 0), duration)
        local i = math.min(math.floor(time / STORE), #rows - 2)
        local f = (time - i * STORE) / STORE
        local a, b, pose = rows[i + 1], rows[i + 2], {}
        for _, key in ipairs(POSE_FIELDS) do pose[key] = a[key] + (b[key] - a[key]) * f end
        return pose
    end
    return {name = name, duration = duration, brake_end = s.brake_end, at = at}
end

-- Samples the true flight as the pilot's client does, every 0.10 s, in the
-- format company_cockpit_playback.lua accepts. Velocity is left out when any
-- component is above 5,000 m/s, as the real sampler does.
function L.sender(flight, opts)
    opts = opts or {}
    local interval, version = opts.interval or .10, opts.version or 4
    local source_offset, capture_offset = opts.source_offset or 100, opts.capture_offset or 200
    local out, k = {}, 0
    while k * interval <= flight.duration + 1e-9 do
        local t = k * interval
        local p = flight.at(t)
        local velocity = {x = p.vx, y = p.vy, z = p.vz}
        if math.abs(p.vx) > 5000 or math.abs(p.vy) > 5000 or math.abs(p.vz) > 5000 then velocity = nil end
        local sample = {
            version = version, epoch = L.EPOCH, index = L.TARGET_INDEX, generation = L.GENERATION,
            sector_macro = L.SECTOR, sequence = k + 1, source_time = t + source_offset,
            capture_time = t + capture_offset, x = p.x, y = p.y, z = p.z, real_velocity = velocity,
        }
        if version == 4 then sample.attitude = {yaw = p.yaw, pitch = p.pitch, roll = p.roll} end
        out[#out + 1] = {departure = t, sample = sample}
        k = k + 1
    end
    return out
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
