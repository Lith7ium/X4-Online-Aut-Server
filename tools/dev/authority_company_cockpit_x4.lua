-- Opt-in native Windows X4 9.00 harness exercising real X4LAN_COMPANY_COCKPIT_OBSERVER +
-- X4LAN_COMPANY_OBSERVER_STREAM + X4LAN_COMPANY_COCKPIT_PLAYBACK + X4LAN_COMPANY_HULL_PLAYBACK_X4
-- over real loopback UDP (x4lan_net_64.dll) with HUD target, untargeted radar-visible pilot,
-- independent map slot, 5-second loss cutoff, generation checks, reacquisition, and readback.
local cfg = X4NEON_COMPANY_COCKPIT
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

local EXT_DIR = './extensions/' .. tostring(cfg.extension_id or 'x4_neon_company_cockpit') .. '/'
local resolve = assert(X4LAN_RESOLVE_STRING, 'X4LAN_RESOLVE_STRING required')

local function log(msg)
    DebugError('[X4LAN] AUTH_COMPANY_COCKPIT ' .. msg)
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
    return ('x=%.4f y=%.4f z=%.4f yaw=%.4f pitch=%.4f roll=%.4f'):format(
        p.x, p.y, p.z, p.yaw, p.pitch, p.roll)
end

local state = {
    stage = 'initial',
    epoch = cfg.epoch,
    reg_reply = nil,
    probe_reply = nil,
    own_actor = nil,
    own_code = nil,
    own_init_pose = nil,
    remote_actor = nil,
    remote_hex = nil,
    remote_code = nil,
    remote_sector = nil,
    map_actor = nil,
    map_hex = nil,
    map_code = nil,
    map_sector = nil,
    map_enabled = false,
    cockpit_reader = nil,
    playback_ctrl = nil,
    stream = nil,
    udp_net = nil,
    mono_offset = 0.0,
    last_mono = 0.0,
    next_seq = 1,
    active_req = nil,
    last_poll = 0,
    readback_count = 0,
    last_readback_err = 0.0,
    max_readback_err = 0.0,
    min_readback_elapsed = 999.0,
    alignment_readback_err = -1.0,
    alignment_readback_elapsed = -1.0,
    events = {},
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

RegisterEvent('X4AuthorityCompanyCockpitProbe.Result', function(_, value)
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
    local rt = mono_clock()
    log(('RESP seq=%d status=%s game_time=%.6f real_time=%.6f %s'):format(
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

    local clock_fn
    if type(udp.monotonic) == 'function' then
        clock_fn = udp.monotonic
    else
        state.mono_offset = (tonumber(clock_base) or 0) - GetCurRealTime() + 0.05
        state.last_mono = state.mono_offset + GetCurRealTime()
        clock_fn = mono_clock
    end
    state.clock_fn = clock_fn

    local pinned = {epoch = state.epoch, nonce = cfg.nonce, issued_count = 3}
    local function available()
        return C.IsPlayerValid() and not C.IsGamePaused()
    end

    -- Real production X4LAN_COMPANY_COCKPIT_OBSERVER (HUD target first, radar fallback without lock!)
    state.cockpit_reader = assert(X4LAN_COMPANY_COCKPIT_OBSERVER.new_x4(pinned, 1, available, {
        radar_fallback = true,
        target_index = 2,
        clock = clock_fn,
    }))

    -- Independent map slot reader observing the 3rd Company ship in a separate sector
    local map_binding = {
        epoch = state.epoch, index = 3, sector_macro = state.map_sector,
        actor = state.map_hex, code = state.map_code,
    }
    local map_reader = {
        tick = function() return state.map_enabled and map_binding or nil end,
        matches = function(_, ep, sec, idx, act, code)
            return state.map_enabled and ep == state.epoch and sec == state.map_sector
                and idx == 3 and act == state.map_hex and code == state.map_code
        end,
        close = function() end,
    }

    -- Real production X4LAN_COMPANY_HULL_PLAYBACK_X4 + X4LAN_COMPANY_COCKPIT_PLAYBACK
    local adapter = assert(X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4({
        enabled = true, pinned = pinned, own_index = 1,
        observer = state.cockpit_reader, guard = available,
    }))
    state.playback_ctrl = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK.new({
        enabled = true,
        own_index = 1,
        target_index = 2,
        allow_reacquire_align = true,
        adapter = adapter,
        clock = clock_fn,
        game = function() return tonumber(C.GetCurrentGameTime()) or 0 end,
        paused = function() return C.IsGamePaused() end,
        record = function(ev)
            if ev.kind == 'readback' or ev.kind == 'alignment_readback' then
                state.readback_count = state.readback_count + 1
                state.last_readback_err = ev.error
                if ev.error > state.max_readback_err then state.max_readback_err = ev.error end
                if ev.elapsed < state.min_readback_elapsed then state.min_readback_elapsed = ev.elapsed end
                if ev.kind == 'alignment_readback' then
                    state.alignment_readback_err = ev.error
                    state.alignment_readback_elapsed = ev.elapsed
                    log(('ALIGN_READBACK seq=%d elapsed=%.6f error=%.6f %s'):format(
                        ev.source_sequence, ev.elapsed, ev.error, fmt_pose(ev.actual)))
                end
            end
            return true
        end,
        fault = function(reason)
            state.events[#state.events + 1] = 'fault:' .. tostring(reason)
            log('PLAYBACK_FAULT ' .. tostring(reason))
        end,
        on_event = function(kind, detail)
            state.events[#state.events + 1] = tostring(kind)
            local kv = {}
            for k, v in pairs(detail or {}) do kv[#kv + 1] = tostring(k) .. '=' .. tostring(v) end
            table.sort(kv)
            log('PLAYBACK_EVENT kind=' .. tostring(kind) .. ' ' .. table.concat(kv, ' '))
        end,
    }))

    -- Real production X4LAN_COMPANY_OBSERVER_STREAM over real loopback UDP!
    local prefix = 'C|' .. cfg.udp_lease .. '|' .. cfg.udp_generation .. '|'
    state.stream = assert(X4LAN_COMPANY_OBSERVER_STREAM.new(
        {cockpit = state.cockpit_reader, map = map_reader},
        state.epoch, state.udp_net, prefix, clock_fn, state.playback_ctrl
    ))
end

local function step_active_req()
    local req = state.active_req
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    if req.wait_until_game_time and gt < req.wait_until_game_time then
        return
    end
    if not req.dispatched then
        req.dispatched = true
        state.probe_reply = nil
        local op = req.op
        if op == 'start_stream' then
            init_production_stack(req.clock_base)
            finish_req(req.seq, 'stream_started', 'started=true')
            return
        elseif op == 'lock_hud' then
            assert(C.SetSofttarget(state.remote_actor, ''), 'SetSofttarget failed')
            local tgt = GetPlayerTarget()
            local tgt_u64 = tgt and resolve.actor_u64(ConvertIDTo64Bit(tgt)) or nil
            local scr = C.GetComponentScreenPosition(state.remote_actor, 32, 32)
            local dist = tonumber(C.GetDistanceBetween(state.own_actor, state.remote_actor)) or -1
            finish_req(req.seq, 'hud_locked', ('locked=%s on_screen=%s dist=%.2f'):format(
                tostring(tgt_u64 == state.remote_actor), tostring(scr.onScreen), dist))
            return
        elseif op == 'unlock_hud' then
            RemoveSofttarget()
            local tgt = GetPlayerTarget()
            finish_req(req.seq, 'hud_unlocked', 'has_target=' .. tostring(tgt ~= nil))
            return
        elseif op == 'set_map_enabled' then
            state.map_enabled = (req.enabled == true)
            finish_req(req.seq, 'map_set', 'map_enabled=' .. tostring(state.map_enabled))
            return
        elseif op == 'probe_50m' then
            local ok, reason, dist = state.playback_ctrl:probe_step_guard(req.offset_m or 120.0)
            finish_req(req.seq, 'probed_50m', ('ok=%s reason=%s dist=%.4f'):format(
                tostring(ok), tostring(reason), tonumber(dist) or -1))
            return
        elseif op == 'inject_generation' then
            -- Exercise strict generation check directly on playback controller via stream binding!
            local cur = state.cockpit_reader:current()
            assert(cur, 'no active cockpit binding for generation check')
            local p = read_pose(state.remote_actor)
            local ok, reason = state.playback_ctrl:sample(cur, req.serial or 2, {
                version = 3, epoch = state.epoch, index = 2, sector_macro = state.remote_sector,
                generation = req.generation, sequence = req.sample_seq or 999,
                x = p.x + 2.0, y = p.y, z = p.z, real_velocity = {x = 18.0, y = 4.0, z = 12.0},
                source_time = gt, capture_time = state.clock_fn(),
            }, state.clock_fn())
            finish_req(req.seq, 'gen_injected', ('ok=%s reason=%s'):format(tostring(ok), tostring(reason)))
            return
        elseif op == 'probe_state' then
            local rpose = read_pose(state.remote_actor)
            local opose = read_pose(state.own_actor)
            local odisp = dist3(state.own_init_pose, opose)
            local st = state.playback_ctrl and state.playback_ctrl:stats() or {}
            local tgt = GetPlayerTarget()
            local mode = state.cockpit_reader and state.cockpit_reader:mode() or 'none'
            finish_req(req.seq, 'probed', (
                'mode=%s has_target=%s %s own_disp_m=%.4f ' ..
                'binds=%d writes=%d readbacks=%d stale_cutoffs=%d alignments=%d ' ..
                'gen_rejects=%d refusals_50m=%d max_err=%.5f min_elapsed=%.5f ' ..
                'align_err=%.6f align_elapsed=%.6f'
            ):format(
                tostring(mode), tostring(tgt ~= nil), fmt_pose(rpose), odisp,
                st.binds or 0, st.writes or 0, st.readbacks or 0, st.stale_cutoffs or 0,
                st.alignments or 0, st.generation_rejections or 0, st.refusals_over_50m or 0,
                state.max_readback_err,
                state.min_readback_elapsed < 900 and state.min_readback_elapsed or -1,
                state.alignment_readback_err, state.alignment_readback_elapsed))
            return
        elseif op == 'readback' then
            AddUITriggeredEvent('X4AuthorityCompanyCockpitProbe', 'Readback', {
                request_id = req.request_id,
                epoch = state.epoch,
                index = 2,
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
    local rpose = read_pose(state.remote_actor)
    local opose = read_pose(state.own_actor)
    local odisp = dist3(state.own_init_pose, opose)
    local st = state.playback_ctrl and state.playback_ctrl:stats() or {}
    finish_req(req.seq, 'readback', (
        'index=%s actor_hex=%s sector_macro=%s idcode=%s %s own_disp_m=%.4f ' ..
        'writes=%d readbacks=%d stale_cutoffs=%d alignments=%d gen_rejects=%d refusals_50m=%d ' ..
        'max_err=%.5f min_elapsed=%.5f'
    ):format(
        parts[3], parts[4], parts[7], parts[8], fmt_pose(rpose), odisp,
        st.writes or 0, st.readbacks or 0, st.stale_cutoffs or 0, st.alignments or 0,
        st.generation_rejections or 0, st.refusals_over_50m or 0,
        state.max_readback_err,
        state.min_readback_elapsed < 900 and state.min_readback_elapsed or -1))
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
            request_id = 'init-1',
            epoch = state.epoch,
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
        AddUITriggeredEvent('X4AuthorityCompanyCockpitProbe', 'Setup', {
            request_id = 'setup-1',
            epoch = state.epoch,
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
        state.map_hex = parts[9]
        state.map_actor = assert(resolve.parse_hex(parts[9]), 'invalid map actor hex')
        state.map_code = parts[10]
        state.map_sector = parts[11]
        state.own_init_pose = read_pose(state.own_actor)
        local r0 = read_pose(state.remote_actor)
        state.stage = 'ready'
        log((
            'READY own_code=%s remote_code=%s remote_hex=%s remote_sector=%s ' ..
            'remote_x=%.4f remote_y=%.4f remote_z=%.4f ' ..
            'map_code=%s map_hex=%s map_sector=%s'
        ):format(
            state.own_code, state.remote_code, state.remote_hex, state.remote_sector,
            r0.x, r0.y, r0.z, state.map_code, state.map_hex, state.map_sector))
        return
    end

    if state.stream then
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
