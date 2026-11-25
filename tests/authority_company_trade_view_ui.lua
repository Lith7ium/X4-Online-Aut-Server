local ffi = require('ffi')
local module = dofile('src/authority/lua/company_trade_view_ui.lua')
local local_money = 1
local sale_mode = arg[1] == 'sale'
function GetPlayerMoney() return local_money end
function RoundTotalTradePrice(total) return math.floor(total) end
function GetNumAffordableTradeItems(money, price)
    local units = math.floor(money / price)
    while RoundTotalTradePrice((units + 1) * price) <= money do units = units + 1 end
    return units
end
function GetWareData() return 'container', 2 end
function GetTradeList() return {{ware = 'local_only'}} end
function GetCargoAfterTradeOrders() return {water = 99} end
function GetAmmoCountAfterTradeOrders() return {} end
function GetComponentData() return false end
function ConvertIDTo64Bit(id) assert(id > 0); return id end
function ConvertStringToLuaID(id)
    assert(type(id) == 'string')
    local value = tonumber((id:gsub('ULL$', '')))
    assert(value > 0)
    return value
end
function IsSameTrade(left, right) return left == right end
function CanTradeWith() return false end

local menu = {contextMenuMode = 'trade', contextMenuData = {currentShip = 7354, component = 9, orders = {}}}
local native_slider_calls = 0
function menu.getAmmoTypeNameByWare() end
function menu.initTradeContextData()
    local data = menu.contextMenuData
    data.playerMoney = GetPlayerMoney()
    data.currentcargo = GetCargoAfterTradeOrders()
    data.offers = GetTradeList()
    for _, offer in ipairs(data.offers) do offer.active = CanTradeWith(offer.id) end
end
function menu.createTradeContext()
    assert(ConvertStringToLuaID(ffi.new('uint64_t', 9)) == 9)
    assert(GetPlayerMoney() == 116)
    assert(GetComponentData(9, 'tradesubscription'))
    menu.updateTradeCost()
end
function menu.updateTradeCost()
    menu.contextMenuData.total = 0
    for id, quantity in pairs(menu.contextMenuData.orders) do
        local offer = menu.getTradeOfferByID(ConvertStringToLuaID(tostring(id)))
        menu.contextMenuData.total = RoundTotalTradePrice(-quantity * offer.price)
    end
end
function menu.getTradeContextRowContent(row)
    if row.sell then
        ConvertIDTo64Bit(row.sell.id)
        assert(not (row.sell.quantityfactor > 1) and row.sell.quantityfactor - 1 == 0)
    end
    return {{}, {}, {}, {scale = {min = -100, minselect = -100, max = 100, start = 0}},
        [11] = {{text = 'Volume adjustment', amount = '0%'}, {text = 'Total', amount = '2%'}}}
end
function menu.getTradeOfferByID() end
function menu.getTradeContextStorableAmountAfterTradeOrders() return 1 end
function menu.getCargoOrderAmountByWare() return 0 end
function menu.getAmmoOrderAmountByWare() return 0 end
function menu.slidercellShipCargo() native_slider_calls = native_slider_calls + 1 end
menu.slidercellShipAmmo = menu.slidercellShipCargo
function menu.tradeContextCostAndStorageUpdateHelper() menu.updateTradeCost() end
function menu.closeContextMenu() menu.contextMenuData = nil end

local market, serial, view = {}, 0, nil
local dock_live = true
function market:open()
    serial = serial + 1
    view = {serial = serial, status = 'pending', balance = 11600, rows = {
        {side = 'buy', ware = 'water', price_cents = 5848, amount = 50, min_amount = 1,
            cargo_units = 2, free_units = 6, tradeable = true},
        {side = 'sell', ware = 'water', price_cents = 4500, amount = 100, min_amount = 1,
            cargo_units = 2, free_units = 6, tradeable = true},
        {side = 'buy', ware = 'ore', price_cents = 5000, amount = 50, min_amount = 1,
            cargo_units = 0, free_units = 6, tradeable = false},
        {side = 'buy', ware = 'energycells', price_cents = 100, amount = 70000, min_amount = 1,
            cargo_units = 0, free_units = 70000, tradeable = true},
    }}
    if sale_mode then
        view.rows[1].cargo_units = 12
        local bid = view.rows[2]
        bid.price_cents, bid.amount, bid.min_amount, bid.cargo_units = 3473, 60, 2, 12
    end
    return view
