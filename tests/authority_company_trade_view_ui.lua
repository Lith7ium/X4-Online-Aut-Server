local ffi = require('ffi')
local module = dofile('src/authority/lua/company_trade_view_ui.lua')
local local_money = 1
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
    return view
end
function market:close() end
function market.clock() return 0 end
local ui = assert(module.install({menu = menu, market = market, ship = 7354,
    identity = function(value) return value end,
    read_dock = function(ship, station)
        if ship == 7354 and station == 9 then
            return {ship = ship, station = station, sector = 'sector', station_code = 'BIM-169'}
        end
    end}))

local data = menu.contextMenuData
menu.initTradeContextData()
assert(#data.offers == 0 and not ui:order(data))
view.status = 'observed'
ui:tick()
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
