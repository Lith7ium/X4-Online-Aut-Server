-- Run once from the paused load owner after it has inspected the loaded register.
local M = {}
local SCREEN = 'X4AuthorityClientSeat'
local REFUSALS = {
    REGISTRY_INVALID = true, REGISTRY_CHANGED = true, INDEX_INVALID = true,
    BINDING_INVALID = true, OWNER_INVALID = true, SHIP_INOPERABLE = true,
    TARGET_CHANGED = true, CONTROL_MISSING = true, PLAYER_CONTEXT = true,
    SHIP_DOCKED_OR_STORED = true, SHIP_SECTOR_INVALID = true, PILOT_PRESENT = true,
    ACTION_INVALID = true,
}

function M.new(options)
    local pinned, index = options.pinned, options.index
    local prefix = pinned.nonce .. '|' .. pinned.epoch .. '|' .. index .. '|'
    local state, pending, target = 'inspect', nil, nil
    local deadline, last_time, target_owner
    local api = {}

    local function fail(reason)
        if state ~= 'failed' then
            state = 'failed'
            options.fault(prefix .. reason)
        end
        return false
    end

    local function guard()
        local now = options.clock()
        if type(now) ~= 'number' or not (now >= 0 and now < math.huge)
                or (last_time and now < last_time) then
            return fail('CLOCK_INVALID')
        end
        last_time = now
        if deadline and now >= deadline then return fail('TIMEOUT') end
        if options.guard() ~= true then return fail('PAUSE_OR_REGISTRY_LOST') end
        return true
    end

    local function read_target(controlled)
        local ok, refusal = pcall(options.inspect, target, target_owner, controlled)
        if not ok then return fail('SHIP_READ_FAILED') end
        if refusal then return fail(refusal) end
        return true
    end

    function api:event(row)
        if not pending or state ~= 'waiting' then return fail('UNEXPECTED_RESULT') end
        if not guard() then return false end
        if type(row) ~= 'string' or #row > 200 or row:sub(1, #prefix) ~= prefix then
            return fail('RESULT_MISMATCH')
        end
        local action, result, actor, owner =
            row:sub(#prefix + 1):match('^([a-z]+)|([A-Z_]+)|([^|]+)|([^|]+)$')
        if action ~= pending then return fail('RESULT_MISMATCH') end
        if REFUSALS[result] then return fail(result) end
        if target and actor ~= target then return fail('TARGET_CHANGED') end
        local ready = action == 'inspect' and result == 'READY'
        local dispatched = action == 'take' and result == 'DISPATCHED'
        local controlled = result == 'CONTROLLED'
        if not (ready or dispatched or controlled) then return fail('RESULT_INVALID') end
        pending = nil
        target, target_owner = actor, owner
        if dispatched then
            state = 'verify'
            return true
        end
        if not read_target(controlled) then return false end
        if controlled then state = 'confirm' else state = 'take' end
        return true
    end

    function api:tick()
        if state == 'failed' or not guard() then return false end
        if state == 'confirm' then
            if not read_target(true) then return false end
            options.log(prefix .. 'controlled')
            state = 'done'
            return true
        end
        if state == 'waiting' or state == 'done' then return true end
        if state == 'take' and not read_target(false) then return false end
        deadline = deadline or (last_time + 10)
        pending, state = state, 'waiting'
        local ok, issued = pcall(options.issue, {
            nonce = pinned.nonce, epoch = pinned.epoch, count = pinned.issued_count,
            index = index, action = pending, actor = target or '0',
        })
        if not ok or issued ~= true then return fail('DISPATCH_FAILED') end
        return state ~= 'failed'
    end

    function api:ready()
        return state == 'done'
    end

    function api:controlled_actor()
        if state ~= 'done' then return nil end
        local ok, refusal = pcall(options.inspect, target, target_owner, true)
        if ok and refusal == nil then return target end
    end

    local ok, registered = pcall(options.register, function(_, row) api:event(row) end)
    if not ok or registered ~= true then
        fail('REGISTRATION_FAILED')
        return nil
    end
    return api
end

function M.new_x4(pinned, index, registry, on_fault)
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[
        bool IsValidComponent(UniverseID);
        bool IsComponentOperational(UniverseID);
        bool IsComponentClass(UniverseID,const char*);
        UniverseID GetPlayerContainerID(void);
        UniverseID GetPlayerOccupiedShipID(void);
        UniverseID GetPlayerControlledShipID(void);
    ]]
    local C = ffi.C
    return M.new({
        pinned = pinned, index = index, clock = GetCurRealTime,
        guard = function() return registry:ready() end,
        register = function(callback)
            return RegisterEvent(SCREEN .. '.Result', callback) ~= false
        end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Seat', args) ~= false end,
        inspect = function(actor, owner, controlled)
            local id = X4LAN_RESOLVE_STRING.parse_hex(actor)
            if not id or not C.IsValidComponent(id) or not C.IsComponentClass(id, 'ship')
                    or not C.IsComponentOperational(id) then return 'SHIP_ID_INVALID' end
            local key = tostring(id):gsub('ULL$', '')
            local lua_id = ConvertStringToLuaID(key)
            if not lua_id or tostring(ConvertIDTo64Bit(lua_id)):gsub('ULL$', '') ~= key then
                return 'SHIP_ID_INVALID'
            end
            if GetComponentData(lua_id, 'owner') ~= owner then return 'OWNER_INVALID' end
            local occupied, active, container = C.GetPlayerOccupiedShipID(),
                C.GetPlayerControlledShipID(), C.GetPlayerContainerID()
            if controlled then
                if occupied ~= id or active ~= id or container ~= id then
                    return 'LOCAL_CONTROL_MISSING'
                end
            else
                if occupied == id or active == id or container == id then return 'PLAYER_CONTEXT' end
                local docked, docking, stored =
                    GetComponentData(lua_id, 'isdocked', 'isdocking', 'isinternallystored')
                if docked ~= false or docking ~= false or stored ~= false then
                    return 'SHIP_DOCK_STATE_UNSAFE'
                end
            end
        end,
        log = function(row) DebugError('[X4LAN] AUTH_CLIENT_SEAT ' .. row) end,
        fault = function(row)
            DebugError('[X4LAN] AUTH_CLIENT_SEAT_REFUSED ' .. row)
            on_fault()
        end,
    })
end

X4LAN_AUTHORITY_CLIENT_SEAT = M
return M
