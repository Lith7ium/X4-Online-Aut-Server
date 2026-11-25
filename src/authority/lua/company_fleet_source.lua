-- Send a stock fleet intent to the owned participant sidecar and receive its result.
local M = {}
local screen = 'X4AuthorityCompanyFleetLookup'
local max_serial = 4294967295

local function lease(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$')
end

function M.new(o)
    local pending, serial, running = nil, 0, false
    local prefix = 'F|' .. o.lease .. '|'
    local api = {}

    function api:submit(intent)
        if not running or not o.available() or serial >= max_serial then
            o.notice('unavailable')
            return
        end
        if pending then
            o.notice('busy')
            return
        end
        serial = serial + 1
        local id = o.lease:sub(1, 24) .. ('%08x'):format(serial)
        pending = {id = id, intent = intent, lookup_deadline = o.clock() + 5}
        if not o.lookup(id, intent.component) then
            pending = nil
            o.notice('binding_unavailable', id)
        end
    end

    function api:binding(payload)
        if not pending or pending.packet or type(payload) ~= 'string' or #payload > 128 then return end
        local id, epoch, value, actor = payload:match('^([0-9a-f]+)|([0-9a-f]+)|(%d+)|([^|]+)$')
        if id ~= pending.id or epoch ~= o.epoch then return end
        local index = tonumber(value)
        local intent = pending.intent
        local ok, current = pcall(intent.current)
        if not running or not o.available() or o.clock() >= pending.lookup_deadline
                or #value > 6 or index < 1 or tostring(index) ~= value
                or not o.operational_index(index) or o.actor(actor) ~= intent.ship or not ok or current ~= true then
            pending = nil
            o.notice('binding_unavailable', id)
            return
        end
        pending.packet = prefix .. table.concat({'ORDER', id, value, intent.ship_code, intent.sector,
            ('%.17g'):format(intent.x), ('%.17g'):format(intent.y), ('%.17g'):format(intent.z)}, '|')
        pending.next_send, pending.deadline = 0, o.clock() + 30
    end

    function api:set_running(value)
        running = value == true
        if not running and pending then
            if not pending.sent then
                local id = pending.id
                pending = nil
                o.notice('unavailable', id)
            elseif not pending.timed_out then
                pending.timed_out = true
                o.notice('unknown', pending.id)
            end
        end
    end

    local function finish(packet)
        if not pending.sent or #packet > 256 or packet:sub(1, #prefix) ~= prefix then return false end
        local id, outcome, suffix = packet:sub(#prefix + 1):match('^RESULT|([0-9a-f]+)|([a-z]+)(.*)$')
        if id ~= pending.id then return false end
        local reason = suffix:match('^|([a-z][a-z0-9_]*)$')
        if not ((outcome == 'refused' and reason and #reason <= 64)
                or ((outcome == 'accepted' or outcome == 'unknown') and suffix == '')) then return false end
        local timed_out = pending.timed_out
        pending = nil
        if not timed_out or outcome ~= 'unknown' then o.notice(outcome, id, reason) end
        return true
    end

    function api:tick()
        if not pending then return end
        if pending.sent then
            for _ = 1, 8 do
                local packet = o.net:receive()
                if not packet then break end
                if finish(packet) then return end
            end
        end
        local now = o.clock()
        if not pending.packet then
            if now >= pending.lookup_deadline then
                local id = pending.id
                pending = nil
                o.notice('binding_unavailable', id)
            end
            return
        end
        if (not running or not o.available() or now >= pending.deadline) then
            if not pending.timed_out then
                local id = pending.id
                if pending.sent then
                    -- Late authority receipts can still settle the existing ID.
                    -- Hold it and stop dispatching rather than creating a replacement.
                    pending.timed_out = true
                    o.notice('unknown', id)
                else
                    pending = nil
                    o.notice('unavailable', id)
                end
            end
            return
        end
        if not pending.timed_out and now >= pending.next_send then
            if not pending.sent then
                local ok, current = pcall(pending.intent.current)
                if not ok or current ~= true then
                    local id = pending.id
                    pending = nil
                    o.notice('ship_unavailable', id)
                    return
                end
            end
            pending.next_send = now + 0.25
            if o.net:send(pending.packet) and not pending.sent then
                pending.sent = true
                o.notice('pending', pending.id)
            end
        end
    end

    return api
end

function M.new_x4(config, pinned, udp)
    assert(type(config) == 'table' and config.epoch == pinned.epoch and lease(config.epoch) and lease(config.lease)
        and type(config.port) == 'number' and config.port % 1 == 0 and config.port >= 1 and config.port <= 65535)
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[bool IsGamePaused(void); bool IsPlayerValid(void); UniverseID GetPlayerControlledShipID(void);]]
    local C, capture = ffi.C, X4LAN_COMPANY_FLEET_CAPTURE
    local seat, installed
    local function available()
        if not seat or X4LAN_AUTHORITY_SELECTED_SEAT ~= seat or not C.IsPlayerValid() or C.IsGamePaused() then
            return false
        end
        local actor = X4LAN_RESOLVE_STRING.parse_hex(seat:controlled_actor())
        return actor ~= nil and C.GetPlayerControlledShipID() == actor
    end
    local api = M.new({
        lease = config.lease, epoch = config.epoch, net = assert(udp.open('127.0.0.1', config.port)),
        clock = udp.monotonic, available = available, notice = capture.notice_x4,
        operational_index = function(index) return pinned.operational_bits:sub(index, index) == '1' end,
        actor = X4LAN_RESOLVE_STRING.parse_hex,
        lookup = function(id, ship)
            return AddUITriggeredEvent(screen, 'Lookup', {order_id = id, epoch = config.epoch, ship = ship}) ~= false
        end,
    })
    assert(RegisterEvent(screen .. '.Result', function(_, payload) api:binding(payload) end) ~= false)

    function api:prepare(admitted_seat)
        if admitted_seat then
            assert(not seat or seat == admitted_seat, 'Company fleet seat changed')
            seat = admitted_seat
        end
        if installed then return true end
        if type(Menus) ~= 'table' then return false end
        local map, interact
        for _, menu in ipairs(Menus) do
            if menu.name == 'MapMenu' then
                assert(not map, 'ambiguous Company fleet map menu')
                map = menu
            elseif menu.name == 'InteractMenu' then
                assert(not interact, 'ambiguous Company fleet interaction menu')
                interact = menu
            end
        end
        if not map or not interact then return false end
        installed = assert(capture.install_x4(map, interact, function(intent) api:submit(intent) end, available))
        return true
    end
    return api
end

X4LAN_COMPANY_FLEET_SOURCE = M
return M
