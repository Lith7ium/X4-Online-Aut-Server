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
local completion, ledger_read_at, entry
local preparation, advance_reset, reset_applied
local previous, previous_deal, previous_observed
local balance, order_checks = 1000000, 0
local sale_mode, sale_readbacks = arg[4] == 'sale', 0
local sale_time
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
    assert(name == screen)
    if action == 'Prepare' then
        assert(not preparation and not reset_applied)
        preparation = request.request_id
        -- X4 resets only after the current action block. An old callback is
        -- deliberately emitted while the new reset is still pending.
        if previous then event('Armed', previous) end
        advance_reset = 3
        return true
    end
    if action == 'SaleReadback' then
        assert(sale_mode and request.request_id == previous and not ledger_read_at)
        sale_readbacks = sale_readbacks + 1
        if sale_readbacks == 1 then
            event('SaleTransfer', previous .. '|0x50|2|0|3473|' .. game_time)
        else
            event('SaleReadback', table.concat({previous, epoch, '0x10', '0x20', '0x30',
                game_time, 10, 3310, 937000, 937000, 6900}, '|'))
            if sale_readbacks == 3 then ledger_read_at = game_time + 1 end
        end
        return true
    end
    assert(action == (sale_mode and 'Sell' or 'Buy') and reset_applied == request.request_id)
    reset_applied = nil
    assert(request.live_request and request.fixed_request and request.provider_lease == lease)
    assert(request.ship_index == 7354 and request.ship_code == 'WRN-302')
    assert(request.sector == (sale_mode and 'cluster_43_sector001_macro' or 'cluster_01_sector001_macro'))
    assert(request.station_code == (sale_mode and 'CTD-093' or 'BIM-169'))
    assert(request.trial == (sale_mode and 'live_sale' or 'live_purchase'))
    assert(request.previous_confirmed == (previous or '-'))
    local id = request.request_id
    assert(not dispatched[id])
    dispatched[id] = true
    if request.epoch ~= epoch then
        event('Result', id .. '|invalid_registry|0')
        return true
    end
    local function join(...) return table.concat({...}, '|') end
    if sale_mode then
        assert(not previous and request.ware == 'water' and request.quantity == 2)
        game_time = game_time + 1
        event('Bound', id .. '|READY|0x10|0x20|0x30|' .. game_time)
        event('Result', id .. '|quote|3473')
        if request.price_cents ~= 3473 then
            event('Refused', join(id, 'price_changed', request.price_cents, 3473, 12, 12, 930100, 930100))
            event('Result', id .. '|price_changed|3473')
            return true
        end
        event('Before', join(id, epoch, '0x10', '0x20', '0x30', 'water', 2, 3473, 12,
            930100, 930100, game_time, 3308))
        event('Result', id .. '|submitted|3473')
        completion = function()
            game_time = game_time + 1
            sale_time, previous = game_time, id
            entry = {source='ship', time=game_time, buyer_id='32', seller_id='16', partner_id='32',
                ware='water', quantity=2, complete=true, entry_id='14', trade_id='5',
                eventtype='trade', trade_eventtype='complete', role='completed_trade',
                buyer_matches=true, seller_matches=true, money_cents='6900',
                price_cents='3473', linked=true}
            event('SaleComplete', join(id, epoch, '0x20', '0x10', 'water', '0x40', '0x50', game_time))
        end
        return true
    end
    local before, unit, debit, trade_id
    if previous then
        assert(order_checks == 3)
        assert(request.ware == 'energycells' and request.quantity == 3)
        before, unit, debit, trade_id = 5, 2719, 8100, '76'
    else
        assert(request.ware == 'water' and request.quantity == 6)
        before, unit, debit, trade_id = 12, 5819, 34800, '75'
    end
    game_time = game_time + 1
    local bound_at = ('%.3f'):format(game_time)
    event('Bound', id .. '|READY|0x10|0x20|0x30|' .. bound_at)
    event('Result', id .. '|quote|' .. unit)
    if request.price_cents ~= unit then
        assert(not previous and request.price_cents == 5848)
        event('Refused', id .. '|price_changed|5848|5819|12|12|1000000|1000000')
        event('Result', id .. '|price_changed|5819')
        return true
    end
    if previous then
        -- An old native event and an old wire receipt both arrive during the
        -- new request. Neither belongs to the active purchase.
        event('Deal', previous_deal)
        event('Observed', previous_observed)
        net:send('RECEIPT|' .. lease .. '|' .. previous .. '|9|DONE')
        net:send('RECEIPT|' .. lease .. '|' .. previous .. '|9|DONE')
    end
    local ware, quantity = request.ware, request.quantity
    event('Before', join(id, epoch, '0x10', '0x20', '0x30', ware, quantity, unit,
        before, balance, balance, bound_at))
    event('Result', id .. '|submitted|' .. unit)
    completion = function()
        game_time = game_time + 1
        local after_balance = balance - debit
        local deal = join(id, epoch, 'live_purchase', quantity, unit, bound_at,
            ('%.3f'):format(game_time), '0x10', '0x20', ware, quantity, 0, unit, unit * quantity,
            balance, after_balance, 1, 1, balance, after_balance, before, before + quantity)
        local observed = join(id, unit, quantity, 0, 0, 0, -debit, 1, 1)
        previous, previous_deal, previous_observed = id, deal, observed
        balance = after_balance
        entry = {source='ship', time=game_time, buyer_id='16', seller_id='32', partner_id='32',
            ware=ware, quantity=quantity, complete=true, entry_id=trade_id, trade_id=trade_id,
            eventtype='trade', trade_eventtype='complete', role='completed_trade',
            buyer_matches=true, seller_matches=true, money_cents=tostring(-debit),
            price_cents=tostring(unit), linked=true}
        ledger_read_at = game_time + 1
        event('Deal', deal)
        event('Observed', observed)
    end
    return true
end

X4LAN_AUTHORITY_TRADE_LEDGER = {
    ship_orders_idle=function(ship)
        assert(ship == '0x10')
        order_checks = order_checks + 1
        return order_checks >= 3
    end,
    collect=function(request)
        assert(request.start_time < entry.time and request.end_time >= ledger_read_at)
        assert(request.ship == '0x10' and request.station == '0x20'
            and request.ware == entry.ware and request.quantity == entry.quantity)
        assert(request.side == (sale_mode and 'sell' or nil))
        return {status='observed', trade_id=entry.trade_id, rows={entry}}
    end,
}
local tick = module.new(lease, net)
for frame = 1, 3000 do
    real_time = frame / 200
    if preparation then
        advance_reset = advance_reset - 1
        if advance_reset == 0 then
            reset_applied, preparation = preparation, nil
            event('Armed', reset_applied)
        end
    elseif completion then
        local fn = completion
        completion = nil
        fn()
    elseif ledger_read_at then
        game_time = ledger_read_at
    elseif sale_time then
        game_time = game_time + 1
    end
    tick(false)
    ffi.C.usleep(5000)
end
