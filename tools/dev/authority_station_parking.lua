-- A single paused overlay update owns the test, without starting another callback loop.
local M = {}
local SCREEN = 'X4AuthorityStationParking'
local SHIPS = 'X4AuthorityStationParkingShips'
local HEX = '0[xX][0-9a-fA-F]+'
local CODE = '[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]'
local MACRO = '[%w_]+'

function M.new(o)
    local step = o.mode == 'prepare' and 'preflight' or 'readback'
    local wait_deadline, pending, station, destination, next_read
    local index = 1
    local function fail(reason)
        if step ~= 'failed' then
            step = 'failed'
            o.log('ERROR|' .. reason)
        end
        return false
    end
    local function valid()
        if o.guard() ~= true then return fail('PAUSE_OR_REGISTRY_LOST') end
        if wait_deadline and o.clock() >= wait_deadline then return fail('TIMEOUT') end
        return true
    end

    local function parking_event(row)
        if not valid() then return end
        if (pending ~= 'Read' and pending ~= 'Park') or type(row) ~= 'string' or #row > 450 then
            return fail('UNEXPECTED_PARKING_EVENT')
        end
        local nonce, op, status, kind, target, code, sector_id, sector, room, previous, prior_code, room_macro =
            row:match('^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$')
        if nonce ~= o.nonce or op ~= pending:lower() or not status:match('^[A-Z_]+$') then
            return fail('PARKING_EVENT_CORRELATION')
        end
        pending = nil
        if step == 'readback' and status == 'ARRIVAL_UNCONFIRMED' and o.mode == 'prepare' then
            next_read = o.clock() + .1
            return true
        end
        if (step == 'preflight' and status ~= 'READY') or (step == 'park' and status ~= 'DISPATCHED') or
                (step == 'readback' and status ~= 'PARKED') then
            return fail(status)
        end
        if (kind ~= 'HQ' and kind ~= 'FALLBACK') or not target:match('^' .. HEX .. '$')
                or not code:match('^' .. CODE .. '$') or not sector_id:match('^' .. HEX .. '$')
                or not sector:match('^' .. MACRO .. '$') then
            return fail('PARKING_TARGET_INVALID')
        end
        if destination and (target ~= destination.ref or code ~= destination.code
                or sector ~= destination.sector or kind ~= destination.kind) then
            return fail('TARGET_CHANGED')
        end
        if step == 'preflight' then
            if prior_code ~= o.ships[1].code or o.check_previous(previous) ~= true then
                return fail('SOURCE_COCKPIT_CHANGED')
            end
            destination = {ref = target, code = code, sector = sector, kind = kind}
            step = 'park'
        elseif step == 'park' then
            step = 'readback'
        else
            if not room:match('^' .. HEX .. '$') or room_macro == '0'
                    or not room_macro:match('^' .. MACRO .. '$') then return fail('ROOM_INVALID') end
            if o.expected and (code ~= o.expected.code or sector ~= o.expected.sector
                    or kind ~= o.expected.kind or room_macro ~= o.expected.room) then
                return fail('COLD_LOCATION_CHANGED')
            end
            station = {ref = target, sector_ref = sector_id, room_ref = room, room = room_macro,
                       code = code, sector = sector, kind = kind}
            if o.check_station(station) ~= true then return fail('LOCAL_STATION_READBACK') end
            step = 'ship'
        end
        return true
    end

    local function ship_event(row)
        if not valid() then return end
        if step ~= 'ship' or pending ~= 'Inspect' or type(row) ~= 'string' or #row > 150 then
            return fail('UNEXPECTED_SHIP_EVENT')
        end
        local nonce, idx, status, actor = row:match('^([^|]+)|([0-9]+)|([A-Z_]+)|([^|]+)$')
        local ship = o.ships[index]
        if nonce ~= o.nonce or idx ~= tostring(ship.index) then return fail('SHIP_EVENT_CORRELATION') end
        if status ~= 'AVAILABLE' then return fail(status) end
        if o.check_ship(actor, ship.code) ~= true then return fail('LOCAL_SHIP_READBACK') end
        pending = nil
        index = index + 1
        if index > #o.ships then
            if o.check_station(station) ~= true then return fail('LOCAL_STATION_READBACK') end
            o.log('PARKED|' .. station.kind .. '|' .. station.code .. '|' .. station.sector .. '|' .. station.room)
            step = 'done'
        end
        return true
    end

    assert(o.register(SCREEN .. '.Result', parking_event))
    assert(o.register(SHIPS .. '.Result', ship_event))
    return function()
        if step == 'failed' then error('station parking refused') end
        if step == 'done' then return true end
        if not valid() then return false end
        wait_deadline = wait_deadline or (o.clock() + 20)
        if pending or (next_read and o.clock() < next_read) then return false end
        next_read = nil
        local payload = {nonce = o.nonce, fallback_sector = o.sector, fallback_codes = o.codes,
                         fallback_owner = o.owner, fallback_macros = o.macros}
        local screen = SCREEN
        if step == 'ship' then
            local s = o.ships[index]
            screen, pending = SHIPS, 'Inspect'
            payload = {nonce = o.nonce, epoch = o.epoch, count = o.count, index = s.index,
                       code = s.code, macro = s.macro, sector = s.sector}
        elseif step == 'park' then
            pending, payload.target = 'Park', destination.ref
        else
            pending = 'Read'
        end
        if o.issue(screen, pending, payload) ~= true then return fail('DISPATCH_FAILED') end
        return step == 'done'
    end
