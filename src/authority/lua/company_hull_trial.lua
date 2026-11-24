-- One opt-in cockpit playback trial bound to an operator-selected company ship and pilot.
local M = {}

function M.new(options)
    assert(type(options) == 'table' and options.enabled == true, 'explicit ship playback trial required')
    local target_index = options.target_index
    assert(type(target_index) == 'number' and target_index % 1 == 0 and target_index >= 1
        and target_index <= 200000 and target_index ~= options.own_index, 'invalid trial target')
    local factory = options.playback or X4LAN_COMPANY_HULL_PLAYBACK
    local prediction_seconds = assert(factory.MAX_PREDICTION_SECONDS)
    local binding, session, controller, last
    local finished = false
    local trial = {}

    local function paused()
        return options.paused and options.paused() ~= false
    end

    local function stop_track()
        if controller then controller:stop(); controller = nil end
        if session then session:close(); session = nil end
    end

    function trial:close()
        finished = true
        stop_track()
    end

    local function retire(reason, now)
        local timed_out = (reason == 'stale' or reason == 'arrival_gap')
            and last and now >= last.arrival + prediction_seconds
        if timed_out and controller:outcome_uncertain() == false then
            stop_track()
        else
            trial:close()
        end
    end

    local function same_view(observed, serial)
        return observed and serial == binding.serial and observed.epoch == binding.epoch
            and observed.index == binding.index and observed.actor == binding.actor
            and observed.code == binding.code and observed.sector_macro == binding.sector_macro
    end

    function trial:view(observed, serial)
        if binding and not same_view(observed, serial) then self:close() end
    end

    function trial:end_track(observed, serial, generation)
        if observed.index == target_index and
                (not binding or (same_view(observed, serial) and binding.generation == generation)) then
            self:close()
        end
    end

    local function bind(observed, serial, generation)
        binding = binding or {
            slot = 'cockpit', serial = serial, epoch = observed.epoch, index = observed.index,
            actor = observed.actor, code = observed.code, sector_macro = observed.sector_macro,
            generation = generation,
        }
        session = options.adapter:bind(binding)
        if not session then
            trial:close()
            options.fault('native_binding')
            return false
        end
        local active_session = session
        controller = assert(factory.new({
            enabled = true, own_index = options.own_index, binding = binding,
            clock = options.clock, game = options.game, paused = options.paused,
            guard = function() return active_session:guard() end,
            read = function() return active_session:read() end,
            write = function(_, target, before) return active_session:write(target, before) end,
            record = options.record, fault = options.fault,
        }))
        return true
    end

    function trial:tick()
        if finished then return end
        if paused() then self:close(); return end
        if controller then
            local ok, reason = controller:tick()
            if not ok then retire(reason or controller:state(), options.clock()) end
        end
    end

    function trial:sample(observed, serial, sample, arrival)
        if finished or observed.index ~= target_index then return end
        if paused() then self:close(); return end
        local now = options.clock()
        if type(arrival) ~= 'number' or not (arrival >= 0 and arrival <= now)
                or now - arrival >= prediction_seconds then return end
        if binding and (not same_view(observed, serial) or binding.generation ~= sample.generation) then
            self:close()
            return
        end
        if not sample.real_velocity then return end

        if last then
            -- Compare source clocks only with the preceding source sample. Compare
            -- ingress with the local deadline, even if Lua only drained it later.
            if not (sample.sequence > last.sequence and sample.capture_time > last.capture_time
                    and sample.source_time >= last.source_time and arrival >= last.arrival) then return end
            local deadline = last.arrival + prediction_seconds
            if now >= deadline then
                if arrival < deadline then return end
                -- Settle the last native write on its readback tick before closing.
                if controller then self:tick() end
                if finished or controller then return end
            end
        end

        if not controller and not bind(observed, serial, sample.generation) then return end
        local ok, reason = controller:push(binding, sample, arrival)
        if not ok then
            retire(reason or controller:state(), now)
            return
        end
        last = {sequence=sample.sequence, capture_time=sample.capture_time,
                source_time=sample.source_time, arrival=arrival}
    end

    return trial
end

-- Keep the first readback and the first metre of measured movement in the
-- engine log. A stationary period cannot overwrite either diagnostic.
function M.new_x4(config, pinned, cockpit_reader, guard, clock)
    local ffi = require('ffi')
    ffi.cdef[[double GetCurrentGameTime(void); bool IsGamePaused(void);]]
    local adapter = X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4({
        enabled = true, pinned = pinned, own_index = config.index, observer = cockpit_reader, guard = guard,
    })
    local origin, recorded_readback, recorded_movement
    local function decimal(value) return ('%.17g'):format(value) end
    local function coordinates(pose)
        return table.concat({decimal(pose.x), decimal(pose.y), decimal(pose.z)}, ',')
    end
    local function record(event)
        if not origin then origin = event.actual end
        if event.kind == 'readback' then
            local actual, target = event.actual, event.target
            local dx, dy, dz = actual.x - origin.x, actual.y - origin.y, actual.z - origin.z
            local displacement = math.sqrt(dx * dx + dy * dy + dz * dz)
            if not recorded_readback or (not recorded_movement and displacement >= 1) then
                DebugError(table.concat({
                    '[X4LAN] AUTH_COMPANY_SHIP_PLAYBACK_READBACK', 'ship=' .. event.code,
                    'epoch=' .. event.epoch, 'index=' .. event.index, 'sector=' .. event.sector_macro,
                    'generation=' .. event.generation, 'sequence=' .. event.source_sequence,
                    'source_time=' .. decimal(event.source_time), 'capture_time=' .. decimal(event.capture_time),
                    'elapsed=' .. decimal(event.elapsed), 'target=' .. coordinates(target),
                    'actual=' .. coordinates(actual), 'error=' .. decimal(event.error),
                    'displacement=' .. decimal(displacement),
                }, ' '))
                recorded_readback = true
                recorded_movement = recorded_movement or displacement >= 1
            end
        end
        return true
    end
    return M.new({
        enabled = true, own_index = config.index, target_index = config.playback_index,
        adapter = adapter, clock = clock, game = function() return ffi.C.GetCurrentGameTime() end,
        paused = function() return ffi.C.IsGamePaused() end, record = record,
        fault = function(reason) DebugError('[X4LAN] AUTH_COMPANY_SHIP_PLAYBACK_STOP ' .. tostring(reason)) end,
    })
end

X4LAN_COMPANY_HULL_TRIAL = M
return M
