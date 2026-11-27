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

local function default_zone_text(zone)
    if type(zone) == 'string' and zone ~= '' then return zone end
    if type(zone) == 'number' and finite(zone) and zone >= 0 and zone % 1 == 0 then
        return ('0x%x'):format(zone)
    end
    return nil
end

function M.new(options, engine)
    assert(type(options) == 'table' and options.enabled == true, 'explicit hull playback opt-in required')
    local pinned, own_index, observer = options.pinned, options.own_index, options.observer
    assert(type(pinned) == 'table' and token(pinned.epoch) and index_in(pinned.issued_count, 200000)
        and index_in(own_index, pinned.issued_count), 'verified local register required')
    assert(type(observer) == 'table' and type(observer.matches) == 'function'
        and type(options.guard) == 'function', 'verified local cockpit observer and guard required')
    local epoch, count = pinned.epoch, pinned.issued_count
    local velocity = options.velocity
    local zone_text = type(engine.zone_text) == 'function' and engine.zone_text or default_zone_text
    local claims = options.exclusive_claims
    local enforce_claims = velocity ~= nil or type(claims) == 'table'
    if enforce_claims then
        if type(claims) ~= 'table' then
            claims = {by_actor = {}, by_slot = {}, fenced_actors = {}}
        else
            claims.by_actor = claims.by_actor or {}
            claims.by_slot = claims.by_slot or {}
            claims.fenced_actors = claims.fenced_actors or {}
        end
    end
    local adapter = {}

    function adapter:tick()
        if velocity and velocity.tick then
            return velocity:tick()
        end
        return true
    end

    function adapter:cleanup_uncertain()
        if velocity and velocity.cleanup_uncertain then
            return velocity:cleanup_uncertain()
        end
        return false, nil
    end

    function adapter:bind(binding)
        if type(binding) ~= 'table' or not COCKPIT_SLOTS[binding.slot] or binding.epoch ~= epoch
                or not index_in(binding.index, count) or binding.index == own_index
                or type(binding.actor) ~= 'string' or type(binding.sector_macro) ~= 'string'
                or type(binding.code) ~= 'string' then return nil end
        if velocity and velocity.tick then pcall(velocity.tick, velocity) end
        local index, actor_text, frame, code, slot = binding.index, binding.actor, binding.sector_macro, binding.code, binding.slot
        if enforce_claims then
            local existing_actor = claims.by_actor[actor_text]
            if existing_actor and existing_actor.tick then pcall(existing_actor.tick) end
            local existing_slot = claims.by_slot[slot]
            if existing_slot and existing_slot ~= existing_actor and existing_slot.tick then
                pcall(existing_slot.tick)
            end
        end
        if velocity and velocity.cleanup_uncertain and velocity:cleanup_uncertain() then
            return nil, 'velocity_cleanup_uncertain'
        end
        if enforce_claims and claims.fenced_actors[actor_text] ~= nil then
            return nil, 'actor_fenced'
        end
        if velocity and velocity.has_pending_stop and velocity:has_pending_stop() then
            return nil, 'stop_pending'
        end
        if enforce_claims then
            local actor_claim = claims.by_actor[actor_text]
            local slot_claim = claims.by_slot[slot]
            if actor_claim ~= nil or slot_claim ~= nil then
                if (actor_claim and actor_claim.pending_stop) or (slot_claim and slot_claim.pending_stop) then
                    return nil, 'stop_pending'
                end
                return nil, 'claim_active'
            end
        end

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
        local claim = enforce_claims and {
            slot = slot, actor = actor_text, index = index, generation = binding.generation,
            pending_stop = false,
            tick = velocity and velocity.tick and function() return velocity:tick() end or nil,
        } or nil
        if enforce_claims then
            claims.by_actor[actor_text] = claim
            claims.by_slot[slot] = claim
        end
        local closed, has_verified_motion = false, false
        local confirmed_cockpit_zone = original_cockpit.zone
        local confirmed_zone, pending_zone = original_target.zone, nil
        local session_cleanup_uncertain, session_cleanup_reason = false, nil
        local stop_watcher_attached = false
        local session = {}

        local function release_claim()
            if not enforce_claims then return end
            if claims.by_actor[actor_text] == claim then claims.by_actor[actor_text] = nil end
            if claims.by_slot[slot] == claim then claims.by_slot[slot] = nil end
        end

        local function fence_actor(reason)
            if not enforce_claims then return end
            if claims.fenced_actors[actor_text] == nil then
                claims.fenced_actors[actor_text] = reason or 'velocity_cleanup_uncertain'
            end
            release_claim()
        end

        local function hold_claim_until_stop_settled()
            if not enforce_claims then return end
            claim.pending_stop = true
            if stop_watcher_attached then return end
            stop_watcher_attached = true
            if velocity and velocity.on_pending_stops_settled then
                velocity:on_pending_stops_settled(function(confirmed, err)
                    if confirmed then
                        release_claim()
                    else
                        session_cleanup_uncertain = true
                        session_cleanup_reason = session_cleanup_reason or err or 'velocity_stop_unproved'
                        fence_actor(session_cleanup_reason)
                    end
                end)
            else
                fence_actor('velocity_stop_unwatchable')
            end
        end

        local function is_pilot()
            return observer.is_active_pilot and observer:is_active_pilot(index) == true
        end

        local function owns_exclusive_binding()
            if not enforce_claims then return true end
            return claims.by_actor[actor_text] == claim and claims.by_slot[slot] == claim
        end

        local function mark_closed()
            closed = true
            if not velocity then release_claim() end
        end

        local function eligible()
            if closed then return false end
            if not owns_exclusive_binding() then
                mark_closed()
                return false
            end
            local checked, cockpit, target = pcall(inspect)
            if not checked or not same_ship_and_sector(cockpit, original_cockpit)
                    or not same_ship_and_sector(target, original_target)
                    or (cockpit.zone ~= original_cockpit.zone and not (has_verified_motion and is_pilot()))
                    or (target.zone ~= confirmed_zone and target.zone ~= pending_zone)
                    or (pending_zone and not is_pilot()) then
                mark_closed()
                return false
            end
            if has_verified_motion and is_pilot() then
                confirmed_cockpit_zone = cockpit.zone
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
                mark_closed()
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

        -- Verify original local cockpit, target, code, zone, and exclusive observer/slot binding
        -- before any native SetObjectSectorPos anchor write (including during stop after view loss).
        local function anchor_live_pose()
            if options.guard() ~= true then return false, 'anchor_guard_failed' end
            if not owns_exclusive_binding() then return false, 'anchor_exclusive_binding_lost' end
            if not observer:matches(epoch, frame, index, actor_text, code) then
                return false, 'anchor_observer_mismatch'
            end
            local ok_c, c = pcall(engine.cockpit)
            local ok_t, t = pcall(engine.target, actor_text)
            if not (ok_c and c and ok_t and t) then
                return false, 'anchor_cockpit_or_target_unavailable'
            end
            if not same_ship_and_sector(c, original_cockpit) then
                return false, 'anchor_cockpit_changed'
            end
            if c.zone ~= original_cockpit.zone and c.zone ~= confirmed_cockpit_zone
                    and not (has_verified_motion and is_pilot()) then
                return false, 'anchor_cockpit_zone_changed'
            end
            if not same_ship_and_sector(t, original_target) or t.actor == c.actor or t.sector ~= c.sector then
                return false, 'anchor_target_changed'
            end
            if t.sector_macro ~= frame or t.code ~= code then
                return false, 'anchor_target_identity_changed'
            end
            if (t.zone ~= confirmed_zone and t.zone ~= pending_zone) or (pending_zone and not is_pilot()) then
                return false, 'anchor_target_zone_changed'
            end
            local target_zone_text = zone_text(t.zone)
            if not target_zone_text then
                return false, 'anchor_zone_unresolvable'
            end
            local ok_r, live = pcall(engine.read, original_target.actor)
            if not (ok_r and position(live) and finite(live.yaw) and finite(live.pitch) and finite(live.roll)) then
                return false, 'anchor_read_failed'
            end
            local ok_z, dest_zone = pcall(engine.destination_zone, original_target.sector, live)
            if not ok_z or dest_zone ~= t.zone then
                return false, 'anchor_destination_zone_mismatch'
            end
            if options.guard() ~= true or not owns_exclusive_binding()
                    or not observer:matches(epoch, frame, index, actor_text, code) then
                return false, 'anchor_binding_lost_before_write'
            end
            local ok_w, dispatched = pcall(engine.write, original_target.actor, original_target.sector, live)
            if not ok_w or dispatched ~= true then
                return false, 'anchor_write_failed'
            end
            return {
                x = live.x, y = live.y, z = live.z,
                yaw = live.yaw, pitch = live.pitch, roll = live.roll,
                zone = target_zone_text, actor = actor_text, sector_macro = frame,
            }
        end

        function session:acknowledged_velocity()
            if not velocity or closed then return nil end
            if velocity.tick then pcall(velocity.tick, velocity) end
            if velocity.cleanup_uncertain and velocity:cleanup_uncertain() then return nil end
            if not self:guard() then return nil end
            local expected_zone = zone_text(pending_zone or confirmed_zone)
            if not expected_zone then return nil end
            return velocity:acknowledged_velocity(binding, expected_zone)
        end

        function session:cleanup_uncertain()
            if velocity and velocity.cleanup_uncertain then
                local v_unc, v_reason = velocity:cleanup_uncertain()
                if v_unc then
                    session_cleanup_uncertain = true
                    session_cleanup_reason = session_cleanup_reason or v_reason
                    fence_actor(session_cleanup_reason)
                end
            end
            return session_cleanup_uncertain, session_cleanup_reason
        end

        function session:write(target, before, recheck, attitude, continuous, linear_velocity)
            -- An older crossing can remain unresolved after a position deviation.
            -- Refusing a new setter cannot certify that crossing.
            if pending_zone then return false end
            if velocity and velocity.tick then pcall(velocity.tick, velocity) end
            if velocity and velocity.cleanup_uncertain then
                local v_unc, v_reason = velocity:cleanup_uncertain()
                if v_unc then
                    session_cleanup_uncertain = true
                    session_cleanup_reason = session_cleanup_reason or v_reason
                    fence_actor(session_cleanup_reason)
                    closed = true
                    return false
                end
            end
            if velocity and velocity.has_pending_stop and velocity:has_pending_stop() then
                return false, nil, 'not_dispatched'
            end
            if not position(target) or not position(before) then return false, nil, 'not_dispatched' end
            local actual = self:read()
            if not actual or not finite(actual.yaw) or not finite(actual.pitch) or not finite(actual.roll) then return false, nil, 'not_dispatched' end
            local dx, dy, dz = target.x - actual.x, target.y - actual.y, target.z - actual.z
            if recheck ~= nil and type(recheck) ~= 'function' then return false, nil, 'not_dispatched' end
            local maximum = recheck and VELOCITY_STEP_LIMIT or MAX_STEP
            if continuous and (not recheck or not self:tracks_active_pilot()) then return false, nil, 'not_dispatched' end
            if not continuous and dx * dx + dy * dy + dz * dz > maximum * maximum then
                return false, recheck and actual or nil, 'not_dispatched'
            end
            local destination_zone = engine.destination_zone(original_target.sector, target)
            if not destination_zone then return false, nil, 'not_dispatched' end
            local crossing = destination_zone ~= confirmed_zone
            if crossing and (not has_verified_motion or not recheck or not is_pilot()) then return false, nil, 'not_dispatched' end
            if not eligible() then return false, nil, 'not_dispatched' end
            -- The native read can differ from the playback observation. Check the
            -- actual displacement and prediction deadline again before dispatch.
            if recheck and recheck(actual) ~= true then return false, nil, 'not_dispatched' end
            local rotation = actual
            if attitude ~= nil then
                rotation = X4LAN_MOTION_BUFFER.copy_attitude(attitude)
                if not rotation then return false, nil, 'not_dispatched' end
            end
            if crossing then pending_zone = destination_zone end
            local ok, dispatched = pcall(engine.write, original_target.actor, original_target.sector, {
                x = target.x, y = target.y, z = target.z,
                yaw = rotation.yaw, pitch = rotation.pitch, roll = rotation.roll,
            })
            if not ok or dispatched ~= true then
                mark_closed()  -- The native call may have had a partial effect.
                return false
            end
            if not self:guard() then
                if velocity then
                    local ok_s, stopped, s_reason = pcall(velocity.stop, velocity, anchor_live_pose, 'post_write_guard')
                    if not ok_s or stopped ~= true then
                        session_cleanup_uncertain = true
                        session_cleanup_reason = session_cleanup_reason or (type(s_reason) == 'string' and s_reason or 'post_write_stop_failed')
                        fence_actor(session_cleanup_reason)
                    end
                end
                return false
            end
            if velocity and linear_velocity then
                local expected_zone = zone_text(crossing and destination_zone or confirmed_zone)
                if not expected_zone then
                    local ok_s, stopped, s_reason = pcall(velocity.stop, velocity, anchor_live_pose, 'zone_unresolvable')
                    if not ok_s or stopped ~= true then
                        session_cleanup_uncertain = true
                        session_cleanup_reason = session_cleanup_reason or (type(s_reason) == 'string' and s_reason or 'zone_stop_failed')
                        fence_actor(session_cleanup_reason)
                        closed = true
                        return false
                    end
                    return true
                end
                local vok, applied, vel_reason = pcall(
                    velocity.apply, velocity, binding, linear_velocity, anchor_live_pose, expected_zone)
                if not vok or applied ~= true then
                    if not (velocity.cleanup_uncertain and velocity:cleanup_uncertain()) then
                        pcall(velocity.stop, velocity, anchor_live_pose, 'apply_failed')
                    end
                    if velocity.cleanup_uncertain and velocity:cleanup_uncertain() then
                        local _, v_reason = velocity:cleanup_uncertain()
                        session_cleanup_uncertain = true
                        session_cleanup_reason = session_cleanup_reason or v_reason or (type(vel_reason) == 'string' and vel_reason)
                        fence_actor(session_cleanup_reason)
                    end
                    closed = true
                    return false
                end
            end
            return true
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
            if velocity and ((velocity.cleanup_uncertain and velocity:cleanup_uncertain())
                    or (velocity.has_pending_stop and velocity:has_pending_stop())) then
                return false
            end
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
                mark_closed()
                return false, 'unconfirmed'
            end
            return true
        end

        function session:close()
            if velocity then
                local ok_s, stopped, s_reason = pcall(velocity.stop, velocity, anchor_live_pose, 'session_close')
                if not ok_s or stopped ~= true then
                    session_cleanup_uncertain = true
                    session_cleanup_reason = session_cleanup_reason or (type(s_reason) == 'string' and s_reason or 'stop_failed')
                end
                if velocity.cleanup_uncertain then
                    local v_unc, v_reason = velocity:cleanup_uncertain()
                    if v_unc then
                        session_cleanup_uncertain = true
                        session_cleanup_reason = session_cleanup_reason or v_reason
                    end
                end
                closed = true
                if session_cleanup_uncertain then
                    fence_actor(session_cleanup_reason)
                elseif velocity.has_pending_stop and velocity:has_pending_stop() then
                    hold_claim_until_stop_settled()
                else
                    release_claim()
                end
                return not session_cleanup_uncertain, session_cleanup_reason
            end
            release_claim()
            closed = true
            return not session_cleanup_uncertain, session_cleanup_reason
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

    local HEX_DIGITS = '0123456789abcdef'
    local function u64_decimal_to_hex(dec)
        if type(dec) ~= 'string' or not dec:match('^[0-9]+$') then return nil end
        local digits = {}
        for i = 1, #dec do digits[i] = dec:byte(i) - 48 end
        local out = {}
        while #digits > 0 do
            local rem, next_digits = 0, {}
            for i = 1, #digits do
                local cur = rem * 10 + digits[i]
                local q = math.floor(cur / 16)
                rem = cur - q * 16
                if #next_digits > 0 or q > 0 then next_digits[#next_digits + 1] = q end
            end
            out[#out + 1] = HEX_DIGITS:sub(rem + 1, rem + 1)
            digits = next_digits
        end
        if #out == 0 then return nil end
        local rev = {}
        for i = #out, 1, -1 do rev[#rev + 1] = out[i] end
        return '0x' .. table.concat(rev)
    end

    local function zone_text(id)
        if type(id) == 'string' and id ~= '' then return id end
        if id == nil or id == 0 then return nil end
        local u = resolve.actor_u64(id)
        if not u or u == 0 then return nil end
        if type(u) == 'number' then
            if finite(u) and u >= 0 and u % 1 == 0 then return ('0x%x'):format(u) end
            return nil
        end
        return u64_decimal_to_hex((tostring(u):gsub('ULL$', '')))
    end

    return M.new(options, {
        cockpit = cockpit,
        target = target,
        zone_text = zone_text,
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
