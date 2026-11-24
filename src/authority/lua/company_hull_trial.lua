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

X4LAN_COMPANY_HULL_TRIAL = M
return M
