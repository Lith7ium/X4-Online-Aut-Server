-- Opt-in isolated server pose trial. The existing X4 update owner calls tick().
local M = {}
local SCREEN = 'X4AuthorityCompanyShipPose'
local HELLO_INTERVAL = 0.5
local MD_TIMEOUT = 2
local success_status = {inspect = 'INSPECTED', apply = 'DISPATCHED', verify = 'OBSERVED'}
local POSITION_LIMIT = 10000000

local function token(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function actor_id(value)
    return #value <= 18 and value:match('^0[xX][0-9a-fA-F]+$') ~= nil
end

local function finite(value)
    return type(value) == 'number' and value > -math.huge and value < math.huge
end

local function number(value)
    return string.format('%.17g', value)
end

local function fields(value, count)
    if type(value) ~= 'string' or #value > 768 then return nil end
    local row = {}
    for field in value:gmatch('[^|]+') do row[#row + 1] = field end
    if #row ~= count or table.concat(row, '|') ~= value then return nil end
    return row
end

local function request_fields(value, lease, epoch)
    local row = fields(value, 12)
    if not row or row[1] ~= 'P' or row[2] ~= lease or row[5] ~= epoch then return nil end
    local action, request, index, generation, frame, actor = row[3], row[4], row[6], row[7], row[8], row[9]
    if action ~= 'inspect' and action ~= 'apply' and action ~= 'verify' then return nil end
    if not token(request) or not token(generation) or #index > 6 or not index:match('^[1-9]%d*$')
            or tonumber(index) > 200000 or #frame > 128 or not frame:match('^[a-z0-9_]+$') then return nil end
    if (action == 'inspect' and actor ~= '0') or (action ~= 'inspect' and not actor_id(actor)) then return nil end
    local x, y, z = tonumber(row[10]), tonumber(row[11]), tonumber(row[12])
    for _, axis in ipairs({x or false, y or false, z or false}) do
        if not finite(axis) or math.abs(axis) > POSITION_LIMIT then return nil end
    end
    return {
        action = action, request_id = request, epoch = epoch, index = tonumber(index),
        generation = generation, frame = frame, actor = actor, x = x, y = y, z = z,
    }
end

local function binding(args, actor)
    return table.concat({args.epoch, tostring(args.index), args.generation, args.frame, actor}, '|')
end

function M.new(config, engine, net)
    assert(token(config.lease) and token(config.epoch))
    local prefix = 'P|' .. config.lease .. '|'
    local allow_apply = config.allow_apply == true
    local next_hello = 0
    local pending, inspected, last_request
    local closed, write_fault = false, false
    local api = {}

    local function refuse(args, status)
        net:send(prefix .. 'RESULT|' .. table.concat({
            args.request_id, args.epoch, tostring(args.index), args.generation,
            args.action, status, '0', args.frame, '0', '0', '0', '0', '0', '0', '-',
        }, '|'))
    end

    local function fail_pending(status)
        local args = pending.args
        pending = nil
        if args.action == 'apply' then write_fault = true end
        refuse(args, status)
    end

    local function reply(_, value)
        if closed or not pending or type(value) ~= 'string'
                or value:sub(1, 33) ~= pending.args.request_id .. '|' then return end
        if engine.clock() >= pending.deadline then
            fail_pending('BRIDGE_MD_TIMEOUT')
            return
        end
        local args = pending.args
        local row = fields(value, 11)
        if not row or row[2] ~= args.epoch or row[3] ~= tostring(args.index) or row[4] ~= args.generation
                or row[5] ~= args.action or row[8] ~= args.frame or not row[6]:match('^[A-Z_]+$') then
            fail_pending('BRIDGE_MD_INVALID')
            return
        end
        local suffix = '|0|0|0|-'
        if row[6] == success_status[args.action] then
            if not actor_id(row[7]) or (args.action ~= 'inspect' and row[7] ~= args.actor)
                    or not finite(tonumber(row[9])) or not finite(tonumber(row[10]))
                    or not finite(tonumber(row[11])) then
                fail_pending('BRIDGE_MD_INVALID')
                return
            end
            local live, x, y, z, code = engine.actor(args, row[7], args.action ~= 'apply')
            if not live or (args.action ~= 'apply' and (not finite(x) or not finite(y) or not finite(z)
                    or type(code) ~= 'string' or not code:match('^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$'))) then
                fail_pending('BRIDGE_LOCAL_REFUSED')
                return
            end
            if args.action ~= 'apply' then
                suffix = '|' .. number(x) .. '|' .. number(y) .. '|' .. number(z) .. '|' .. code
            end
            if args.action == 'inspect' then inspected = binding(args, row[7]) end
        elseif row[7] ~= '0' or row[9] ~= '0' or row[10] ~= '0' or row[11] ~= '0' then
            fail_pending('BRIDGE_MD_INVALID')
            return
        end
        pending = nil
        net:send(prefix .. 'RESULT|' .. value .. suffix)
    end

    local function update()
        if not engine.ready() then
            inspected = nil
            if pending then fail_pending('BRIDGE_LOCAL_REFUSED') end
            return
        end
        local now = engine.clock()
        assert(finite(now) and now >= 0)
        if now >= next_hello then
            net:send(prefix .. 'HELLO')
            next_hello = now + HELLO_INTERVAL
        end
        if pending then
            if now >= pending.deadline then
                fail_pending('BRIDGE_MD_TIMEOUT')
            end
            return
        end
        local args = request_fields(net:receive(), config.lease, config.epoch)
        if not args or args.request_id == last_request then return end
        last_request = args.request_id
        if args.action == 'inspect' then inspected = nil end
        if (args.action == 'apply' and (not allow_apply or write_fault
                or inspected ~= binding(args, args.actor)))
                or (args.action ~= 'inspect' and not engine.actor(args, args.actor, false))
                or (args.action == 'apply' and engine.paused()) then
            refuse(args, 'BRIDGE_LOCAL_REFUSED')
            return
        end
        pending = {args = args, deadline = now + MD_TIMEOUT}
        if not engine.issue(args) and pending then
            pending = nil
            refuse(args, 'BRIDGE_ISSUE_FAILED')
        end
    end

    function api:close()
        if closed then return end
        closed = true
        pending, inspected = nil, nil
        net:close()
    end

    function api:tick()
        if not closed and not pcall(update) then self:close() end
    end

    local ok = engine.register(function(...)
        if not pcall(reply, ...) then api:close() end
    end)
    if not ok then api:close() end
    assert(ok, 'server pose event registration failed')
    return api
end

function M.new_x4(config, registry_ready, profile_path, udp)
    assert(type(registry_ready) == 'function' and token(config.epoch) and token(config.lease))
    assert(type(config.port) == 'number' and config.port % 1 == 0 and config.port > 0 and config.port <= 65535)
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    if not pcall(ffi.typeof, 'UIPosRot') then
        ffi.cdef('typedef struct { float x,y,z,yaw,pitch,roll; } UIPosRot;')
    end
    ffi.cdef[[
        bool IsGamePaused(void); bool IsPlayerValid(void); const char* GetSaveFolderPath(void);
        bool IsValidComponent(UniverseID); bool IsComponentOperational(UniverseID);
        bool IsComponentClass(UniverseID,const char*);
        UniverseID GetContextByClass(UniverseID,const char*,bool);
        UniverseID GetPlayerContainerID(void); UniverseID GetPlayerOccupiedShipID(void);
        UniverseID GetPlayerControlledShipID(void); UIPosRot GetObjectPositionInSector(UniverseID);
        const char* GetObjectIDCode(UniverseID);
    ]]
    local C = ffi.C
    local resolve = assert(X4LAN_RESOLVE_STRING)
    local normalize = X4LAN_REPLICA_MOTION_BINDING.normalize_profile_save_path
    local expected = assert(normalize(profile_path))
    local function ready()
        if registry_ready() ~= true or not C.IsPlayerValid() then return false end
        local path = C.GetSaveFolderPath()
        return path ~= nil and path ~= ffi.NULL and normalize(ffi.string(path)) == expected
    end

    local function to_lua_id(id)
        local key = tostring(id):gsub('ULL$', '')
        local ref = ConvertStringToLuaID(key)
        if not ref or tostring(ConvertIDTo64Bit(ref)):gsub('ULL$', '') ~= key then return nil end
        return ref
    end

    local function live_context(id, frame)
        if not ready() or not C.IsValidComponent(id) or not C.IsComponentOperational(id)
                or not C.IsComponentClass(id, 'ship') then return nil end
        local occupied, controlled, container = C.GetPlayerOccupiedShipID(),
            C.GetPlayerControlledShipID(), C.GetPlayerContainerID()
        if id == occupied or id == controlled or id == container
                or container == 0 or not C.IsValidComponent(container)
                or C.GetContextByClass(container, 'ship', true) == id then return nil end
        local sector = C.GetContextByClass(id, 'sector', false)
        if sector == 0 or not C.IsValidComponent(sector) or not C.IsComponentClass(sector, 'sector') then return nil end
        local ship_ref, sector_ref = to_lua_id(id), to_lua_id(sector)
        if not ship_ref or not sector_ref or GetComponentData(sector_ref, 'macro') ~= frame then return nil end
        local owner, docked, docking, stored = GetComponentData(ship_ref,
            'owner', 'isdocked', 'isdocking', 'isinternallystored')
        if owner ~= 'player' or docked ~= false or docking ~= false or stored ~= false then return nil end
        return sector
    end

    udp = udp or X4LAN_LOCAL_UDP_MODULE.load(ffi.os, package.loadlib)
    local net = assert(udp.open('127.0.0.1', config.port))
    return M.new(config, {
        ready = ready, clock = udp.monotonic, paused = function() return C.IsGamePaused() end,
        actor = function(args, actor, read)
            local id = resolve.parse_hex(actor)
            local sector = id and live_context(id, args.frame)
            if not sector then return false end
            if not read then return true end
            local value = C.GetObjectPositionInSector(id)
            local ptr = C.GetObjectIDCode(id)
            if ptr == nil or ptr == ffi.NULL then return false end
            local code = ffi.string(ptr)
            if live_context(id, args.frame) ~= sector then return false end
            return true, tonumber(value.x), tonumber(value.y), tonumber(value.z), code
        end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Request', args) ~= false end,
        register = function(callback) return RegisterEvent(SCREEN .. '.Result', callback) ~= false end,
    }, net)
end

X4LAN_AUTHORITY_COMPANY_SHIP_POSE = M
return M
