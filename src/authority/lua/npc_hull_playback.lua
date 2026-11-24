-- Opt-in multi-NPC hull playback controller reusing X4LAN_MOTION_BUFFER and X4LAN_MOTION_PLAYBACK.
-- Default-off. No wire APIs or unsolicited game writes; caller supplies native read/write/align callbacks.
local M = {}

local MAX_STEP = 50
local PREDICTION_SECONDS = 5
local READBACK_TOLERANCE = 3
local CAPTURE_CONTINUITY_LIMIT = 60
local LIMIT = 10000000
local MAX_RATE = 5000
local MAX_SEQUENCE = 9007199254740991

M.MAX_STEP = MAX_STEP
M.MAX_PREDICTION_SECONDS = PREDICTION_SECONDS
M.READBACK_TOLERANCE = READBACK_TOLERANCE

local function finite_in(value, lo, hi)
    return type(value) == 'number' and value == value and value >= lo and value <= hi
end

local function positive_int(value, hi)
    return finite_in(value, 1, hi) and value % 1 == 0
end

local function hex32(value)
    return type(value) == 'string' and #value == 32 and value:match('^[0-9a-f]+$') ~= nil
end

local function valid_sector(value)
    return type(value) == 'string' and #value >= 1 and #value <= 128 and value:match('^[A-Za-z0-9_]+$') ~= nil
end

local function valid_actor(value)
    return type(value) == 'string' and #value >= 1 and #value <= 24
        and (value:match('^0[xX][0-9A-Fa-f]+$') ~= nil or value:match('^[1-9][0-9]*$') ~= nil)
end

local function valid_code(value)
    return type(value) == 'string' and #value >= 1 and #value <= 32 and value:match('^[A-Z0-9]+%-%d+$') ~= nil
end

local function xyz(value, limit)
    if type(value) ~= 'table' or getmetatable(value) ~= nil then return nil end
    local x, y, z = value.x, value.y, value.z
    if finite_in(x, -limit, limit) and finite_in(y, -limit, limit) and finite_in(z, -limit, limit) then
        return {x = x, y = y, z = z}
    end
    return nil
end

local function copy_table(value)
    local out = {}
    for k, v in pairs(value) do out[k] = v end
    return out
end

local function dist3(a, b)
    local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local function validate_binding(binding, own_index)
    if type(binding) ~= 'table' or getmetatable(binding) ~= nil then return nil end
    if not (hex32(binding.epoch) and positive_int(binding.index, 200000) and binding.index ~= own_index
            and valid_sector(binding.sector_macro) and valid_actor(binding.actor)
            and valid_code(binding.code)) then
        return nil
    end
    return {
        epoch = binding.epoch,
        index = binding.index,
        sector_macro = binding.sector_macro,
        actor = binding.actor,
        code = binding.code,
    }
end

local function same_binding(a, b)
    return a ~= nil and b ~= nil
        and a.epoch == b.epoch and a.index == b.index
        and a.sector_macro == b.sector_macro and a.actor == b.actor and a.code == b.code
end

