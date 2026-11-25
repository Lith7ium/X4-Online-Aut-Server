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
local calls = {market = 0, cargo = 0, free = 0}
local ware = string.rep('w', 128)
local offers = {
    {id = 1, ware = 'water', price = 58.48, amount = 80, minamount = 1, isselloffer = true},
    {id = 2, ware = 'water', price = 53.25, amount = 31, minamount = 2, isbuyoffer = true},
    {id = 3, ware = 'water', price = 58.51, amount = 22, minamount = 1, isselloffer = true},
    {id = 4, ware = 'water', price = 10.22, amount = 1, minamount = 1, isselloffer = true, ismissionoffer = true},
    {id = 5, ware = 'water', price = 92.55, amount = 3, minamount = 2, isbuyoffer = true, stale = true},
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

function GetCargoAfterTradeOrders(ship_lua)
    assert(ship_lua == 'ship')
    calls.cargo = calls.cargo + 1
    return {water = 4}
end

function GetFreeCargoAfterTradeOrders(ship_lua, name)
    assert(ship_lua == 'ship')
    calls.free = calls.free + 1
    return name == 'water' and 21 or 13
end

function GetAmmoCountAfterTradeOrders(ship_lua)
    assert(ship_lua == 'ship')
    return {defencedrone = 2}
end

function GetFreeUnitStorageAfterTradeOrders(ship_lua)
    assert(ship_lua == 'ship')
    return 3
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
assert(sent[#sent] == 'MARKET_HEADER|' .. lease .. '|' .. first .. '|976800|16')
open(first, 1)
assert(#requests == 1 and calls.market == 1 and calls.cargo == 1)
local offset, pages, seen = 0, 0, {}
while offset < 16 do
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
assert(seen['buy,water,5848,80,1,4,21'] and seen['sell,water,5325,31,2,4,21'])
assert(seen['buy,water,5851,22,1,4,21'] and seen['buy,defencedrone,15300,18,1,2,3'])
assert(seen['buy,' .. ware .. ',3200,120,1,0,13'])
assert(calls.market == 1 and calls.cargo == 1 and calls.free == 2)

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
