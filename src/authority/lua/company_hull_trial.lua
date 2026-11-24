-- One opt-in cockpit playback attempt on an operator-selected company ship.
local M = {}

function M.new(options)
    assert(type(options) == 'table' and options.enabled == true, 'explicit ship playback trial required')
    local target_index = options.target_index
    assert(type(target_index) == 'number' and target_index % 1 == 0 and target_index >= 1
        and target_index <= 200000 and target_index ~= options.own_index, 'invalid trial target')
    local factory = options.playback or X4LAN_COMPANY_HULL_PLAYBACK
    local binding, session, controller
    local finished = false
    local trial = {}

    function trial:close()
        finished = true
        if controller then controller:stop(); controller = nil end
        if session then session:close(); session = nil end
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

    function trial:sample(observed, serial, sample, arrival)
        if finished or observed.index ~= target_index then return end
        if binding and (not same_view(observed, serial) or binding.generation ~= sample.generation) then
            self:close()
            return
        end
        if not binding then
            if not sample.real_velocity then return end
            binding = {
                slot = 'cockpit', serial = serial, epoch = observed.epoch, index = observed.index,
                actor = observed.actor, code = observed.code, sector_macro = observed.sector_macro,
                generation = sample.generation,
            }
            session = options.adapter:bind(binding)
            if not session then
                self:close()
                options.fault('native_binding')
                return
            end
            controller = assert(factory.new({
                enabled = true, own_index = options.own_index, binding = binding,
                clock = options.clock, game = options.game, paused = options.paused,
                guard = function() return session:guard() end,
                read = function() return session:read() end,
                write = function(_, target, before) return session:write(target, before) end,
                record = options.record, fault = options.fault,
            }))
        end
        if not controller:push(binding, sample, arrival) then self:close() end
    end

    function trial:tick()
        if controller and not controller:tick() then self:close() end
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
