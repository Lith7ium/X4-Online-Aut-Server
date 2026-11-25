-- Read-only selected-player source. The existing participant owns the update hook.
local M = {}
local seat

-- The native sampler reports only these operations and binding checks. Never
-- log an unrecognized value, which could include an engine event or Lua error.
local fault_labels = {}
local binding_checks = {'actor_key', 'id_convert', 'id_roundtrip', 'dock_values', 'external_binding'}
for _, operation in ipairs({'register', 'tick', 'sample', 'event', 'reset', 'close', 'discard_pending'}) do
    fault_labels[operation] = true
    for _, check in ipairs(binding_checks) do
        fault_labels[operation .. '_' .. check] = true
    end
end

for _, check in ipairs({'clock_invalid', 'clock_regressed', 'deadline', 'guard_error',
                         'guard_rejected', 'request_counter', 'request_format', 'reader_factory'}) do
    fault_labels['tick_' .. check] = true
end
for _, operation in ipairs({'tick_reader_start', 'tick_reader_tick'}) do
    fault_labels[operation] = true
    for _, check in ipairs({'clock_failed', 'clock_invalid', 'clock_timeout', 'context_lost',
                             'guard_failed', 'dispatch', 'reentrant', 'state_changed',
                             'callback_failed', 'unavailable', 'not_waiting', 'not_idle',
                             'actor_id', 'game_time', 'deadline'}) do
        fault_labels[operation .. '_' .. check] = true
    end
end

for _, check in ipairs({'response_type', 'response_mismatch', 'descriptor_unavailable',
                         'descriptor_invalid', 'transit_rejected', 'actor_changed', 'sector_id',
                         'snapshot_callback', 'snapshot_invalid', 'capture_clock_invalid', 'orientation_invalid',
                         'clock_invalid', 'clock_timeout', 'context_lost', 'clock_failed',
                         'guard_failed', 'reentrant', 'state_changed', 'callback_failed', 'unavailable'}) do
    fault_labels['sample_reader_response_' .. check] = true
end
fault_labels.sample_boundary_recheck_exhausted = true

function M.arm(verified_seat)
    assert(seat == nil or seat == verified_seat)
    seat = verified_seat
end

function M.new(options)
    local running, active, reset, failed = false, false, false, false
    local sampler, actor, in_transit
    local observer = options.observer
    local api = {}

    local function available()
        return running and not failed and options.available() == true
    end

    local function suspend()
        if not active then return end
        active, reset = false, true
        if sampler and sampler:state() == 'pending' then assert(sampler:discard_pending()) end
        options.send('GAP')
    end

    local function sample_outcome(ok, gap)
        if not ok then
            failed = true
        elseif gap == 'superhighway_transit' or gap == 'superhighway_boundary_recheck' then
            local was_in_transit = in_transit
            if not was_in_transit then
                in_transit = true
                options.send('GAP')
            end
            if options.superhighway_gap and (not was_in_transit or gap == 'superhighway_boundary_recheck') then
                options.superhighway_gap(gap)
            end
            return false
        elseif gap == 'pending_timeout' then
            if options.pending_timeout then options.pending_timeout() end
        else
            return true
        end
        suspend()
        return false
    end

    local function observe(enabled)
        if observer and not pcall(observer.tick, observer, enabled) then
            pcall(observer.close, observer)
            observer = nil
            options.observer_fault()
        end
    end

    function api:set_running(value)
        running = value
        if not running then suspend(); observe(false) end
    end

    function api:event(row)
        if not sampler or type(row) ~= 'string' then return end
        local request = sampler:expected_request()
        if not request or row:sub(1, 33) ~= request .. '|' then return end
        if not available() then
            suspend()
            return
        end
        local ok, sample, gap = sampler:event(row)
        if not sample_outcome(ok, gap) or not sample then return end
        in_transit = false
        local rate = sample.real_velocity
        local function number(value) return string.format('%.17g', value) end
        options.send(table.concat({
            'SAMPLE3', sample.epoch, tostring(sample.index),
            string.format('%.0f', sample.sequence), sample.sector_macro,
            number(sample.source_time), number(sample.capture_time),
            number(sample.x), number(sample.y), number(sample.z),
            rate and number(rate.x) or '-', rate and number(rate.y) or '-',
            rate and number(rate.z) or '-', '',
        }, '|'))
    end

    function api:tick()
        local enabled = available()
        observe(enabled)
        if not enabled then suspend(); return end
        if not sampler then
            actor = options.actor()
            if not actor then return end
            sampler = assert(options.make_sampler(actor, available))
            if not sampler:register({
                epoch = options.epoch, index = options.index, classification = 'saved_baseline',
            }, actor) then failed = true; return end
        end
        active = true
        if reset then
            if not sampler:reset_baseline(options.index) then failed = true; suspend(); return end
            reset = false
        end
        local ok, _, gap = sampler:tick()
        sample_outcome(ok, gap)
    end

    return api
