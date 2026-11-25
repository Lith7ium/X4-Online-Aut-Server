-- Privately staged in an authority provider. Only the guarded MD Buy and Sell controls can book.
local M = {}
local C = require('ffi').C
local screen = 'X4AuthorityCompanyStockTrade'
local entry_fields = {'source', 'time', 'buyer_id', 'seller_id', 'partner_id', 'ware',
    'quantity', 'complete', 'entry_id', 'trade_id', 'eventtype', 'trade_eventtype',
    'role', 'buyer_matches', 'seller_matches', 'money_cents', 'price_cents', 'linked'}

local function positive_integer(value, maximum)
    if #value > 10 then return end
    local number = tonumber(value)
    if number >= 1 and number <= maximum then return number end
end

local function field(value)
    if type(value) == 'boolean' then return value and '1' or '0' end
    if type(value) == 'number' then return ('%.17g'):format(value) end
    if type(value) == 'string' and value ~= '' and not value:find('[|\r\n]') then return value end
    return '-'
end

function M.new(lease, net)
    local active, next_hello
    local seen = {}
    local attempted, completed

    local function send(id, sequence, row)
        DebugError('[X4LAN] AUTH_NATIVE_PURCHASE ' .. id .. '|' .. row)
        net:send('RECEIPT|' .. lease .. '|' .. id .. '|' .. sequence .. '|' .. row)
    end

    local function emit(row)
        active.sequence = active.sequence + 1
        send(active.id, active.sequence, row)
    end

    local function finish()
        emit('DONE')
        active = nil
    end

    local function matches(row)
        return active and type(row) == 'string' and row:match('^([^|]+)') == active.id
    end

    RegisterEvent(screen .. '.Armed', function(_, row)
        if active and active.preparing and not active.start and row == active.id then
            active.armed = true
        end
    end)

    RegisterEvent(screen .. '.Bound', function(_, row)
        if not matches(row) or active.ship then return end
        active.ship, active.station = row:match('^[^|]+|READY|([^|]+)|([^|]+)|')
        emit('DOCK|' .. row)
    end)
    RegisterEvent(screen .. '.Before', function(_, row)
        if matches(row) then emit('BEFORE|' .. row) end
    end)
    RegisterEvent(screen .. '.Result', function(_, row)
        if not matches(row) then return end
        local status = row:match('^[^|]+|([%a_]+)|')
        emit('RESULT|' .. row)
        if status == 'submitted' then
            attempted = active.id
            active.submitted = true
        elseif status ~= 'quote' then
            -- A missing order handle after create_trade_order is an uncertain effect.
            if status == 'order_refused' then attempted = active.id end
            finish()
        end
    end)
    RegisterEvent(screen .. '.Refused', function(_, row)
        if matches(row) then emit('REFUSED|' .. row) end
    end)
    RegisterEvent(screen .. '.Deal', function(_, row)
        if matches(row) then emit('DEAL|' .. row) end
    end)
    RegisterEvent(screen .. '.Observed', function(_, row)
        if not matches(row) or not active.submitted or active.observed then return end
        emit('OBSERVED|' .. row)
        active.observed = C.GetCurrentGameTime()
        active.next_read = active.observed + 1
    end)
    RegisterEvent(screen .. '.SaleComplete', function(_, row)
        if not matches(row) or not active.sell or not active.submitted or active.observed then return end
        emit('SALE_COMPLETE|' .. row)
        active.observed = C.GetCurrentGameTime()
        active.next_read = active.observed
    end)
    RegisterEvent(screen .. '.SaleTransfer', function(_, row)
        if not matches(row) or not active.sell or not active.observed or active.transferred then return end
        emit('SALE_TRANSFER|' .. row)
        active.transferred = true
    end)
    RegisterEvent(screen .. '.SaleReadback', function(_, row)
        if not matches(row) or not active.sell or not active.transferred or active.readbacks == 2 then return end
        emit('SALE_READBACK|' .. row)
        active.readbacks = active.readbacks + 1
    end)

    local function receive(real_time)
        if not net:pending() then return end
        local data = net:receive()
        if type(data) ~= 'string' then return end
        local verb, packet_lease, id, epoch, index, code, sector, station, ware, quantity, cents, previous =
            data:match('^([A-Z]+)|([0-9a-f]+)|([0-9a-f]+)|([0-9a-f]+)|(%d+)|([A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9])|([A-Za-z0-9_]+)|([A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9])|([a-z][a-z0-9_]*)|(%d+)|(%d+)|([0-9a-f%-]+)$')
        if (verb ~= 'PURCHASE' and verb ~= 'SALE') or packet_lease ~= lease
                or #id ~= 32 or #epoch ~= 32 or #sector > 128 or #ware > 128
                or active or seen[id] then return end
        index = positive_integer(index, 2147483647)
        quantity = positive_integer(quantity, 65535)
        cents = positive_integer(cents, 2147483647)
        if not index or not quantity or not cents then return end
        seen[id] = true
        -- The Lua journal is only evidence. Python includes this ID on a later
        -- request only after independent verification and a committed host ledger.
        local confirmed_id = completed and completed.id or '-'
        if previous ~= confirmed_id or (attempted and attempted ~= confirmed_id) then
            send(id, 1, 'RESULT|' .. id .. '|provider_limit|0')
            send(id, 2, 'DONE')
            return
        end
        active = {id=id, sequence=0, deadline=real_time + 15, sell=verb == 'SALE', readbacks=0}
        active.request = {
            request_id=id, epoch=epoch, ship_index=index, ship_code=code, sector=sector,
            station_code=station, ware=ware, quantity=quantity, price_cents=cents,
            trial=active.sell and 'live_sale' or 'live_purchase', fixed_request=true, live_request=true,
            provider_lease=lease, previous_confirmed=previous,
        }
    end

    local function prepare(real_time)
        if active.preparing then
            -- The MD callback is the readiness proof. Count neither the
            -- deferred reset nor its preparation time in the journal window.
            if real_time >= active.arm_deadline then
                emit('RESULT|' .. active.id .. '|provider_limit|0')
                finish()
            end
            return
        end
        local request = active.request
        if completed and request.epoch == completed.epoch and request.ship_index == completed.index
                and request.ship_code == completed.code then
            if real_time >= active.deadline then
                emit('RESULT|' .. active.id .. '|provider_busy|0')
                finish()
                return
            end
            if active.next_check and real_time < active.next_check then return end
            local ok, idle = pcall(X4LAN_AUTHORITY_TRADE_LEDGER.ship_orders_idle, completed.ship)
            if not ok or idle ~= true then
                active.next_check = real_time + .25
                return
            end
        end
        active.preparing, active.arm_deadline = true, real_time + 5
        local ok, result = pcall(AddUITriggeredEvent, screen, 'Prepare', request)
        if (not ok or result == false) and active then
            emit('RESULT|' .. active.id .. '|provider_limit|0')
            finish()
        end
    end

    local function dispatch()
        local id = active.id
        active.start = C.GetCurrentGameTime()
        local ok, result = pcall(AddUITriggeredEvent, screen, active.sell and 'Sell' or 'Buy', active.request)
        if (not ok or result == false) and active then
            attempted = active.id
            emit('RESULT|' .. id .. '|dispatch_uncertain|0')
            finish()
        end
    end

    local function journal()
        local now = C.GetCurrentGameTime()
        local cutoff = active.start + 30
        if now > cutoff then
            emit('LEDGER|unknown|window_expired|-|' .. field(active.start) .. '|' .. field(now))
            finish()
            return
        end
        if not active.next_read or now < active.next_read then return end
        if active.sell and active.readbacks < 2 then
            active.next_read = now + 1
            pcall(AddUITriggeredEvent, screen, 'SaleReadback', active.request)
            return
        end
        local ok, result = pcall(X4LAN_AUTHORITY_TRADE_LEDGER.collect, {
            ship=active.ship, station=active.station, start_time=active.start,
            end_time=now, quantity=active.request.quantity, ware=active.request.ware,
            side=active.sell and 'sell' or nil,
        })
        if not ok or type(result) ~= 'table' or type(result.rows) ~= 'table' then
            result = {status='unknown', reason='collector_error', rows={}}
        end
        if result.status ~= 'observed' and now < math.min(active.observed + (active.sell and 30 or 5), cutoff - 1) then
            active.next_read = now + 1
            return
        end
        emit(table.concat({'LEDGER', field(result.status), field(result.reason),
            field(result.trade_id), field(active.start), field(now)}, '|'))
        for _, row in ipairs(result.rows) do
            local values = {'ENTRY'}
            for _, name in ipairs(entry_fields) do values[#values + 1] = field(row[name]) end
            emit(table.concat(values, '|'))
        end
        if result.status == 'observed' then
            local request = active.request
            completed = {id=active.id, ship=active.ship, epoch=request.epoch,
                index=request.ship_index, code=request.ship_code}
        end
        finish()
    end

    return function(paused)
        local real_time = GetCurRealTime()
        if not next_hello or real_time >= next_hello then
            net:send('PURCHASE_HELLO|' .. lease)
            next_hello = real_time + .5
        end
        if paused then return end
        receive(real_time)
        if active and not active.start then
            if active.armed and real_time < active.arm_deadline then
                dispatch()
            else
                prepare(real_time)
            end
        end
        if active and active.submitted then journal() end
    end
end

X4LAN_AUTHORITY_NATIVE_PURCHASE = M
return M
