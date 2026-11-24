-- Default-off, one verified cockpit target in the same sector. No native or wire APIs.
local M = {}
local MAX_STEP, PREDICTION_SECONDS, READBACK_TOLERANCE = 50, 5, 3
M.MAX_PREDICTION_SECONDS = PREDICTION_SECONDS
-- The shared buffer's remote-capture continuity bound is independent of
-- the local prediction deadline and tolerates different transit delays.
local CAPTURE_CONTINUITY_LIMIT = 60
local BINDING_FIELDS = {'slot', 'serial', 'epoch', 'sector_macro', 'index', 'actor', 'code', 'generation'}
local COCKPIT_SLOTS = {cockpit = true, cockpit2 = true, cockpit3 = true}

local function number(value, minimum, maximum)
    return type(value) == 'number' and value >= minimum and value <= maximum
end

local function integer(value, maximum)
    return number(value, 1, maximum) and value % 1 == 0
end

local function token(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function pattern(value, limit, expression)
    return type(value) == 'string' and #value <= limit and value:match(expression) ~= nil
end

local function xyz(value, limit)
    if type(value) ~= 'table' or getmetatable(value) ~= nil then return nil end
    local x, y, z = value.x, value.y, value.z
    if number(x, -limit, limit) and number(y, -limit, limit) and number(z, -limit, limit) then
        return {x = x, y = y, z = z}
    end
end

local function copy(value)
    local detached = {}
    for key, item in pairs(value) do detached[key] = item end
    return detached
end

local function same_binding(value, binding)
    if type(value) ~= 'table' or getmetatable(value) ~= nil then return false end
    for _, key in ipairs(BINDING_FIELDS) do
        if value[key] ~= binding[key] then return false end
    end
    return true
end

function M.new(options)
    if type(options) ~= 'table' or options.enabled ~= true then return nil, 'disabled' end
    local cfg = copy(options)
    for _, key in ipairs({'clock', 'game', 'paused', 'guard', 'read', 'write', 'record', 'fault'}) do
        assert(type(cfg[key]) == 'function', key)
    end
    local buffer = cfg.buffer or X4LAN_MOTION_BUFFER
    local playback = cfg.playback or X4LAN_MOTION_PLAYBACK
    assert(type(buffer) == 'table' and type(buffer.new) == 'function')
    assert(type(playback) == 'table' and type(playback.new) == 'function')

    local claimed = cfg.binding
    if not (integer(cfg.own_index, 200000) and type(claimed) == 'table' and getmetatable(claimed) == nil
            and COCKPIT_SLOTS[claimed.slot] and integer(claimed.serial, 9007199254740991)
            and integer(claimed.index, 200000) and claimed.index ~= cfg.own_index
            and token(claimed.epoch) and token(claimed.generation)
            and pattern(claimed.sector_macro, 128, '^[A-Za-z0-9_]+$')
            and pattern(claimed.actor, 18, '^0[xX][0-9A-Fa-f]+$')
            and claimed.actor:sub(3):find('[1-9A-Fa-f]')
            and pattern(claimed.code, 7, '^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$')) then
        return nil, 'invalid_cockpit_binding'
    end
    local binding = {}
    for _, key in ipairs(BINDING_FIELDS) do binding[key] = claimed[key] end
    local player, terminal, refusal, last_arrival
    local rendered_source, write_source

    local function stop(reason, stage)
        if terminal then return false, terminal end
        terminal = reason
        player:stop()
        if stage then pcall(cfg.fault, reason, player:outcome_uncertain(), stage) end
        return false, reason
    end

    local function expiry()
        local ok, now = pcall(cfg.clock)
        if not ok or not number(now, 0, 1e12) then return nil end
        return last_arrival ~= nil and now >= last_arrival + PREDICTION_SECONDS
    end

    local function guard()
        if terminal then return false end
        if cfg.paused() ~= false then
            refusal = 'paused'
        elseif cfg.guard(copy(binding)) ~= true then
            refusal = 'binding_lost'
        else
            return true
        end
        return false
    end

    -- Buffer owns reconstruction. Keep only the original clocks for its at most
    -- 16 source sequences so a delayed readback can identify the exact write.
    local checked_buffer = {}
    function checked_buffer.new(settings)
        local inner, lineage = buffer.new(settings), {}
        local newest
        return {
            push = function(_, sample, arrival)
                local ok, reason = inner:push(sample, arrival)
                if not ok or reason ~= 'append' then
                    refusal = reason == 'gap' and 'capture_gap'
                        or reason == 'teleport' and 'source_discontinuity' or 'invalid_source_progress'
                    return false
                end
                lineage[#lineage + 1] = {sequence = sample.sequence, source_time = sample.source_time,
                                         capture_time = sample.capture_time}
                if #lineage > 16 then table.remove(lineage, 1) end
                newest = {pose = sample.pose, rate = sample.real_rate, arrival = arrival}
                return true
            end,
            render = function(_, now)
                local rendered = inner:render(now)
                if not rendered then return nil end
                if rendered.phase == 'extrapolated' or rendered.phase == 'frozen' then
                    -- Capture time supports interpolation, but transit jitter
                    -- must never predict farther than actual local time elapsed.
                    local elapsed = math.min(PREDICTION_SECONDS, now - newest.arrival)
                    for axis = 1, 3 do
                        local start = newest.pose[axis]
                        local limit = start + newest.rate[axis] * elapsed
                        rendered.pose[axis] = math.min(math.max(start, limit),
                            math.max(math.min(start, limit), rendered.pose[axis]))
                    end
                end
                for _, source in ipairs(lineage) do
                    if source.sequence == rendered.source_sequence then
                        rendered_source = source
                        return rendered
                    end
                end
            end,
        }
    end

    player = assert(playback.new({
        enabled = true, buffer = checked_buffer, sample_version = 3, frame = binding.sector_macro,
        max_age = PREDICTION_SECONDS, max_step = MAX_STEP,
        buffer_options = {delay = .15, horizon = PREDICTION_SECONDS,
                          stale = CAPTURE_CONTINUITY_LIMIT, teleport_distance = MAX_STEP},
        clock = cfg.clock, game = cfg.game, paused = cfg.paused, guard = guard,
        read = function()
            local actual = assert(xyz(cfg.read(copy(binding)), 1e7), 'invalid native company hull read')
            -- The shared engine accepts six axes. The company adapter only reads
            -- and writes XYZ and is responsible for preserving native orientation.
            actual.yaw, actual.pitch, actual.roll = 0, 0, 0
            return actual
        end,
        write = function(target, before)
            local expired = expiry()
            if expired ~= false then
                refusal = expired and 'stale' or 'invalid_local_clock'
                return false
            end
            local position = assert(xyz(target, 1e7))
            write_source = rendered_source
            return cfg.write(copy(binding), position, xyz(before, 1e7))
        end,
        record = function(event)
            local readback = event.kind == 'readback'
            if readback then
                if event.elapsed <= 0 then
                    refusal = 'readback_not_later'
                    return false
                end
                if event.error > READBACK_TOLERANCE then
                    refusal = 'readback_out_of_bounds'
                    return false
                end
            end
            local source = readback and write_source or rendered_source
            assert(source and source.sequence == event.source_sequence)
            return cfg.record({
                kind = event.kind, slot = binding.slot, serial = binding.serial, epoch = binding.epoch,
                index = binding.index, generation = binding.generation, sector_macro = binding.sector_macro,
                code = binding.code, source_sequence = source.sequence, source_time = source.source_time,
                capture_time = source.capture_time, local_time = event.local_time, game_time = event.game_time,
                actual = xyz(event.actual, 1e7), target = xyz(event.target, 1e7), phase = event.phase,
                error = event.error, elapsed = event.elapsed,
            })
        end,
        fault = function(_, _, stage)
            stop(refusal or (stage == 'distance' and 'correction_out_of_bounds') or 'playback_failed', stage)
        end,
    }))

    local api = {}
    function api:push(view, sample, arrival)
        if terminal then return false, terminal end
        if not same_binding(view, binding) then return stop('binding_changed', 'binding') end
        if type(sample) ~= 'table' or getmetatable(sample) ~= nil or sample.version ~= 3
                or sample.epoch ~= binding.epoch or sample.index ~= binding.index
                or sample.generation ~= binding.generation or sample.sector_macro ~= binding.sector_macro then
            return stop('sample_identity_changed', 'sample')
        end
        local position = xyz(sample, 1e7)
        if not position or not integer(sample.sequence, 9007199254740991)
                or not number(sample.source_time, 0, 1e12) or not number(sample.capture_time, 0, 1e12)
                or not number(arrival, 0, 1e12) then
            return stop('invalid_sample', 'sample')
        end
        local expired = expiry()
        if expired == nil then return stop('invalid_local_clock', 'arrival') end
        if expired or (last_arrival and arrival >= last_arrival + PREDICTION_SECONDS) then
            return stop('arrival_gap', 'arrival')
        end
        local velocity = xyz(sample.real_velocity, 5000)
        if not velocity then
            -- Incomplete data cannot start a track, replace a known rate, or
            -- reset the deadline for predictions from the last complete sample.
            if sample.real_velocity == nil then return last_arrival ~= nil, 'waiting_for_velocity' end
            return stop('invalid_velocity', 'sample')
        end
        local ok, reason = player:push({
            version = 3, frame = binding.sector_macro, sequence = sample.sequence, source_time = sample.source_time,
            capture_time = sample.capture_time, pose = {position.x, position.y, position.z},
            real_rate = {velocity.x, velocity.y, velocity.z},
        }, arrival)
        if not ok then
            if terminal then return false, terminal end
            return stop(reason == 'paused' and 'paused' or 'stale', 'arrival')
        end
        last_arrival = arrival
        return true
    end

    function api:tick()
        if terminal then return false, terminal end
        if not player:tick() then return false, terminal end
        local state = player:state()
        if state == 'paused' or state == 'stale' then return stop(state, 'lifetime') end
        local expired = expiry()
        if expired == nil then return stop('invalid_local_clock', 'lifetime') end
        if expired then return stop('stale', 'lifetime') end
        return true
    end

    function api:stop()
        if not terminal then stop('stopped') end
        return true
    end

    function api:state() return terminal or player:state() end
    function api:outcome_uncertain() return player:outcome_uncertain() end
    return api
end

X4LAN_COMPANY_HULL_PLAYBACK = M
return M
