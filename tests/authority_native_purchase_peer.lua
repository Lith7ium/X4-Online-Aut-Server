-- Real native UDP, with X4's UI and MD events doubled.
local port, lease, library = tonumber(arg[1]), arg[2], arg[3]
local native = assert(package.loadlib(library, 'luaopen_x4lan_net'))()
local net = assert(native.open('127.0.0.1', port))
local ffi = require('ffi')
ffi.cdef('int usleep(unsigned int usec);')
local game_time, real_time = 10, 0
package.loaded.ffi = {C={GetCurrentGameTime=function() return game_time end}}
local module = dofile('tools/dev/authority_native_purchase.lua')
package.loaded.ffi = ffi

local callbacks, dispatched = {}, {}
local completion
local screen = 'X4AuthorityCompanyStockTrade'
local epoch = string.rep('e', 32)

function RegisterEvent(name, action)
    assert(not callbacks[name])
    callbacks[name] = action
end

function GetCurRealTime() return real_time end
function DebugError(_) end

local function event(name, row) callbacks[screen .. '.' .. name](nil, row) end

function AddUITriggeredEvent(name, action, request)
    assert(name == screen and action == 'Buy')
    assert(request.live_request and request.fixed_request)
    assert(request.ship_index == 7354 and request.ship_code == 'WRN-302')
    assert(request.sector == 'cluster_01_sector001_macro' and request.station_code == 'BIM-169')
    assert(request.trial == 'live_purchase' and request.ware == 'water' and request.quantity == 6)
    local id = request.request_id
    assert(not dispatched[id])
    dispatched[id] = true
    if request.epoch ~= epoch then
        event('Result', id .. '|invalid_registry|0')
        return true
    end
    game_time = 11
    event('Bound', id .. '|READY|0x10|0x20|0x30|11.000')
    event('Result', id .. '|quote|5819')
    if request.price_cents ~= 5819 then
        assert(request.price_cents == 5848)
        event('Refused', id .. '|price_changed|5848|5819|12|12|1000000|1000000')
        event('Result', id .. '|price_changed|5819')
        return true
    end
    event('Before', id .. '|' .. epoch
        .. '|0x10|0x20|0x30|water|6|5819|12|1000000|1000000|11.000')
    event('Result', id .. '|submitted|5819')
    completion = function()
        game_time = 12
        event('Deal', id .. '|' .. epoch .. '|live_purchase|6|5819|11.000|12.000'
            .. '|0x10|0x20|water|6|0|5819|34914|1000000|965200|1|1|1000000|965200|12|18')
        event('Observed', id .. '|5819|6|0|0|0|-34800|1|1')
    end
    return true
end

X4LAN_AUTHORITY_TRADE_LEDGER = {
    collect=function(request)
        assert(request.start_time <= 11 and request.end_time >= 13)
        assert(request.ship == '0x10' and request.station == '0x20'
            and request.ware == 'water' and request.quantity == 6)
        return {status='observed', trade_id='75', rows={{
            source='ship', time=12, buyer_id='16', seller_id='32', partner_id='32',
            ware='water', quantity=6, complete=true, entry_id='73', trade_id='75',
            eventtype='trade', trade_eventtype='complete', role='completed_trade',
            buyer_matches=true, seller_matches=true, money_cents='-34800', price_cents='5819', linked=true,
        }}}
    end,
}
local tick = module.new(lease, net)
for frame = 1, 3000 do
    real_time = frame / 200
    if completion then
        local fn = completion
        completion = nil
        fn()
    elseif game_time == 12 then
        game_time = 13
    end
    tick(false)
    ffi.C.usleep(5000)
end