end
function market:close() end
function market.clock() return 0 end
local function read_dock(ship, station)
    if dock_live and ship == 7354 and station == 9 then
        return {ship = ship, station = station, sector = 'sector', station_code = sale_mode and 'CTD-093' or 'BIM-169'}
    end
end
local function identity(value) return value end
local request_sale, settle_sale, sale_requests, notices = nil, nil, {}, {}
local sale_id
local inline_sale = true
local function receipt(intent, quantity)
    return {outcome='confirmed', sale_id=sale_id, ship_code=intent.ship_code, ware=intent.ware,
        sector=intent.sector, station_code=intent.station_code, quantity=quantity,
        price_cents=intent.price_cents, credited_cents=6900, company_balance_cents=937000, cargo_units=10}
end
if sale_mode then
    request_sale = function(intent, callback, displayed_total)
        assert(intent.ware == 'water' and intent.price_cents == 3473)
        assert(intent.sector == 'sector' and intent.station_code == 'CTD-093' and intent.ship_code == 'WRN-302')
        assert(displayed_total == RoundTotalTradePrice(intent.quantity * 34.73) * 100)
        sale_requests[#sale_requests+1] = intent
        sale_id = string.format('%032x', #sale_requests)
        settle_sale = callback
        if inline_sale then callback(receipt(intent, 2)) end
        return true, sale_id
    end
end
local ui = assert(module.install({menu = menu, market = market, ship = 7354,
    identity = identity, read_dock = read_dock, request_sale = request_sale}))

local data = menu.contextMenuData
menu.initTradeContextData()
assert(#data.offers == 0 and not ui:order(data))
view.status = 'observed'
ui:tick()
if sale_mode then
    local capture = dofile('src/authority/lua/company_trade_capture.lua')
    local function notice(status, amount, snapshot)
        notices[#notices+1] = {status=status, amount=amount, snapshot=snapshot}
    end
    menu.name = 'MapMenu'
    menu.buttonConfirmTrade = function() error('sale must never go through local stock confirmation') end
    local purchases = 0
    assert(capture.install({enabled=true, menu=menu, market_ui=ui, dynamic_station=true,
        ship=7354, ship_code='WRN-302', identity=identity, read_dock=read_dock,
        request=function(intent)
            assert(intent.ware == 'water' and intent.quantity == 2 and intent.price_cents == 5848 and not intent.side)
            purchases = purchases + 1
            return true
        end,
        request_sale=request_sale, notice=notice, decode_offer_id=identity, offer_station=identity}))

    local function check_dock()
        data.immediate, data.immediateObject = true, 7354
        return unpack(data.offers)
    end
    local function reopen()
        menu.closeContextMenu()
        menu.contextMenuData = {currentShip=7354, component=9, orders={}}
        data = menu.contextMenuData
        menu.initTradeContextData()
        view.status = 'observed'
        ui:tick()
        return check_dock()
    end
    local buy, sell = check_dock()
    local scale = menu.getTradeContextRowContent({sell=buy, buy=sell})[4].scale
    assert(sell.active and scale.min == -12 and scale.max == 2)
    sell.cargo_units = 90
    assert(menu.getTradeContextRowContent({sell=buy, buy=sell})[4].scale.min == -60)
    sell.cargo_units = 12
    menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, -13)
    assert(not ui:order(data))
    menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, -1)
    assert(not ui:order(data))
    menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, 2)
    assert(ui:order(data).quantity == 2 and not ui:order(data).side)
    menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, -2)
    assert(data.orders[sell.id] == 2 and data.orders[buy.id] == nil and ui:order(data).side == 'sell')
    data.orders[buy.id] = -1
    menu.buttonConfirmTrade()
    assert(notices[#notices].status == 'single_trade_required' and #sale_requests == 0)
    data.orders[buy.id] = nil
    data.currentShip = 89
    menu.buttonConfirmTrade()
    assert(notices[#notices].status == 'stale_identity' and #sale_requests == 0)
    data.currentShip = 7354
    dock_live = false
    menu.buttonConfirmTrade()
    assert(notices[#notices].status == 'stale_dock' and #sale_requests == 0)
    dock_live = true
    menu.buttonConfirmTrade()
    assert(#sale_requests == 1 and purchases == 0 and native_slider_calls == 0)
    local confirmed = notices[#notices]
    assert(confirmed.status == 'sale_confirmed' and confirmed.amount == 6900)
    assert(confirmed.snapshot.company_balance_cents == 937000 and confirmed.snapshot.cargo_units == 10)
    menu.buttonConfirmTrade()
    assert(notices[#notices].status == 'already_attempted' and #sale_requests == 1)

    inline_sale = false
    buy, sell = reopen()
    menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, -12)
    menu.buttonConfirmTrade()
    local delayed = settle_sale
    assert(notices[#notices].status == 'sale_pending' and #sale_requests == 2)
    menu.buttonConfirmTrade()
    assert(notices[#notices].status == 'already_attempted' and #sale_requests == 2)
    local empty = receipt(sale_requests[2], 12)
    empty.cargo_units = 0
    delayed(empty)
    assert(notices[#notices].status == 'sale_confirmed' and notices[#notices].snapshot.cargo_units == 0)

    if arg[2] == 'buy' then
        buy, sell = reopen()
        menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, 2)
        menu.buttonConfirmTrade()
        assert(purchases == 1 and #sale_requests == 2 and notices[#notices].status == 'pending')
        return
    end
    buy, sell = reopen()
    menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, -2)
    menu.buttonConfirmTrade()
    if arg[2] == 'price_changed' then
        local refusal = receipt(sale_requests[3], 2)
        refusal.outcome, refusal.reason, refusal.current_price_cents = 'refused', 'price_changed', 3501
        settle_sale(refusal)
        assert(notices[#notices].status == 'sale_refused')
        assert(notices[#notices].snapshot.price_cents == 3473 and notices[#notices].snapshot.current_price_cents == 3501)
        menu.buttonConfirmTrade()
        assert(notices[#notices].status == 'already_attempted' and #sale_requests == 3)
        return
    end
    local wrong = receipt(sale_requests[3], 2)
    if arg[2] == 'wrong_price' then
        wrong.price_cents = 3472
    elseif arg[2] == 'negative_cargo' then
        wrong.cargo_units = -1
    else
        wrong.sale_id = ('c'):rep(32)
    end
    settle_sale(wrong)
    assert(notices[#notices].status == 'sale_uncertain' and purchases == 0)
    settle_sale(receipt(sale_requests[3], 2))
    assert(notices[#notices].status == 'sale_uncertain')
    return
end
assert(#data.offers == 4 and data.currentcargo.water == 2 and data.playerMoney == local_money)
local buy, sell, disabled, large_offer = unpack(data.offers)
assert(buy.active and not sell.active and not disabled.active)
local content = menu.getTradeContextRowContent({sell = buy, buy = sell})
local scale = content[4].scale
assert(#content[11] == 1 and content[11][1].text == 'Net price vs. average' and content[11][1].amount == '2%')
assert(scale.min == 0 and scale.max == 2) -- $116 can buy two despite a 58.48 unit quote.
assert(menu.getTradeContextRowContent({sell = disabled})[4].scale.max == 0)
menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, -1)
assert(not ui:order(data))
menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, 2)
local intent, _, total = ui:order(data)
assert(intent.ware == 'water' and intent.quantity == 2 and intent.price_cents == 5848)
assert(total == 11600 and data.total == 116 and native_slider_calls == 0)
menu.createTradeContext({content = {}})
assert(GetPlayerMoney() == local_money and GetTradeList()[1].ware == 'local_only')

-- The native Company trade command accepts at most 65,535 units per confirmation.
view.balance = 7000000
menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, 0)
assert(menu.getTradeContextRowContent({sell = large_offer})[4].scale.max == 65535)
large_offer.minamount = 65536
assert(menu.getTradeContextRowContent({sell = large_offer})[4].scale.max == 0)

-- A newly opened window may not accept stale widget callbacks or old quotes.
ui:consume(data)
assert(not ui:order(data) and #data.offers == 0)
menu.closeContextMenu()
menu.contextMenuData = {currentShip = 7354, component = 9, orders = {}}
menu.initTradeContextData()
view.status = 'observed'
ui:tick()
menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, 2)
assert(not ui:order(menu.contextMenuData))
menu.contextMenuData = {currentShip = 89, component = 9, orders = {}}
menu.initTradeContextData()
assert(menu.contextMenuData.offers[1].ware == 'local_only')
menu.slidercellShipCargo(buy.id, sell.id, 'water', 0, 2)
assert(native_slider_calls == 0)
menu.slidercellShipCargo(2, 3, 'water', 0, 2)
assert(native_slider_calls == 1)
