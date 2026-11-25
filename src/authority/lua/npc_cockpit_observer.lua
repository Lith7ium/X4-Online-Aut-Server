-- Opt-in read-only registered-NPC interest observer (verified cockpit radar or map).
-- Strictly complements company_cockpit_observer.lua: accepts ONLY operational undocked
-- non-player AI-piloted ships (owner ~= 'player' and pilot/assigned/ai present) and
-- gates delivery on verified cockpit radar range or a selected, picked local map ship.
local M = {}
local SCREEN = 'X4AuthorityNpcCockpitObserver'
local INTERVAL, FRESHNESS = 0.12, 0.45
local MAX_INDEX = 200000
local LOCAL_MEASUREMENT_LIMIT = 160
local ATTENTION = {visible=true, inzone=true, nearby=true, adjacentroom=true, inroom=true}

local function bounded_number(number, limit)
    return type(number) == 'number' and number == number and math.abs(number) <= limit
end

local function native_velocity(values, sequence, game_before)
    if not values then return nil end
    local serial, attention, observed_time, vx, vy, vz, rx, ry, rz, ux, uy, uz, fx, fy, fz = values:match(
        '^([0-9]+)|([a-z]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)|([^|]+)$')
    if tonumber(serial) ~= sequence or not ATTENTION[attention] then return nil end
    observed_time = tonumber(observed_time)
    if not bounded_number(observed_time, 1e12) or observed_time < 0
            or math.abs(game_before - observed_time) > 0.05 then return nil end
    local velocity = {tonumber(vx), tonumber(vy), tonumber(vz)}
    local right, up, forward = {tonumber(rx), tonumber(ry), tonumber(rz)},
        {tonumber(ux), tonumber(uy), tonumber(uz)}, {tonumber(fx), tonumber(fy), tonumber(fz)}
    local sector_velocity = {}
    for axis = 1, 3 do
        if not bounded_number(velocity[axis], 1e5) or not bounded_number(right[axis], 1.001)
                or not bounded_number(up[axis], 1.001) or not bounded_number(forward[axis], 1.001) then return nil end
    end
    for axis = 1, 3 do
        local value = velocity[1] * right[axis] + velocity[2] * up[axis] + velocity[3] * forward[axis]
        if not bounded_number(value, 1e5) then return nil end
        sector_velocity[axis] = value
    end
    return attention, sector_velocity
