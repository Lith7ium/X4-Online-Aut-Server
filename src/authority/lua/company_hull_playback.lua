-- Default-off, one verified cockpit target in the same sector. No native or wire APIs.
local M = {}
local MAX_STEP, PREDICTION_SECONDS, READBACK_TOLERANCE = 50, 5, 3
local VELOCITY_STEP_LIMIT, VELOCITY_INTERVAL_LIMIT = 250, .100
local INTERPOLATION_DELAY = .15
-- Largest distance between two valid sector positions, each with axes in ±10⁷ m.
local SECTOR_DIAGONAL = 2 * math.sqrt(3) * 1e7
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

local function decimal(value)
    return value and ('%.17g'):format(value) or 'unknown'
end

local function coordinates(value)
    return value and table.concat({decimal(value.x), decimal(value.y), decimal(value.z)}, ',') or 'unknown'
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
    local sample_version = cfg.sample_version or 3
    assert(sample_version == 3 or sample_version == 4)
    local follows_attitude = sample_version == 4
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
    local newest, write_input, write_input_age
    local velocity_steps = cfg.velocity_steps == true
    local continuous = cfg.continuous_playback == true
    local maximum_step = velocity_steps and VELOCITY_STEP_LIMIT or MAX_STEP
    if continuous then maximum_step = SECTOR_DIAGONAL end
    local measured_pose, last_display_time, last_display_pose, last_display_rate
    local velocity, sample_count, previous_update = nil, 0, nil
    local authorize_step
    local observation, prior_update, last_real_velocity
    local native_refusal, native_refusal_phase

    local function report_correction(phase, actual)
        if not observation or type(cfg.correction_refused) ~= 'function' then return end
        local target = observation.target
        local dx, dy, dz = target.x - actual.x, target.y - actual.y, target.z - actual.z
        local source = rendered_source
        pcall(cfg.correction_refused, {
            index = binding.index, phase = phase, render_phase = observation.phase,
            sequence = source and ('%.0f'):format(source.sequence) or 'unknown',
            source_time_s = decimal(source and source.source_time),
            capture_time_s = decimal(source and source.capture_time),
            capture_dt_s = decimal(source and source.capture_interval),
            update_dt_s = decimal(prior_update and observation.local_time - prior_update.time),
            last_real_velocity_mps = coordinates(last_real_velocity),
            previous_update_velocity_mps = coordinates(prior_update and prior_update.velocity),
            actual_xyz = coordinates(actual), requested_xyz = coordinates(target),
            distance_m = decimal(math.sqrt(dx * dx + dy * dy + dz * dz)),
        })
    end

    local function step_guard(actual, target, now, phase)
        local previous = previous_update
        previous_update = {time = now, velocity = velocity, samples = sample_count}
        local elapsed = previous and now - previous.time
        authorize_step = function(before)
            local dx, dy, dz = target.x - before.x, target.y - before.y, target.z - before.z
            local squared = dx * dx + dy * dy + dz * dz
            if squared <= MAX_STEP * MAX_STEP then return true end
            if squared > VELOCITY_STEP_LIMIT * VELOCITY_STEP_LIMIT then return false end
            if not previous or previous.samples < 2 or elapsed <= 0 or elapsed > VELOCITY_INTERVAL_LIMIT
                    or (phase ~= 'interpolated' and phase ~= 'extrapolated') then
                return false
            end
            local rate = previous.velocity
            dx, dy, dz = dx - rate.x * elapsed, dy - rate.y * elapsed, dz - rate.z * elapsed
            return dx * dx + dy * dy + dz * dz <= MAX_STEP * MAX_STEP
        end
        return authorize_step(actual)
    end

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
        return {
            push = function(_, sample, arrival)
                local ok, reason = inner:push(sample, arrival)
                if not ok or reason ~= 'append' then
                    refusal = reason == 'gap' and 'capture_gap'
                        or reason == 'teleport' and 'source_discontinuity' or 'invalid_source_progress'
                    return false
                end
                local previous = lineage[#lineage]
                lineage[#lineage + 1] = {sequence = sample.sequence, source_time = sample.source_time,
                    capture_time = sample.capture_time, arrival = arrival,
                    capture_interval = previous and sample.capture_time - previous.capture_time}
                if #lineage > 16 then table.remove(lineage, 1) end
                newest = {sequence = sample.sequence, pose = sample.pose, rate = sample.real_rate, arrival = arrival}
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
                if continuous then
                    -- Integrate the previous flight velocity across the actual render
                    -- interval. Blend the residual toward the latest reconstructed
                    -- position. The blend uses seconds, including after a long frame.
                    local dt = last_display_time and now - last_display_time or 0
                    local weight = 1 - math.exp(-dt / INTERPOLATION_DELAY)
                    local output = {}
                    for i, axis in ipairs({'x', 'y', 'z'}) do
                        local predicted = measured_pose[axis]
                        if last_display_pose then
                            predicted = last_display_pose[i] + last_display_rate[i] * dt
                        end
                        output[i] = predicted + weight * (rendered.pose[i] - predicted)
                    end
                    last_display_time, last_display_pose, last_display_rate = now, output, newest.rate
                    rendered.pose = output
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
        enabled = true, buffer = checked_buffer, sample_version = sample_version, frame = binding.sector_macro,
        attitude_changed = follows_attitude and buffer.attitude_changed or nil,
        max_age = PREDICTION_SECONDS, max_step = maximum_step,
        step_guard = not continuous and velocity_steps and step_guard or nil,
        buffer_options = {delay = INTERPOLATION_DELAY, horizon = PREDICTION_SECONDS, start_at_first_pair = cfg.start_at_first_pair,
                          stale = CAPTURE_CONTINUITY_LIMIT, teleport_distance = continuous and SECTOR_DIAGONAL or MAX_STEP},
        clock = cfg.clock, game = cfg.game, paused = cfg.paused, guard = guard,
        read = function(timed)
            local observed, read_start, read_end = cfg.read(copy(binding), timed)
            local actual = assert(xyz(observed, 1e7), 'invalid native company hull read')
            measured_pose = actual
            local attitude = follows_attitude and assert(buffer.copy_attitude(observed)) or nil
            -- Legacy XYZ playback leaves native orientation to the adapter.
            actual.yaw, actual.pitch, actual.roll = attitude and attitude.yaw or 0,
                attitude and attitude.pitch or 0, attitude and attitude.roll or 0
            return actual, read_start, read_end
        end,
        write = function(target, before)
            local expired = expiry()
            if expired ~= false then
                refusal = expired and 'stale' or 'invalid_local_clock'
                return false, 'not_dispatched'
            end
            local position = assert(xyz(target, 1e7))
            write_source = rendered_source
            if continuous then
                -- A newer input can arrive before X4 reads this write back.
                write_input = newest
                write_input_age = observation.local_time - newest.arrival
            end
            local authorize = authorize_step
            authorize_step = nil
            local function recheck(actual)
                if expiry() ~= false then return false end
                local permitted = authorize(actual)
                if not permitted then
                    native_refusal, native_refusal_phase = actual, 'native_recheck'
                end
                return permitted
            end
            local attitude = follows_attitude and assert(buffer.copy_attitude(target)) or nil
            local before_dispatch
            if continuous then
                before_dispatch = function() return expiry() == false end
            elseif authorize then
                before_dispatch = recheck
            end
            local written, outside_native_limit, dispatch = cfg.write(
                copy(binding), position, xyz(before, 1e7), before_dispatch, attitude)
            if written == false and outside_native_limit then
                native_refusal, native_refusal_phase = xyz(outside_native_limit, 1e7), 'native_step_limit'
            end
            return written, dispatch
        end,
        record = function(event)
            local readback = event.kind == 'readback'
            local kind = event.kind
            if readback then
                if event.elapsed <= 0 then
                    refusal = 'readback_not_later'
                    return false
                end
                if event.error > READBACK_TOLERANCE then
                    if not continuous then
                        refusal = 'readback_out_of_bounds'
                        return false
                    end
                    -- The same bound actor is at a different measured pose. Restart
                    -- the blend there and do not certify the previous write.
                    kind, last_display_pose = 'position_deviation', nil
                end
            else
                observation, prior_update = event, previous_update
            end
            local source = readback and write_source or rendered_source
            assert(source and source.sequence == event.source_sequence)
            return cfg.record({
                kind = kind, slot = binding.slot, serial = binding.serial, epoch = binding.epoch,
                continuous_playback = continuous,
                input_sequence = readback and write_input and write_input.sequence or nil,
                input_velocity = readback and write_input and {
                    x = write_input.rate[1], y = write_input.rate[2], z = write_input.rate[3],
                } or nil,
                input_age = readback and write_input_age or nil,
                index = binding.index, generation = binding.generation, sector_macro = binding.sector_macro,
                code = binding.code, source_sequence = source.sequence, source_time = source.source_time,
                capture_time = source.capture_time, local_time = event.local_time, game_time = event.game_time,
                native_read_start = event.native_read_start, native_read_end = event.native_read_end,
                source_arrival = readback and source.arrival or nil,
                actual = xyz(event.actual, 1e7), target = xyz(event.target, 1e7), phase = event.phase,
                error = event.error, elapsed = event.elapsed,
                actual_attitude = follows_attitude and buffer.copy_attitude(event.actual) or nil,
                target_attitude = follows_attitude and buffer.copy_attitude(event.target) or nil,
            })
        end,
        fault = function(_, _, stage)
            local reason = refusal or (stage == 'distance' and 'correction_out_of_bounds') or 'playback_failed'
            stop(reason, stage)
            if reason == 'correction_out_of_bounds' and observation then
                report_correction('playback_distance', observation.actual)
            elseif native_refusal then
                report_correction(native_refusal_phase, native_refusal)
            end
        end,
    }))

    local api = {}
    function api:push(view, sample, arrival)
        if terminal then return false, terminal end
        if not same_binding(view, binding) then return stop('binding_changed', 'binding') end
        if type(sample) ~= 'table' or getmetatable(sample) ~= nil or sample.version ~= sample_version
                or sample.epoch ~= binding.epoch or sample.index ~= binding.index
                or sample.generation ~= binding.generation or sample.sector_macro ~= binding.sector_macro then
            return stop('sample_identity_changed', 'sample')
        end
        local position = xyz(sample, 1e7)
        local attitude = follows_attitude and buffer.copy_attitude(sample.attitude) or nil
        if (follows_attitude and not attitude) or (not follows_attitude and sample.attitude ~= nil) then
            return stop('invalid_attitude', 'sample')
        end
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
        local incoming_velocity = xyz(sample.real_velocity, 5000)
        if not incoming_velocity then
            -- Incomplete data cannot start a track, replace a known rate, or
            -- reset the deadline for predictions from the last complete sample.
            if sample.real_velocity == nil then
                velocity, sample_count, previous_update = nil, 0, nil
                return last_arrival ~= nil, 'waiting_for_velocity'
            end
            return stop('invalid_velocity', 'sample')
        end
        local ok, reason = player:push({
            version = sample_version, attitude = attitude, frame = binding.sector_macro, sequence = sample.sequence, source_time = sample.source_time,
            capture_time = sample.capture_time, pose = {position.x, position.y, position.z},
            real_rate = {incoming_velocity.x, incoming_velocity.y, incoming_velocity.z},
        }, arrival)
        if not ok then
            if terminal then return false, terminal end
            return stop(reason == 'paused' and 'paused' or 'stale', 'arrival')
        end
        last_arrival = arrival
        last_real_velocity = incoming_velocity
        if velocity_steps then
            velocity, sample_count = incoming_velocity, math.min(sample_count + 1, 2)
        end
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
