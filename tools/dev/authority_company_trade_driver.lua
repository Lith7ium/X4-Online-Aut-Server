-- Runs only in the disposable provider trade trial. The MD cues own all game actions.
local M = {}

function M.new(epoch, nonce, trial, optional_request)
    assert(trial == 'one_unit' or trial == 'six_unit', 'unsupported_trade_trial')
    local quantity = trial == 'six_unit' and 6 or 1
    local fixed
    if optional_request ~= nil then
        local request = optional_request
        assert(type(request) == 'table' and request.sector == 'cluster_01_sector001_macro'
            and request.station_code == 'BIM-169' and request.ship_code == 'WRN-302'
            and request.ware == 'water' and request.quantity == quantity
            and type(request.price_cents) == 'number' and request.price_cents >= 1
            and request.price_cents <= 2147483647 and request.price_cents % 1 == 0,
            'invalid_fixed_trade_request')
        fixed = {sector = request.sector, station_code = request.station_code, ship_code = request.ship_code,
            ware = request.ware, quantity = request.quantity, price_cents = request.price_cents}
    end
    local phase = 'wait_for_resume'
    local quote, ship, station, start_time, completed_at
    local refused, refusal_price
    local prefix = '[X4LAN] AUTH_TRADE_TRIAL ' .. nonce .. '|'
    local screen = 'X4AuthorityCompanyStockTrade'

    local function report(text)
        DebugError(prefix .. text)
    end

    local function fail(reason)
        if phase == 'failed' then return end
        phase = 'failed'
        report('FAILED|' .. reason)
    end

    local function issue(action)
        local request = {request_id = nonce, epoch = epoch, trial = trial}
        if fixed then
            request.fixed_request = true
            for name, value in pairs(fixed) do request[name] = value end
        elseif action == 'Buy' then
            request.price_cents = quote
        end
        if AddUITriggeredEvent(screen, action, request) == false then fail('dispatch_' .. action) end
    end

    RegisterEvent('X4AuthorityTradeDock.Result', function(_, row)
        if type(row) ~= 'string' or row:match('^([^|]+)') ~= nonce then return end
        if phase ~= 'docking' then fail('unexpected_dock_reply'); return end
        report('DOCK|' .. row)
        if row:match('^[^|]+|([^|]+)|') ~= 'READY' then fail('dock_refused'); return end
        ship, station = row:match('^[^|]+|READY|([^|]+)|([^|]+)|')
        if fixed then
            phase = 'buying'
            start_time = require('ffi').C.GetCurrentGameTime()
            issue('Buy')
        else
            phase = 'quoting'
            issue('Quote')
        end
    end)

    local function finish_refusal()
        if not refused or not refusal_price then return end
        if refused.current ~= refusal_price or tonumber(refusal_price) ~= quote then
            fail('refusal_quote_mismatch')
            return
        end
        phase = 'done'
        report('REFUSED|' .. refused.row)
        report('DONE')
    end

    RegisterEvent(screen .. '.Refused', function(_, row)
        if type(row) ~= 'string' or row:match('^([^|]+)') ~= nonce then return end
        if not fixed or phase ~= 'buying' or refused then fail('unexpected_refusal'); return end
        local original, current, cargo_before, cargo_after, money_before, money_after = row:match(
            '^[^|]+|price_changed|(%d+)|(%d+)|(%d+)|(%d+)|(%d+)|(%d+)$')
        if tonumber(original) ~= fixed.price_cents or not current or tonumber(current) <= 0
                or current == original or cargo_before ~= cargo_after or money_before ~= money_after then
            fail('refusal_unverified')
            return
        end
        refused = {row = row, current = current}
        finish_refusal()
    end)

    RegisterEvent(screen .. '.Result', function(_, row)
        if type(row) ~= 'string' or row:match('^([^|]+)') ~= nonce then return end
        local status, price = row:match('^[^|]+|([%w_]+)|(%d+)$')
        report('RESULT|' .. row)
        if phase == 'buying' and fixed and status == 'quote' and not quote and tonumber(price) > 0 then
            quote = tonumber(price)
        elseif phase == 'quoting' and not fixed and status == 'quote' and tonumber(price) > 0 then
            quote = tonumber(price)
            phase = 'buying'
            start_time = require('ffi').C.GetCurrentGameTime()
            issue('Buy')
        elseif phase == 'buying' and status == 'price_changed' and fixed and not refusal_price then
            refusal_price = price
            finish_refusal()
        elseif phase == 'buying' and status == 'submitted' and tonumber(price) == quote
                and (not fixed or (quote == fixed.price_cents and not refused and not refusal_price)) then
            phase = 'settling'
        elseif phase == 'check_duplicate' and status == 'already_attempted' then
            phase = 'journal'
        else
            fail('unexpected_trade_reply')
        end
    end)

    RegisterEvent(screen .. '.Deal', function(_, row)
        if type(row) == 'string' and row:match('^([^|]+)') == nonce then report('DEAL|' .. row) end
    end)
    RegisterEvent(screen .. '.Observed', function(_, row)
        if type(row) ~= 'string' or row:match('^([^|]+)') ~= nonce then return end
        if phase ~= 'settling' then fail('unexpected_settlement'); return end
        report('OBSERVED|' .. row)
        completed_at = require('ffi').C.GetCurrentGameTime()
        phase = 'check_duplicate'
        issue('Buy')
    end)

    local next_read
    local fields = {'source', 'time', 'buyer_id', 'seller_id', 'partner_id', 'ware', 'quantity',
                    'complete', 'entry_id', 'trade_id', 'eventtype', 'trade_eventtype', 'role',
                    'buyer_matches', 'seller_matches', 'money_cents', 'price_cents', 'linked'}
    local function field(value)
        if type(value) == 'boolean' then return value and '1' or '0' end
        if type(value) == 'number' then return ('%.17g'):format(value) end
        if type(value) == 'string' and value ~= '' and not value:find('[|\r\n]') then return value end
        return '-'
    end
    local function journal()
        local now = require('ffi').C.GetCurrentGameTime()
        if now < (next_read or completed_at + 1) then return end
        local ok, result = pcall(X4LAN_AUTHORITY_TRADE_LEDGER.collect, {
            ship=ship, station=station, start_time=start_time, end_time=now, quantity=quantity, ware='water',
        })
        if not ok or type(result) ~= 'table' then
            result = {status='unknown', reason='collector_error', rows={}}
        end
        if result.status ~= 'observed' and now < math.min(completed_at+5, start_time+26) then
            next_read = now + 1
            return
        end
        report(table.concat({'LEDGER', field(result.status), field(result.reason), field(result.trade_id),
                             field(start_time), field(now)}, '|'))
        for _, row in ipairs(result.rows or {}) do
            local values = {'ENTRY'}
            for _, name in ipairs(fields) do values[#values + 1] = field(row[name]) end
            report(table.concat(values, '|'))
        end
        phase = 'done'
        report('DONE')
    end

    return function(paused)
        if phase == 'wait_for_resume' and not paused then
            phase = 'docking'
            report('START')
            if AddUITriggeredEvent('X4AuthorityTradeDock', 'Prepare', {nonce=nonce, epoch=epoch, prepare_berth=fixed and true or nil}) == false then
                fail('dispatch_dock')
            end
        elseif phase == 'journal' and not paused then
            journal()
        end
    end
end

X4LAN_COMPANY_TRADE_DRIVER = M
