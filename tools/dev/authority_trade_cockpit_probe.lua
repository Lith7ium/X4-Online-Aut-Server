-- Private recovered-world offer/cockpit probe. No trade is dispatched.
local cfg = X4LAN_CONFIG
local mode = cfg and cfg.authority_trade_probe_mode
local from_flight = cfg and cfg.authority_player_dock_flight == true
if not from_flight and mode ~= 'offer' and mode ~= 'cockpit' then return end

local ffi = require('ffi')
if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
ffi.cdef[[
    bool IsComponentClass(UniverseID,const char*);
    bool IsComponentOperational(UniverseID);
    UniverseID GetPlayerControlledShipID(void);
    UniverseID GetPlayerOccupiedShipID(void);
    UniverseID GetPlayerContainerID(void);
    UniverseID GetContextByClass(UniverseID,const char*,bool);
    uint32_t GetNumDockedShips(UniverseID,const char*);
    uint32_t GetDockedShips(UniverseID*,uint32_t,UniverseID,const char*);
    const char* GetObjectIDCode(UniverseID);
    const char* CanTeleportPlayerTo(UniverseID,bool,bool);
    double GetCurrentGameTime(void);
    bool IsGamePaused(void);
]]
local C = ffi.C
local screen = 'X4AuthorityTradeCockpit'
local quantity = 6
local phase, deadline, take_time, next_verify = 'init'
local last_md_seat
local binding, menu, hook, captured, parked
local failed = false
local final_status, prepared

local function log(row) DebugError('[X4LAN] AUTH_TRADE_COCKPIT ' .. row) end

local function finish(status)
    phase, final_status = 'done', status
    log('DONE|' .. status)
end

local function guarded(fn, ...)
    if failed or phase == 'done' then return end
    local ok, failure = pcall(fn, ...)
    if not ok then
        failed = true
        log('ERROR|' .. phase .. '/' .. tostring(failure):gsub('[\r\n|]', ' '))
    end
end

local function issue(action, data, wait)
    phase = wait
    assert(AddUITriggeredEvent(screen, action, data) ~= false, 'dispatch_' .. action)
end

local function lua_id(id)
    return assert(ConvertStringToLuaID(tostring(id)), 'lua_id')
end

local function integer(value)
    return type(value) == 'number' and value >= 0 and value <= 2147483647 and value % 1 == 0
end

local function unit_cents(price)
    assert(type(price) == 'number' and price > 0 and price <= 21474836.47, 'price_type')
    local scaled = price * 100
    local cents = math.floor(scaled + 0.5)
    assert(cents > 0 and math.abs(cents - scaled) <= 0.0000001, 'non_integral_price')
    return cents
end

local function external_dock(report)
    local ship, station = binding.ship, binding.station
    if not C.IsComponentClass(ship, 'ship') or not C.IsComponentOperational(ship)
            or not C.IsComponentClass(station, 'station') or not C.IsComponentOperational(station) then return false end
    local parent = C.GetContextByClass(binding.dock, 'station', true)
    local owner, docked, docking, stored = GetComponentData(binding.ship_lua,
        'owner', 'isdocked', 'isdocking', 'isinternallystored')
    local count = tonumber(C.GetNumDockedShips(station, nil))
    local returned, listed
    if count and count >= 1 and count <= 4096 then
        local ships = ffi.new('UniverseID[?]', count)
        returned = tonumber(C.GetDockedShips(ships, count, station, nil))
        if returned == count then
            listed = false
            for i = 0, count - 1 do
                if ships[i] == ship then listed = true; break end
            end
        end
    end
    if report then
        log(('DOCK|owner=%s|docked=%s|docking=%s|stored=%s|parent=%s|expected=%s|count=%s|returned=%s|listed=%s'):format(
            tostring(owner), tostring(docked), tostring(docking), tostring(stored), tostring(parent),
            tostring(station), tostring(count), tostring(returned), tostring(listed)))
    end
    return owner == 'player' and docked == true and stored == false and parent == station and listed == true
end

local function seat_state()
    return table.concat({last_md_seat or 'unobserved|unknown|unknown|unknown',
        tostring(C.GetPlayerControlledShipID()), tostring(C.GetPlayerOccupiedShipID()),
        tostring(C.GetPlayerContainerID())}, '|')
end