function M.new(options)
    if type(options) ~= 'table' or options.enabled ~= true then
        return nil, 'disabled'
    end
    local cfg = copy_table(options)
    for _, key in ipairs({'clock', 'game', 'paused', 'guard', 'read', 'write', 'record', 'fault'}) do
        assert(type(cfg[key]) == 'function', 'missing callback: ' .. key)
    end
    if cfg.align ~= nil then
        assert(type(cfg.align) == 'function', 'align must be a function')
    end
    local buffer_mod = cfg.buffer or X4LAN_MOTION_BUFFER
    local playback_mod = cfg.playback or X4LAN_MOTION_PLAYBACK
    assert(type(buffer_mod) == 'table' and type(buffer_mod.new) == 'function', 'motion_buffer required')
    assert(type(playback_mod) == 'table' and type(playback_mod.new) == 'function', 'motion_playback required')

    if not positive_int(cfg.own_index, 200000) then
        return nil, 'invalid_own_index'
    end
    local initial_binding = validate_binding(cfg.binding, cfg.own_index)
    if not initial_binding then
        return nil, 'invalid_npc_binding'
    end
    local allow_reacquire_align = cfg.allow_reacquire_align == true

    local binding = initial_binding
    local generation = nil
    local state = 'waiting'   -- waiting | active | stale | withdrawn | ended | align_readback | failed | stopped
    local player = nil
    local refusal = nil
    local last_arrival = nil
    local last_sequence = 0
    local last_capture_time = nil
    local last_source_time = nil
    local rendered_source = nil
    local write_source = nil
    local reacquire_align_allowed = false
    local pending_align = nil
    local transport_lost_flag = false

    local stats = {
        writes = 0,
        readbacks = 0,
        alignments = 0,
        withdrawals = 0,
        reacquisitions = 0,
        stale_cutoffs = 0,
        refusals_over_50m = 0,
        discarded_offscreen = 0,
        discarded_stale = 0,
        last_readback = nil,
    }

    local function stop_player()
        if player then
            player:stop()
            player = nil
        end
        rendered_source = nil
        write_source = nil
        refusal = nil
    end

    local function emit_event(kind, detail)
        if type(cfg.on_event) == 'function' then
            pcall(cfg.on_event, kind, copy_table(binding), detail or {})
        end
    end

    local function is_expired(now)
        if not finite_in(now, 0, 1e12) then return nil end
        return last_arrival ~= nil and now >= last_arrival + PREDICTION_SECONDS
    end

    local function create_player(track_binding, track_generation)
        local checked_buffer = {}
        function checked_buffer.new(settings)
            local inner = buffer_mod.new(settings)
            local lineage = {}
            local newest = nil
            return {
                push = function(_, sample, arrival)
                    local ok, reason = inner:push(sample, arrival)
                    if not ok or reason ~= 'append' then
                        refusal = reason == 'gap' and 'capture_gap'
                            or reason == 'teleport' and 'source_discontinuity'
                            or 'invalid_source_progress'
                        return false
                    end
                    lineage[#lineage + 1] = {
                        sequence = sample.sequence,
                        source_time = sample.source_time,
                        capture_time = sample.capture_time,
                    }
                    if #lineage > 16 then table.remove(lineage, 1) end
                    newest = {pose = sample.pose, rate = sample.real_rate, arrival = arrival}
                    return true
                end,
                render = function(_, now)
                    local rendered = inner:render(now)
                    if not rendered then return nil end
                    if (rendered.phase == 'extrapolated' or rendered.phase == 'frozen') and newest then
                        -- Transit jitter must never predict farther than elapsed local time up to 5 s.
                        local elapsed = math.min(PREDICTION_SECONDS, math.max(0, now - newest.arrival))
                        for axis = 1, 3 do
                            local start = newest.pose[axis]
                            local bound = start + newest.rate[axis] * elapsed
                            rendered.pose[axis] = math.min(math.max(start, bound),
                                math.max(math.min(start, bound), rendered.pose[axis]))
                        end
                    end
                    for _, src in ipairs(lineage) do
                        if src.sequence == rendered.source_sequence then
                            rendered_source = src
                            return rendered
                        end
                    end
                    return nil
                end,
            }
        end

        local function playback_guard()
            if state ~= 'active' and state ~= 'waiting' then return false end
            if cfg.paused() ~= false then
                refusal = 'paused'
                return false
            end
            if cfg.guard(copy_table(track_binding)) ~= true then
                refusal = 'binding_lost'
                return false
            end
            return true
        end

        return assert(playback_mod.new({
            enabled = true,
            buffer = checked_buffer,
            sample_version = 3,
            frame = track_binding.sector_macro,
            max_age = PREDICTION_SECONDS,
            max_step = MAX_STEP,
            buffer_options = {
                delay = 0.15,
                horizon = PREDICTION_SECONDS,
                stale = CAPTURE_CONTINUITY_LIMIT,
                teleport_distance = MAX_STEP,
            },
            clock = cfg.clock,
            game = cfg.game,
            paused = cfg.paused,
            guard = playback_guard,
            read = function()
                local actual = assert(xyz(cfg.read(copy_table(track_binding)), LIMIT), 'invalid native npc hull read')
                actual.yaw, actual.pitch, actual.roll = 0, 0, 0
                return actual
            end,
            write = function(target, before)
                local ok_now, now = pcall(cfg.clock)
                if not ok_now then refusal = 'invalid_local_clock'; return false end
                local expired = is_expired(now)
                if expired ~= false then
                    refusal = expired and 'stale' or 'invalid_local_clock'
                    return false
                end
                local pos = assert(xyz(target, LIMIT))
                local pre = assert(xyz(before, LIMIT))
                local step = dist3(pos, pre)
                if step > MAX_STEP then
                    refusal = 'correction_out_of_bounds'
                    stats.refusals_over_50m = stats.refusals_over_50m + 1
                    return false
                end
                write_source = rendered_source
                local written = cfg.write(copy_table(track_binding), pos, pre)
                if written == true then
                    stats.writes = stats.writes + 1
                end
                return written
            end,
            record = function(event)
                local is_readback = event.kind == 'readback'
                if is_readback then
                    if event.elapsed <= 0 then
                        refusal = 'readback_not_later'
                        return false
                    end
                    if event.error > READBACK_TOLERANCE then
                        refusal = 'readback_out_of_bounds'
                        return false
                    end
                end
                local src = is_readback and write_source or rendered_source
                assert(src and src.sequence == event.source_sequence)
                local rec = {
                    kind = event.kind,
                    epoch = track_binding.epoch,
                    index = track_binding.index,
                    generation = track_generation,
                    sector_macro = track_binding.sector_macro,
                    code = track_binding.code,
                    source_sequence = src.sequence,
                    source_time = src.source_time,
                    capture_time = src.capture_time,
                    local_time = event.local_time,
                    game_time = event.game_time,
                    actual = xyz(event.actual, LIMIT),
                    target = xyz(event.target, LIMIT),
                    phase = event.phase,
                    error = event.error,
                    elapsed = event.elapsed,
                }
                if is_readback then
                    stats.readbacks = stats.readbacks + 1
                    stats.last_readback = rec
                end
                return cfg.record(rec)
            end,
            fault = function(_, _, stage)
                local reason = refusal or (stage == 'distance' and 'correction_out_of_bounds') or 'playback_failed'
                if reason == 'correction_out_of_bounds' then
                    stats.refusals_over_50m = stats.refusals_over_50m + 1
                end
                pcall(cfg.fault, reason, player and player:outcome_uncertain() or false, stage)
            end,
        }))
    end

    local function settle_alignment_readback(now)
        if not pending_align then return true end
        if now <= pending_align.written_at then return true end
        if cfg.paused() ~= false or cfg.guard(copy_table(binding)) ~= true then
            pending_align = nil
            state = 'ended'
            return false, 'alignment_guard_lost'
        end
        local actual = xyz(cfg.read(copy_table(binding)), LIMIT)
        if not actual then
            pending_align = nil
            state = 'failed'
            pcall(cfg.fault, 'alignment_read_failed', true, 'alignment_readback')
            return false, 'alignment_read_failed'
        end
        local err = dist3(actual, pending_align.target)
        local elapsed = now - pending_align.written_at
        local gt = cfg.game()
        local rec = {
            kind = 'alignment_readback',
            epoch = binding.epoch,
            index = binding.index,
            generation = pending_align.generation,
            sector_macro = binding.sector_macro,
            code = binding.code,
            source_sequence = pending_align.sequence,
            source_time = pending_align.source_time,
            capture_time = pending_align.capture_time,
            local_time = now,
            game_time = gt,
            actual = actual,
            target = pending_align.target,
            phase = 'reacquire_aligned',
            error = err,
            elapsed = elapsed,
        }
        stats.readbacks = stats.readbacks + 1
        stats.last_readback = rec
        cfg.record(rec)
        pending_align = nil
        if err > READBACK_TOLERANCE then
            state = 'failed'
            pcall(cfg.fault, 'alignment_readback_out_of_bounds', false, 'alignment_readback')
            return false, 'alignment_readback_out_of_bounds'
        end
        state = 'waiting'
        emit_event('reacquire_aligned_confirmed', {error = err, elapsed = elapsed, sequence = rec.source_sequence})
        return true
    end

    local api = {}

    function api:binding()
        return copy_table(binding)
    end

    function api:state()
        return state
    end

    function api:stats()
        local out = copy_table(stats)
        out.state = state
        out.last_sequence = last_sequence
        out.reacquire_align_allowed = reacquire_align_allowed
        out.transport_lost = transport_lost_flag
        return out
    end

    function api:transport_lost(reason)
        if state == 'stopped' or state == 'failed' then return false end
        transport_lost_flag = true
        emit_event('transport_lost', {reason = reason or 'transport_lost'})
        -- Do NOT stop the active player; Lith7ium's 5-second local prediction window continues until deadline.
        return true
    end

    function api:withdraw(reason)
        if state == 'stopped' or state == 'failed' then return false end
        stop_player()
        pending_align = nil
        last_arrival = nil
        transport_lost_flag = false
        if state ~= 'withdrawn' then
            stats.withdrawals = stats.withdrawals + 1
        end
        state = 'withdrawn'
        reacquire_align_allowed = true
        emit_event('withdrawn', {reason = reason or 'offscreen'})
        return true
    end

    function api:end_track(reason)
        if state == 'stopped' or state == 'failed' then return false end
        stop_player()
        pending_align = nil
        last_arrival = nil
        transport_lost_flag = false
        state = 'ended'
        reacquire_align_allowed = true
        emit_event('ended', {reason = reason or 'end'})
        return true
    end

    function api:reacquire_view(new_binding)
        if state == 'stopped' or state == 'failed' then return false, state end
        local checked = validate_binding(new_binding, cfg.own_index)
        if not checked or checked.epoch ~= binding.epoch or checked.index ~= binding.index then
            return false, 'invalid_reacquire_binding'
        end
        local changed_sector = checked.sector_macro ~= binding.sector_macro
        if changed_sector or not same_binding(checked, binding) then
            stop_player()
            pending_align = nil
            last_arrival = nil
            binding = checked
            reacquire_align_allowed = true
        end
        if state == 'withdrawn' or state == 'stale' or state == 'ended' then
            stats.reacquisitions = stats.reacquisitions + 1
            reacquire_align_allowed = true
            state = 'waiting'
            transport_lost_flag = false
            emit_event('reacquire_view', {
                sector_macro = binding.sector_macro,
                changed_sector = changed_sector,
            })
        end
        return true
    end

    function api:probe_step_guard(target_pos)
        -- Explicit read-only guard test (never mutates hull if step > MAX_STEP).
        local pos = xyz(target_pos, LIMIT)
        if not pos then return false, 'invalid_target' end
        if cfg.paused() ~= false or cfg.guard(copy_table(binding)) ~= true then
            return false, 'guard_refused'
        end
        local actual = xyz(cfg.read(copy_table(binding)), LIMIT)
        if not actual then return false, 'read_failed' end
        local step = dist3(pos, actual)
        if step > MAX_STEP then
            stats.refusals_over_50m = stats.refusals_over_50m + 1
            emit_event('refused_over_50m', {dist = step, max_step = MAX_STEP})
            return false, 'correction_out_of_bounds', step
        end
        return true, 'within_bounds', step
    end

    function api:push(sample, arrival)
        if state == 'stopped' or state == 'failed' then return false, state end
        if state == 'withdrawn' then
            stats.discarded_offscreen = stats.discarded_offscreen + 1
            return false, 'discarded_offscreen'
        end
        if type(sample) ~= 'table' or getmetatable(sample) ~= nil or sample.version ~= 3
                or sample.epoch ~= binding.epoch or sample.index ~= binding.index
                or not hex32(sample.generation) then
            stats.discarded_stale = stats.discarded_stale + 1
            return false, 'sample_identity_mismatch'
        end
        if sample.sector_macro ~= binding.sector_macro then
            stats.discarded_offscreen = stats.discarded_offscreen + 1
            return false, 'sector_mismatch'
        end
        local pos = xyz(sample, LIMIT)
        if not pos or not positive_int(sample.sequence, MAX_SEQUENCE)
                or not finite_in(sample.source_time, 0, 1e12)
                or not finite_in(sample.capture_time, 0, 1e12)
                or not finite_in(arrival, 0, 1e12) then
            return false, 'invalid_sample'
        end
        local ok_now, now = pcall(cfg.clock)
        if not ok_now or not finite_in(now, 0, 1e12) or arrival > now then
            return false, 'invalid_local_clock'
        end
        if now - arrival >= PREDICTION_SECONDS then
            stats.discarded_stale = stats.discarded_stale + 1
            return false, 'expired_on_arrival'
        end

        local new_gen = (generation ~= nil and sample.generation ~= generation)
        if not new_gen and last_sequence > 0 then
            if sample.sequence <= last_sequence
                    or (last_capture_time and sample.capture_time <= last_capture_time)
                    or (last_source_time and sample.source_time < last_source_time) then
                stats.discarded_stale = stats.discarded_stale + 1
                return false, 'non_monotone_sample'
            end
        end

        local vel = nil
        if sample.real_velocity ~= nil then
            vel = xyz(sample.real_velocity, MAX_RATE)
            if not vel then return false, 'invalid_velocity' end
        end

        -- If waiting on alignment readback, settle first if clock advanced.
        if state == 'align_readback' then
            local ok_align, align_err = settle_alignment_readback(now)
            if not ok_align then return false, align_err end
            if state == 'align_readback' then
                -- Alignment just wrote on this exact timestamp; accept watermark and wait for next tick.
                return true, 'align_readback_pending'
            end
        end

        -- If active track expired locally (>= 5 s since last arrival), retire the old player cleanly first.
        if player and last_arrival and (now >= last_arrival + PREDICTION_SECONDS or arrival >= last_arrival + PREDICTION_SECONDS) then
            stop_player()
            last_arrival = nil
            stats.stale_cutoffs = stats.stale_cutoffs + 1
            state = 'stale'
            reacquire_align_allowed = true
            emit_event('stale_5s', {sequence = last_sequence})
        end

        if new_gen then
            stop_player()
            last_arrival = nil
            generation = sample.generation
            reacquire_align_allowed = true
            state = 'waiting'
        elseif generation == nil then
            generation = sample.generation
        end

        -- Handle reacquisition / first baseline distance check when no player is currently active.
        if not player then
            if cfg.paused() ~= false then return false, 'paused' end
            if cfg.guard(copy_table(binding)) ~= true then return false, 'binding_lost' end
            local actual = xyz(cfg.read(copy_table(binding)), LIMIT)
            if not actual then return false, 'read_failed' end
            local gap = dist3(pos, actual)
            if gap > MAX_STEP then
                if not reacquire_align_allowed or not allow_reacquire_align or type(cfg.align) ~= 'function' then
                    stats.refusals_over_50m = stats.refusals_over_50m + 1
                    emit_event('refused_over_50m', {dist = gap, max_step = MAX_STEP, stage = 'reacquire'})
                    return false, 'correction_out_of_bounds'
                end
                -- Strictly ONE authorized fresh alignment per loss/withdrawal episode!
                reacquire_align_allowed = false
                local aligned = cfg.align(copy_table(binding), pos, actual)
                if aligned ~= true then
                    state = 'failed'
                    pcall(cfg.fault, 'alignment_failed', true, 'align')
                    return false, 'alignment_failed'
                end
                stats.alignments = stats.alignments + 1
                last_sequence = sample.sequence
                last_capture_time = sample.capture_time
                last_source_time = sample.source_time
                pending_align = {
                    written_at = now,
                    target = pos,
                    generation = generation,
                    sequence = sample.sequence,
                    source_time = sample.source_time,
                    capture_time = sample.capture_time,
                    gap_metres = gap,
                }
                state = 'align_readback'
                emit_event('reacquire_aligned', {dist = gap, sequence = sample.sequence})
                return true, 'aligned'
            end
            -- Within <= 50 m! If baseline has no velocity yet, record watermark and wait for velocity sample.
            if vel == nil then
                last_sequence = sample.sequence
                last_capture_time = sample.capture_time
                last_source_time = sample.source_time
                return true, 'baseline_recorded'
            end
            player = create_player(binding, generation)
            reacquire_align_allowed = false
            state = 'active'
            transport_lost_flag = false
        end

        -- Active player exists.
        if vel == nil then
            -- Never invent velocity or reset the 5-second deadline on a velocity-less sample.
            return true, 'waiting_for_velocity'
        end

        local ok, reason = player:push({
            version = 3,
            frame = binding.sector_macro,
            sequence = sample.sequence,
            source_time = sample.source_time,
            capture_time = sample.capture_time,
            pose = {pos.x, pos.y, pos.z},
            real_rate = {vel.x, vel.y, vel.z},
        }, arrival)
        if not ok then
            local stop_reason = refusal or reason or 'push_rejected'
            stop_player()
            last_arrival = nil
            if stop_reason == 'source_discontinuity' or stop_reason == 'correction_out_of_bounds' then
                stats.refusals_over_50m = stats.refusals_over_50m + 1
                emit_event('refused_over_50m', {reason = stop_reason, sequence = sample.sequence})
                state = 'ended'
                return false, 'correction_out_of_bounds'
            end
            state = 'stale'
            reacquire_align_allowed = true
            return false, stop_reason
        end

        last_arrival = arrival
        last_sequence = sample.sequence
        last_capture_time = sample.capture_time
        last_source_time = sample.source_time
        state = 'active'
        transport_lost_flag = false
        return true, 'accepted'
    end

    function api:tick()
        if state == 'stopped' or state == 'failed' then return false, state end
        local ok_now, now = pcall(cfg.clock)
        if not ok_now or not finite_in(now, 0, 1e12) then
            stop_player()
            state = 'failed'
            pcall(cfg.fault, 'invalid_local_clock', false, 'clock')
            return false, 'invalid_local_clock'
        end
        if state == 'align_readback' then
            return settle_alignment_readback(now)
        end
        if not player then
            return true, state
        end
        if cfg.paused() ~= false then
            stop_player()
            last_arrival = nil
            state = 'ended'
            reacquire_align_allowed = true
            emit_event('paused_stop', {})
            return true, 'paused'
        end
        local ok = player:tick()
        local pstate = player and player:state() or 'stopped'
        if not ok then
            local reason = refusal or 'playback_failed'
            stop_player()
            last_arrival = nil
            if reason == 'stale' then
                stats.stale_cutoffs = stats.stale_cutoffs + 1
                state = 'stale'
                reacquire_align_allowed = true
                emit_event('stale_5s', {sequence = last_sequence})
                return true, 'stale'
            end
            state = 'ended'
            return false, reason
        end
        if pstate == 'stale' or is_expired(now) == true then
            stop_player()
            last_arrival = nil
            stats.stale_cutoffs = stats.stale_cutoffs + 1
            state = 'stale'
            reacquire_align_allowed = true
            emit_event('stale_5s', {sequence = last_sequence})
            return true, 'stale'
        end
        return true, state
    end

    function api:stop()
        stop_player()
        pending_align = nil
        last_arrival = nil
        state = 'stopped'
        return true
    end

    return api
