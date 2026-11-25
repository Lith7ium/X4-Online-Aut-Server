-- Disposable opt-in native Windows X4 9.00 harness for the bounded d0756d51
-- indexed cockpit observer trial: keeps a third Company ship (index=3) locked
-- on the stock HUD while the reserved remote Company ship (index=2) moves
-- within radar range, using the exact production d0756d51 observer, playback,
-- AUTH_COMPANY_COCKPIT_READBACK (with view serial), and
-- AUTH_COMPANY_COCKPIT_WITHDRAWAL modules unmodified.
local cfg = X4NEON_COMPANY_INDEXED_HUD
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
    'bool SetSofttarget(UniverseID componentid, const char* const connectionname);',
    'ScreenPos GetComponentScreenPosition(UniverseID componentid, uint32_t width, uint32_t height);',
    'float GetDistanceBetween(UniverseID componentid1, UniverseID componentid2);',
}) do declare(text) end

local EXT_DIR = './extensions/' .. tostring(cfg.extension_id or 'x4_neon_indexed_hud') .. '/'
local resolve = assert(X4LAN_RESOLVE_STRING, 'X4LAN_RESOLVE_STRING required')

local function log(msg)
    DebugError('[X4LAN] AUTH_INDEXED_HUD ' .. msg)
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

local function fmt_pose(p)
    return ('x=%.6f y=%.6f z=%.6f'):format(p.x, p.y, p.z)
end

local state = {
    stage = 'initial',
    epoch = cfg.epoch,
    reg_reply = nil,
    probe_reply = nil,
    own_actor = nil, own_code = nil, own_hex = nil, own_init_pose = nil, home_sector = nil,
    pilots = {}, -- keyed by index 2, 3, 4: {actor, hex, code, init_pose}
    map_actor = nil, map_hex = nil, map_code = nil, map_sector = nil,
    cockpit_reader = nil,
    map_reader = nil,
    playback_ctrl = nil,
    stream = nil,
    udp_net = nil,
    mono_offset = 0.0,
    last_mono = 0.0,
    clock_fn = nil,
    clock_source = 'none',
    next_seq = 1,
    active_req = nil,
    last_poll = 0,
    max_frame_gap_s = 0.0,
    last_tick_qpc = nil,
    tick_count = 0,
}

local function mono_clock()
    local v = state.mono_offset + GetCurRealTime()
    if v < state.last_mono then v = state.last_mono end
    state.last_mono = v
    return v
end

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.reg_reply = tostring(value)
end)

RegisterEvent('X4AuthorityCompanyCockpitMultiProbe.Result', function(_, value)
    state.probe_reply = tostring(value)
end)

local function load_request(seq)
    local ok, chunk = pcall(loadfile, EXT_DIR .. 'req_' .. tostring(seq) .. '.lua')
    if not (ok and chunk) then return nil end
    local rok, val = pcall(chunk)
    if rok and type(val) == 'table' and val.seq == seq then return val end
    return nil
end

local function format_hex_u64(u64)
    local divisor = ffi.new('uint64_t', 0x100000000ULL)
    local hi = tonumber(u64 / divisor)
    local lo = tonumber(u64 % divisor)
    if hi > 0 then
        return ('0x%x%08x'):format(hi, lo)
    end
    return ('0x%x'):format(lo)
end

local function current_hud_target()
    local tgt = GetPlayerTarget()
    if not tgt then return 'none', 'none', 0 end
    local u64 = resolve.actor_u64(ConvertIDTo64Bit(tgt))
    if not u64 then return 'none', 'none', 0 end
    local hex = format_hex_u64(u64)
    local code_ptr = C.GetObjectIDCode(u64)
    local code = (code_ptr ~= nil and code_ptr ~= ffi.NULL) and ffi.string(code_ptr) or 'unknown'
    local idx = 0
    if u64 == state.own_actor then
        idx = 1
        hex = state.own_hex or hex
    else
        for k, info in pairs(state.pilots) do
            if info.actor == u64 then
                idx = k
                hex = info.hex or hex
                break
            end
        end
    end
    return hex, code, idx
end

local function emit_resp(seq, status, extra)
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    local rt = state.clock_fn and state.clock_fn() or GetCurRealTime()
    log(('RESP seq=%d status=%s game_time=%.6f clock=%.6f %s'):format(
        seq, status, gt, rt, extra or ''))
end

local function finish_req(seq, status, extra)
    emit_resp(seq, status, extra)
    state.active_req = nil
    state.next_seq = seq + 1
end

