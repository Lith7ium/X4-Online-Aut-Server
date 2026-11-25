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
    local api, active, stopped, paused = {}, nil, false, false
    local read_serial = 0

    local function current(operation)
        return not stopped and not paused and active == operation and options.admission() == operation.admission
            and seat:controlled_actor() == operation.actor
    end

    local function matches_menu(operation)
        return not menu.buildInProgress and not menu.buildToCancel
            and options.menu_context(operation.actor, operation.berth) == true
    end

    local function read_berth(operation, phase, callback)
        operation.phase = phase
        read_serial = read_serial + 1
        local serial = read_serial
        local ok, issued = pcall(seat.read_occupied_berth, seat, function(snapshot)
            if serial ~= read_serial or not current(operation) or operation.phase ~= phase then return end
            local berth = berth_from_seat(snapshot)
            if not berth or (operation.berth and not same_berth(operation.berth, berth)) then
                if phase == 'arming' then active = nil else operation.phase = 'failed' end
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
        if stopped or paused or active then return false end
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
        if stopped or paused or not active then return end
        local phase = active.phase
        if phase ~= 'consumed' and (not current(active)
                or ((phase == 'capturing' or phase == 'pending' or phase == 'authorizing'
                    or phase == 'releasing') and not matches_menu(active))) then
            active.phase, active.arguments = 'failed', nil
        end
    end

    function api:pause()
        if stopped or paused then return end
        paused = true
        if not active then return end
        read_serial = read_serial + 1
        local phase = active.phase
        if phase == 'arming' then
            active = nil -- No lock mutation has been dispatched.
        elseif phase == 'capturing' then
            active.phase = 'held' -- No click request has been sent.
        elseif phase == 'revalidating' then
            active.phase = active.resume_phase
        elseif phase == 'authorizing' then
            active.phase = 'pending' -- A fresh read must precede release.
        elseif phase == 'setting' or phase == 'releasing' then
            -- A stock mutation may have been dispatched before the pause.
            -- Never act on its delayed reply; the private owner must clean up.
            self:stop()
        end
    end

    function api:resume()
        if stopped or not paused then return end
        paused = false
        if not active then return end
        local operation, phase = active, active.phase
        if phase ~= 'held' and phase ~= 'pending' then return end
        if not current(operation) or (phase == 'pending' and not matches_menu(operation)) then
            operation.phase, operation.arguments = 'failed', nil
            return
        end
        operation.resume_phase = phase
        read_berth(operation, 'revalidating', function()
            if phase == 'pending' and not matches_menu(operation) then
                operation.phase, operation.arguments = 'failed', nil
                return
            end
            operation.phase = phase
        end)
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

-- Use the already owned Company purchase UDP socket. The first hold marker
-- and request ID are private, generated independently by the Python owner.
function M.new_ipc(config, net, clock, available)
    assert(token(config.undock_nonce) and token(config.undock_request))
    local screen = 'X4AuthorityClientUndock'
    local prefix = 'T|' .. config.lease .. '|'
    local running, stopped, resume_needed = false, false, false
    local controller, pending, change, receipt
    local next_send, selected_seat = 0, nil
    local api = {}
    local seat_proxy = {
        controlled_actor = function() return selected_seat and selected_seat:controlled_actor() end,
        read_occupied_berth = function(_, callback)
            return selected_seat and selected_seat:read_occupied_berth(callback)
        end,
    }

    local function live()
        return not stopped and running and available()
    end

    assert(RegisterEvent(screen .. '.Result', function(_, row)
        if not change or type(row) ~= 'string' then return end
        local expected = config.undock_nonce .. '|' .. change.action .. '|'
        if row ~= expected .. 'ok' and row ~= expected .. 'refused' then return end
        local action, settled = change.action, change.callback
        change = nil
        if live() then
            local accepted = row == expected .. 'ok'
            if accepted then
                DebugError('[X4LAN] AUTH_COMPANY_UNDOCK_HOLD ' .. (action == 'set' and 'set' or 'released'))
            end
            settled(accepted)
        end
    end) ~= false)

    function api:stop()
        stopped, running = true, false
        pending, change, receipt = nil, nil, nil
        if controller then controller:stop() end
    end

    function api:set_running(value)
        if stopped or running == (value == true) then return end
        running = value == true
        if not running and controller then
            controller:pause()
            if controller:state() == 'stopped' then self:stop(); return end
            resume_needed = true
        end
    end

    function api:result(row)
        if stopped or not pending or receipt or #row ~= 8 or row[3] ~= pending.request_id then return end
        local result = {outcome = row[2], request_id = row[3]}
        for i, key in ipairs(berth_keys) do
            if row[3 + i] ~= pending[key] then return end
            result[key] = row[3 + i]
        end
        if result.outcome == 'confirmed' or result.outcome == 'refused' or result.outcome == 'unknown' then
            receipt = result
        end
    end

    function api:tick(seat)
        if stopped then return end
        if selected_seat and seat ~= selected_seat then self:stop(); return end
        selected_seat = seat
        if not controller then
            controller = M.install_x4({
                enabled = true, seat = seat_proxy, epoch = config.epoch, ship_index = config.index,
                admission = function() return live() and config.undock_nonce or nil end,
                request_id = function() return config.undock_request end,
                hold = function(action, binding, fn)
                    if not live() or change or binding.admission ~= config.undock_nonce then return false end
                    local args = {action = action, nonce = config.undock_nonce, request_id = config.undock_request,
                        epoch = binding.epoch, index = binding.ship_index, actor = binding.actor}
                    for _, key in ipairs(berth_keys) do args[key] = binding.berth[key] end
                    change = {action = action, callback = fn}
                    if AddUITriggeredEvent(screen, 'Change', args) == false then change = nil; return false end
                    return true
                end,
                request = function(request)
                    if not live() or pending then return false end
                    pending, next_send = request, 0
                    return true
                end,
            })
        end
        if not controller or not live() then return end
        if resume_needed then
            resume_needed = false
            controller:resume()
        end
        controller:tick()
        local phase = controller:state()
        if phase == 'idle' then controller:arm() end
        if phase == 'releasing' or phase == 'consumed' or phase == 'failed' then
            pending, receipt = nil, nil
        elseif phase == 'pending' and receipt then
            if receipt.outcome == 'confirmed' then
                controller:confirm(receipt)
            else
                pending, receipt = nil, nil
            end
            return
        end
        local now = clock()
        if pending and phase == 'pending' and now >= next_send then
            next_send = now + 0.25
            local parts = {'UNDOCK', pending.request_id}
            for _, key in ipairs(berth_keys) do parts[#parts + 1] = pending[key] end
            net:send(prefix .. table.concat(parts, '|'))
        end
    end
    return api
end

X4LAN_COMPANY_UNDOCK_HOLD = M
return M
