-- Read one bound provider market, then serve its captured rows in bounded pages.
local M = {}
local screen = 'X4AuthorityMarketSurvey'
local max_packet, max_rows, max_page = 768, 512, 16
local max_integer, max_exact = 2147483647, 9007199254740991
local component_pattern = '^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$'

local function integer(value, minimum, maximum)
    return type(value) == 'number' and value >= minimum and value <= maximum and value % 1 == 0
end

local function exact_text(value, minimum, maximum)
    if type(value) ~= 'string' or #value > 16 or not value:match('^%d+$') then return end
    local number = tonumber(value)
    if integer(number, minimum, maximum) and ('%.0f'):format(number) == value then return number end
end

local function cents(value)
    if type(value) ~= 'number' or not (value > 0 and value <= max_integer / 100) then return end
    local scaled = value * 100
    local amount = math.floor(scaled + 0.5)
    if amount > 0 and math.abs(amount - scaled) <= 1e-7 then return amount end
end

local function free_units(C, ship, ship_lua, ware, ammo)
    if not ammo then return GetFreeCargoAfterTradeOrders(ship_lua, ware) end
    if ammo == 'missile' then return tonumber(C.GetFreeMissileStorageAfterTradeOrders(ship)) end
    if ammo == 'countermeasure' then return tonumber(C.GetFreeCountermeasureStorageAfterTradeOrders(ship)) end
    if ammo == 'unit' then return GetFreeUnitStorageAfterTradeOrders(ship_lua) end
    if ammo == 'deployable' then return tonumber(C.GetFreeDeployableStorageAfterTradeOrders(ship)) end
end

local function can_trade(ffi, offer, ship, side)
    local allowed
    if side == 'buy' then
        allowed = CanTradeWith(offer.id, ship, offer.minamount, true)
    else
        allowed = CanTradeWith(offer.id, ship, offer.minamount)
    end
    if ffi.istype('bool', allowed) then return tonumber(allowed) == 1 end
    if allowed == nil or type(allowed) == 'boolean' then return allowed == true end
    error('invalid stock trade permission')
end

local function stock_rows(ffi, ship, station)
    local C = ffi.C
    local ship_lua = assert(ConvertStringToLuaID(tostring(ship)))
    local station_lua = assert(ConvertStringToLuaID(tostring(station)))
    local cargo = GetCargoAfterTradeOrders(ship_lua)
    local offers = GetTradeList(station_lua, ship_lua)
    if type(cargo) ~= 'table' or type(offers) ~= 'table' then return nil, 'invalid_observation' end
    local ammo, rows, space, inspected = nil, {}, {}, 0
    for _, offer in pairs(offers) do
        inspected = inspected + 1
        if inspected > max_rows then return nil, 'market_limit' end
        if type(offer) ~= 'table' then return nil, 'invalid_observation' end
        if not offer.ismissionoffer and not offer.stale
                and (offer.isselloffer == true or offer.isbuyoffer == true) then
            local ware = offer.ware
            local price = cents(offer.price)
            local amount, minimum = offer.amount, offer.minamount
            if type(ware) ~= 'string' or #ware > 128 or not ware:match('^[a-z][a-z0-9_]*$')
                    or not price or not integer(amount, 0, max_integer)
                    or not integer(minimum, 0, max_integer) or not offer.id
                    or (offer.isselloffer == true and offer.isbuyoffer == true) then
                return nil, 'invalid_observation'
            end
            local detail = GetTradeData(offer.id)
            if type(detail) ~= 'table' or not detail.station or ConvertIDTo64Bit(detail.station) ~= station then
                return nil, 'invalid_observation'
            end
            local available = cargo[ware]
            if offer.ammotypename then
                if not ammo then ammo = GetAmmoCountAfterTradeOrders(ship_lua) end
                if type(ammo) ~= 'table' then return nil, 'invalid_observation' end
                available = ammo[ware]
            end
            if available == nil then available = 0 end
            local space_key = ware .. ':' .. (offer.ammotypename or '')
            if space[space_key] == nil then
                space[space_key] = free_units(C, ship, ship_lua, ware, offer.ammotypename)
            end
            local free = space[space_key]
            if not integer(available, 0, max_integer) or not integer(free, 0, max_integer) then
                return nil, 'invalid_observation'
            end
            local side = offer.isselloffer == true and 'buy' or 'sell'
            local tradeable = (side == 'buy' or available >= math.max(1, minimum))
                and can_trade(ffi, offer, ship_lua, side)
            rows[#rows + 1] = table.concat({side, ware, price, amount, minimum,
                available, free, tradeable and '1' or '0'}, ',')
        end
    end
    -- Keep all same-ware offers. Order is stable within the cached observation.
    table.sort(rows)
    return rows
