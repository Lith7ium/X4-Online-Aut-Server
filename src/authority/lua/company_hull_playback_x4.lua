-- Opt-in native sector-space adapter for one locally observed Company hull.
local M = {}
local LIMIT = 10000000
local MAX_STEP = 50

local function finite(value)
    return type(value) == 'number' and value == value and math.abs(value) < math.huge
end

local function position(value)
    return type(value) == 'table' and finite(value.x) and math.abs(value.x) <= LIMIT
        and finite(value.y) and math.abs(value.y) <= LIMIT
        and finite(value.z) and math.abs(value.z) <= LIMIT
end

local function index_in(value, count)
    return type(value) == 'number' and value >= 1 and value <= count and value % 1 == 0
end

local function token(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function same_context(a, b)
    return a and b and a.actor == b.actor and a.sector == b.sector and a.zone == b.zone
end

function M.new(options, engine)
    assert(type(options) == 'table' and options.enabled == true, 'explicit hull playback opt-in required')
    local pinned, own_index, observer = options.pinned, options.own_index, options.observer
    assert(type(pinned) == 'table' and token(pinned.epoch) and index_in(pinned.issued_count, 200000)
        and index_in(own_index, pinned.issued_count), 'verified local register required')
    assert(type(observer) == 'table' and type(observer.matches) == 'function'
        and type(options.guard) == 'function', 'verified local cockpit observer and guard required')
    local epoch, count = pinned.epoch, pinned.issued_count
    local adapter = {}

    function adapter:bind(binding)
        if type(binding) ~= 'table' or binding.slot ~= 'cockpit' or binding.epoch ~= epoch
                or not index_in(binding.index, count) or binding.index == own_index
                or type(binding.actor) ~= 'string' or type(binding.sector_macro) ~= 'string'
                or type(binding.code) ~= 'string' then return nil end
        local index, actor_text, frame, code = binding.index, binding.actor, binding.sector_macro, binding.code

        local function inspect()
            if options.guard() ~= true or not observer:matches(epoch, frame, index, actor_text, code) then return end
            local cockpit = engine.cockpit()
            if not cockpit then return end
            local target = engine.target(actor_text)
            if target and target.actor ~= cockpit.actor and target.sector == cockpit.sector
                    and target.sector_macro == frame and target.code == code then
                return cockpit, target
            end
        end

        local ok, original_cockpit, original_target = pcall(inspect)
        if not ok or not original_cockpit then return nil end
        local closed = false
        local session = {}

        local function eligible()
            if closed then return false end
            local checked, cockpit, target = pcall(inspect)
            if not checked or not same_context(cockpit, original_cockpit)
                    or not same_context(target, original_target) then
                closed = true
                return false
            end
            return true
        end

        function session:guard()
            return eligible()
        end

        function session:read()
            if not eligible() then return nil end
            local actual = engine.read(original_target.actor)
            if not position(actual) or not eligible() then return nil end
            return actual
        end

        function session:write(target, before)
            if not position(target) or not position(before) then return false end
            local actual = self:read()
            if not actual or not finite(actual.yaw) or not finite(actual.pitch) or not finite(actual.roll) then return false end
            local dx, dy, dz = target.x - actual.x, target.y - actual.y, target.z - actual.z
            if dx * dx + dy * dy + dz * dz > MAX_STEP * MAX_STEP then return false end
            if not engine.same_zone(original_target, target) or not eligible() then return false end
            -- Follow the stock map editor: modify XYZ on the returned sector pose.
            -- The caller independently reads back on a later update.
            local ok, dispatched = pcall(engine.write, original_target.actor, original_target.sector, {
                x = target.x, y = target.y, z = target.z,
                yaw = actual.yaw, pitch = actual.pitch, roll = actual.roll,
            })
            if not ok or dispatched ~= true then
                closed = true  -- The native call may have had a partial effect.
                return false
            end
            return eligible()
        end

        function session:close()
            closed = true
        end
        return session
    end
    return adapter
end

function M.new_x4(options)
    assert(type(options) == 'table' and options.enabled == true, 'explicit hull playback opt-in required')
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    if not pcall(ffi.typeof, 'UIPosRot') then
        ffi.cdef('typedef struct { float x, y, z, yaw, pitch, roll; } UIPosRot;')
    end
    ffi.cdef[[
        bool IsPlayerValid(void);
        bool IsGamePaused(void);
        bool IsValidComponent(UniverseID);
        bool IsComponentOperational(UniverseID);
        bool IsComponentClass(UniverseID, const char*);
        UniverseID GetPlayerControlledShipID(void);
        UniverseID GetPlayerOccupiedShipID(void);
        UniverseID GetPlayerContainerID(void);
        UniverseID GetContextByClass(UniverseID, const char*, bool);
        const char* GetObjectIDCode(UniverseID);
        UIPosRot GetObjectPositionInSector(UniverseID);
        UniverseID GetZoneAt(UniverseID, UIPosRot*);
        void SetObjectSectorPos(UniverseID, UniverseID, UIPosRot);
    ]]
    local C, resolve = ffi.C, assert(X4LAN_RESOLVE_STRING)
    local read, set, zone_at = C.GetObjectPositionInSector, C.SetObjectSectorPos, C.GetZoneAt

    local function ref(id)
        local value = ConvertStringToLuaID((tostring(id):gsub('ULL$', '')))
        if value and resolve.actor_u64(ConvertIDTo64Bit(value)) == id then return value end
    end

    local function typed(id, class)
        return id ~= 0 and C.IsValidComponent(id) and C.IsComponentClass(id, class)
    end

    local function spaces(actor)
        local sector = C.GetContextByClass(actor, 'sector', false)
        local zone = C.GetContextByClass(actor, 'zone', false)
        if typed(sector, 'sector') and typed(zone, 'zone')
                and C.GetContextByClass(zone, 'sector', false) == sector then
            return sector, zone
        end
    end

    local function cockpit()
        if not C.IsPlayerValid() or C.IsGamePaused() then return end
        local actor = C.GetPlayerControlledShipID()
        if not typed(actor, 'ship') or actor ~= C.GetPlayerOccupiedShipID()
                or actor ~= C.GetPlayerContainerID() then return end
        local sector, zone = spaces(actor)
        if sector then return {actor = actor, sector = sector, zone = zone} end
    end

    local function target(actor_text)
        local actor = resolve.parse_hex(actor_text)
        if not actor or not typed(actor, 'ship') or not C.IsComponentOperational(actor) then return end
        local sector, zone = spaces(actor)
        if not sector then return end
        local ship, sector_ref = ref(actor), ref(sector)
        if not ship or not sector_ref then return end
        local owner, docked, docking, stored, pilot, assigned, ai = GetComponentData(
            ship, 'owner', 'isdocked', 'isdocking', 'isinternallystored', 'pilot', 'assignedpilot', 'assignedaipilot')
        if owner ~= 'player' or docked ~= false or docking ~= false or stored ~= false
                or pilot or assigned or ai then return end
        local code = C.GetObjectIDCode(actor)
        if code == nil or code == ffi.NULL then return end
        return {actor = actor, sector = sector, zone = zone, code = ffi.string(code),
                sector_macro = GetComponentData(sector_ref, 'macro')}
    end

    return M.new(options, {
        cockpit = cockpit,
        target = target,
        read = function(actor)
            local value = read(actor)
            return {x = tonumber(value.x), y = tonumber(value.y), z = tonumber(value.z),
                    yaw = tonumber(value.yaw), pitch = tonumber(value.pitch), roll = tonumber(value.roll)}
        end,
        same_zone = function(hull, destination)
            -- The stock query can change its offset argument to zone space.
            -- Pass a throwaway copy and keep the setter in sector coordinates.
            local offset = ffi.new('UIPosRot[1]')
            offset[0].x, offset[0].y, offset[0].z = destination.x, destination.y, destination.z
            return zone_at(hull.sector, offset) == hull.zone
        end,
        write = function(actor, sector, target_pose)
            set(actor, sector, ffi.new('UIPosRot', target_pose))
            return true
        end,
    })
end

X4LAN_COMPANY_HULL_PLAYBACK_X4 = M
return M
