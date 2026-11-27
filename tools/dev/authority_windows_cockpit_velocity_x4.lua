-- Native Windows X4 9.00 trial driver exercising the GENUINE opt-in receiving cockpit
-- (company_observer_stream + company_cockpit_observer + company_cockpit_playback +
--  company_hull_playback + company_hull_playback_x4 + company_cockpit_velocity +
--  company_cockpit_velocity.xml constantvel="false") on a disposable local save with
-- the observed ship locked in the stock target HUD.
local cfg = X4NEON_COCKPIT_VEL
if type(cfg) ~= 'table' or cfg.enabled ~= true then return end

local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
declare('typedef struct { float x, y, z, yaw, pitch, roll; } UIPosRot;')
declare('typedef struct { const char* name; float hull; float shield; int speed; bool hasShield; } ComponentDetails;')
declare('typedef struct { UniverseID softtargetID; const char* softtargetConnectionName; uint32_t messageID; } SofttargetDetails2;')
declare('typedef struct { float x; float y; bool onScreen; } ScreenPos;')
declare('bool IsPlayerValid(void);')
declare('bool IsGamePaused(void);')
declare('bool IsHUDActive(void);')
declare('bool IsStartmenu(void);')
declare('bool IsValidComponent(UniverseID);')
declare('bool IsComponentOperational(UniverseID);')
declare('UniverseID GetPlayerControlledShipID(void);')
declare('UniverseID GetPlayerOccupiedShipID(void);')
declare('UniverseID GetPlayerContainerID(void);')
declare('UniverseID GetContextByClass(UniverseID, const char*, bool);')
declare('double GetCurrentGameTime(void);')
declare('UIPosRot GetObjectPositionInSector(UniverseID);')
declare('UniverseID GetZoneAt(UniverseID, UIPosRot*);')
declare('void SetSofttarget(UniverseID componentid, const char* const connectionname);')
declare('SofttargetDetails2 GetSofttarget2(void);')
declare('ComponentDetails GetComponentDetails(const UniverseID componentid, const char* const connectionname);')
declare('ScreenPos GetComponentScreenPosition(UniverseID componentid, uint32_t width, uint32_t height);')

