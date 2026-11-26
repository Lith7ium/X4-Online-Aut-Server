-- Connect the selected local cockpit to its participant's private purchase queue.
local M = {}
local seat
local max_order_integer = 2147483647
local max_exact_integer = 9007199254740991
local ship_code = '^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$'
local booking_refusals = {
    captain_required = true, dock_mismatch = true, provider_busy = true,
    no_unique_offer = true, not_tradeable = true,
}

function M.arm(verified_seat)
    assert(seat == nil or seat == verified_seat)
    seat = verified_seat
end

local function fields(packet)
    local values = {}
    for field in (packet .. '|'):gmatch('(.-)|') do values[#values + 1] = field end
    return values
end

local function exact_decimal(field, minimum)
    if not field:match('^%d+$') or #field > 16 then return end
    local number = tonumber(field)
    if number >= minimum and number <= max_exact_integer then return number end
end

local function canonical_decimal(field, minimum, maximum)
    if #field > 1 and field:sub(1, 1) == '0' then return end
    local number = exact_decimal(field, minimum)
    if number and number <= maximum then return number end
end

local function is_sale_id(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function decode_notice(row)
    if #row ~= 7 or #row[3] ~= 32 or not row[3]:match('^[0-9a-f]+$')
            or #row[4] > 128 or not row[4]:match('^[a-z][a-z0-9_]*$')
            or not row[6]:match(ship_code) then return end
    local serial = canonical_decimal(row[2], 1, max_exact_integer)
    local quantity = canonical_decimal(row[5], 1, max_order_integer)
    local total = canonical_decimal(row[7], 0, max_exact_integer)
    if serial and quantity and total then
        return serial, {purchase_id = row[3], ware = row[4], quantity = quantity,
            station_code = row[6], settled_total_cents = total}
    end
end

local function market_client(options, prefix)
    local serial = 0
    local view
    local api = {}
    local function send(...)
        options.net:send(prefix .. table.concat({...}, '|'))
    end

    function api:close()
        if view then
            if view.deadline then send('MARKET_CLOSE', view.id) end
            view.status, view.rows, view = 'unavailable', nil, nil
        end
    end

    function api:open(dock)
        self:close()
        if serial >= 4294967295 or not options.available() then return end
        if type(dock.sector) ~= 'string' or #dock.sector > 128 or not dock.sector:match('^[A-Za-z0-9_]+$')
                or type(dock.station_code) ~= 'string' or not dock.station_code:match(ship_code) then return end
        serial = serial + 1
        view = {id = options.lease:sub(1, 24) .. string.format('%08x', serial),
            serial = serial, sector = dock.sector, station = dock.station_code, offset = 0, rows = {}, page = {},
            next_send = 0, status = 'pending'}
        return view
    end

    local function fail()
        view.status, view.rows, view.page = 'unavailable', nil, nil
    end

    function api:receive(row)
        if not view or not view.deadline or view.status ~= 'pending'
                or row[2] ~= view.id or row[3] ~= tostring(view.offset) then return end
        if row[1] == 'MARKET_PAGE' then
            if #row == 5 and (row[4] == 'refused' or row[4] == 'unavailable') then
                if #row[5] <= 64 and row[5]:match('^[a-z][a-z0-9_]*$') then fail() end
                return
            end
            if (#row ~= 7 and (not options.sales or #row ~= 8)) or row[4] ~= 'observed' then return end
            local balance = canonical_decimal(row[5], 0, max_exact_integer)
            local total = canonical_decimal(row[6], view.offset, 4096)
            local count = canonical_decimal(row[7], 0, 8)
            if not balance or not total or not count or count ~= math.min(8, total - view.offset)
                    or (view.total and (view.total ~= total or view.balance ~= balance)) then
                fail()
                return
            end
            if row[8] and (not is_sale_id(row[8]) or view.offset + count ~= total
                    or (view.sale_id and view.sale_id ~= row[8])) then
                fail()
                return
            end
            view.total, view.balance, view.count = total, balance, count
            view.sale_id = row[8]
        elseif row[1] == 'MARKET_ROW' and #row == 12 then
            local index = canonical_decimal(row[4], 1, 8)
            local side, ware = row[5], row[6]
            local price = canonical_decimal(row[7], 1, max_exact_integer)
            local amount = canonical_decimal(row[8], 0, max_order_integer)
            local minimum = canonical_decimal(row[9], 0, max_order_integer)
            local cargo = canonical_decimal(row[10], 0, max_order_integer)
            local free = canonical_decimal(row[11], 0, max_order_integer)
            if not index or (side ~= 'buy' and side ~= 'sell') or #ware > 128
                    or not ware:match('^[a-z][a-z0-9_]*$') or not price or not amount
                    or not minimum or not cargo or not free or (row[12] ~= '0' and row[12] ~= '1') then
                fail()
                return
            end
            local encoded = table.concat(row, '|')
            if view.page[index] and view.page[index].encoded ~= encoded then
                fail()
                return
            end
            view.page[index] = {side = side, ware = ware, price_cents = price, amount = amount,
                min_amount = minimum, cargo_units = cargo, free_units = free, tradeable = row[12] == '1',
                encoded = encoded}
        end
        if not view.count then return end
        for index in pairs(view.page) do
            if index > view.count then fail(); return end
        end
        for index = 1, view.count do
            if not view.page[index] then return end
        end
        for index = 1, view.count do
            local offer = view.page[index]
            offer.encoded = nil
            view.rows[#view.rows + 1] = offer
        end
        if #view.rows == view.total then
            view.status, view.page = 'observed', nil
            return
        end
        view.offset, view.count, view.next_send, view.page = view.offset + 8, nil, 0, {}
        view.page_deadline = options.clock() + 8
    end

    function api:can_sell(expected_view)
        return view ~= nil and view == expected_view and view.status == 'observed'
            and view.sale_id ~= nil and options.clock() < view.deadline
    end

    function api:take_sale(intent)
        if not self:can_sell(view) or view.sector ~= intent.sector or view.station ~= intent.station_code then return end
        local match
        for _, offer in ipairs(view.rows) do
            if offer.side == 'sell' and offer.tradeable and offer.ware == intent.ware
                    and offer.price_cents == intent.price_cents then
                if match then return end
                match = offer
            end
        end
        if not match or intent.quantity < math.max(1, match.min_amount)
                or intent.quantity > math.min(65535, match.amount, match.cargo_units) then return end
        local sale_id = view.sale_id
        view.sale_id = nil
        return sale_id, view.id
    end

    function api:tick()
        if not view or view.status ~= 'pending' then return end
        local now = options.clock()
        if not options.available() then fail(); return end
        if not view.deadline then
            view.deadline, view.page_deadline = now + 90, now + 20
        elseif now >= view.deadline or now >= view.page_deadline then
            fail()
            return
        end
        if now >= view.next_send then
            view.next_send = now + 0.35
            send('MARKET', view.id, view.sector, view.station, view.offset)
        end
    end
    api.clock = options.clock
    return api
end

function M.new(options)
    local pending, sequence, next_send = nil, 0, 0
    local pending_sale, next_sale_send
    local running = false
    local last_notice_serial, last_notice_packet = 0, nil
    local next_notice_poll, next_notice_read = 0, 0
    local prefix = 'T|' .. options.lease .. '|'
    local api = {}
    local market = options.trade_view and market_client(options, prefix)
    api.market = market

    function api:request(intent, settle, displayed_total_cents)
        if not running or pending or pending_sale or sequence >= max_order_integer or not options.available()
                or #intent.sector > 128 or not intent.sector:match('^[A-Za-z0-9_]+$')
                or not intent.station_code:match(ship_code) or #intent.ware > 128
                or not intent.ware:match('^[a-z][a-z0-9_]*$') then return false end
        sequence = sequence + 1
        local packet = prefix .. table.concat({sequence, intent.sector, intent.station_code, intent.ware,
                                                intent.quantity, intent.price_cents,
                                                displayed_total_cents and string.format('%.0f', displayed_total_cents)
                                                    or '-'}, '|')
        pending = {intent = intent, settle = settle, packet = packet}
        next_send = 0
        return true
    end

    if options.sales and market then
        function api:request_sale(intent, settle)
            if not running or pending or pending_sale or not options.available()
                    or type(intent) ~= 'table' or type(settle) ~= 'function'
                    or type(intent.ship_code) ~= 'string' or not intent.ship_code:match(ship_code)
                    or type(intent.quantity) ~= 'number' or intent.quantity % 1 ~= 0
                    or type(intent.price_cents) ~= 'number' or intent.price_cents % 1 ~= 0
                    or intent.price_cents < 1 or intent.price_cents > max_order_integer then return false end
            local sale_id, view_id = market:take_sale(intent)
            if not sale_id then return false end
            local value = {sale_id=sale_id, sector=intent.sector, station_code=intent.station_code, ware=intent.ware,
                quantity=intent.quantity, price_cents=intent.price_cents, ship_code=intent.ship_code}
            local packet = prefix .. table.concat({'SALE', sale_id, view_id, value.sector, value.station_code,
                value.ware, value.quantity, value.price_cents, value.ship_code}, '|')
            pending_sale, next_sale_send = {intent=value, settle=settle, packet=packet}, 0
            return true, sale_id
        end
    end

    local function handle_sale(row)
        local intent = pending_sale.intent
        if row[2] ~= intent.sale_id or row[3] ~= intent.ship_code then return end
        local outcome = row[4]
        if outcome == 'confirmed' and #row == 7 then
            local credit = canonical_decimal(row[5], 1, max_exact_integer)
            local balance = canonical_decimal(row[6], 0, max_exact_integer)
            local cargo = canonical_decimal(row[7], 0, max_order_integer)
            if not credit or not balance or not cargo then return end
            intent.credited_cents, intent.company_balance_cents, intent.cargo_units = credit, balance, cargo
        elseif outcome == 'refused' and #row == 6 and row[5] == 'price_changed' then
            local price = canonical_decimal(row[6], 1, max_order_integer)
            if not price or price == intent.price_cents then return end
            intent.reason, intent.current_price_cents = 'price_changed', price
        elseif #row ~= 4 or (outcome ~= 'refused' and outcome ~= 'unknown') then
            return
        end
        intent.outcome = outcome
        local settle = pending_sale.settle
        pending_sale = nil
        settle(intent)
    end

    function api:set_running(value)
        running = value == true
        if market and not running then market:close() end
    end


    local function handle_notice(packet, row)
        local serial, notice = decode_notice(row)
        if not serial or serial < last_notice_serial then return end
        if serial == last_notice_serial then
            if packet ~= last_notice_packet then return end
        else
            local ok, shown = pcall(options.recovered_notice, notice)
            if not ok or shown ~= true then return end
            last_notice_serial, last_notice_packet = serial, packet
        end
        options.net:send(prefix .. 'NOTICE_ACK|' .. row[2] .. '|' .. row[3])
    end

    function api:tick()
        if not pending and not options.undock_result and not market then
            if not options.recovered_notice or not running then return end
            local now = options.clock()
            if now < next_notice_read then return end
            next_notice_read = now + 0.25
        end
        for _ = 1, 8 do
            local packet = options.net:receive()
            if not packet then break end
            if #packet <= 512 and packet:sub(1, #prefix) == prefix then
                local row = fields(packet:sub(#prefix + 1))
                if market and (row[1] == 'MARKET_PAGE' or row[1] == 'MARKET_ROW') then
                    market:receive(row)
                elseif row[1] == 'UNDOCK_RESULT' and options.undock_result then
                    options.undock_result(row)
                elseif row[1] == 'NOTICE' and options.recovered_notice and running and options.available() then
                    handle_notice(packet, row)
                elseif pending_sale and row[1] == 'SALE_RECEIPT' then
                    handle_sale(row)
                elseif pending and row[1] == 'RECEIPT' and row[2] == tostring(sequence) then
                    local receipt = {outcome = row[3]}
                    for key, value in pairs(pending.intent) do receipt[key] = value end
                    if row[3] == 'confirmed' and #row == 7 then
                        receipt.settled_total_cents = exact_decimal(row[4], 1)
                        receipt.company_balance_cents = exact_decimal(row[5], 0)
                        receipt.cargo_units = exact_decimal(row[6], pending.intent.quantity)
                        receipt.ship_code = row[7]
                        if not receipt.settled_total_cents or not receipt.company_balance_cents
                                or not receipt.cargo_units or not receipt.ship_code:match(ship_code) then
                            receipt = nil
                        end
                    elseif row[3] == 'refused' and #row == 5 and row[4] == 'price_changed' then
                        receipt.reason = row[4]
                        receipt.current_price_cents = exact_decimal(row[5], 1)
                        if not receipt.current_price_cents or receipt.current_price_cents == pending.intent.price_cents then
                            receipt = nil
                        end
                    elseif row[3] == 'refused' and #row == 4 and booking_refusals[row[4]] then
                        receipt.reason = row[4]
                    elseif row[3] == 'pilot_reported' and #row == 4 then
                        receipt.settled_total_cents = exact_decimal(row[4], 1)
                        if not receipt.settled_total_cents then receipt = nil end
                    elseif #row ~= 3 or (row[3] ~= 'refused' and row[3] ~= 'unknown'
                            and row[3] ~= 'accepted_pending_statement') then
                        receipt = nil
                    end
                    if receipt then
                        if receipt.outcome == 'accepted_pending_statement' then
                            if not pending.awaiting_statement then
                                pending.awaiting_statement = true
                                pending.settle(receipt)
                            end
                        else
                            local settle = pending.settle
                            pending = nil
                            settle(receipt)
                            return
                        end
                    end
                end
            end
        end
        local now = options.clock()
        -- The launcher retains one sale authorization. A newer market request
        -- must wait until the sale using the previous view receives a receipt.
        if market and running and not pending_sale then market:tick() end
        if options.recovered_notice and running and now >= next_notice_poll and options.available() then
            next_notice_poll = now + 4
            options.net:send(prefix .. 'NOTICE_POLL')
        end
        if pending_sale and running and options.available() and now >= next_sale_send then
            next_sale_send = now + 0.25
            options.net:send(pending_sale.packet)
        end
        if pending and running and options.available() and now >= next_send then
            next_send = now + (pending.awaiting_statement and 1 or 0.25)
            options.net:send(pending.packet)
        end
    end

    return api
end

function M.new_x4(config, pinned, udp)
    assert(type(config) == 'table' and config.epoch == pinned.epoch
        and type(config.index) == 'number' and config.index % 1 == 0 and config.index >= 1
        and config.index <= pinned.issued_count and pinned.operational_bits:sub(config.index, config.index) == '1'
        and type(config.lease) == 'string' and #config.lease == 32 and config.lease:match('^[0-9a-f]+$')
        and type(config.port) == 'number' and config.port % 1 == 0 and config.port >= 1 and config.port <= 65535)
    local selected
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[bool IsGamePaused(void); bool IsPlayerValid(void); UniverseID GetPlayerControlledShipID(void);]]
    local C = ffi.C
    local net = assert(udp.open('127.0.0.1', config.port))
    local function available()
        return selected and C.IsPlayerValid() and not C.IsGamePaused()
            and C.GetPlayerControlledShipID() == selected
            and X4LAN_RESOLVE_STRING.parse_hex(seat:controlled_actor()) == selected
    end
    local options = {net = net, lease = config.lease, clock = udp.monotonic, available = available,
        trade_view = config.trade_view == true, sales = config.sales == true}
    options.recovered_notice = function(notice)
        return X4LAN_COMPANY_TRADE_CAPTURE.show_recovered_purchase(notice)
    end
    local undock
    if config.undock_nonce then
        undock = assert(X4LAN_COMPANY_UNDOCK_HOLD.new_ipc(config, net, udp.monotonic, available))
        options.undock_result = function(row) undock:result(row) end
    end
    local source = M.new(options)
    local market_ui
    if undock or source.market then
        local set_running, tick = source.set_running, source.tick
        function source:set_running(value)
            set_running(self, value)
            if undock then undock:set_running(value) end
            if market_ui and value ~= true then market_ui:stop() end
        end
        if undock then
            function source:stop_undock() undock:stop() end
        end
        function source:tick()
            tick(self)
            if market_ui then market_ui:tick() end
            if undock then undock:tick(seat) end
        end
    end
    function source:prepare_seat()
        if selected then return true end
        local actor = seat and X4LAN_RESOLVE_STRING.parse_hex(seat:controlled_actor())
        if not actor or type(Menus) ~= 'table' then return false end
        local map
        for _, menu in ipairs(Menus) do
            if menu.name == 'MapMenu' then
                assert(not map, 'ambiguous Company purchase menu')
                map = menu
            end
        end
        if not map then return false end
        local capture = assert(X4LAN_COMPANY_TRADE_CAPTURE.install_x4({
            enabled = true, menu = map, ship = actor, dynamic_station = true, market = source.market,
            request_sale = source.request_sale and function(intent, settle)
                return source:request_sale(intent, settle)
            end,
            request = function(intent, settle, displayed_total_cents)
                return source:request(intent, settle, displayed_total_cents)
            end,
        }))
        market_ui = capture.market_ui
        selected = actor
        return true
    end
    return source
end

X4LAN_COMPANY_PURCHASE_SOURCE = M
return M
