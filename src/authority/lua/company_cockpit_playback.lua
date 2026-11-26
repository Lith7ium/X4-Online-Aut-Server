-- Opt-in receiving-cockpit Company ship playback with independent per-ship 5 s loss limit, strict generation checks, and reacquisition (up to 3 pilots).
-- Reuses X4LAN_COMPANY_HULL_PLAYBACK (X4LAN_MOTION_BUFFER + X4LAN_MOTION_PLAYBACK) and X4LAN_COMPANY_HULL_PLAYBACK_X4.
local M = {}
local MAX_STEP = 50
local FIRST_CONTACT_DISTANCE, FIRST_CONTACT_AGE = 250, .25
local READBACK_TOLERANCE = 3
local COCKPIT_SLOTS = {'cockpit', 'cockpit2', 'cockpit3'}

local function dist3(a, b)
    local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local function hex32(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function watermark(sample, arrival)
    return {sequence = sample.sequence, capture_time = sample.capture_time, source_time = sample.source_time,
            arrival = arrival}
end

local function alignment(sample, written_at, first_contact)
    return {target = {x = sample.x, y = sample.y, z = sample.z}, sequence = sample.sequence,
            source_time = sample.source_time, capture_time = sample.capture_time, generation = sample.generation,
            written_at = written_at, first_contact = first_contact}
end

local function first_contact_matches(previous, sample, arrival, now)
    local elapsed = sample.capture_time - previous.capture_time
    local velocity = sample.real_velocity
    if elapsed <= 0 or elapsed > FIRST_CONTACT_AGE or now - previous.arrival > FIRST_CONTACT_AGE
            or arrival < previous.arrival or type(velocity) ~= 'table' then return false end
    local squared_error = 0
    for _, axis in ipairs({'x', 'y', 'z'}) do
        local rate = velocity[axis]
        if type(rate) ~= 'number' or not (math.abs(rate) <= 5000) then return false end
        local residual = sample[axis] - previous.position[axis] - rate * elapsed
        squared_error = squared_error + residual * residual
    end
    return squared_error <= MAX_STEP ^ 2
end

function M.new(options)
    assert(type(options) == 'table' and options.enabled == true, 'explicit cockpit playback opt-in required')
    local own_index = options.own_index
    assert(type(own_index) == 'number' and own_index % 1 == 0 and own_index >= 1 and own_index <= 200000,
        'invalid own_index')
    local target_index = options.target_index
    if target_index ~= nil then
        assert(type(target_index) == 'number' and target_index % 1 == 0 and target_index >= 1
            and target_index <= 200000 and target_index ~= own_index, 'invalid cockpit target_index')
    end
    local slot_name = options.slot or 'cockpit'
    assert(slot_name == 'cockpit' or slot_name == 'cockpit2' or slot_name == 'cockpit3', 'invalid cockpit slot')
    local factory = options.playback or X4LAN_COMPANY_HULL_PLAYBACK
    local prediction_seconds = assert(factory.MAX_PREDICTION_SECONDS)
    local allow_reacquire_align = options.allow_reacquire_align == true

    -- Shared per-ship ledgers preserve revocation, reacquisition eligibility, and monotone watermarks across slot re-entry.
    local revoked_by_index = options.revoked_by_index or {}
    local reacquire_by_index = options.reacquire_by_index or {}
    local last_by_index_gen = options.last_by_index_gen or {}

    local binding, session, controller
    local active_index = nil
    local active_generation = nil
    local pending_align, first_contact
    local last_tick_now = nil
    local closed = false

    local stats = {
        slot = slot_name,
        binds = 0,
        writes = 0,
        readbacks = 0,
        stale_cutoffs = 0,
        withdrawals = 0,
        reacquisitions = 0,
        alignments = 0,
        generation_rejections = 0,
        refusals_over_50m = 0,
    }

    local function watermark_key(index, generation)
        return tostring(index) .. ':' .. tostring(generation)
    end

    local function is_revoked(index, generation)
        local bucket = revoked_by_index[index]
        return bucket ~= nil and bucket[generation] == true
    end

    local function mark_revoked(index, generation)
        if not hex32(generation) then return end
        local bucket = revoked_by_index[index]
        if not bucket then
            bucket = {}
            revoked_by_index[index] = bucket
        end
        bucket[generation] = true
    end

    local function emit(kind, detail)
        if type(options.on_event) == 'function' then
            local payload = detail or {}
            payload.slot = payload.slot or slot_name
            pcall(options.on_event, kind, payload)
        end
    end

    local function paused()
        return options.paused and options.paused() ~= false
    end

    local function stop_track()
        if pending_align and pending_align.first_contact then
            -- Its native setter may have succeeded; a second alignment is unsafe.
            closed = true
        end
        if controller then controller:stop(); controller = nil end
        if session then session:close(); session = nil end
        pending_align, first_contact = nil, nil
    end

    local function same_hull(observed, current)
        return observed and current and observed.epoch == current.epoch
            and observed.index == current.index and observed.actor == current.actor
            and observed.code == current.code and observed.sector_macro == current.sector_macro
    end

    local function same_view(observed, serial)
        return binding and serial == binding.serial and same_hull(observed, binding)
    end

    local function settle_alignment(now)
        if not pending_align then return true end
        if now <= pending_align.written_at then return true end
        if paused() or not session or session:guard() ~= true then
            stop_track()
            return false
        end
        local initial = pending_align.first_contact
        local actual = session:read()
        if not actual or (initial and not session:first_contact_in_range(actual, pending_align.target)) then
            stop_track()
            options.fault('alignment_read_failed')
            return false
        end
        local err = dist3(actual, pending_align.target)
        local elapsed = now - pending_align.written_at
        local rec = {
            kind = 'alignment_readback',
            slot = slot_name,
            serial = binding.serial,
            epoch = binding.epoch,
            index = binding.index,
            generation = pending_align.generation,
            sector_macro = binding.sector_macro,
            code = binding.code,
            source_sequence = pending_align.sequence,
            source_time = pending_align.source_time,
            capture_time = pending_align.capture_time,
            local_time = now,
            game_time = options.game(),
            actual = actual,
            target = pending_align.target,
            phase = initial and 'first_contact_aligned' or 'reacquire_aligned',
            error = err,
            elapsed = elapsed,
        }
        stats.readbacks = stats.readbacks + 1
        local recorded = options.record(rec)
        if err > READBACK_TOLERANCE or (initial and recorded ~= true) then
            stop_track()
            options.fault('alignment_readback_out_of_bounds')
            return false
        end
        pending_align = nil
        if initial then
            emit('first_contact_confirmed', {index = rec.index, error = err, sequence = rec.source_sequence})
            return true
        end
        -- Close the alignment session so the next velocity sample opens a fresh controller on this aligned pose.
        stop_track()
        emit('reacquire_aligned_confirmed', {
            index = rec.index, error = err, elapsed = elapsed, sequence = rec.source_sequence,
        })
        return true
    end

    local function retire(reason)
        local idx = binding and binding.index or active_index
        local uncertain = controller and controller:outcome_uncertain() == true
        stop_track()
        if uncertain then
            closed = true
            emit('closed_uncertain', {index = idx, reason = reason})
            return
        end
        if reason == 'stale' or reason == 'arrival_gap' then
            stats.stale_cutoffs = stats.stale_cutoffs + 1
            if idx then reacquire_by_index[idx] = true end
            emit('stale_5s', {index = idx, reason = reason})
        elseif reason == 'correction_out_of_bounds' then
            stats.refusals_over_50m = stats.refusals_over_50m + 1
            emit('refused_over_50m', {index = idx, reason = reason})
        else
            if idx then reacquire_by_index[idx] = true end
            emit('track_stopped', {index = idx, reason = reason})
        end
    end

    local function accept_binding(verified)
        binding = verified
        active_index = verified.index
        active_generation = verified.generation
    end

    local function new_controller(verified, active_session, start_at_first_pair)
        stats.binds = stats.binds + 1
        return assert(factory.new({
            enabled = true, own_index = own_index, binding = verified, velocity_steps = true,
            start_at_first_pair = start_at_first_pair,
            clock = options.clock, game = options.game, paused = options.paused,
            guard = function() return active_session:guard() end,
            read = function() return active_session:read() end,
            write = function(_, target, before, recheck)
                local ok = active_session:write(target, before, recheck)
                if ok == true then stats.writes = stats.writes + 1 end
                return ok
            end,
            record = function(ev)
                if options.record(ev) ~= true then return false end
                if ev.kind == 'readback' then
                    if active_session.confirm_motion and not active_session:confirm_motion() then return false end
                    stats.readbacks = stats.readbacks + 1
                end
                return true
            end,
            fault = function(reason, uncertain, stage)
                if reason == 'correction_out_of_bounds' then
                    stats.refusals_over_50m = stats.refusals_over_50m + 1
                end
                options.fault(reason, uncertain, stage)
            end,
        }))
    end

    local function start_first_contact(verified, native_session, previous, sample, arrival)
        local candidate = new_controller(verified, native_session, true)
        local ok, reason = candidate:push(verified, sample, arrival)
        if not ok then
            candidate:stop()
            native_session:close()
            return false, reason
        end
        local function still_fresh(native_position)
            return options.clock() - previous.arrival <= FIRST_CONTACT_AGE
                and dist3(native_position, previous.position) <= MAX_STEP
        end
        local aligned, failure = native_session:align(sample, still_fresh)
        if not aligned then
            if failure == 'unconfirmed' then closed = true end
            candidate:stop()
            native_session:close()
            return false, 'first_contact_refused'
        end
        accept_binding(verified)
        controller, session = candidate, native_session
        stats.alignments = stats.alignments + 1
        pending_align = alignment(sample, options.clock(), true)
        last_by_index_gen[watermark_key(verified.index, verified.generation)] = watermark(sample, arrival)
        return true, 'aligned'
    end

    local ctrl = {}

    function ctrl:close()
        closed = true
        stop_track()
    end

    function ctrl:stats()
        local out = {}
        for k, v in pairs(stats) do out[k] = v end
        out.active = controller ~= nil
        out.closed = closed
        out.active_index = active_index
        out.active_generation = active_generation
        out.reacquire_align_allowed = active_index ~= nil and (reacquire_by_index[active_index] == true) or false
        return out
    end

    function ctrl:view(observed, serial)
        if closed then return end
        if first_contact and (first_contact.serial ~= serial or not same_hull(observed, first_contact)) then
            first_contact = nil
        end
        if not binding then return end
        if observed == nil or not same_view(observed, serial) then
            local prev_index = binding.index
            if controller or pending_align then
                stop_track()
            end
            if observed == nil or not same_hull(observed, binding) then
                stats.withdrawals = stats.withdrawals + 1
                reacquire_by_index[prev_index] = true
                if active_index == prev_index then
                    active_index = nil
                    active_generation = nil
                end
                emit('withdrawn', {index = prev_index})
            end
            binding = nil
        end
    end

    function ctrl:end_track(observed, serial, generation)
        if closed or not observed then return end
        if target_index ~= nil and observed.index ~= target_index then return end
        mark_revoked(observed.index, generation)
        if first_contact and first_contact.index == observed.index and first_contact.generation == generation then
            first_contact = nil
        end
        local matches_active = (active_index == observed.index and active_generation == generation)
        local matches_binding = (binding and same_view(observed, serial) and binding.generation == generation)
        if matches_active or matches_binding then
            stop_track()
            if matches_active then
                active_generation = nil
            end
            binding = nil
            reacquire_by_index[observed.index] = true
            emit('generation_revoked', {index = observed.index, generation = generation})
        end
    end

    function ctrl:probe_step_guard(offset_m)
        if closed or paused() then return false, 'unavailable' end
        if not session then return false, 'not_bound' end
        local actual = session:read()
        if not actual then return false, 'read_failed' end
        local dest = {x = actual.x + (offset_m or 120), y = actual.y, z = actual.z}
        local step = dist3(dest, actual)
        if step > MAX_STEP then
            stats.refusals_over_50m = stats.refusals_over_50m + 1
            emit('refused_over_50m', {index = binding and binding.index, dist = step, max_step = MAX_STEP})
            return false, 'correction_out_of_bounds', step
        end
        return true, 'within_bounds', step
    end

    function ctrl:tick(update_started_at)
        if closed then return end
        if paused() then
            first_contact = nil
            if controller or pending_align then
                local idx = binding and binding.index or active_index
                stop_track()
                if idx then reacquire_by_index[idx] = true end
                emit('paused_stop', {index = idx})
            end
            return
        end
        local now = options.clock()
        if last_tick_now ~= nil and now <= last_tick_now then
            return
        end
        last_tick_now = now
        if pending_align then
            -- An alignment written while draining UDP must settle on a later update.
            if (update_started_at or now) > pending_align.written_at then settle_alignment(now) end
            return
        end
        if controller then
            local ok, reason = controller:tick()
            if not ok then retire(reason or controller:state()) end
        end
    end

    function ctrl:sample(observed, serial, sample, arrival)
        if closed or not observed or observed.index == own_index then return false, 'ineligible' end
        if target_index ~= nil and observed.index ~= target_index then return false, 'other_target' end
        if paused() then
            first_contact = nil
            if controller or pending_align then
                local idx = binding and binding.index or active_index
                stop_track()
                if idx then reacquire_by_index[idx] = true end
            end
            return false, 'paused'
        end
        local now = options.clock()
        if type(arrival) ~= 'number' or not (arrival >= 0 and arrival <= now)
                or now - arrival >= prediction_seconds then
            return false, 'expired_arrival'
        end
        if not hex32(sample.generation) then
            stats.generation_rejections = stats.generation_rejections + 1
            return false, 'invalid_generation'
        end
        local ship_index = observed.index
        if is_revoked(ship_index, sample.generation) then
            stats.generation_rejections = stats.generation_rejections + 1
            emit('rejected_revoked_generation', {
                index = ship_index, generation = sample.generation, sequence = sample.sequence,
            })
            return false, 'revoked_generation'
        end

        -- Switch ship cleanly if this slot previously held a different ship that was withdrawn.
        if active_index ~= nil and active_index ~= ship_index then
            if controller or pending_align then
                stop_track()
                reacquire_by_index[active_index] = true
            end
            binding = nil
            active_index = nil
            active_generation = nil
        end

        -- Strict generation check: never mix poses across two active grants without prior END/retirement.
        local replacing_generation = active_index == ship_index and active_generation ~= nil
            and sample.generation ~= active_generation
        if replacing_generation and (controller or pending_align) then
            stats.generation_rejections = stats.generation_rejections + 1
            emit('generation_mismatch', {
                index = ship_index, expected = active_generation, actual = sample.generation,
            })
            return false, 'generation_mismatch'
        end

        if pending_align and not pending_align.first_contact then return true, 'alignment_pending' end
        if pending_align and not same_view(observed, serial) then return false, 'binding_changed' end

        local wkey = watermark_key(ship_index, sample.generation)
        local last = last_by_index_gen[wkey]
        if last then
            if not (sample.sequence > last.sequence and sample.capture_time > last.capture_time
                    and sample.source_time >= last.source_time and arrival >= last.arrival) then
                return false, 'non_monotone'
            end
            local deadline = last.arrival + prediction_seconds
            if now >= deadline then
                if arrival < deadline then return false, 'pre_deadline_sample' end
                if controller then self:tick() end
                if closed or controller then return false, 'waiting_cutoff_settle' end
            end
        end

        -- If currently unbound (initial start OR reacquisition after loss/withdrawal/revocation):
        if not controller then
            local probe_binding = {
                slot = slot_name, serial = serial, epoch = observed.epoch, index = ship_index,
                actor = observed.actor, code = observed.code, sector_macro = observed.sector_macro,
                generation = sample.generation,
            }
            local temp_session = options.adapter:bind(probe_binding)
            if not temp_session then
                options.fault('native_binding')
                return false, 'native_binding'
            end
            local actual = temp_session:read()
            if not actual then
                temp_session:close()
                return false, 'read_failed'
            end
            local gap = dist3({x = sample.x, y = sample.y, z = sample.z}, actual)
            local align_allowed = reacquire_by_index[ship_index] == true or replacing_generation
            local previous = first_contact
            first_contact = nil
            if gap > MAX_STEP then
                local initial = previous and not align_allowed and gap <= FIRST_CONTACT_DISTANCE
                    and previous.serial == serial and same_hull(previous, probe_binding)
                    and previous.generation == sample.generation
                    and first_contact_matches(previous, sample, arrival, now)
                if initial then
                    return start_first_contact(probe_binding, temp_session, previous, sample, arrival)
                end
                if not align_allowed or not allow_reacquire_align or type(temp_session.align) ~= 'function' then
                    temp_session:close()
                    stats.refusals_over_50m = stats.refusals_over_50m + 1
                    emit('refused_over_50m', {
                        index = ship_index, dist = gap, max_step = MAX_STEP, stage = 'reacquire',
                    })
                    return false, 'correction_out_of_bounds'
                end
                -- Strictly ONE authorized post-loss/post-withdrawal alignment per episode for this ship.
                reacquire_by_index[ship_index] = false
                local ok_align = temp_session:align({x = sample.x, y = sample.y, z = sample.z})
                if ok_align ~= true then
                    temp_session:close()
                    binding, active_index, active_generation = nil, nil, nil
                    options.fault('alignment_failed')
                    return false, 'alignment_failed'
                end
                accept_binding(probe_binding)
                session = temp_session
                stats.alignments = stats.alignments + 1
                stats.reacquisitions = stats.reacquisitions + 1
                pending_align = alignment(sample, now, false)
                last_by_index_gen[wkey] = watermark(sample, arrival)
                emit('reacquire_aligned', {
                    index = ship_index, dist = gap, sequence = sample.sequence,
                    mode = observed.mode or slot_name,
                })
                return true, 'aligned'
            end
            if not sample.real_velocity then
                -- A fresh velocity-less baseline can identify the first contact.
                -- It cannot grant the normal post-loss reacquisition permission.
                if options.trial_first_contact == true and not align_allowed and not last and not active_index then
                    if sample.version == 3 and sample.epoch == observed.epoch and sample.index == ship_index
                            and sample.sector_macro == observed.sector_macro and temp_session.first_contact_in_range
                            and temp_session:first_contact_in_range(actual, sample) then
                        first_contact = probe_binding
                        first_contact.position = {x = sample.x, y = sample.y, z = sample.z}
                        first_contact.arrival, first_contact.capture_time = arrival, sample.capture_time
                    end
                else
                    accept_binding(probe_binding)
                end
                temp_session:close()
                last_by_index_gen[wkey] = watermark(sample, arrival)
                return true, 'baseline_recorded'
            end
            local candidate = new_controller(probe_binding, temp_session)
            local ok, reason = candidate:push(probe_binding, sample, arrival)
            if not ok then
                candidate:stop()
                temp_session:close()
                return false, reason
            end
            accept_binding(probe_binding)
            controller, session = candidate, temp_session
            if align_allowed then
                stats.reacquisitions = stats.reacquisitions + 1
                emit('reacquired_direct', {
                    index = ship_index, dist = gap, sequence = sample.sequence,
                    mode = observed.mode or slot_name,
                })
            end
            reacquire_by_index[ship_index] = false
        else
            local ok, reason = controller:push(binding, sample, arrival)
            if not ok then
                retire(reason or controller:state())
                return false, reason
            end
            if not sample.real_velocity then return true, 'waiting_for_velocity' end
        end
        last_by_index_gen[wkey] = watermark(sample, arrival)
        return true, 'accepted'
    end

    return ctrl
end

function M.new_multi(options)
    assert(type(options) == 'table' and options.enabled == true, 'explicit multi-pilot cockpit playback opt-in required')
    local slot_names = options.slot_names or COCKPIT_SLOTS
    assert(type(slot_names) == 'table' and #slot_names >= 1 and #slot_names <= #COCKPIT_SLOTS, 'invalid cockpit slots')
    assert(type(options.adapters) == 'table', 'per-slot native adapters required')

    local revoked_by_index = {}
    local reacquire_by_index = {}
    local last_by_index_gen = {}
    local controllers = {}
    for _, name in ipairs(slot_names) do
        local adapter = assert(options.adapters[name], 'missing adapter for slot ' .. tostring(name))
        controllers[name] = M.new({
            enabled = true,
            slot = name,
            own_index = options.own_index,
            allow_reacquire_align = options.allow_reacquire_align,
            playback = options.playback,
            trial_first_contact = options.trial_first_contact,
            adapter = adapter,
            clock = options.clock,
            game = options.game,
            paused = options.paused,
            record = options.record,
            fault = function(reason, uncertain, stage)
                if options.fault then options.fault(reason, uncertain, stage, name) end
            end,
            on_event = options.on_event,
            revoked_by_index = revoked_by_index,
            reacquire_by_index = reacquire_by_index,
            last_by_index_gen = last_by_index_gen,
        })
    end

    local api = {slot_names = slot_names}

    local function resolve_slot(slot_name)
        return controllers[slot_name or 'cockpit']
    end

    function api:view(observed, serial, slot_name)
        local c = resolve_slot(slot_name)
        if c then c:view(observed, serial) end
    end

    function api:sample(observed, serial, sample, arrival, slot_name)
        local c = resolve_slot(slot_name)
        if not c then return false, 'unknown_slot' end
        return c:sample(observed, serial, sample, arrival)
    end

    function api:end_track(observed, serial, generation, slot_name)
        local c = resolve_slot(slot_name)
        if c then c:end_track(observed, serial, generation) end
    end

    function api:tick(update_started_at)
        for _, name in ipairs(slot_names) do
            controllers[name]:tick(update_started_at)
        end
    end

    function api:probe_step_guard(offset_m, slot_or_index)
        if type(slot_or_index) == 'number' then
            for _, name in ipairs(slot_names) do
                local st = controllers[name]:stats()
                if st.active_index == slot_or_index and st.active then
                    return controllers[name]:probe_step_guard(offset_m)
                end
            end
            return false, 'not_bound'
        end
        local c = resolve_slot(slot_or_index)
        if not c then return false, 'unknown_slot' end
        return c:probe_step_guard(offset_m)
    end

    function api:slot_stats(slot_name)
        local c = resolve_slot(slot_name)
        return c and c:stats() or nil
    end

    function api:stats()
        local total = {
            binds = 0, writes = 0, readbacks = 0, stale_cutoffs = 0, withdrawals = 0,
            reacquisitions = 0, alignments = 0, generation_rejections = 0, refusals_over_50m = 0,
            active_slots = 0, slots = {},
        }
        for _, name in ipairs(slot_names) do
            local st = controllers[name]:stats()
            total.slots[name] = st
            for _, field in ipairs({'binds', 'writes', 'readbacks', 'stale_cutoffs', 'withdrawals',
                                    'reacquisitions', 'alignments', 'generation_rejections', 'refusals_over_50m'}) do
                total[field] = total[field] + (st[field] or 0)
            end
            if st.active then total.active_slots = total.active_slots + 1 end
        end
        return total
    end

    function api:close()
        for _, name in ipairs(slot_names) do
            controllers[name]:close()
        end
    end

    return api
end

local function make_x4_record(readers_by_slot, clock)
    local tracks = {}
    local function decimal(value) return ('%.17g'):format(value) end
    local function coordinates(pose)
        return table.concat({decimal(pose.x), decimal(pose.y), decimal(pose.z)}, ',')
    end
    return function(event)
        local slot_name = event.slot
        local track = tracks[slot_name]
        if not track or track.serial ~= event.serial or track.epoch ~= event.epoch
                or track.sector ~= event.sector_macro or track.index ~= event.index
                or track.generation ~= event.generation then
            track = {
                serial = event.serial, epoch = event.epoch, sector = event.sector_macro,
                index = event.index, generation = event.generation,
                origin = event.actual, count = 0, last_logged_at = 0,
            }
            tracks[slot_name] = track
        end
        -- The first observation is pre-write. A later alignment sets a new
        -- origin at its readback so the correction itself adds no displacement.
        local alignment = event.kind == 'alignment_readback'
        if alignment then track.origin = event.actual end
        if not alignment and event.kind ~= 'readback' then return true end

        local now = clock()
        if alignment or track.count < 3 or now - track.last_logged_at >= 0.5 then
            track.last_logged_at = now
            track.count = track.count + 1
            local reader = readers_by_slot[slot_name]
            local mode = reader and reader.mode and reader:mode() or slot_name
            DebugError(table.concat({
                '[X4LAN] AUTH_COMPANY_COCKPIT_READBACK', 'slot=' .. slot_name,
                'kind=' .. event.kind, 'mode=' .. tostring(mode),
                'ship=' .. event.code, 'epoch=' .. event.epoch, 'index=' .. event.index,
                'sector=' .. event.sector_macro, 'generation=' .. event.generation,
                'serial=' .. ('%.0f'):format(event.serial),
                'sequence=' .. event.source_sequence, 'source_time=' .. decimal(event.source_time),
                'capture_time=' .. decimal(event.capture_time), 'elapsed=' .. decimal(event.elapsed),
                'target=' .. coordinates(event.target), 'actual=' .. coordinates(event.actual),
                'error=' .. decimal(event.error), 'displacement=' .. decimal(dist3(event.actual, track.origin)),
            }, ' '))
        end
        return true
    end
end

function M.new_x4(config, pinned, cockpit_reader, guard, clock)
    local ffi = require('ffi')
    ffi.cdef[[double GetCurrentGameTime(void); bool IsGamePaused(void);]]
    local adapter = X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4({
        enabled = true, pinned = pinned, own_index = config.index, observer = cockpit_reader, guard = guard,
    })
    return M.new({
        enabled = true,
        slot = 'cockpit',
        own_index = config.index,
        target_index = config.playback_index,
        allow_reacquire_align = true,
        trial_first_contact = config.trial_first_contact == true,
        adapter = adapter,
        clock = clock,
        game = function() return ffi.C.GetCurrentGameTime() end,
        paused = function() return ffi.C.IsGamePaused() end,
        record = make_x4_record({cockpit = cockpit_reader}, clock),
        fault = function(reason) DebugError('[X4LAN] AUTH_COMPANY_COCKPIT_STOP ' .. tostring(reason)) end,
        on_event = function(kind, detail)
            local parts = {'[X4LAN] AUTH_COMPANY_COCKPIT_EVENT', 'kind=' .. tostring(kind)}
            for k, v in pairs(detail or {}) do parts[#parts + 1] = tostring(k) .. '=' .. tostring(v) end
            DebugError(table.concat(parts, ' '))
        end,
    })
end

function M.new_multi_x4(config, pinned, cockpit_readers, guard, clock)
    local ffi = require('ffi')
    ffi.cdef[[double GetCurrentGameTime(void); bool IsGamePaused(void);]]
    local slot_names, adapters = {}, {}
    for _, name in ipairs(COCKPIT_SLOTS) do
        local reader = cockpit_readers[name]
        if reader ~= nil then
            slot_names[#slot_names + 1] = name
            adapters[name] = X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4({
                enabled = true, pinned = pinned, own_index = config.index, observer = reader, guard = guard,
            })
        end
    end
    return M.new_multi({
        enabled = true,
        slot_names = slot_names,
        own_index = config.index,
        allow_reacquire_align = true,
        trial_first_contact = config.trial_first_contact == true,
        adapters = adapters,
        clock = clock,
        game = function() return ffi.C.GetCurrentGameTime() end,
        paused = function() return ffi.C.IsGamePaused() end,
        record = make_x4_record(cockpit_readers, clock),
        fault = function(reason, _, _, slot_name)
            DebugError('[X4LAN] AUTH_COMPANY_COCKPIT_STOP slot=' .. tostring(slot_name or 'cockpit') .. ' ' .. tostring(reason))
        end,
        on_event = function(kind, detail)
            local parts = {'[X4LAN] AUTH_COMPANY_COCKPIT_EVENT', 'kind=' .. tostring(kind)}
            for k, v in pairs(detail or {}) do parts[#parts + 1] = tostring(k) .. '=' .. tostring(v) end
            DebugError(table.concat(parts, ' '))
        end,
    })
end

X4LAN_COMPANY_COCKPIT_PLAYBACK = M
return M
