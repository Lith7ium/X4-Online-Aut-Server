-- Opt-in client coordinator. The caller owns the admitted transport and the
-- correlated MD lock actuator. No events, hooks or locks are installed on load.
local M = {}
local berth_keys = {'sector', 'station_code', 'bay_seed', 'bay_macro', 'dockarea_macro'}

local function token(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function berth_from_seat(value)
    if type(value) ~= 'table' or value.external ~= true or value.occupied_by_ship ~= true then return end
    local berth = {sector = value.sector, station_code = value.station_code,
        bay_seed = value.bay_seed, bay_macro = value.bay_macro, dockarea_macro = value.dockarea_macro}
    for _, key in ipairs(berth_keys) do
        if type(berth[key]) ~= 'string' or berth[key] == '' then return end
    end
    return berth
end

local function same_berth(left, right)
    if type(right) ~= 'table' then return false end
    for _, key in ipairs(berth_keys) do
        if left[key] ~= right[key] then return false end
    end
    return true
end

local function copy_binding(binding)
    local copy = {}
    for key, value in pairs(binding) do copy[key] = value end
    if binding.berth then
        copy.berth = {}
        for _, key in ipairs(berth_keys) do copy.berth[key] = binding.berth[key] end
    end
    return copy
end

function M.install(options)
    if options.enabled ~= true then return nil, 'disabled' end
    local menu, seat = options.menu, options.seat
    assert(type(menu) == 'table' and type(menu.buttonUndock) == 'function'
        and type(menu.isUndockButtonActive) == 'function')
    assert(type(seat) == 'table' and type(seat.controlled_actor) == 'function'
        and type(seat.read_occupied_berth) == 'function')
    assert(token(options.epoch) and type(options.ship_index) == 'number' and options.ship_index >= 1
        and options.ship_index % 1 == 0)
    for _, name in ipairs({'admission', 'menu_context', 'permission', 'request_id', 'hold', 'request'}) do
        assert(type(options[name]) == 'function', name .. ' required')
    end
    local stock, epoch, ship_index = menu.buttonUndock, options.epoch, options.ship_index
    local api, active, stopped = {}, nil, false

    local function current(operation)
        return not stopped and active == operation and options.admission() == operation.admission
            and seat:controlled_actor() == operation.actor
    end

    local function matches_menu(operation)
        return not menu.buildInProgress and not menu.buildToCancel
            and options.menu_context(operation.actor, operation.berth) == true
    end

    local function read_berth(operation, phase, callback)
        operation.phase = phase
        local ok, issued = pcall(seat.read_occupied_berth, seat, function(snapshot)
            if not current(operation) or operation.phase ~= phase then return end
            local berth = berth_from_seat(snapshot)
            if not berth or (operation.berth and not same_berth(operation.berth, berth)) then
                operation.phase = 'failed'
                return
            end
            operation.berth = berth
            callback()
        end)
        if (not ok or issued ~= true) and operation.phase == phase then operation.phase = 'failed' end
        return ok and issued == true
    end

    local function change_hold(operation, action, phase, done)
        operation.phase = phase
        -- The native actuator must confirm its own matched lock mutation and
        -- revalidate the exact local berth/actor before it clears the lock.
        local ok, issued = pcall(options.hold, action, copy_binding({
            admission = operation.admission, epoch = epoch, ship_index = ship_index,
            actor = operation.actor, berth = operation.berth, request_id = operation.request_id,
        }), function(confirmed)
            if not current(operation) or operation.phase ~= phase then return end
            if confirmed ~= true then operation.phase = 'failed'; return end
            done()
        end)
        if (not ok or issued ~= true) and operation.phase == phase then operation.phase = 'failed' end
    end

    local function click(...)
        local operation = active
        if not operation or not matches_menu(operation) then return stock(...) end
        -- A matching row remains captured even if the admission expired or a
        -- request failed. Only the explicit confirmed path may replay it.
        if not current(operation) or operation.phase ~= 'held' then return end
        local arguments = {n = select('#', ...), ...}
        read_berth(operation, 'capturing', function()
            if not matches_menu(operation) then operation.phase = 'failed'; return end
            local request_id = options.request_id()
            if not token(request_id) then operation.phase = 'failed'; return end
            operation.request_id = request_id
            operation.arguments = arguments
            operation.phase = 'pending'
            local request = {request_id = request_id}
            for _, key in ipairs(berth_keys) do request[key] = operation.berth[key] end
            local ok, sent = pcall(options.request, request)
            if (not ok or sent ~= true) and operation.phase == 'pending' then operation.phase = 'failed' end
        end)
    end
    menu.buttonUndock = click

    function api:arm()
        -- This must run before the supported Docked row is usable. The stock
        -- permission check establishes a granted baseline before claiming MD's
        -- paired global player lock. This is a one-departure controller.
        if stopped or active then return false end
        local admission, actor = options.admission(), seat:controlled_actor()
        if not admission or not actor or options.permission() ~= true then return false end
        local operation = {admission = admission, actor = actor}
        active = operation
        return read_berth(operation, 'arming', function()
            if options.permission() ~= true then
                operation.phase = 'failed'
                return
            end
            change_hold(operation, 'set', 'setting', function() operation.phase = 'held' end)
        end)
    end

    -- The caller delivers only receipts from the authenticated, current seat
    -- envelope. The echo names the local berth even if the provider uses a
    -- different bay at the same station.
    function api:confirm(result)
        local operation = active
        if not operation or operation.phase ~= 'pending' or not current(operation)
                or not matches_menu(operation) or type(result) ~= 'table'
                or result.outcome ~= 'confirmed' or result.request_id ~= operation.request_id
                or not same_berth(operation.berth, result) then return false end
        return read_berth(operation, 'authorizing', function()
            if not matches_menu(operation) then operation.phase = 'failed'; return end
            change_hold(operation, 'release', 'releasing', function()
                if not matches_menu(operation) then operation.phase = 'failed'; return end
                -- Mark consumed before entering stock. Even a failed stock
                -- invocation cannot replay the authorization.
                operation.phase = 'consumed'
                local args = operation.arguments
                operation.arguments = nil
                stock(unpack(args, 1, args.n))
            end)
        end)
    end

    function api:tick()
        if stopped or not active then return end
        local phase = active.phase
        if phase ~= 'consumed' and (not current(active)
                or ((phase == 'capturing' or phase == 'pending' or phase == 'authorizing'
                    or phase == 'releasing') and not matches_menu(active))) then
            active.phase, active.arguments = 'failed', nil
        end
    end

    function api:state()
        return stopped and 'stopped' or (active and active.phase or 'idle')
    end

    function api:stop()
        stopped = true
        if active then active.arguments = nil end
        -- A held or uncertain lock survives stop. The disposable session is
        -- disposed by its owner. Old captured rows remain fenced.
        if not active and menu.buttonUndock == click then menu.buttonUndock = stock end
    end

    return api
end

function M.install_x4(options)
    if options.enabled ~= true then return nil, 'disabled' end
    local menu
    for _, candidate in ipairs(Menus or {}) do
        if candidate.name == 'DockedMenu' then
            if menu then return nil, 'ambiguous_menu' end
            menu = candidate
        end
    end
    if not menu then return nil, 'menu_unavailable' end

    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[
        const char* UndockPlayerShip(bool);
        UniverseID GetContextByClass(UniverseID, const char*, bool);
    ]]
    local C = ffi.C
    return M.install({
        enabled = true, menu = menu, seat = options.seat,
        epoch = options.epoch, ship_index = options.ship_index,
        admission = options.admission, request_id = options.request_id,
        hold = options.hold, request = options.request,
        permission = function()
            local result = C.UndockPlayerShip(true)
            return result ~= nil and ffi.string(result) == 'granted'
        end,
        menu_context = function(actor)
            local ship = X4LAN_RESOLVE_STRING.parse_hex(actor)
            return menu.shown == true and menu.mode == 'docked' and ship ~= nil and ship ~= 0
                and menu.currentplayership == ship and menu.currentcontainer ~= nil
                and menu.currentcontainer ~= 0 and C.GetContextByClass(ship, 'container', false) == menu.currentcontainer
        end,
    })
end

X4LAN_COMPANY_UNDOCK_HOLD = M
return M
