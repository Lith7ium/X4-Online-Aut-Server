-- Native Windows X4 9.00 target-speed investigation UI driver (disposable profile 45850 only).
-- Exercises:
--   * Stock HUD target lock via C.SetSofttarget + full HUD telemetry (GetSofttarget2,
--     GetComponentDetails with resolved connectionName AND empty "", GetPlayerSpeed, ScreenPos)
--   * Position-only control (SetObjectSectorPos without native velocity)
--   * Forward (+60 m/s, constantvel=true and constantvel=false), pure lateral (+60 m/s),
--     diagonal (+60 fwd / +60 lat), and reverse (-60 m/s) LAN native velocity
--   * Uncoupled heading turns (+12, +30, +90 deg) and snap-back control (release without anchor)
--   * Real unmodified loopback CompanyMotionService V4 stream (REMOTE4/SEEN4) driving coupled
--     turning + speed ramp (60 -> 90 m/s) + 5-second packet gap + safe anchor-zero-release stop
--     via opt-in X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY.
local cfg = X4NEON_TARGET_SPEED
if type(cfg) ~= 'table' or cfg.enabled ~= true then return end

local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
declare('typedef struct { float x; float y; float z; float yaw; float pitch; float roll; } UIPosRot;')
declare('typedef struct { float x; float y; bool onScreen; } ScreenPos;')
for _, text in ipairs({
    'bool IsStartmenu(void);',
    'bool IsPlayerValid(void);',
    'bool IsGamePaused(void);',
    'bool IsSaveListLoadingComplete(void);',
    'void ReloadSaveList(void);',
    'const char* GetSaveFolderPath(void);',
    'double GetCurrentGameTime(void);',
    'UniverseID GetPlayerControlledShipID(void);',
    'UniverseID GetPlayerOccupiedShipID(void);',
    'UniverseID GetPlayerContainerID(void);',
    'UniverseID GetContextByClass(UniverseID componentid, const char* classname, bool includeself);',
    'bool IsValidComponent(UniverseID componentid);',
    'bool IsComponentClass(UniverseID componentid, const char* classname);',
    'bool IsComponentOperational(UniverseID componentid);',
    'const char* GetObjectIDCode(UniverseID objectid);',
    'UIPosRot GetObjectPositionInSector(UniverseID objectid);',
    'void SetObjectSectorPos(UniverseID objectid, UniverseID sectorid, UIPosRot uioffset);',
    'bool SetSofttarget(UniverseID componentid, const char* const connectionname);',
    'float GetDistanceBetween(UniverseID componentid1, UniverseID componentid2);',
}) do declare(text) end

local EXT_DIR = './extensions/' .. tostring(cfg.extension_id or 'x4_neon_target_speed') .. '/'
local SAVE_SLOT = cfg.save_slot or 'save_009'
local resolve = assert(X4LAN_RESOLVE_STRING, 'X4LAN_RESOLVE_STRING required')
local native_vel_mod = assert(X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY, 'X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY required')

local function log(msg)
    DebugError('[X4LAN] AUTH_TARGET_SPEED ' .. msg)
end

