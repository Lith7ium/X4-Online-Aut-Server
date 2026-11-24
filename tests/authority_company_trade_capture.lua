local module = dofile('src/authority/lua/company_trade_capture.lua')
local live, native_calls, requests, notices, lines = true, 0, {}, {}, {}
local offer = {ware = 'water', price = 58.77}
local offer_station = 71
local menu = {name = 'MapMenu'}
function menu.buttonConfirmTrade(value)
    native_calls = native_calls + 1
    return value, 'stock_return'
end
function menu.getTradeOfferByID(id)
    assert(id == 123)
    return offer
end

local function original(value)
    local x, y = menu.buttonConfirmTrade(value)
    assert(x == value and y == 'stock_return')
end

package.preload.ffi = function()
    local C = {
        GetPlayerControlledShipID = function() return 11 end,
        ShowInfoLine = function(text, seconds) assert(seconds == 4); lines[#lines + 1] = text end,
        IsComponentClass = function(id, class)
            return (id == 11 and class == 'ship') or (id == 71 and class == 'station')
        end,
        IsComponentOperational = function() return true end,
        GetNumDockedShips = function(station) assert(station == 71); return 1 end,
        GetDockedShips = function(members, count, station)
            assert(count == 1 and station == 71)
            members[0] = live and 11 or 12
            return 1
        end,
        GetContextByClass = function(station, class)
            assert(station == 71 and class == 'sector')
            return 82
        end,
        GetObjectIDCode = function(id) assert(id == 71); return 'BIM-169' end,
    }
    return {C = C, typeof = function() return true end, cdef = function() end,
        new = function() return {} end, string = function(value) return value end}
end
function ConvertIDTo64Bit(value) return value end
function ConvertStringToLuaID(value) return tonumber(value) end
function GetComponentData(id, key)
    if id == 11 then assert(key == 'owner'); return 'player', true, false, false end
    if id == 71 then assert(key == 'owner'); return 'teladi' end
    assert(id == 82 and key == 'macro')
    return 'cluster_01_sector001_macro'
end
function GetTradeData(id)
    assert(id == 123)
    return {station = offer_station}
end

local options = {menu = menu, ship = 11, station = 71, sector = 'cluster_01_sector001_macro',
    station_code = 'BIM-169', notice = function(status) notices[#notices + 1] = status end,
    request = function(intent, settle)
        requests[#requests + 1] = {intent = intent, settle = settle}
        return true
    end}
assert(module.install_x4(options) == nil)
menu.contextMenuData = {component = 71, currentShip = 11, immediate = true, immediateObject = 11, orders = {[123] = -1}}
original('disabled')

options.enabled = true
local installed = assert(module.install_x4(options))
menu.contextMenuData.currentShip = 12
original('other ship')
menu.contextMenuData.currentShip, menu.contextMenuData.component = 11, 72
original('other station')
menu.contextMenuData.component = 71
menu.contextMenuData.currentShip = nil
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_identity')
menu.contextMenuData.currentShip = 11
menu.contextMenuData.immediate = false
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'immediate_trade_required')
menu.contextMenuData.immediate, menu.contextMenuData.immediateObject = true, 71
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'immediate_trade_required')
menu.contextMenuData.immediateObject = 11
live = false
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_dock')
live = true
menu.contextMenuData.orders[123] = 1
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'single_buy_required')
menu.contextMenuData.orders[123] = -1
offer.price = 58.771
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'ambiguous_price')
offer.price, offer_station = 58.77, 72
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_offer')
offer_station = 71

assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'pending')
local purchase = requests[1]
assert(#requests == 1 and purchase.intent.sector == 'cluster_01_sector001_macro'
    and purchase.intent.station_code == 'BIM-169' and purchase.intent.ware == 'water'
    and purchase.intent.quantity == 1 and purchase.intent.price_cents == 5877)
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'already_attempted')
assert(#requests == 1 and not installed.detach())
purchase.settle({outcome = 'confirmed', sector = purchase.intent.sector,
    station_code = 'BIM-169', ware = 'water', quantity = 1, price_cents = 5878, settled_total_cents = 5877})
assert(notices[#notices] == 'uncertain')
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'already_attempted')
assert(#requests == 1 and native_calls == 3)
assert(installed.detach())
original('detached')

-- The stock notice uses the booked total, independent of the unit quote.
options.notice = nil
menu.contextMenuData.orders[123] = -6
offer.price = 58.19
local function report(outcome, total, expect_notice)
    options.request = function(intent, settle)
        assert(intent.quantity == 6 and intent.price_cents == 5819)
        if outcome then
            intent.outcome, intent.settled_total_cents = outcome, total
            settle(intent)
        else
            settle(nil)
        end
        return true
    end
    local adapter = assert(module.install_x4(options))
    local previous = #lines
    assert(menu.buttonConfirmTrade() == nil and #lines == previous + 1)
    assert(lines[#lines] == expect_notice, lines[#lines])
    assert(adapter.detach())
end
report('confirmed', 34900, 'Company purchase confirmed. Paid 349 Cr.')
report('confirmed', 34905, 'Company purchase confirmed. Paid 349.05 Cr.')
report('refused', nil, 'Company purchase refused.')
local uncertain = 'Company purchase status uncertain. Check the authoritative receipt.'
report(nil, nil, uncertain)
report('confirmed', nil, uncertain)
for _, malformed in ipairs({'34900', -34900, 0, 34900.5, math.huge, 0 / 0, 9007199254740992}) do
    report('confirmed', malformed, uncertain)
end
assert(native_calls == 4)
