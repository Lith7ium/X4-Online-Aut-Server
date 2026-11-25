-- Disposable opt-in native Windows X4 9.00 harness exercising the EXACT packaged
-- network/x4lan_net_64.dll (6ad97e04...) through the unmodified production
-- X4LAN_LOCAL_UDP_MODULE -> X4LAN_COMPANY_MOTION_SOURCE.new_x4 ->
-- X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER.new_x4 sender stack.
local cfg = X4NEON_WINDOWS_SENDER
if type(cfg) ~= 'table' or cfg.enabled ~= true then return end

local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
declare('typedef struct { float x; float y; float z; float yaw; float pitch; float roll; } UIPosRot;')
for _, text in ipairs({
    'bool IsStartmenu(void);',
    'bool IsPlayerValid(void);',
    'bool IsGamePaused(void);',
    'const char* GetSaveFolderPath(void);',
    'double GetCurrentGameTime(void);',
    'UniverseID GetPlayerControlledShipID(void);',
    'UIPosRot GetObjectPositionInSector(UniverseID objectid);',
}) do declare(text) end

local EXT_DIR = './extensions/x4_lan_lab/'
local resolve = assert(X4LAN_RESOLVE_STRING, 'X4LAN_RESOLVE_STRING required')

local function log(msg)
    DebugError('[X4LAN] AUTH_WIN_SENDER ' .. msg)
end

local function split(text, sep)
    sep = sep or '|'
    local parts = {}
    for item in (text .. sep):gmatch('(.-)' .. sep) do parts[#parts + 1] = item end
    return parts
end

local function check_profile()
    local path = C.GetSaveFolderPath()
    assert(path ~= nil and path ~= ffi.NULL, 'save folder unavailable')
    local text = ffi.string(path):gsub('\\', '/'):gsub('/+$', '')
    assert(text:match('/' .. tostring(cfg.profile) .. '/save$'), 'not disposable profile ' .. tostring(cfg.profile))
    return text
end

local function read_pose(actor)
    local p = C.GetObjectPositionInSector(actor)
    return {
        x = tonumber(p.x), y = tonumber(p.y), z = tonumber(p.z),
        yaw = tonumber(p.yaw), pitch = tonumber(p.pitch), roll = tonumber(p.roll),
    }
end

local function dist3(a, b)
    local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

local state = {
    stage = 'initial',
    epoch = cfg.epoch,
    reg_reply = nil,
    probe_reply = nil,
    own_hex = nil,
    own_actor = nil,
    own_code = nil,
    own_macro = nil,
    own_sector = nil,
    own_init_pose = nil,
    save_path = nil,
    udp_lib = nil,
    company_motion = nil,
    next_seq = 1,
    active_req = nil,
    last_poll = 0,
    tick_count = 0,
    mono_first = nil,
    mono_last = nil,
    mono_regressions = 0,
}

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.reg_reply = tostring(value)
end)