local function controlled()
    local ship = binding.ship
    return C.GetPlayerControlledShipID() == ship and C.GetPlayerOccupiedShipID() == ship
        and C.GetPlayerContainerID() == ship
end

local function map_unavailable(reason)
    log('MAP_UNAVAILABLE|' .. tostring(reason):gsub('[\r\n|]', ' '))
    finish('seat_unavailable')
end

local function parked_unchanged()
    return parked and C.GetPlayerControlledShipID() == parked.controlled
        and C.GetPlayerOccupiedShipID() == parked.occupied and C.GetPlayerContainerID() == parked.container
        and C.IsGamePaused() and external_dock()
end

local function find_water(rows)
    assert(type(rows) == 'table', 'offer_list')
    local found
    for _, row in pairs(rows) do
        if row.ware == 'water' and row.isselloffer == true and not row.isbuyoffer and not row.ismissionoffer then
            assert(not found, 'ambiguous_water')
            found = row
        end
    end
    assert(found and found.id, 'water_offer_missing')
    local detail = GetTradeData(lua_id(ConvertIDTo64Bit(found.id)))
    assert(type(detail) == 'table' and detail.station
        and ConvertIDTo64Bit(detail.station) == binding.station, 'offer_station')
    return found
end

local function read_offer()
    local row = find_water(GetTradeList(binding.station_lua, binding.ship_lua))
    assert(integer(row.amount) and integer(row.minamount), 'offer_amounts')
    local tradable = CanTradeWith(row.id, binding.ship_lua, quantity, true)
    log(('OFFER|%d|%d|%d|%d'):format(unit_cents(row.price), row.amount, row.minamount, tradable and 1 or 0))
    local checked, at_dock = pcall(external_dock, true)
    if not checked or not at_dock then log('DOCK|unverified') end
    if mode == 'offer' then finish('offer_read'); return end
    if not checked or not at_dock then
        log('SEAT|unavailable|dock_binding')
        map_unavailable('dock_binding')
        return
    end

    local function permission(force)
        local value = C.CanTeleportPlayerTo(binding.ship, true, force)
        return value ~= nil and ffi.string(value) or 'unavailable'
    end
    local regular, waived = permission(false), permission(true)
    log('PERMISSION|' .. regular .. '|' .. waived)
    if waived ~= 'granted' then
        log('SEAT|unavailable|' .. waived)
        -- No Take was dispatched. Pin the original local seat context.
        parked = {controlled = C.GetPlayerControlledShipID(), occupied = C.GetPlayerOccupiedShipID(),
            container = C.GetPlayerContainerID()}
        phase = 'begin_remote_map'
        return
    end
    deadline = GetCurRealTime() + 8
    issue('Take', {available = true, ship = binding.ship_hex, station = binding.station_hex}, 'take_reply')
end

local function begin_map(read_only)
    if read_only then
        assert(parked_unchanged(), 'dock_or_seat_changed_before_map')
    else
        assert(controlled() and external_dock(), 'seat_changed_before_map')
    end
    for _, candidate in ipairs(Menus) do
        if candidate.name == 'MapMenu' then assert(not menu, 'ambiguous_map_menu'); menu = candidate end
    end
    assert(menu and type(menu.buttonConfirmTrade) == 'function', 'map_menu')
    -- Install before the stock button handlers are built. For this disposable
    -- menu the fallback is a tripwire, never the original booking function.
    menu.buttonConfirmTrade = function() error('blocked_original_trade_confirmation', 0) end
    if not read_only then
        local sector = C.GetContextByClass(binding.station, 'sector', false)
        local code = C.GetObjectIDCode(binding.station)
        assert(sector ~= 0 and code ~= nil, 'station_context')
        binding.sector = GetComponentData(lua_id(sector), 'macro')
        binding.station_code = ffi.string(code)
        hook = assert(X4LAN_COMPANY_TRADE_CAPTURE.install_x4({
            enabled = true, menu = menu, ship = binding.ship, station = binding.station,
            sector = binding.sector, station_code = binding.station_code,
            request = function(intent)
                assert(not captured and intent.sector == binding.sector and intent.station_code == binding.station_code
                    and intent.ware == 'water' and intent.quantity == quantity, 'unexpected_intent')
                captured = intent
                return false
            end,
            notice = function(status) log('NOTICE|' .. status) end,
        }))
    end
    deadline = GetCurRealTime() + 8
    phase = read_only and 'wait_remote_map' or 'wait_map'
    OpenMenu('MapMenu', {0, 0, true, nil, nil, 'tradecontext',
        {binding.station_lua, binding.ship_lua, false}}, nil)
