-- Establish the occupied cockpit or a reserved passenger during paused load.
local M = {}
local SCREEN = 'X4AuthorityClientSeat'
local REFUSALS = {
    REGISTRY_INVALID = true, REGISTRY_CHANGED = true, INDEX_INVALID = true,
    BINDING_INVALID = true, OWNER_INVALID = true, SHIP_INOPERABLE = true,
    TARGET_CHANGED = true, CONTROL_MISSING = true, PLAYER_CONTEXT = true,
    SHIP_DOCKED_OR_STORED = true, SHIP_SECTOR_INVALID = true, PILOT_PRESENT = true,
    ACTION_INVALID = true, CAPTAIN_CHANGED = true, CAPTAIN_CONTEXT = true,
}

local function berth_identity(row)
    local sector, station, seed, bay, area = row:match(
        '^([A-Za-z0-9_]+)|([A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9])|(-?[1-9]%d*)|([a-z][a-z0-9_]*)|([a-z][a-z0-9_]*)$')
    if not sector or #sector > 128 or #bay > 128 or #area > 128 then return nil end
    -- A seed is a native largeint. Keep it exact across Lua/JSON boundaries.
    local negative = seed:sub(1, 1) == '-'
    local magnitude = negative and seed:sub(2) or seed
    local limit = negative and '9223372036854775808' or '18446744073709551615'
    if #magnitude > #limit or (#magnitude == #limit and magnitude > limit) then return nil end
    return {sector = sector, station_code = station, bay_seed = seed, bay_macro = bay,
        dockarea_macro = area, external = true, occupied_by_ship = true}
end

