-- Connect the selected local cockpit to its participant's private purchase queue.
local M = {}
local seat
local max_order_integer = 2147483647
local max_exact_integer = 9007199254740991
local ship_code = '^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$'
local booking_refusals = {captain_required = true, dock_mismatch = true, provider_busy = true}

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

function M.new(options)
    local pending, sequence, next_send = nil, 0, 0
    local running = false
    local last_notice_serial, last_notice_packet = 0, nil
    local next_notice_poll, next_notice_read = 0, 0
    local prefix = 'T|' .. options.lease .. '|'
    local api = {}

    function api:request(intent, settle, displayed_total_cents)
        if not running or pending or sequence >= max_order_integer or not options.available()
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

    function api:set_running(value)
        running = value == true
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
        if not pending and not options.undock_result then
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
                if row[1] == 'UNDOCK_RESULT' and options.undock_result then
                    options.undock_result(row)
                elseif row[1] == 'NOTICE' and options.recovered_notice and running and options.available() then
                    handle_notice(packet, row)
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
        if options.recovered_notice and running and now >= next_notice_poll and options.available() then
            next_notice_poll = now + 4
            options.net:send(prefix .. 'NOTICE_POLL')
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
    local options = {net = net, lease = config.lease, clock = udp.monotonic, available = available}
    options.recovered_notice = function(notice)
        return X4LAN_COMPANY_TRADE_CAPTURE.show_recovered_purchase(notice)
    end
    local undock
    if config.undock_nonce then
        undock = assert(X4LAN_COMPANY_UNDOCK_HOLD.new_ipc(config, net, udp.monotonic, available))
        options.undock_result = function(row) undock:result(row) end
    end
    local source = M.new(options)
    if undock then
        local set_running, tick = source.set_running, source.tick
        function source:set_running(value)
            set_running(self, value)
            undock:set_running(value)
        end
        function source:stop_undock()
            undock:stop()
        end
        function source:tick()
            tick(self)
            undock:tick(seat)
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
        assert(X4LAN_COMPANY_TRADE_CAPTURE.install_x4({
            enabled = true, menu = map, ship = actor, dynamic_station = true,
            request = function(intent, settle, displayed_total_cents)
                return source:request(intent, settle, displayed_total_cents)
            end,
        }))
        selected = actor
        return true
    end
    return source
end

X4LAN_COMPANY_PURCHASE_SOURCE = M
return M
