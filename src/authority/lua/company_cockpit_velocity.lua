-- Opt-in, default-off one-shot (constantvel="false") native linear velocity bridge
-- for the receiving cockpit. Keeps wire protocol, shared server, and clock policy unchanged;
-- anchors the live pose before zeroing velocity on every stop/withdrawal/5s cutoff path.
local M = {}
local SCREEN = 'X4AuthorityCompanyCockpitVelocity'
local COCKPIT_SLOTS = {cockpit = true, cockpit2 = true, cockpit3 = true}
local MAX_SPEED = 2500
local CHANGE_EPS = 0.25
local MIN_INTERVAL = 0.05

local next_request_id = 0
local pending_by_id = {}
local shared_registered = false

local function finite(v)
    return type(v) == 'number' and v == v and math.abs(v) < math.huge
end

local function vec(text)
    local sx, sy, sz = (text or ''):match('^([^,]+),([^,]+),([^,]+)$')
    local x, y, z = tonumber(sx), tonumber(sy), tonumber(sz)
    if finite(x) and finite(y) and finite(z) then return {x = x, y = y, z = z} end
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
        and a.actor == b.actor and a.sector_macro == b.sector_macro and a.code == b.code
end

local function handle_result(_, text)
    if type(text) ~= 'string' then return end
    local id_text = text:match('^([^|]+)|')
    local id = tonumber(id_text)
    local entry = id and pending_by_id[id]
    if not entry then return end
    pending_by_id[id] = nil
    entry(text)
end