end

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
    local measurement_stopped = false
    local measurement_count = 0
    local measurement_sent = false
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

    local function report(state, row, reason, distance, range)
        if engine.on_status then
            pcall(engine.on_status, state, reason, row.sector_macro, row.code, distance, range)
        end
    end

    local function stop_measurement()
        measurement_stopped = true
        if measurement_sent and engine.measurement_end then
            measurement_sent = false
            pcall(engine.measurement_end, target_index, target_code)
        end
    end

    local function clear_observation(reason, detail, distance, range)
        if observed then stop_measurement() end
        if observed == nil then return end
        local previous = observed.row
        observed = nil
        was_withdrawn = true
        stats.withdrawals = stats.withdrawals + 1
        report('END', previous, detail or reason, distance, range)
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
        local ok, probe, reason = pcall(engine.probe)
        if not ok or probe == nil or probe.mode ~= mode
                or (same_map_target and (not probe.actor or not probe.ref or not probe.space or not probe.sector_ref
                                         or not valid_sector(probe.sector_macro) or not valid_code(probe.code))) then
            pending = nil
            clear_observation('probe_unavailable', ok and (reason or 'probe_unavailable') or 'probe_failed')
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
                local vok, valid, why, distance, range = pcall(engine.validate_actor, observed.hull, mode, probe)
                if not vok or valid ~= true then
                    clear_observation('revalidation_failed', vok and (why or 'revalidation_failed') or 'native_check_failed',
                                      distance, range)
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
        local witness
        if request.measurement and #row <= 768 then
            local base, fields = row:match('^(.-)|MEASURED|(.+)$')
            if base then row, witness = base, fields end
        end
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
            local why = 'md_reply_invalid'
            if status == 'NONE' then
                why = 'md_refused'
            elseif target_code and valid_code(code) and code ~= target_code then
                why = 'code_mismatch'
            end
            clear_observation('ineligible_or_out_of_range', why, dist_text, radar_text)
            return
        end
        local hull, why, distance, range
        if engine.verify_actor then
            hull, why, distance, range = engine.verify_actor(actor_id, sector, code, mode, probe)
        end
        if not hull or (same_map_target and not same_map_target(hull, request.target)) then
            stats.rejections = stats.rejections + 1
            clear_observation('native_verification_failed', why or 'target_binding_changed', distance, range)
            return
        end
        if request.measurement and not measurement_stopped then
            local ok, sent = pcall(engine.measure, request.measurement, epoch, index, code, actor_text, hull, witness)
            if not ok then stop_measurement()
            elseif sent == true then measurement_sent = true end
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
            report('VISIBLE', observed.row, 'verified', dist_text, radar_text)
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
            local measurement = engine.measure ~= nil and not measurement_stopped
                and measurement_count < LOCAL_MEASUREMENT_LIMIT
            if measurement then
                measurement_count = measurement_count + 1
                args.local_measurement = measurement_count
            end
            pending = {id = id, deadline = now + FRESHNESS, target = probe, measurement = args.local_measurement}
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
        stop_measurement()
        closed = true
        pending = nil
        if observed then report('END', observed.row, 'reader_closed') end
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
        if registry_ready() ~= true then return nil, 'pilot_registry_not_ready' end
        if not C.IsPlayerValid() then return nil, 'player_unavailable' end
        if C.IsGamePaused() then return nil, 'paused' end
        local ship = C.GetPlayerControlledShipID()
        if ship == 0 or ship ~= C.GetPlayerOccupiedShipID() or ship ~= C.GetPlayerContainerID()
                or not C.IsValidComponent(ship) or not C.IsComponentClass(ship, 'ship') then
            return nil, 'cockpit_binding_changed'
        end
        return ship
    end

    local function within_radar(cockpit, actor)
        local ref = native_ref(cockpit)
        local range = ref and GetComponentData(ref, 'maxradarrange')
        if type(range) ~= 'number' or not (range > 0 and range < math.huge) then
            return false, 'radar_range_unavailable', nil, range
        end
        local distance = tonumber(C.GetDistanceBetween(cockpit, actor))
        if distance == nil or not (distance >= 0) then return false, 'radar_distance_unavailable', distance, range end
        return distance <= range, 'radar_out_of_range', distance, range
    end

    local function check_npc(cockpit, actor_id, expected_sector, expected_code, check_mode, viewed_sector)
        if not actor_id or actor_id == cockpit or not C.IsValidComponent(actor_id)
                or not C.IsComponentOperational(actor_id) or not C.IsComponentClass(actor_id, 'ship') then
            return nil, 'ship_unavailable'
        end
        local space = C.GetContextByClass(actor_id, 'sector', false)
        if space == 0 or not C.IsValidComponent(space) or not C.IsComponentClass(space, 'sector') then
            return nil, 'npc_sector_unavailable'
        end
        if check_mode == 'radar' then
            local own_space = C.GetContextByClass(cockpit, 'sector', false)
            if space ~= own_space then return nil, 'radar_sector_mismatch' end
            local in_range, why, distance, range = within_radar(cockpit, actor_id)
            if not in_range then return nil, why, distance, range end
        elseif check_mode == 'map' then
            if space ~= viewed_sector then return nil, 'map_sector_mismatch' end
        else
            return nil
        end
        local ship_ref, sector_ref = native_ref(actor_id), native_ref(space)
        if not ship_ref or not sector_ref then return nil, 'native_reference_mismatch' end
        local owner, docked, docking, stored, pilot, assigned, ai = GetComponentData(
            ship_ref, 'owner', 'isdocked', 'isdocking', 'isinternallystored', 'pilot', 'assignedpilot', 'assignedaipilot')
        -- Strictly non-player AI-piloted NPC (mutually exclusive with Company player-ship observer).
        if owner == nil or owner == 'player' then return nil, 'owner_mismatch' end
        if docked ~= false or docking ~= false or stored ~= false then return nil, 'docked_or_stored' end
        if not (pilot or assigned or ai) then return nil, 'captain_unavailable' end
        local known, visible = GetComponentData(ship_ref, 'isknown', 'isradarvisible')
        if known ~= true then return nil, 'native_ship_unknown' end
        if check_mode == 'radar' and visible ~= true then return nil, 'native_radar_hidden' end
        local sector_macro = GetComponentData(sector_ref, 'macro')
        local code_ptr = C.GetObjectIDCode(actor_id)
        if not valid_sector(sector_macro) or code_ptr == nil or code_ptr == ffi.NULL then return nil end
        local code = ffi.string(code_ptr)
        if expected_sector and sector_macro ~= expected_sector then return nil, 'npc_sector_changed' end
        if expected_code and code ~= expected_code then return nil, 'code_mismatch' end
        return {actor = actor_id, space = space, sector_macro = sector_macro, code = code, mode = check_mode,
                ref = ship_ref, sector_ref = sector_ref}
    end

    local function measurement(value)
        local text = tostring(value)
        if #text <= 32 and text:match('^[0-9.eE+%-]+%s*[km]*$') then return text end
        return '-'
    end

    local measure
    if options.local_measurement == true then
        assert(mode == 'radar' and own_index == 1073 and options.target_index == 943 and options.target_code == 'MFQ-419')
        if not pcall(ffi.typeof, 'UIPosRot') then
            ffi.cdef('typedef struct {float x,y,z,yaw,pitch,roll;} UIPosRot;')
        end
        pcall(ffi.cdef, 'UIPosRot GetObjectPositionInSector(UniverseID);')
        pcall(ffi.cdef, 'double GetCurrentGameTime(void);')
        local clock = assert(options.clock)
        measure = function(request, epoch, index, code, actor, hull, witness)
            local before = clock()
            local game_before = tonumber(C.GetCurrentGameTime())
            local pose = C.GetObjectPositionInSector(hull.actor)
            local game_after = tonumber(C.GetCurrentGameTime())
            local after = clock()
            assert(before <= after and game_before <= game_after)
            for _, value in ipairs({pose.x, pose.y, pose.z, before, after, game_before, game_after}) do
                assert(type(value) == 'number' and value == value and math.abs(value) < math.huge)
            end
            DebugError(('[X4LAN] AUTH_NPC_LOCAL_POSE %d|%s|%d|%s|%s|%s|%.17g|%.17g|%.17g|%.17g|%.17g,%.17g,%.17g'):format(
                request, epoch, index, code, actor, hull.sector_macro, before, after, game_before, game_after,
                pose.x, pose.y, pose.z))
            if not options.send_measurement then return false end
            if not bounded_number(before, 1e12) or before < 0 or not bounded_number(game_before, 1e12)
                    or game_before < 0 or not actor:match('^0x[0-9a-f]+$') or #actor > 18
                    or not bounded_number(pose.x, 1e7) or not bounded_number(pose.y, 1e7)
                    or not bounded_number(pose.z, 1e7) then return false end
            local attention, rate = native_velocity(witness, request, game_before)
            if not attention then return false end
            return options.send_measurement(('NPC_POINT|%d|%s|%s|%s|%d|%s|'
                .. '%.17g|%.17g|%.17g|%.17g|%.17g|%.17g|%.17g|%.17g'):format(
                    index, code, hull.sector_macro, actor, request, attention, before, game_before,
                    pose.x, pose.y, pose.z, rate[1], rate[2], rate[3]))
        end
    end

    return M.new(pinned, own_index, {
        enabled = true,
        target_index = options.target_index,
        target_code = options.target_code,
        mode = mode,
        initial_interest = options.initial_interest,
        measure = measure,
        measurement_end = measure and options.send_measurement and function(index, code)
            return options.send_measurement(('NPC_END|%d|%s'):format(index, code))
        end or nil,
        clock = options.clock or GetCurRealTime,
        on_withdrawal = options.on_withdrawal,
        on_reacquire = options.on_reacquire,
        on_status = function(state, reason, sector, code, distance, range)
            DebugError(('[X4LAN] AUTH_REGISTERED_NPC_PILOT %s|%s|%d|%s|%s|%s|distance=%s|range=%s'):format(
                state, mode, options.target_index, code, reason, sector, measurement(distance), measurement(range)))
        end,
        probe = function()
            local cockpit, why = cockpit_ship()
            if not cockpit then return nil, why end
            if map_selection then
                local actor, viewed_sector = map_selection:sample()
                if not actor then return nil, 'map_pick_unavailable' end
                return check_npc(cockpit, actor, nil, nil, mode, viewed_sector)
            end
            return {mode = mode}
        end,
        parse_actor = resolve.parse_hex,
        verify_actor = function(actor_id, sector, code, check_mode, probe)
            local cockpit, why = cockpit_ship()
            if not cockpit then return nil, why end
            return check_npc(cockpit, actor_id, sector, code, check_mode, probe.space)
        end,
        validate_actor = function(hull, check_mode, probe)
            local cockpit, why = cockpit_ship()
            if not cockpit or not hull then return false, why end
            local checked, reason, distance, range = check_npc(cockpit, hull.actor, hull.sector_macro, hull.code, check_mode, probe.space)
            if not checked then return false, reason, distance, range end
            return same_hull(checked, hull), 'target_binding_changed'
        end,
        register = function(callback) return RegisterEvent(SCREEN .. '.Result', callback) ~= false end,
        issue = function(args) return AddUITriggeredEvent(SCREEN, 'Read', args) ~= false end,
    })
end

X4LAN_NPC_COCKPIT_OBSERVER = M
return M
