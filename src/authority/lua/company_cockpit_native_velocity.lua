-- Opt-in, default-off native linear velocity companion for one observed Company ship.
-- Keeps wire protocol and shared server unchanged; accepts V3 and V4 samples, enforces
-- the 5-second monotonic packet-gap cutoff, anchors current pose before zero/release on
-- every stop path (preventing X4's release snap-back), and provides stock HUD target-speed reads.
local M = {}
local SCREEN = 'X4AuthorityCompanyCockpitVelocity'
local MAX_LOSS_SECONDS, READ_INTERVAL, RECEIPT_TIMEOUT = 5.0, 0.05, 1.0
local SPEED_OF_LIGHT = 299792458
local SPEED_CHANGE_THRESHOLD = 0.5
local TRANSITION_TOLERANCE_M = 0.25
local EXPECTED = {
    baseline = 'baseline',
    apply = 'dispatched',
    stop = 'released',
    zero = 'zeroed',
    readback = 'readback',
    release_readback = 'release_readback',
}
local COCKPIT_SLOTS = {cockpit = true, cockpit2 = true, cockpit3 = true}

local function finite(v)
    return type(v) == 'number' and v == v and math.abs(v) < math.huge
end

local function vec(text)
    local sx, sy, sz = (text or ''):match('^([^,]+),([^,]+),([^,]+)$')
    local x, y, z = tonumber(sx), tonumber(sy), tonumber(sz)
    if finite(x) and finite(y) and finite(z) then return {x = x, y = y, z = z} end
end

local function dot(a, b)
    return a.x * b.x + a.y * b.y + a.z * b.z
end

local function norm(v)
    return math.sqrt(dot(v, v))
end

local function distance(a, b)
    local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local function point_to_segment_distance(p, a, b)
    local abx, aby, abz = b.x - a.x, b.y - a.y, b.z - a.z
    local ab2 = abx * abx + aby * aby + abz * abz
    if ab2 <= 1e-12 then return distance(p, a) end
    local apx, apy, apz = p.x - a.x, p.y - a.y, p.z - a.z
    local t = (apx * abx + apy * aby + apz * abz) / ab2
    if t < 0 then t = 0 elseif t > 1 then t = 1 end
    local cx, cy, cz = a.x + t * abx, a.y + t * aby, a.z + t * abz
    local dx, dy, dz = p.x - cx, p.y - cy, p.z - cz
    return math.sqrt(dx * dx + dy * dy + dz * dz), t
end

-- Exact formatting from X4 9.00 ui/core/lua/targetsystem.lua:updateSideText2 (lines 5780-5792).
function M.format_hud_speed(speed)
    if not finite(speed) then return nil end
    local s = speed >= 0 and math.floor(speed) or math.ceil(speed)
    if math.abs(s) >= 0.01 * SPEED_OF_LIGHT then
        local c = math.floor(s * 100 / SPEED_OF_LIGHT + 0.5) / 100
        return tostring(c) .. 'c'
    elseif math.abs(s) >= 10000 then
        local km
        if math.abs(s) >= 100000 then
            km = math.floor(s / 1000 + 0.5)
        else
            km = math.floor(s / 100 + 0.5) / 10
        end
        return tostring(km) .. ' km/s'
    else
        return tostring(s) .. ' m/s'
    end
end

function M.new(cfg)
    if type(cfg) ~= 'table' or cfg.enabled ~= true then return nil, 'disabled' end
    assert(cfg.observer and cfg.pinned and cfg.own_index, 'observer, pinned, and own_index required')
    assert(type(cfg.clock) == 'function' and type(cfg.game) == 'function'
        and type(cfg.paused) == 'function' and type(cfg.guard) == 'function'
        and type(cfg.record) == 'function', 'callbacks required')
    local loss_seconds = cfg.loss_seconds or MAX_LOSS_SECONDS
    assert(finite(loss_seconds) and loss_seconds > 0 and loss_seconds <= MAX_LOSS_SECONDS, 'invalid loss_seconds')
    local constantvel = cfg.constantvel ~= false
    local observer, clock, game = cfg.observer, cfg.clock, cfg.game
    local issue = cfg.issue or function(args) return AddUITriggeredEvent(SCREEN, 'Request', args) ~= false end

    local adapter = {}
    local active, pending, latest, previous_read = nil, nil, nil, nil
    local request, sent_sequence, next_read, release = 0, nil, 0, nil
    local baseline, may_hold, stopped, released_at = false, false, nil, nil
    local stop_anchor_pos = nil

    local function eligible()
        local b = active
        if not b then return false end
        return cfg.guard() == true and cfg.paused() == false
            and (not observer.is_active_pilot or observer:is_active_pilot(b.index) == true)
            and observer:matches(b.epoch, b.sector_macro, b.index, b.actor, b.code) == true
    end

    local function dispatch(op, sample)
        request = request + 1
        local b = active
        local args = {
            op = op, request = request, epoch = b.epoch, index = b.index,
            own = cfg.own_index, count = cfg.pinned.issued_count,
            actor = b.actor, sector = b.sector_macro, code = b.code,
        }
        if op == 'baseline' or op == 'apply' or op == 'readback' then
            args.pilot = b.index
        end
        if op == 'apply' then
            args.vx, args.vy, args.vz = sample.command.x, sample.command.y, sample.command.z
            args.constantvel = sample.constantvel
            may_hold = true
        end
        pending = {op = op, time = clock(), sample = sample, id = request}
        if not issue(args) then
            pending = nil
            cfg.record({kind = 'refused', operation = op, reason = 'issue_failed'})
            stopped = stopped or 'issue_failed'
            if op == 'stop' or op == 'zero' or op == 'release_readback' then release = 'uncertain' end
            return false
        end
        return true
    end

    local function result(_, text)
        if not pending or type(text) ~= 'string' then return end
        local fields = {}
        for field in (text .. '|'):gmatch('([^|]*)|') do fields[#fields + 1] = field end
        if tonumber(fields[1]) ~= pending.id then return end
        local p = pending
        pending = nil
        local kind = fields[2]
        local xyz, native = vec(fields[7]), vec(fields[8])
        local right, up, forward = vec(fields[9]), vec(fields[10]), vec(fields[11])
        local dist_m = tonumber(fields[12])
        local sent_sector = kind == 'dispatched' and vec(fields[13]) or nil
        local cvel_flag = kind == 'dispatched' and (fields[14] ~= 'false' and fields[14] ~= '0') or nil
        local basis_valid = right and up and forward
            and math.abs(dot(right, right) - 1) <= 0.001
            and math.abs(dot(up, up) - 1) <= 0.001
            and math.abs(dot(forward, forward) - 1) <= 0.001
            and math.abs(dot(right, up)) <= 0.001
            and math.abs(dot(up, forward)) <= 0.001
            and math.abs(dot(forward, right)) <= 0.001
        local age = tonumber(fields[6])
        if kind ~= EXPECTED[p.op] or not xyz or not native or not basis_valid or not finite(age)
                or not active or fields[3] ~= active.actor or (p.op == 'apply' and not sent_sector) then
            cfg.record({kind = 'refused', operation = p.op, reason = 'md_refused_or_invalid_receipt', raw = text})
            stopped = stopped or 'md_refused_or_invalid_receipt'
            if p.op == 'stop' or p.op == 'zero' or p.op == 'release_readback' then release = 'uncertain' end
            return
        end
        local sector_velocity = {}
        for _, axis in ipairs({'x', 'y', 'z'}) do
            sector_velocity[axis] = native.x * right[axis] + native.y * up[axis] + native.z * forward[axis]
        end
        local fwd_speed = dot(sector_velocity, forward)
        local lat_speed = math.sqrt(native.x * native.x + native.y * native.y)
        local tot_speed = norm(sector_velocity)
        local row = {
            kind = kind,
            sequence = p.sample and p.sample.sequence,
            actor = fields[3],
            sector = fields[4],
            zone = fields[5],
            game_time = age,
            real_time = clock(),
            position = xyz,
            local_velocity = native,
            sector_velocity = sector_velocity,
            forward_speed_mps = fwd_speed,
            lateral_speed_mps = lat_speed,
            total_speed_mps = tot_speed,
            distance_to_player_m = dist_m,
            dispatched_sector_velocity = sent_sector,
            dispatched_constantvel = cvel_flag,
            reason = stopped,
            sector_basis = {right = right, up = up, forward = forward},
        }
        if p.sample then
            row.requested_sector_velocity = p.sample.command
            row.source_clock_ratio = p.sample.source_ratio
            row.receiver_clock_ratio = p.sample.receiver_ratio
            if p.sample.command then row.velocity_error_mps = distance(sector_velocity, p.sample.command) end
        end
        if (kind == 'readback' or kind == 'dispatched') and previous_read and previous_read.zone == row.zone then
            local dt = age - previous_read.game_time
            if dt > 0 then
                local pred_old = {
                    x = previous_read.position.x + previous_read.sector_velocity.x * dt,
                    y = previous_read.position.y + previous_read.sector_velocity.y * dt,
                    z = previous_read.position.z + previous_read.sector_velocity.z * dt,
                }
                row.game_dt = dt
                row.displacement_m = distance(xyz, previous_read.position)
                row.physical_drift_error_m = distance(xyz, pred_old)
                if distance(previous_read.sector_velocity, sector_velocity) > SPEED_CHANGE_THRESHOLD then
                    local pred_new = {
                        x = previous_read.position.x + sector_velocity.x * dt,
                        y = previous_read.position.y + sector_velocity.y * dt,
                        z = previous_read.position.z + sector_velocity.z * dt,
                    }
                    local seg_err, alpha = point_to_segment_distance(xyz, pred_old, pred_new)
                    row.speed_transition = true
                    row.transition_segment_error_m = seg_err
                    row.transition_alpha = alpha
                    row.transition_bounded = seg_err <= TRANSITION_TOLERANCE_M
                end
            end
        end
        if kind == 'zeroed' then
            release, released_at = 'zeroed', row.real_time
        end
        if kind == 'zeroed' or kind == 'released' then
            local before = vec(fields[13])
            local velocity = vec(fields[14])
            row.before_position = before
            row.before_local_velocity = velocity
            if before then
                row.snapback_m = distance(xyz, before)
            end
            if velocity then
                row.before_sector_velocity = {}
                for _, axis in ipairs({'x', 'y', 'z'}) do
                    row.before_sector_velocity[axis] =
                        velocity.x * right[axis] + velocity.y * up[axis] + velocity.z * forward[axis]
                end
            end
        end
        if kind == 'baseline' then baseline = true end
        if kind == 'released' then
            release, may_hold, released_at = 'dispatched', false, row.real_time
        end
        if kind == 'release_readback' then
            release = 'readback'
            row.native_speed_mps = tot_speed
            row.observed_near_zero = tot_speed <= 0.05
            if stop_anchor_pos then
                row.anchor_drift_m = distance(xyz, stop_anchor_pos)
            end
        end
        previous_read = row
        cfg.record(row)
    end

    local register = cfg.register or function(cb) return RegisterEvent(SCREEN .. '.Result', cb) end
    register(result)

    function adapter:state()
        return {
            active = active ~= nil,
            baseline = baseline,
            may_hold = may_hold,
            stopped = stopped,
            release = release,
            pending_op = pending and pending.op or nil,
            sent_sequence = sent_sequence,
        }
    end

    function adapter:stop(reason)
        stopped = stopped or reason or 'explicit_loss'
        return self:tick()
    end

    function adapter:sample(binding, sample)
        if stopped or type(binding) ~= 'table' or type(sample) ~= 'table'
                or binding.epoch ~= cfg.pinned.epoch or not COCKPIT_SLOTS[binding.slot]
                or type(binding.index) ~= 'number' or binding.index % 1 ~= 0 or binding.index < 1
                or binding.index > cfg.pinned.issued_count or binding.index == cfg.own_index
                or type(binding.actor) ~= 'string' or type(binding.code) ~= 'string'
                or type(binding.generation) ~= 'string'
                or (sample.version ~= 3 and sample.version ~= 4)
                or sample.index ~= binding.index or sample.epoch ~= binding.epoch
                or sample.sector_macro ~= binding.sector_macro or sample.generation ~= binding.generation
                or not finite(sample.sequence) or not finite(sample.source_time)
                or not finite(sample.capture_time) then
            return false
        end
        if active then
            for _, key in ipairs({'index', 'epoch', 'slot', 'serial', 'actor', 'sector_macro', 'code', 'generation'}) do
                if binding[key] ~= active[key] then
                    self:stop('binding_changed')
                    return false
                end
            end
        else
            active = {}
            for key, value in pairs(binding) do active[key] = value end
        end
        if not eligible() then
            self:stop('view_or_roster_lost')
            return false
        end
        local now, sim = clock(), game()
        if not finite(now) or not finite(sim) then return false end
        local item = {
            sequence = sample.sequence,
            capture = sample.capture_time,
            source = sample.source_time,
            time = now,
            game = sim,
            constantvel = sample.constantvel ~= nil and sample.constantvel or constantvel,
        }
        local rate = sample.real_velocity
        if latest then
            local dt, real = item.capture - latest.capture, now - latest.time
            if sample.sequence <= latest.sequence or dt <= 0 or real <= 0 then return false end
            if type(rate) == 'table' and finite(rate.x) and finite(rate.y) and finite(rate.z)
                    and dot(rate, rate) <= 2500 ^ 2 then
                item.source_ratio = (item.source - latest.source) / dt
                item.receiver_ratio = (sim - latest.game) / real
                if item.source_ratio < 0.9 or item.source_ratio > 1.1
                        or item.receiver_ratio < 0.9 or item.receiver_ratio > 1.1 then
                    cfg.record({
                        kind = 'refused',
                        reason = 'clock_ratio_out_of_range',
                        sequence = item.sequence,
                        source_clock_ratio = item.source_ratio,
                        receiver_clock_ratio = item.receiver_ratio,
                    })
                    self:stop('clock_ratio_out_of_range')
                    return false
                end
                item.command = {
                    x = rate.x / item.receiver_ratio,
                    y = rate.y / item.receiver_ratio,
                    z = rate.z / item.receiver_ratio,
                }
            else
                self:stop('missing_or_invalid_rate')
                return false
            end
        elseif rate ~= nil then
            if type(rate) == 'table' and finite(rate.x) and finite(rate.y) and finite(rate.z)
                    and dot(rate, rate) <= 2500 ^ 2 then
                item.source_ratio = 1.0
                item.receiver_ratio = 1.0
                item.command = {x = rate.x, y = rate.y, z = rate.z}
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
            if cfg.paused() ~= false then
                stopped = 'paused'
            elseif not eligible() then
                stopped = 'view_or_roster_lost'
            elseif now - latest.time >= loss_seconds then
                stopped = 'stale_5s'
            end
        end
        if pending and now - pending.time < RECEIPT_TIMEOUT then return true end
        if pending then
            cfg.record({kind = 'refused', operation = pending.op, reason = 'receipt_timeout'})
            if pending.op == 'stop' or pending.op == 'zero' or pending.op == 'release_readback' then
                release = 'uncertain'
            end
            pending, stopped = nil, stopped or 'receipt_timeout'
        end
        if stopped then
            if release == 'dispatched' then
                if now - released_at < READ_INTERVAL then return true end
                return dispatch('release_readback')
            end
            if release == 'zeroed' then
                if now - released_at < READ_INTERVAL then return true end
                return dispatch('stop')
            end
            if may_hold and not release then
                -- Anchor current live pose before zero/release on EVERY stop reason
                -- so X4 never snaps back to the last explicit SetObjectSectorPos write.
                if type(cfg.anchor) == 'function' then
                    local ok_anchor, anchored_pose = pcall(cfg.anchor, active)
                    if ok_anchor and anchored_pose then
                        if type(anchored_pose) == 'table' and finite(anchored_pose.x)
                                and finite(anchored_pose.y) and finite(anchored_pose.z) then
                            stop_anchor_pos = {x = anchored_pose.x, y = anchored_pose.y, z = anchored_pose.z}
                        end
                        return dispatch('zero')
                    end
                end
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

-- Stock X4 9.00 HUD target-speed and softtarget inspection helper (read-only).
function M.read_hud_target_speed_x4(actor_u64, width, height)
    local ffi = require('ffi')
    local C = ffi.C
    local function declare(text) pcall(ffi.cdef, text) end
    declare('typedef uint64_t UniverseID;')
    declare('typedef struct { const char* name; float hull; float shield; int speed; bool hasShield; } ComponentDetails;')
    declare('typedef struct { UniverseID softtargetID; const char* softtargetConnectionName; uint32_t messageID; } SofttargetDetails2;')
    declare('typedef struct { float x; float y; bool onScreen; } ScreenPos;')
    declare('SofttargetDetails2 GetSofttarget2(void);')
    declare('ComponentDetails GetComponentDetails(const UniverseID componentid, const char* const connectionname);')
    declare('ScreenPos GetComponentScreenPosition(UniverseID componentid, uint32_t width, uint32_t height);')
    declare('bool IsHUDActive(void);')
    declare('bool IsExternalTargetMode(void);')
    declare('bool IsExternalViewActive(void);')

    local st = C.GetSofttarget2()
    local conn = ''
    if st.softtargetConnectionName ~= nil and st.softtargetConnectionName ~= ffi.NULL then
        conn = ffi.string(st.softtargetConnectionName)
    end
    local det_resolved = C.GetComponentDetails(actor_u64, conn)
    local det_empty = C.GetComponentDetails(actor_u64, '')
    local name_str = (det_resolved.name ~= nil and det_resolved.name ~= ffi.NULL) and ffi.string(det_resolved.name) or ''
    local scr = C.GetComponentScreenPosition(actor_u64, width or 800, height or 600)
    local ps_ok, p_actual, p_targeted, p_actual_s, p_boost, p_travel, p_match, p_tgt_spd, p_norm_tgt_spd =
        pcall(GetPlayerSpeed)
    local ver_ok, ver_str = pcall(GetVersionString)

    return {
        x4_version = ver_ok and tostring(ver_str) or 'unknown',
        is_hud_active = C.IsHUDActive() == true,
        is_external_target = C.IsExternalTargetMode() == true,
        is_external_view = C.IsExternalViewActive() == true,
        softtarget_id = tostring(st.softtargetID),
        softtarget_matches = st.softtargetID == actor_u64,
        connection_name = conn,
        message_id = tonumber(st.messageID) or 0,
        component_name = name_str,
        speed_resolved_conn = tonumber(det_resolved.speed),
        speed_empty_conn = tonumber(det_empty.speed),
        hull = tonumber(det_resolved.hull),
        shield = tonumber(det_resolved.shield),
        has_shield = det_resolved.hasShield == true,
        hud_formatted_resolved = M.format_hud_speed(tonumber(det_resolved.speed)),
        hud_formatted_empty = M.format_hud_speed(tonumber(det_empty.speed)),
        screen_on = scr.onScreen == true,
        screen_x = tonumber(scr.x),
        screen_y = tonumber(scr.y),
        player_speed_ok = ps_ok == true,
        player_actual_speed = ps_ok and tonumber(p_actual) or nil,
        player_actual_speed_mps = ps_ok and tonumber(p_actual_s) or nil,
        match_speed = ps_ok and (p_match == true) or nil,
        crosshair_target_speed = ps_ok and tonumber(p_tgt_spd) or nil,
        crosshair_normal_target_speed = ps_ok and tonumber(p_norm_tgt_spd) or nil,
    }
end

-- Safe pose-anchor helper for native X4: reads current live sector XYZ + orientation
-- and writes that exact pose back via SetObjectSectorPos before zeroing native velocity.
function M.make_x4_anchor(actor_u64)
    local ffi = require('ffi')
    local C = ffi.C
    local function declare(text) pcall(ffi.cdef, text) end
    declare('typedef uint64_t UniverseID;')
    declare('typedef struct { float x; float y; float z; float yaw; float pitch; float roll; } UIPosRot;')
    declare('bool IsValidComponent(UniverseID);')
    declare('bool IsComponentOperational(UniverseID);')
    declare('bool IsComponentClass(UniverseID, const char*);')
    declare('UniverseID GetPlayerControlledShipID(void);')
    declare('UniverseID GetContextByClass(UniverseID, const char*, bool);')
    declare('UIPosRot GetObjectPositionInSector(UniverseID);')
    declare('void SetObjectSectorPos(UniverseID, UniverseID, UIPosRot);')

    return function()
        local own = C.GetPlayerControlledShipID()
        if own == 0 or actor_u64 == 0 or actor_u64 == own then return false end
        if not C.IsValidComponent(actor_u64) or not C.IsComponentOperational(actor_u64)
                or not C.IsComponentClass(actor_u64, 'ship') then
            return false
        end
        local sector = C.GetContextByClass(actor_u64, 'sector', false)
        local own_sector = C.GetContextByClass(own, 'sector', false)
        if sector == 0 or sector ~= own_sector then return false end
        local p = C.GetObjectPositionInSector(actor_u64)
        local pose = {
            x = tonumber(p.x), y = tonumber(p.y), z = tonumber(p.z),
            yaw = tonumber(p.yaw), pitch = tonumber(p.pitch), roll = tonumber(p.roll),
        }
        if not (finite(pose.x) and finite(pose.y) and finite(pose.z)
                and finite(pose.yaw) and finite(pose.pitch) and finite(pose.roll)) then
            return false
        end
        C.SetObjectSectorPos(actor_u64, sector, ffi.new('UIPosRot', {
            x = pose.x, y = pose.y, z = pose.z,
            yaw = math.deg(pose.yaw), pitch = math.deg(pose.pitch), roll = math.deg(pose.roll),
        }))
        return pose
    end
end

X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY = M
return M
