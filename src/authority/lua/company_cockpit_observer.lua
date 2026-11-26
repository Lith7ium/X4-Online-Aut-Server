-- Opt-in read-only cockpit Company observer with active-pilot preference and HUD/radar fallbacks.
local M = {}
local SCREEN = 'X4AuthorityCompanyCockpitObserver'
local INTERVAL, FRESHNESS = 0.15, 0.45
local MAX_INDEX = 200000
local MAX_PILOTS = 3
local COCKPIT_SLOTS = {'cockpit', 'cockpit2', 'cockpit3'}

M.MAX_PILOTS = MAX_PILOTS
M.COCKPIT_SLOTS = COCKPIT_SLOTS

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
    }
end

function M.new(pinned, own_index, engine)
    local epoch, nonce, count = pinned.epoch, pinned.nonce, pinned.issued_count
    assert(token(epoch) and token(nonce) and index_in(count, MAX_INDEX) and index_in(own_index, count))
    local target_index = engine.target_index
    local pending, observed, last, next_poll
    local sequence = 0
    local closed = false
    local api = {}
    local reported = {}

    local function clear_observation(reason)
        if observed == nil then return end
        observed = nil
        if target_index ~= nil and engine.on_withdrawal and not reported[reason] then
            reported[reason] = true
            pcall(engine.on_withdrawal, reason)
        end
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
            if now >= observed.deadline then
                clear_observation('expired')
            elseif probe == nil then
                clear_observation('probe_unavailable')
            else
                -- Keep an indexed radar binding while the HUD points elsewhere.
                local still_valid = probe.mode ~= 'radar' and same_hull(observed.hull, probe)
                if not still_valid and (probe.mode == 'radar' or target_index ~= nil) then
                    still_valid = probe.mode == 'radar'
                    if engine.validate_actor then
                        local ok, valid = pcall(engine.validate_actor, observed.hull)
                        still_valid = ok and valid == true
                    end
                end
                if not still_valid then clear_observation('revalidation_failed') end
            end
        end
        return probe, now
    end

    local function receive(_, row)
        if not pending or type(row) ~= 'string' or row:sub(1, #pending.id + 1) ~= pending.id .. '|' then return end
        local request = pending
        local probe = inspect()
        local eligible = pending == request and probe ~= nil
        pending = nil
        if not eligible or #row > 416 then
            clear_observation('response_rejected')
            return
        end
        local id, actual_epoch, status, index_text, actor_text, sector, code, rmode =
            row:match('^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$')
        local index = tonumber(index_text)
        local mode_matches = rmode == request.probe.mode
            or (target_index ~= nil and rmode == 'radar')
        local actor_id = actor_text and engine.parse_actor(actor_text) or nil
        if id ~= request.id or actual_epoch ~= epoch or status ~= 'OBSERVED'
                or not index_in(index, count) or index == own_index or tostring(index) ~= index_text
                or not valid_sector(sector) or not valid_code(code) or not actor_id
                or (target_index ~= nil and index ~= target_index) or not mode_matches then
            clear_observation('response_rejected')
            return
        end
        local hull
        if rmode == 'radar' then
            hull = engine.verify_radar_actor and engine.verify_radar_actor(actor_id, sector, code) or nil
            if not hull then
                clear_observation('observation_rejected')
                return
            end
        else
            if sector ~= probe.sector_macro or code ~= probe.code or actor_id ~= probe.actor then
                clear_observation('observation_rejected')
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
                mode = probe.mode, target_index = target_index,
            })
            if not ok or issued ~= true then
                pending = nil
                clear_observation('issue_failed')
            end
        end
        return clone_row(observed and observed.row)
    end

    function api:current()
        inspect()
        return clone_row(observed and observed.row)
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

