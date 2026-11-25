local ffi = require('ffi')
local survey = dofile('tools/dev/authority_native_market_survey.lua')
dofile('tools/dev/authority_registry_resolve_string.lua')
local lease, epoch = string.rep('a', 32), string.rep('e', 32)
local first, second, third, fourth = string.rep('b', 32), string.rep('c', 32), string.rep('d', 32), string.rep('f', 32)
local input, sent, now, requests, callback = {}, {}, 100, {}, nil
local net = {
    pending = function() return #input > 0 end,
    receive = function() return table.remove(input, 1) end,
    send = function(_, packet)
        assert(#packet <= 768)
        sent[#sent + 1] = packet
    end,
}
local ship = X4LAN_RESOLVE_STRING.parse_hex('0x1')
local station = X4LAN_RESOLVE_STRING.parse_hex('0x2')
local physical = {water = 2}
local calls = {market = 0, cargo = 0, free = 0, sell = 0, buy = 0}
local ware = string.rep('w', 128)
local offers = {
    {id = 1, ware = 'water', price = 58.48, amount = 80, minamount = 1, isselloffer = true},
    {id = 2, ware = 'water', price = 53.25, amount = 31, minamount = 2, isbuyoffer = true},
    {id = 3, ware = 'water', price = 58.51, amount = 22, minamount = 1, isselloffer = true},
    {id = 4, ware = 'water', price = 10.22, amount = 1, minamount = 1, isselloffer = true, ismissionoffer = true},
    {id = 5, ware = 'water', price = 92.55, amount = 3, minamount = 2, isbuyoffer = true, stale = true},
    {id = 19, ware = 'energycells', price = 13, amount = 120, minamount = 1, isbuyoffer = true},
    {id = 20, ware = 'water', price = 51, amount = 120, minamount = 1, isbuyoffer = true},
    {id = 6, ware = 'defencedrone', price = 153, amount = 18, minamount = 1, isselloffer = true, ammotypename = 'unit'},
}
for i = 1, 12 do
    offers[#offers + 1] = {id = 6 + i, ware = ware, price = 31 + i, amount = 120, minamount = 1, isselloffer = true}
end

function RegisterEvent(name, fn)
    assert(name == 'X4AuthorityMarketSurvey.Result')
    callback = fn
end

function AddUITriggeredEvent(screen, control, request)
    assert(screen == 'X4AuthorityMarketSurvey' and control == 'Read')
    assert(request.epoch == epoch and request.index == 7354 and request.ship_code == 'WRN-302')
    assert(request.station_code == 'BIM-169' and request.sector == 'cluster_01_sector001_macro')
    requests[#requests + 1] = request.nonce
end

function ConvertStringToLuaID(value)
    if value == tostring(ship) then return 'ship' end
    if value == tostring(station) then return 'station' end
    error('unexpected conversion')
end

function ConvertIDTo64Bit(value)
    assert(value == 'station')
    return station
end

function GetTradeList(station_lua, ship_lua)
    assert(station_lua == 'station' and ship_lua == 'ship')
    calls.market = calls.market + 1
    return offers
end

function GetComponentData(ship_lua, property)
    assert(ship_lua == 'ship' and property == 'cargo')
    calls.cargo = calls.cargo + 1
    return physical
end

function GetCargoAfterTradeOrders()
    error('virtual cargo may be empty without a stock trade context')
end

function GetFreeCargoAfterTradeOrders(ship_lua, name)
    assert(ship_lua == 'ship')
    calls.free = calls.free + 1
    return name == 'water' and 21 or 13
end

function GetAmmoCountAfterTradeOrders()
    error('unverified equipment must be omitted without reading virtual stock')
end

function GetFreeUnitStorageAfterTradeOrders()
    error('equipment with unavailable physical stock must not reach the free-space read')
end

function CanTradeWith(id, ship_lua, minimum, buying)
    assert(ship_lua == 'ship' and id ~= 19)
    if id == 2 or id == 20 then
        assert(buying == nil and minimum == (id == 2 and 2 or 1))
        calls.sell = calls.sell + 1
        if id == 20 then return false end
    else
        assert(buying == true and minimum == 1)
        calls.buy = calls.buy + 1
    end
    return ffi.new('bool', id ~= 3)
end

function GetTradeData(id)
    assert(id ~= 4 and id ~= 5)
    return {station = 'station'}
end

local tick = survey.new(lease, net, function() return now end)
local function send(kind, id, tail, paused)
    input[#input + 1] = 'MARKET_' .. kind .. '|' .. lease .. '|' .. id .. '|' .. tail
    tick(paused or false)
end
local function open(id, serial, paused)
    send('OPEN', id, serial .. '|' .. epoch .. '|7354|WRN-302|cluster_01_sector001_macro|BIM-169', paused)
end
local function observe(id)
    callback(nil, id .. '|observed|0x1|0x2|976800')
end

open(first, 1, true)
assert(sent[1] == 'MARKET_HELLO|' .. lease and #requests == 0)
tick(false)
assert(#requests == 1 and requests[1] == first)
observe(second)
assert(calls.market == 0)
observe(first)
assert(sent[#sent] == 'MARKET_HEADER|' .. lease .. '|' .. first .. '|976800|17')
open(first, 1)
assert(#requests == 1 and calls.market == 1 and calls.cargo == 1)
local offset, pages, seen = 0, 0, {}
while offset < 17 do
    send('READ', first, tostring(offset))
    local prefix = 'MARKET_PAGE|' .. lease .. '|' .. first .. '|' .. offset .. '|'
    local page = sent[#sent]
    assert(page:sub(1, #prefix) == prefix)
    for row in page:sub(#prefix + 1):gmatch('[^|]+') do
        seen[row] = true
        offset = offset + 1
    end
    pages = pages + 1
end
assert(pages > 1)
assert(seen['buy,water,5848,80,1,2,21,1'] and seen['sell,water,5325,31,2,2,21,1'])
assert(seen['buy,water,5851,22,1,2,21,0'])
for row in pairs(seen) do assert(not row:find(',defencedrone,', 1, true)) end
assert(seen['buy,' .. ware .. ',3200,120,1,0,13,1'])
assert(seen['sell,energycells,1300,120,1,0,13,0'] and seen['sell,water,5100,120,1,2,21,0'])
assert(calls.market == 1 and calls.cargo == 1 and calls.free == 3 and calls.buy == 14 and calls.sell == 2)

open(second, 2)
open(first, 1)
assert(#requests == 2 and requests[2] == second)
observe(first)
assert(calls.market == 1)
callback(nil, second .. '|dock_mismatch')
assert(sent[#sent] == 'MARKET_FAIL|' .. lease .. '|' .. second .. '|dock_mismatch')

open(third, 3)
offers[1].price = 58.481
observe(third)
assert(sent[#sent] == 'MARKET_FAIL|' .. lease .. '|' .. third .. '|invalid_observation')

open(fourth, 4)
offers = {}
for i = 1, 513 do offers[i] = {ismissionoffer = true} end
observe(fourth)
assert(sent[#sent] == 'MARKET_FAIL|' .. lease .. '|' .. fourth .. '|market_limit')

-- An unfamiliar list-shaped inventory is an invalid observation, not an empty ship.
local fifth = string.rep('1', 32)
physical = {{ware = 'water', amount = 4}}
open(fifth, 5)
observe(fifth)
assert(sent[#sent] == 'MARKET_FAIL|' .. lease .. '|' .. fifth .. '|invalid_observation')

-- A fresh view uses the new absolute physical count after a confirmed delivery.
local sixth = string.rep('2', 32)
physical = {water = 4}
offers = {{id = 1, ware = 'water', price = 58.48, amount = 80, minamount = 1, isselloffer = true}}
open(sixth, 6)
observe(sixth)
send('READ', sixth, '0')
assert(sent[#sent] == 'MARKET_PAGE|' .. lease .. '|' .. sixth .. '|0|buy,water,5848,80,1,4,21,1')

-- The installed stock deployable inventory enumerates these five independent
-- classes by ware; the count for another ware must not leak into the offer.
local C = {}
for _, name in ipairs({'Satellites', 'NavBeacons', 'ResourceProbes', 'LaserTowers', 'Mines'}) do
    C['GetNumAll' .. name] = function(actor)
        assert(actor == ship)
        return name == 'Satellites' and 2 or 0
    end
end
C.GetAllSatellites = function(rows, count, actor)
    assert(count == 2 and actor == ship)
    rows[0].ware, rows[0].amount = 'satellite_mk1', 7
    rows[1].ware, rows[1].amount = 'satellite_mk2', 3
    return 2
end
C.GetFreeDeployableStorageAfterTradeOrders = function(actor)
    assert(actor == ship)
    return 10
end
package.loaded.ffi = setmetatable({C = C}, {__index = ffi})
tick = survey.new(lease, net, function() return now end)
package.loaded.ffi = ffi
offers = {
    {id = 1, ware = 'satellite_mk2', price = 120, amount = 25, minamount = 1,
        isselloffer = true, ammotypename = 'deployable'},
    {id = 7, ware = 'resourceprobe_01', price = 80, amount = 15, minamount = 1,
        isselloffer = true, ammotypename = 'deployable'},
}
local seventh = string.rep('3', 32)
open(seventh, 7)
observe(seventh)
send('READ', seventh, '0')
assert(sent[#sent] == 'MARKET_PAGE|' .. lease .. '|' .. seventh
    .. '|0|buy,resourceprobe_01,8000,15,1,0,10,1|buy,satellite_mk2,12000,25,1,3,10,1')