RegisterEvent('X4AuthorityWindowsSenderProbe.Result', function(_, value)
    state.probe_reply = tostring(value)
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
    local mt = state.udp_lib and state.udp_lib.monotonic() or -1
    log(('RESP seq=%d status=%s game_time=%.6f dll_monotonic=%.9f %s'):format(
        seq, status, gt, mt, extra or ''))
end

local function finish_req(seq, status, extra)
    emit_resp(seq, status, extra)
    state.active_req = nil
    state.next_seq = seq + 1
end

local function start_production_sender()
    -- Exact production DLL loader: loads ./extensions/x4_lan_lab/native/x4lan_net_64.dll
    -- and asserts BOTH open and monotonic functions are exported!
    local lib = assert(X4LAN_LOCAL_UDP_MODULE.load(ffi.os, package.loadlib))
    local t1 = assert(lib.monotonic(), 'udp.monotonic returned nil')
    local t2 = assert(lib.monotonic(), 'udp.monotonic second call returned nil')
    assert(type(t1) == 'number' and t1 >= 0 and t1 < math.huge, 'invalid udp.monotonic t1')
    assert(type(t2) == 'number' and t2 >= t1 and t2 < math.huge, 'udp.monotonic regressed')
    state.udp_lib = lib
    state.mono_first = t1
    state.mono_last = t2

    local pinned = {epoch = state.epoch, nonce = cfg.nonce, issued_count = 1}
    -- Arm production X4LAN_COMPANY_MOTION_SOURCE with verified controlled ship actor
    X4LAN_COMPANY_MOTION_SOURCE.arm({
        controlled_actor = function() return state.own_hex end,
    })
    -- Exact production X4LAN_COMPANY_MOTION_SOURCE.new_x4 call!
    state.company_motion = assert(X4LAN_COMPANY_MOTION_SOURCE.new_x4(
        cfg.company_motion,
        cfg.save_path,
        pinned,
        lib,
        cfg.profile
    ))
    state.company_motion:set_running(true)
    return t1, t2
end

local function step_active_req()
    local req = state.active_req
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    if req.wait_until_game_time and gt < req.wait_until_game_time then return end
    if not req.dispatched then
        req.dispatched = true
        state.probe_reply = nil
        local op = req.op
        if op == 'start_sender' then
            local t1, t2 = start_production_sender()
            finish_req(req.seq, 'sender_started', (
                'ffi_os=%s open_type=%s monotonic_type=%s mono_t1=%.9f mono_t2=%.9f save_path_ok=true'
            ):format(ffi.os, type(state.udp_lib.open), type(state.udp_lib.monotonic), t1, t2))
            return
        elseif op == 'start_throttle' then
            AddUITriggeredEvent('X4AuthorityWindowsSenderProbe', 'StartThrottle', {
                request_id = req.request_id, epoch = state.epoch,
            })
            return
        elseif op == 'stop_throttle' then
            AddUITriggeredEvent('X4AuthorityWindowsSenderProbe', 'StopThrottle', {
                request_id = req.request_id, epoch = state.epoch,
            })
            return
        elseif op == 'probe_pose' then
            local p = read_pose(state.own_actor)
            local disp = dist3(state.own_init_pose, p)
            local mt = state.udp_lib and state.udp_lib.monotonic() or -1
            finish_req(req.seq, 'probed', (
                'x=%.6f y=%.6f z=%.6f yaw=%.6f pitch=%.6f roll=%.6f disp_m=%.6f ' ..
                'ticks=%d mono_now=%.9f mono_regressions=%d'
            ):format(p.x, p.y, p.z, p.yaw, p.pitch, p.roll, disp,
                     state.tick_count, mt, state.mono_regressions))
            return
        elseif op == 'done' then
            if state.company_motion then state.company_motion:set_running(false) end
            finish_req(req.seq, 'done', 'stopped=true')
            state.stage = 'done'
            log('DONE')
            return
        else
            error('unknown op: ' .. tostring(op))
        end
    end

    if not state.probe_reply then return end
    local raw = state.probe_reply
    state.probe_reply = nil
    local parts = split(raw, '|')
    if parts[1] ~= req.request_id then error('md correlation mismatch: ' .. raw) end
    local p = read_pose(state.own_actor)
    local disp = dist3(state.own_init_pose, p)
    if parts[2] == 'ThrottleStarted' then
        finish_req(req.seq, 'throttle_started', ('speed=%s x=%.6f y=%.6f z=%.6f disp_m=%.6f'):format(
            parts[4], p.x, p.y, p.z, disp))
    elseif parts[2] == 'ThrottleStopped' then
        finish_req(req.seq, 'throttle_stopped', ('speed=%s x=%.6f y=%.6f z=%.6f disp_m=%.6f'):format(
            parts[4], p.x, p.y, p.z, disp))
    else
        error('probe refused: ' .. raw)
    end
end

local function frame()
    if state.stage == 'done' or state.stage == 'failed' then return end
    local t = getElapsedTime()
    if state.stage == 'initial' then
        state.save_path = check_profile()
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
        state.stage = 'setup_ship'
        AddUITriggeredEvent('X4AuthorityWindowsSenderProbe', 'Setup', {
            request_id = 'setup-1', epoch = state.epoch,
        })
        return
    end
    if state.stage == 'setup_ship' then
        if not state.probe_reply then return end
        local raw = state.probe_reply
        state.probe_reply = nil
        local parts = split(raw, '|')
        assert(parts[1] == 'setup-1' and parts[2] == 'Ready', 'setup failed: ' .. raw)
        state.own_hex = parts[3]
        state.own_actor = assert(resolve.parse_hex(parts[3]), 'invalid own actor hex')
        state.own_code = parts[4]
        state.own_macro = parts[5]
        state.own_sector = parts[6]
        state.own_init_pose = read_pose(state.own_actor)
        local p = state.own_init_pose
        state.stage = 'ready'
        log((
            'READY own_hex=%s own_code=%s own_macro=%s own_sector=%s ' ..
            'own_x=%.6f own_y=%.6f own_z=%.6f'
        ):format(
            state.own_hex, state.own_code, state.own_macro, state.own_sector,
            p.x, p.y, p.z))
        return
    end

    if state.company_motion then
        local now = state.udp_lib.monotonic()
        if state.mono_last and now < state.mono_last then
            state.mono_regressions = state.mono_regressions + 1
        end
        state.mono_last = now
        state.tick_count = state.tick_count + 1
        state.company_motion:tick()
    end
    if state.active_req then
        step_active_req()
        return
    end
    if t - state.last_poll < 0.03 then return end
    state.last_poll = t
    local req = load_request(state.next_seq)
    if req then
        if req.min_game_delay and req.min_game_delay > 0 then
            req.wait_until_game_time = (tonumber(C.GetCurrentGameTime()) or 0) + req.min_game_delay
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
