-- Opt-in owner of a single docked purchase through the stock MapMenu.
local M = {}
local max_integer = 2147483647
local max_exact_lua_integer = 9007199254740991

local function positive_integer(n)
    return type(n) == 'number' and n >= 1 and n <= max_integer and n % 1 == 0
end

local function exact_cents(total)
    return type(total) == 'number' and total >= 0 and total <= max_exact_lua_integer and total % 1 == 0
end

local function cents(price)
    if type(price) ~= 'number' or not (price > 0 and price <= max_integer / 100) then return end
    local scaled = price * 100
    local exact = math.floor(scaled + 0.5)
    if positive_integer(exact) and math.abs(exact - scaled) <= 1e-7 then return exact end
end

local function stock_order(menu, data, o, station)
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
            or o.offer_station(offer_id) ~= station or offer.ismissionoffer or offer.isbuyoffer
            or offer.isselloffer ~= true or offer.active ~= true or offer.stale == true then
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

local function resulting_snapshot(intent, receipt, ship_code)
    if type(ship_code) ~= 'string' or not ship_code:match('^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$')
            or receipt.ship_code ~= ship_code or not exact_cents(receipt.company_balance_cents)
            or not positive_integer(receipt.cargo_units) or receipt.cargo_units < intent.quantity
            or not intent.ware:match('^[a-zA-Z0-9_]+$') then return end
    return {company_balance_cents = receipt.company_balance_cents, ship_code = ship_code,
        ware = intent.ware, cargo_units = receipt.cargo_units}
end

function M.install(o)
    if o.enabled ~= true then return nil, 'disabled' end
    local menu = o.menu
    local dynamic_station = o.dynamic_station == true
    if type(menu) ~= 'table' or menu.name ~= 'MapMenu' or type(menu.getTradeOfferByID) ~= 'function'
            or type(menu.buttonConfirmTrade) ~= 'function' or o.ship == nil
            or (not dynamic_station and (o.station == nil or type(o.sector) ~= 'string' or o.sector == ''
                or type(o.station_code) ~= 'string' or o.station_code == '')) then
        return nil, 'unavailable'
    end
    for _, name in ipairs({'identity', 'read_dock', 'decode_offer_id', 'offer_station', 'request', 'notice'}) do
        if type(o[name]) ~= 'function' then return nil, 'unavailable' end
    end
    local original = menu.buttonConfirmTrade
    local phase = 'ready'
    local active_attempt, price_retry

    local function show_price_change(previous, current, reopen)
        phase = 'reporting_price_change'
        o.notice('refused', nil, {reason = 'price_changed', price_cents = previous,
            current_price_cents = current, reopen = reopen})
        phase = 'ready'
    end

    local function confirm(...)
        local data = menu.contextMenuData
        if type(data) ~= 'table' then return original(...) end
        local ship, station = o.identity(data.currentShip), o.identity(data.component)
        if (ship == o.ship and station == nil) or (not dynamic_station and station == o.station and ship == nil)
                or (dynamic_station and ship == nil) then
            o.notice('stale_identity')
            return
        end
        if ship ~= o.ship or (not dynamic_station and station ~= o.station) then return original(...) end
        if phase ~= 'ready' then
            o.notice('already_attempted')
            return
        end
        if price_retry and (data == price_retry.rejected_context or data.orders == price_retry.rejected_orders) then
            o.notice('fresh_offer_required')
            return
        end
        if data.immediate ~= true or o.identity(data.immediateObject) ~= ship then
            o.notice('immediate_trade_required')
            return
        end
        local dock_ok, dock = pcall(o.read_dock, data.currentShip, data.component)
        if not dock_ok or type(dock) ~= 'table' or dock.ship ~= ship or dock.station ~= station
                or type(dock.sector) ~= 'string' or dock.sector == ''
                or type(dock.station_code) ~= 'string' or dock.station_code == ''
                or (not dynamic_station and (dock.sector ~= o.sector or dock.station_code ~= o.station_code)) then
            o.notice('stale_dock')
            return
        end
        local ok, intent, why = pcall(stock_order, menu, data, o, station)
        if not ok or not intent then
            o.notice(ok and why or 'stale_offer')
            return
        end
        intent.sector, intent.station_code = dock.sector, dock.station_code
        if price_retry and intent.ware == price_retry.ware and intent.station_code == price_retry.station_code
                and intent.sector == price_retry.sector and intent.price_cents ~= price_retry.current_price then
            local previous = price_retry.current_price
            price_retry.current_price = intent.price_cents
            show_price_change(previous, intent.price_cents, false)
            return
        end

        local attempt = {}
        local submitted_orders = data.orders
        active_attempt, price_retry, phase = attempt, nil, 'dispatching'
        local answered, early_receipt
        local function settle(receipt)
            if active_attempt ~= attempt then return end
            if phase == 'dispatching' then
                if not answered then answered, early_receipt = true, receipt end
                return
            end
            if phase ~= 'pending' then return end
            active_attempt, phase = nil, 'finished'
            if type(receipt) == 'table' and receipt.outcome == 'confirmed' and matches(intent, receipt)
                    and exact_cents(receipt.settled_total_cents) and receipt.settled_total_cents > 0 then
                local snapshot = resulting_snapshot(intent, receipt, o.ship_code)
                if snapshot then
                    o.notice('confirmed', receipt.settled_total_cents, snapshot)
                else
                    o.notice('confirmed', receipt.settled_total_cents)
                end
            elseif type(receipt) == 'table' and receipt.outcome == 'refused' and matches(intent, receipt) then
                local price = receipt.current_price_cents
                if receipt.reason == 'price_changed' and positive_integer(price) and price ~= intent.price_cents then
                    price_retry = {rejected_context = data, rejected_orders = submitted_orders,
                        ware = intent.ware, sector = intent.sector, station_code = intent.station_code,
                        current_price = price}
                    show_price_change(intent.price_cents, price, true)
                else
                    o.notice('refused')
                end
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
            active_attempt, phase = nil, 'finished'
            o.notice(sent and accepted == false and not answered and 'refused' or 'uncertain')
        end
    end

    menu.buttonConfirmTrade = confirm
    return {
        detach = function()
            if phase == 'dispatching' or phase == 'pending' or phase == 'reporting_price_change'
                    or menu.buttonConfirmTrade ~= confirm then return false end
            menu.buttonConfirmTrade = original
            return true
        end,
    }