local function split(text, sep)
    sep = sep or '|'
    local parts = {}
    for item in (text .. sep):gmatch('(.-)' .. sep) do parts[#parts + 1] = item end
    return parts
end

local function vec3(text)
    local sx, sy, sz = (text or ''):match('^([^,]+),([^,]+),([^,]+)$')
    return {x = tonumber(sx) or 0, y = tonumber(sy) or 0, z = tonumber(sz) or 0}
end

local function dist3(a, b)
    local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local function dot3(a, b)
    return a.x * b.x + a.y * b.y + a.z * b.z
end

local function fmt_vec(v)
    return ('%.4f,%.4f,%.4f'):format(v.x, v.y, v.z)
end

local function safe_token(text)
    local s = tostring(text or ''):gsub('%s+', '_')
    if s == '' then return '<empty>' end
    return s
end

local function check_profile()
    local path = C.GetSaveFolderPath()
    assert(path ~= nil and path ~= ffi.NULL, 'save folder unavailable')
    local text = ffi.string(path):gsub('\\', '/'):gsub('/+$', '')
    assert(text:match('/' .. tostring(cfg.profile) .. '/save$'), 'not disposable profile ' .. tostring(cfg.profile))
end

local function read_pose(actor)
    local p = C.GetObjectPositionInSector(actor)
    return {
        x = tonumber(p.x), y = tonumber(p.y), z = tonumber(p.z),
        yaw = tonumber(p.yaw), pitch = tonumber(p.pitch), roll = tonumber(p.roll),
    }
end

local function write_pose(actor, sector, pose)
    C.SetObjectSectorPos(actor, sector, ffi.new('UIPosRot', {
        x = pose.x, y = pose.y, z = pose.z,
        yaw = math.deg(pose.yaw), pitch = math.deg(pose.pitch), roll = math.deg(pose.roll),
    }))
end

local state = {
    stage = 'initial',
    epoch = cfg.epoch,
    reg_reply = nil,
    probe_reply = nil,
    direct_vel_reply = nil,
    direct_vel_req = 10000,
    direct_vel_handler = nil,
    game_saved = nil,
    save_list_reloaded = false,
    own_actor = nil,
    own_code = nil,
    own_init_pose = nil,
    remote_actor = nil,
    remote_hex = nil,
    remote_code = nil,
    remote_sector_id = nil,
    remote_sector = nil,
    remote_init_pose = nil,
    remote_basis = nil,
    basis_error = nil,
    udp_net = nil,
    clock_fn = nil,
    mono_offset = 0.0,
    last_mono = 0.0,
    cockpit_reader = nil,
    stream = nil,
    vel_ctrl = nil,
    vel_rows = {},
    v4_samples_received = 0,
    v4_pose_writes = 0,
    next_seq = 1,
    active_req = nil,
    last_poll = 0,
}

local function fallback_clock()
    local v = state.mono_offset + GetCurRealTime()
    if v < state.last_mono then v = state.last_mono end
    state.last_mono = v
    return v
end

local function now_clock()
    if state.clock_fn then return state.clock_fn() end
    return GetCurRealTime()
end

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.reg_reply = tostring(value)
end)

RegisterEvent('X4AuthorityTargetSpeedProbe.Result', function(_, value)
    state.probe_reply = tostring(value)
end)

RegisterEvent('gameSaved', function(_, success)
    state.game_saved = (success == true)
end)

RegisterEvent('X4AuthorityCompanyCockpitVelocity.Result', function(_, value)
    local text = tostring(value)
    if state.direct_vel_handler then
        state.direct_vel_handler(text)
    end
    if state.v4_vel_callback then
        state.v4_vel_callback(nil, text)
    end
end)

local function load_request(seq)
    local ok, chunk = pcall(loadfile, EXT_DIR .. 'req_' .. tostring(seq) .. '.lua')
    if not (ok and chunk) then return nil end
    local rok, val = pcall(chunk)
    if rok and type(val) == 'table' and val.seq == seq then return val end
    return nil
end

local function emit_resp(seq, status, extra)
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    local rt = now_clock()
    log(('RESP seq=%d status=%s game_time=%.6f real_time=%.6f %s'):format(
        seq, status, gt, rt, extra or ''))
end

local function finish_req(seq, status, extra)
    emit_resp(seq, status, extra)
    state.active_req = nil
    state.next_seq = seq + 1
end

local function fmt_hud_kv(hud)
    return (
        'x4_ver=%s hud_active=%s ext_tgt=%s ext_view=%s st_match=%s conn=%s msg_id=%d ' ..
        'comp_name=%s spd_resolved=%s spd_empty=%s hud_txt_resolved=%s hud_txt_empty=%s ' ..
        'screen_on=%s screen_xy=%.1f,%.1f ps_ok=%s match_spd=%s ch_tgt_spd=%s ch_norm_tgt_spd=%s'
    ):format(
        safe_token(hud.x4_version), tostring(hud.is_hud_active), tostring(hud.is_external_target),
        tostring(hud.is_external_view), tostring(hud.softtarget_matches), safe_token(hud.connection_name),
        hud.message_id or 0, safe_token(hud.component_name),
        tostring(hud.speed_resolved_conn), tostring(hud.speed_empty_conn),
        safe_token(hud.hud_formatted_resolved), safe_token(hud.hud_formatted_empty),
        tostring(hud.screen_on), hud.screen_x or -1, hud.screen_y or -1,
        tostring(hud.player_speed_ok), tostring(hud.match_speed),
        tostring(hud.crosshair_target_speed), tostring(hud.crosshair_normal_target_speed)
    )
