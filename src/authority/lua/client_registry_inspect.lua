-- Optional read-only full registry inspection during a paused client load.
-- The caller owns event delivery and the later legacy header/admission checks.
local M = {}

function M.new(options)
    assert(type(options) == 'table' and options.enabled == true,
        'client registry inspection is default-off')
    local timeout = options.timeout
    if timeout == nil then timeout = 110 end
    assert(timeout == 110 or timeout == 300, 'unsupported client registry inspection timeout')
    local pinned, engine = options.pinned, options.engine
    assert(type(pinned) == 'table' and type(engine) == 'table')
    for _, name in ipairs({'is_paused', 'game_time'}) do
        assert(type(engine[name]) == 'function', 'missing client engine method ' .. name)
    end
    for _, name in ipairs({'clock', 'issue', 'register', 'log', 'fault'}) do
        assert(type(options[name]) == 'function', 'missing inspection callback ' .. name)
    end

    local gate, paused_time, failed = nil, nil, false
    local api = {}
    local function fail(reason)
        if not failed then
            failed = true
            pcall(options.fault, tostring(reason))
        end
        return false
    end
    local function live_guard()
        if failed or paused_time == nil then return false end
        local ok, paused, time = pcall(function()
            return engine.is_paused(), engine.game_time()
        end)
        return ok and paused == true and time == paused_time
    end

    function api:guard()
        return live_guard()
    end

    function api:event(response)
        if not gate then return fail('unsolicited full registry response') end
        local ok, accepted = pcall(gate.event, gate, response)
        if not ok or accepted ~= true then
            return fail('full registry response refused')
        end
        return true
    end

    function api:tick()
        if failed then return false end
        if not gate then
            local ok, time = pcall(engine.game_time)
            if not ok or type(time) ~= 'number' or time ~= time
                or time < 0 or time == math.huge or not engine.is_paused() then
                return fail('invalid paused game time')
            end
            paused_time = time
            local created, result = pcall(X4LAN_AUTHORITY_REGISTRY_CHECKPOINT_GATE.new, {
                enabled = true, epoch = pinned.epoch, nonce = pinned.nonce,
                initial_run_id = pinned.run_id, issued_count = pinned.issued_count,
                expected_binding_bits = pinned.binding_bits,
                expected_operational_bits = pinned.operational_bits,
                clock = options.clock, timeout = timeout, guard = live_guard,
                issue = function(action, payload) return options.issue(action, payload) end,
                store_marker = function()
                    error('client registry inspection cannot store a marker')
                end,
                report = function(evidence)
                    assert(evidence.mode == 'inspect' and evidence.run_id == pinned.run_id)
                    options.log(evidence)
                end,
                fault = fail,
            })
            if not created then return fail(result) end
            gate = result
            if not gate:start() then return fail('full registry inspect could not start') end
        end
        if not live_guard() then return fail('client registry pause or game time changed') end
        local ok, accepted = pcall(gate.tick, gate)
        if not ok or accepted ~= true then return fail('full registry inspect failed') end
        if not live_guard() then return fail('client registry pause or game time changed') end
        return true
    end

    function api:ready()
        return not failed and gate ~= nil and gate:ready() and live_guard()
    end

    local ok, registered = pcall(options.register, function(_, response)
        api:event(response)
    end)
    if not ok or registered ~= true then
        fail('full registry event registration refused')
        return nil, 'registration'
    end
    return api
end

function M.new_x4(pinned, engine, on_fault, timeout)
    assert(type(on_fault) == 'function', 'load fault callback required')
    return M.new({
        enabled = true, pinned = pinned, engine = engine,
        clock = GetCurRealTime, timeout = timeout,
        register = function(callback)
            return RegisterEvent('X4AuthorityRegistryAvailabilitySnapshot.Result', callback) ~= false
        end,
        issue = function(action, payload)
            return AddUITriggeredEvent('X4AuthorityRegistryAvailabilitySnapshot', action, payload) ~= false
        end,
        log = function(evidence)
            DebugError('[X4LAN] AUTH_REGISTRY_FULL_INSPECT '
                .. evidence.run_id .. ' ' .. evidence.issued_count)
        end,
        fault = function(reason)
            DebugError('[X4LAN] AUTH_REGISTRY_FULL_INSPECT_ERROR ' .. reason)
            on_fault(reason)
        end,
    })
end

X4LAN_AUTHORITY_CLIENT_REGISTRY_INSPECT = M
return M