local TAG = '[X4LAN] AUTH_COCKPIT_VEL '
local function log(kind, kv)
    local parts = {TAG .. 'kind=' .. tostring(kind)}
    if type(kv) == 'table' then
        local keys = {}
        for k in pairs(kv) do keys[#keys + 1] = k end
        table.sort(keys)
        for _, k in ipairs(keys) do
            parts[#parts + 1] = tostring(k) .. '=' .. tostring(kv[k]):gsub('[ \r\n]+', '_')
        end
    end
    DebugError(table.concat(parts, ' '))
end

local function load_udp()
    local loader = assert(package.loadlib('extensions/' .. cfg.extension_id .. '/native/x4lan_net_64.dll', 'luaopen_x4lan_net'))
    local mod = assert(loader())
    assert(type(mod.monotonic) == 'function', 'x4lan_net_64.dll missing monotonic()')
    return mod
end

local udp = load_udp()
local resolve = assert(X4LAN_RESOLVE_STRING)
local phase = 'boot'
local boot_started = udp.monotonic()
local launched = false
local setup_req_id = nil
local save_started_at = nil
local world = nil
local remote_u64 = nil
local own_u64 = nil

local stream = nil
local multi_obs = nil
local playback_ctrl = nil
local net = nil
local active_cmd = nil
local last_cmd_seq = 0
local pending_probe_rb = nil
local req_counter = 0
local failed = false
local done = false

local function read_hud(actor_u64)
    local st = C.GetSofttarget2()
    local conn = ''
    if st.softtargetConnectionName ~= nil and st.softtargetConnectionName ~= ffi.NULL then
        conn = ffi.string(st.softtargetConnectionName)
    end
    local det_res = C.GetComponentDetails(actor_u64, conn)
    local det_emp = C.GetComponentDetails(actor_u64, '')
    local scr = C.GetComponentScreenPosition(actor_u64, cfg.width or 800, cfg.height or 600)
    local ver_ok, ver_str = pcall(GetVersionString)
    local p = C.GetObjectPositionInSector(actor_u64)
    local own_p = C.GetObjectPositionInSector(own_u64)
    return {
        x4_version = ver_ok and tostring(ver_str) or 'unknown',
        hud_active = C.IsHUDActive() == true,
        softtarget_matches = st.softtargetID == actor_u64,
        conn = conn,
        speed_conn = tonumber(det_res.speed) or 0,
        speed_empty = tonumber(det_emp.speed) or 0,
        screen_on = scr.onScreen == true,
        screen_x = ('%.1f'):format(tonumber(scr.x) or 0),
        screen_y = ('%.1f'):format(tonumber(scr.y) or 0),
        ffi_xyz = ('%.6f,%.6f,%.6f'):format(tonumber(p.x), tonumber(p.y), tonumber(p.z)),
        ffi_rot = ('%.6f,%.6f,%.6f'):format(tonumber(p.yaw), tonumber(p.pitch), tonumber(p.roll)),
        own_ffi_xyz = ('%.6f,%.6f,%.6f'):format(tonumber(own_p.x), tonumber(own_p.y), tonumber(own_p.z)),
        game_time = ('%.6f'):format(tonumber(C.GetCurrentGameTime())),
        mono_time = ('%.6f'):format(udp.monotonic()),
    }
end

local function parse_vec(s)
    local x, y, z = (s or ''):match('^([^,]+),([^,]+),([^,]+)$')
    if x and y and z then return tonumber(x), tonumber(y), tonumber(z) end
end

local function compute_sector_vel(lvel_str, r_str, u_str, f_str)
    local lx, ly, lz = parse_vec(lvel_str)
    local rx, ry, rz = parse_vec(r_str)
    local ux, uy, uz = parse_vec(u_str)
    local fx, fy, fz = parse_vec(f_str)
    if not (lx and rx and ux and fx) then return 0, 0, 0, 0, 0 end
    local vx = lx * rx + ly * ux + lz * fx
    local vy = lx * ry + ly * uy + lz * fy
    local vz = lx * rz + ly * uz + lz * fz
    local fwd = vx * fx + vy * fy + vz * fz
    local tot = math.sqrt(vx * vx + vy * vy + vz * vz)
    return vx, vy, vz, fwd, tot
end

local function on_probe_result(_, raw)
    if type(raw) ~= 'string' then return end
    local f = {}
    for item in (raw .. '|'):gmatch('([^|]*)|') do f[#f + 1] = item end
    if setup_req_id and f[1] == setup_req_id then
        setup_req_id = nil
        if f[2] ~= 'Ready' then
            failed = true
            log('error', {reason = 'setup_refused', detail = f[3] or raw})
            return
        end
        world = {
            own_actor = f[3], own_code = f[4], sector_macro = f[5],
            remote_actor = f[6], remote_code = f[7], remote_zone = f[8],
            basis_error = f[9], remote_xyz = f[10], own_xyz = f[11],
            right = f[12], up = f[13], forward = f[14],
        }
        own_u64 = assert(resolve.parse_hex(world.own_actor))
        remote_u64 = assert(resolve.parse_hex(world.remote_actor))
        local ok_save = pcall(SaveGame, cfg.save_slot, 'Neon cockpit velocity trial')
        save_started_at = udp.monotonic()
        phase = 'saving'
        log('setup_ok', {
            own_actor = world.own_actor, own_code = world.own_code,
            remote_actor = world.remote_actor, remote_code = world.remote_code,
            sector = world.sector_macro, basis_error = world.basis_error,
            save_ok = ok_save,
        })
        return
    end
    if pending_probe_rb and f[1] == pending_probe_rb.req_id then
        local cb = pending_probe_rb.cb
        pending_probe_rb = nil
        cb(f)
    end
end

assert(RegisterEvent('X4AuthorityCockpitVelProbe.Result', on_probe_result) ~= false)

local function start_genuine_receiver()
    if stream then return true end
    local pinned = {epoch = cfg.epoch, issued_count = 2, nonce = cfg.nonce}
    local function guard()
        return not failed and C.IsPlayerValid() and not C.IsGamePaused()
            and C.GetPlayerControlledShipID() == own_u64
            and C.GetPlayerOccupiedShipID() == own_u64
            and C.GetPlayerContainerID() == own_u64
    end
    multi_obs = assert(X4LAN_COMPANY_COCKPIT_OBSERVER.new_multi_x4(pinned, 1, guard, {
        max_pilots = 3, clock = udp.monotonic,
    }))
    local motion_cfg = {
        index = 1,
        cockpit_radar_playback = true,
        cockpit_max_pilots = 3,
        cockpit_continuous_playback = true,
        cockpit_native_velocity = true,
    }
    playback_ctrl = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK.new_multi_x4(
        motion_cfg, pinned, multi_obs.readers, guard, udp.monotonic))
    local map_reader = {
        tick = function() return nil end,
        matches = function() return false end,
        close = function() end,
    }
    local readers = {map = map_reader}
    for slot_name, reader in pairs(multi_obs.readers) do
        readers[slot_name] = reader
    end
    net = assert(udp.open('127.0.0.1', cfg.udp_port))
    local prefix = 'C|' .. cfg.udp_lease .. '|' .. cfg.udp_generation .. '|'
    stream = assert(X4LAN_COMPANY_OBSERVER_STREAM.new(
        readers, cfg.epoch, net, prefix, udp.monotonic, playback_ctrl, multi_obs))
    return true
end

local function finish_cmd(cmd, payload)
    payload.seq = cmd.seq
    payload.op = cmd.op
    log('resp', payload)
    active_cmd = nil
end

local function read_command_file()
    local path = 'extensions/' .. cfg.extension_id .. '/ui/command.lua'
    local chunk = loadfile(path)
    if not chunk then return nil end
    local ok, data = pcall(chunk)
    if ok and type(data) == 'table' and type(data.seq) == 'number' and data.seq > last_cmd_seq then
        return data
    end
    return nil
end

local function request_md_readback(cb)
    req_counter = req_counter + 1
    local rid = 'rb-' .. tostring(req_counter)
    pending_probe_rb = {req_id = rid, cb = cb}
    AddUITriggeredEvent('X4AuthorityCockpitVelProbe', 'Readback', {
        request_id = rid, epoch = cfg.epoch, index = 2,
    })
end

local function begin_cmd(cmd)
    active_cmd = cmd
    if cmd.op == 'lock_hud_and_start' then
        C.SetSofttarget(remote_u64, '')
        start_genuine_receiver()
        cmd.started_at = udp.monotonic()
        cmd.stage = 'wait_ready'
        return
    elseif cmd.op == 'probe' then
        request_md_readback(function(f)
            if f[2] ~= 'Readback' then
                finish_cmd(cmd, {status = 'refused', detail = f[3] or 'unknown'})
                return
            end
            local vx, vy, vz, fwd, tot = compute_sector_vel(f[11], f[12], f[13], f[14])
            local hud = read_hud(remote_u64)
            local st_c = playback_ctrl and playback_ctrl:slot_stats('cockpit') or {}
            local payload = {
                status = 'ok',
                md_age = f[9],
                md_xyz = f[10],
                md_local_vel = f[11],
                md_sec_vel = ('%.6f,%.6f,%.6f'):format(vx, vy, vz),
                md_fwd_mps = ('%.4f'):format(fwd),
                md_tot_mps = ('%.4f'):format(tot),
                md_own_xyz = f[15],
                md_dist = f[16],
                obs_mode = tostring(multi_obs and multi_obs:mode('cockpit') or 'none'),
                is_active_pilot = tostring(multi_obs and multi_obs.readers.cockpit:is_active_pilot(2) or false),
                pb_active = tostring(st_c.active or false),
                pb_closed = tostring(st_c.closed or false),
                pb_vel_uncertain = tostring(st_c.velocity_cleanup_uncertain or false),
                pb_vel_reason = tostring(st_c.velocity_uncertain_reason or 'none'),
                pb_writes = tostring(st_c.writes or 0),
                pb_readbacks = tostring(st_c.readbacks or 0),
                pb_stale = tostring(st_c.stale_cutoffs or 0),
                pb_withdrawals = tostring(st_c.withdrawals or 0),
                pb_reacq = tostring(st_c.reacquisitions or 0),
                pb_over50 = tostring(st_c.refusals_over_50m or 0),
            }
            for k, v in pairs(hud) do payload[k] = v end
            finish_cmd(cmd, payload)
        end)
        return
    elseif cmd.op == 'finish' then
        if stream then pcall(stream.close, stream); stream = nil end
        done = true
        finish_cmd(cmd, {status = 'ok'})
        return
    else
        finish_cmd(cmd, {status = 'unknown_op'})
    end
end

local function step_cmd()
    if not active_cmd then return end
    if active_cmd.op == 'lock_hud_and_start' then
        local mode = multi_obs and multi_obs:mode('cockpit')
        local cur = multi_obs and multi_obs.readers.cockpit:current()
        local is_pilot = multi_obs and multi_obs.readers.cockpit:is_active_pilot(2)
        if mode == 'hud' and cur and cur.index == 2 and is_pilot == true then
            local hud = read_hud(remote_u64)
            hud.status = 'ok'
            hud.obs_mode = mode
            hud.is_active_pilot = 'true'
            finish_cmd(active_cmd, hud)
        elseif udp.monotonic() - active_cmd.started_at > 15.0 then
            finish_cmd(active_cmd, {
                status = 'timeout',
                obs_mode = tostring(mode),
                is_active_pilot = tostring(is_pilot),
            })
        end
    end
end

local function on_frame()
    if failed or done then return end
    local now = udp.monotonic()
    if phase == 'boot' then
        if not launched and C.IsStartmenu() and now - boot_started >= 1.0 then
            launched = true
            NewGame(cfg.gamestart)
        elseif not C.IsStartmenu() and C.IsPlayerValid() and not C.IsGamePaused() and C.GetPlayerControlledShipID() ~= 0 then
            req_counter = req_counter + 1
            setup_req_id = 'setup-' .. tostring(req_counter)
            phase = 'setup_wait'
            AddUITriggeredEvent('X4AuthorityCockpitVelProbe', 'Setup', {
                request_id = setup_req_id, epoch = cfg.epoch,
            })
        end
        return
    end
    if phase == 'saving' then
        if now - save_started_at >= 4.0 and not C.IsGamePaused() then
            phase = 'ready'
            local hud = read_hud(remote_u64)
            local payload = {
                own_actor = world.own_actor, own_code = world.own_code,
                remote_actor = world.remote_actor, remote_code = world.remote_code,
                remote_sector = world.sector_macro, remote_zone = world.remote_zone,
                basis_error = world.basis_error,
                remote_xyz = world.remote_xyz, own_xyz = world.own_xyz,
                right = world.right, up = world.up, forward = world.forward,
                ffi_xyz = hud.ffi_xyz, ffi_rot = hud.ffi_rot,
                x4_version = hud.x4_version,
            }
            log('ready', payload)
        end
        return
    end
    if phase == 'ready' then
        if stream then
            stream:tick(not C.IsGamePaused())
        end
        if active_cmd then
            step_cmd()
        else
            local cmd = read_command_file()
            if cmd then
                last_cmd_seq = cmd.seq
                begin_cmd(cmd)
            end
        end
    end
end

SetScript('onUpdate', function()
    local ok, err = pcall(on_frame)
    if not ok and not failed then
        failed = true
        log('error', {reason = 'lua_exception', detail = tostring(err)})
    end
end)