end

local function init_v4_stream(clock_base)
    if not state.udp_net then
        local dll_path = EXT_DIR .. 'native/x4lan_net_64.dll'
        local open_fn, err = package.loadlib(dll_path, 'luaopen_x4lan_net')
        assert(type(open_fn) == 'function', 'failed loading x4lan_net_64.dll: ' .. tostring(err))
        local udp = open_fn()
        assert(type(udp) == 'table' and type(udp.open) == 'function', 'invalid x4lan_net module')
        state.udp_net = assert(udp.open('127.0.0.1', assert(cfg.udp_port, 'udp_port required')))
        if type(udp.monotonic) == 'function' then
            state.clock_fn = udp.monotonic
        else
            state.mono_offset = (tonumber(clock_base) or 0) - GetCurRealTime() + 0.05
            state.last_mono = state.mono_offset + GetCurRealTime()
            state.clock_fn = fallback_clock
        end
    end

    local pinned = {epoch = state.epoch, nonce = cfg.nonce, issued_count = 2}
    local function available()
        return C.IsPlayerValid() and not C.IsGamePaused()
    end

    state.cockpit_reader = assert(X4LAN_COMPANY_COCKPIT_OBSERVER.new_x4(pinned, 1, available, {
        radar_fallback = true,
        target_index = 2,
        clock = state.clock_fn,
    }))
    local map_reader = {
        tick = function() return nil end,
        matches = function() return false end,
        close = function() end,
    }

    local anchor_fn = native_vel_mod.make_x4_anchor(state.remote_actor)
    state.vel_rows = {}
    state.v4_samples_received = 0
    state.v4_pose_writes = 0
    state.vel_ctrl = assert(native_vel_mod.new({
        enabled = true,
        pinned = pinned,
        own_index = 1,
        observer = state.cockpit_reader,
        guard = available,
        clock = state.clock_fn,
        game = function() return tonumber(C.GetCurrentGameTime()) or 0 end,
        paused = function() return C.IsGamePaused() end,
        constantvel = true,
        loss_seconds = 5.0,
        anchor = anchor_fn,
        register = function(cb)
            state.v4_vel_callback = cb
            return true
        end,
        record = function(row)
            state.vel_rows[#state.vel_rows + 1] = row
            local hud = native_vel_mod.read_hud_target_speed_x4(state.remote_actor, cfg.width, cfg.height)
            local opose = read_pose(state.own_actor)
            local odisp = dist3(state.own_init_pose, opose)
            log((
                'VEL_EVENT kind=%s seq=%s reason=%s game_t=%.4f real_t=%.4f pos=%s ' ..
                'loc_vel=%s sec_vel=%s fwd_mps=%.3f lat_mps=%.3f tot_mps=%.3f ' ..
                'disp_m=%s drift_m=%s trans=%s trans_err=%s snapback_m=%s anchor_drift_m=%s near_zero=%s ' ..
                'own_disp_m=%.5f spd_resolved=%s spd_empty=%s ch_tgt_spd=%s'
            ):format(
                tostring(row.kind), tostring(row.sequence or '-'), tostring(row.reason or '-'),
                row.game_time or -1, row.real_time or -1,
                row.position and fmt_vec(row.position) or '-',
                row.local_velocity and fmt_vec(row.local_velocity) or '-',
                row.sector_velocity and fmt_vec(row.sector_velocity) or '-',
                row.forward_speed_mps or -1, row.lateral_speed_mps or -1, row.total_speed_mps or -1,
                row.displacement_m and ('%.4f'):format(row.displacement_m) or '-',
                row.physical_drift_error_m and ('%.5f'):format(row.physical_drift_error_m) or '-',
                tostring(row.speed_transition or false),
                row.transition_segment_error_m and ('%.5f'):format(row.transition_segment_error_m) or '-',
                row.snapback_m and ('%.5f'):format(row.snapback_m) or '-',
                row.anchor_drift_m and ('%.5f'):format(row.anchor_drift_m) or '-',
                tostring(row.observed_near_zero),
                odisp, tostring(hud.speed_resolved_conn), tostring(hud.speed_empty_conn),
                tostring(hud.crosshair_target_speed)
            ))
        end,
    }))

    -- Composite V4 playback receiver: applies incoming V4 attitude+pose on sample arrival
    -- and drives X4LAN_COMPANY_COCKPIT_NATIVE_VELOCITY through turns and the 5-second packet gap.
    local playback_bridge = {
        view = function() end,
        end_track = function(_, _, _, _)
            if state.vel_ctrl then state.vel_ctrl:stop('end_track') end
        end,
        sample = function(_, observed, serial, sample, arrival, slot_name)
            if slot_name ~= 'cockpit' or not observed or observed.index ~= 2 then return false end
            state.v4_samples_received = state.v4_samples_received + 1
            if sample.attitude then
                write_pose(state.remote_actor, state.remote_sector_id, {
                    x = sample.x, y = sample.y, z = sample.z,
                    yaw = sample.attitude.yaw, pitch = sample.attitude.pitch, roll = sample.attitude.roll,
                })
                state.v4_pose_writes = state.v4_pose_writes + 1
            end
            local binding = {
                slot = 'cockpit', serial = serial, epoch = observed.epoch, index = observed.index,
                actor = observed.actor, code = observed.code, sector_macro = observed.sector_macro,
                generation = sample.generation,
            }
            return state.vel_ctrl:sample(binding, sample)
        end,
        tick = function()
            if state.vel_ctrl then state.vel_ctrl:tick() end
        end,
        close = function()
            if state.vel_ctrl then state.vel_ctrl:stop('closed') end
        end,
    }

    local prefix = 'C|' .. cfg.udp_lease .. '|' .. cfg.udp_generation .. '|'
    state.stream = assert(X4LAN_COMPANY_OBSERVER_STREAM.new(
        {cockpit = state.cockpit_reader, map = map_reader},
        state.epoch, state.udp_net, prefix, state.clock_fn, playback_bridge
    ))
end

local function issue_direct_md_vel(op, extra_args)
    state.direct_vel_req = state.direct_vel_req + 1
    local req_id = state.direct_vel_req
    state.direct_vel_reply = nil
    state.direct_vel_handler = function(raw)
        local parts = split(raw, '|')
        if tonumber(parts[1]) == req_id then
            state.direct_vel_reply = raw
            state.direct_vel_handler = nil
        end
    end
    local args = {
        op = op, request = req_id, epoch = state.epoch, index = 2,
        own = 1, count = 2, actor = state.remote_hex, sector = state.remote_sector,
        code = state.remote_code,
    }
    if op == 'baseline' or op == 'apply' or op == 'readback' then args.pilot = 2 end
    for k, v in pairs(extra_args or {}) do args[k] = v end
    assert(AddUITriggeredEvent('X4AuthorityCompanyCockpitVelocity', 'Request', args) ~= false,
        'failed dispatching direct MD velocity op ' .. tostring(op))
    return req_id
end

local function step_active_req()
    local req = state.active_req
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    if req.wait_until_game_time and gt < req.wait_until_game_time then return end
    if req.wait_until_real_time and now_clock() < req.wait_until_real_time then return end

    if not req.dispatched then
        req.dispatched = true
        state.probe_reply = nil
        local op = req.op
        if op == 'lock_hud' then
            assert(C.SetSofttarget(state.remote_actor, ''), 'SetSofttarget failed')
            local hud = native_vel_mod.read_hud_target_speed_x4(state.remote_actor, cfg.width, cfg.height)
            local dist = tonumber(C.GetDistanceBetween(state.own_actor, state.remote_actor)) or -1
            finish_req(req.seq, 'hud_locked', ('dist=%.3f %s'):format(dist, fmt_hud_kv(hud)))
            return
        elseif op == 'read_hud' then
            local hud = native_vel_mod.read_hud_target_speed_x4(state.remote_actor, cfg.width, cfg.height)
            local rpose = read_pose(state.remote_actor)
            local opose = read_pose(state.own_actor)
            local odisp = dist3(state.own_init_pose, opose)
            local dist = tonumber(C.GetDistanceBetween(state.own_actor, state.remote_actor)) or -1
            finish_req(req.seq, 'hud_read', (
                'pos=%s yaw_deg=%.3f pitch_deg=%.3f roll_deg=%.3f dist=%.3f own_disp_m=%.5f %s'
            ):format(
                fmt_vec(rpose), math.deg(rpose.yaw), math.deg(rpose.pitch), math.deg(rpose.roll),
                dist, odisp, fmt_hud_kv(hud)
            ))
            return
        elseif op == 'set_pose' then
            local cur = read_pose(state.remote_actor)
            local target = {
                x = req.x or (cur.x + (req.dx or 0)),
                y = req.y or (cur.y + (req.dy or 0)),
                z = req.z or (cur.z + (req.dz or 0)),
                yaw = req.yaw_deg and math.rad(req.yaw_deg) or cur.yaw,
                pitch = req.pitch_deg and math.rad(req.pitch_deg) or cur.pitch,
                roll = req.roll_deg and math.rad(req.roll_deg) or cur.roll,
            }
            write_pose(state.remote_actor, state.remote_sector_id, target)
            finish_req(req.seq, 'pose_written', ('target=%s yaw_deg=%.3f'):format(fmt_vec(target), math.deg(target.yaw)))
            return
        elseif op == 'anchor_current_pose' then
            local anchor_fn = native_vel_mod.make_x4_anchor(state.remote_actor)
            local posed = assert(anchor_fn(), 'anchor_current_pose failed')
            finish_req(req.seq, 'anchored', ('pos=%s yaw_deg=%.3f'):format(fmt_vec(posed), math.deg(posed.yaw)))
            return
        elseif op == 'direct_vel' then
            req.md_mode = 'direct_vel'
            issue_direct_md_vel(req.vel_op, {
                vx = req.vx, vy = req.vy, vz = req.vz, constantvel = req.constantvel,
            })
            return
        elseif op == 'start_v4_stream' then
            init_v4_stream(req.clock_base)
            finish_req(req.seq, 'v4_stream_started', 'started=true')
            return
        elseif op == 'probe_v4_state' then
            local vst = state.vel_ctrl and state.vel_ctrl:state() or {}
            local hud = native_vel_mod.read_hud_target_speed_x4(state.remote_actor, cfg.width, cfg.height)
            local rpose = read_pose(state.remote_actor)
            local opose = read_pose(state.own_actor)
            local odisp = dist3(state.own_init_pose, opose)
            local mode = state.cockpit_reader and state.cockpit_reader:mode() or 'none'
            finish_req(req.seq, 'v4_probed', (
                'mode=%s v4_samples=%d v4_pose_writes=%d vel_rows=%d vel_active=%s vel_may_hold=%s ' ..
                'vel_stopped=%s vel_release=%s pos=%s yaw_deg=%.3f own_disp_m=%.5f %s'
            ):format(
                tostring(mode), state.v4_samples_received, state.v4_pose_writes, #state.vel_rows,
                tostring(vst.active), tostring(vst.may_hold), tostring(vst.stopped), tostring(vst.release),
                fmt_vec(rpose), math.deg(rpose.yaw), odisp, fmt_hud_kv(hud)
            ))
            return
        elseif op == 'md_readback' then
            req.md_mode = 'probe_readback'
            AddUITriggeredEvent('X4AuthorityTargetSpeedProbe', 'Readback', {
                request_id = req.request_id, epoch = state.epoch, index = 2,
            })
            return
        elseif op == 'done' then
            if state.stream then state.stream:close() end
            finish_req(req.seq, 'done', 'closed=true')
            state.stage = 'done'
            log('DONE')
            return
        else
            error('unknown op: ' .. tostring(op))
        end
    end

    if req.md_mode == 'direct_vel' then
        if not state.direct_vel_reply then return end
        local raw = state.direct_vel_reply
        state.direct_vel_reply = nil
        local parts = split(raw, '|')
        local status = parts[2]
        assert(status ~= 'refused', 'direct MD velocity refused: ' .. raw)
        local pos = vec3(parts[7])
        local loc_vel = vec3(parts[8])
        local r_vec, u_vec, f_vec = vec3(parts[9]), vec3(parts[10]), vec3(parts[11])
        local dist_m = tonumber(parts[12]) or -1
        local sec_vel = {
            x = loc_vel.x * r_vec.x + loc_vel.y * u_vec.x + loc_vel.z * f_vec.x,
            y = loc_vel.x * r_vec.y + loc_vel.y * u_vec.y + loc_vel.z * f_vec.y,
            z = loc_vel.x * r_vec.z + loc_vel.y * u_vec.z + loc_vel.z * f_vec.z,
        }
        local fwd_mps = dot3(sec_vel, f_vec)
        local lat_mps = math.sqrt(loc_vel.x * loc_vel.x + loc_vel.y * loc_vel.y)
        local tot_mps = math.sqrt(dot3(sec_vel, sec_vel))
        local hud = native_vel_mod.read_hud_target_speed_x4(state.remote_actor, cfg.width, cfg.height)
        local opose = read_pose(state.own_actor)
        local odisp = dist3(state.own_init_pose, opose)
        local snap_extra = ''
        if status == 'zeroed' or status == 'released' then
            local before_pos = vec3(parts[13])
            local before_vel = vec3(parts[14])
            snap_extra = (' before_pos=%s before_loc_vel=%s snapback_m=%.5f'):format(
                fmt_vec(before_pos), fmt_vec(before_vel), dist3(pos, before_pos))
        end
        finish_req(req.seq, 'direct_vel_ok', (
            'md_status=%s md_age=%.5f pos=%s loc_vel=%s sec_vel=%s fwd_mps=%.4f lat_mps=%.4f ' ..
            'tot_mps=%.4f right=%s up=%s forward=%s dist_m=%.3f own_disp_m=%.5f%s %s'
        ):format(
            status, tonumber(parts[6]) or -1, fmt_vec(pos), fmt_vec(loc_vel), fmt_vec(sec_vel),
            fwd_mps, lat_mps, tot_mps, fmt_vec(r_vec), fmt_vec(u_vec), fmt_vec(f_vec),
            dist_m, odisp, snap_extra, fmt_hud_kv(hud)
        ))
        return
    end

    if req.md_mode == 'probe_readback' then
        if not state.probe_reply then return end
        local raw = state.probe_reply
        state.probe_reply = nil
        local parts = split(raw, '|')
        assert(parts[1] == req.request_id and parts[2] == 'Readback', 'md_readback failed: ' .. raw)
        local pos = vec3(parts[10])
        local loc_vel = vec3(parts[11])
        local r_vec, u_vec, f_vec = vec3(parts[12]), vec3(parts[13]), vec3(parts[14])
        local own_md = vec3(parts[15])
        local dist_m = tonumber(parts[16]) or -1
        local sec_vel = {
            x = loc_vel.x * r_vec.x + loc_vel.y * u_vec.x + loc_vel.z * f_vec.x,
            y = loc_vel.x * r_vec.y + loc_vel.y * u_vec.y + loc_vel.z * f_vec.y,
            z = loc_vel.x * r_vec.z + loc_vel.y * u_vec.z + loc_vel.z * f_vec.z,
        }
        local fwd_mps = dot3(sec_vel, f_vec)
        local lat_mps = math.sqrt(loc_vel.x * loc_vel.x + loc_vel.y * loc_vel.y)
        local tot_mps = math.sqrt(dot3(sec_vel, sec_vel))
        local hud = native_vel_mod.read_hud_target_speed_x4(state.remote_actor, cfg.width, cfg.height)
        local opose = read_pose(state.own_actor)
        local odisp = dist3(state.own_init_pose, opose)
        finish_req(req.seq, 'md_readback_ok', (
            'actor=%s sector=%s code=%s md_age=%.5f pos=%s loc_vel=%s sec_vel=%s ' ..
            'fwd_mps=%.4f lat_mps=%.4f tot_mps=%.4f own_md=%s own_disp_m=%.5f dist_m=%.3f %s'
        ):format(
            parts[4], parts[7], parts[8], tonumber(parts[9]) or -1,
            fmt_vec(pos), fmt_vec(loc_vel), fmt_vec(sec_vel), fwd_mps, lat_mps, tot_mps,
            fmt_vec(own_md), odisp, dist_m, fmt_hud_kv(hud)
        ))
    end
end

local function frame()
    if state.stage == 'done' or state.stage == 'failed' then return end
    local t = getElapsedTime()
    if state.stage == 'initial' then
        check_profile()
        if C.IsStartmenu() then
            log('NEWGAME gamestart=' .. tostring(cfg.gamestart))
            state.stage = 'starting'
            Helper.addDelayedOneTimeCallbackOnUpdate(function()
                NewGame(cfg.gamestart)
            end, true, t + 2)
            return
        end
        if not C.IsPlayerValid() then return end
        local gt = tonumber(C.GetCurrentGameTime()) or 0
        if gt < 2.0 then return end
        state.reg_reply = nil
        state.stage = 'init_registry'
        AddUITriggeredEvent('X4AuthorityShipRegistry', 'Initialize', {
            request_id = 'init-1', epoch = state.epoch,
        })
        return
    end
    if state.stage == 'starting' then return end
    if state.stage == 'init_registry' then
        if not state.reg_reply then return end
        local raw = state.reg_reply
        state.reg_reply = nil
        assert(raw == ('init-1|Initialized|' .. state.epoch), 'registry init failed: ' .. raw)
        state.probe_reply = nil
        state.stage = 'setup_ships'
        AddUITriggeredEvent('X4AuthorityTargetSpeedProbe', 'Setup', {
            request_id = 'setup-1', epoch = state.epoch,
        })
        return
    end
    if state.stage == 'setup_ships' then
        if not state.probe_reply then return end
        local raw = state.probe_reply
        state.probe_reply = nil
        local parts = split(raw, '|')
        assert(parts[1] == 'setup-1' and parts[2] == 'Ready', 'setup failed: ' .. raw)
        state.own_actor = assert(resolve.parse_hex(parts[3]), 'invalid own actor hex')
        state.own_code = parts[4]
        state.remote_sector = parts[5]
        state.remote_hex = parts[6]
        state.remote_actor = assert(resolve.parse_hex(parts[6]), 'invalid remote actor hex')
        state.remote_code = parts[7]
        state.remote_sector_id = C.GetContextByClass(state.remote_actor, 'sector', false)
        state.basis_error = tonumber(parts[9]) or -1
        state.remote_init_pose = read_pose(state.remote_actor)
        state.own_init_pose = read_pose(state.own_actor)
        state.remote_basis = {
            right = vec3(parts[12]), up = vec3(parts[13]), forward = vec3(parts[14]),
        }
        state.save_list_reloaded = false
        state.stage = 'save_wait'
        return
    end
    if state.stage == 'save_wait' then
        if not state.save_list_reloaded then
            C.ReloadSaveList()
            state.save_list_reloaded = true
            return
        end
        if not C.IsSaveListLoadingComplete() then return end
        state.game_saved = nil
        SaveGame(SAVE_SLOT, 'Neon Windows target-speed disposable save')
        state.stage = 'wait_save'
        return
    end
    if state.stage == 'wait_save' then
        if state.game_saved == nil then return end
        assert(state.game_saved == true, 'SaveGame failed')
        if C.IsGamePaused() then Unpause() end
        state.stage = 'wait_unpause'
        return
    end
    if state.stage == 'wait_unpause' then
        if C.IsGamePaused() then
            Unpause()
            return
        end
        state.own_init_pose = read_pose(state.own_actor)
        state.remote_init_pose = read_pose(state.remote_actor)
        local ver_ok, ver_str = pcall(GetVersionString)
        state.stage = 'ready'
        log((
            'READY x4_ver=%s save_slot=%s own_code=%s remote_code=%s remote_hex=%s remote_sector=%s ' ..
            'basis_error=%.12f remote_xyz=%s own_xyz=%s right=%s up=%s forward=%s'
        ):format(
            safe_token(ver_ok and ver_str or 'unknown'), SAVE_SLOT,
            state.own_code, state.remote_code, state.remote_hex, state.remote_sector,
            state.basis_error, fmt_vec(state.remote_init_pose), fmt_vec(state.own_init_pose),
            fmt_vec(state.remote_basis.right), fmt_vec(state.remote_basis.up), fmt_vec(state.remote_basis.forward)
        ))
        return
    end

    if state.stream then
        state.stream:tick(not C.IsGamePaused())
    end
    if state.active_req then
        step_active_req()
        return
    end
    if t - state.last_poll < 0.02 then return end
    state.last_poll = t
    local req = load_request(state.next_seq)
    if req then
        if req.min_game_delay and req.min_game_delay > 0 then
            req.wait_until_game_time = (tonumber(C.GetCurrentGameTime()) or 0) + req.min_game_delay
        end
        if req.min_real_delay and req.min_real_delay > 0 then
            req.wait_until_real_time = now_clock() + req.min_real_delay
        end
        state.active_req = req
    end
end

SetScript('onUpdate', function()
    local ok, err = pcall(frame)
    if not ok and state.stage ~= 'failed' then
        state.stage = 'failed'
        log('ERROR ' .. tostring(err):gsub('%s+', ' '))
    end
end)