end

local trade_notices = {
    pending = 'Company purchase awaiting confirmation.',
    refused = 'Company purchase refused.',
    uncertain = 'Company purchase status uncertain. Check the authoritative receipt.',
    already_attempted = 'Company purchase already submitted.',
    stale_identity = 'Company purchase refused: ship or station changed.',
    stale_dock = 'Company purchase refused: confirm the current dock.',
    immediate_trade_required = 'Company purchase requires an immediate trade.',
    unsupported_trade = 'Company purchase does not support this trade.',
    single_buy_required = 'Company purchase requires a single buy.',
    stale_offer = 'Company purchase refused: offer changed.',
    fresh_offer_required = 'Reopen the stock trade offer and select the item again.',
    ambiguous_price = 'Company purchase refused: invalid quote.',
}

local function format_credits(cents)
    local remainder = cents % 100
    local credits = (cents - remainder) / 100
    local amount = string.format('%d', credits)
    if remainder ~= 0 then amount = amount .. string.format('.%02d', remainder) end
    return amount .. ' Cr'
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
        void ShowInfoLine(const char*,uint32_t);
        uint32_t GetNumDockedShips(UniverseID,const char*);
        uint32_t GetDockedShips(UniverseID*,uint32_t,UniverseID,const char*);
    ]]
    local C = ffi.C
    local function id(ref)
        if ref == nil then return end
        if ffi.istype('UniverseID', ref) then
            if ref ~= 0 then return ref end
            return
        end
        local ok, value = pcall(ConvertIDTo64Bit, ref)
        if ok and value and value ~= 0 then return value end
    end
    local function lua_id(ref)
        return ConvertStringToLuaID(tostring(ref))
    end
    local ship, expected_station = id(o.ship), id(o.station)
    local dynamic_station = o.dynamic_station == true
    local ship_code
    if ship and C.IsComponentClass(ship, 'ship') then
        local native_ship_code = C.GetObjectIDCode(ship)
        if native_ship_code ~= nil then ship_code = ffi.string(native_ship_code) end
    end
    local function live_dock(ship_ref, station_ref)
        local station = id(station_ref)
        if id(ship_ref) ~= ship or not station or (not dynamic_station and station ~= expected_station)
                or C.GetPlayerControlledShipID() ~= ship
                or not C.IsComponentClass(ship, 'ship') or not C.IsComponentOperational(ship)
                or not C.IsComponentClass(station, 'station') or not C.IsComponentOperational(station) then return end
        local ship_id, station_id = lua_id(ship), lua_id(station)
        if not ship_id or not station_id then return end
        local owner, docked, stored = GetComponentData(ship_id, 'owner', 'isdocked', 'isinternallystored')
        -- Stock can keep isdocking true after a ship reaches the trading bay.
        if owner ~= 'player' or docked ~= true or stored ~= false then return end
        local station_owner = GetComponentData(station_id, 'owner')
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
                local sector_id = lua_id(sector)
                if not sector_id then return end
                return {ship = ship, station = station, sector = GetComponentData(sector_id, 'macro'),
                    station_code = ffi.string(code)}
            end
        end
    end
    local function stock_notice(status, settled_total_cents, details)
        local text = trade_notices[status]
        if status == 'confirmed' then
            text = 'Company purchase confirmed. Paid ' .. format_credits(settled_total_cents) .. '.'
            if details then
                text = text .. ' Company balance ' .. format_credits(details.company_balance_cents)
                    .. '. ' .. details.ship_code .. ': ' .. details.cargo_units .. ' ' .. details.ware .. '.'
            end
        elseif status == 'refused' and details then
            text = 'Company purchase refused. Unit price changed from ' .. format_credits(details.price_cents)
                .. ' to ' .. format_credits(details.current_price_cents) .. '.'
            if details.reopen then
                text = text .. ' Reopen the stock offer and confirm the new price.'
            else
                text = text .. ' Review the updated price and confirm again.'
            end
        end
        if text then C.ShowInfoLine(text, 4) end
    end
    return M.install({
        enabled = true, menu = o.menu, ship = ship, station = expected_station, sector = o.sector,
        dynamic_station = dynamic_station,
        station_code = o.station_code, ship_code = ship_code, request = o.request, notice = o.notice or stock_notice,
        identity = id, read_dock = live_dock, decode_offer_id = ConvertStringToLuaID,
        offer_station = function(offer_id)
            local data = GetTradeData(offer_id)
            return type(data) == 'table' and id(data.station)
        end,
    })
end

X4LAN_COMPANY_TRADE_CAPTURE = M
return M
