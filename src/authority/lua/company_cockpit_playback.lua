-- Opt-in receiving-cockpit Company ship playback with 5 s loss limit, strict generation checks, and reacquisition.
-- Reuses X4LAN_COMPANY_HULL_PLAYBACK (X4LAN_MOTION_BUFFER + X4LAN_MOTION_PLAYBACK) and X4LAN_COMPANY_HULL_PLAYBACK_X4.
local M = {}
local MAX_STEP = 50
local READBACK_TOLERANCE = 3

local function dist3(a, b)
    local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local function hex32(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
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
    local factory = options.playback or X4LAN_COMPANY_HULL_PLAYBACK
    local prediction_seconds = assert(factory.MAX_PREDICTION_SECONDS)
    local allow_reacquire_align = options.allow_reacquire_align == true

    local binding, session, controller, last
    local active_generation = nil
    local revoked_generations = {}
    local reacquire_align_allowed = false
    local pending_align = nil
    local last_tick_now = nil
    local closed = false

    local stats = {
        binds = 0,
        writes = 0,
        readbacks = 0,
        stale_cutoffs = 0,
        withdrawals = 0,
        reacquisitions = 0,
        alignments = 0,
        generation_rejections = 0,
        refusals_over_50m = 0,
        active_generation = nil,
    }

    local function emit(kind, detail)
        if type(options.on_event) == 'function' then
            pcall(options.on_event, kind, detail or {})
        end
    end

    local function paused()
        return options.paused and options.paused() ~= false
    end

    local function stop_track()
        if controller then controller:stop(); controller = nil end
        if session then session:close(); session = nil end
        pending_align = nil
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
        local actual = session:read()
        if not actual then
            stop_track()
            options.fault('alignment_read_failed')
            return false
        end
        local err = dist3(actual, pending_align.target)
        local elapsed = now - pending_align.written_at
        local rec = {
            kind = 'alignment_readback',
            slot = 'cockpit',
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
            phase = 'reacquire_aligned',
            error = err,
            elapsed = elapsed,
        }
        stats.readbacks = stats.readbacks + 1
        options.record(rec)
        pending_align = nil
        if err > READBACK_TOLERANCE then
            stop_track()
            options.fault('alignment_readback_out_of_bounds')
            return false
        end
        -- Close the alignment session so the next velocity sample opens a fresh controller on this aligned pose.
        stop_track()
        emit('reacquire_aligned_confirmed', {error = err, elapsed = elapsed, sequence = rec.source_sequence})
        return true
    end

    local function retire(reason, now)
        local uncertain = controller and controller:outcome_uncertain() == true
        stop_track()
        if uncertain then
            closed = true
            emit('closed_uncertain', {reason = reason})
            return
        end
        if reason == 'stale' or reason == 'arrival_gap' then
            stats.stale_cutoffs = stats.stale_cutoffs + 1
            reacquire_align_allowed = true
            emit('stale_5s', {reason = reason})
        elseif reason == 'correction_out_of_bounds' then
            stats.refusals_over_50m = stats.refusals_over_50m + 1
            emit('refused_over_50m', {reason = reason})
        else
            reacquire_align_allowed = true
            emit('track_stopped', {reason = reason})
        end
    end

    local function bind_controller(observed, serial, generation)
        binding = {
            slot = 'cockpit', serial = serial, epoch = observed.epoch, index = observed.index,
            actor = observed.actor, code = observed.code, sector_macro = observed.sector_macro,
            generation = generation,
        }
        session = options.adapter:bind(binding)
        if not session then
            options.fault('native_binding')
            return false
        end
        stats.binds = stats.binds + 1
        local active_session = session
        controller = assert(factory.new({
            enabled = true, own_index = own_index, binding = binding,
            clock = options.clock, game = options.game, paused = options.paused,
            guard = function() return active_session:guard() end,
            read = function() return active_session:read() end,
            write = function(_, target, before)
                local ok = active_session:write(target, before)
                if ok == true then stats.writes = stats.writes + 1 end
                return ok
            end,
            record = function(ev)
                if ev.kind == 'readback' then stats.readbacks = stats.readbacks + 1 end
                return options.record(ev)
            end,
            fault = function(reason, uncertain, stage)
                if reason == 'correction_out_of_bounds' then
                    stats.refusals_over_50m = stats.refusals_over_50m + 1
                end
                options.fault(reason, uncertain, stage)
            end,
        }))
        return true
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
        out.active_generation = active_generation
        out.reacquire_align_allowed = reacquire_align_allowed
        return out
    end

    function ctrl:view(observed, serial)
        if closed then return end
        if not binding then return end
        if observed == nil or not same_view(observed, serial) then
            if controller or pending_align then
                stop_track()
            end
            if observed == nil or not same_hull(observed, binding) then
                stats.withdrawals = stats.withdrawals + 1
                reacquire_align_allowed = true
                emit('withdrawn', {index = binding.index})
            end
            binding = nil
        end
    end

    function ctrl:end_track(observed, serial, generation)
        if closed then return end
        if target_index ~= nil and observed.index ~= target_index then return end
        if hex32(generation) then
            revoked_generations[generation] = true
        end
        if active_generation == generation or (binding and same_view(observed, serial) and binding.generation == generation) then
            stop_track()
            if active_generation == generation then
                active_generation = nil
                stats.active_generation = nil
                last = nil
            end
            reacquire_align_allowed = true
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
            emit('refused_over_50m', {dist = step, max_step = MAX_STEP})
            return false, 'correction_out_of_bounds', step
        end
        return true, 'within_bounds', step
    end

    function ctrl:tick()
        if closed then return end
        if paused() then
            if controller or pending_align then
                stop_track()
                reacquire_align_allowed = true
                emit('paused_stop', {})
            end
            return
        end
        local now = options.clock()
        if last_tick_now ~= nil and now <= last_tick_now then
            return
        end
        last_tick_now = now
        if pending_align then
            settle_alignment(now)
            return
        end
        if controller then
            local ok, reason = controller:tick()
            if not ok then retire(reason or controller:state(), now) end
        end
    end

    function ctrl:sample(observed, serial, sample, arrival)
        if closed or not observed or observed.index == own_index then return false, 'ineligible' end
        if target_index ~= nil and observed.index ~= target_index then return false, 'other_target' end
        if paused() then
            if controller or pending_align then stop_track(); reacquire_align_allowed = true end
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
        if revoked_generations[sample.generation] then
            stats.generation_rejections = stats.generation_rejections + 1
            emit('rejected_revoked_generation', {generation = sample.generation, sequence = sample.sequence})
            return false, 'revoked_generation'
        end

        -- Strict generation check: never mix poses across two active grants without prior END/retirement.
        if active_generation ~= nil and sample.generation ~= active_generation then
            if controller then
                stats.generation_rejections = stats.generation_rejections + 1
                emit('generation_mismatch', {expected = active_generation, actual = sample.generation})
                return false, 'generation_mismatch'
            end
            -- Previous track ended/staled cleanly; switch to new generation cleanly.
            active_generation = sample.generation
            stats.active_generation = active_generation
            last = nil
            reacquire_align_allowed = true
        elseif active_generation == nil then
            active_generation = sample.generation
            stats.active_generation = active_generation
        end

        -- Settle pending alignment readback if clock advanced.
        if pending_align then
            if not settle_alignment(now) then return false, 'alignment_failed' end
            if pending_align then return true, 'alignment_pending' end
        end

        -- Enforce per-generation monotone ordering and 5-second cutoff settling.
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
                slot = 'cockpit', serial = serial, epoch = observed.epoch, index = observed.index,
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
            if gap > MAX_STEP then
                if not reacquire_align_allowed or not allow_reacquire_align or type(temp_session.align) ~= 'function' then
                    temp_session:close()
                    stats.refusals_over_50m = stats.refusals_over_50m + 1
                    emit('refused_over_50m', {dist = gap, max_step = MAX_STEP, stage = 'reacquire'})
                    return false, 'correction_out_of_bounds'
                end
                -- Strictly ONE authorized post-loss/post-withdrawal alignment per episode!
                reacquire_align_allowed = false
                binding = probe_binding
                session = temp_session
                local ok_align = session:align({x = sample.x, y = sample.y, z = sample.z})
                if ok_align ~= true then
                    stop_track()
                    options.fault('alignment_failed')
                    return false, 'alignment_failed'
                end
                stats.alignments = stats.alignments + 1
                stats.reacquisitions = stats.reacquisitions + 1
                pending_align = {
                    written_at = now,
                    target = {x = sample.x, y = sample.y, z = sample.z},
                    generation = sample.generation,
                    sequence = sample.sequence,
                    source_time = sample.source_time,
                    capture_time = sample.capture_time,
                }
                last = {
                    sequence = sample.sequence, capture_time = sample.capture_time,
                    source_time = sample.source_time, arrival = arrival,
                }
                emit('reacquire_aligned', {dist = gap, sequence = sample.sequence, mode = observed.mode or 'cockpit'})
                return true, 'aligned'
            end
            temp_session:close()
            if not sample.real_velocity then
                -- Record baseline watermark without inventing velocity.
                last = {
                    sequence = sample.sequence, capture_time = sample.capture_time,
                    source_time = sample.source_time, arrival = arrival,
                }
                return true, 'baseline_recorded'
            end
            if not bind_controller(observed, serial, sample.generation) then
                return false, 'native_binding'
            end
            if reacquire_align_allowed then
                stats.reacquisitions = stats.reacquisitions + 1
                emit('reacquired_direct', {dist = gap, sequence = sample.sequence, mode = observed.mode or 'cockpit'})
            end
            reacquire_align_allowed = false
        end

        if not sample.real_velocity then
            return true, 'waiting_for_velocity'
        end

        local ok, reason = controller:push(binding, sample, arrival)
        if not ok then
            retire(reason or controller:state(), now)
            return false, reason
        end
        last = {
            sequence = sample.sequence, capture_time = sample.capture_time,
            source_time = sample.source_time, arrival = arrival,
        }
        return true, 'accepted'
    end

    return ctrl
end

function M.new_x4(config, pinned, cockpit_reader, guard, clock)
    local ffi = require('ffi')
    ffi.cdef[[double GetCurrentGameTime(void); bool IsGamePaused(void);]]
    local adapter = X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4({
        enabled = true, pinned = pinned, own_index = config.index, observer = cockpit_reader, guard = guard,
    })
    local origin, last_logged_at, logged_count = nil, 0, 0
    local function decimal(value) return ('%.17g'):format(value) end
    local function coordinates(pose)
        return table.concat({decimal(pose.x), decimal(pose.y), decimal(pose.z)}, ',')
    end
    local function record(event)
        if not origin then origin = event.actual end
        if event.kind == 'readback' or event.kind == 'alignment_readback' then
            local now = clock()
            local actual, target = event.actual, event.target
            local dx, dy, dz = actual.x - origin.x, actual.y - origin.y, actual.z - origin.z
            local displacement = math.sqrt(dx * dx + dy * dy + dz * dz)
            if event.kind == 'alignment_readback' or logged_count < 3 or (now - last_logged_at >= 0.5) then
                last_logged_at = now
                logged_count = logged_count + 1
                local mode = cockpit_reader.mode and cockpit_reader:mode() or 'cockpit'
                DebugError(table.concat({
                    '[X4LAN] AUTH_COMPANY_COCKPIT_READBACK', 'kind=' .. event.kind, 'mode=' .. tostring(mode),
                    'ship=' .. event.code, 'epoch=' .. event.epoch, 'index=' .. event.index,
                    'sector=' .. event.sector_macro, 'generation=' .. event.generation,
                    'sequence=' .. event.source_sequence, 'source_time=' .. decimal(event.source_time),
                    'capture_time=' .. decimal(event.capture_time), 'elapsed=' .. decimal(event.elapsed),
                    'target=' .. coordinates(target), 'actual=' .. coordinates(actual),
                    'error=' .. decimal(event.error), 'displacement=' .. decimal(displacement),
                }, ' '))
            end
        end
        return true
    end
    return M.new({
        enabled = true,
        own_index = config.index,
        target_index = config.playback_index,
        allow_reacquire_align = true,
        adapter = adapter,
        clock = clock,
        game = function() return ffi.C.GetCurrentGameTime() end,
        paused = function() return ffi.C.IsGamePaused() end,
        record = record,
        fault = function(reason) DebugError('[X4LAN] AUTH_COMPANY_COCKPIT_STOP ' .. tostring(reason)) end,
        on_event = function(kind, detail)
            local parts = {'[X4LAN] AUTH_COMPANY_COCKPIT_EVENT', 'kind=' .. tostring(kind)}
            for k, v in pairs(detail or {}) do parts[#parts + 1] = tostring(k) .. '=' .. tostring(v) end
            DebugError(table.concat(parts, ' '))
        end,
    })
end

X4LAN_COMPANY_COCKPIT_PLAYBACK = M
return M