function M.new_multi(pinned, own_index, max_pilots, engine)
    local epoch, nonce, count = pinned.epoch, pinned.nonce, pinned.issued_count
    assert(token(epoch) and token(nonce) and index_in(count, MAX_INDEX) and index_in(own_index, count))
    max_pilots = max_pilots or MAX_PILOTS
    assert(type(max_pilots) == 'number' and max_pilots % 1 == 0 and max_pilots >= 1 and max_pilots <= MAX_PILOTS,
        'invalid max_pilots')
    local slot_names = {}
    for k = 1, max_pilots do slot_names[k] = COCKPIT_SLOTS[k] end

    local pending, last, next_poll
    local slots, roster = {}, {}
    local sequence = 0
    local closed = false

    local function clear_all()
        for k = 1, max_pilots do slots[k] = nil end
    end

    local function inspect()
        if closed then return nil end
        local now = engine.clock()
        if type(now) ~= 'number' or not (now >= 0 and now < math.huge)
                or (last ~= nil and now < last) then
            pending, closed = nil, true
            clear_all()
            return nil
        end
        last = now
        local ok, probe = pcall(engine.probe)
        if not ok then probe = nil end
        if pending and (now >= pending.deadline or probe == nil) then
            pending = nil
        end
        if probe == nil then
            clear_all()
            return nil, now
        end
        for k = 1, max_pilots do
            local entry = slots[k]
            if entry then
                local still_valid = false
                if now < entry.deadline then
                    if probe.hud_hull and same_hull(entry.hull, probe.hud_hull) then
                        still_valid = true
                    elseif engine.validate_actor == nil then
                        still_valid = true
                    else
                        local vok, res = pcall(engine.validate_actor, entry.hull)
                        still_valid = vok and res == true
                    end
                end
                if not still_valid then
                    slots[k] = nil
                end
            end
        end
        return probe, now
    end

    local function receive(_, row)
        if not pending or type(row) ~= 'string' or row:sub(1, #pending.id + 1) ~= pending.id .. '|' then return end
        local request = pending
        local probe = inspect()
        local eligible = pending == request and probe ~= nil
        pending = nil
        if not eligible or #row > 1024 then clear_all(); return end
        local radar_range
        if engine.read_radar_range then
            local response, meters = row:match('^(.*)|(%d+)$')
            radar_range = tonumber(meters)
            if not radar_range or radar_range >= math.huge then clear_all(); return end
            row = response
        end
        local id, actual_epoch, status, count_text, entries_text =
            row:match('^([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]*)$')
        local reported = tonumber(count_text)
        if id ~= request.id or actual_epoch ~= epoch or status ~= 'MULTI'
                or type(reported) ~= 'number' or reported % 1 ~= 0 or reported < 0 or reported > max_pilots
                or tostring(reported) ~= count_text then
            clear_all()
            return
        end
        local candidates = {}
        local seen_index, seen_actor = {}, {}
        if reported > 0 and entries_text ~= '' and entries_text ~= '-' then
            for chunk in (entries_text .. ';'):gmatch('([^;]*);') do
                if chunk ~= '' and #candidates < max_pilots then
                    local index_text, actor_text, sector, code, rmode =
                        chunk:match('^([^,]+),([^,]+),([^,]+),([^,]+),([^,]+)$')
                    local index = tonumber(index_text)
                    local actor_id = actor_text and engine.parse_actor(actor_text) or nil
                    local actor_key = actor_id ~= nil and tostring(actor_id) or nil
                    if index_in(index, count) and index ~= own_index and tostring(index) == index_text
                            and valid_sector(sector) and valid_code(code) and actor_id and actor_key
                            and not seen_index[index] and not seen_actor[actor_key]
                            and (rmode == 'hud' or rmode == 'hudmap' or rmode == 'radar') then
                        local hull, final_mode
                        if (rmode == 'hud' or rmode == 'hudmap') and probe.hud_hull
                                and probe.hud_hull.actor == actor_id
                                and probe.hud_hull.sector_macro == sector
                                and probe.hud_hull.code == code then
                            hull = probe.hud_hull
                            final_mode = rmode
                            if engine.confirm then engine.confirm(hull, rmode) end
                        elseif engine.verify_radar_actor then
                            hull = engine.verify_radar_actor(actor_id, sector, code)
                            final_mode = hull and 'radar' or nil
                        end
                        if hull and final_mode then
                            seen_index[index] = true
                            seen_actor[actor_key] = true
                            candidates[#candidates + 1] = {
                                hull = hull,
                                mode = final_mode,
                                radar_range = radar_range,
                                deadline = request.deadline,
                                row = {
                                    epoch = epoch, sector_macro = sector, index = index,
                                    code = code, actor = actor_text, mode = final_mode,
                                },
                            }
                        end
                    end
                end
            end
        end

        -- Sticky slot assignment: key by Lua integer index (never uint64_t cdata, which LuaJIT hashes by identity)
        -- and verify exact actor/sector/code value equality so slots never churn when distances cross or another pilot leaves.
        local by_index = {}
        for _, cand in ipairs(candidates) do
            by_index[cand.row.index] = cand
        end
        for k = 1, max_pilots do
            local existing = slots[k]
            if existing then
                local matched = by_index[existing.row.index]
                if matched and matched.hull.actor == existing.hull.actor
                        and matched.row.sector_macro == existing.row.sector_macro
                        and matched.row.code == existing.row.code then
                    slots[k] = matched
                    matched.assigned = true
                else
                    slots[k] = nil
                end
            end
        end
        for _, cand in ipairs(candidates) do
            if not cand.assigned then
                for k = 1, max_pilots do
                    if slots[k] == nil then
                        slots[k] = cand
                        break
                    end
                end
            end
        end
    end

    local function poll_shared()
        local probe, now = inspect()
        if probe and not pending and (not next_poll or now >= next_poll) then
            if sequence >= 4294967295 then
                closed, pending = true, nil
                clear_all()
                return
            end
            sequence = sequence + 1
            next_poll = now + INTERVAL
            local id = nonce .. '-m-' .. tostring(sequence)
            pending = {id = id, probe = probe, deadline = now + FRESHNESS}
            local ok, issued = pcall(engine.issue, {
                request_id = id, epoch = epoch, count = count, own = own_index,
                read_radar_range = engine.read_radar_range,
                mode = 'multi', max_pilots = max_pilots, hud_mode = probe.hud_mode or 'none',
                pilot1 = roster[1] or 0, pilot2 = roster[2] or 0, pilot3 = roster[3] or 0,
            })
            if not ok or issued ~= true then
                pending = nil
                clear_all()
            end
        end
    end

    local readers = {}
    for k = 1, max_pilots do
        local slot_name = slot_names[k]
        local slot_idx = k
        local reader = {}
        function reader:tick()
            poll_shared()
            local entry = slots[slot_idx]
            return clone_row(entry and entry.row)
        end
        function reader:current()
            inspect()
            local entry = slots[slot_idx]
            return clone_row(entry and entry.row)
        end
        function reader:mode()
            inspect()
            local entry = slots[slot_idx]
            return entry and entry.mode or nil
        end
        function reader:current_radar_range(index)
            local _, now = inspect()
            local entry = slots[slot_idx]
            -- Use the current range from this very ship observation. HUD
            -- visibility alone is insufficient to authorize an alignment.
            if entry and entry.row.index == index and now - (entry.deadline - FRESHNESS) <= .25 then
                return entry.radar_range
            end
        end
        function reader:is_active_pilot(index)
            for _, pilot_index in ipairs(roster) do
                if pilot_index == index then return true end
            end
            return false
        end
        function reader:matches(wanted_epoch, sector, index, actor, code)
            inspect()
            local entry = slots[slot_idx]
            local row = entry and entry.row
            return row ~= nil and row.epoch == wanted_epoch and row.sector_macro == sector
                and row.index == index and row.actor == actor and row.code == code
        end
        function reader:close()
            slots[slot_idx] = nil
            local any_open = false
            for j = 1, max_pilots do
                if slots[j] ~= nil then any_open = true; break end
            end
            if not any_open then pending = nil end
        end
        readers[slot_name] = reader
    end

    local multi = {
        slot_names = slot_names,
        readers = readers,
        close = function()
            closed, pending = true, nil
            clear_all()
        end,
        mode = function(_, slot_name)
            inspect()
            for k = 1, max_pilots do
                if slot_names[k] == (slot_name or 'cockpit') then
                    return slots[k] and slots[k].mode or nil
                end
            end
            return nil
        end,
        bindings = function()
            inspect()
            local out = {}
            for k = 1, max_pilots do
                local name = slot_names[k]
                out[name] = clone_row(slots[k] and slots[k].row)
            end
            return out
        end,
    }
    function multi:set_pilot_roster(indices)
        if closed or type(indices) ~= 'table' then return false end
        local replacement, seen = {}, {}
        local length = 0
        for key, index in pairs(indices) do
            if not index_in(key, MAX_PILOTS) or not index_in(index, MAX_INDEX)
                    or index == own_index or seen[index] then return false end
            seen[index] = true
            length = length + 1
        end
        -- Peers may have joined after this client's checkpoint was loaded.
        -- Pass only preferences that this client can resolve in its register.
        for key = 1, length do
            local index = rawget(indices, key)
            if index == nil then return false end
            if index <= count then replacement[#replacement + 1] = index end
        end
        if roster[1] ~= replacement[1] or roster[2] ~= replacement[2] or roster[3] ~= replacement[3] then
            -- An in-flight result belongs to the old preferences. Let current bindings
            -- expire normally while the next tick requests a fresh result.
            roster, pending, next_poll = replacement, nil, nil
        end
        return true
    end

    assert(engine.register(receive) == true, 'company cockpit multi-observer event unavailable')
    return multi
end

local function make_x4_helpers(registry_ready)
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

    local function probe_hud(cockpit, map_open)
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
                            return hull, mode
                        end
                    end
                end
            end
        end
        if last_hud_target and (not locked or last_hud_target.actor ~= locked) then
            last_hud_target = nil
        end
        return nil, 'none'
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

    return {
        resolve = resolve,
        cockpit_view = cockpit_view,
        probe_hud = probe_hud,
        verify_radar_actor = verify_radar_actor,
        validate_actor = validate_actor,
        clear_hud = function() last_hud_target = nil end,
        confirm = function(hull, mode)
            if mode == 'hud' then last_hud_target = hull end
        end,
    }
end

function M.new_x4(pinned, own_index, registry_ready, options)
    assert(type(registry_ready) == 'function')
    options = options or {}
    local radar_fallback = options.radar_fallback ~= false
    local target_index = options.target_index
    if target_index ~= nil then
        assert(index_in(target_index, pinned.issued_count) and target_index ~= own_index, 'invalid radar target index')
    end
    local h = make_x4_helpers(registry_ready)
    local function probe()
        local cockpit, map_open = h.cockpit_view()
        if not cockpit then h.clear_hud(); return nil end
        local hull = h.probe_hud(cockpit, map_open)
        if hull then return hull end
        if radar_fallback then
            return {mode = 'radar'}
        end
        return nil
    end
    return M.new(pinned, own_index, {
        target_index = radar_fallback and target_index or nil,
        on_withdrawal = options.on_withdrawal or function(reason)
            DebugError('[X4LAN] AUTH_COMPANY_COCKPIT_WITHDRAWAL ' .. reason)
        end,
        clock = options.clock or GetCurRealTime,
        probe = probe,
        parse_actor = h.resolve.parse_hex,
        verify_radar_actor = h.verify_radar_actor,
        validate_actor = h.validate_actor,
        confirm = h.confirm,
        register = function(callback) return RegisterEvent(SCREEN .. '.Result', callback) ~= false end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Read', args) ~= false end,
    })
end

function M.new_multi_x4(pinned, own_index, registry_ready, options)
    assert(type(registry_ready) == 'function')
    options = options or {}
    local max_pilots = options.max_pilots or MAX_PILOTS
    local h = make_x4_helpers(registry_ready)
    local function probe()
        local cockpit, map_open = h.cockpit_view()
        if not cockpit then h.clear_hud(); return nil end
        local hud_hull, hud_mode = h.probe_hud(cockpit, map_open)
        return {mode = 'multi', hud_hull = hud_hull, hud_mode = hud_mode}
    end
    return M.new_multi(pinned, own_index, max_pilots, {
        read_radar_range = options.read_radar_range == true or nil,
        clock = options.clock or GetCurRealTime,
        probe = probe,
        parse_actor = h.resolve.parse_hex,
        verify_radar_actor = h.verify_radar_actor,
        validate_actor = h.validate_actor,
        confirm = h.confirm,
        register = function(callback) return RegisterEvent(SCREEN .. '.Result', callback) ~= false end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Read', args) ~= false end,
    })
end

X4LAN_COMPANY_COCKPIT_OBSERVER = M
return M