end

function M.new_x4(config, save_path, pinned, udp, windows_company_profile)
    local ffi = require('ffi')
    assert(windows_company_profile == nil or ffi.os == 'Windows')
    ffi.cdef[[bool IsGamePaused(void); bool IsPlayerValid(void); const char* GetSaveFolderPath(void);]]
    local C = ffi.C
    local resolve = X4LAN_RESOLVE_STRING
    local normalize_path = X4LAN_REPLICA_MOTION_BINDING.normalize_profile_save_path
    local function normalize(path)
        return normalize_path(path, windows_company_profile)
    end
    local expected = assert(normalize(save_path))
    local net = udp.open('127.0.0.1', config.port)
    local function actor()
        local selected = seat and seat:controlled_actor()
        return selected and resolve.parse_hex(selected) or nil
    end
    local function available()
        if not C.IsPlayerValid() or C.IsGamePaused() or not actor() then return false end
        local actual = C.GetSaveFolderPath()
        return actual ~= nil and actual ~= ffi.NULL and normalize(ffi.string(actual)) == expected
    end
    local prefix = 'C|' .. config.lease .. '|' .. config.generation .. '|'
    local function observer_fault()
        DebugError('[X4LAN] AUTH_COMPANY_OBSERVER_UNAVAILABLE')
    end
    local ok, observer = pcall(function()
        if config.observed_npc_index then
            assert(not config.playback_index and not config.cockpit_radar_playback)
            assert(config.registered_npc_alignment_trial ~= true or config.registered_npc_hold_trial == true)
            local measure = config.registered_npc_local_measurement == true
            assert(not measure or (config.registered_npc_hold_trial ~= true and
                config.index == 1073 and config.observed_npc_index == 943 and config.observed_npc_code == 'MFQ-419'))
            local hold_trial
            local function send_npc_measurement(message)
                local packet = prefix .. message
                return #packet <= 576 and net:send(packet) == true
            end
            local function radar_withdrawn()
                if hold_trial then hold_trial:withdraw() end
            end
            local function npc_reader(mode)
                return assert(X4LAN_NPC_COCKPIT_OBSERVER.new_x4(pinned, config.index, available, {
                    enabled = true, mode = mode, target_index = config.observed_npc_index,
                    target_code = assert(config.observed_npc_code), clock = udp.monotonic,
                    local_measurement = measure and mode == 'radar',
                    send_measurement = measure and mode == 'radar' and send_npc_measurement or nil,
                    on_withdrawal = config.registered_npc_hold_trial == true and mode == 'radar' and radar_withdrawn or nil,
                }))
            end
            local readers = {cockpit = npc_reader('radar'), map = npc_reader('map')}
            if config.registered_npc_hold_trial == true then
                hold_trial = assert(X4LAN_REGISTERED_NPC_HOLD_TRIAL.new_x4({
                    enabled = true, pinned = pinned, own_index = config.index, target_index = config.observed_npc_index,
                    target_code = config.observed_npc_code, radar = readers.cockpit, nonce = config.nonce,
                    expected_save_path = expected, clock = udp.monotonic, guard = available, own_actor = actor,
                    alignment = config.registered_npc_alignment_trial == true,
                }))
            end
            return X4LAN_COMPANY_OBSERVER_STREAM.new(readers, pinned.epoch, net, prefix, udp.monotonic, nil, nil, hold_trial)
        end
        local map = X4LAN_COMPANY_OBSERVER_MAP.new_x4(pinned, config.index, available)
        local readers, trial, pilot_roster
        if config.cockpit_radar_playback == true then
            local max_pilots = config.cockpit_max_pilots or (config.playback_index and 1 or 3)
            local playback_mod = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK)
            if max_pilots > 1 or config.playback_index == nil then
                local multi = assert(X4LAN_COMPANY_COCKPIT_OBSERVER).new_multi_x4(pinned, config.index, available, {
                    max_pilots = max_pilots,
                    clock = udp.monotonic,
                })
                if config.playback_index == nil then pilot_roster = multi end
                readers = {map = map}
                for slot_name, reader in pairs(multi.readers) do
                    readers[slot_name] = reader
                end
                local created, candidate = pcall(playback_mod.new_multi_x4,
                                                 config, pinned, multi.readers, available, udp.monotonic)
                if created then trial = candidate
                else DebugError('[X4LAN] AUTH_COMPANY_SHIP_PLAYBACK_STOP initialization') end
            else
                local cockpit = assert(X4LAN_COMPANY_COCKPIT_OBSERVER).new_x4(pinned, config.index, available, {
                    radar_fallback = true,
                    target_index = config.playback_index,
                    clock = udp.monotonic,
                })
                readers = {cockpit = cockpit, map = map}
                local created, candidate = pcall(playback_mod.new_x4,
                                                 config, pinned, cockpit, available, udp.monotonic)
                if created then trial = candidate
                else DebugError('[X4LAN] AUTH_COMPANY_SHIP_PLAYBACK_STOP initialization') end
            end
        else
            local cockpit = X4LAN_COMPANY_OBSERVER_TARGET.new_x4(pinned, config.index, available)
            readers = {cockpit = cockpit, map = map}
            if config.playback_index then
                local created, candidate = pcall(X4LAN_COMPANY_HULL_TRIAL and X4LAN_COMPANY_HULL_TRIAL.new_x4,
                                                 config, pinned, cockpit,
                                                 available, udp.monotonic)
                if created then trial = candidate
                else DebugError('[X4LAN] AUTH_COMPANY_SHIP_PLAYBACK_STOP initialization') end
            end
        end
        return X4LAN_COMPANY_OBSERVER_STREAM.new(readers, pinned.epoch, net, prefix, udp.monotonic, trial,
                                                pilot_roster)
    end)
    if not ok then observer = nil; observer_fault() end
    local source = M.new({
        observer = observer, observer_fault = observer_fault,
        superhighway_gap = function(reason) DebugError('[X4LAN] AUTH_COMPANY_MOTION_SOURCE_GAP ' .. reason) end,
        pending_timeout = function() DebugError('[X4LAN] AUTH_COMPANY_MOTION_SOURCE_GAP pending_timeout') end,
        epoch = pinned.epoch, index = config.index, available = available, actor = actor,
        send = function(payload) net:send(prefix .. payload) end,
        make_sampler = function(selected, guard)
            return X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER.new_x4({
                enabled = true, epoch = pinned.epoch, generation = config.generation,
                nonce = config.nonce, max_tracks = 1, scan_budget = 1,
                interval = .10, timeout = 1, min_dt = .02, max_dt = 1,
                real_time_motion = true, capture_clock = udp.monotonic, recover_expired_pending = true,
                allow_superhighway_gap = true, recheck_superhighway_boundary = true,
                expected_save_path = expected, normalize_save_path = normalize,
                guard = guard, binding_guard = function() return false end,
                saved_baseline_limit = pinned.issued_count,
                saved_baseline_guard = function(row, entity)
                    return row.index == config.index and entity == selected and actor() == selected
                end,
                descriptor = X4LAN_AUTHORITY_JOB_DESCRIPTOR_PROBE, resolve = resolve,
                diagnostics = true,
                fault = function(reason)
                    local label = fault_labels[reason] and reason or 'unknown'
                    DebugError('[X4LAN] AUTH_COMPANY_MOTION_SOURCE_FAILED ' .. label)
                end,
            })
        end,
    })
    assert(RegisterEvent('X4AuthorityJobDescriptorProbe.Result',
                         function(_, row) source:event(row) end) ~= false)
    return source
end

X4LAN_COMPANY_MOTION_SOURCE = M
return M
