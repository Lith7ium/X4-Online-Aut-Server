-- Opt-in, default-off one-shot (constantvel="false") native linear velocity bridge
-- for the receiving cockpit. Keeps wire protocol, shared server, and clock policy unchanged;
-- enforces guarded pose anchoring before every stop, checks receipt expiry inside the MD
-- callback before accepting any SET/STOP/zero/out-of-order reply, serializes new dispatches
-- behind confirmed stop cleanup, fences delayed set/stop receipts across generation and slot
-- reuse, preserves possible prior physical motion until a matching independently confirmed
-- near-zero receipt arrives, bounds pending receipts, preserves continuous position playback
-- above MAX_SPEED when cleanly stopped, and exposes acknowledged native velocity alongside
-- receipt game_time and request id for established-duration readback.
local M = {}
local SCREEN = 'X4AuthorityCompanyCockpitVelocity'
local COCKPIT_SLOTS = {cockpit = true, cockpit2 = true, cockpit3 = true}
local MAX_SPEED = 2500
local CHANGE_EPS = 0.25
local MIN_INTERVAL = 0.05
local STOP_SPEED_TOLERANCE = 0.05
local STOP_ANCHOR_DRIFT_LIMIT = 3.0
local RECEIPT_TIMEOUT = 1.5
local MAX_PENDING_PER_SLOT = 16
local MAX_GLOBAL_PENDING = 64

local next_request_id = 0
local next_lease_id = 0
local pending_by_id = {}
local global_pending_count = 0
local shared_registered = false

local function finite(v)
    return type(v) == 'number' and v == v and math.abs(v) < math.huge
end

local function hex32(v)
    return type(v) == 'string' and #v == 32 and v:match('^[0-9a-f]+$') ~= nil
end

local function vec(text)
    local sx, sy, sz = (text or ''):match('^([^,]+),([^,]+),([^,]+)$')
    local x, y, z = tonumber(sx), tonumber(sy), tonumber(sz)
    if finite(x) and finite(y) and finite(z) then return {x = x, y = y, z = z} end
end

local function copy_vec(v)
    return v and {x = v.x, y = v.y, z = v.z} or nil
end

local function dist3(a, b)
    local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local function norm3(v)
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
end

local function same_binding(a, b)
    return a and b and a.epoch == b.epoch and a.index == b.index
        and a.actor == b.actor and a.sector_macro == b.sector_macro
        and a.code == b.code and a.generation == b.generation
end

local function handle_result(_, text)
    if type(text) ~= 'string' then return end
    local id_text = text:match('^([^|]+)|')
    local id = tonumber(id_text)
    local entry = id and pending_by_id[id]
    if not entry then return end
    pending_by_id[id] = nil
    global_pending_count = math.max(0, global_pending_count - 1)
    entry.handler(text)
end

