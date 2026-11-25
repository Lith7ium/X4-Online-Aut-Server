-- Opt-in owner of docked Company trades through the stock MapMenu.
local M = {}
local max_integer = 2147483647
local max_exact_lua_integer = 9007199254740991
-- Each of these replies identifies a booking the provider declined before execution.
local refusal_notices = {
    captain_required = 'Company purchase refused. Assign a Company NPC pilot to this ship on the server.',
    dock_mismatch = 'The server could not confirm this ship at a trading berth on the selected station. Reopen the offer after docking.',
    provider_busy = 'The server is busy with another purchase. Reopen the offer to try again.',
    no_unique_offer = 'The server could not match this offer at the station. Reopen trade to see current offers.',
    not_tradeable = 'The server could not book that quantity. Reopen trade to check stock, funds and cargo space.',
}

local function positive_integer(n)
    return type(n) == 'number' and n >= 1 and n <= max_integer and n % 1 == 0
end

local function exact_cents(total)
    return type(total) == 'number' and total >= 0 and total <= max_exact_lua_integer and total % 1 == 0
end

local function cents(price, maximum)
    maximum = maximum or max_integer
    if type(price) ~= 'number' or not (price > 0 and price <= maximum / 100) then return end
    local scaled = price * 100
    local exact = math.floor(scaled + 0.5)
    if exact >= 1 and exact <= maximum and math.abs(exact - scaled) <= 1e-7 then return exact end
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
    local request_sale = dynamic_station and o.market_ui and type(o.request_sale) == 'function' and o.request_sale
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
    local completed_contexts = setmetatable({}, {__mode = 'k'})
    local completed_orders = setmetatable({}, {__mode = 'k'})

    local function show_price_change(previous, current, reopen)
        phase = 'reporting_price_change'
        o.notice('refused', nil, {reason = 'price_changed', price_cents = previous,
            current_price_cents = current, reopen = reopen})
        phase = 'ready'
    end

    local function dispatch_sale(data, intent, displayed_total)
        -- The sale transport returns the accepted transaction ID before any
        -- receipt can be trusted. Inline receipts are held until then.
        if not request_sale or not exact_cents(intent.price_cents) or intent.price_cents <= 0
                or not positive_integer(intent.quantity) or type(o.ship_code) ~= 'string'
                or not o.ship_code:match('^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$') then
            o.notice('sale_refused')
            return
        end
        local attempt, orders = {}, data.orders
        active_attempt, phase = attempt, 'dispatching'
        local sale_id, answered, early_receipt
        local function settle(receipt)
            if active_attempt ~= attempt then return end
            if phase == 'dispatching' then
                if not answered then answered, early_receipt = true, receipt end
                return
            end
            active_attempt, phase = nil, 'finished'
            if type(receipt) ~= 'table' or receipt.sale_id ~= sale_id
                    or receipt.ship_code ~= o.ship_code or not matches(intent, receipt) then
                o.notice('sale_uncertain')
                return
            end
            if receipt.outcome == 'confirmed' and exact_cents(receipt.credited_cents) and receipt.credited_cents > 0
                    and exact_cents(receipt.company_balance_cents) and exact_cents(receipt.cargo_units)
                    and receipt.cargo_units <= max_integer then
                phase = 'ready'
                o.notice('sale_confirmed', receipt.credited_cents, {company_balance_cents = receipt.company_balance_cents,
                    ship_code = receipt.ship_code, ware = intent.ware, cargo_units = receipt.cargo_units})
            elseif receipt.outcome == 'refused' then
                phase = 'ready'
                local price = receipt.current_price_cents
                if receipt.reason == 'price_changed' and positive_integer(price) and price ~= intent.price_cents then
                    o.notice('sale_refused', nil, {price_cents=intent.price_cents, current_price_cents=price})
                else
                    o.notice('sale_refused')
                end
            else
                o.notice('sale_uncertain')
            end
        end
        local sent, accepted, id = pcall(request_sale, {
            sector = intent.sector, station_code = intent.station_code, ware = intent.ware,
            quantity = intent.quantity, price_cents = intent.price_cents, ship_code = o.ship_code,
        }, settle, displayed_total)
        completed_contexts[data], completed_orders[orders] = true, true
        o.market_ui:consume(data)
        if sent and accepted == true and type(id) == 'string' and #id == 32 and id:match('^[0-9a-f]+$') then
            sale_id, phase = id, 'pending'
            if answered then settle(early_receipt) else o.notice('sale_pending') end
        else
            active_attempt, phase = nil, 'finished'
            if sent and accepted == false and not answered then
                phase = 'ready'
                o.notice('sale_refused')
            else
                o.notice('sale_uncertain')
            end
        end
    end

    local function confirm(...)
        local data = menu.contextMenuData
        if type(data) ~= 'table' then
            if dynamic_station then o.notice('stale_identity'); return end
            return original(...)
        end
        local ship, station = o.identity(data.currentShip), o.identity(data.component)
        if (ship == o.ship and station == nil) or (not dynamic_station and station == o.station and ship == nil)
                or (dynamic_station and ship == nil) then
            o.notice('stale_identity')
            return
        end
        if ship ~= o.ship or (not dynamic_station and station ~= o.station) then
            if request_sale and type(data.orders) == 'table' then
                for id, quantity in pairs(data.orders) do
                    if type(id) == 'number' and id < 0 and type(quantity) == 'number' and quantity > 0 then
                        o.notice('stale_identity')
                        return
                    end
                end
            end
            return original(...)
        end
        if phase ~= 'ready' or completed_contexts[data] or completed_orders[data.orders] then
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
        local ok, intent, why, market_total
        if o.market_ui then
            ok, intent, why, market_total = pcall(o.market_ui.order, o.market_ui, data)
        else
            ok, intent, why = pcall(stock_order, menu, data, o, station)
        end
        if not ok or not intent then
            o.notice(ok and why or 'stale_offer')
            return
        end
        intent.sector, intent.station_code = dock.sector, dock.station_code
        if intent.side == 'sell' then
            dispatch_sale(data, intent, market_total)
            return
        end
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
        local answered, early_receipt, awaiting_statement
        local displayed_total = market_total
            or (dynamic_station and cents(data.totalsellcost, max_exact_lua_integer)) or nil
        local function settle(receipt)
            if active_attempt ~= attempt then return end
            if phase == 'dispatching' then
                if not answered then answered, early_receipt = true, receipt end
                return
            end
            if phase ~= 'pending' then return end
            if dynamic_station and type(receipt) == 'table' and receipt.outcome == 'accepted_pending_statement'
                    and matches(intent, receipt) then
                if not awaiting_statement then
                    awaiting_statement = true
                    o.notice('accepted_pending_statement')
                end
                return
            end
            active_attempt, phase = nil, 'finished'
            if type(receipt) == 'table' and receipt.outcome == 'confirmed' and matches(intent, receipt)
                    and exact_cents(receipt.settled_total_cents) and receipt.settled_total_cents > 0 then
                local snapshot = resulting_snapshot(intent, receipt, o.ship_code)
                if dynamic_station and not snapshot then
                    o.notice('uncertain')
                    return
                end
                if dynamic_station then
                    completed_contexts[data], completed_orders[submitted_orders] = true, true
                end
                if snapshot then
                    o.notice('confirmed', receipt.settled_total_cents, snapshot)
                else
                    o.notice('confirmed', receipt.settled_total_cents)
                end
                if dynamic_station then phase = 'ready' end
            elseif dynamic_station and type(receipt) == 'table' and receipt.outcome == 'pilot_reported'
                    and matches(intent, receipt) and displayed_total
                    and receipt.settled_total_cents == displayed_total then
                completed_contexts[data], completed_orders[submitted_orders] = true, true
                o.notice('pilot_reported', displayed_total)
                phase = 'ready'
            elseif type(receipt) == 'table' and receipt.outcome == 'refused' and matches(intent, receipt) then
                local price = receipt.current_price_cents
                if receipt.reason == 'price_changed' and positive_integer(price) and price ~= intent.price_cents then
                    price_retry = {rejected_context = data, rejected_orders = submitted_orders,
                        ware = intent.ware, sector = intent.sector, station_code = intent.station_code,
                        current_price = price}
                    if dynamic_station then
                        completed_contexts[data], completed_orders[submitted_orders] = true, true
                    end
                    show_price_change(intent.price_cents, price, true)
                elseif refusal_notices[receipt.reason] then
                    if dynamic_station then
                        completed_contexts[data], completed_orders[submitted_orders] = true, true
                    end
                    o.notice(receipt.reason)
                    if dynamic_station then phase = 'ready' end
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
        }, settle, displayed_total)
        if sent and accepted == true then
            if o.market_ui then o.market_ui:consume(data) end
            phase = 'pending'
            if answered then settle(early_receipt) else o.notice('pending') end
        else
            active_attempt, phase = nil, 'finished'
            o.notice(sent and accepted == false and not answered and 'refused' or 'uncertain')
        end
    end

    menu.buttonConfirmTrade = confirm
    return {
        market_ui = o.market_ui,
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
    sale_pending = 'Company sale awaiting confirmation.',
    sale_refused = 'Company sale refused. Reopen trade for a fresh offer.',
    sale_uncertain = 'Company sale status uncertain. Check the authoritative receipt.',
    single_trade_required = 'Company trade requires one order.',
    accepted_pending_statement = 'Company purchase accepted. Awaiting reconciliation with your purchase record.',
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

function M.show_recovered_purchase(notice)
    local text = 'Earlier Company purchase recorded from your report. '
        .. notice.quantity .. ' ' .. notice.ware .. ' at ' .. notice.station_code .. ' for '
        .. format_credits(notice.settled_total_cents) .. '.'
    require('ffi').C.ShowInfoLine(text, 4)
    return true
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
    if dynamic_station and (not ship_code or not ship_code:match('^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$')) then
        return nil, 'unavailable'
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
        local text = trade_notices[status] or refusal_notices[status]
        if status == 'confirmed' or status == 'sale_confirmed' then
            text = (status == 'sale_confirmed' and 'Company sale confirmed. Credited ' or 'Company purchase confirmed. Paid ')
                .. format_credits(settled_total_cents) .. '.'
            if details then
                text = text .. ' Company balance ' .. format_credits(details.company_balance_cents)
                    .. '. ' .. details.ship_code .. ': ' .. details.cargo_units .. ' ' .. details.ware .. '.'
            end
        elseif status == 'pilot_reported' then
            text = 'Company purchase recorded from your report: ' .. format_credits(settled_total_cents) .. '.'
        elseif status == 'sale_refused' and details then
            text = 'Company sale refused. Unit price changed from ' .. format_credits(details.price_cents)
                .. ' to ' .. format_credits(details.current_price_cents)
                .. '. Reopen the stock offer and confirm the new price.'
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
    local market_ui
    if o.market then
        market_ui = X4LAN_COMPANY_TRADE_VIEW_UI.install({
            menu = o.menu, ship = ship, identity = id, read_dock = live_dock, market = o.market,
            request_sale = dynamic_station and o.request_sale,
        })
        if not market_ui then return nil, 'unavailable' end
    end
    return M.install({
        enabled = true, menu = o.menu, ship = ship, station = expected_station, sector = o.sector,
        market_ui = market_ui,
        dynamic_station = dynamic_station,
        station_code = o.station_code, ship_code = ship_code, request = o.request, request_sale = o.request_sale,
        notice = o.notice or stock_notice,
        identity = id, read_dock = live_dock, decode_offer_id = ConvertStringToLuaID,
        offer_station = function(offer_id)
            local data = GetTradeData(offer_id)
            return type(data) == 'table' and id(data.station)
        end,
    })
end

X4LAN_COMPANY_TRADE_CAPTURE = M
return M