end

function M.new_pool(options)
    if type(options) ~= 'table' or options.enabled ~= true then
        return nil, 'disabled'
    end
    local max_tracks = options.max_tracks or 32
    assert(positive_int(max_tracks, 256), 'invalid max_tracks')
    assert(type(options.make_controller) == 'function', 'make_controller required')
    local clock = assert(options.clock, 'clock required')
    local tracks = {}
    local order = {}
    local last_tick_ms = 0
    local max_tick_ms = 0
    local total_ticks = 0

    local pool = {}

    function pool:ensure(binding)
        local idx = binding and binding.index
        assert(positive_int(idx, 200000), 'invalid pool index')
        local existing = tracks[idx]
        if existing then
            existing:reacquire_view(binding)
            return existing
        end
        if #order >= max_tracks then
            return nil, 'pool_capacity_reached'
        end
        local ctrl, err = options.make_controller(binding)
        if not ctrl then return nil, err end
        tracks[idx] = ctrl
        order[#order + 1] = idx
        return ctrl
    end

    function pool:get(index)
        return tracks[index]
    end

    function pool:remove(index)
        local ctrl = tracks[index]
        if not ctrl then return false end
        ctrl:stop()
        tracks[index] = nil
        for i = 1, #order do
            if order[i] == index then
                table.remove(order, i)
                break
            end
        end
        return true
    end

    function pool:tick_all()
        local t0 = clock()
        for i = 1, #order do
            local ctrl = tracks[order[i]]
            if ctrl then ctrl:tick() end
        end
        local dt_ms = (clock() - t0) * 1000.0
        last_tick_ms = dt_ms
        if dt_ms > max_tick_ms then max_tick_ms = dt_ms end
        total_ticks = total_ticks + 1
        return dt_ms
    end

    function pool:stats()
        local active = 0
        local by_index = {}
        for i = 1, #order do
            local idx = order[i]
            local s = tracks[idx]:stats()
            by_index[idx] = s
            if s.state == 'active' then active = active + 1 end
        end
        return {
            track_count = #order,
            active_count = active,
            total_ticks = total_ticks,
            last_tick_ms = last_tick_ms,
            max_tick_ms = max_tick_ms,
            tracks = by_index,
        }
    end

    function pool:stop_all()
        for i = 1, #order do
            local ctrl = tracks[order[i]]
            if ctrl then ctrl:stop() end
        end
        tracks = {}
        order = {}
        return true
    end

    return pool
end

X4LAN_NPC_HULL_PLAYBACK = M
return M