local function parse_receipt(text, expected_op, anchor_pose)
    local fields = {}
    for field in (text .. '|'):gmatch('([^|]*)|') do fields[#fields + 1] = field end
    local status = fields[2]
    local row = {request = tonumber(fields[1]), op = expected_op, status = status}
    if status == 'refused' or (expected_op == 'set' and status ~= 'set')
            or (expected_op == 'stop' and status ~= 'stopped') then
        row.ok = false
        return row
    end
    local xyz, native = vec(fields[7]), vec(fields[8])
    local right, up, forward = vec(fields[9]), vec(fields[10]), vec(fields[11])
    local age = tonumber(fields[6])
    if not (xyz and native and right and up and forward and finite(age)) then
        row.ok = false
        return row
    end
    local sec_vel = {
        x = native.x * right.x + native.y * up.x + native.z * forward.x,
        y = native.x * right.y + native.y * up.y + native.z * forward.y,
        z = native.x * right.z + native.y * up.z + native.z * forward.z,
    }
    row.ok = true
    row.actor = fields[3]
    row.sector = fields[4]
    row.zone = fields[5]
    row.game_time = age
    row.position = xyz
    row.local_velocity = native
    row.sector_velocity = sec_vel
    row.total_speed_mps = norm3(sec_vel)
    row.forward_speed_mps = sec_vel.x * forward.x + sec_vel.y * forward.y + sec_vel.z * forward.z
    if status == 'set' then
        row.dispatched_velocity = vec(fields[12])
    elseif status == 'stopped' then
        local before = vec(fields[12])
        local before_vel = vec(fields[13])
        row.before_position = before
        row.before_local_velocity = before_vel
        row.snapback_m = before and dist3(xyz, before) or nil
        row.observed_near_zero = row.total_speed_mps <= 0.05
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

    if options.register then
        assert(options.register(handle_result) ~= false, 'cockpit velocity event registration failed')
    elseif not shared_registered then
        assert(RegisterEvent(SCREEN .. '.Result', handle_result) ~= false, 'cockpit velocity event unavailable')
        shared_registered = true
    end

    local active_binding = nil
    local last_sent = nil
    local last_sent_at = nil
    local may_be_moving = false
    local stats = {
        slot = slot,
        sets = 0,
        stops = 0,
        receipts = 0,
        refusals = 0,
        last_snapback_m = nil,
        last_stop_speed_mps = nil,
    }

    local function dispatch(op, binding, velocity, anchor_pose)
        next_request_id = next_request_id + 1
        local id = next_request_id
        local args = {
            op = op,
            slot = slot,
            request = id,
            epoch = binding.epoch,
            index = binding.index,
            own = own_index,
            count = pinned.issued_count,
            actor = binding.actor,
            sector = binding.sector_macro,
            code = binding.code,
        }
        if op == 'set' then
            args.vx, args.vy, args.vz = velocity.x, velocity.y, velocity.z
        end
        pending_by_id[id] = function(text)
            local row = parse_receipt(text, op, anchor_pose)
            row.slot = slot
            row.index = binding.index
            row.code = binding.code
            if row.ok then
                stats.receipts = stats.receipts + 1
                if op == 'stop' then
                    stats.last_snapback_m = row.snapback_m
                    stats.last_stop_speed_mps = row.total_speed_mps
                end
            else
                stats.refusals = stats.refusals + 1
            end
            if type(options.on_event) == 'function' then
                pcall(options.on_event, row)
            end
        end
        local ok, dispatched = pcall(issue, args)
        if not ok or dispatched ~= true then
            pending_by_id[id] = nil
            stats.refusals = stats.refusals + 1
            return false
        end
        if op == 'set' then
            stats.sets = stats.sets + 1
        else
            stats.stops = stats.stops + 1
        end
        return true
    end

    local api = {}

    function api:apply(binding, velocity)
        if type(binding) ~= 'table' or type(velocity) ~= 'table'
                or binding.epoch ~= pinned.epoch or binding.index == own_index
                or not (finite(velocity.x) and finite(velocity.y) and finite(velocity.z))
                or norm3(velocity) > MAX_SPEED then
            return false
        end
        if active_binding and not same_binding(active_binding, binding) then
            self:stop()
        end
        local now = clock()
        if not finite(now) then return false end
        local is_zero = velocity.x == 0 and velocity.y == 0 and velocity.z == 0
        local was_nonzero = last_sent ~= nil and (last_sent.x ~= 0 or last_sent.y ~= 0 or last_sent.z ~= 0)
        if not may_be_moving and is_zero then
            return true
        end
        if last_sent and not (is_zero and was_nonzero) then
            if dist3(velocity, last_sent) <= CHANGE_EPS then
                return true
            end
            if last_sent_at and now - last_sent_at < MIN_INTERVAL then
                return true
            end
        end
        local bound = {
            epoch = binding.epoch, index = binding.index, actor = binding.actor,
            sector_macro = binding.sector_macro, code = binding.code,
        }
        if not dispatch('set', bound, velocity, nil) then
            return false
        end
        active_binding = bound
        last_sent = {x = velocity.x, y = velocity.y, z = velocity.z}
        last_sent_at = now
        may_be_moving = not is_zero
        return true
    end

    function api:stop(anchor_fn)
        local bound = active_binding
        local was_active = may_be_moving or (bound ~= nil and last_sent ~= nil)
        active_binding = nil
        last_sent = nil
        last_sent_at = nil
        may_be_moving = false
        if not was_active or not bound then
            return true
        end
        local anchored_pose = nil
        if type(anchor_fn) == 'function' then
            local ok_a, pose = pcall(anchor_fn)
            if ok_a and type(pose) == 'table' and finite(pose.x) and finite(pose.y) and finite(pose.z) then
                anchored_pose = {x = pose.x, y = pose.y, z = pose.z}
            end
        end
        return dispatch('stop', bound, nil, anchored_pose)
    end

    function api:stats()
        local out = {}
        for k, v in pairs(stats) do out[k] = v end
        out.may_be_moving = may_be_moving
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
            if not row.ok then
                DebugError(('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_REFUSED slot=%s op=%s status=%s index=%s'):format(
                    tostring(row.slot), tostring(row.op), tostring(row.status), tostring(row.index)))
                return
            end
            if row.op == 'stop' then
                DebugError(('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_STOP slot=%s index=%s ship=%s snapback=%.6f speed=%.6f anchor_drift=%s pos=%.6f,%.6f,%.6f'):format(
                    tostring(row.slot), tostring(row.index), tostring(row.code),
                    row.snapback_m or -1, row.total_speed_mps or -1,
                    row.anchor_drift_m and ('%.6f'):format(row.anchor_drift_m) or 'none',
                    row.position.x, row.position.y, row.position.z))
                return
            end
            set_count = set_count + 1
            if set_count <= 3 or set_count % 10 == 0 then
                DebugError(('[X4LAN] AUTH_COMPANY_COCKPIT_VELOCITY_SET slot=%s index=%s ship=%s n=%d fwd=%.3f tot=%.3f cmd=%.3f,%.3f,%.3f'):format(
                    tostring(row.slot), tostring(row.index), tostring(row.code), set_count,
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
