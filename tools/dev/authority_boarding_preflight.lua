-- Private one-shot, read-only stock menu preflight. No event or update registration.
-- The caller supplies the loader's confirmed format-3 registry resolver and save guard.
local M = {}
local ffi = require('ffi')

if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
if not pcall(ffi.typeof, 'UIPosRot') then
    ffi.cdef('typedef struct {float x,y,z,yaw,pitch,roll;} UIPosRot;')
end
if not pcall(ffi.typeof, 'RoleTierData') then
    ffi.cdef('typedef struct {const char* name; int32_t skilllevel; uint32_t amount;} RoleTierData;')
end
if not pcall(ffi.typeof, 'PeopleInfo') then
    ffi.cdef[[typedef struct {const char* id; const char* name; const char* desc;
        uint32_t amount; uint32_t numtiers; bool canhire;} PeopleInfo;]]
end
ffi.cdef[[
bool IsPlayerValid(void);
bool IsValidComponent(UniverseID component);
bool IsComponentOperational(UniverseID component);
bool IsComponentClass(UniverseID component, const char* classid);
bool IsUnit(UniverseID component);
bool IsOrderSelectableFor(const char* orderdefid, UniverseID controllableid);
const char* GetObjectIDCode(UniverseID component);
UniverseID GetPlayerID(void);
UniverseID GetTopLevelContainer(UniverseID component);
UniverseID GetContextByClass(UniverseID component, const char* classid, bool includeself);
UIPosRot GetObjectPositionInSector(UniverseID component);
uint32_t GetNumTiersOfRole(const char* role);
uint32_t GetNumAllRoles(void);
uint32_t GetRoleTiers2(RoleTierData* result, uint32_t resultlen,
    UniverseID controllableid, const char* role, bool includearriving);
uint32_t GetPeople2(PeopleInfo* result, uint32_t resultlen,
    UniverseID controllableid, bool includearriving);
uint32_t GetPeopleCapacity(UniverseID controllableid, const char* macroname, bool includepilot);
]]
local C = ffi.C

local function finite(value)
    return type(value) == 'number' and value == value and math.abs(value) < math.huge
end

local function count(value, limit)
    return finite(value) and value >= 0 and value <= limit and value % 1 == 0
end

local function observe(read, valid)
    local ok, value = pcall(read)
    if not ok or not valid(value) then return {status='unavailable'} end
    return {status='observed', value=value}
end

local function boolean(value) return type(value) == 'boolean' end

