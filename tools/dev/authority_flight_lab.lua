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

-- The real receiving modules, loaded once from the source root.
local SOURCE = 'src/authority/lua/'
X4LAN_MOTION_BUFFER = dofile(SOURCE .. 'motion_buffer.lua')
L.modules = {buffer = X4LAN_MOTION_BUFFER}
L.modules.playback = dofile(SOURCE .. 'motion_playback.lua')
L.modules.hull = dofile(SOURCE .. 'company_hull_playback.lua')
L.modules.native = dofile(SOURCE .. 'company_hull_playback_x4.lua')
L.modules.cockpit = dofile(SOURCE .. 'company_cockpit_playback.lua')

local OWN_ACTOR, TARGET_ACTOR, SECTOR_ID, ZONE = 101, 202, 303, 404
local OBSERVED = {epoch = L.EPOCH, index = L.TARGET_INDEX, actor = '0xca', code = 'TGT-002', sector_macro = L.SECTOR}
local FRAME = 1 / 60
L.MODES = {'ordinary', 'continuous'}

local function distance(a, b)
    return math.sqrt((a.x - b.x)^2 + (a.y - b.y)^2 + (a.z - b.z)^2)
end

local function pose_copy(p)
    return {x = p.x, y = p.y, z = p.z, yaw = p.yaw or 0, pitch = p.pitch or 0, roll = p.roll or 0}
end

local function forward(p)
    local yaw, pitch = p.yaw or 0, p.pitch or 0
    return math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch)
end

local function heading_error(a, b)
    local ax, ay, az = forward(a)
    local bx, by, bz = forward(b)
    local dot = math.max(-1, math.min(1, ax * bx + ay * by + az * bz))
    return math.deg(math.acos(dot))
end