local function init_production_stack(clock_base)
    local dll_path = EXT_DIR .. 'native/x4lan_net_64.dll'
    local open_fn, err = package.loadlib(dll_path, 'luaopen_x4lan_net')
    assert(type(open_fn) == 'function', 'failed loading x4lan_net_64.dll: ' .. tostring(err))
    local udp = open_fn()
    assert(type(udp) == 'table' and type(udp.open) == 'function', 'invalid x4lan_net module')
    state.udp_net = assert(udp.open('127.0.0.1', assert(cfg.udp_port, 'udp_port required')))

    if type(udp.monotonic) == 'function' then
        state.clock_fn = udp.monotonic
        state.clock_source = 'udp_monotonic'
    else
        state.mono_offset = (tonumber(clock_base) or 0) - GetCurRealTime() + 0.05
        state.last_mono = state.mono_offset + GetCurRealTime()
        state.clock_fn = mono_clock
        state.clock_source = 'GetCurRealTime_qpc_aligned'
    end

    local pinned = {epoch = state.epoch, nonce = cfg.nonce, issued_count = 5}
    local function available()
        return C.IsPlayerValid() and not C.IsGamePaused()
    end

    -- Exact d0756d51 production indexed cockpit observer (default on_withdrawal logs AUTH_COMPANY_COCKPIT_WITHDRAWAL)
    state.cockpit_reader = assert(X4LAN_COMPANY_COCKPIT_OBSERVER.new_x4(pinned, 1, available, {
        radar_fallback = true,
        target_index = 2,
        clock = state.clock_fn,
    }))

    -- Exact production map observer (loaded with full production module set)
    state.map_reader = assert(X4LAN_COMPANY_OBSERVER_MAP.new_x4(pinned, 1, available))

    -- Exact d0756d51 production playback controller (make_x4_record logs AUTH_COMPANY_COCKPIT_READBACK with serial)
    state.playback_ctrl = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK.new_x4(
        {index = 1, playback_index = 2},
        pinned, state.cockpit_reader, available, state.clock_fn
    ))

    local prefix = 'C|' .. cfg.udp_lease .. '|' .. cfg.udp_generation .. '|'
    state.stream = assert(X4LAN_COMPANY_OBSERVER_STREAM.new(
        {cockpit = state.cockpit_reader, map = state.map_reader},
        state.epoch, state.udp_net, prefix, state.clock_fn, state.playback_ctrl
    ))
end

