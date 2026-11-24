local game_time = 10
package.loaded.ffi = {C={GetCurrentGameTime=function() return game_time end}}
local module = dofile('tools/dev/authority_native_purchase.lua')
local callbacks, packets = {}, {}
local pending
local lease, epoch = string.rep('f', 32), string.rep('e', 32)
local screen = 'X4AuthorityCompanyStockTrade'
local processed = {}

function RegisterEvent(name, callback) callbacks[name] = callback end
function GetCurRealTime() return game_time end
function DebugError(_) end

local function event(name, row) callbacks[screen .. '.' .. name](nil, row) end

function AddUITriggeredEvent(name, action, request)
    assert(name == screen and action == 'Buy')
    assert(request.trial == 'live_purchase' and request.fixed_request and request.live_request)
    assert(request.epoch == epoch)
    assert(request.ship_index == 1073 and request.ship_code == 'HVC-42A')
    assert(request.sector == 'cluster_01_sector001_macro' and request.station_code == 'SLL-0X2')
    assert(request.ware == 'energycells' and request.quantity == 13)
    local id = request.request_id
    assert(not processed[id])
    processed[id] = true
    if request.price_cents ~= 2100 then
        event('Result', id .. '|price_changed|2100')
        return true
    end
    event('Bound', id .. '|READY|0x17|0x24|0x32|10.000')
    event('Result', id .. '|submitted|2100')
    return true
end

local journals = 0
X4LAN_AUTHORITY_TRADE_LEDGER = {
    collect=function(request)
        journals = journals + 1
        assert(request.ship == '0x17' and request.station == '0x24')
        assert(request.ware == 'energycells' and request.quantity == 13)
        return {status='observed', rows={}}
    end,
}
local tick = module.new(lease, {
    pending=function() return pending ~= nil end,
    receive=function()
        local next_packet = pending
        pending = nil
        return next_packet
    end,
    send=function(_, row) packets[#packets + 1] = row end,
})

local function buy(id, quantity, cents)
    pending = table.concat({'PURCHASE', lease, id, epoch, '1073',
        'HVC-42A', 'cluster_01_sector001_macro', 'SLL-0X2', 'energycells', quantity, cents}, '|')
    tick(false)
end

local first, second, third = string.rep('a', 32), string.rep('b', 32), string.rep('c', 32)
buy(first, 65536, 2000)
assert(not processed[first])
buy(first, 13, 2000)
assert(processed[first] and packets[#packets]:match('|DONE$'))
buy(first, 13, 2100)
buy(second, 13, 2100)
assert(processed[second])
game_time = 11
event('Observed', second .. '|2100|13|0|0|0|-27300|1|1')
game_time = 12
tick(false)
assert(journals == 1 and packets[#packets]:match('|DONE$'))
buy(third, 13, 2100)
assert(not processed[third] and packets[#packets - 1]:match('|provider_limit|0$'))
