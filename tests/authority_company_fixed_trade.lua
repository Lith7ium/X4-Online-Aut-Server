-- Exercise the actual private provider driver against synthetic MD replies.
local callbacks, logs = {}, {}
local epoch, nonce = 'epoch', 'request'
local now = 1
package.loaded.ffi = {C = {GetCurrentGameTime = function() return now end}}
function RegisterEvent(event, callback) callbacks[event] = callback end
function DebugError(row) logs[#logs + 1] = row:match('AUTH_TRADE_TRIAL request|(.+)$') end

local request = {sector = 'cluster_01_sector001_macro', station_code = 'BIM-169',
    ship_code = 'WRN-302', ware = 'water', quantity = 6, price_cents = 5877}
local screen = 'X4AuthorityCompanyStockTrade'
local scenario, buys, audits, quotes
local function emit(event, row) callbacks[screen .. '.' .. event](nil, row) end
function AddUITriggeredEvent(name, action, payload)
    local fixed = scenario ~= 'legacy'
    if action == 'Prepare' then
        assert((payload.prepare_berth == true) == fixed and payload.epoch == epoch and payload.nonce == nonce)
        callbacks[name .. '.Result'](nil, nonce .. '|READY|0x10|0x20|0x30|1')
        return true
    end
    assert(name == screen)
    if fixed then
        assert(payload.fixed_request == true and payload.price_cents == 5877
            and payload.sector == request.sector and payload.station_code == request.station_code
            and payload.ship_code == request.ship_code and payload.ware == request.ware
            and payload.quantity == request.quantity)
    else
        assert(payload.fixed_request == nil)
    end
    if action == 'Quote' then
        assert(not fixed and buys == 0)
        quotes = quotes + 1
        emit('Result', nonce .. '|quote|5877')
    else
        assert(action == 'Buy' and payload.price_cents == 5877)
        buys = buys + 1
        local current = scenario == 'success' and '5877' or (scenario == 'cheaper' and '5848' or '5878')
        if fixed and buys == 1 then emit('Result', nonce .. '|quote|' .. current) end
        if scenario == 'success' or scenario == 'legacy' then
            emit('Result', nonce .. (buys == 1 and '|submitted|5877' or '|already_attempted|0'))
        else
            assert(buys == 1)
            emit('Refused', nonce .. '|price_changed|5877|' .. current .. '|0|'
                .. (scenario == 'bad_evidence' and '1' or '0') .. '|1000000|1000000')
            emit('Result', nonce .. '|price_changed|' .. current)
        end
    end
    return true
end
X4LAN_AUTHORITY_TRADE_LEDGER = {collect = function(args)
    assert((scenario == 'success' or scenario == 'legacy') and args.quantity == 6 and args.ship == '0x10' and args.station == '0x20')
    audits = audits + 1
    return {status = 'observed', trade_id = '8', rows = {}}
end}

dofile('tools/dev/authority_company_trade_driver.lua')
for _, name in ipairs({'stale', 'cheaper', 'bad_evidence', 'success', 'legacy'}) do
    scenario, logs, buys, audits, quotes = name, {}, 0, 0, 0
    local tick = X4LAN_COMPANY_TRADE_DRIVER.new(epoch, nonce, 'six_unit', name ~= 'legacy' and request or nil)
    tick(true)
    assert(#logs == 0)
    tick(false)
    assert(buys == 1 and quotes == (name == 'legacy' and 1 or 0))
    if name == 'stale' or name == 'cheaper' then
        local current = name == 'stale' and '5878' or '5848'
        assert(logs[#logs - 1] == 'REFUSED|request|price_changed|5877|' .. current .. '|0|0|1000000|1000000'
            and logs[#logs] == 'DONE' and buys == 1 and audits == 0)
    elseif name == 'bad_evidence' then
        assert(table.concat(logs, '\n'):find('FAILED|refusal_unverified', 1, true)
            and logs[#logs] ~= 'DONE' and buys == 1 and audits == 0)
    else
        emit('Deal', nonce .. '|deal')
        emit('Observed', nonce .. '|5877|6|-6|0|0|-34900|1|1')
        now = now + 2
        tick(false)
        assert(logs[#logs - 1]:match('^LEDGER|observed|') and logs[#logs] == 'DONE'
            and buys == 2 and audits == 1)
        local quote_rows = 0
        for _, row in ipairs(logs) do
            if row == 'RESULT|request|quote|5877' then quote_rows = quote_rows + 1 end
        end
        assert(quote_rows == 1)
    end
end

request.station_code = 'another station'
assert(not pcall(X4LAN_COMPANY_TRADE_DRIVER.new, epoch, nonce, 'six_unit', request))
