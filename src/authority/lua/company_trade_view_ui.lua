-- Feed the stock MapMenu trade initializer, renderer and controls with Company data.
local TradeViewUI = {}
-- Limits accepted by the native Company trade command.
local max_unit_price_cents = 2147483647
local max_units_per_order = 65535

local function capture_returns(...)
    return {n = select('#', ...), ...}
end

-- Override only the stock function's own lookup environment. Other game menus
-- keep their globals and no synthetic offer ID can reach a native operation.
local function call_with_environment(stock_function, replacements, ...)
    local original_environment = getfenv(stock_function)
    setfenv(stock_function, setmetatable(replacements, {__index = original_environment}))
    local result = capture_returns(pcall(stock_function, ...))
    setfenv(stock_function, original_environment)
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

local function build_stock_offers(view)
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
            cargo_units = value.cargo_units,
            isselloffer = value.side == 'buy', isbuyoffer = value.side == 'sell'}
        local ammotypename = view.get_ammo_type(value.ware)
        offer.ammotypename = ammotypename
        local storage = ammotypename and ammo or cargo
        storage[offer.ware] = value.cargo_units
        offers[#offers + 1], by_id[offer.id] = offer, offer
    end
    return cargo, ammo, offers, by_id
end

function TradeViewUI.install(options)
    local menu, market = options.menu, options.market
    local sales_enabled = type(options.request_sale) == 'function'
    local stock = {}
    for _, name in ipairs({'initTradeContextData', 'createTradeContext', 'getTradeContextRowContent',
            'getTradeContextStorableAmountAfterTradeOrders', 'getTradeOfferByID', 'updateTradeCost',
            'getCargoOrderAmountByWare', 'getAmmoOrderAmountByWare', 'slidercellShipCargo',
            'slidercellShipAmmo', 'closeContextMenu'}) do
        stock[name] = menu[name]
        if type(stock[name]) ~= 'function' then return end
    end
    local active_context
    local api = {}

    local function clear()
        if active_context then market:close(); active_context = nil end
    end

    local function owns_trade_context(context)
        return context and active_context == context and menu.contextMenuMode == 'trade'
            and menu.contextMenuData == context.data
    end

    local function current_dock(data, ship)
        local ok, dock = pcall(options.read_dock, ship or data.currentShip, data.component)
        if ok then return dock end
    end

    local function same_dock(expected, current)
        return expected and current and expected.ship == current.ship and expected.station == current.station
            and expected.sector == current.sector and expected.station_code == current.station_code
    end

    local function redraw()
        if menu.contextshiptable then menu.queuetradecontextrefresh = true end
    end

    local function balance(context)
        return context.status == 'observed' and context.view.balance / 100 or 0
    end

    local function remaining_cash_and_space(context, offer)
        local cash = balance(context)
        local space = offer.free
        local transport, unit_volume = GetWareData(offer.ware, 'transport', 'volume')
        for id, order in pairs(context.data.orders) do
            local other = context.by_id[id]
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

    local function maximum_purchase_quantity(context, offer)
        if context.status ~= 'observed' or context.used or not offer or not offer.isselloffer or not offer.tradeable
                or offer.price_cents > max_unit_price_cents then return 0 end
        local cash, capacity = remaining_cash_and_space(context, offer)
        local maximum = math.min(max_units_per_order, offer.amount, capacity,
            GetNumAffordableTradeItems(cash, offer.price))
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

    local function maximum_sale_quantity(context, offer)
        if not sales_enabled or context.status ~= 'observed' or context.used or not offer or not offer.isbuyoffer
                or not offer.tradeable or offer.price_cents < 1 or offer.price_cents > max_unit_price_cents
                or offer.price_cents % 1 ~= 0 then return 0 end
        local maximum = math.min(max_units_per_order, offer.amount, offer.cargo_units)
        return maximum >= math.max(1, offer.minamount) and maximum or 0
    end

    local function trade_environment(context)
        return {
            ConvertIDTo64Bit = function(id)
                return context.by_id[id] and id or ConvertIDTo64Bit(id)
            end,
            ConvertStringToLuaID = function(value)
                -- The stock trade-context event passes its uint64 station directly.
                if type(value) == 'cdata' and value == context.dock.station then value = tostring(value) end
                local id = tonumber(value)
                return context.by_id[id] and id or ConvertStringToLuaID(value)
            end,
            IsSameTrade = function(left, right)
                if context.by_id[left] or context.by_id[right] then return left == right end
                return IsSameTrade(left, right)
            end,
            GetPlayerMoney = function() return owns_trade_context(context) and balance(context) or GetPlayerMoney() end,
            GetComponentData = function(component, ...)
                local results = capture_returns(GetComponentData(component, ...))
                if options.identity(component) == context.dock.station then
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
            return owns_trade_context(active_context) and active_context.by_id[id] or nil
        end
        return stock.getTradeOfferByID(id)
    end

    for _, name in ipairs({'updateTradeCost', 'getCargoOrderAmountByWare', 'getAmmoOrderAmountByWare'}) do
        local stock_function = stock[name]
        menu[name] = function(...)
            if owns_trade_context(active_context) then
                return call_with_environment(stock_function, trade_environment(active_context), ...)
            end
            return stock_function(...)
        end
    end

    function menu.getTradeContextStorableAmountAfterTradeOrders(ship, ware, ...)
        local context = active_context
        if not owns_trade_context(context) then
            return stock.getTradeContextStorableAmountAfterTradeOrders(ship, ware, ...)
        end
        local offer = context.buy_ware[ware]
        if not offer then return 0 end
        local _, space = remaining_cash_and_space(context, offer)
        return math.max(0, space + (context.data.orders[offer.id] or 0))
    end

    function menu.getTradeContextRowContent(waredata)
        local context = active_context
        if not owns_trade_context(context) then return stock.getTradeContextRowContent(waredata) end
        local result = call_with_environment(stock.getTradeContextRowContent, trade_environment(context), waredata)
        -- The last stock modifier compares the effective price with the ware
        -- average. The provider does not report the component breakdown.
        local modifiers = result[11]
        local comparison = modifiers[#modifiers]
        result[11] = comparison and {{text = 'Net price vs. average', amount = comparison.amount}} or {}
        local scale = result[4].scale
        local offer = waredata.sell
        local sale_limit = maximum_sale_quantity(context, waredata.buy)
        scale.min, scale.minselect = -sale_limit, -sale_limit
        scale.max = maximum_purchase_quantity(context, offer)
        scale.maxselect = scale.max
        scale.start = math.min(scale.max, math.max(scale.min, scale.start))
        return result
    end

    for _, name in ipairs({'slidercellShipCargo', 'slidercellShipAmmo'}) do
        local stock_function = stock[name]
        menu[name] = function(sell_id, buy_id, ware, stock_amount, quantity)
            local context = active_context
            if not owns_trade_context(context) then
                if (type(sell_id) == 'number' and sell_id < 0) or (type(buy_id) == 'number' and buy_id < 0) then
                    return
                end
                return stock_function(sell_id, buy_id, ware, stock_amount, quantity)
            end
            if type(quantity) ~= 'number' or quantity % 1 ~= 0 then return end
            local selling = quantity < 0
            local id = selling and buy_id or sell_id
            local offer = context.by_id[id]
            if sales_enabled and quantity == 0 and (not offer or not offer.isselloffer) then
                offer = context.by_id[buy_id]
            end
            if not offer or offer.ware ~= ware then return end
            if selling then
                if -quantity > maximum_sale_quantity(context, offer) then return end
            elseif quantity > maximum_purchase_quantity(context, offer) or
                    (not offer.isselloffer and not (sales_enabled and quantity == 0 and offer.isbuyoffer)) then
                return
            end
            if quantity ~= 0 and math.abs(quantity) < math.max(1, offer.minamount) then quantity = 0 end
            if not same_dock(context.dock, current_dock(context.data)) then return end
            if quantity ~= 0 then
                for order_id in pairs(context.data.orders) do context.data.orders[order_id] = nil end
                context.data.orders[id] = -quantity
            else
                if sell_id then context.data.orders[sell_id] = nil end
                if sales_enabled and buy_id then context.data.orders[buy_id] = nil end
            end
            menu.tradeAmountChanged = ware
            menu.tradeContextCostAndStorageUpdateHelper(name == 'slidercellShipAmmo' and 'ammo' or 'cargo', ware)
        end
    end

    local function initialize(context, ...)
        local cargo, ammo, offers, by_id = build_stock_offers(context.status == 'observed' and context.view or nil)
        context.by_id, context.buy_ware = by_id, {}
        for _, offer in ipairs(offers) do
            if offer.isselloffer and not context.buy_ware[offer.ware] then context.buy_ware[offer.ware] = offer end
        end
        local environment = trade_environment(context)
        environment.GetTradeList = function(_, _, tradeable)
            return tradeable == false and {} or offers
        end
        environment.GetCargoAfterTradeOrders = function() return cargo end
        environment.GetAmmoCountAfterTradeOrders = function() return ammo end
        environment.CanTradeWith = function(id)
            local offer = context.by_id[id]
            return offer and (maximum_purchase_quantity(context, offer) > 0 or maximum_sale_quantity(context, offer) > 0)
        end
        environment.SetVirtualCargoMode = function() end
        environment.AddKnownItem = function() end
        local wares, info = call_with_environment(stock.initTradeContextData, environment, ...)
        -- Stock onUpdate compares against local cash, while the trade quotes
        -- and totals are rendered from the scoped Company account.
        context.data.playerMoney = GetPlayerMoney()
        return wares, info
    end

    local function refresh(context)
        local wares, info = initialize(context)
        if menu.updateTradeContextDimensions then menu.updateTradeContextDimensions(wares, info) end
        redraw()
    end

    function menu.initTradeContextData(...)
        local data = menu.contextMenuData
        if menu.contextMenuMode ~= 'trade' or type(data) ~= 'table' or data.wareexchange
                or (data.currentShip ~= nil and options.identity(data.currentShip) ~= options.ship) then
            clear()
            return stock.initTradeContextData(...)
        end
        if data.currentShip == nil then
            -- Preserve the actual stock ship-selection default without loading
            -- local quotes or changing virtual cargo while it is determined.
            call_with_environment(stock.initTradeContextData, {GetTradeList = function() return {} end,
                SetVirtualCargoMode = function() end})
        end
        local dock = current_dock(data)
        if not dock then clear(); return stock.initTradeContextData(...) end
        if not owns_trade_context(active_context) or not same_dock(dock, active_context.dock) then
            clear()
            data.orders = {}
            local view = market:open(dock)
            if view then view.get_ammo_type = menu.getAmmoTypeNameByWare end
            active_context = {data = data, dock = dock, view = view, status = view and 'pending' or 'unavailable',
                next_dock_check = 0}
        end
        return initialize(active_context, ...)
    end

    function menu.createTradeContext(frame, ...)
        local context = active_context
        if not owns_trade_context(context) then return stock.createTradeContext(frame, ...) end
        local environment = trade_environment(context)
        environment.ReadText = function(page, id, ...)
            if page == 1001 and id == 8311 then
                if context.used then return 'Reopen trade for a fresh Company view.' end
                if context.status == 'pending' then return 'Loading Company offers…' end
                if context.status == 'unavailable' then return 'Company offers unavailable. Reopen trade to retry.' end
            end
            return ReadText(page, id, ...)
        end
        call_with_environment(stock.createTradeContext, environment, frame, ...)
        local footer = frame.content[#frame.content]
        if footer and type(footer.addRow) == 'function' and context.status == 'observed' and not context.used then
            local status = footer:addRow(nil, {fixed = true})
            status[1]:setColSpan(4):createText('Company balance  ' .. credits(context.view.balance)
                .. (sales_enabled and '' or '  ·  Purchases only'))
            frame.properties.y = math.min(frame.properties.y,
                Helper.viewHeight - footer.properties.y - footer:getFullHeight() - Helper.frameBorder)
        end
    end

    function menu.closeContextMenu(...)
        local result = capture_returns(stock.closeContextMenu(...))
        if active_context and not owns_trade_context(active_context) then clear() end
        return unpack(result, 1, result.n)
    end

    function api:order(data)
        local context = active_context
        if not owns_trade_context(context) or context.data ~= data or context.used
                or not same_dock(context.dock, current_dock(data)) then
            return nil, 'fresh_offer_required'
        end
        local selected_offer, quantity, selling
        for id, signed_quantity in pairs(data.orders) do
            if signed_quantity ~= 0 then
                if selected_offer or type(signed_quantity) ~= 'number' or signed_quantity % 1 ~= 0
                        or (signed_quantity > 0 and not sales_enabled) then
                    return nil, sales_enabled and 'single_trade_required' or 'single_buy_required'
                end
                selected_offer, quantity, selling = context.by_id[id], math.abs(signed_quantity), signed_quantity > 0
                if not selected_offer then return nil, 'stale_offer' end
            end
        end
        if not selected_offer or quantity < math.max(1, selected_offer.minamount) then return nil, 'stale_offer' end
        local maximum = selling and maximum_sale_quantity(context, selected_offer)
            or maximum_purchase_quantity(context, selected_offer)
        if quantity > maximum then return nil, 'stale_offer' end
        local total = quote_cents(RoundTotalTradePrice(quantity * selected_offer.price))
        if not total or (not selling and total > context.view.balance) then return nil, 'stale_offer' end
        local intent = {ware = selected_offer.ware, quantity = quantity, price_cents = selected_offer.price_cents}
        if selling then intent.side = 'sell' end
        return intent, nil, total
    end

    function api:consume(data)
        if owns_trade_context(active_context) and active_context.data == data then
            active_context.used = true
            self:stop()
        end
    end

    function api:stop()
        if active_context then
            market:close()
            active_context.status, active_context.data.orders = 'unavailable', {}
            if owns_trade_context(active_context) then refresh(active_context) end
        end
    end

    function api:tick()
        local context = active_context
        if not context then return end
        if not owns_trade_context(context) then clear(); return end
        if context.used or context.status == 'unavailable' then return end
        local now = market.clock()
        if now >= context.next_dock_check then
            context.next_dock_check = now + 0.25
            if not same_dock(context.dock, current_dock(context.data)) then self:stop(); return end
        end
        if context.view.status ~= context.status then
            context.status = context.view.status
            refresh(context)
        end
    end

    return api
end
X4LAN_COMPANY_TRADE_VIEW_UI = TradeViewUI
return TradeViewUI
