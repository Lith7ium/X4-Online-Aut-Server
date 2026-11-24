-- Opt-in owner of a single docked purchase through the stock MapMenu.
local M = {}
local max_integer = 2147483647

local function positive_integer(n)
    return type(n) == 'number' and n >= 1 and n <= max_integer and n % 1 == 0
end

local function cents(price)
    if type(price) ~= 'number' or not (price > 0 and price <= max_integer / 100) then return end
    local scaled = price * 100
    local exact = math.floor(scaled + 0.5)
    if positive_integer(exact) and math.abs(exact - scaled) <= 1e-7 then return exact end
end

local function stock_order(menu, data, o)
    if data.wareexchange or type(data.orders) ~= 'table' then return nil, 'unsupported_trade' end
    local id, quantity
    for key, amount in pairs(data.orders) do
        if amount ~= 0 then
            if id ~= nil or type(amount) ~= 'number' or not positive_integer(-amount) then
                return nil, 'single_buy_required'
            end
            id, quantity = key, -amount
        end
    end
    if id == nil then return nil, 'single_buy_required' end

    local offer_id = o.decode_offer_id(tostring(id))
    local offer = offer_id and menu.getTradeOfferByID(offer_id)
    if type(offer) ~= 'table' or type(offer.ware) ~= 'string' or offer.ware == ''
            or o.offer_station(offer_id) ~= o.station or offer.ismissionoffer or offer.isbuyoffer then
        return nil, 'stale_offer'
    end
    local price_cents = cents(offer.price)
    if not price_cents then return nil, 'ambiguous_price' end
    return {ware = offer.ware, quantity = quantity, price_cents = price_cents}
end

local function matches(intent, receipt)
    return receipt.sector == intent.sector and receipt.station_code == intent.station_code
        and receipt.ware == intent.ware and receipt.quantity == intent.quantity
        and receipt.price_cents == intent.price_cents
end

function M.install(o)
    if o.enabled ~= true then return nil, 'disabled' end
    local menu = o.menu
    if type(menu) ~= 'table' or menu.name ~= 'MapMenu' or type(menu.getTradeOfferByID) ~= 'function'
            or type(menu.buttonConfirmTrade) ~= 'function' or o.ship == nil or o.station == nil
            or type(o.sector) ~= 'string' or o.sector == ''
            or type(o.station_code) ~= 'string' or o.station_code == '' then
        return nil, 'unavailable'
    end
    for _, name in ipairs({'identity', 'read_dock', 'decode_offer_id', 'offer_station', 'request', 'notice'}) do
        if type(o[name]) ~= 'function' then return nil, 'unavailable' end
    end
    local original = menu.buttonConfirmTrade
    local phase = 'ready'

    local function confirm(...)
        local data = menu.contextMenuData
        if type(data) ~= 'table' then return original(...) end
        local ship, station = o.identity(data.currentShip), o.identity(data.component)
        if (ship == o.ship and station == nil) or (station == o.station and ship == nil) then
            o.notice('stale_identity')
            return
        end
        if ship ~= o.ship or station ~= o.station then return original(...) end
        if phase ~= 'ready' then
            o.notice('already_attempted')
            return
        end
        if data.immediate ~= true or o.identity(data.immediateObject) ~= ship then
            o.notice('immediate_trade_required')
            return
        end
        local dock_ok, dock = pcall(o.read_dock, data.currentShip, data.component)
        if not dock_ok or type(dock) ~= 'table' or dock.ship ~= ship or dock.station ~= station
                or dock.sector ~= o.sector or dock.station_code ~= o.station_code then
            o.notice('stale_dock')
            return
        end
        local ok, intent, why = pcall(stock_order, menu, data, o)
        if not ok or not intent then
            o.notice(ok and why or 'stale_offer')
            return
        end
        intent.sector, intent.station_code = dock.sector, dock.station_code

        phase = 'dispatching'
        local answered, early_receipt
        local function settle(receipt)
            if phase == 'dispatching' then
                if not answered then answered, early_receipt = true, receipt end
                return
            end
            if phase ~= 'pending' then return end
            phase = 'finished'
            if type(receipt) == 'table' and receipt.outcome == 'confirmed' and matches(intent, receipt) then
                o.notice('confirmed')
            elseif type(receipt) == 'table' and receipt.outcome == 'refused' and matches(intent, receipt) then
                o.notice('refused')
            else
                o.notice('uncertain')
            end
        end

        local sent, accepted = pcall(o.request, {
            sector = intent.sector, station_code = intent.station_code, ware = intent.ware,
            quantity = intent.quantity, price_cents = intent.price_cents,
        }, settle)
        if sent and accepted == true then
            phase = 'pending'
            if answered then settle(early_receipt) else o.notice('pending') end
        else
            phase = 'finished'
            o.notice(sent and accepted == false and not answered and 'refused' or 'uncertain')
        end
    end

    menu.buttonConfirmTrade = confirm
    return {
        detach = function()
            if phase == 'dispatching' or phase == 'pending' or menu.buttonConfirmTrade ~= confirm then return false end
            menu.buttonConfirmTrade = original
            return true
        end,
    }
