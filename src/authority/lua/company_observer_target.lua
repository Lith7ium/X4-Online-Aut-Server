-- Read-only registry binding for a locally observed Company hull.
local M = {}
local SCREEN = 'X4AuthorityCompanyObserverTarget'
local INTERVAL, FRESHNESS = .15, .4
local MAX_INDEX = 200000

local function token(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function index_in(value, count)
    return type(value) == 'number' and value >= 1 and value <= count and value % 1 == 0
end

local function same_target(a, b)
    return a ~= nil and b ~= nil and a.actor == b.actor and a.space == b.space
        and a.sector_macro == b.sector_macro and a.code == b.code and a.mode == b.mode
end

local function valid_code(value)
    return type(value) == 'string' and #value <= 32 and value:match('^[A-Z0-9]+%-%d+$') ~= nil
end

local function valid_sector(value)
    return type(value) == 'string' and #value <= 128 and value:match('^[A-Za-z0-9_]+$') ~= nil
end

function M.new(pinned, own_index, engine)
    local epoch, nonce, count = pinned.epoch, pinned.nonce, pinned.issued_count
    assert(token(epoch) and token(nonce) and index_in(count, MAX_INDEX) and index_in(own_index, count))
    local pending, observed, last, next_poll
    local sequence = 0
    local closed = false
    local api = {}

    local function inspect()
        if closed then return nil end
        local now = engine.clock()
        if type(now) ~= 'number' or not (now >= 0 and now < math.huge)
                or (last ~= nil and now < last) then
            pending, observed, closed = nil, nil, true
            return nil
        end
        last = now
        local ok, target = pcall(engine.target)
        if not ok then target = nil end
        if pending and (not same_target(pending.target, target) or now >= pending.deadline) then pending = nil end
        if observed and (not same_target(observed.target, target) or now >= observed.deadline) then observed = nil end
        return target, now
    end

    local function receive(_, row)
        if not pending or type(row) ~= 'string' or row:sub(1, #pending.id + 1) ~= pending.id .. '|' then return end
        local request = pending
        local target = inspect()
        local eligible = pending == request and target ~= nil
        pending = nil
        if not eligible or #row > 384 then observed = nil; return end
        local id, actual_epoch, status, index_text, actor, sector, code =
            row:match('^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$')
        local index = tonumber(index_text)
        if id ~= request.id or actual_epoch ~= epoch or status ~= 'OBSERVED' or not index_in(index, count)
                or index == own_index or tostring(index) ~= index_text or sector ~= target.sector_macro
                or code ~= target.code or engine.parse_actor(actor) ~= target.actor then
            observed = nil
            return
        end
        engine.confirm(target)
        observed = {target = target, deadline = request.deadline,
                    row = {epoch = epoch, sector_macro = sector, index = index, code = code, actor = actor}}
    end

    function api:tick()
        local target, now = inspect()
        if not target or pending or (next_poll and now < next_poll) then return end
        if sequence >= 4294967295 then self:close(); return end
        sequence = sequence + 1
        next_poll = now + INTERVAL
        local id = nonce .. '-' .. engine.scope .. '-' .. tostring(sequence)
        pending = {id = id, target = target, deadline = now + FRESHNESS}
        local ok, issued = pcall(engine.issue, {
            request_id = id, epoch = epoch, count = count, own = own_index, mode = target.mode, target = target.ref,
        })
        if not ok or issued ~= true then pending, observed = nil, nil end
    end

    function api:current()
        inspect()
        if not observed then return nil end
        local row = observed.row
        return {epoch = row.epoch, sector_macro = row.sector_macro, index = row.index, code = row.code, actor = row.actor}
    end

    function api:matches(wanted_epoch, sector, index, actor, code)
        inspect()
        local row = observed and observed.row
        return row ~= nil and row.epoch == wanted_epoch and row.sector_macro == sector
            and row.index == index and row.actor == actor and row.code == code
    end

    function api:close()
        closed, pending, observed = true, nil, nil
    end

    assert(engine.register(receive) == true, 'company target observation event unavailable')
    return api
end

local function new_x4(pinned, own_index, registry_ready, map_target)
    assert(type(registry_ready) == 'function')
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    if not pcall(ffi.typeof, 'ScreenPos') then
        ffi.cdef('typedef struct { float x; float y; bool onScreen; } ScreenPos;')
    end
    ffi.cdef[[
        bool IsPlayerValid(void); bool IsGamePaused(void); bool IsHUDActive(void);
        bool IsExternalTargetMode(void); bool IsExternalViewActive(void);
        bool IsValidComponent(UniverseID); bool IsComponentOperational(UniverseID);
        bool IsComponentClass(UniverseID, const char*);
        UniverseID GetPlayerControlledShipID(void); UniverseID GetPlayerOccupiedShipID(void);
        UniverseID GetPlayerContainerID(void); UniverseID GetContextByClass(UniverseID, const char*, bool);
        const char* GetObjectIDCode(UniverseID);
        ScreenPos GetComponentScreenPosition(UniverseID, uint32_t, uint32_t);
        float GetDistanceBetween(UniverseID, UniverseID);
    ]]
    local C = ffi.C
    local resolve = assert(X4LAN_RESOLVE_STRING)
    local last_hud_target

    local function cockpit_view()
        if registry_ready() ~= true or not C.IsPlayerValid() or C.IsGamePaused()
                or type(Menus) ~= 'table' then return nil end
        local map_open
        for _, menu in ipairs(Menus) do
            if type(menu) == 'table' and menu.name == 'MapMenu' then
                if map_open ~= nil then return nil end
                map_open = not not menu.shown
            end
        end
        if map_open == nil then return nil end
        local ship = C.GetPlayerControlledShipID()
        if ship == 0 or ship ~= C.GetPlayerOccupiedShipID() or ship ~= C.GetPlayerContainerID()
                or not C.IsValidComponent(ship) then return nil end
        return ship, map_open
    end

    local function native_ref(id)
        local ref = ConvertStringToLuaID((tostring(id):gsub('ULL$', '')))
        if not ref or resolve.actor_u64(ConvertIDTo64Bit(ref)) ~= id then return nil end
        return ref
    end

    local function hud_target()
        local ref = GetPlayerTarget()
        return ref and resolve.actor_u64(ConvertIDTo64Bit(ref)) or nil
    end

    local function within_cockpit_radar(cockpit, actor)
        local ref = native_ref(cockpit)
        local range = ref and GetComponentData(ref, 'maxradarrange')
        if type(range) ~= 'number' or not (range > 0 and range < math.huge) then return false end
        local distance = tonumber(C.GetDistanceBetween(cockpit, actor))
        return distance ~= nil and distance >= 0 and distance <= range
    end

    local function candidate()
        local cockpit, map_open = cockpit_view()
        if not cockpit or (map_target and not map_open) then return nil end
        local mode, actor
        if map_target then
            mode, actor = 'map', map_target()
        elseif map_open then
            mode, actor = 'hudmap', hud_target()
            if not last_hud_target or last_hud_target.actor ~= actor then return nil end
        else
            if not C.IsHUDActive() or C.IsExternalTargetMode() or C.IsExternalViewActive() then return nil end
            mode, actor = 'hud', hud_target()
        end
        if not actor or actor == cockpit or not C.IsValidComponent(actor)
                or not C.IsComponentOperational(actor) or not C.IsComponentClass(actor, 'ship') then return nil end
        local space = C.GetContextByClass(actor, 'sector', false)
        if space == 0 or not C.IsValidComponent(space) or not C.IsComponentClass(space, 'sector')
                or (mode ~= 'map' and space ~= C.GetContextByClass(cockpit, 'sector', false)) then return nil end
        local ship_ref, sector_ref = native_ref(actor), native_ref(space)
        if not ship_ref or not sector_ref then return nil end
        local owner, known, radar, stored = GetComponentData(ship_ref, 'owner', 'isknown', 'isradarvisible', 'isinternallystored')
        if owner ~= 'player' or known ~= true or stored ~= false or (mode ~= 'map' and radar ~= true) then return nil end
        if (mode == 'hud' and not C.GetComponentScreenPosition(actor, 32, 32).onScreen)
                or (mode == 'hudmap' and not within_cockpit_radar(cockpit, actor)) then return nil end
        local sector = GetComponentData(sector_ref, 'macro')
        local code_ptr = C.GetObjectIDCode(actor)
        if not valid_sector(sector) or code_ptr == nil or code_ptr == ffi.NULL then return nil end
        local code = ffi.string(code_ptr)
        local current_cockpit, current_map = cockpit_view()
        if not valid_code(code) or current_cockpit ~= cockpit or current_map ~= map_open
                or (mode ~= 'map' and hud_target() ~= actor) then return nil end
        if mode == 'hudmap' and (last_hud_target.space ~= space or last_hud_target.sector_macro ~= sector
                or last_hud_target.code ~= code) then return nil end
        return {actor = actor, space = space, sector_macro = sector, code = code, mode = mode,
                ref = map_target and ship_ref or nil}
    end

    local function target()
        local value = candidate()
        if not value or (last_hud_target and value.actor ~= last_hud_target.actor) then last_hud_target = nil end
        return value
    end

    return M.new(pinned, own_index, {
        scope = map_target and 'm' or 'h',
        clock = GetCurRealTime, target = target, parse_actor = resolve.parse_hex,
        confirm = function(value)
            if value.mode == 'hud' then last_hud_target = value end
        end,
        register = function(callback) return RegisterEvent(SCREEN .. '.Result', callback) ~= false end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Read', args) ~= false end,
    })
end

function M.new_x4(pinned, own_index, registry_ready)
    return new_x4(pinned, own_index, registry_ready)
end

function M.new_map_x4(pinned, own_index, registry_ready, map_target)
    assert(type(map_target) == 'function')
    return new_x4(pinned, own_index, registry_ready, map_target)
end

X4LAN_COMPANY_OBSERVER_TARGET = M
return M