end

function M.new_x4(o, guard)
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    ffi.cdef[[
        bool IsValidComponent(UniverseID); bool IsComponentClass(UniverseID,const char*);
        bool IsComponentOperational(UniverseID);
        UniverseID GetContextByClass(UniverseID,const char*,bool);
        UniverseID GetPlayerContainerID(void);
        UniverseID GetPlayerControlledShipID(void); UniverseID GetPlayerOccupiedShipID(void);
        const char* GetObjectIDCode(UniverseID);
    ]]
    local C, parse = ffi.C, X4LAN_RESOLVE_STRING.parse_hex
    local function code_matches(id, expected)
        local value = C.GetObjectIDCode(id)
        return value ~= nil and value ~= ffi.NULL and ffi.string(value) == expected
    end
    o.guard, o.clock = guard, GetCurRealTime
    o.log = function(row) DebugError('[X4LAN] AUTH_STATION_PARKING ' .. o.nonce .. '|' .. row) end
    o.register = function(name, callback)
        return RegisterEvent(name, function(_, row) callback(row) end) ~= false
    end
    o.issue = function(screen, action, args) return AddUITriggeredEvent(screen, action, args) ~= false end
    o.check_previous = function(value)
        local actor = parse(value)
        return actor ~= nil and code_matches(actor, o.ships[1].code) and C.GetPlayerContainerID() == actor
            and C.GetPlayerControlledShipID() == actor and C.GetPlayerOccupiedShipID() == actor
    end
    o.check_station = function(value)
        local target, sector, room = parse(value.ref), parse(value.sector_ref), parse(value.room_ref)
        local container = C.GetPlayerContainerID()
        return target and sector and room and C.IsValidComponent(target)
            and C.IsComponentClass(target, 'station') and C.IsComponentOperational(target)
            and code_matches(target, value.code) and C.GetContextByClass(target, 'sector', false) == sector
            and C.IsValidComponent(room) and C.GetContextByClass(room, 'station', true) == target
            and C.IsValidComponent(container) and C.GetContextByClass(container, 'station', true) == target
            and C.GetPlayerControlledShipID() == 0 and C.GetPlayerOccupiedShipID() == 0
    end
    o.check_ship = function(value, code)
        local actor = parse(value)
        if not actor or not C.IsValidComponent(actor) or not C.IsComponentClass(actor, 'ship')
                or not C.IsComponentOperational(actor) or not code_matches(actor, code) then return false end
        local key = tostring(actor):gsub('ULL$', '')
        local lua_id = ConvertStringToLuaID(key)
        if not lua_id or tostring(ConvertIDTo64Bit(lua_id)):gsub('ULL$', '') ~= key then return false end
        local docked, docking, stored = GetComponentData(lua_id, 'isdocked', 'isdocking', 'isinternallystored')
        return docked == false and docking == false and stored == false
    end
    return M.new(o)
end

X4LAN_AUTHORITY_STATION_PARKING = M
return M