local function marine_count(ship)
    local capacity = tonumber(C.GetNumTiersOfRole('marine'))
    assert(count(capacity, 64) and capacity > 0)
    local rows = ffi.new('RoleTierData[?]', capacity)
    for i = 0, capacity-1 do rows[i].skilllevel = -2147483648 end
    local used = tonumber(C.GetRoleTiers2(rows, capacity, ship, 'marine', false))
    assert(count(used, capacity))
    if used == 0 then
        -- The tier getter has no error status. Corroborate a zero-row result
        -- with a populated crew getter before recording zero current Marines.
        local slots = tonumber(C.GetNumAllRoles())
        assert(count(slots, 128) and slots > 0)
        local people = ffi.new('PeopleInfo[?]', slots)
        local length = tonumber(C.GetPeople2(people, slots, ship, false))
        assert(count(length, slots) and length > 0)
        for i = 0, length-1 do
            local role = people[i].id
            assert(role ~= nil and role ~= ffi.NULL)
            if ffi.string(role) == 'marine' then assert(tonumber(people[i].amount) == 0) end
        end
        return {total=0, tiers={}, source='crew_corroborated'}
    end
    local tiers, total, seen = {}, 0, {}
    for i = 0, used-1 do
        local tier, amount = tonumber(rows[i].skilllevel), tonumber(rows[i].amount)
        assert(tier ~= -2147483648 and not seen[tier])
        seen[tier] = true
        total = total + amount
        tiers[#tiers+1] = {skill=tier, amount=amount}
    end
    return {total=total, tiers=tiers, source='role_tiers'}
end

local function clear_position(id)
    local p = C.GetObjectPositionInSector(id)
    local x, y, z = tonumber(p.x), tonumber(p.y), tonumber(p.z)
    assert(finite(x) and finite(y) and finite(z))
    return x, y, z
end

local function distance_to(avatar, ship, sector)
    if avatar.sector ~= sector then return {status='different_sector'} end
    return observe(function()
        local x,y,z = clear_position(avatar.id)
        local sx,sy,sz = clear_position(ship)
        return math.sqrt((x-sx)^2 + (y-sy)^2 + (z-sz)^2)
    end, function(value) return finite(value) and value >= 0 end)
end

function M.read_x4(options)
    if type(options) ~= 'table' or options.enabled ~= true then return nil, 'disabled' end
    local function read()
        local resolver = X4LAN_RESOLVE_STRING
        assert(type(resolver) == 'table' and type(options.lookup) == 'function' and type(options.guard) == 'function')
        assert(type(options.epoch) == 'string' and options.epoch:match('^[0-9a-f]+$') and #options.epoch == 32)
        local function identify(witness)
            assert(type(witness) == 'table' and count(witness.index, 200000) and witness.index > 0)
            assert(type(witness.code) == 'string' and witness.code:match('^[A-Z0-9]+%-[A-Z0-9]+$'))
            assert(type(witness.macro) == 'string' and witness.macro:match('^[a-z0-9_]+$'))
            assert(type(witness.trueowner) == 'string' and witness.trueowner:match('^[a-z0-9_]+$'))
            local id = assert(resolver.actor_u64(witness.id))
            -- The loader returns the runtime uint64 and independently observed MD
            -- true owner for this epoch/index after checking both registry directions.
            local bound_id, trueowner = options.lookup(options.epoch, witness.index)
            assert(resolver.actor_u64(bound_id) == id and trueowner == witness.trueowner)
            assert(C.IsValidComponent(id) and C.IsComponentOperational(id) and C.IsComponentClass(id, 'ship'))
            local code = C.GetObjectIDCode(id)
            assert(code ~= nil and code ~= ffi.NULL and ffi.string(code) == witness.code)
            local macro, visible_owner, player_owned = GetComponentData(id, 'macro', 'owner', 'isplayerowned')
            assert(macro == witness.macro and type(visible_owner) == 'string'
                and visible_owner:match('^[a-z0-9_]+$'))
            assert(witness.visible_owner == nil or visible_owner == witness.visible_owner)
            local sector = C.GetContextByClass(id, 'sector', false)
            assert(sector ~= 0 and C.IsValidComponent(sector) and C.IsComponentClass(sector, 'sector'))
            if witness.trueowner == 'player' then
                assert(visible_owner == 'player' and player_owned == true)
            else
                assert(visible_owner ~= 'player' and visible_owner ~= 'ownerless' and not player_owned)
            end
            return id, sector, visible_owner
        end

        assert(options.guard() == true and C.IsPlayerValid())
        local ship, ship_sector, ship_owner = identify(options.ship)
        local target, target_sector, target_owner = identify(options.target)
        assert(ship ~= target and options.ship.index ~= options.target.index and options.ship.trueowner == 'player')
        assert(options.target.trueowner ~= 'player' and options.target.trueowner ~= 'ownerless')
        assert(C.IsComponentClass(target, 'ship_l') or C.IsComponentClass(target, 'ship_xl'))
        -- Match the stock interaction menu: a falsy value excludes no ship.
        assert(not C.IsUnit(ship) and not GetComponentData(ship, 'isonlineobject'))

        local avatar_id = C.GetPlayerID()
        assert(avatar_id ~= 0 and C.IsValidComponent(avatar_id))
        local container = C.GetTopLevelContainer(avatar_id)
        assert(container ~= 0 and C.IsValidComponent(container) and container ~= ship and container ~= target)
        local avatar_sector = C.GetContextByClass(container, 'sector', false)
        assert(avatar_sector ~= 0 and C.IsComponentClass(avatar_sector, 'sector'))
        local avatar = {id=avatar_id, sector=avatar_sector}
        local result = {
            epoch=options.epoch, ship_index=options.ship.index, target_index=options.target.index,
            ship_code=options.ship.code, target_code=options.target.code,
            ship_visible_owner=ship_owner, target_visible_owner=target_owner,
            ship_trueowner=options.ship.trueowner, target_trueowner=options.target.trueowner,
            ship_sector=tostring(ship_sector):gsub('ULL$', ''),
            target_sector=tostring(target_sector):gsub('ULL$', ''),
            avatar_sector=tostring(avatar_sector):gsub('ULL$', ''),
            avatar_on_station=C.IsComponentClass(container, 'station'),
            avatar_to_ship_m=distance_to(avatar, ship, ship_sector),
            avatar_to_target_m=distance_to(avatar, target, target_sector),
            same_sector=ship_sector == target_sector, minimum_assigned_marines=1,
            board_order=observe(function() return C.IsOrderSelectableFor('Board', ship) end, boolean),
            capturable=observe(function() return GetComponentData(target, 'iscapturable') end, boolean),
            marines=observe(function() return marine_count(ship) end, function(v) return type(v) == 'table' end),
            target_capacity=observe(function() return tonumber(C.GetPeopleCapacity(target, '', false)) end,
                function(v) return count(v, 1000000) end),
            resistance=observe(function() return GetComponentData(target, 'boardingresistance') end,
                function(v) return finite(v) and v >= 0 end),
        }
        local ship_after, ship_sector_after, ship_owner_after = identify(options.ship)
        local target_after, target_sector_after, target_owner_after = identify(options.target)
        assert(ship_after == ship and target_after == target and ship_sector_after == ship_sector
            and target_sector_after == target_sector and ship_owner_after == ship_owner
            and target_owner_after == target_owner and C.GetPlayerID() == avatar_id
            and C.GetTopLevelContainer(avatar_id) == container
            and C.GetContextByClass(container, 'sector', false) == avatar_sector and options.guard() == true)
        -- This is the initial stock allocation bound for this single ship.
        -- It does not include subordinate crews or allocations to an existing
        -- operation. Map access and native start eligibility are separate.
        if result.marines.status == 'observed' and result.target_capacity.status == 'observed' then
            result.maximum_from_ship = math.min(result.marines.value.total, result.target_capacity.value)
        end
        return result
    end
    local ok, result = pcall(read)
    if not ok then return nil, 'binding_or_context_unavailable' end
    return result
end

return M
