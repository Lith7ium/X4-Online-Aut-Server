-- Copied-save trial only. No production overlay loads this module.
local M = {}
local SCREEN = 'X4AuthorityCompanyNativeVelocity'
local LOSS_SECONDS, READ_INTERVAL, RECEIPT_TIMEOUT = 5, .05, 1
local EXPECTED = {baseline = 'baseline', apply = 'dispatched', stop = 'released', zero = 'zeroed',
    readback = 'readback', release_readback = 'release_readback'}

local function finite(v)
    return type(v) == 'number' and v == v and math.abs(v) < math.huge
end

local function vec(text)
    local sx, sy, sz = (text or ''):match('^([^,]+),([^,]+),([^,]+)$')
    local x, y, z = tonumber(sx), tonumber(sy), tonumber(sz)
    if finite(x) and finite(y) and finite(z) then return {x = x, y = y, z = z} end
end

local function distance(a, b)
    return math.sqrt((a.x - b.x)^2 + (a.y - b.y)^2 + (a.z - b.z)^2)
end

local function dot(a, b)
    return a.x * b.x + a.y * b.y + a.z * b.z
end

function M.new(cfg)
    assert(cfg.enabled == true and cfg.observer and cfg.pinned and cfg.own_index)
    assert(type(cfg.clock) == 'function' and type(cfg.game) == 'function' and type(cfg.paused) == 'function'
        and type(cfg.guard) == 'function' and type(cfg.record) == 'function')
    local adapter, active, pending, latest, previous_read = {}, nil, nil, nil, nil
    local request, sent_sequence, next_read, release = 0, nil, 0, nil
    local baseline, may_hold, stopped, released_at = false, false, nil, nil
    local observer, clock, game = cfg.observer, cfg.clock, cfg.game
    local issue = cfg.issue or function(args) return AddUITriggeredEvent(SCREEN, 'Request', args) ~= false end

    local function eligible()
        local b = active
        return cfg.guard() == true and cfg.paused() == false and observer:is_active_pilot(b.index) == true
            and observer:matches(b.epoch, b.sector_macro, b.index, b.actor, b.code) == true
    end

    local function dispatch(op, sample)
        request = request + 1
        local b = active
        local args = {op = op, request = request, epoch = b.epoch, index = b.index,
            own = cfg.own_index, count = cfg.pinned.issued_count, actor = b.actor,
            sector = b.sector_macro, code = b.code}
        if op == 'baseline' or op == 'apply' or op == 'readback' then args.pilot = b.index end
        if op == 'apply' then
            args.vx, args.vy, args.vz = sample.command.x, sample.command.y, sample.command.z
            may_hold = true -- Dispatch without a receipt can still change the engine.
        end
        pending = {op = op, time = clock(), sample = sample, id = request}
        if not issue(args) then
            pending = nil
            cfg.record({kind = 'refused', operation = op, reason = 'issue_failed'})
            stopped = stopped or 'issue_failed'
            if op == 'stop' or op == 'zero' then release = 'uncertain' end
            return false
        end
        return true
    end

    local function result(_, text)
        if not pending or type(text) ~= 'string' then return end
        local fields = {}
        for field in text:gmatch('[^|]+') do fields[#fields + 1] = field end
        if tonumber(fields[1]) ~= pending.id then return end
        local p = pending
        pending = nil
        local kind = fields[2]
        local xyz, native = vec(fields[7]), vec(fields[8])
        local right, up, forward = vec(fields[9]), vec(fields[10]), vec(fields[11])
        local sent_sector = kind == 'dispatched' and vec(fields[12]) or nil
        local basis_valid = right and up and forward and math.abs(dot(right, right) - 1) <= .001
            and math.abs(dot(up, up) - 1) <= .001 and math.abs(dot(forward, forward) - 1) <= .001
            and math.abs(dot(right, up)) <= .001 and math.abs(dot(up, forward)) <= .001
            and math.abs(dot(forward, right)) <= .001
        local age = tonumber(fields[6])
        if kind ~= EXPECTED[p.op] or not xyz or not native or not basis_valid or not finite(age)
                or fields[3] ~= active.actor or (p.op == 'apply' and not sent_sector) then
            cfg.record({kind = 'refused', operation = p.op, reason = 'md_refused_or_invalid_receipt'})
            stopped = stopped or 'md_refused_or_invalid_receipt'
            if p.op == 'stop' or p.op == 'zero' or p.op == 'release_readback' then release = 'uncertain' end
            return
        end
        local sector_velocity = {}
        for _, axis in ipairs({'x', 'y', 'z'}) do
            sector_velocity[axis] = native.x * right[axis] + native.y * up[axis] + native.z * forward[axis]
        end
        local row = {kind = kind, sequence = p.sample and p.sample.sequence, actor = fields[3], sector = fields[4],
            zone = fields[5], game_time = age, real_time = clock(), position = xyz, local_velocity = native,
            sector_velocity = sector_velocity, dispatched_sector_velocity = sent_sector, reason = stopped,
            sector_basis = {right = right, up = up, forward = forward}}
        if p.sample then
            row.requested_sector_velocity = p.sample.command
            row.source_clock_ratio, row.receiver_clock_ratio = p.sample.source_ratio, p.sample.receiver_ratio
            if p.sample.command then row.velocity_error_mps = distance(sector_velocity, p.sample.command) end
        end
        if kind == 'readback' and previous_read and previous_read.zone == row.zone then
            local dt = age - previous_read.game_time
            if dt > 0 then
                local predicted = {}
                for _, axis in ipairs({'x', 'y', 'z'}) do
                    predicted[axis] = previous_read.position[axis] + previous_read.sector_velocity[axis] * dt
                end
                row.game_dt = dt
                row.displacement_m = distance(xyz, previous_read.position)
                row.exceeds_position_readback_tolerance = row.displacement_m > 3
                row.physical_drift_error_m = distance(xyz, predicted)
            end
        end
        if kind == 'zeroed' then release, released_at = 'zeroed', row.real_time end
        if kind == 'zeroed' or kind == 'released' then
            local before, velocity = vec(fields[12]), vec(fields[13])
            row.before_position = before
            row.before_local_velocity = velocity
            if velocity then
                row.before_sector_velocity = {}
                for _,axis in ipairs({'x','y','z'}) do
                    row.before_sector_velocity[axis] =
                        velocity.x*right[axis]+velocity.y*up[axis]+velocity.z*forward[axis]
                end
            end
        end
        if kind == 'baseline' then baseline = true end
        if kind == 'released' then release, may_hold, released_at = 'dispatched', false, row.real_time end
        if kind == 'release_readback' then
            release = 'readback'
            row.native_speed_mps = math.sqrt(dot(sector_velocity, sector_velocity))
            row.observed_near_zero = row.native_speed_mps <= .05
        end
        previous_read = row
        cfg.record(row)
    end

    local register = cfg.register or function(callback) RegisterEvent(SCREEN .. '.Result', callback) end
    register(result)

    function adapter:stop(reason)
        stopped = stopped or reason or 'explicit_loss'
        return self:tick()
    end

    function adapter:sample(binding, sample)
        if stopped or type(binding) ~= 'table' or type(sample) ~= 'table'
                or binding.epoch ~= cfg.pinned.epoch or binding.slot ~= 'cockpit'
                or type(binding.index) ~= 'number' or binding.index % 1 ~= 0 or binding.index < 1
                or binding.index > cfg.pinned.issued_count or binding.index == cfg.own_index
                or type(binding.actor) ~= 'string' or type(binding.code) ~= 'string'
                or type(binding.generation) ~= 'string' or sample.version ~= 3 or sample.index ~= binding.index
                or sample.epoch ~= binding.epoch or sample.sector_macro ~= binding.sector_macro
                or sample.generation ~= binding.generation or not finite(sample.sequence)
                or not finite(sample.source_time) or not finite(sample.capture_time) then return false end
        if active then
            for _, key in ipairs({'index', 'epoch', 'slot', 'serial', 'actor', 'sector_macro', 'code', 'generation'}) do
                if binding[key] ~= active[key] then self:stop('binding_changed'); return false end
            end
        else
            if sample.real_velocity ~= nil then return false end
            active = {}
            for key, value in pairs(binding) do active[key] = value end
        end
        if not eligible() then self:stop('view_or_roster_lost'); return false end
        local now, sim = clock(), game()
        if not finite(now) or not finite(sim) then return false end
        local item = {sequence = sample.sequence, capture = sample.capture_time, source = sample.source_time,
            time = now, game = sim}
        if latest then
            local dt, real = item.capture - latest.capture, now - latest.time
            if sample.sequence <= latest.sequence or dt <= 0 or real <= 0 then return false end
            local rate = sample.real_velocity
            if type(rate) == 'table' and finite(rate.x) and finite(rate.y) and finite(rate.z)
                    and dot(rate, rate) <= 2500^2 then
                item.source_ratio, item.receiver_ratio = (item.source - latest.source) / dt, (sim - latest.game) / real
                if item.source_ratio < .9 or item.source_ratio > 1.1
                        or item.receiver_ratio < .9 or item.receiver_ratio > 1.1 then
                    cfg.record({kind = 'refused', reason = 'clock_ratio_out_of_range', sequence = item.sequence,
                        source_clock_ratio = item.source_ratio, receiver_clock_ratio = item.receiver_ratio})
                    self:stop('clock_ratio_out_of_range')
                    return false
                end
                item.command = {x = rate.x / item.receiver_ratio, y = rate.y / item.receiver_ratio,
                    z = rate.z / item.receiver_ratio}
            else
                self:stop('missing_or_invalid_rate')
                return false
            end
        end
        latest = item
        return self:tick()
    end

    function adapter:tick()
        if not active or not latest then return false end
        local now = clock()
        if not stopped then
            if cfg.paused() ~= false then stopped = 'paused'
            elseif not eligible() then stopped = 'view_or_roster_lost'
            elseif now - latest.time >= LOSS_SECONDS then stopped = 'stale_5s' end
        end
        if pending and now - pending.time < RECEIPT_TIMEOUT then return true end
        if pending then
            cfg.record({kind = 'refused', operation = pending.op, reason = 'receipt_timeout'})
            if pending.op == 'stop' or pending.op == 'zero' or pending.op == 'release_readback' then release = 'uncertain' end
            pending, stopped = nil, stopped or 'receipt_timeout'
        end
        if stopped then
            if release == 'dispatched' then
                if now - released_at < READ_INTERVAL then return true end
                return dispatch('release_readback')
            end
            if release=='zeroed' and now-released_at>=READ_INTERVAL then return dispatch('stop') end
            if may_hold and not release then
                if stopped == 'stale_5s' and cfg.anchor and cfg.anchor() then return dispatch('zero') end
                return dispatch('stop')
            end
            return false
        end
        if not baseline then return dispatch('baseline', latest) end
        if now < next_read then return true end
        next_read = now + READ_INTERVAL
        if latest.command and sent_sequence ~= latest.sequence then
            sent_sequence = latest.sequence
            return dispatch('apply', latest)
        end
        if may_hold then return dispatch('readback', latest) end
        return true
    end
    return adapter
end

X4AuthorityCompanyNativeVelocity = M
return M