end

local function capture_remote_quote(data)
    assert(parked_unchanged() and not data.wareexchange, 'remote_context_changed')
    local offer = find_water(data.selloffers)
    local actual = menu.getTradeOfferByID(lua_id(ConvertIDTo64Bit(offer.id)))
    assert(actual == offer and integer(actual.amount) and type(data.immediate) == 'boolean', 'remote_offer_shape')
    log(('MAP_QUOTE|%d|%d|%d'):format(unit_cents(actual.price), actual.amount, data.immediate and 1 or 0))
    finish('seat_unavailable')
end

local function capture_menu(data)
    assert(controlled() and external_dock() and data.immediate == true and data.immediateObject == binding.ship
        and not data.wareexchange, 'stock_immediate_context')
    local offer = find_water(data.selloffers)
    assert(offer.active and offer.minamount <= quantity and offer.amount >= quantity, 'water_selection_unavailable')
    assert(CanTradeWith(offer.id, binding.ship_lua, quantity, true), 'water_offer_not_tradable')
    local waredata
    for _, row in ipairs(data.waredatalist) do
        if row.ware == 'water' and row.sell and IsSameTrade(row.sell.id, offer.id) then
            assert(not waredata, 'ambiguous_water_slider')
            waredata = row
        end
    end
    assert(waredata and type(data.orders) == 'table', 'water_slider_or_basket')
    for _, amount in pairs(data.orders) do assert(amount == 0, 'basket_not_empty') end
    local prior = data.currentcargo.water or 0
    assert(integer(prior), 'water_cargo_before')
    -- A stock slider takes the movement, with the row callback's zero basis.
    menu.slidercellShipCargo(waredata.sell.id, waredata.buy and waredata.buy.id, 'water', 0, quantity)
    menu.slidercellTradeConfirmed('water')
    assert(menu.contextMenuData == data and controlled() and external_dock(), 'context_changed')
    prepared = {data = data, offer = offer, prior = prior}
    phase = 'confirm_map'
end

local function confirm_map()
    local data, offer = prepared.data, prepared.offer
    assert(menu.contextMenuData == data and controlled() and external_dock() and data.immediate == true
        and data.immediateObject == binding.ship and data.component == binding.station
        and data.currentShip == binding.ship and not data.wareexchange, 'preconfirm_context_changed')
    local selected
    for key, amount in pairs(data.orders) do
        if amount ~= 0 then
            local row = menu.getTradeOfferByID(lua_id(key))
            assert(not selected and amount == -quantity and row and IsSameTrade(row.id, offer.id), 'stock_basket')
            selected = row
        end
    end
    assert(selected, 'stock_slider_no_quantity')
    log(('SLIDER|%d|%d|%d'):format(prepared.prior, prepared.prior + quantity, -quantity))
    local cents = unit_cents(selected.price)
    menu.buttonConfirmTrade()
    assert(captured and captured.price_cents == cents and hook.detach(), 'intent_not_captured')
    log(('INTENT|%s|%s|water|%d|%d'):format(captured.sector, captured.station_code, quantity, cents))
    finish('intent_captured')
end

local function seat_unavailable(reason)
    if take_time then log('SEAT_LAST|' .. seat_state()) end
    log('SEAT|unavailable|' .. reason)
    map_unavailable('seat_transition_unverified')
end

