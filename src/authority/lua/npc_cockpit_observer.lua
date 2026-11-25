-- Opt-in read-only registered-NPC interest observer (verified cockpit radar or map).
-- Strictly complements company_cockpit_observer.lua: accepts ONLY operational undocked
-- non-player AI-piloted ships (owner ~= 'player' and pilot/assigned/ai present) and
-- gates delivery on verified cockpit radar range or a selected, picked local map ship.
local M = {}
local SCREEN = 'X4AuthorityNpcCockpitObserver'
local INTERVAL, FRESHNESS = 0.12, 0.45
local MAX_INDEX = 200000

local function token(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function index_in(value, count)
    return type(value) == 'number' and value >= 1 and value <= count and value % 1 == 0
end

local function valid_code(value)
    return type(value) == 'string'
        and value:match('^[A-Z0-9][A-Z0-9][A-Z0-9]%-[A-Z0-9][A-Z0-9][A-Z0-9]$') ~= nil
end

local function valid_sector(value)
    return type(value) == 'string' and #value <= 128 and value:match('^[A-Za-z0-9_]+$') ~= nil
end

local function same_hull(a, b)
    return a ~= nil and b ~= nil and a.actor == b.actor and a.space == b.space
        and a.sector_macro == b.sector_macro and a.code == b.code
end

local function clone_row(row)
    if not row then return nil end
    return {
        epoch = row.epoch, sector_macro = row.sector_macro, index = row.index,
        code = row.code, actor = row.actor, mode = row.mode,
        dist_m = row.dist_m, radar_range_m = row.radar_range_m,
    }
end

function M.new(pinned, own_index, engine)
    if type(engine) ~= 'table' or engine.enabled ~= true then
        return nil, 'disabled'
    end
    local epoch, nonce, count = pinned.epoch, pinned.nonce, pinned.issued_count
    assert(token(epoch) and token(nonce) and index_in(count, MAX_INDEX) and index_in(own_index, count),
        'verified registry metadata required')
    local target_index = engine.target_index
    assert(index_in(target_index, count) and target_index ~= own_index, 'valid registered NPC target_index required')
    local target_code = engine.target_code
    assert(target_code == nil or valid_code(target_code), 'invalid registered NPC ship code')
    local mode = engine.mode or 'radar'
    assert(mode == 'radar' or mode == 'map', 'mode must be radar or map')
    local same_map_target
    if mode == 'map' then
        same_map_target = assert(X4LAN_COMPANY_OBSERVER_TARGET and X4LAN_COMPANY_OBSERVER_TARGET.same_target,
                                 'verified map target comparison required')
    end

    local interest_active = engine.initial_interest ~= false
    local pending, observed, last, next_poll
    local sequence = 0
    local closed = false
    local ever_observed = false
    local was_withdrawn = false
    local stats = {
        mode = mode,
        observations = 0,
        withdrawals = 0,
        reacquisitions = 0,
        rejections = 0,
    }

    local function clear_observation(reason)
        if observed == nil then return end
        observed = nil
        was_withdrawn = true
        stats.withdrawals = stats.withdrawals + 1
        if engine.on_withdrawal then pcall(engine.on_withdrawal, reason) end
    end

    local function inspect()
        if closed then return nil end
        local now = engine.clock()
        if type(now) ~= 'number' or not (now >= 0 and now < math.huge)
                or (last ~= nil and now < last) then
            pending, closed = nil, true
            clear_observation('clock_invalid')
            return nil
        end
        last = now
        if not interest_active then
            pending = nil
            clear_observation('interest_withdrawn')
            return nil, now
        end
        local ok, probe = pcall(engine.probe)
        if not ok or probe == nil or probe.mode ~= mode
                or (same_map_target and (not probe.actor or not probe.ref or not probe.space or not probe.sector_ref
                                         or not valid_sector(probe.sector_macro) or not valid_code(probe.code))) then
            pending = nil
            clear_observation('probe_unavailable')
            return nil, now
        end
        if pending and (now >= pending.deadline or (same_map_target and not same_map_target(pending.target, probe))) then
            pending = nil
        end
        if observed then
            if now >= observed.deadline then
                clear_observation('expired')
            elseif same_map_target and not same_map_target(observed.hull, probe) then
                clear_observation('map_target_changed')
            elseif engine.validate_actor then
                local vok, valid = pcall(engine.validate_actor, observed.hull, mode, probe)
                if not vok or valid ~= true then
                    clear_observation('revalidation_failed')
                end
            end
        end
        return probe, now
    end

    local function receive(_, row)
        if not pending or type(row) ~= 'string' or row:sub(1, #pending.id + 1) ~= pending.id .. '|' then
            return
        end
        local request = pending
        local probe, now = inspect()
        local eligible = pending == request and probe ~= nil and interest_active
        pending = nil
        if not eligible or #row > 448 then
            stats.rejections = stats.rejections + 1
            clear_observation('response_rejected')
            return
        end
        local id, actual_epoch, status, index_text, actor_text, sector, code, rmode, dist_text, radar_text =
            row:match('^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$')
        local index = tonumber(index_text)
        local actor_id = actor_text and engine.parse_actor(actor_text) or nil
        if id ~= request.id or actual_epoch ~= epoch or status ~= 'OBSERVED'
                or index ~= target_index or index == own_index or rmode ~= mode
                or not valid_sector(sector) or not valid_code(code) or (target_code and code ~= target_code) or not actor_id then
            stats.rejections = stats.rejections + 1
            clear_observation('ineligible_or_out_of_range')
            return
        end
        local hull = engine.verify_actor and engine.verify_actor(actor_id, sector, code, mode, probe) or nil
        if not hull or (same_map_target and not same_map_target(hull, request.target)) then
            stats.rejections = stats.rejections + 1
            clear_observation('native_verification_failed')
            return
        end
        local was_new = (observed == nil)
        observed = {
            row = {
                epoch = epoch,
                sector_macro = sector,
                index = index,
                code = code,
                actor = actor_text,
                mode = mode,
                dist_m = tonumber(dist_text),
                radar_range_m = tonumber(radar_text),
            },
            hull = hull,
            deadline = now + FRESHNESS,
        }
        stats.observations = stats.observations + 1
        if was_new then
            if ever_observed and was_withdrawn then
                stats.reacquisitions = stats.reacquisitions + 1
                was_withdrawn = false
                if engine.on_reacquire then pcall(engine.on_reacquire, clone_row(observed.row)) end
            end
            ever_observed = true
        end
    end

    assert(engine.register(receive) == true, 'failed to register NPC observer event')

    local api = {}

    function api:set_interest(active)
        local flag = (active == true)
        if interest_active == flag then return end
        interest_active = flag
        if not flag then
            pending = nil
            clear_observation('interest_withdrawn')
        else
            next_poll = nil
        end
    end

    function api:withdraw()
        self:set_interest(false)
    end

    function api:reacquire()
        self:set_interest(true)
    end

    function api:tick()
        local probe, now = inspect()
        if not probe then return nil end
        if not pending and (next_poll == nil or now >= next_poll) then
            if sequence >= 4294967295 then self:close(); return nil end
            sequence = sequence + 1
            local id = nonce .. '-npc-' .. mode .. '-' .. tostring(sequence)
            local args = {
                request_id = id,
                epoch = epoch,
                count = count,
                own = own_index,
                target_index = target_index,
                mode = mode,
                target = probe.ref,
                view_space = probe.sector_ref,
            }
            pending = {id = id, deadline = now + FRESHNESS, target = probe}
            next_poll = now + INTERVAL
            if engine.issue(args) ~= true then
                pending = nil
                clear_observation('issue_failed')
            end
        end
        return observed and clone_row(observed.row) or nil
    end

    function api:matches(expected_epoch, sector_macro, index, actor_text, code)
        if not inspect() or not observed then return false end
        local row = observed.row
        return row.epoch == expected_epoch and row.sector_macro == sector_macro
            and row.index == index and row.actor == actor_text and row.code == code
    end

    function api:mode()
        return observed and observed.row.mode or mode
    end

    function api:stats()
        local out = {}
        for k, v in pairs(stats) do out[k] = v end
        out.interest_active = interest_active
        out.currently_observed = observed ~= nil
        out.last_dist_m = observed and observed.row.dist_m or nil
        out.last_radar_range_m = observed and observed.row.radar_range_m or nil
        return out
    end

    function api:close()
        closed = true
        pending = nil
        observed = nil
    end

    return api
end

function M.new_x4(pinned, own_index, registry_ready, options)
    assert(type(registry_ready) == 'function', 'registry_ready predicate required')
    assert(type(options) == 'table' and options.enabled == true, 'explicit NPC observer opt-in required')
    local ffi = require('ffi')
    if not pcall(ffi.typeof, 'UniverseID') then ffi.cdef('typedef uint64_t UniverseID;') end
    for _, decl in ipairs({
        'bool IsPlayerValid(void);',
        'bool IsGamePaused(void);',
        'bool IsValidComponent(UniverseID);',
        'bool IsComponentOperational(UniverseID);',
        'bool IsComponentClass(UniverseID, const char*);',
        'UniverseID GetPlayerControlledShipID(void);',
        'UniverseID GetPlayerOccupiedShipID(void);',
        'UniverseID GetPlayerContainerID(void);',
        'UniverseID GetContextByClass(UniverseID, const char*, bool);',
        'const char* GetObjectIDCode(UniverseID);',
        'float GetDistanceBetween(UniverseID, UniverseID);',
    }) do pcall(ffi.cdef, decl) end

    local C, resolve = ffi.C, assert(X4LAN_RESOLVE_STRING, 'X4LAN_RESOLVE_STRING required')
    local mode = options.mode or 'radar'
    local map_selection = mode == 'map' and assert(X4LAN_MAP_RENDER_OBSERVATION).new_selected_x4({
        require_picked_sector = true,
    })

    local function native_ref(id)
        local ref = ConvertStringToLuaID((tostring(id):gsub('ULL$', '')))
        if not ref or resolve.actor_u64(ConvertIDTo64Bit(ref)) ~= id then return nil end
        return ref
    end

    local function cockpit_ship()
        if registry_ready() ~= true or not C.IsPlayerValid() or C.IsGamePaused() then return nil end
        local ship = C.GetPlayerControlledShipID()
        if ship == 0 or ship ~= C.GetPlayerOccupiedShipID() or ship ~= C.GetPlayerContainerID()
                or not C.IsValidComponent(ship) or not C.IsComponentClass(ship, 'ship') then
            return nil
        end
        return ship
    end

    local function within_radar(cockpit, actor)
        local ref = native_ref(cockpit)
        local range = ref and GetComponentData(ref, 'maxradarrange')
        if type(range) ~= 'number' or not (range > 0 and range < math.huge) then return false end
        local distance = tonumber(C.GetDistanceBetween(cockpit, actor))
        return distance ~= nil and distance >= 0 and distance <= range
    end

    local function check_npc(cockpit, actor_id, expected_sector, expected_code, check_mode, viewed_sector)
        if not actor_id or actor_id == cockpit or not C.IsValidComponent(actor_id)
                or not C.IsComponentOperational(actor_id) or not C.IsComponentClass(actor_id, 'ship') then
            return nil
        end
        local space = C.GetContextByClass(actor_id, 'sector', false)
        if space == 0 or not C.IsValidComponent(space) or not C.IsComponentClass(space, 'sector') then
            return nil
        end
        if check_mode == 'radar' then
            local own_space = C.GetContextByClass(cockpit, 'sector', false)
            if space ~= own_space or not within_radar(cockpit, actor_id) then return nil end
        elseif check_mode == 'map' then
            if space ~= viewed_sector then return nil end
        else
            return nil
        end
        local ship_ref, sector_ref = native_ref(actor_id), native_ref(space)
        if not ship_ref or not sector_ref then return nil end
        local owner, docked, docking, stored, pilot, assigned, ai = GetComponentData(
            ship_ref, 'owner', 'isdocked', 'isdocking', 'isinternallystored', 'pilot', 'assignedpilot', 'assignedaipilot')
        -- Strictly non-player AI-piloted NPC (mutually exclusive with Company player-ship observer).
        if owner == nil or owner == 'player' or docked ~= false or docking ~= false or stored ~= false
                or not (pilot or assigned or ai) then
            return nil
        end
        local known, visible = GetComponentData(ship_ref, 'isknown', 'isradarvisible')
        if known ~= true or (check_mode == 'radar' and visible ~= true) then return nil end
        local sector_macro = GetComponentData(sector_ref, 'macro')
        local code_ptr = C.GetObjectIDCode(actor_id)
        if not valid_sector(sector_macro) or code_ptr == nil or code_ptr == ffi.NULL then return nil end
        local code = ffi.string(code_ptr)
        if expected_sector and sector_macro ~= expected_sector then return nil end
        if expected_code and code ~= expected_code then return nil end
        return {actor = actor_id, space = space, sector_macro = sector_macro, code = code, mode = check_mode,
                ref = ship_ref, sector_ref = sector_ref}
    end

    return M.new(pinned, own_index, {
        enabled = true,
        target_index = options.target_index,
        target_code = options.target_code,
        mode = mode,
        initial_interest = options.initial_interest,
        clock = options.clock or GetCurRealTime,
        on_withdrawal = options.on_withdrawal,
        on_reacquire = options.on_reacquire,
        probe = function()
            local cockpit = cockpit_ship()
            if not cockpit then return nil end
            if map_selection then
                local actor, viewed_sector = map_selection:sample()
                return check_npc(cockpit, actor, nil, nil, mode, viewed_sector)
            end
            return {mode = mode}
        end,
        parse_actor = resolve.parse_hex,
        verify_actor = function(actor_id, sector, code, check_mode, probe)
            local cockpit = cockpit_ship()
            if not cockpit then return nil end
            return check_npc(cockpit, actor_id, sector, code, check_mode, probe.space)
        end,
        validate_actor = function(hull, check_mode, probe)
            local cockpit = cockpit_ship()
            if not cockpit or not hull then return false end
            local checked = check_npc(cockpit, hull.actor, hull.sector_macro, hull.code, check_mode, probe.space)
            return checked ~= nil and same_hull(checked, hull)
        end,
        register = function(callback) return RegisterEvent(SCREEN .. '.Result', callback) ~= false end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Read', args) ~= false end,
    })
end

X4LAN_NPC_COCKPIT_OBSERVER = M
return M