function M.new(options)
    local pinned, index = options.pinned, options.index
    local prefix = pinned.nonce .. '|' .. pinned.epoch .. '|' .. index .. '|'
    local state, pending, target = 'inspect', nil, nil
    local deadline, last_time, target_owner, captain
    local admitted, admission, next_poll = false, false, 0
    local berth_registered, berth_request, berth_pending = false, 0, nil
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
        if not admission and options.guard() ~= true then return fail('PAUSE_OR_REGISTRY_LOST') end
        return true
    end

    local function native_refusal(context)
        local ok, refusal = pcall(options.inspect, target, target_owner, context == 'helm', context)
        if not ok then return 'SHIP_READ_FAILED' end
        return refusal
    end

    local function read_target(context)
        local refusal = native_refusal(context)
        if refusal then return fail(refusal) end
        return true
    end

    local function accept_result(action, result)
        if action == 'inspect' and result == 'READY' then
            if not read_target('empty') then return false end
            state = 'take'
        elseif action == 'inspect' and result == 'CREWED' then
            if not captain then return fail('CAPTAIN_CHANGED') end
            if not read_target('crew') then return false end
            state = 'board'
        elseif action == 'take' and result == 'DISPATCHED' then
            state = 'verify'
        elseif (action == 'inspect' or action == 'verify' or action == 'helmsample')
                and result == 'CONTROLLED' then
            if not read_target('helm') then return false end
            state = 'confirm'
        elseif (action == 'board' and result == 'BOARD_DISPATCHED')
                or (action == 'aboard' and result == 'BOARD_WAIT') then
            state = 'aboard'
            next_poll = last_time + 0.25
        elseif action == 'aboard' and result == 'ABOARD' then
            if not read_target('aboard') then return false end
            state = 'confirmaboard'
        elseif action == 'relief' and result == 'RELIEF_WAIT' then
            state = 'relief'
        elseif (action == 'relief' and result == 'RELIEF_DISPATCHED')
                or (action == 'helm' and result == 'HELM_WAIT') then
            state = 'helm'
        elseif (action == 'helm' and result == 'HELM_DISPATCHED')
                or (action == 'helmsample' and result == 'HELM_WAIT') then
            state = 'helmsample'
        else
            return fail('RESULT_INVALID')
        end
        return true
    end

    function api:event(row)
        if not pending or state ~= 'waiting' then return fail('UNEXPECTED_RESULT') end
        if not guard() then return false end
        if type(row) ~= 'string' or #row > 240 or row:sub(1, #prefix) ~= prefix then
            return fail('RESULT_MISMATCH')
        end
        local body = row:sub(#prefix + 1)
        local action, result, actor, owner, reported_captain =
            body:match('^([a-z]+)|([A-Z_]+)|([^|]+)|([^|]+)|([^|]+)$')
        if not action then
            action, result, actor, owner = body:match('^([a-z]+)|([A-Z_]+)|([^|]+)|([^|]+)$')
        end
        if action ~= pending then return fail('RESULT_MISMATCH') end
        if REFUSALS[result] then return fail(result) end
        if target and (actor ~= target or owner ~= target_owner) then return fail('TARGET_CHANGED') end
        if reported_captain == '0' then reported_captain = nil end
        if reported_captain and not reported_captain:match('^0[xX][0-9a-fA-F]+$') then
            return fail('CAPTAIN_CHANGED')
        end
        if (target and captain ~= reported_captain) or (not target and result ~= 'CREWED' and reported_captain) then
            return fail('CAPTAIN_CHANGED')
        end
        pending = nil
        target, target_owner, captain = actor, owner, reported_captain
        if admitted then
            deadline = nil
            next_poll = last_time + 0.25
        end
        return accept_result(action, result)
    end

    function api:tick()
        if state == 'failed' or not guard() then return false end
        if state == 'confirm' then
            if not read_target('helm') then return false end
            options.log(prefix .. 'controlled')
            state, deadline, admission = 'done', nil, true
            return true
        end
        if state == 'confirmaboard' then
            if not read_target('aboard') then return false end
            options.log(prefix .. 'aboard')
            state, deadline, admission = 'passenger', nil, true
            return true
        end
        if state == 'waiting' or state == 'done' then return true end
        if state == 'passenger' then
            if not admitted then return true end
            state = 'relief'
        end
        if (admitted and options.running() ~= true) or last_time < next_poll then return true end
        if state == 'take' and not read_target('empty') then return false end
        if state == 'board' and not read_target('crew') then return false end
        deadline = deadline or (last_time + 10)
        pending, state = state, 'waiting'
        local ok, issued = pcall(options.issue, {
            nonce = pinned.nonce, epoch = pinned.epoch, count = pinned.issued_count,
            index = index, action = pending, actor = target or '0', captain = captain or '0',
        })
        if not ok or issued ~= true then return fail('DISPATCH_FAILED') end
        return state ~= 'failed'
    end

    local function admission_context()
        if state == 'passenger' then return native_refusal('aboard') == nil end
        return api:ready()
    end

    function api:admission_ready()
        if not admission or state == 'failed' then return false end
        if admitted then return true end
        return options.guard() == true and admission_context()
    end

    function api:admit()
        -- The server can acknowledge after the local clock has resumed.
        if not admission or state == 'failed' or (not admitted and not admission_context()) then return false end
        admitted = true
        return true
    end

    function api:ready()
        return self:controlled_actor() ~= nil
    end

    function api:controlled_actor()
        if state == 'done' and native_refusal('helm') == nil then return target end
    end

    -- Request a local snapshot of this occupied cockpit's physical trade bay.
    -- The latest request supersedes any unanswered read. No purchase IPC is used.
    function api:read_occupied_berth(callback)
        local actor = self:controlled_actor()
        if not actor or type(callback) ~= 'function' or not options.register_berth
                or berth_request >= 2147483647 then return false end
        if not berth_registered then
            local ok, registered = pcall(options.register_berth, function(_, row)
                local read = berth_pending
                if not read or type(row) ~= 'string' or #row > 512
                        or row:sub(1, #read.prefix) ~= read.prefix then return end
                berth_pending = nil
                if self:controlled_actor() ~= read.actor then
                    read.callback(nil)
                else
                    read.callback(berth_identity(row:sub(#read.prefix + 1)))
                end
            end)
            if not ok or registered ~= true then return false end
            berth_registered = true
        end
        berth_request = berth_request + 1
        berth_pending = {prefix = prefix .. berth_request .. '|' .. actor .. '|',
            actor = actor, callback = callback}
        local ok, issued = pcall(options.issue_berth, {nonce = pinned.nonce, epoch = pinned.epoch,
            count = pinned.issued_count, index = index, request = berth_request, actor = actor})
        if not ok or issued ~= true then
            berth_pending = nil
            return false
        end
        return true
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
        bool IsGamePaused(void);
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
        guard = function() return registry:ready() and C.IsGamePaused() end,
        running = function() return not C.IsGamePaused() end,
        register = function(callback)
            return RegisterEvent(SCREEN .. '.Result', callback) ~= false
        end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Seat', args) ~= false end,
        register_berth = function(callback)
            return RegisterEvent(SCREEN .. '.OccupiedBerth', callback) ~= false
        end,
        issue_berth = function(args) return AddUITriggeredEvent(SCREEN, 'ReadOccupiedBerth', args) ~= false end,
        inspect = function(actor, owner, controlled, context)
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
            elseif context == 'aboard' then
                if container ~= id or active ~= 0 then return 'PLAYER_CONTEXT' end
                if GetComponentData(lua_id, 'isinternallystored') ~= false then return 'SHIP_DOCK_STATE_UNSAFE' end
            else
                if occupied == id or active == id or container == id then return 'PLAYER_CONTEXT' end
                local docked, docking, stored =
                    GetComponentData(lua_id, 'isdocked', 'isdocking', 'isinternallystored')
                if stored ~= false or (context ~= 'crew' and (docked ~= false or docking ~= false)) then
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
