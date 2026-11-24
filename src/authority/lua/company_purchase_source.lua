-- Connect the selected local cockpit to its participant's private purchase queue.
local M = {}
local seat

function M.arm(verified_seat)
    assert(seat == nil or seat == verified_seat)
    seat = verified_seat
end

local function fields(packet)
    local values = {}
    for field in (packet .. '|'):gmatch('(.-)|') do values[#values + 1] = field end
    return values
end

function M.new(options)
    local pending, sequence, next_send = nil, 0, 0
    local running = false
    local prefix = 'T|' .. options.lease .. '|'
    local api = {}

    function api:request(intent, settle)
        if not running or pending or sequence >= 2147483647 or not options.available() then return false end
        sequence = sequence + 1
        local packet = prefix .. table.concat({sequence, intent.sector, intent.station_code, intent.ware,
                                                intent.quantity, intent.price_cents}, '|')
        pending = {intent = intent, settle = settle, packet = packet}
        next_send = 0
        return true
    end

    function api:set_running(value)
        running = value == true
    end

    function api:tick()
        if not pending then return end
        for _ = 1, 8 do
            local packet = options.net:receive()
            if not packet then break end
            if #packet <= 512 and packet:sub(1, #prefix) == prefix then
                local row = fields(packet:sub(#prefix + 1))
                if row[1] == 'RECEIPT' and row[2] == tostring(sequence) then
                    local receipt = {outcome = row[3]}
                    for key, value in pairs(pending.intent) do receipt[key] = value end
                    if row[3] == 'confirmed' and #row == 7 then
                        receipt.settled_total_cents = tonumber(row[4])
                        receipt.company_balance_cents = tonumber(row[5])
                        receipt.cargo_units = tonumber(row[6])
                        receipt.ship_code = row[7]
                    elseif row[3] == 'refused' and #row == 5 and row[4] == 'price_changed' then
                        receipt.reason = row[4]
                        receipt.current_price_cents = tonumber(row[5])
                    elseif #row ~= 3 or (row[3] ~= 'refused' and row[3] ~= 'unknown') then
                        receipt = nil
                    end
                    local settle = pending.settle
                    pending = nil
                    settle(receipt)
                    return
                end
            end
        end
        local now = options.clock()
        if now >= next_send then
            next_send = now + 0.25
            options.net:send(pending.packet)
        end
    end

    return api
end

function M.new_x4(config, pinned, udp)
    assert(type(config) == 'table' and config.epoch == pinned.epoch and config.index <= pinned.issued_count
        and pinned.operational_bits:sub(config.index, config.index) == '1')
    local selected
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[bool IsGamePaused(void); bool IsPlayerValid(void); UniverseID GetPlayerControlledShipID(void);]]
    local C = ffi.C
    local net = assert(udp.open('127.0.0.1', config.port))
    local source = M.new({net = net, lease = config.lease, clock = udp.monotonic,
        available = function()
            return C.IsPlayerValid() and not C.IsGamePaused() and C.GetPlayerControlledShipID() == selected
        end})
    function source:prepare_seat()
        if selected then return true end
        local actor = seat and X4LAN_RESOLVE_STRING.parse_hex(seat:controlled_actor())
        if not actor then return false end
        local map
        for _, menu in ipairs(Menus) do
            if menu.name == 'MapMenu' then
                assert(not map, 'ambiguous Company purchase menu')
                map = menu
            end
        end
        assert(X4LAN_COMPANY_TRADE_CAPTURE.install_x4({
            enabled = true, menu = map, ship = actor, dynamic_station = true,
            request = function(intent, settle) return source:request(intent, settle) end,
        }))
        selected = actor
        return true
    end
    return source
end

X4LAN_COMPANY_PURCHASE_SOURCE = M
return M
