-- Opt-in native Windows X4 9.00 multi-pilot harness exercising real X4LAN_COMPANY_COCKPIT_OBSERVER.new_multi_x4 +
-- X4LAN_COMPANY_OBSERVER_STREAM + X4LAN_COMPANY_COCKPIT_PLAYBACK.new_multi + X4LAN_COMPANY_HULL_PLAYBACK_X4
-- for up to 3 other Company pilots in one cockpit + independent map slot over real loopback UDP.
local cfg = X4NEON_COMPANY_COCKPIT_MULTI
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

local EXT_DIR = './extensions/' .. tostring(cfg.extension_id or 'x4_neon_company_cockpit_multi') .. '/'
local resolve = assert(X4LAN_RESOLVE_STRING, 'X4LAN_RESOLVE_STRING required')

local function log(msg)
    DebugError('[X4LAN] AUTH_COMPANY_COCKPIT_MULTI ' .. msg)
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
    return ('x=%.4f y=%.4f z=%.4f'):format(p.x, p.y, p.z)
end

local state = {
    stage = 'initial',
    epoch = cfg.epoch,
    reg_reply = nil,
    probe_reply = nil,
    own_actor = nil, own_code = nil, own_init_pose = nil, home_sector = nil,
    pilots = {}, -- keyed by index 2, 3, 4: {actor, hex, code}
    map_actor = nil, map_hex = nil, map_code = nil, map_sector = nil, map_enabled = false,
    multi_observer = nil,
    playback_ctrl = nil,
    stream = nil,
    udp_net = nil,
    mono_offset = 0.0, last_mono = 0.0, clock_fn = nil,
    next_seq = 1, active_req = nil, last_poll = 0,
    rb = {
        [2] = {count = 0, max_err = 0.0, min_elapsed = 999.0, align_err = -1.0, align_elapsed = -1.0},
        [3] = {count = 0, max_err = 0.0, min_elapsed = 999.0, align_err = -1.0, align_elapsed = -1.0},
        [4] = {count = 0, max_err = 0.0, min_elapsed = 999.0, align_err = -1.0, align_elapsed = -1.0},
    },
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

local function emit_resp(seq, status, extra)
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    local rt = state.clock_fn and state.clock_fn() or mono_clock()
    log(('RESP seq=%d status=%s game_time=%.6f real_time=%.6f %s'):format(
        seq, status, gt, rt, extra or ''))
end

local function finish_req(seq, status, extra)
    emit_resp(seq, status, extra)
    state.active_req = nil
    state.next_seq = seq + 1
end

local function find_slot_for_index(idx)
    if not state.multi_observer then return 'none', 'none' end
    local b = state.multi_observer:bindings()
    for _, sname in ipairs({'cockpit', 'cockpit2', 'cockpit3'}) do
        local row = b[sname]
        if row and row.index == idx then
            return sname, row.mode or 'none'
        end
    end
    return 'none', 'none'
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

    local pinned = {epoch = state.epoch, nonce = cfg.nonce, issued_count = 5}
    local function available()
        return C.IsPlayerValid() and not C.IsGamePaused()
    end

    -- Real production multi-pilot observer (up to 3 sticky slots: cockpit, cockpit2, cockpit3)
    state.multi_observer = assert(X4LAN_COMPANY_COCKPIT_OBSERVER.new_multi_x4(pinned, 1, available, {
        max_pilots = 3,
        clock = clock_fn,
    }))

    -- Independent map slot reader observing index 5 in a separate sector
    local map_binding = {
        epoch = state.epoch, index = 5, sector_macro = state.map_sector,
        actor = state.map_hex, code = state.map_code,
    }
    local map_reader = {
        tick = function() return state.map_enabled and map_binding or nil end,
        matches = function(_, ep, sec, idx, act, code)
            return state.map_enabled and ep == state.epoch and sec == state.map_sector
                and idx == 5 and act == state.map_hex and code == state.map_code
        end,
        close = function() end,
    }

    -- Real production per-slot native adapters + multi-pilot playback controller
    local adapters = {}
    for _, sname in ipairs({'cockpit', 'cockpit2', 'cockpit3'}) do
        adapters[sname] = assert(X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4({
            enabled = true, pinned = pinned, own_index = 1,
            observer = state.multi_observer.readers[sname], guard = available,
        }))
    end
    state.playback_ctrl = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK.new_multi({
        enabled = true,
        slot_names = {'cockpit', 'cockpit2', 'cockpit3'},
        own_index = 1,
        allow_reacquire_align = true,
        adapters = adapters,
        clock = clock_fn,
        game = function() return tonumber(C.GetCurrentGameTime()) or 0 end,
        paused = function() return C.IsGamePaused() end,
        record = function(ev)
            local rec = state.rb[ev.index]
            if rec and (ev.kind == 'readback' or ev.kind == 'alignment_readback') then
                rec.count = rec.count + 1
                if ev.error > rec.max_err then rec.max_err = ev.error end
                if ev.elapsed < rec.min_elapsed then rec.min_elapsed = ev.elapsed end
                if ev.kind == 'alignment_readback' then
                    rec.align_err = ev.error
                    rec.align_elapsed = ev.elapsed
                    log(('ALIGN_READBACK slot=%s index=%d seq=%d elapsed=%.6f error=%.6f %s'):format(
                        tostring(ev.slot), ev.index, ev.source_sequence, ev.elapsed, ev.error, fmt_pose(ev.actual)))
                end
            end
            return true
        end,
        fault = function(reason, _, _, slot_name)
            log(('PLAYBACK_FAULT slot=%s reason=%s'):format(tostring(slot_name), tostring(reason)))
        end,
        on_event = function(kind, detail)
            local kv = {}
            for k, v in pairs(detail or {}) do kv[#kv + 1] = tostring(k) .. '=' .. tostring(v) end
            table.sort(kv)
            log('PLAYBACK_EVENT kind=' .. tostring(kind) .. ' ' .. table.concat(kv, ' '))
        end,
    }))

    local readers = {map = map_reader}
    for sname, reader in pairs(state.multi_observer.readers) do
        readers[sname] = reader
    end
    local prefix = 'C|' .. cfg.udp_lease .. '|' .. cfg.udp_generation .. '|'
    state.stream = assert(X4LAN_COMPANY_OBSERVER_STREAM.new(
        readers, state.epoch, state.udp_net, prefix, clock_fn, state.playback_ctrl
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
        if op == 'start_stream' then
            init_production_stack(req.clock_base)
            finish_req(req.seq, 'stream_started', 'started=true')
            return
        elseif op == 'lock_hud' then
            local idx = req.index or 2
            local actor = assert(state.pilots[idx]).actor
            assert(C.SetSofttarget(actor, ''), 'SetSofttarget failed')
            local tgt = GetPlayerTarget()
            local tgt_u64 = tgt and resolve.actor_u64(ConvertIDTo64Bit(tgt)) or nil
            local scr = C.GetComponentScreenPosition(actor, 32, 32)
            local dist = tonumber(C.GetDistanceBetween(state.own_actor, actor)) or -1
            finish_req(req.seq, 'hud_locked', ('index=%d locked=%s on_screen=%s dist=%.2f'):format(
                idx, tostring(tgt_u64 == actor), tostring(scr.onScreen), dist))
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
        elseif op == 'remove_ship' then
            AddUITriggeredEvent('X4AuthorityCompanyCockpitMultiProbe', 'RemoveShip', {
                request_id = req.request_id, epoch = state.epoch, index = req.index or 2,
            })
            return
        elseif op == 'restore_ship' then
            AddUITriggeredEvent('X4AuthorityCompanyCockpitMultiProbe', 'RestoreShip', {
                request_id = req.request_id, epoch = state.epoch, index = req.index or 2,
                x = req.x, y = req.y, z = req.z,
            })
            return
        elseif op == 'probe_50m' then
            local ok, reason, dist = state.playback_ctrl:probe_step_guard(req.offset_m or 120.0, req.index or 2)
            finish_req(req.seq, 'probed_50m', ('index=%d ok=%s reason=%s dist=%.4f'):format(
                req.index or 2, tostring(ok), tostring(reason), tonumber(dist) or -1))
            return
        elseif op == 'inject_generation' then
            local idx = req.index or 2
            local sname = find_slot_for_index(idx)
            assert(sname ~= 'none', 'no active slot for index ' .. tostring(idx))
            local cur = state.multi_observer.readers[sname]:current()
            assert(cur, 'no active binding for generation check')
            local p = read_pose(state.pilots[idx].actor)
            local ok, reason = state.playback_ctrl:sample(cur, req.serial or 1, {
                version = 3, epoch = state.epoch, index = idx, sector_macro = state.home_sector,
                generation = req.generation, sequence = req.sample_seq or 999,
                x = p.x + 2.0, y = p.y, z = p.z, real_velocity = {x = 18.0, y = 4.0, z = 12.0},
                source_time = gt, capture_time = state.clock_fn(),
            }, state.clock_fn(), sname)
            finish_req(req.seq, 'gen_injected', ('index=%d slot=%s ok=%s reason=%s'):format(
                idx, sname, tostring(ok), tostring(reason)))
            return
        elseif op == 'probe_state' then
            local opose = read_pose(state.own_actor)
            local odisp = dist3(state.own_init_pose, opose)
            local tgt = GetPlayerTarget()
            local total = state.playback_ctrl and state.playback_ctrl:stats() or {slots = {}}
            local parts = {
                ('has_target=%s own_disp_m=%.4f total_writes=%d total_readbacks=%d'):format(
                    tostring(tgt ~= nil), odisp, total.writes or 0, total.readbacks or 0),
            }
            for _, idx in ipairs({2, 3, 4}) do
                local sname, mode = find_slot_for_index(idx)
                local p = read_pose(state.pilots[idx].actor)
                local sst = (sname ~= 'none' and total.slots[sname]) or {}
                local rrec = state.rb[idx]
                parts[#parts + 1] = (
                    'p%d_slot=%s p%d_mode=%s p%d_x=%.4f p%d_y=%.4f p%d_z=%.4f ' ..
                    'p%d_rb=%d p%d_max_err=%.5f p%d_min_el=%.5f p%d_align_err=%.6f p%d_align_el=%.6f'
                ):format(
                    idx, sname, idx, mode, idx, p.x, idx, p.y, idx, p.z,
                    idx, rrec.count, idx, rrec.max_err,
                    idx, rrec.min_elapsed < 900 and rrec.min_elapsed or -1,
                    idx, rrec.align_err, idx, rrec.align_elapsed)
                if sname ~= 'none' then
                    parts[#parts + 1] = (
                        'p%d_writes=%d p%d_stale=%d p%d_align=%d p%d_gen_rej=%d p%d_ref50=%d p%d_wd=%d'
                    ):format(
                        idx, sst.writes or 0, idx, sst.stale_cutoffs or 0, idx, sst.alignments or 0,
                        idx, sst.generation_rejections or 0, idx, sst.refusals_over_50m or 0, idx, sst.withdrawals or 0)
                end
            end
            for _, sname in ipairs({'cockpit', 'cockpit2', 'cockpit3'}) do
                local sst = total.slots[sname] or {}
                parts[#parts + 1] = (
                    '%s_writes=%d %s_rb=%d %s_stale=%d %s_wd=%d %s_align=%d %s_gen_rej=%d %s_ref50=%d'
                ):format(
                    sname, sst.writes or 0, sname, sst.readbacks or 0, sname, sst.stale_cutoffs or 0,
                    sname, sst.withdrawals or 0, sname, sst.alignments or 0,
                    sname, sst.generation_rejections or 0, sname, sst.refusals_over_50m or 0)
            end
            finish_req(req.seq, 'probed', table.concat(parts, ' '))
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
    if parts[2] == 'Removed' then
        finish_req(req.seq, 'removed', ('index=%s sector=%s'):format(parts[3], parts[4]))
    elseif parts[2] == 'Restored' then
        local idx = tonumber(parts[3])
        local p = read_pose(state.pilots[idx].actor)
        finish_req(req.seq, 'restored', ('index=%s sector=%s %s'):format(parts[3], parts[4], fmt_pose(p)))
    elseif parts[2] == 'Readback' then
        local idx = tonumber(parts[3])
        local p = read_pose(state.pilots[idx].actor)
        local opose = read_pose(state.own_actor)
        local odisp = dist3(state.own_init_pose, opose)
        local rrec = state.rb[idx]
        finish_req(req.seq, 'readback', (
            'index=%s actor_hex=%s sector_macro=%s idcode=%s %s own_disp_m=%.4f ' ..
            'rb_count=%d max_err=%.5f min_elapsed=%.5f'
        ):format(
            parts[3], parts[4], parts[7], parts[8], fmt_pose(p), odisp,
            rrec.count, rrec.max_err, rrec.min_elapsed < 900 and rrec.min_elapsed or -1))
    else
        error('probe refused: ' .. raw)
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
        local p2 = read_pose(state.pilots[2].actor)
        local p3 = read_pose(state.pilots[3].actor)
        local p4 = read_pose(state.pilots[4].actor)
        state.stage = 'ready'
        log((
            'READY own_code=%s home_sector=%s ' ..
            'p2_code=%s p2_hex=%s p2_x=%.4f p2_y=%.4f p2_z=%.4f ' ..
            'p3_code=%s p3_hex=%s p3_x=%.4f p3_y=%.4f p3_z=%.4f ' ..
            'p4_code=%s p4_hex=%s p4_x=%.4f p4_y=%.4f p4_z=%.4f ' ..
            'map_code=%s map_hex=%s map_sector=%s'
        ):format(
            state.own_code, state.home_sector,
            state.pilots[2].code, state.pilots[2].hex, p2.x, p2.y, p2.z,
            state.pilots[3].code, state.pilots[3].hex, p3.x, p3.y, p3.z,
            state.pilots[4].code, state.pilots[4].hex, p4.x, p4.y, p4.z,
            state.map_code, state.map_hex, state.map_sector))
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