local function percentile(sorted, p)
    if #sorted == 0 then return nil end
    return sorted[math.max(1, math.ceil(p * #sorted))]
end

-- A fake engine: the receiving cockpit's writes become the shown pose on the
-- next frame, and reads return the shown pose.
local function receiver(spec, start, radar_m)
    local r = {now = 0, actual = pose_copy(start), faults = 0, events = {}}
    local observer = {
        matches = function(_, seen_epoch, frame, index, actor, code)
            return seen_epoch == OBSERVED.epoch and frame == OBSERVED.sector_macro and index == OBSERVED.index
                and actor == OBSERVED.actor and code == OBSERVED.code
        end,
        is_active_pilot = function(_, index) return index == L.TARGET_INDEX end,
        current_radar_range = function() return radar_m end,
    }
    local adapter = L.modules.native.new({
        enabled = true, pinned = {epoch = L.EPOCH, issued_count = 2}, own_index = L.OWN_INDEX,
        observer = observer, guard = function() return true end,
    }, {
        cockpit = function() return {actor = OWN_ACTOR, sector = SECTOR_ID, zone = ZONE} end,
        target = function(actor)
            assert(actor == OBSERVED.actor)
            return {actor = TARGET_ACTOR, sector = SECTOR_ID, zone = ZONE, sector_macro = L.SECTOR, code = OBSERVED.code}
        end,
        read = function(actor)
            assert(actor == TARGET_ACTOR)
            return r.actual
        end,
        write = function(actor, sector, target)
            assert(actor == TARGET_ACTOR and sector == SECTOR_ID)
            r.pending = pose_copy(target)
            return true
        end,
        destination_zone = function() return ZONE end,
        within_radar = function(actor, actual, target, radius)
            assert(actor == OWN_ACTOR)
            return distance(L.OWN_POSE, actual) <= radius and distance(L.OWN_POSE, target) <= radius
        end,
    })
    r.controller = L.modules.cockpit.new_multi({
        enabled = true, continuous_playback = spec.mode == 'continuous', allow_reacquire_align = true,
        own_index = L.OWN_INDEX, slot_names = {'cockpit3'}, adapters = {cockpit3 = adapter},
        clock = function() return r.now end, game = function() return r.now end,
        paused = function() return false end,
        record = function(event)
            r.events[event.kind] = (r.events[event.kind] or 0) + 1
            if spec.trace then spec.trace(r.now, event.kind, event) end
            return true
        end,
        fault = function(reason)
            r.faults = r.faults + 1
            if spec.trace then spec.trace(r.now, 'fault', reason) end
        end,
        wan = spec.wan,
    })
    return r
end

local function long_frames(spec, duration, seed)
    if spec.frames and spec.frames.long then return spec.frames.long end
    local draw, out = L.rng(seed + 7919), {}
    for k = 0, math.floor(duration / 10) do out[#out + 1] = {at = 10 * k + draw() * 10, length = .25} end
    return out
end

-- Plays one flight over one link into the real receiving cockpit and scores
-- what the cockpit shows against the true flight, frame by frame.
function L.run(spec)
    local flight = L.flight(spec.flight)
    local seed, version, mode = spec.seed or 1, spec.version or 4, spec.mode or 'continuous'
    local radar_m = spec.radar_m or 100000
    local profile = spec.link and 'custom' or assert(spec.profile, 'profile or link required')
    local arrival = spec.link
    if not arrival then
        local link = L.link(profile, seed)
        arrival = function(departure) return link:arrival(departure) end
    end
    local samples = L.sender(flight, {version = version})
    for _, e in ipairs(samples) do e.arrival = arrival(e.departure) end
    table.sort(samples, function(a, b)
        if a.arrival ~= b.arrival then return a.arrival < b.arrival end
        return a.sample.sequence < b.sample.sequence
    end)
    local long = long_frames(spec, flight.duration, seed)
    local rcv = receiver({mode = mode, wan = spec.wan, trace = spec.trace}, flight.at(0), radar_m)
    local ctl = rcv.controller

    local rest, rest_dir = nil, nil
    if flight.brake_end then
        rest = flight.at(flight.duration)
        local before = flight.at(flight.brake_end - 1)
        local n = math.sqrt(before.vx^2 + before.vy^2 + before.vz^2)
        rest_dir = {before.vx / n, before.vy / n, before.vz / n}
    end

    local errors, headings = {}, {}
    local freeze, longest, jumps, backwards, out_of_radar, overshoot = 0, 0, 0, 0, 0, 0
    local next_sample, next_long, started = 1, 1, false
    local prev_truth, prev_shown
    local t = 0
    ctl:view(OBSERVED, 1, 'cockpit3')
    while t <= flight.duration + 1e-9 do
        while samples[next_sample] and samples[next_sample].arrival <= t do
            local e = samples[next_sample]
            rcv.now = math.max(rcv.now, e.arrival)
            ctl:sample(OBSERVED, 1, e.sample, rcv.now, 'cockpit3')
            started = true
            next_sample = next_sample + 1
        end
        local dt = FRAME
        if long[next_long] and t >= long[next_long].at then
            dt = long[next_long].length
            next_long = next_long + 1
        end
        rcv.now = t
        if rcv.pending then rcv.actual, rcv.pending = rcv.pending, nil end
        local truth, shown = flight.at(t), pose_copy(rcv.actual)
        if distance(truth, L.OWN_POSE) > radar_m then out_of_radar = out_of_radar + 1 end
        if started then
            errors[#errors + 1] = distance(shown, truth)
            headings[#headings + 1] = heading_error(shown, truth)
            if prev_truth then
                local true_move, shown_move = distance(truth, prev_truth), distance(shown, prev_shown)
                local frame_dt = t - prev_truth.t
                if true_move > .5 and shown_move < .1 * true_move then
                    freeze = freeze + frame_dt
                    longest = math.max(longest, freeze)
                else
                    freeze = 0
                end
                if shown_move > math.max(100, 2 * truth.speed * frame_dt) then jumps = jumps + 1 end
                local dot = (shown.x - prev_shown.x) * truth.vx + (shown.y - prev_shown.y) * truth.vy
                    + (shown.z - prev_shown.z) * truth.vz
                if truth.speed > 1 and dot < 0 then backwards = backwards + 1 end
            end
            if rest and t >= flight.brake_end then
                local ahead = (shown.x - rest.x) * rest_dir[1] + (shown.y - rest.y) * rest_dir[2]
                    + (shown.z - rest.z) * rest_dir[3]
                overshoot = math.max(overshoot, ahead)
            end
            prev_truth, prev_shown = {x = truth.x, y = truth.y, z = truth.z, t = t}, shown
        end
        ctl:view(OBSERVED, 1, 'cockpit3')
        ctl:tick(t)
        t = t + dt
    end
    local stats = ctl:slot_stats('cockpit3') or {}
    ctl:close()
    table.sort(errors)
    table.sort(headings)
    return {
        key = string.format('%s/%s/%s/v%d/s%d', spec.flight, profile, mode, version, seed),
        position_error_m = {median = percentile(errors, .5), p95 = percentile(errors, .95), max = errors[#errors]},
        heading_error_deg = {median = percentile(headings, .5), p95 = percentile(headings, .95)},
        longest_freeze_s = longest, refusals = stats.refusals_over_50m or 0,
        stale_cutoffs = stats.stale_cutoffs or 0, faults = rcv.faults, jumps = jumps,
        backwards = backwards, out_of_radar = out_of_radar, overshoot_m = rest and overshoot or nil,
    }
end

-- Every flight on every profile in both playback modes, sorted by key.
function L.scorecard(opts)
    opts = opts or {}
    local out = {}
    for _, seed in ipairs(opts.seeds or {1}) do
        for _, flight in ipairs(L.FLIGHTS) do
            for _, profile in ipairs(L.PROFILE_NAMES) do
                for _, mode in ipairs(L.MODES) do
                    out[#out + 1] = L.run{flight = flight, profile = profile, mode = mode, seed = seed,
                                          version = opts.version or 4, wan = opts.wan}
                end
            end
        end
    end
    table.sort(out, function(a, b) return a.key < b.key end)
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
    if argv[1] == '--scorecard' then
        local opts, i = {}, 2
        local function list(text)
            local out = {}
            for item in text:gmatch('[^,]+') do out[#out + 1] = item end
            return out
        end
        while argv[i] do
            local flag, value = argv[i], argv[i + 1]
            if flag == '--seeds' and value then
                opts.seeds = {}
                for _, item in ipairs(list(value)) do opts.seeds[#opts.seeds + 1] = assert(tonumber(item), 'bad seed') end
            elseif flag == '--version' and (value == '3' or value == '4') then
                opts.version = tonumber(value)
            elseif flag == '--wan' and value then
                opts.wan = {}
                for _, name in ipairs(list(value)) do opts.wan[name] = true end
            else
                io.stderr:write('unknown or incomplete option: ' .. tostring(flag) .. '\n')
                return 2
            end
            i = i + 2
        end
        io.write(L.json(L.scorecard(opts)), '\n')
        return 0
    end
    io.stderr:write('usage: luajit tools/dev/authority_flight_lab.lua --arrivals PROFILE SEED N\n'
                    .. '       luajit tools/dev/authority_flight_lab.lua --scorecard [--seeds 1,2,3] [--version 3|4] [--wan a,b]\n')
    return 2
end

-- Run the command line only when this file is the main script, not when a
-- test loads it with dofile.
if arg and arg[0] and debug.getinfo(1, 'S').source == '@' .. arg[0] then
    os.exit(main(arg))
end

return L
