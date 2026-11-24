-- Default-off gate for correlated registry snapshots before checkpoint saves.
-- The caller owns events, ticks, pause/resume and the native save lifecycle.
local M = {}

local function token(value)
    return type(value) == 'string' and #value == 32
        and value:match('^[0-9a-f]+$') ~= nil
end

local function bits(value, count)
    return type(value) == 'string' and #value == count
        and value:match('^[01]+$') ~= nil
end

local function check_inspected_bits(label, expected, observed)
    if observed == expected then return end
    if type(observed) ~= 'string' or #observed ~= #expected then
        error(label .. ' vector has an unexpected length')
    end
    local count, examples = 0, {}
    for index = 1, #expected do
        local before, after = expected:byte(index), observed:byte(index)
        if before ~= after then
            count = count + 1
            if #examples < 6 then
                examples[#examples + 1] = string.format('%d(%c>%c)', index, before, after)
            end
        end
    end
    error(string.format('%s differs at %d indices, first %s', label, count,
        table.concat(examples, ',')))
end

local function copy(value)
    if type(value) ~= 'table' then return value end
    local result = {}
    for key, item in pairs(value) do result[key] = copy(item) end
    return result
end

function M.new(options)
    assert(type(options) == 'table' and options.enabled == true,
        'registry checkpoint gate is default-off')
    local first_capture = options.initial_mode == 'first_capture'
    assert(options.initial_mode == nil or first_capture, 'invalid initial mode')
    assert(token(options.epoch) and token(options.nonce),
        'epoch and nonce must be 32 lowercase hex characters')
    assert(options.epoch ~= options.nonce, 'gate correlation tokens must be distinct')
    if first_capture then
        assert(options.initial_run_id == nil and options.expected_binding_bits == nil
            and options.expected_operational_bits == nil,
            'first capture cannot claim a saved snapshot')
        assert(token(options.source_marker) and options.source_marker ~= options.epoch
            and options.source_marker ~= options.nonce,
            'distinct confirmed source marker required')
    else
        assert(options.source_marker == nil and token(options.initial_run_id),
            'initial_run_id must identify a saved snapshot')
        assert(options.initial_run_id ~= options.epoch and options.initial_run_id ~= options.nonce,
            'gate correlation tokens must be distinct')
    end
    assert(type(options.issued_count) == 'number' and options.issued_count % 1 == 0
        and options.issued_count >= 1 and options.issued_count <= 200000,
        'issued_count is outside the supported range')
    assert(type(options.timeout) == 'number' and options.timeout > 0
        and options.timeout <= 300 and options.timeout == options.timeout,
        'timeout is outside the supported range')
    if not first_capture then
        assert(bits(options.expected_binding_bits, options.issued_count), 'invalid expected binding bits')
        assert(bits(options.expected_operational_bits, options.issued_count), 'invalid expected operational bits')
        for index = 1, options.issued_count do
            assert(options.expected_operational_bits:sub(index, index) ~= '1'
                or options.expected_binding_bits:sub(index, index) == '1',
                'expected operational bits exceed binding bits')
        end
    end
    for _, name in ipairs({'clock', 'guard', 'issue', 'store_marker', 'report', 'fault'}) do
        assert(type(options[name]) == 'function', name .. ' callback is required')
    end
    local Snapshot = options.snapshot_owner or X4LAN_AUTHORITY_REGISTRY_AVAILABILITY_SNAPSHOT
    assert(type(Snapshot) == 'table' and type(Snapshot.new) == 'function',
        'availability snapshot owner is unavailable')

    local cfg = {
        epoch = options.epoch, nonce = options.nonce, initial_run_id = options.initial_run_id,
        source_marker = options.source_marker, first_capture = first_capture,
        issued_count = options.issued_count, expected_binding_bits = options.expected_binding_bits,
        expected_operational_bits = options.expected_operational_bits,
        timeout = options.timeout,
        clock = options.clock, guard = options.guard, issue = options.issue,
        store_marker = options.store_marker, report = options.report, fault = options.fault,
        snapshot_owner = Snapshot,
    }
    local api = {}
    local active, active_mode, pending_marker, active_deadline
    local ready, started, stopped, busy = false, false, false, false
    local last_time
    local used_markers = {[cfg.epoch] = true, [cfg.nonce] = true}
    used_markers[cfg.initial_run_id or cfg.source_marker] = true
    local current_run_id = cfg.initial_run_id
    local first_save_confirmed = not first_capture
    local last_evidence

    local function fail(reason)
        if stopped then return end
        stopped, ready, active, pending_marker = true, false, nil, nil
        active_deadline = nil
        pcall(cfg.fault, tostring(reason))
    end
    local function protected(fn, ...)
        local ok, result = pcall(fn, ...)
        if not ok then error(result) end
        if stopped then error('gate stopped during callback') end
        if active_deadline then
            local clock_ok, now = pcall(cfg.clock)
            if not clock_ok or stopped or type(now) ~= 'number' or now ~= now
                or now == math.huge or now == -math.huge
                or (last_time and now < last_time) or now >= active_deadline then
                error('registry checkpoint operation timed out or clock invalid')
            end
            last_time = now
        end
        return result
    end
    local function checked_clock()
        local ok, now = pcall(cfg.clock)
        if not ok or type(now) ~= 'number' or now ~= now
            or now == math.huge or now == -math.huge or (last_time and now < last_time) then
            error('registry checkpoint clock invalid or moved backwards')
        end
        last_time = now
        if stopped or (active_deadline and now >= active_deadline) then
            error('registry checkpoint operation timed out or gate stopped')
        end
        return now
    end
    local function guard()
        return protected(cfg.guard) == true
    end
    local function make_owner(mode, run_id, previous_run_id)
        local owner = cfg.snapshot_owner.new({
            enabled = true, mode = mode, epoch = cfg.epoch, run_id = run_id,
            previous_run_id = previous_run_id, nonce = cfg.nonce,
            issued_count = cfg.issued_count, batch_size = 64,
            timeout = 30, clock = checked_clock, guard = guard,
            issue = function(action, payload) return protected(cfg.issue, action, payload) end,
            complete = function(evidence)
                if stopped or not active then error('snapshot completed outside an active gate operation') end
                if evidence.epoch ~= cfg.epoch or evidence.run_id ~= run_id
                    or evidence.issued_count ~= cfg.issued_count then error('snapshot correlation mismatch') end
                if mode == 'inspect' then
                    check_inspected_bits('saved binding', cfg.expected_binding_bits, evidence.saved_bits)
                    check_inspected_bits('live binding', cfg.expected_binding_bits, evidence.fresh_bits)
                    check_inspected_bits('saved operational', cfg.expected_operational_bits, evidence.saved_operational_bits)
                    check_inspected_bits('live operational', cfg.expected_operational_bits, evidence.fresh_operational_bits)
                    if not guard() then error('pause/game-time guard failed before initial readiness') end
                    last_evidence = copy(evidence)
                    protected(cfg.report, copy(evidence))
                    if stopped or not guard() then error('initial inspect guard failed after report') end
                    ready, active, active_mode = true, nil, nil
                    active_deadline = nil
                else
                    local previous = cfg.expected_binding_bits
                    local current = evidence.availability_bits
                    if previous then
                        for index = 1, cfg.issued_count do
                            if previous:sub(index, index) == '0' and current:sub(index, index) == '1' then
                                error('unavailable registry ID revived')
                            end
                        end
                    end
                    if not guard() then error('pause/game-time guard failed before marker store') end
                    if cfg.first_capture and not current_run_id then
                        evidence.mode = 'first_capture'
                        evidence.source_marker = cfg.source_marker
                    else
                        evidence.previous_run_id = current_run_id
                    end
                    last_evidence = copy(evidence)
                    protected(cfg.report, copy(evidence))
                    if stopped or not guard() then error('pause/game-time guard failed after report') end
                    local marker = pending_marker
                    if marker ~= run_id then error('capture marker changed') end
                    if protected(cfg.store_marker, marker) == false then error('marker store refused') end
                    if stopped then error('gate stopped during marker store') end
                    current_run_id = run_id
                    cfg.expected_binding_bits = current
                    pending_marker, active, active_mode = nil, nil, nil
                    active_deadline = nil
                    ready = true
                end
            end,
            fault = function(reason) fail('snapshot owner: ' .. tostring(reason)) end,
        })
        active, active_mode = owner, mode
        local now = checked_clock()
        active_deadline = now + cfg.timeout
        assert(owner:start())
        return owner
    end

    function api:start()
        if cfg.first_capture then fail('first capture requires explicit start'); return false end
        if busy or stopped or started then fail('start repeated or reentrant'); return false end
        busy, started = true, true
        local ok = pcall(function() make_owner('inspect', cfg.initial_run_id) end)
        busy = false
        if not ok then fail('initial inspect could not start'); return false end
        return true
    end

    function api:start_first_capture(marker)
        if not cfg.first_capture or busy or stopped or started then
            fail('first capture start refused'); return false
        end
        if not token(marker) or used_markers[marker] then
            fail('invalid or reserved first marker'); return false
        end
        busy, started = true, true
        pending_marker, used_markers[marker] = marker, true
        local ok = pcall(function() make_owner('capture', marker, nil) end)
        busy = false
        if not ok then fail('first capture could not start'); return false end
        return true
    end

    function api:confirm_first_save(marker, verified)
        if not cfg.first_capture or busy or stopped or not ready or active
            or first_save_confirmed or marker ~= current_run_id or verified ~= true then
            fail('first save verification refused'); return false
        end
        local ok, valid = pcall(guard)
        if not ok or valid ~= true then
            fail('pause/game-time guard failed before first save confirmation'); return false
        end
        first_save_confirmed = true
        return true
    end

    function api:prepare(marker)
        if busy or stopped or not ready or active or pending_marker or not first_save_confirmed then
            fail('prepare while gate is not ready'); return false, 'not_ready'
        end
        if not token(marker) or used_markers[marker]
            or marker == cfg.epoch or marker == cfg.nonce then
            fail('invalid or replayed checkpoint marker'); return false, 'marker'
        end
        busy, ready = true, false
        pending_marker = marker
        used_markers[marker] = true
        local ok = pcall(function() make_owner('capture', marker, current_run_id) end)
        busy = false
        if not ok then fail('capture could not start'); return false, 'capture' end
        return true
    end

    function api:event(response)
        if stopped or not active then fail('unexpected registry snapshot event'); return false end
        local accepted = active:event(response)
        if accepted ~= true then fail('snapshot owner refused event'); return false end
        return true
    end

    function api:tick()
        if busy then fail('reentrant gate tick'); return false end
        if stopped then return false, 'faulted' end
        if not started then fail('gate has not started'); return false end
        -- Ready-state ticks intentionally do not require pause; active snapshot
        -- ticks delegate to the owner's pause/game-time guard.
        if not active then return ready end
        busy = true
        local ok, result = pcall(function()
            checked_clock()
            return active:tick()
        end)
        busy = false
        if not ok or result ~= true then
            if not stopped then fail('snapshot owner tick failed') end
            return false, 'faulted'
        end
        return true
    end

    function api:ready()
        return ready and not stopped
    end

    function api:admission_ready()
        return ready and not stopped and first_save_confirmed
    end

    function api:state()
        return {ready = ready, admission_ready = ready and first_save_confirmed,
            first_save_confirmed = first_save_confirmed,
            source_marker = cfg.source_marker, started = started, stopped = stopped,
            active_mode = active_mode, current_run_id = current_run_id,
            pending_marker = pending_marker, evidence = copy(last_evidence),
            snapshot = active and active:state() or nil}
    end
    return api
end

X4LAN_AUTHORITY_REGISTRY_CHECKPOINT_GATE = M
return M