local function on_result(_, row)
    assert(type(row) == 'string', 'md_result_type')
    local action, status, ship, station, dock, captain, control, occupied, container = row:match(
        '^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$')
    assert(action, 'md_result_shape')
    local parse = X4LAN_RESOLVE_STRING.parse_hex
    if phase == 'inspect_reply' then
        assert(action == 'INSPECT' and status == 'READY', 'inspect_' .. status)
        binding = {ship = assert(parse(ship), 'ship_id'), station = assert(parse(station), 'station_id'),
            dock = assert(parse(dock), 'dock_id'), captain = parse(captain), ship_hex = ship, station_hex = station}
        binding.ship_lua, binding.station_lua = lua_id(binding.ship), lua_id(binding.station)
        phase = 'read_offer'
        return
    end
    if phase == 'verify_reply' and action == 'VERIFY' then
        last_md_seat = table.concat({status, control, occupied, container}, '|')
        log('SEAT_STATE|' .. seat_state())
        if parse(ship) ~= binding.ship or parse(station) ~= binding.station or parse(dock) ~= binding.dock
                or parse(captain) ~= binding.captain then
            seat_unavailable('md_binding_changed')
            return
        end
    else
        assert(parse(ship) == binding.ship and parse(station) == binding.station and parse(dock) == binding.dock
            and parse(captain) == binding.captain, 'md_binding_changed')
    end
    if phase == 'take_reply' then
        assert(action == 'TAKE', 'take_result_kind')
        if status ~= 'DISPATCHED' then seat_unavailable(status); return end
        log('SEAT|dispatched')
        take_time = tonumber(C.GetCurrentGameTime())
        assert(take_time and take_time >= 0 and take_time < math.huge, 'take_time')
        phase = 'resume_seat'
    elseif phase == 'verify_reply' then
        assert(action == 'VERIFY', 'verify_result_kind')
        if status == 'CONTROLLED' and parse(control) == binding.ship and parse(occupied) == binding.ship
                and parse(container) == binding.ship and controlled() then
            log('SEAT_LAST|' .. seat_state())
            log('SEAT|controlled')
            phase = 'begin_map'
            return
        end
        if status == 'CONTROLLED' or status == 'CONTROL_MISSING' then
            next_verify = GetCurRealTime() + 0.5
            phase = 'verify'
        else
            seat_unavailable(status)
        end
    else
        error('unexpected_md_result', 0)
    end
end

local function frame(allow_confirm)
    local now = GetCurRealTime()
    if phase == 'init' then
        assert(RegisterEvent(screen .. '.Result', function(...) guarded(on_result, ...) end) ~= false, 'register')
        deadline = now + 8
        issue('Inspect', {}, 'inspect_reply')
    elseif phase == 'read_offer' then
        read_offer()
    elseif phase == 'resume_seat' then
        if C.IsGamePaused() then Unpause() end
        deadline, next_verify = now + 8, now
        phase = 'verify'
    elseif phase == 'take_reply' or phase == 'verify' or phase == 'verify_reply' then
        if now >= deadline then
            seat_unavailable('control_timeout')
        elseif phase == 'verify' and now >= next_verify and tonumber(C.GetCurrentGameTime()) > take_time then
            issue('Verify', {}, 'verify_reply')
        end
    elseif phase == 'begin_map' then
        begin_map(false)
    elseif phase == 'begin_remote_map' then
        local ok, failure = pcall(begin_map, true)
        if not ok then map_unavailable(failure) end
    elseif phase == 'wait_map' or phase == 'wait_remote_map' then
        local data = menu.contextMenuData
        if menu.contextMenuMode == 'trade' and type(data) == 'table'
                and data.component == binding.station and data.currentShip == binding.ship
                and type(data.waredatalist) == 'table' then
            if phase == 'wait_remote_map' then
                local ok, failure = pcall(capture_remote_quote, data)
                if not ok then map_unavailable(failure) end
            else
                capture_menu(data)
            end
        elseif now >= deadline then
            if phase == 'wait_remote_map' then map_unavailable('map_context_timeout')
            else error('map_context_timeout', 0) end
        end
    elseif phase == 'confirm_map' then
        if not from_flight or allow_confirm then confirm_map() end
    elseif phase == 'inspect_reply' then
        assert(now < deadline, 'inspect_timeout')
    end
end

if from_flight then
    function X4LAN_AUTHORITY_TRADE_CAPTURE_FROM_DOCK(ship, station, dock)
        assert(phase == 'init' and ship and station and dock, 'capture_binding')
        binding = {ship = ship, station = station, dock = dock,
            ship_lua = lua_id(ship), station_lua = lua_id(station)}
        phase = 'begin_map'
        return function(allow_confirm)
            guarded(frame, allow_confirm)
            return final_status, failed, phase == 'confirm_map', captured
        end
    end
else
    function X4LAN_RECOVERY_ON_PAUSED()
        guarded(frame)
    end
end
