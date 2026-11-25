local module = dofile('src/authority/lua/company_trade_capture.lua')
local ffi = require('ffi')
ffi.cdef('typedef uint64_t UniverseID;')
local ship_id, other_ship_id = 9007199254740993ULL, 9007199254740994ULL
local station_id, other_station_id = 9007199254740995ULL, 9007199254740996ULL
local sector_id, order_id = 9007199254740997ULL, 9007199254740998ULL
local lua_ids = {}
for _, id in ipairs({ship_id, other_ship_id, station_id, other_station_id, sector_id, order_id}) do
    lua_ids[tostring(id)] = {id = id}
end
local docked, docking = true, true
local live, native_calls, requests, notices, lines = true, 0, {}, {}, {}
local offer = {ware = 'water', price = 58.77, isselloffer = true, active = true}
local offer_station = lua_ids[tostring(station_id)]
local menu = {name = 'MapMenu'}
function menu.buttonConfirmTrade(value)
    native_calls = native_calls + 1
    return value, 'stock_return'
end
function menu.getTradeOfferByID(id)
    assert(id == lua_ids[tostring(order_id)])
    return offer
end

local function original(value)
    local x, y = menu.buttonConfirmTrade(value)
    assert(x == value and y == 'stock_return')
end

package.loaded.ffi = (function()
    local C = {
        GetPlayerControlledShipID = function() return ship_id end,
        ShowInfoLine = function(text, seconds) assert(seconds == 4); lines[#lines + 1] = text end,
        IsComponentClass = function(id, class)
            return (id == ship_id and class == 'ship') or (id == station_id and class == 'station')
        end,
        IsComponentOperational = function() return true end,
        GetNumDockedShips = function(station) assert(station == station_id); return 1 end,
        GetDockedShips = function(members, count, station)
            assert(count == 1 and station == station_id)
            members[0] = live and ship_id or other_ship_id
            return 1
        end,
        GetContextByClass = function(station, class)
            assert(station == station_id and class == 'sector')
            return sector_id
        end,
        GetObjectIDCode = function(id)
            if id == ship_id then return 'WRN-302' end
            assert(id == station_id)
            return 'BIM-169'
        end,
    }
    return {C = C, typeof = ffi.typeof, istype = ffi.istype, cdef = function() end,
        new = ffi.new, string = function(value) return value end}
end)()
function ConvertIDTo64Bit(value)
    assert(type(value) == 'table', 'expected opaque Lua ID')
    return value.id
end
function ConvertStringToLuaID(value) return lua_ids[value] end
function GetComponentData(ref, key, ...)
    local id = ref.id
    if id == ship_id then
        local values = {owner = 'player', isdocked = docked, isdocking = docking, isinternallystored = false}
        local second, third, fourth = ...
        return values[key], values[second], values[third], values[fourth]
    end
    if id == station_id then assert(key == 'owner'); return 'teladi' end
    assert(id == sector_id and key == 'macro')
    return 'cluster_01_sector001_macro'
end
function GetTradeData(id)
    assert(id == lua_ids[tostring(order_id)])
    return {station = offer_station, price = 59.25}
end

local options = {menu = menu, ship = lua_ids[tostring(ship_id)], station = station_id, sector = 'cluster_01_sector001_macro',
    station_code = 'BIM-169', notice = function(status) notices[#notices + 1] = status end,
    request = function(intent, settle)
        requests[#requests + 1] = {intent = intent, settle = settle}
        return true
    end}
assert(module.install_x4(options) == nil)
menu.contextMenuData = {component = station_id, currentShip = ship_id, immediate = true,
    immediateObject = ship_id, orders = {[order_id] = -1}}
original('disabled')

options.enabled = true
local installed = assert(module.install_x4(options))
menu.contextMenuData.currentShip = other_ship_id
original('other ship')
menu.contextMenuData.currentShip, menu.contextMenuData.component = ship_id, other_station_id
original('other station')
menu.contextMenuData.component = station_id
menu.contextMenuData.currentShip = nil
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_identity')
menu.contextMenuData.currentShip = ship_id
menu.contextMenuData.immediate = false
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'immediate_trade_required')
menu.contextMenuData.immediate, menu.contextMenuData.immediateObject = true, station_id
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'immediate_trade_required')
menu.contextMenuData.immediateObject = ship_id
-- A ship merely approaching must fail even if the station lists it.
docked = false
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_dock')
docked, docking = nil, false
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_dock')
-- Native X4 can report both docked and docking on the completed berth.
docked, docking = true, true
live = false
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_dock')
live = true
menu.contextMenuData.orders[order_id] = 1
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'single_buy_required')
menu.contextMenuData.orders[order_id] = -1
offer.price = 58.771
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'ambiguous_price')
offer.price, offer_station = 58.77, other_station_id
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_offer')
offer_station = lua_ids[tostring(station_id)]
offer.active = false
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_offer')
offer.active, offer.isselloffer = true, false
assert(menu.buttonConfirmTrade() == nil and notices[#notices] == 'stale_offer')
offer.isselloffer = true

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
menu.contextMenuData.orders[order_id] = -6
offer.price = 58.19
local function report(outcome, total, expect_notice, fields)
    options.request = function(intent, settle)
        assert(intent.quantity == 6 and intent.price_cents == 5819)
        if outcome then
            intent.outcome, intent.settled_total_cents = outcome, total
            for key, value in pairs(fields or {}) do intent[key] = value end
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
report('confirmed', 34900, 'Company purchase confirmed. Paid 349 Cr. Company balance 9651 Cr. WRN-302: 6 water.',
    {company_balance_cents = 965100, ship_code = 'WRN-302', cargo_units = 6})
report('confirmed', 34900, 'Company purchase confirmed. Paid 349 Cr. Company balance 0 Cr. WRN-302: 7 water.',
    {company_balance_cents = 0, ship_code = 'WRN-302', cargo_units = 7})
for _, fields in ipairs({
    {company_balance_cents = 965100, ship_code = 'WRN-302'},
    {ship_code = 'WRN-302', cargo_units = 6},
    {company_balance_cents = math.huge, ship_code = 'WRN-302', cargo_units = 6},
    {company_balance_cents = '965100', ship_code = 'WRN-302', cargo_units = 6},
    {company_balance_cents = 965100, ship_code = 'WOO-831', cargo_units = 6},
    {company_balance_cents = 965100, ship_code = 'WRN-302', cargo_units = 5},
    {company_balance_cents = 965100, ship_code = 'WRN-302', cargo_units = 6.5},
}) do
    report('confirmed', 34900, 'Company purchase confirmed. Paid 349 Cr.', fields)
end
report('refused', nil, 'Company purchase refused.')
report('refused', nil, 'Company purchase refused. Assign a Company NPC pilot to this ship on the server.',
    {reason = 'captain_required'})
report('refused', nil,
    'Company purchase refused. Unit price changed from 58.19 Cr to 58.77 Cr. Reopen the stock offer and confirm the new price.',
    {reason = 'price_changed', current_price_cents = 5877})
report('refused', nil, 'Company purchase refused.', {reason = 'price_changed'})
for _, invalid in ipairs({'5877', 0, 5819, 5877.5, math.huge, 2147483648}) do
    report('refused', nil, 'Company purchase refused.', {reason = 'price_changed', current_price_cents = invalid})
end
report('refused', nil, 'Company purchase refused.', {reason = 'other', current_price_cents = 5877})
local uncertain = 'Company purchase status uncertain. Check the authoritative receipt.'
report(nil, nil, uncertain)
report('confirmed', nil, uncertain)
report('confirmed', 34900, uncertain,
    {company_balance_cents = 965100, ship_code = 'WRN-302', cargo_units = 6, price_cents = 5818})
for _, malformed in ipairs({'34900', -34900, 0, 34900.5, math.huge, 0 / 0, 9007199254740992}) do
    report('confirmed', malformed, uncertain)
end
assert(native_calls == 4)

local function fresh_context(orders)
    return {component = station_id, currentShip = ship_id, immediate = true, immediateObject = ship_id,
        orders = orders or {[order_id] = -6}}
end

local function reply(attempt, outcome, fields)
    local receipt = {outcome = outcome}
    for key, value in pairs(attempt.intent) do receipt[key] = value end
    for key, value in pairs(fields) do receipt[key] = value end
    attempt.settle(receipt)
end

for _, case in ipairs({{5800, false}, {5877, false}, {5877, true}}) do
    local new_price, cycle = case[1], case[2]
    offer.price = 58.19
    menu.contextMenuData = fresh_context()
    local rejected = menu.contextMenuData
    local attempts = {}
    local adapter
    options.request = function(intent, settle)
        attempts[#attempts + 1] = {intent = intent, settle = settle}
        if #attempts == 2 then
            -- A's duplicate arrives synchronously while B is dispatching.
            reply(attempts[1], 'confirmed', {settled_total_cents = 34900})
            menu.buttonConfirmTrade()
        end
        return true
    end
    options.notice = function(status, _, details)
        notices[#notices + 1] = status
        if details and details.reason == 'price_changed' then
            menu.contextMenuData = fresh_context()
            offer.price = details.current_price_cents / 100
            -- Notification callbacks may be reentrant. They cannot act as the user.
            menu.buttonConfirmTrade()
            assert(not adapter.detach())
        end
    end
    adapter = assert(module.install_x4(options))
    menu.buttonConfirmTrade()
    reply(attempts[1], 'refused', {reason = 'price_changed', current_price_cents = new_price})
    assert(#attempts == 1)
    menu.contextMenuData = rejected
    menu.buttonConfirmTrade()
    assert(notices[#notices] == 'fresh_offer_required' and #attempts == 1)
    menu.contextMenuData = fresh_context(rejected.orders)
    menu.buttonConfirmTrade()
    assert(notices[#notices] == 'fresh_offer_required' and #attempts == 1)
    menu.contextMenuData = fresh_context()
    offer.stale = true
    menu.buttonConfirmTrade()
    assert(notices[#notices] == 'stale_offer' and #attempts == 1)
    offer.stale = false
    if cycle then
        -- The price can legitimately return to A's quote after reopening.
        offer.price = 58.19
        menu.buttonConfirmTrade()
        assert(#attempts == 1)
    end
    menu.buttonConfirmTrade()
    assert(#attempts == 2 and attempts[2].intent.price_cents == (cycle and 5819 or new_price))
    assert(notices[#notices] == 'pending')
    reply(attempts[1], 'refused', {reason = 'price_changed', current_price_cents = 6000})
    menu.buttonConfirmTrade()
    assert(#attempts == 2 and notices[#notices] == 'already_attempted')
    reply(attempts[2], 'confirmed', {settled_total_cents = 34900})
    assert(notices[#notices] == 'confirmed')
    assert(adapter.detach())
end
assert(native_calls == 4)

offer.ware, offer.price = 'water', 58.19
local rejected_context = fresh_context()
menu.contextMenuData = rejected_context
local different_ware_attempts = {}
options.request = function(intent, settle)
    different_ware_attempts[#different_ware_attempts + 1] = {intent = intent, settle = settle}
    return true
end
options.notice = function(status) notices[#notices + 1] = status end
local adapter = assert(module.install_x4(options))
menu.buttonConfirmTrade()
local rejected_attempt = different_ware_attempts[1]
reply(rejected_attempt, 'refused', {reason = 'price_changed', current_price_cents = 5877})
offer.ware, offer.price = 'energycells', 14.20
menu.buttonConfirmTrade()
assert(#different_ware_attempts == 1 and notices[#notices] == 'fresh_offer_required')
menu.contextMenuData = fresh_context(rejected_context.orders)
menu.buttonConfirmTrade()
assert(#different_ware_attempts == 1 and notices[#notices] == 'fresh_offer_required')
menu.contextMenuData = fresh_context()
menu.buttonConfirmTrade()
assert(#different_ware_attempts == 2 and different_ware_attempts[2].intent.ware == 'energycells')
assert(different_ware_attempts[2].intent.price_cents == 1420 and notices[#notices] == 'pending')
reply(rejected_attempt, 'confirmed', {settled_total_cents = 34900})
assert(notices[#notices] == 'pending')
reply(different_ware_attempts[2], 'confirmed', {settled_total_cents = 8500})
assert(notices[#notices] == 'confirmed' and adapter.detach() and native_calls == 4)

-- The live bridge intercepts the selected ship at its actual berth and assigns
-- independent stock baskets only after a conclusive receipt.
local live_attempts = {}
local live_capture = assert(module.install_x4({
    enabled = true, dynamic_station = true, menu = menu, ship = ship_id,
    notice = function(status) notices[#notices + 1] = status end,
    request = function(intent, settle)
        live_attempts[#live_attempts + 1] = {intent = intent, settle = settle}
        return true
    end,
}))
local originals = native_calls
menu.contextMenuData = nil
menu.buttonConfirmTrade()
assert(notices[#notices] == 'stale_identity' and native_calls == originals)
menu.contextMenuData = fresh_context()
menu.contextMenuData.component = other_station_id
menu.buttonConfirmTrade()
assert(notices[#notices] == 'stale_dock' and #live_attempts == 0 and native_calls == originals)
menu.contextMenuData = fresh_context()
local first_context = menu.contextMenuData
offer.price = 58.19
menu.buttonConfirmTrade()
assert(#live_attempts == 1 and live_attempts[1].intent.station_code == 'BIM-169')
reply(live_attempts[1], 'refused', {reason = 'price_changed', current_price_cents = 5800})
offer.price = 58
menu.buttonConfirmTrade()
assert(#live_attempts == 1)
menu.contextMenuData = fresh_context()
menu.buttonConfirmTrade()
assert(#live_attempts == 2 and live_attempts[2].intent.price_cents == 5800)
reply(live_attempts[1], 'confirmed', {settled_total_cents = 34900, company_balance_cents = 965100,
    ship_code = 'WRN-302', cargo_units = 6})
assert(notices[#notices] == 'pending')
local second_context = menu.contextMenuData
reply(live_attempts[2], 'confirmed', {settled_total_cents = 34800, company_balance_cents = 965200,
    ship_code = 'WRN-302', cargo_units = 6})
assert(notices[#notices] == 'confirmed')
menu.buttonConfirmTrade()
menu.contextMenuData = first_context
menu.buttonConfirmTrade()
assert(#live_attempts == 2 and notices[#notices] == 'already_attempted')
menu.contextMenuData = fresh_context()
menu.buttonConfirmTrade()
assert(#live_attempts == 3)
reply(live_attempts[3], 'confirmed', {settled_total_cents = 34800, company_balance_cents = 965200,
    ship_code = 'WOO-831', cargo_units = 6})
assert(notices[#notices] == 'uncertain')
menu.contextMenuData = second_context
menu.buttonConfirmTrade()
assert(#live_attempts == 3 and native_calls == originals)
assert(live_capture.detach())

-- After a conclusive pre-booking refusal only a fresh menu and basket may submit again.
local retry_attempts = {}
local retry_capture = assert(module.install_x4({
    enabled = true, dynamic_station = true, menu = menu, ship = ship_id,
    notice = function(status) notices[#notices + 1] = status end,
    request = function(intent, settle)
        retry_attempts[#retry_attempts + 1] = {intent = intent, settle = settle}
        return true
    end,
}))
for attempt_index, reason in ipairs({'captain_required', 'dock_mismatch', 'provider_busy'}) do
    menu.contextMenuData = fresh_context()
    menu.buttonConfirmTrade()
    assert(#retry_attempts == attempt_index)
    local refused_basket = menu.contextMenuData.orders
    reply(retry_attempts[attempt_index], 'refused', {reason = reason})
    assert(notices[#notices] == reason)
    menu.buttonConfirmTrade()
    menu.contextMenuData = fresh_context(refused_basket)
    menu.buttonConfirmTrade()
    assert(#retry_attempts == attempt_index and notices[#notices] == 'already_attempted')
end
menu.contextMenuData = fresh_context()
menu.buttonConfirmTrade()
reply(retry_attempts[4], 'unknown', {})
menu.contextMenuData = fresh_context()
menu.buttonConfirmTrade()
assert(#retry_attempts == 4 and native_calls == originals and retry_capture.detach())

-- The stale fixture's native basket is observed as displayed, rather than
-- derived from the quoted unit price or from a later native debit.
local pilot_result
local reported = assert(module.install_x4({
    enabled = true, dynamic_station = true, menu = menu, ship = ship_id,
    request = function(intent, settle, displayed_total)
        assert(displayed_total == 34914 and intent.quantity == 6)
        pilot_result = {intent = intent, settle = settle}
        return true
    end,
}))
menu.contextMenuData = fresh_context()
menu.contextMenuData.totalsellcost = 349.14
menu.buttonConfirmTrade()
reply(pilot_result, 'accepted_pending_statement', {})
local waiting = 'Company purchase accepted. Awaiting reconciliation with your purchase record.'
assert(lines[#lines] == waiting)
reply(pilot_result, 'accepted_pending_statement', {})
assert(lines[#lines] == waiting and lines[#lines - 1] ~= waiting)
reply(pilot_result, 'pilot_reported', {settled_total_cents = 34914})
assert(lines[#lines] == 'Company purchase recorded from your report: 349.14 Cr.')
assert(reported.detach())

assert(module.show_recovered_purchase({quantity = 6, ware = 'water', station_code = 'BIM-169',
    settled_total_cents = 34914}))
assert(lines[#lines] == 'Earlier Company purchase recorded from your report. 6 water at BIM-169 for 349.14 Cr.')
