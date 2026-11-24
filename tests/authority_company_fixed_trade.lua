-- Exercise the actual private provider driver against synthetic MD replies.
local callbacks, logs = {}, {}
local epoch, nonce = 'epoch', 'request'
local now = 1
package.loaded.ffi = {C = {GetCurrentGameTime = function() return now end}}
function RegisterEvent(event, callback) callbacks[event] = callback end
function DebugError(row) logs[#logs + 1] = row:match('AUTH_TRADE_TRIAL request|(.+)$') end

local request = {sector = 'cluster_01_sector001_macro', station_code = 'BIM-169',
    ship_code = 'WRN-302', ware = 'water', quantity = 6, price_cents = 5819}
local screen = 'X4AuthorityCompanyStockTrade'
local scenario, buys, audits
local function emit(event, row) callbacks[screen .. '.' .. event](nil, row) end
function AddUITriggeredEvent(name, action, payload)
    if action == 'Prepare' then
        assert(payload.prepare_berth == true and payload.epoch == epoch and payload.nonce == nonce)
        callbacks[name .. '.Result'](nil, nonce .. '|READY|0x10|0x20|0x30|1')
        return true
    end
    assert(name == screen and payload.fixed_request == true and payload.price_cents == 5819
        and payload.sector == request.sector and payload.station_code == request.station_code
        and payload.ship_code == request.ship_code and payload.ware == request.ware
        and payload.quantity == request.quantity)
    if action == 'Quote' then
        assert(buys == 0)
        emit('Result', nonce .. (scenario == 'success' and '|quote|5819' or '|quote|5820'))
    else
        assert(action == 'Buy')
        buys = buys + 1
        if scenario == 'success' then
            emit('Result', nonce .. (buys == 1 and '|submitted|5819' or '|already_attempted|0'))
        else
            assert(buys == 1)
            emit('Refused', nonce .. '|price_changed|5819|5820|0|'
                .. (scenario == 'bad_evidence' and '1' or '0') .. '|1000000|1000000')
            emit('Result', nonce .. '|price_changed|5820')
        end
    end
    return true
end
X4LAN_AUTHORITY_TRADE_LEDGER = {collect = function(args)
    assert(scenario == 'success' and args.quantity == 6 and args.ship == '0x10' and args.station == '0x20')
    audits = audits + 1
    return {status = 'observed', trade_id = '8', rows = {}}
end}

dofile('tools/dev/authority_company_trade_driver.lua')
for _, name in ipairs({'stale', 'bad_evidence', 'success'}) do
    scenario, logs, buys, audits = name, {}, 0, 0
    local tick = X4LAN_COMPANY_TRADE_DRIVER.new(epoch, nonce, 'six_unit', request)
    tick(true)
    assert(#logs == 0)
    tick(false)
    if name == 'stale' then
        assert(logs[#logs - 1] == 'REFUSED|request|price_changed|5819|5820|0|0|1000000|1000000'
            and logs[#logs] == 'DONE' and buys == 1 and audits == 0)
    elseif name == 'bad_evidence' then
        assert(table.concat(logs, '\n'):find('FAILED|refusal_unverified', 1, true)
            and logs[#logs] ~= 'DONE' and buys == 1 and audits == 0)
    else
        emit('Deal', nonce .. '|deal')
        emit('Observed', nonce .. '|5819|6|-6|0|0|-34900|1|1')
        now = now + 2
        tick(false)
        assert(logs[#logs - 1]:match('^LEDGER|observed|') and logs[#logs] == 'DONE'
            and buys == 2 and audits == 1)
    end
end

request.station_code = 'another station'
assert(not pcall(X4LAN_COMPANY_TRADE_DRIVER.new, epoch, nonce, 'six_unit', request))