end

function M.new(lease, net, monotonic)
    local ffi = require('ffi')
    local resolve = assert(X4LAN_RESOLVE_STRING)
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[
        uint32_t GetFreeMissileStorageAfterTradeOrders(UniverseID);
        uint32_t GetFreeCountermeasureStorageAfterTradeOrders(UniverseID);
        uint32_t GetFreeDeployableStorageAfterTradeOrders(UniverseID);
    ]]
    local active, next_hello, serial = nil, nil, 0

    local function send(kind, fields)
        net:send('MARKET_' .. kind .. '|' .. lease .. '|' .. active.id .. '|' .. fields)
    end

    local function header()
        if active.reason then
            send('FAIL', active.reason)
        elseif active.rows then
            send('HEADER', ('%.0f|%d'):format(active.balance, #active.rows))
        end
    end

    local function reject(reason)
        active.reason = reason
        header()
    end

    assert(RegisterEvent(screen .. '.Result', function(_, row)
        if not active or active.reason or active.rows or type(row) ~= 'string' or #row > max_packet then return end
        local id, status = row:match('^([a-f0-9]+)|([a-z_]+)')
        if id ~= active.id then return end
        if status == 'invalid_registry' or status == 'dock_mismatch' or status == 'account_unavailable' then
            if row == id .. '|' .. status then reject(status) end
            return
        end
        local ship_hex, station_hex, balance_text = row:match('^[a-f0-9]+|observed|([^|]+)|([^|]+)|(%d+)$')
        local ship = resolve.parse_hex(ship_hex)
        local station = resolve.parse_hex(station_hex)
        local balance = exact_text(balance_text, 0, max_exact)
        if not ship or not station or not balance then reject('invalid_observation'); return end
        local ok, rows, reason = pcall(stock_rows, ffi, ship, station)
        if not ok or not rows then reject(reason or 'invalid_observation'); return end
        active.rows, active.balance = rows, balance
        header()
    end) ~= false)

    local function open(packet)
        local wire_lease, id, number, epoch, index, code, sector, station = packet:match(
            '^MARKET_OPEN|([a-f0-9]+)|([a-f0-9]+)|(%d+)|([a-f0-9]+)|(%d+)|([^|]+)|([A-Za-z0-9_]+)|([^|]+)$')
        if wire_lease ~= lease or #id ~= 32 or #epoch ~= 32 or #sector > 128
                or not code:match(component_pattern) or not station:match(component_pattern) then return end
        number = exact_text(number, 1, max_integer)
        index = exact_text(index, 1, max_integer)
        if not number or not index or number < serial then return end
        if number == serial then
            if active and packet == active.open then header() end
            return
        end
        serial = number
        active = {id = id, open = packet}
        local ok, sent = pcall(AddUITriggeredEvent, screen, 'Read', {
            nonce = id, epoch = epoch, index = index, ship_code = code, sector = sector, station_code = station,
        })
        if (not ok or sent == false) and not active.rows and not active.reason then
            reject('invalid_observation')
        end
    end

    local function read(packet)
        local wire_lease, id, offset = packet:match('^MARKET_READ|([a-f0-9]+)|([a-f0-9]+)|(%d+)$')
        if wire_lease ~= lease or not active or id ~= active.id or not active.rows then return end
        offset = exact_text(offset, 0, #active.rows - 1)
        if not offset then return end
        local page, total = {tostring(offset)}, #'MARKET_PAGE|||' + #lease + #id + #tostring(offset)
        for index = offset + 1, math.min(offset + max_page, #active.rows) do
            local row = active.rows[index]
            total = total + #row + 1
            if total > max_packet then break end
            page[#page + 1] = row
        end
        send('PAGE', table.concat(page, '|'))
    end

    return function(paused)
        local now = monotonic()
        if not next_hello or now >= next_hello then
            net:send('MARKET_HELLO|' .. lease)
            next_hello = now + .5
        end
        if paused or not net:pending() then return end
        local packet = net:receive()
        if type(packet) ~= 'string' or #packet > max_packet then return end
        if packet:sub(1, 12) == 'MARKET_OPEN|' then open(packet) else read(packet) end
    end
end

X4LAN_AUTHORITY_NATIVE_MARKET_SURVEY = M
return M
