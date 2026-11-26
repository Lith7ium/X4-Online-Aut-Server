-- Opt-in native sector-space adapter for one locally observed Company hull.
local M = {}
local LIMIT = 10000000
local MAX_STEP, VELOCITY_STEP_LIMIT = 50, 250
local COCKPIT_SLOTS = {cockpit = true, cockpit2 = true, cockpit3 = true}

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

local function same_ship_and_sector(current, original)
    return current and current.actor == original.actor and current.sector == original.sector
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
        if type(binding) ~= 'table' or not COCKPIT_SLOTS[binding.slot] or binding.epoch ~= epoch
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
        local closed, has_verified_motion = false, false
        local confirmed_zone, pending_zone = original_target.zone, nil
        local session = {}

        local function is_pilot()
            return observer.is_active_pilot and observer:is_active_pilot(index) == true
        end

        local function eligible()
            if closed then return false end
            local checked, cockpit, target = pcall(inspect)
            if not checked or not same_ship_and_sector(cockpit, original_cockpit)
                    or not same_ship_and_sector(target, original_target)
                    or (cockpit.zone ~= original_cockpit.zone and not (has_verified_motion and is_pilot()))
                    or (target.zone ~= confirmed_zone and target.zone ~= pending_zone)
                    or (pending_zone and not is_pilot()) then
                closed = true
                return false
            end
            return target
        end

        function session:guard()
            return eligible() and true or false
        end

        function session:tracks_active_pilot()
            return is_pilot() and self:guard()
        end

        function session:read()
            local before = eligible()
            if not before then return nil end
            local actual = engine.read(original_target.actor)
            local after = eligible()
            if not position(actual) or not after then return nil end
            -- The setter can return while the ship is still in its original zone.
            -- A later position is evidence only when read from the expected zone.
            if pending_zone and (before.zone ~= pending_zone or after.zone ~= pending_zone) then
                closed = true
                return nil
            end
            return actual
        end

        -- Called only after the controller checked the later XYZ against its
        -- previous write and the recorder accepted that readback.
        function session:confirm_motion()
            if pending_zone then
                local target = eligible()
                if not target or target.zone ~= pending_zone then return false end
                confirmed_zone, pending_zone = pending_zone, nil
            end
            has_verified_motion = true
            return true
        end

        function session:write(target, before, recheck, attitude, continuous)
            if pending_zone or not position(target) or not position(before) then return false end
            local actual = self:read()
            if not actual or not finite(actual.yaw) or not finite(actual.pitch) or not finite(actual.roll) then return false end
            local dx, dy, dz = target.x - actual.x, target.y - actual.y, target.z - actual.z
            if recheck ~= nil and type(recheck) ~= 'function' then return false end
            local maximum = recheck and VELOCITY_STEP_LIMIT or MAX_STEP
            if continuous and (not recheck or not self:tracks_active_pilot()) then return false end
            if not continuous and dx * dx + dy * dy + dz * dz > maximum * maximum then
                return false, recheck and actual or nil
            end
            local destination_zone = engine.destination_zone(original_target.sector, target)
            if not destination_zone then return false end
            local crossing = destination_zone ~= confirmed_zone
            if crossing and (not has_verified_motion or not recheck or not is_pilot()) then return false end
            if not eligible() then return false end
            -- The native read can differ from the playback observation. Check the
            -- actual displacement and prediction deadline again before dispatch.
            if recheck and recheck(actual) ~= true then return false end
            local rotation = actual
            if attitude ~= nil then
                rotation = X4LAN_MOTION_BUFFER.copy_attitude(attitude)
                if not rotation then return false end
            end
            if crossing then pending_zone = destination_zone end
            local ok, dispatched = pcall(engine.write, original_target.actor, original_target.sector, {
                x = target.x, y = target.y, z = target.z,
                yaw = rotation.yaw, pitch = rotation.pitch, roll = rotation.roll,
            })
            if not ok or dispatched ~= true then
                closed = true  -- The native call may have had a partial effect.
                return false
            end
            return self:guard()
        end

        function session:first_contact_in_range(actual, target)
            if not position(actual) or not position(target) or not is_pilot() or not eligible() then return false end
            local dx, dy, dz = target.x - actual.x, target.y - actual.y, target.z - actual.z
            local range = observer.current_radar_range and observer:current_radar_range(index)
            return finite(range) and range > 0 and dx * dx + dy * dy + dz * dz <= VELOCITY_STEP_LIMIT ^ 2
                and engine.within_radar(original_cockpit.actor, actual, target, range)
        end

        function session:align(target, first_contact_guard)
            if pending_zone or not position(target) then return false end
            local actual = self:read()
            if not actual or not finite(actual.yaw) or not finite(actual.pitch) or not finite(actual.roll) then return false end
            if engine.destination_zone(original_target.sector, target) ~= confirmed_zone or not eligible() then
                return false
            end
            if first_contact_guard and (not self:first_contact_in_range(actual, target)
                    or first_contact_guard(actual) ~= true) then return false end
            local ok, dispatched = pcall(engine.write, original_target.actor, original_target.sector, {
                x = target.x, y = target.y, z = target.z,
                yaw = actual.yaw, pitch = actual.pitch, roll = actual.roll,
            })
            if not ok or dispatched ~= true or not self:guard() then
                closed = true
                return false, 'unconfirmed'
            end
            return true
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
        within_radar = function(cockpit_actor, actual, destination, radius)
            local origin = read(cockpit_actor)
            local function inside(point)
                local dx, dy, dz = point.x - origin.x, point.y - origin.y, point.z - origin.z
                return dx * dx + dy * dy + dz * dz <= radius * radius
            end
            return inside(actual) and inside(destination)
        end,
        destination_zone = function(sector, destination)
            -- The stock query can change its offset argument to zone space.
            -- Pass a throwaway copy and keep the setter in sector coordinates.
            local offset = ffi.new('UIPosRot[1]')
            offset[0].x, offset[0].y, offset[0].z = destination.x, destination.y, destination.z
            local zone = zone_at(sector, offset)
            if typed(zone, 'zone') and C.GetContextByClass(zone, 'sector', false) == sector then
                return zone
            end
        end,
        write = function(actor, sector, target_pose)
            -- GetObjectPositionInSector returns radians. SetObjectSectorPos takes degrees.
            set(actor, sector, ffi.new('UIPosRot', {
                x = target_pose.x, y = target_pose.y, z = target_pose.z,
                yaw = math.deg(target_pose.yaw), pitch = math.deg(target_pose.pitch), roll = math.deg(target_pose.roll),
            }))
            return true
        end,
    })
end

X4LAN_COMPANY_HULL_PLAYBACK_X4 = M
return M
