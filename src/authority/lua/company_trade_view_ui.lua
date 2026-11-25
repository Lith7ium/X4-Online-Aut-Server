-- Feed the stock MapMenu trade initializer, renderer and controls with Company data.
local M = {}
local max_order_integer = 2147483647

local function pack(...)
    return {n = select('#', ...), ...}
end

-- Override only the stock function's own lookup environment. Other game menus
-- keep their globals and no synthetic offer ID can reach a native operation.
local function scoped(fn, replacements, ...)
    local original = getfenv(fn)
    setfenv(fn, setmetatable(replacements, {__index = original}))
    local result = pack(pcall(fn, ...))
    setfenv(fn, original)
    if not result[1] then error(result[2], 0) end
    return unpack(result, 2, result.n)
end

local function credits(cents)
    local remainder = cents % 100
    return string.format('%.0f.%02d Cr', (cents - remainder) / 100, remainder)
end

local function quote_cents(total)
    local rounded = math.floor(total * 100 + 0.5)
    if rounded >= 1 and rounded <= 9007199254740991 then return rounded end
end

local function quotes(view)
    local cargo, ammo, offers, by_id = {}, {}, {}, {}
    if not view or view.status ~= 'observed' then return cargo, ammo, offers, by_id end
    for i, value in ipairs(view.rows) do
        -- Stock TradeIDs are unsigned. Negative numeric IDs exist only in the
        -- scoped Lua environment for this view and cannot match another trade.
        local offer = {id = -(view.serial * 4096 + i), ware = value.ware, price = value.price_cents / 100,
            -- The server reports the effective unit price, with no separate quantity adjustment.
            quantityfactor = 1, price_cents = value.price_cents, amount = value.amount,
            minamount = value.min_amount,
            desiredamount = value.amount, free = value.free_units, tradeable = value.tradeable,
            isselloffer = value.side == 'buy', isbuyoffer = value.side == 'sell'}
        local ammotypename = view.get_ammo_type(value.ware)
        offer.ammotypename = ammotypename
        local storage = ammotypename and ammo or cargo
        storage[offer.ware] = value.cargo_units
        offers[#offers + 1], by_id[offer.id] = offer, offer
    end
    return cargo, ammo, offers, by_id
end

function M.install(o)
    local menu, market = o.menu, o.market
    local native = {}
    for _, name in ipairs({'initTradeContextData', 'createTradeContext', 'getTradeContextRowContent',
            'getTradeContextStorableAmountAfterTradeOrders', 'getTradeOfferByID', 'updateTradeCost',
            'getCargoOrderAmountByWare', 'getAmmoOrderAmountByWare', 'slidercellShipCargo',
            'slidercellShipAmmo', 'closeContextMenu'}) do
        native[name] = menu[name]
        if type(native[name]) ~= 'function' then return end
    end
    local active
    local api = {}

    local function clear()
        if active then market:close(); active = nil end
    end

    local function owns(c)
        return c and active == c and menu.contextMenuMode == 'trade' and menu.contextMenuData == c.data
    end

    local function current_dock(data, ship)
        local ok, dock = pcall(o.read_dock, ship or data.currentShip, data.component)
        if ok then return dock end
    end

    local function same_dock(a, b)
        return a and b and a.ship == b.ship and a.station == b.station
            and a.sector == b.sector and a.station_code == b.station_code
    end

    local function redraw()
        if menu.contextshiptable then menu.queuetradecontextrefresh = true end
    end

    local function balance(c)
        return c.status == 'observed' and c.view.balance / 100 or 0
    end

    local function provisioned(c, offer)
        local cash = balance(c)
        local space = offer.free
        local transport, unit_volume = GetWareData(offer.ware, 'transport', 'volume')
        for id, order in pairs(c.data.orders) do
            local other = c.by_id[id]
            if id ~= offer.id and other and other.isselloffer and order < 0 then
                cash = cash - RoundTotalTradePrice(-order * other.price)
                if offer.ammotypename and offer.ammotypename == other.ammotypename then
                    space = space + order
                elseif not offer.ammotypename and not other.ammotypename then
                    local other_transport, other_volume = GetWareData(other.ware, 'transport', 'volume')
                    if transport == other_transport and unit_volume and unit_volume > 0 and other_volume then
                        space = space - math.ceil(-order * other_volume / unit_volume)
                    end
                end
            end
        end
        return math.max(0, cash), math.max(0, space)
    end

    local function cap(c, offer)
        if c.status ~= 'observed' or c.used or not offer or not offer.isselloffer or not offer.tradeable
                or offer.price_cents > max_order_integer then return 0 end
        local cash, capacity = provisioned(c, offer)
        local maximum = math.min(offer.amount, capacity, GetNumAffordableTradeItems(cash, offer.price))
        local minimum = math.max(1, offer.minamount)
        if maximum < minimum or RoundTotalTradePrice(minimum * offer.price) > cash then return 0 end
        local low = minimum
        while low < maximum do
            local middle = math.floor((low + maximum + 1) / 2)
            if RoundTotalTradePrice(middle * offer.price) <= cash then low = middle
            else maximum = middle - 1 end
        end
        return low
    end

    local function scope(c)
        return {
            ConvertIDTo64Bit = function(id)
                return c.by_id[id] and id or ConvertIDTo64Bit(id)
            end,
            ConvertStringToLuaID = function(value)
                -- The stock trade-context event passes its uint64 station directly.
                if type(value) == 'cdata' and value == c.dock.station then value = tostring(value) end
                local id = tonumber(value)
                return c.by_id[id] and id or ConvertStringToLuaID(value)
            end,
            IsSameTrade = function(left, right)
                if c.by_id[left] or c.by_id[right] then return left == right end
                return IsSameTrade(left, right)
            end,
            GetPlayerMoney = function() return owns(c) and balance(c) or GetPlayerMoney() end,
            GetComponentData = function(component, ...)
                local results = pack(GetComponentData(component, ...))
                if o.identity(component) == c.dock.station then
                    for index = 1, select('#', ...) do
                        if select(index, ...) == 'tradesubscription' then results[index] = true end
                    end
                end
                return unpack(results, 1, results.n)
            end,
        }
    end

    function menu.getTradeOfferByID(id)
        if type(id) == 'number' and id < 0 then
            return owns(active) and active.by_id[id] or nil
        end
        return native.getTradeOfferByID(id)
    end

    for _, name in ipairs({'updateTradeCost', 'getCargoOrderAmountByWare', 'getAmmoOrderAmountByWare'}) do
        local fn = native[name]
        menu[name] = function(...)
            if owns(active) then return scoped(fn, scope(active), ...) end
            return fn(...)
        end
    end

    function menu.getTradeContextStorableAmountAfterTradeOrders(ship, ware, ...)
        local c = active
        if not owns(c) then return native.getTradeContextStorableAmountAfterTradeOrders(ship, ware, ...) end
        local offer = c.buy_ware[ware]
        if not offer then return 0 end
        local _, space = provisioned(c, offer)
        return math.max(0, space + (c.data.orders[offer.id] or 0))
    end

    function menu.getTradeContextRowContent(waredata)
        local c = active
        if not owns(c) then return native.getTradeContextRowContent(waredata) end
        local result = scoped(native.getTradeContextRowContent, scope(c), waredata)
        -- The last stock modifier compares the effective price with the ware
        -- average. The provider does not report the component breakdown.
        local modifiers = result[11]
        local comparison = modifiers[#modifiers]
        result[11] = comparison and {{text = 'Net price vs. average', amount = comparison.amount}} or {}
        local scale = result[4].scale
        local offer = waredata.sell
        scale.min, scale.minselect = 0, 0 -- Provider settlement currently accepts purchases only.
        scale.max = cap(c, offer)
        scale.maxselect = scale.max
        scale.start = math.min(scale.max, math.max(0, scale.start))
        return result
    end

    for _, name in ipairs({'slidercellShipCargo', 'slidercellShipAmmo'}) do
        local fn = native[name]
        menu[name] = function(sell_id, buy_id, ware, amount, value)
            local c = active
            if not owns(c) then
                if (type(sell_id) == 'number' and sell_id < 0) or (type(buy_id) == 'number' and buy_id < 0) then
                    return
                end
                return fn(sell_id, buy_id, ware, amount, value)
            end
            local offer = c.by_id[sell_id]
            if not offer or not offer.isselloffer or offer.ware ~= ware or type(value) ~= 'number'
                    or value % 1 ~= 0 or value < 0 or value > cap(c, offer) then return end
            if value > 0 and value < math.max(1, offer.minamount) then value = 0 end
            if not same_dock(c.dock, current_dock(c.data)) then return end
            -- The existing booking service accepts one buy. Keep this a single
            -- stock provisional order and let stock draw its own rounded total.
            if value > 0 then
                for id in pairs(c.data.orders) do c.data.orders[id] = nil end
                c.data.orders[sell_id] = -value
            else
                c.data.orders[sell_id] = nil
            end
            menu.tradeAmountChanged = ware
            menu.tradeContextCostAndStorageUpdateHelper(name == 'slidercellShipAmmo' and 'ammo' or 'cargo', ware)
        end
    end

    local function initialize(c, ...)
        local cargo, ammo, offers, by_id = quotes(c.status == 'observed' and c.view or nil)
        c.by_id, c.buy_ware = by_id, {}
        for _, offer in ipairs(offers) do
            if offer.isselloffer and not c.buy_ware[offer.ware] then c.buy_ware[offer.ware] = offer end
        end
        local env = scope(c)
        env.GetTradeList = function(_, _, tradeable)
            return tradeable == false and {} or offers
        end
        env.GetCargoAfterTradeOrders = function() return cargo end
        env.GetAmmoCountAfterTradeOrders = function() return ammo end
        env.CanTradeWith = function(id)
            return c.by_id[id] and cap(c, c.by_id[id]) > 0
        end
        env.SetVirtualCargoMode = function() end
        env.AddKnownItem = function() end
        local wares, info = scoped(native.initTradeContextData, env, ...)
        -- Stock onUpdate compares against local cash, while the trade quotes
        -- and totals are rendered from the scoped Company account.
        c.data.playerMoney = GetPlayerMoney()
        return wares, info
    end

    local function refresh(c)
        local wares, info = initialize(c)
        if menu.updateTradeContextDimensions then menu.updateTradeContextDimensions(wares, info) end
        redraw()
    end

    function menu.initTradeContextData(...)
        local data = menu.contextMenuData
        if menu.contextMenuMode ~= 'trade' or type(data) ~= 'table' or data.wareexchange
                or (data.currentShip ~= nil and o.identity(data.currentShip) ~= o.ship) then
            clear()
            return native.initTradeContextData(...)
        end
        if data.currentShip == nil then
            -- Preserve the actual stock ship-selection default without loading
            -- local quotes or changing virtual cargo while it is determined.
            scoped(native.initTradeContextData, {GetTradeList = function() return {} end,
                SetVirtualCargoMode = function() end})
        end
        local dock = current_dock(data)
        if not dock then clear(); return native.initTradeContextData(...) end
        if not owns(active) or not same_dock(dock, active.dock) then
            clear()
            data.orders = {}
            local view = market:open(dock)
            if view then view.get_ammo_type = menu.getAmmoTypeNameByWare end
            active = {data = data, dock = dock, view = view, status = view and 'pending' or 'unavailable',
                next_dock_check = 0}
        end
        return initialize(active, ...)
    end

    function menu.createTradeContext(frame, ...)
        local c = active
        if not owns(c) then return native.createTradeContext(frame, ...) end
        local env = scope(c)
        env.ReadText = function(page, id, ...)
            if page == 1001 and id == 8311 then
                if c.used then return 'Reopen trade for a fresh Company view.' end
                if c.status == 'pending' then return 'Loading Company offers…' end
                if c.status == 'unavailable' then return 'Company offers unavailable. Reopen trade to retry.' end
            end
            return ReadText(page, id, ...)
        end
        scoped(native.createTradeContext, env, frame, ...)
        local footer = frame.content[#frame.content]
        if footer and type(footer.addRow) == 'function' and c.status == 'observed' and not c.used then
            local status = footer:addRow(nil, {fixed = true})
            status[1]:setColSpan(4):createText('Company balance  ' .. credits(c.view.balance)
                .. '  ·  Purchases only')
            frame.properties.y = math.min(frame.properties.y,
                Helper.viewHeight - footer.properties.y - footer:getFullHeight() - Helper.frameBorder)
        end
    end

    function menu.closeContextMenu(...)
        local result = pack(native.closeContextMenu(...))
        if active and not owns(active) then clear() end
        return unpack(result, 1, result.n)
    end

    function api:order(data)
        local c = active
        if not owns(c) or c.data ~= data or c.used or not same_dock(c.dock, current_dock(data)) then
            return nil, 'fresh_offer_required'
        end
        local order, quantity
        for id, amount in pairs(data.orders) do
            if amount ~= 0 then
                if order or type(amount) ~= 'number' or amount >= 0 or amount % 1 ~= 0 then
                    return nil, 'single_buy_required'
                end
                order, quantity = c.by_id[id], -amount
                if not order then return nil, 'stale_offer' end
            end
        end
        if not order or quantity < math.max(1, order.minamount) or quantity > cap(c, order) then
            return nil, 'stale_offer'
        end
        local total = quote_cents(RoundTotalTradePrice(quantity * order.price))
        if not total or total > c.view.balance then return nil, 'stale_offer' end
        return {ware = order.ware, quantity = quantity, price_cents = order.price_cents}, nil, total
    end

    function api:consume(data)
        if owns(active) and active.data == data then
            active.used = true
            self:stop()
        end
    end

    function api:stop()
        if active then
            market:close()
            active.status, active.data.orders = 'unavailable', {}
            if owns(active) then refresh(active) end
        end
    end

    function api:tick()
        local c = active
        if not c then return end
        if not owns(c) then clear(); return end
        if c.used or c.status == 'unavailable' then return end
        local now = market.clock()
        if now >= c.next_dock_check then
            c.next_dock_check = now + 0.25
            if not same_dock(c.dock, current_dock(c.data)) then self:stop(); return end
        end
        if c.view.status ~= c.status then
            c.status = c.view.status
            refresh(c)
        end
    end

    return api
end
X4LAN_COMPANY_TRADE_VIEW_UI = M
return M