local function step_active_req()
    local req = state.active_req
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    if req.wait_until_game_time and gt < req.wait_until_game_time then return end
    if not req.dispatched then
        req.dispatched = true
        state.probe_reply = nil
        local op = req.op
        if op == 'lock_hud' then
            local idx = req.index or 3
            local actor = assert(state.pilots[idx]).actor
            assert(C.SetSofttarget(actor, ''), 'SetSofttarget failed')
            local hex, code, hud_idx = current_hud_target()
            local scr = C.GetComponentScreenPosition(actor, 32, 32)
            local dist = tonumber(C.GetDistanceBetween(state.own_actor, actor)) or -1
            finish_req(req.seq, 'hud_locked', (
                'requested_index=%d locked=%s hud_index=%d hud_hex=%s hud_code=%s on_screen=%s dist_m=%.3f'
            ):format(idx, tostring(hud_idx == idx), hud_idx, hex, code, tostring(scr.onScreen), dist))
            return
        elseif op == 'start_stream' then
            init_production_stack(req.clock_base)
            local hex, code, hud_idx = current_hud_target()
            finish_req(req.seq, 'stream_started', (
                'started=true clock_source=%s hud_index=%d hud_hex=%s hud_code=%s'
            ):format(state.clock_source, hud_idx, hex, code))
            return
        elseif op == 'probe_state' then
            local opose = read_pose(state.own_actor)
            local p2 = read_pose(state.pilots[2].actor)
            local p3 = read_pose(state.pilots[3].actor)
            local own_disp = dist3(state.own_init_pose, opose)
            local p2_disp = dist3(state.pilots[2].init_pose, p2)
            local p3_disp = dist3(state.pilots[3].init_pose, p3)
            local hex, code, hud_idx = current_hud_target()
            local cur = state.cockpit_reader and state.cockpit_reader:current() or nil
            local mode = state.cockpit_reader and state.cockpit_reader:mode() or 'none'
            local st = state.playback_ctrl and state.playback_ctrl:stats() or {}
            local dist2 = tonumber(C.GetDistanceBetween(state.own_actor, state.pilots[2].actor)) or -1
            finish_req(req.seq, 'probed', (
                'hud_index=%d hud_hex=%s hud_code=%s ' ..
                'obs_index=%d obs_code=%s obs_mode=%s ' ..
                'own_x=%.6f own_y=%.6f own_z=%.6f own_disp_m=%.6f ' ..
                'p2_x=%.6f p2_y=%.6f p2_z=%.6f p2_disp_m=%.6f p2_dist_m=%.3f ' ..
                'p3_x=%.6f p3_y=%.6f p3_z=%.6f p3_disp_m=%.6f ' ..
                'binds=%d writes=%d readbacks=%d withdrawals=%d stale=%d alignments=%d ' ..
                'ticks=%d max_frame_gap_s=%.6f'
            ):format(
                hud_idx, hex, code,
                cur and cur.index or 0, cur and cur.code or 'none', tostring(mode),
                opose.x, opose.y, opose.z, own_disp,
                p2.x, p2.y, p2.z, p2_disp, dist2,
                p3.x, p3.y, p3.z, p3_disp,
                st.binds or 0, st.writes or 0, st.readbacks or 0,
                st.withdrawals or 0, st.stale_cutoffs or 0, st.alignments or 0,
                state.tick_count, state.max_frame_gap_s))
            return
        elseif op == 'readback' then
            AddUITriggeredEvent('X4AuthorityCompanyCockpitMultiProbe', 'Readback', {
                request_id = req.request_id, epoch = state.epoch, index = req.index or 2,
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

    if not state.probe_reply then return end
    local raw = state.probe_reply
    state.probe_reply = nil
    local parts = split(raw, '|')
    if parts[1] ~= req.request_id then error('md correlation mismatch: ' .. raw) end
    assert(parts[2] == 'Readback', 'readback refused: ' .. raw)
    local idx = tonumber(parts[3])
    local p = read_pose(state.pilots[idx].actor)
    local disp = dist3(state.pilots[idx].init_pose, p)
    local opose = read_pose(state.own_actor)
    local odisp = dist3(state.own_init_pose, opose)
    local hex, code, hud_idx = current_hud_target()
    finish_req(req.seq, 'readback', (
        'index=%s actor_hex=%s sector_macro=%s idcode=%s %s disp_m=%.6f ' ..
        'own_x=%.6f own_y=%.6f own_z=%.6f own_disp_m=%.6f ' ..
        'hud_index=%d hud_hex=%s hud_code=%s'
    ):format(
        parts[3], parts[4], parts[7], parts[8], fmt_pose(p), disp,
        opose.x, opose.y, opose.z, odisp, hud_idx, hex, code))
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
        AddUITriggeredEvent('X4AuthorityCompanyCockpitMultiProbe', 'Setup', {
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
        state.own_hex = parts[3]
        state.own_actor = assert(resolve.parse_hex(parts[3]), 'invalid own actor hex')
        state.own_code = parts[4]
        state.home_sector = parts[5]
        state.pilots[2] = {hex = parts[6], actor = assert(resolve.parse_hex(parts[6])), code = parts[7]}
        state.pilots[3] = {hex = parts[8], actor = assert(resolve.parse_hex(parts[8])), code = parts[9]}
        state.pilots[4] = {hex = parts[10], actor = assert(resolve.parse_hex(parts[10])), code = parts[11]}
        state.map_hex = parts[12]
        state.map_actor = assert(resolve.parse_hex(parts[12]))
        state.map_code = parts[13]
        state.map_sector = parts[14]
        state.own_init_pose = read_pose(state.own_actor)
        for idx = 2, 4 do
            state.pilots[idx].init_pose = read_pose(state.pilots[idx].actor)
        end
        local own_p = state.own_init_pose
        local p2 = state.pilots[2].init_pose
        local p3 = state.pilots[3].init_pose
        state.stage = 'ready'
        log((
            'READY own_code=%s own_hex=%s home_sector=%s ' ..
            'own_x=%.6f own_y=%.6f own_z=%.6f ' ..
            'p2_code=%s p2_hex=%s p2_x=%.6f p2_y=%.6f p2_z=%.6f ' ..
            'p3_code=%s p3_hex=%s p3_x=%.6f p3_y=%.6f p3_z=%.6f'
        ):format(
            state.own_code, state.own_hex, state.home_sector,
            own_p.x, own_p.y, own_p.z,
            state.pilots[2].code, state.pilots[2].hex, p2.x, p2.y, p2.z,
            state.pilots[3].code, state.pilots[3].hex, p3.x, p3.y, p3.z))
        return
    end

    if state.stream then
        local now = state.clock_fn()
        if state.last_tick_qpc ~= nil then
            local gap = now - state.last_tick_qpc
            if gap > state.max_frame_gap_s then state.max_frame_gap_s = gap end
        end
        state.last_tick_qpc = now
        state.tick_count = state.tick_count + 1
        state.stream:tick(not C.IsGamePaused())
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
