-- Opt-in read-only cockpit Company observer: selected HUD target first, then nearby radar-visible Company pilot without target lock.
local M = {}
local SCREEN = 'X4AuthorityCompanyCockpitObserver'
local INTERVAL, FRESHNESS = 0.15, 0.45
local MAX_INDEX = 200000

local function token(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function index_in(value, count)
    return type(value) == 'number' and value >= 1 and value <= count and value % 1 == 0
end

local function valid_code(value)
    return type(value) == 'string' and #value <= 32 and value:match('^[A-Z0-9]+%-%d+$') ~= nil
end

local function valid_sector(value)
    return type(value) == 'string' and #value <= 128 and value:match('^[A-Za-z0-9_]+$') ~= nil
end

local function same_hull(a, b)
    return a ~= nil and b ~= nil and a.actor == b.actor and a.space == b.space
        and a.sector_macro == b.sector_macro and a.code == b.code
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
        local ok, probe = pcall(engine.probe)
        if not ok then probe = nil end
        if pending then
            local expired = now >= pending.deadline
            local mismatch = false
            if probe == nil then
                mismatch = true
            elseif pending.probe.mode ~= 'radar' then
                mismatch = not same_hull(pending.probe, probe) or pending.probe.mode ~= probe.mode
            elseif probe.mode ~= 'radar' then
                mismatch = true
            end
            if expired or mismatch then pending = nil end
        end
        if observed then
            local expired = now >= observed.deadline
            local still_valid = false
            if not expired and probe ~= nil then
                if probe.mode == 'radar' then
                    still_valid = (engine.validate_actor == nil)
                        or (pcall(engine.validate_actor, observed.hull) and engine.validate_actor(observed.hull) == true)
                else
                    -- Seamless hud <-> radar handoff when the same Company ship remains eligible.
                    still_valid = same_hull(observed.hull, probe)
                end
            end
            if not still_valid then observed = nil end
        end
        return probe, now
    end

    local function receive(_, row)
        if not pending or type(row) ~= 'string' or row:sub(1, #pending.id + 1) ~= pending.id .. '|' then return end
        local request = pending
        local probe = inspect()
        local eligible = pending == request and probe ~= nil
        pending = nil
        if not eligible or #row > 416 then observed = nil; return end
        local id, actual_epoch, status, index_text, actor_text, sector, code, rmode =
            row:match('^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$')
        local index = tonumber(index_text)
        local actor_id = actor_text and engine.parse_actor(actor_text) or nil
        if id ~= request.id or actual_epoch ~= epoch or status ~= 'OBSERVED'
                or not index_in(index, count) or index == own_index or tostring(index) ~= index_text
                or not valid_sector(sector) or not valid_code(code) or not actor_id
                or rmode ~= request.probe.mode then
            observed = nil
            return
        end
        local hull
        if rmode == 'radar' then
            hull = engine.verify_radar_actor and engine.verify_radar_actor(actor_id, sector, code) or nil
            if not hull then observed = nil; return end
        else
            if sector ~= probe.sector_macro or code ~= probe.code or actor_id ~= probe.actor then
                observed = nil
                return
            end
            hull = probe
        end
        if engine.confirm then engine.confirm(hull, rmode) end
        observed = {
            hull = hull,
            mode = rmode,
            deadline = request.deadline,
            row = {
                epoch = epoch, sector_macro = sector, index = index,
                code = code, actor = actor_text, mode = rmode,
            },
        }
    end

    local function current_binding()
        if not observed then return nil end
        local row = observed.row
        return {
            epoch = row.epoch, sector_macro = row.sector_macro, index = row.index,
            code = row.code, actor = row.actor, mode = row.mode,
        }
    end

    function api:tick()
        local probe, now = inspect()
        if probe and not pending and (not next_poll or now >= next_poll) then
            if sequence >= 4294967295 then self:close(); return end
            sequence = sequence + 1
            next_poll = now + INTERVAL
            local id = nonce .. '-c-' .. tostring(sequence)
            pending = {id = id, probe = probe, deadline = now + FRESHNESS}
            local ok, issued = pcall(engine.issue, {
                request_id = id, epoch = epoch, count = count, own = own_index,
                mode = probe.mode, target_index = probe.target_index,
            })
            if not ok or issued ~= true then pending, observed = nil, nil end
        end
        return current_binding()
    end

    function api:current()
        inspect()
        return current_binding()
    end

    function api:mode()
        inspect()
        return observed and observed.mode or nil
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

    assert(engine.register(receive) == true, 'company cockpit observation event unavailable')
    return api
end

function M.new_x4(pinned, own_index, registry_ready, options)
    assert(type(registry_ready) == 'function')
    options = options or {}
    local radar_fallback = options.radar_fallback ~= false
    local target_index = options.target_index
    if target_index ~= nil then
        assert(index_in(target_index, pinned.issued_count) and target_index ~= own_index, 'invalid radar target index')
    end
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
    local last_hud_target = nil

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

    local function check_company_ship(cockpit, actor, require_on_screen, require_radar_range)
        if not actor or actor == cockpit or not C.IsValidComponent(actor)
                or not C.IsComponentOperational(actor) or not C.IsComponentClass(actor, 'ship') then
            return nil
        end
        local space = C.GetContextByClass(actor, 'sector', false)
        if space == 0 or not C.IsValidComponent(space) or not C.IsComponentClass(space, 'sector')
                or space ~= C.GetContextByClass(cockpit, 'sector', false) then
            return nil
        end
        local ship_ref, sector_ref = native_ref(actor), native_ref(space)
        if not ship_ref or not sector_ref then return nil end
        local owner, known, radar, stored, docked, docking, pilot, assigned, ai = GetComponentData(
            ship_ref, 'owner', 'isknown', 'isradarvisible', 'isinternallystored',
            'isdocked', 'isdocking', 'pilot', 'assignedpilot', 'assignedaipilot')
        if owner ~= 'player' or known ~= true or radar ~= true or stored ~= false
                or docked ~= false or docking ~= false or pilot or assigned or ai then
            return nil
        end
        if require_on_screen and not C.GetComponentScreenPosition(actor, 32, 32).onScreen then
            return nil
        end
        if require_radar_range and not within_cockpit_radar(cockpit, actor) then
            return nil
        end
        local sector = GetComponentData(sector_ref, 'macro')
        local code_ptr = C.GetObjectIDCode(actor)
        if not valid_sector(sector) or code_ptr == nil or code_ptr == ffi.NULL then return nil end
        local code = ffi.string(code_ptr)
        if not valid_code(code) then return nil end
        return {actor = actor, space = space, sector_macro = sector, code = code}
    end

    local function probe()
        local cockpit, map_open = cockpit_view()
        if not cockpit then last_hud_target = nil; return nil end
        -- 1. Selected HUD target takes priority when eligible.
        local locked = hud_target()
        if locked then
            local mode = map_open and 'hudmap' or 'hud'
            local hud_allowed = true
            if mode == 'hudmap' then
                hud_allowed = (last_hud_target ~= nil and last_hud_target.actor == locked)
            else
                hud_allowed = C.IsHUDActive() and not C.IsExternalTargetMode() and not C.IsExternalViewActive()
            end
            if hud_allowed then
                local hull = check_company_ship(cockpit, locked, mode == 'hud', mode == 'hudmap')
                if hull then
                    local cur_cockpit, cur_map = cockpit_view()
                    if cur_cockpit == cockpit and cur_map == map_open and hud_target() == locked then
                        if mode == 'hudmap' and (last_hud_target.space ~= hull.space
                                or last_hud_target.sector_macro ~= hull.sector_macro
                                or last_hud_target.code ~= hull.code) then
                            last_hud_target = nil
                        else
                            hull.mode = mode
                            return hull
                        end
                    end
                end
            end
        end
        if last_hud_target and (not locked or last_hud_target.actor ~= locked) then
            last_hud_target = nil
        end
        -- 2. Opt-in nearby radar-visible Company pilot without requiring a HUD target lock.
        if radar_fallback then
            return {mode = 'radar', target_index = target_index}
        end
        return nil
    end

    local function verify_radar_actor(actor_id, expected_sector, expected_code)
        local cockpit = cockpit_view()
        if not cockpit then return nil end
        local hull = check_company_ship(cockpit, actor_id, false, true)
        if not hull or hull.sector_macro ~= expected_sector or hull.code ~= expected_code then
            return nil
        end
        hull.mode = 'radar'
        return hull
    end

    local function validate_actor(hull)
        local cockpit = cockpit_view()
        if not cockpit or not hull then return false end
        local checked = check_company_ship(cockpit, hull.actor, false, true)
        return checked ~= nil and same_hull(checked, hull)
    end

    return M.new(pinned, own_index, {
        clock = options.clock or GetCurRealTime,
        probe = probe,
        parse_actor = resolve.parse_hex,
        verify_radar_actor = verify_radar_actor,
        validate_actor = validate_actor,
        confirm = function(hull, mode)
            if mode == 'hud' then last_hud_target = hull end
        end,
        register = function(callback) return RegisterEvent(SCREEN .. '.Result', callback) ~= false end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Read', args) ~= false end,
    })
end

X4LAN_COMPANY_COCKPIT_OBSERVER = M
return M
