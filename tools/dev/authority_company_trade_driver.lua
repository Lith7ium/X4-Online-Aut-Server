-- Runs only in the disposable provider trade trial. The MD cues own all game actions.
local M = {}

function M.new(epoch, nonce)
    local phase = 'wait_for_resume'
    local quote
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
        local request = {request_id = nonce, epoch = epoch}
        if action == 'Buy' then request.price_cents = quote end
        if AddUITriggeredEvent(screen, action, request) == false then fail('dispatch_' .. action) end
    end

    RegisterEvent('X4AuthorityTradeDock.Result', function(_, row)
        if type(row) ~= 'string' or row:match('^([^|]+)') ~= nonce then return end
        if phase ~= 'docking' then fail('unexpected_dock_reply'); return end
        report('DOCK|' .. row)
        if row:match('^[^|]+|([^|]+)|') ~= 'READY' then fail('dock_refused'); return end
        phase = 'quoting'
        issue('Quote')
    end)

    RegisterEvent(screen .. '.Result', function(_, row)
        if type(row) ~= 'string' or row:match('^([^|]+)') ~= nonce then return end
        local status, price = row:match('^[^|]+|([%w_]+)|(%d+)$')
        report('RESULT|' .. row)
        if phase == 'quoting' and status == 'quote' and tonumber(price) > 0 then
            quote = tonumber(price)
            phase = 'buying'
            issue('Buy')
        elseif phase == 'buying' and status == 'submitted' and tonumber(price) == quote then
            phase = 'settling'
        elseif phase == 'check_duplicate' and status == 'already_attempted' then
            phase = 'done'
            report('DONE')
        else
            fail('unexpected_trade_reply')
        end
    end)

    RegisterEvent(screen .. '.Observed', function(_, row)
        if type(row) ~= 'string' or row:match('^([^|]+)') ~= nonce then return end
        if phase ~= 'settling' then fail('unexpected_settlement'); return end
        report('OBSERVED|' .. row)
        phase = 'check_duplicate'
        issue('Buy')
    end)

    return function(paused)
        if phase == 'wait_for_resume' and not paused then
            phase = 'docking'
            report('START')
            if AddUITriggeredEvent('X4AuthorityTradeDock', 'Prepare', {nonce=nonce, epoch=epoch}) == false then
                fail('dispatch_dock')
            end
        end
    end
end

X4LAN_COMPANY_TRADE_DRIVER = M