local function parse_receipt(text, expected_op, anchor_pose)
    local fields = {}
    for field in (text .. '|'):gmatch('([^|]*)|') do fields[#fields + 1] = field end
    local status = fields[2]
    local lease = tonumber(fields[3])
    local row = {request = tonumber(fields[1]), op = expected_op, status = status, lease = lease}
    if status == 'refused' or (expected_op == 'set' and status ~= 'set')
            or (expected_op == 'stop' and status ~= 'stopped') then
        row.ok = false
        row.md_why = fields[4] ~= '' and fields[4] or nil
        row.fail_reason = status == 'refused' and (expected_op .. '_refused') or 'unexpected_status'
        return row
    end
    local xyz, native = vec(fields[8]), vec(fields[9])
    local right, up, forward = vec(fields[10]), vec(fields[11]), vec(fields[12])
    local age = tonumber(fields[7])
    if not (xyz and native and right and up and forward and finite(age) and finite(lease)) then
        row.ok = false
        row.fail_reason = 'malformed_receipt'
        return row
    end
    local sec_vel = {
        x = native.x * right.x + native.y * up.x + native.z * forward.x,
        y = native.x * right.y + native.y * up.y + native.z * forward.y,
        z = native.x * right.z + native.y * up.z + native.z * forward.z,
    }
    row.ok = true
    row.actor = fields[4]
    row.sector = fields[5]
    row.zone = fields[6]
    row.game_time = age
    row.position = xyz
    row.local_velocity = native
    row.sector_velocity = sec_vel
    row.total_speed_mps = norm3(sec_vel)
    row.forward_speed_mps = sec_vel.x * forward.x + sec_vel.y * forward.y + sec_vel.z * forward.z
    if status == 'set' then
        row.dispatched_velocity = vec(fields[13])
    elseif status == 'stopped' then
        local before = vec(fields[13])
        local before_vel = vec(fields[14])
        row.before_position = before
        row.before_local_velocity = before_vel
        row.snapback_m = before and dist3(xyz, before) or nil
        row.observed_near_zero = row.total_speed_mps <= STOP_SPEED_TOLERANCE
        if anchor_pose then
            row.anchor_drift_m = dist3(xyz, anchor_pose)
        end
    end
    return row
end

function M.new(options)
    assert(type(options) == 'table' and options.enabled == true, 'explicit cockpit native velocity opt-in required')
    local slot = options.slot or 'cockpit'
    assert(COCKPIT_SLOTS[slot], 'invalid cockpit velocity slot')
    local pinned = assert(options.pinned, 'pinned registry required')
    local own_index = assert(options.own_index, 'own_index required')
    local clock = assert(options.clock, 'clock required')
    local issue = options.issue or function(args) return AddUITriggeredEvent(SCREEN, 'Request', args) ~= false end
    local receipt_timeout = finite(options.receipt_timeout) and options.receipt_timeout > 0
        and options.receipt_timeout or RECEIPT_TIMEOUT
    local max_pending = finite(options.max_pending) and options.max_pending >= 1
        and math.floor(options.max_pending) or MAX_PENDING_PER_SLOT

    if options.register then
        assert(options.register(handle_result) ~= false, 'cockpit velocity event registration failed')
    elseif not shared_registered then
        assert(RegisterEvent(SCREEN .. '.Result', handle_result) ~= false, 'cockpit velocity event unavailable')
        shared_registered = true
    end

    local owner_token = {}
    local active = nil
    local pending_local = {}
    local pending_local_count = 0
    local pending_stops = {}
    local pending_stop_count = 0
    local cleanup_uncertain = false
    local uncertain_reason = nil
    local stats = {
        slot = slot,
        sets = 0,
        stops = 0,
        confirmed_stops = 0,
        receipts = 0,
        refusals = 0,
        fenced_receipts = 0,
        timeouts = 0,
        unsupported_speed_skips = 0,
        last_snapback_m = nil,
        last_stop_speed_mps = nil,
        last_anchor_drift_m = nil,
    }

    local function notify_event(row)
        if type(options.on_event) == 'function' then
            pcall(options.on_event, row)
        end
    end

    local function mark_uncertain(reason, context)
        if not cleanup_uncertain then
            cleanup_uncertain = true
            uncertain_reason = reason or 'cleanup_uncertain'
            notify_event({
                op = 'uncertain',
                ok = false,
                status = 'uncertain',
                slot = slot,
                reason = uncertain_reason,
                index = context and context.index or nil,
                code = context and context.code or nil,
                lease = context and (context.lease_id or context.id) or nil,
            })
            if type(options.on_uncertain) == 'function' then
                pcall(options.on_uncertain, uncertain_reason, context)
            end
        end
    end

    local function has_nonzero_acked(lease)
        return lease ~= nil and lease.acked_velocity ~= nil and norm3(lease.acked_velocity) > STOP_SPEED_TOLERANCE
    end

    local function has_pending_set(lease_id)
        for _, entry in pairs(pending_local) do
            if entry.op == 'set' and entry.lease_id == lease_id then return true end
        end
        return false
    end

    local function has_pending_zero_set(lease_id)
        for _, entry in pairs(pending_local) do
            if entry.op == 'set' and entry.lease_id == lease_id and entry.dispatched_velocity
                    and entry.dispatched_velocity.x == 0 and entry.dispatched_velocity.y == 0
                    and entry.dispatched_velocity.z == 0 then
                return true
            end
        end
        return false
    end

    local function lease_needs_stop(lease)
        return lease ~= nil and (
            lease.possible_motion or has_nonzero_acked(lease) or has_pending_set(lease.id)
        )
    end

    local function remove_pending_entry(id)
        local entry = pending_local[id]
        if entry then
            pending_local[id] = nil
            pending_local_count = math.max(0, pending_local_count - 1)
        end
        if pending_by_id[id] then
            pending_by_id[id] = nil
            global_pending_count = math.max(0, global_pending_count - 1)
        end
        return entry
    end

    local function evict_oldest_set(prefer_local)
        local oldest_id, oldest_at = nil, nil
        for id, entry in pairs(pending_by_id) do
            if entry.op == 'set' and (not prefer_local or entry.owner == owner_token) then
                if oldest_at == nil or entry.sent_at < oldest_at then
                    oldest_id, oldest_at = id, entry.sent_at
                end
            end
        end
        if oldest_id then
            local evicted = pending_by_id[oldest_id]
            if evicted and evicted.on_evict then
                evicted.on_evict('pending_cap_evicted')
            else
                pending_by_id[oldest_id] = nil
                global_pending_count = math.max(0, global_pending_count - 1)
            end
            return true
        end
        return false
    end

    local function settle_stop(meta, confirmed, reason)
        if not meta or meta.settled then return end
        meta.settled = true
        local waiters = meta.waiters
        meta.waiters = nil
        if waiters then
            for _, cb in ipairs(waiters) do
                pcall(cb, confirmed, reason)
            end
        end
    end

    local function prune_pending(now)
        if not finite(now) then return end
        local expired = {}
        for id, entry in pairs(pending_local) do
            if now < entry.sent_at or (now - entry.sent_at) >= receipt_timeout then
                expired[#expired + 1] = id
            end
        end
        for _, id in ipairs(expired) do
            local entry = remove_pending_entry(id)
            if entry then
                stats.timeouts = stats.timeouts + 1
                if entry.op == 'stop' and pending_stops[entry.lease_id] == entry then
                    pending_stops[entry.lease_id] = nil
                    pending_stop_count = math.max(0, pending_stop_count - 1)
                    mark_uncertain('stop_receipt_timeout', entry)
                    settle_stop(entry, false, 'stop_receipt_timeout')
                end
            end
        end
    end

    local function dispatch(op, lease, velocity, anchor_pose, expected_zone, on_settled)
        local now = clock()
        if not finite(now) then return false, 'invalid_clock' end
        prune_pending(now)
        if pending_local_count >= max_pending then
            if op == 'stop' then
                evict_oldest_set(true)
            end
            if pending_local_count >= max_pending then
                stats.refusals = stats.refusals + 1
                return false, 'pending_receipts_full'
            end
        end
        if global_pending_count >= MAX_GLOBAL_PENDING then
            if op == 'stop' then
                evict_oldest_set(false)
            end
            if global_pending_count >= MAX_GLOBAL_PENDING then
                stats.refusals = stats.refusals + 1
                return false, 'global_pending_receipts_full'
            end
        end

        next_request_id = next_request_id + 1
        local id = next_request_id
        local dispatched_vel = copy_vec(velocity)
        local args = {
            op = op,
            slot = slot,
            request = id,
            lease = lease.id,
            generation = lease.generation,
            epoch = lease.epoch,
            index = lease.index,
            own = own_index,
            count = pinned.issued_count,
            actor = lease.actor,
            sector = lease.sector_macro,
            zone = expected_zone,
            code = lease.code,
        }
        if op == 'set' then
            args.vx, args.vy, args.vz = velocity.x, velocity.y, velocity.z
        end

        local meta = {
            id = id,
            op = op,
            owner = owner_token,
            lease_id = lease.id,
            generation = lease.generation,
            index = lease.index,
            actor = lease.actor,
            sector_macro = lease.sector_macro,
            code = lease.code,
            expected_zone = expected_zone,
            dispatched_velocity = dispatched_vel,
            anchor_pose = copy_vec(anchor_pose),
            sent_at = now,
            settled = false,
            waiters = type(on_settled) == 'function' and {on_settled} or {},
        }
        meta.on_evict = function()
            remove_pending_entry(id)
            stats.timeouts = stats.timeouts + 1
        end
        meta.handler = function(text)
            if pending_local[id] then
                pending_local[id] = nil
                pending_local_count = math.max(0, pending_local_count - 1)
            end
            local row = parse_receipt(text, op, meta.anchor_pose)
            row.slot = slot
            row.index = meta.index
            row.code = meta.code
            row.expected_lease = meta.lease_id
            row.generation = meta.generation

            -- Enforce receipt expiry inside the callback itself before accepting any
            -- SET, STOP, ordinary zero SET, or out-of-order reply (even without prior housekeeping).
            local now_cb = clock()
            local expired = (not finite(now_cb)) or (now_cb < meta.sent_at) or ((now_cb - meta.sent_at) >= receipt_timeout)
            if expired then
                local raw_ok = row.ok
                local raw_status = row.status
                local raw_speed = row.total_speed_mps
                local lease_matches_reply = row.lease == meta.lease_id
                stats.timeouts = stats.timeouts + 1
                stats.fenced_receipts = stats.fenced_receipts + 1
                row.expired = true
                row.fenced = true
                row.ok = false
                if op == 'stop' then
                    if pending_stops[meta.lease_id] == meta then
                        pending_stops[meta.lease_id] = nil
                        pending_stop_count = math.max(0, pending_stop_count - 1)
                    end
                    row.confirmed_zero = false
                    row.fail_reason = 'stop_receipt_timeout'
                    mark_uncertain('stop_receipt_timeout', meta)
                    settle_stop(meta, false, 'stop_receipt_timeout')
                else
                    row.fail_reason = 'set_receipt_timeout'
                    if lease_matches_reply and raw_ok and raw_status == 'set' and active == nil
                            and raw_speed and raw_speed > STOP_SPEED_TOLERANCE then
                        mark_uncertain('late_set_after_stop', meta)
                    end
                end
                notify_event(row)
                return
            end

            local lease_matches_reply = row.lease == meta.lease_id
            if not lease_matches_reply then
                row.ok = false
                row.fenced = true
                row.fail_reason = 'lease_mismatch'
                stats.fenced_receipts = stats.fenced_receipts + 1
                if op == 'stop' and pending_stops[meta.lease_id] == meta then
                    pending_stops[meta.lease_id] = nil
                    pending_stop_count = math.max(0, pending_stop_count - 1)
                    mark_uncertain('stop_lease_mismatch', meta)
                    settle_stop(meta, false, 'stop_lease_mismatch')
                end
                notify_event(row)
                return
            end

            if op == 'set' then
                local is_current = active ~= nil and active.id == meta.lease_id
                    and active.generation == meta.generation and active.actor == meta.actor
                    and active.sector_macro == meta.sector_macro
                if not is_current then
                    row.fenced = true
                    stats.fenced_receipts = stats.fenced_receipts + 1
                    -- If MD unexpectedly applied nonzero velocity after this lease was already stopped
                    -- and no newer active lease replaced it, cleanup cannot be proved.
                    if row.ok and row.status == 'set' and active == nil
                            and row.total_speed_mps and row.total_speed_mps > STOP_SPEED_TOLERANCE then
                        mark_uncertain('late_set_after_stop', meta)
                    end
                    notify_event(row)
                    return
                end
                if row.ok then
                    local target_ok = row.actor == meta.actor and row.sector == meta.sector_macro
                        and row.zone == meta.expected_zone
                    local vel_ok = false
                    if target_ok and meta.dispatched_velocity and row.sector_velocity then
                        local tol = math.max(1.5, 0.05 * norm3(meta.dispatched_velocity))
                        vel_ok = dist3(row.sector_velocity, meta.dispatched_velocity) <= tol
                    end
                    local order_ok = meta.id >= (active.last_acked_request or 0)
                    if target_ok and vel_ok and order_ok then
                        stats.receipts = stats.receipts + 1
                        active.last_acked_request = meta.id
                        active.acked_velocity = copy_vec(row.sector_velocity)
                        active.acked_zone = row.zone
                        active.acked_game_time = row.game_time
                        row.matched = true
                        -- Clear possible prior physical motion ONLY when a matching, independently
                        -- confirmed near-zero receipt arrives strictly after the last nonzero set.
                        if row.total_speed_mps ~= nil and row.total_speed_mps <= STOP_SPEED_TOLERANCE
                                and meta.id > (active.last_nonzero_request or 0) then
                            active.possible_motion = false
                        end
                    elseif not order_ok then
                        row.fenced = true
                        stats.fenced_receipts = stats.fenced_receipts + 1
                    else
                        row.ok = false
                        row.fail_reason = not target_ok and 'set_target_or_zone_mismatch' or 'set_velocity_mismatch'
                        stats.refusals = stats.refusals + 1
                    end
                else
                    stats.refusals = stats.refusals + 1
                end
                notify_event(row)
                return
            end

            -- op == 'stop'
            local was_pending = pending_stops[meta.lease_id] == meta
            if was_pending then
                pending_stops[meta.lease_id] = nil
                pending_stop_count = math.max(0, pending_stop_count - 1)
            end
            if active ~= nil and active.id ~= meta.lease_id then
                row.fenced = true
                stats.fenced_receipts = stats.fenced_receipts + 1
            end

            local fail_reason = nil
            if not row.ok then
                fail_reason = row.fail_reason or 'stop_refused'
            elseif row.actor ~= meta.actor then
                fail_reason = 'stop_target_mismatch'
            elseif row.sector ~= meta.sector_macro then
                fail_reason = 'stop_sector_mismatch'
            elseif row.zone ~= meta.expected_zone then
                fail_reason = 'stop_zone_mismatch'
            elseif not row.observed_near_zero then
                fail_reason = 'stop_speed_nonzero'
            elseif row.anchor_drift_m == nil or row.anchor_drift_m > STOP_ANCHOR_DRIFT_LIMIT then
                fail_reason = 'stop_anchor_drift'
            end

            if fail_reason == nil then
                row.ok = true
                row.confirmed_zero = true
                stats.receipts = stats.receipts + 1
                stats.confirmed_stops = stats.confirmed_stops + 1
                stats.last_snapback_m = row.snapback_m
                stats.last_stop_speed_mps = row.total_speed_mps
                stats.last_anchor_drift_m = row.anchor_drift_m
                settle_stop(meta, true, nil)
            else
                row.ok = false
                row.confirmed_zero = false
                row.fail_reason = fail_reason
                stats.refusals = stats.refusals + 1
                mark_uncertain(fail_reason, meta)
                settle_stop(meta, false, fail_reason)
            end
            notify_event(row)
        end

        pending_by_id[id] = meta
        pending_local[id] = meta
        pending_local_count = pending_local_count + 1
        global_pending_count = global_pending_count + 1
        if op == 'stop' then
            pending_stops[lease.id] = meta
            pending_stop_count = pending_stop_count + 1
        end

        local ok, dispatched = pcall(issue, args)
        if not ok or dispatched ~= true then
            remove_pending_entry(id)
            if op == 'stop' and pending_stops[lease.id] == meta then
                pending_stops[lease.id] = nil
                pending_stop_count = math.max(0, pending_stop_count - 1)
            end
            stats.refusals = stats.refusals + 1
            return false, 'issue_failed'
        end
        if op == 'set' then
            stats.sets = stats.sets + 1
        else
            stats.stops = stats.stops + 1
        end
        return true, id
    end

    local api = {}

    function api:tick()
        prune_pending(clock())
        return not cleanup_uncertain, uncertain_reason
    end

    function api:cleanup_uncertain()
        prune_pending(clock())
        return cleanup_uncertain, uncertain_reason
    end

    function api:has_pending_stop()
        prune_pending(clock())
        return pending_stop_count > 0, pending_stop_count
    end

    function api:on_pending_stops_settled(cb)
        if type(cb) ~= 'function' then return end
        prune_pending(clock())
        if pending_stop_count == 0 then
            pcall(cb, not cleanup_uncertain, uncertain_reason)
            return
        end
        for _, entry in pairs(pending_stops) do
            entry.waiters[#entry.waiters + 1] = cb
        end
    end

    function api:acknowledged_velocity(binding, expected_zone)
        prune_pending(clock())
        if cleanup_uncertain or not active or not active.acked_velocity
                or not finite(active.acked_game_time) then
            return nil
        end
        if binding and not same_binding(active, binding) then return nil end
        if expected_zone ~= nil and active.acked_zone ~= expected_zone then return nil end
        return {
            x = active.acked_velocity.x,
            y = active.acked_velocity.y,
            z = active.acked_velocity.z,
            game_time = active.acked_game_time,
            request = active.last_acked_request,
        }
    end

    function api:apply(binding, velocity, anchor_fn, expected_zone)
        local now = clock()
        prune_pending(now)
        if cleanup_uncertain then return false, uncertain_reason end
        if pending_stop_count > 0 then return false, 'stop_pending' end
        if type(binding) ~= 'table' or type(velocity) ~= 'table'
                or binding.epoch ~= pinned.epoch or binding.index == own_index
                or type(binding.actor) ~= 'string' or type(binding.sector_macro) ~= 'string'
                or type(binding.code) ~= 'string' or not hex32(binding.generation)
                or type(expected_zone) ~= 'string' or expected_zone == ''
                or not (finite(velocity.x) and finite(velocity.y) and finite(velocity.z))
                or not finite(now) then
            return false, 'invalid_apply_args'
        end

        if active and not same_binding(active, binding) then
            -- Never silently issue an unanchored stop for a different prior binding.
            local prior = active
            active = nil
            mark_uncertain('unclosed_previous_binding', prior)
            return false, 'unclosed_previous_binding'
        end

        local speed = norm3(velocity)
        if speed > MAX_SPEED then
            stats.unsupported_speed_skips = stats.unsupported_speed_skips + 1
            if not lease_needs_stop(active) then
                -- Auxiliary velocity path is already cleanly stopped: preserve continuous position track.
                return true, 'speed_unsupported_stopped'
            end
            local ok_s, stop_reason = self:stop(anchor_fn, 'speed_unsupported')
            if ok_s == true then
                return true, 'speed_unsupported_stopping'
            end
            return false, stop_reason or 'cleanup_uncertain'
        end

        local is_zero = velocity.x == 0 and velocity.y == 0 and velocity.z == 0
        if not active then
            if is_zero then return true, 'idle_zero' end
            next_lease_id = next_lease_id + 1
            active = {
                id = next_lease_id,
                epoch = binding.epoch,
                index = binding.index,
                actor = binding.actor,
                sector_macro = binding.sector_macro,
                code = binding.code,
                generation = binding.generation,
                last_sent = nil,
                last_sent_at = nil,
                acked_velocity = nil,
                acked_zone = nil,
                acked_game_time = nil,
                last_acked_request = 0,
                last_nonzero_request = 0,
                possible_motion = false,
            }
        end

        local was_nonzero = active.last_sent ~= nil
            and (active.last_sent.x ~= 0 or active.last_sent.y ~= 0 or active.last_sent.z ~= 0)
        if not lease_needs_stop(active) and is_zero then
            return true, 'idle_zero'
        end
        local retry_zero = is_zero and active.possible_motion and not has_pending_zero_set(active.id)
        if active.last_sent and not (is_zero and was_nonzero) then
            if not retry_zero and dist3(velocity, active.last_sent) <= CHANGE_EPS then
                return true, 'throttled_eps'
            end
            if active.last_sent_at and now - active.last_sent_at < MIN_INTERVAL then
                return true, 'throttled_interval'
            end
        end

        local ok_d, disp_res = dispatch('set', active, velocity, nil, expected_zone)
        if not ok_d then
            if not lease_needs_stop(active) then
                -- Cleanly stopped auxiliary path could not enqueue; keep continuous position track alive.
                return true, 'skipped_' .. tostring(disp_res)
            end
            if disp_res == 'pending_receipts_full' or disp_res == 'global_pending_receipts_full' then
                -- Already moving; wait for pending receipts to drain or time out rather than closing position track.
                return true, 'throttled_pending_full'
            end
            local ok_s, stop_reason = self:stop(anchor_fn, disp_res)
            if ok_s == true then
                return true, 'stopped_after_dispatch_failure'
            end
            return false, stop_reason or disp_res
        end
        active.last_sent = copy_vec(velocity)
        active.last_sent_at = now
        -- Preserve possible prior physical motion across zero dispatches until MD confirms near-zero.
        if not is_zero then
            active.possible_motion = true
            active.last_nonzero_request = disp_res
        end
        return true, 'dispatched'
    end

    function api:stop(anchor_fn, reason, on_settled)
        prune_pending(clock())
        local bound = active
        local needs_stop = lease_needs_stop(bound)
        active = nil
        if not needs_stop or not bound then
            return true, 'already_stopped'
        end
        if type(anchor_fn) ~= 'function' then
            mark_uncertain('anchor_missing', bound)
            return false, 'anchor_missing'
        end
        local ok_a, pose, anchor_err = pcall(anchor_fn)
        local valid_pose = ok_a and type(pose) == 'table'
            and finite(pose.x) and finite(pose.y) and finite(pose.z)
            and type(pose.zone) == 'string' and pose.zone ~= ''
            and (pose.actor == nil or pose.actor == bound.actor)
            and (pose.sector_macro == nil or pose.sector_macro == bound.sector_macro)
        if not valid_pose then
            local fail = type(anchor_err) == 'string' and anchor_err
                or (not ok_a and 'anchor_exception') or 'anchor_failed'
            mark_uncertain(fail, bound)
            return false, fail
        end
        local anchored_pose = {x = pose.x, y = pose.y, z = pose.z}
        local ok_d, disp_err = dispatch('stop', bound, nil, anchored_pose, pose.zone, on_settled)
        if not ok_d then
            mark_uncertain(disp_err or 'stop_dispatch_failed', bound)
            return false, disp_err or 'stop_dispatch_failed'
        end
        return true, reason and ('stop_dispatched:' .. tostring(reason)) or 'stop_dispatched'
    end

    function api:stats()
        prune_pending(clock())
        local out = {}
        for k, v in pairs(stats) do out[k] = v end
        out.may_be_moving = active ~= nil and active.possible_motion or false
        out.possible_motion = active ~= nil and active.possible_motion or false
        out.pending_count = pending_local_count
        out.pending_stops = pending_stop_count
        out.cleanup_uncertain = cleanup_uncertain
        out.uncertain_reason = uncertain_reason
        out.acked_velocity = active and copy_vec(active.acked_velocity) or nil
        out.acked_game_time = active and active.acked_game_time or nil
        return out
    end

    return api
end

function M.new_x4(options)
    local cfg = {}
    for k, v in pairs(options or {}) do cfg[k] = v end
    cfg.enabled = true
    if not cfg.on_event then
        local set_count = 0
        cfg.on_event = function(row)
            if row.op == 'uncertain' then
                DebugError(('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_UNCERTAIN slot=%s reason=%s index=%s ship=%s lease=%s'):format(
                    tostring(row.slot), tostring(row.reason), tostring(row.index), tostring(row.code), tostring(row.lease)))
                return
            end
            if row.fenced then
                DebugError(('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_FENCED slot=%s op=%s status=%s lease=%s expected=%s index=%s'):format(
                    tostring(row.slot), tostring(row.op), tostring(row.status),
                    tostring(row.lease), tostring(row.expected_lease), tostring(row.index)))
                return
            end
            if not row.ok then
                DebugError(('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_REFUSED slot=%s op=%s status=%s reason=%s md_why=%s index=%s'):format(
                    tostring(row.slot), tostring(row.op), tostring(row.status),
                    tostring(row.fail_reason), tostring(row.md_why or 'none'), tostring(row.index)))
                return
            end
            if row.op == 'stop' then
                DebugError(('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_STOP slot=%s index=%s ship=%s lease=%s zone=%s snapback=%.6f speed=%.6f anchor_drift=%s pos=%.6f,%.6f,%.6f'):format(
                    tostring(row.slot), tostring(row.index), tostring(row.code), tostring(row.lease), tostring(row.zone),
                    row.snapback_m or -1, row.total_speed_mps or -1,
                    row.anchor_drift_m and ('%.6f'):format(row.anchor_drift_m) or 'none',
                    row.position.x, row.position.y, row.position.z))
                return
            end
            set_count = set_count + 1
            if set_count <= 3 or set_count % 10 == 0 then
                DebugError(('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_SET slot=%s index=%s ship=%s lease=%s n=%d fwd=%.3f tot=%.3f cmd=%.3f,%.3f,%.3f'):format(
                    tostring(row.slot), tostring(row.index), tostring(row.code), tostring(row.lease), set_count,
                    row.forward_speed_mps or 0, row.total_speed_mps or 0,
                    row.dispatched_velocity and row.dispatched_velocity.x or 0,
                    row.dispatched_velocity and row.dispatched_velocity.y or 0,
                    row.dispatched_velocity and row.dispatched_velocity.z or 0))
            end
        end
    end
    return M.new(cfg)
end

X4LAN_COMPANY_COCKPIT_VELOCITY = M
return M