end

function M.install_x4(o)
    if o.enabled ~= true then return nil, 'disabled' end
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[
        bool IsComponentClass(UniverseID,const char*);
        bool IsComponentOperational(UniverseID);
        UniverseID GetPlayerControlledShipID(void);
        UniverseID GetContextByClass(UniverseID,const char*,bool);
        const char* GetObjectIDCode(UniverseID);
        uint32_t GetNumDockedShips(UniverseID,const char*);
        uint32_t GetDockedShips(UniverseID*,uint32_t,UniverseID,const char*);
    ]]
    local C = ffi.C
    local function id(ref)
        if ref == nil then return end
        local ok, value = pcall(ConvertIDTo64Bit, ref)
        if ok and value and value ~= 0 then return value end
    end
    local ship, station = id(o.ship), id(o.station)
    local function live_dock(ship_ref, station_ref)
        if id(ship_ref) ~= ship or id(station_ref) ~= station or C.GetPlayerControlledShipID() ~= ship
                or not C.IsComponentClass(ship, 'ship') or not C.IsComponentOperational(ship)
                or not C.IsComponentClass(station, 'station') or not C.IsComponentOperational(station) then return end
        local owner, docked, docking, stored = GetComponentData(ship_ref, 'owner', 'isdocked', 'isdocking', 'isinternallystored')
        if owner ~= 'player' or docked ~= true or docking ~= false or stored ~= false then return end
        local station_owner = GetComponentData(station_ref, 'owner')
        if not station_owner or station_owner == 'player' then return end
        local count = tonumber(C.GetNumDockedShips(station, nil))
        if not count or count < 1 or count > 4096 then return end
        local members = ffi.new('UniverseID[?]', count)
        if tonumber(C.GetDockedShips(members, count, station, nil)) ~= count then return end
        for i = 0, count - 1 do
            if members[i] == ship then
                local sector = C.GetContextByClass(station, 'sector', false)
                local code = C.GetObjectIDCode(station)
                if sector == 0 or code == nil then return end
                local sector_id = ConvertStringToLuaID(tostring(sector):gsub('ULL$', ''))
                return {ship = ship, station = station, sector = GetComponentData(sector_id, 'macro'),
                    station_code = ffi.string(code)}
            end
        end
    end
    return M.install({
        enabled = true, menu = o.menu, ship = ship, station = station, sector = o.sector,
        station_code = o.station_code, request = o.request, notice = o.notice,
        identity = id, read_dock = live_dock, decode_offer_id = ConvertStringToLuaID,
        offer_station = function(offer_id)
            local data = GetTradeData(offer_id)
            return type(data) == 'table' and id(data.station)
        end,
    })
end

X4LAN_COMPANY_TRADE_CAPTURE = M
return M
