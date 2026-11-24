-- Opt-in native Windows X4 9.00 authoritative NPC capture + admitted-client same-ship playback harness.
-- Runs ONLY in the launcher's disposable profile when X4NEON_NPC_MOTION.enabled == true.
local cfg = X4NEON_NPC_MOTION
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
    'bool IsSaveListLoadingComplete(void);',
    'void ReloadSaveList(void);',
    'const char* GetSaveFolderPath(void);',
    'double GetCurrentGameTime(void);',
    'UniverseID GetPlayerControlledShipID(void);',
    'UniverseID GetPlayerOccupiedShipID(void);',
    'UniverseID GetContextByClass(UniverseID componentid, const char* classname, bool includeself);',
    'bool IsValidComponent(UniverseID componentid);',
    'bool IsComponentClass(UniverseID componentid, const char* classname);',
    'bool IsComponentOperational(UniverseID componentid);',
    'const char* GetObjectIDCode(UniverseID objectid);',
    'UIPosRot GetObjectPositionInSector(UniverseID objectid);',
    'UniverseID GetZoneAt(UniverseID sectorid, UIPosRot* offset);',
    'void SetObjectSectorPos(UniverseID objectid, UniverseID sectorid, UIPosRot pos);',
    'int QueryPerformanceCounter(int64_t* lpPerformanceCount);',
    'int QueryPerformanceFrequency(int64_t* lpFrequency);',
}) do declare(text) end

local MODE = cfg.mode or 'capture'
local EXT_DIR = './extensions/' .. tostring(cfg.extension_id or 'x4_neon_npc_motion') .. '/'
local SAVE_SLOT = cfg.save_slot or 'save_009'

local qpc_k32, qpc_freq_num, qpc_buf
do
    local ok, lib = pcall(ffi.load, 'kernel32')
    if ok and lib then
        local f = ffi.new('int64_t[1]')
        if pcall(function() return lib.QueryPerformanceFrequency(f) end) and tonumber(f[0]) > 0 then
            qpc_k32 = lib
            qpc_freq_num = tonumber(f[0])
            qpc_buf = ffi.new('int64_t[1]')
        end
    end
end

local function log(msg)
    DebugError('[X4LAN] AUTH_NPC_MOTION ' .. msg)
end

local function realtime()
    if qpc_k32 then
        qpc_k32.QueryPerformanceCounter(qpc_buf)
        return tonumber(qpc_buf[0]) / qpc_freq_num
    end
    if type(GetCurRealTime) == 'function' then
        return GetCurRealTime()
    end
    return getElapsedTime()
end

local function id_text(v)
    return (tostring(ffi.cast('UniverseID', v)):gsub('ULL$', ''))
end

local function parse_hex_decimal(hex)
    if type(hex) ~= 'string' or #hex > 18 or not hex:match('^0[xX][0-9a-fA-F]+$') then return nil end
    local s = '0'
    for i = 3, #hex do
        local carry = tonumber(hex:sub(i, i), 16)
        local out = {}
        for j = #s, 1, -1 do
            local n = (s:byte(j) - 48) * 16 + carry
            out[#out + 1] = string.char(48 + n % 10)
            carry = math.floor(n / 10)
        end
        while carry > 0 do
            out[#out + 1] = string.char(48 + carry % 10)
            carry = math.floor(carry / 10)
        end
        s = table.concat(out):reverse():gsub('^0+', '')
        if s == '' then s = '0' end
    end
    return s ~= '0' and s or nil
end

local function check_profile()
    local path = C.GetSaveFolderPath()
    assert(path ~= nil and path ~= ffi.NULL, 'save folder unavailable')
    local text = ffi.string(path):gsub('\\', '/'):gsub('/+$', '')
    assert(text:match('/' .. tostring(cfg.profile) .. '/save$'), 'not disposable profile ' .. tostring(cfg.profile))
end

local function split(text, sep)
    sep = sep or '|'
    local parts = {}
    for item in (text .. sep):gmatch('(.-)' .. sep) do parts[#parts + 1] = item end
    return parts
end

local function read_pose(actor)
    local p = C.GetObjectPositionInSector(actor)
    return {
        x = tonumber(p.x), y = tonumber(p.y), z = tonumber(p.z),
        yaw = tonumber(p.yaw), pitch = tonumber(p.pitch), roll = tonumber(p.roll),
    }
end

local function read_player_pose()
    local pid = C.GetPlayerControlledShipID()
    if pid == 0 then pid = C.GetPlayerOccupiedShipID() end
    if pid ~= 0 and C.IsValidComponent(pid) then
        return read_pose(pid), id_text(pid)
    end
    return {x = 0, y = 0, z = 0, yaw = 0, pitch = 0, roll = 0}, '0'
end

local function fmt_pose(p)
    return ('x=%.4f y=%.4f z=%.4f yaw=%.4f pitch=%.4f roll=%.4f'):format(
        p.x, p.y, p.z, p.yaw, p.pitch, p.roll)
end

local function fmt_num(v)
    return ('%.17g'):format(v)
end

local state = {
    stage = 'initial',
    epoch = cfg.epoch,
    md_reply = nil,
    reg_reply = nil,
    game_saved = nil,
    total_registry = 0,
    candidates = {},
    by_index = {},
    scout_start_gt = nil,
    scout_poses = {},
    primary = nil,
    secondary = nil,
    saved_primary_pose = nil,
    saved_secondary_pose = nil,
    saved_gt = 0,
    player_initial_pose = nil,
    next_seq = 1,
    active_req = nil,
    last_poll = 0,
    pop_started = 0,
    pop_ms = 0,
    -- Capture trace state
    trace = nil,
    -- Observe mode UDP + playback state
    udp_mod = nil,
    udp_net = nil,
    udp_prefix = nil,
    mono_clock = realtime,
    udp_bound = false,
    udp_last_hello = 0,
    view_serials = {high_attention = 0, map = 0},
    npc_controller = nil,
    last_readback_sent = 0,
    readback_sent_count = 0,
    primary_actor_zone = nil,
    primary_actor_sector = nil,
}

RegisterEvent('X4AuthorityNpcMotion.Result', function(_, value)
    state.md_reply = tostring(value)
end)

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.reg_reply = tostring(value)
end)

RegisterEvent('gameSaved', function(_, success)
    state.game_saved = (success == true or success == 1)
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
    local rt = state.mono_clock()
    log(('RESP seq=%d status=%s game_time=%.6f real_time=%.6f %s'):format(
        seq, status, gt, rt, extra or ''))
end

local function finish_req(seq, status, extra)
    emit_resp(seq, status, extra)
    state.active_req = nil
    state.next_seq = seq + 1
end

local function sample_one(entry)
    local actor = entry.actor
    if not (C.IsValidComponent(actor) and C.IsComponentOperational(actor) and C.IsComponentClass(actor, 'ship')) then
        return nil
    end
    local sec = C.GetContextByClass(actor, 'sector', false)
    local zone = C.GetContextByClass(actor, 'zone', false)
    if sec == 0 or zone == 0 or not C.IsValidComponent(sec) or not C.IsValidComponent(zone) then
        return nil
    end
    local smacro = GetComponentData(ConvertStringToLuaID(id_text(sec)), 'macro')
    local p = read_pose(actor)
    return {
        index = entry.index,
        actor_key = entry.actor_key,
        idcode = entry.idcode,
        sector = sec,
        zone = zone,
        sector_key = id_text(sec),
        zone_key = id_text(zone),
        sector_macro = tostring(smacro),
        pose = p,
    }
end

-- ==================== OBSERVE MODE: UDP + X4LAN_NPC_HULL_PLAYBACK ====================

local function udp_send(payload)
    if state.udp_net and state.udp_prefix then
        return state.udp_net:send(state.udp_prefix .. payload)
    end
    return false
end

local function init_observe_playback()
    local dll_path = EXT_DIR .. 'native/x4lan_net_64.dll'
    local open_fn, err = package.loadlib(dll_path, 'luaopen_x4lan_net')
    assert(type(open_fn) == 'function', 'failed loading x4lan_net_64.dll: ' .. tostring(err))
    local mod = open_fn()
    assert(type(mod) == 'table' and type(mod.open) == 'function', 'invalid x4lan_net module')
    local mono = (type(mod.monotonic) == 'function') and mod.monotonic or realtime
    state.udp_mod = mod
    state.mono_clock = mono
    state.udp_net = assert(mod.open('127.0.0.1', assert(cfg.udp_port, 'udp_port required')))
    state.udp_prefix = 'N|' .. assert(cfg.udp_lease, 'udp_lease required') .. '|' .. state.epoch .. '|'

    local entry = assert(state.primary, 'primary ship not resolved')
    local sec = C.GetContextByClass(entry.actor, 'sector', false)
    local zone = C.GetContextByClass(entry.actor, 'zone', false)
    assert(sec ~= 0 and zone ~= 0, 'primary sector/zone unavailable')
    state.primary_actor_sector = sec
    state.primary_actor_zone = zone

    local function guard_fn()
        if not C.IsPlayerValid() or C.IsGamePaused() then return false end
        if not (C.IsValidComponent(entry.actor) and C.IsComponentOperational(entry.actor)
                and C.IsComponentClass(entry.actor, 'ship')) then
            return false
        end
        local cur_sec = C.GetContextByClass(entry.actor, 'sector', false)
        local cur_zone = C.GetContextByClass(entry.actor, 'zone', false)
        return cur_sec == state.primary_actor_sector and cur_zone == state.primary_actor_zone
    end

    local function write_xyz(target_pos)
        local cur_sec = C.GetContextByClass(entry.actor, 'sector', false)
        local cur_zone = C.GetContextByClass(entry.actor, 'zone', false)
        if cur_sec == 0 or cur_zone == 0 or cur_sec ~= state.primary_actor_sector then
            return false
        end
        local offset = ffi.new('UIPosRot[1]')
        offset[0].x, offset[0].y, offset[0].z = target_pos.x, target_pos.y, target_pos.z
        if C.GetZoneAt(cur_sec, offset) ~= cur_zone then
            return false
        end
        local actual = read_pose(entry.actor)
        C.SetObjectSectorPos(entry.actor, cur_sec, ffi.new('UIPosRot', {
            x = target_pos.x, y = target_pos.y, z = target_pos.z,
            yaw = actual.yaw, pitch = actual.pitch, roll = actual.roll,
        }))
        return true
    end

    assert(type(X4LAN_NPC_HULL_PLAYBACK) == 'table', 'X4LAN_NPC_HULL_PLAYBACK unavailable')
    state.npc_controller = assert(X4LAN_NPC_HULL_PLAYBACK.new({
        enabled = true,
        own_index = assert(cfg.own_index, 'own_index required'),
        allow_reacquire_align = true,
        binding = {
            epoch = state.epoch,
            index = entry.index,
            sector_macro = entry.sector_macro,
            actor = entry.actor_key,
            code = entry.idcode,
        },
        clock = mono,
        game = function() return tonumber(C.GetCurrentGameTime()) or 0 end,
        paused = function() return C.IsGamePaused() end,
        guard = guard_fn,
        read = function() return read_pose(entry.actor) end,
        write = function(_, target_pos, _) return write_xyz(target_pos) end,
        align = function(_, target_pos, _) return write_xyz(target_pos) end,
        record = function(ev)
            if ev.kind == 'readback' or ev.kind == 'alignment_readback' then
                local now = mono()
                local force_send = (ev.kind == 'alignment_readback' or state.readback_sent_count < 4
                    or (now - state.last_readback_sent >= 0.25))
                if force_send then
                    state.last_readback_sent = now
                    state.readback_sent_count = state.readback_sent_count + 1
                    udp_send(table.concat({
                        'READBACK', ev.kind, tostring(ev.index), ev.generation,
                        tostring(ev.source_sequence), tostring(ev.phase or 'active'),
                        fmt_num(ev.elapsed), fmt_num(ev.error),
                        fmt_num(ev.actual.x), fmt_num(ev.actual.y), fmt_num(ev.actual.z),
                        fmt_num(ev.target.x), fmt_num(ev.target.y), fmt_num(ev.target.z),
                        fmt_num(ev.game_time), fmt_num(ev.local_time),
                    }, '|'))
                end
            end
            return true
        end,
        fault = function(reason, uncertain, stage)
            udp_send('EVENT|fault|' .. tostring(entry.index) .. '|' .. tostring(reason)
                .. ',stage=' .. tostring(stage) .. ',uncertain=' .. tostring(uncertain))
        end,
        on_event = function(kind, bnd, detail)
            local kv = {}
            for k, v in pairs(detail or {}) do kv[#kv + 1] = tostring(k) .. '=' .. tostring(v) end
            table.sort(kv)
            udp_send('EVENT|' .. tostring(kind) .. '|' .. tostring(bnd.index) .. '|' .. table.concat(kv, ','))
        end,
    }))
end

local function drain_observe_udp()
    if not state.udp_net then return end
    local prefix = state.udp_prefix
    for _ = 1, 24 do
        local packet = state.udp_net:receive()
        if packet == nil then break end
        if packet:sub(1, #prefix) == prefix then
            local fields = split(packet:sub(#prefix + 1), '|')
            local kind = fields[1]
            if kind == 'ACK' and fields[2] == 'HELLO' then
                state.udp_bound = true
            elseif kind == 'REMOTE' and #fields == 14 then
                local idx = tonumber(fields[2])
                local gen = fields[3]
                local seq = tonumber(fields[4])
                local sec = fields[5]
                local x, y, z = tonumber(fields[6]), tonumber(fields[7]), tonumber(fields[8])
                local vx = fields[9] ~= '-' and tonumber(fields[9]) or nil
                local vy = fields[10] ~= '-' and tonumber(fields[10]) or nil
                local vz = fields[11] ~= '-' and tonumber(fields[11]) or nil
                local st, ct = tonumber(fields[12]), tonumber(fields[13])
                local entry = state.by_index[idx]
                if entry and C.IsValidComponent(entry.actor) and C.IsComponentOperational(entry.actor)
                        and sec == entry.sector_macro and x and y and z and st and ct and seq then
                    udp_send(('SEEN|%d|%s|%d'):format(idx, gen, seq))
                    if state.npc_controller and state.primary and idx == state.primary.index then
                        local vel = (vx and vy and vz) and {x = vx, y = vy, z = vz} or nil
                        state.npc_controller:push({
                            version = 3,
                            epoch = state.epoch,
                            generation = gen,
                            index = idx,
                            sequence = seq,
                            sector_macro = sec,
                            x = x, y = y, z = z,
                            real_velocity = vel,
                            source_time = st,
                            capture_time = ct,
                        }, state.mono_clock())
                    end
                end
            elseif kind == 'LOSS' and #fields == 4 then
                local idx = tonumber(fields[2])
                if state.npc_controller and state.primary and idx == state.primary.index then
                    state.npc_controller:transport_lost(fields[4])
                end
            elseif kind == 'END' and #fields == 4 then
                local idx = tonumber(fields[2])
                local reason = fields[4]
                if state.npc_controller and state.primary and idx == state.primary.index then
                    if reason == 'withdrawn' then
                        state.npc_controller:withdraw(reason)
                    else
                        state.npc_controller:end_track(reason)
                    end
                end
            elseif kind == 'PROBE_50M' and #fields >= 2 then
                local idx = tonumber(fields[2])
                local offset_m = tonumber(fields[3]) or 120.0
                if state.npc_controller and state.primary and idx == state.primary.index then
                    local actual = read_pose(state.primary.actor)
                    local ok, reason, dist = state.npc_controller:probe_step_guard({
                        x = actual.x + offset_m, y = actual.y, z = actual.z,
                    })
                    udp_send(('EVENT|probe_50m|%d|ok=%s,reason=%s,dist=%.4f'):format(
                        idx, tostring(ok), tostring(reason), tonumber(dist) or -1))
                end
            end
        end
    end
end

-- ==================== REQUEST EXECUTION ====================

local function step_capture_trace()
    local tr = state.trace
    if not tr then return false end
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    local rt = realtime()
    if tr.count > 0 and (gt - tr.last_gt) < tr.interval then
        return true
    end
    local p_snap = sample_one(state.primary)
    local s_snap = sample_one(state.secondary)
    assert(p_snap and s_snap, 'trace actor became unavailable')
    tr.count = tr.count + 1
    tr.last_gt = gt
    log(('TRACE ship=primary seq=%d index=%d idcode=%s sector=%s zone=%s gt=%.6f rt=%.6f x=%.6f y=%.6f z=%.6f'):format(
        tr.count, p_snap.index, p_snap.idcode, p_snap.sector_macro, p_snap.zone_key,
        gt, rt, p_snap.pose.x, p_snap.pose.y, p_snap.pose.z))
    log(('TRACE ship=secondary seq=%d index=%d idcode=%s sector=%s zone=%s gt=%.6f rt=%.6f x=%.6f y=%.6f z=%.6f'):format(
        tr.count, s_snap.index, s_snap.idcode, s_snap.sector_macro, s_snap.zone_key,
        gt, rt, s_snap.pose.x, s_snap.pose.y, s_snap.pose.z))
    if tr.count >= tr.target_samples or (gt - tr.start_gt) >= tr.duration_s then
        local total = tr.count
        local dur = gt - tr.start_gt
        state.trace = nil
        finish_req(tr.req_seq, 'trace_captured', ('samples=%d duration_gt=%.4f'):format(total, dur))
        return false
    end
    return true
end

local function step_active_req()
    if state.trace then
        step_capture_trace()
        return
    end
    local req = state.active_req
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    if req.wait_until_game_time and gt < req.wait_until_game_time then
        return
    end
    if not req.dispatched then
        req.dispatched = true
        state.md_reply = nil
        local op = req.op
        if op == 'capture_trace' then
            state.trace = {
                req_seq = req.seq,
                interval = req.interval or 0.10,
                duration_s = req.duration_s or 16.0,
                target_samples = req.target_samples or 160,
                start_gt = gt,
                last_gt = gt - 1.0,
                count = 0,
            }
            step_capture_trace()
            if C.IsGamePaused() then Unpause() end
            return
        elseif op == 'profile_capture' then
            local n = math.min(req.count or 1, #state.candidates)
            local iters = req.iters or 40
            local t0 = realtime()
            local reads = 0
            local checksum = 0.0
            for _ = 1, iters do
                for i = 1, n do
                    local c = state.candidates[i]
                    if C.IsValidComponent(c.actor) and C.IsComponentOperational(c.actor) then
                        local p = C.GetObjectPositionInSector(c.actor)
                        local s = C.GetContextByClass(c.actor, 'sector', false)
                        if s ~= 0 then
                            checksum = checksum + tonumber(p.x) * 0.0001
                            reads = reads + 1
                        end
                    end
                end
            end
            local elapsed_ms = (realtime() - t0) * 1000.0
            finish_req(req.seq, 'profiled', ('n=%d iters=%d reads=%d total_ms=%.4f per_tick_ms=%.4f checksum=%.2f'):format(
                n, iters, reads, elapsed_ms, elapsed_ms / iters, checksum))
            return
        elseif op == 'set_view' then
            local slot = assert(req.slot, 'slot required')
            state.view_serials[slot] = (state.view_serials[slot] or 0) + 1
            local serial = state.view_serials[slot]
            local sector = req.sector or '-'
            local indices = req.indices or {}
            local csv = '-'
            if #indices > 0 and sector ~= '-' then
                local parts = {}
                for i = 1, #indices do parts[i] = tostring(indices[i]) end
                csv = table.concat(parts, ',')
            end
            udp_send(('VIEW|%s|%d|%s|%s'):format(slot, serial, sector, csv))
            if slot == 'high_attention' and state.npc_controller and state.primary then
                local has_primary = false
                for i = 1, #indices do
                    if indices[i] == state.primary.index then has_primary = true; break end
                end
                if has_primary and sector == state.primary.sector_macro then
                    state.npc_controller:reacquire_view({
                        epoch = state.epoch,
                        index = state.primary.index,
                        sector_macro = state.primary.sector_macro,
                        actor = state.primary.actor_key,
                        code = state.primary.idcode,
                    })
                else
                    state.npc_controller:withdraw('offscreen')
                end
            end
            finish_req(req.seq, 'view_set', ('slot=%s serial=%d sector=%s indices=%s'):format(
                slot, serial, sector, csv))
            return
        elseif op == 'probe_pose' then
            local entry = assert(state.by_index[req.index], 'unknown probe index')
            local snap = assert(sample_one(entry), 'probe ship unavailable')
            local ppose = read_player_pose()
            local pdisp = 0.0
            if state.player_initial_pose then
                local dx = ppose.x - state.player_initial_pose.x
                local dy = ppose.y - state.player_initial_pose.y
                local dz = ppose.z - state.player_initial_pose.z
                pdisp = math.sqrt(dx * dx + dy * dy + dz * dz)
            end
            local st = state.npc_controller and state.npc_controller:stats() or {}
            finish_req(req.seq, 'probed', (
                'index=%d idcode=%s sector_macro=%s zone_key=%s %s player_disp_m=%.4f ' ..
                'ctrl_state=%s writes=%d readbacks=%d alignments=%d stale_cutoffs=%d refusals_50m=%d'
            ):format(
                snap.index, snap.idcode, snap.sector_macro, snap.zone_key, fmt_pose(snap.pose), pdisp,
                tostring(st.state or 'none'), st.writes or 0, st.readbacks or 0,
                st.alignments or 0, st.stale_cutoffs or 0, st.refusals_over_50m or 0))
            return
        elseif op == 'readback' then
            AddUITriggeredEvent('X4AuthorityNpcMotion', 'Readback', {
                request_id = req.request_id,
                epoch = state.epoch,
                index = req.index,
            })
            return
        elseif op == 'done' then
            if MODE == 'observe' then
                if state.npc_controller then state.npc_controller:stop() end
                AddUITriggeredEvent('X4AuthorityNpcMotion', 'Release', {
                    request_id = req.request_id,
                })
                return
            else
                finish_req(req.seq, 'done', 'mode=capture')
                state.stage = 'done'
                log('DONE')
                return
            end
        else
            error('unknown op: ' .. tostring(op))
        end
    end

    if not state.md_reply then return end
    local raw = state.md_reply
    state.md_reply = nil
    local parts = split(raw, '|')
    if parts[1] ~= req.request_id then
        error('md correlation mismatch: ' .. raw)
    end
    local kind = parts[2]
    if kind == 'Refused' then
        finish_req(req.seq, 'refused', 'reason=' .. tostring(parts[3]))
        return
    end
    if req.op == 'done' and kind == 'Released' then
        finish_req(req.seq, 'done', 'released=true')
        state.stage = 'done'
        log('DONE')
        return
    end
    if req.op == 'readback' and kind == 'Readback' then
        local idx = tonumber(parts[3])
        local entry = assert(state.by_index[idx], 'md returned unknown index')
        local actor_dec = parse_hex_decimal(parts[4])
        local sec_dec = parse_hex_decimal(parts[5])
        local zone_dec = parse_hex_decimal(parts[6])
        local sec_macro = parts[7]
        local idcode = parts[8]
        assert(actor_dec == entry.actor_key, 'actor identity changed')
        assert(idcode == entry.idcode, 'idcode changed')
        local ffi_sec = C.GetContextByClass(entry.actor, 'sector', false)
        local ffi_zone = C.GetContextByClass(entry.actor, 'zone', false)
        assert(id_text(ffi_sec) == sec_dec and id_text(ffi_zone) == zone_dec, 'ffi space mismatch')
        local pose = read_pose(entry.actor)
        local ppose = read_player_pose()
        local pdisp = 0.0
        if state.player_initial_pose then
            local dx = ppose.x - state.player_initial_pose.x
            local dy = ppose.y - state.player_initial_pose.y
            local dz = ppose.z - state.player_initial_pose.z
            pdisp = math.sqrt(dx * dx + dy * dy + dz * dz)
        end
        local st = state.npc_controller and state.npc_controller:stats() or {}
        finish_req(req.seq, 'readback', (
            'index=%d idcode=%s actor_key=%s sector_key=%s zone_key=%s sector_macro=%s %s ' ..
            'player_disp_m=%.4f writes=%d readbacks=%d alignments=%d stale_cutoffs=%d refusals_50m=%d'
        ):format(
            idx, idcode, actor_dec, sec_dec, zone_dec, sec_macro, fmt_pose(pose),
            pdisp, st.writes or 0, st.readbacks or 0, st.alignments or 0,
            st.stale_cutoffs or 0, st.refusals_over_50m or 0))
    else
        error('unexpected md reply: ' .. raw)
    end
end

-- ==================== MAIN FRAME STATE MACHINE ====================

local function step_capture_startup(t)
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
        state.md_reply = nil
        state.stage = 'populate_universe'
        state.pop_started = realtime()
        AddUITriggeredEvent('X4AuthorityNpcMotion', 'PopulateUniverse', {
            request_id = 'pop-1',
            epoch = state.epoch,
        })
        return
    end
    if state.stage == 'populate_universe' then
        if not state.md_reply then return end
        local raw = state.md_reply
        state.md_reply = nil
        state.pop_ms = (realtime() - state.pop_started) * 1000.0
        local parts = split(raw, '|')
        assert(parts[1] == 'pop-1' and parts[2] == 'Populated', 'populate failed: ' .. raw)
        state.total_registry = tonumber(parts[3]) or 0
        local rows_text = parts[5] or ''
        for _, row in ipairs(split(rows_text, ';')) do
            if #row > 0 then
                local cols = split(row, ',')
                local idx = tonumber(cols[1])
                local dec = parse_hex_decimal(cols[2])
                if idx and dec then
                    local actor = ConvertStringTo64Bit(dec)
                    if actor and actor ~= 0 and C.IsValidComponent(actor) and C.IsComponentOperational(actor) then
                        local entry = {
                            index = idx, actor = actor, actor_key = dec,
                            sector_macro = cols[3], idcode = cols[4], owner = cols[5],
                        }
                        state.candidates[#state.candidates + 1] = entry
                        state.by_index[idx] = entry
                        state.scout_poses[idx] = read_pose(actor)
                    end
                end
            end
        end
        assert(#state.candidates >= 3, 'need at least 3 operational undocked NPCs')
        state.scout_start_gt = tonumber(C.GetCurrentGameTime()) or 0
        state.stage = 'scout_moving'
        return
    end
    if state.stage == 'scout_moving' then
        local gt = tonumber(C.GetCurrentGameTime()) or 0
        local dt = gt - state.scout_start_gt
        if dt < 1.2 then return end
        local moving = {}
        for _, entry in ipairs(state.candidates) do
            if C.IsValidComponent(entry.actor) and C.IsComponentOperational(entry.actor) then
                local p0 = state.scout_poses[entry.index]
                local p1 = read_pose(entry.actor)
                local dx, dy, dz = p1.x - p0.x, p1.y - p0.y, p1.z - p0.z
                local speed = math.sqrt(dx * dx + dy * dy + dz * dz) / dt
                if speed >= 2.0 then
                    moving[#moving + 1] = { entry = entry, speed = speed }
                end
            end
        end
        assert(#moving >= 2, 'need at least 2 moving NPCs during scout')
        -- Prefer moderate speed (8..40 m/s, closest to 20 m/s) so 0.10 s steps stay well under 50 m
        -- and 16 s of real AI flight remains cleanly inside one zone.
        local primary = nil
        local best_score = math.huge
        for _, item in ipairs(moving) do
            if item.speed >= 8.0 and item.speed <= 40.0 then
                local score = math.abs(item.speed - 20.0)
                if score < best_score then best_score = score; primary = item end
            end
        end
        if not primary then
            for _, item in ipairs(moving) do
                if item.speed >= 5.0 and item.speed <= 120.0 and item.speed < best_score then
                    best_score = item.speed; primary = item
                end
            end
        end
        if not primary then
            table.sort(moving, function(a, b) return a.speed < b.speed end)
            primary = moving[1]
        end
        local secondary = nil
        for _, item in ipairs(moving) do
            if item.entry.index ~= primary.entry.index and item.entry.sector_macro ~= primary.entry.sector_macro then
                secondary = item
                break
            end
        end
        if not secondary then
            for _, item in ipairs(moving) do
                if item.entry.index ~= primary.entry.index then secondary = item; break end
            end
        end
        assert(primary and secondary, 'unable to select primary and secondary moving NPCs')
        state.primary = primary.entry
        state.primary.scout_speed = primary.speed
        state.secondary = secondary.entry
        state.secondary.scout_speed = secondary.speed
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
        if not IsSavingPossible(false) then return end
        if not C.IsGamePaused() then Pause() end
        state.game_saved = nil
        state.saved_primary_pose = read_pose(state.primary.actor)
        state.saved_secondary_pose = read_pose(state.secondary.actor)
        state.saved_gt = tonumber(C.GetCurrentGameTime()) or 0
        state.stage = 'saving'
        log('SAVE_REQUEST slot=' .. SAVE_SLOT)
        SaveGame(SAVE_SLOT, 'Neon authoritative NPC motion baseline')
        return
    end
    if state.stage == 'saving' then
        if state.game_saved == nil then return end
        assert(state.game_saved == true, 'SaveGame failed')
        -- Stay paused until capture_trace captures seq=1 at saved_pose and unpauses immediately!
        state.stage = 'ready'
        log((
            'READY mode=capture total_registry=%d populate_ms=%.4f candidates=%d save_slot=%s saved_gt=%.6f ' ..
            'primary_index=%d primary_idcode=%s primary_actor=%s primary_sector=%s primary_speed=%.2f ' ..
            'primary_saved_x=%.4f primary_saved_y=%.4f primary_saved_z=%.4f ' ..
            'secondary_index=%d secondary_idcode=%s secondary_actor=%s secondary_sector=%s secondary_speed=%.2f'
        ):format(
            state.total_registry, state.pop_ms, #state.candidates, SAVE_SLOT, state.saved_gt,
            state.primary.index, state.primary.idcode, state.primary.actor_key, state.primary.sector_macro,
            state.primary.scout_speed,
            state.saved_primary_pose.x, state.saved_primary_pose.y, state.saved_primary_pose.z,
            state.secondary.index, state.secondary.idcode, state.secondary.actor_key, state.secondary.sector_macro,
            state.secondary.scout_speed))
        return
    end
end

local function step_observe_startup()
    if state.stage == 'initial' then
        check_profile()
        if C.IsStartmenu() then
            C.ReloadSaveList()
            state.stage = 'save_list'
            log('OBSERVE_SAVE_LIST slot=' .. SAVE_SLOT)
            return
        end
        if not C.IsPlayerValid() then return end
        if C.IsGamePaused() then
            Unpause()
            return
        end
        state.player_initial_pose = read_player_pose()
        state.md_reply = nil
        state.stage = 'resolve_primary'
        AddUITriggeredEvent('X4AuthorityNpcMotion', 'ResolveSavedShip', {
            request_id = 'res-p',
            epoch = state.epoch,
            index = assert(cfg.primary_index, 'primary_index required'),
        })
        return
    end
    if state.stage == 'save_list' then
        if not C.IsStartmenu() then error('left start menu before save list') end
        if not C.IsSaveListLoadingComplete() then return end
        local entries = GetSaveList({[SAVE_SLOT] = true})
        assert(type(entries) == 'table', 'save list unavailable')
        local matches = 0
        for _, entry in pairs(entries) do
            if entry.filename == SAVE_SLOT then matches = matches + 1 end
        end
        assert(matches == 1, 'expected saved checkpoint ' .. SAVE_SLOT)
        state.stage = 'loading'
        log('LOAD_REQUEST slot=' .. SAVE_SLOT)
        LoadGame(SAVE_SLOT)
        return
    end
    if state.stage == 'loading' then return end
    if state.stage == 'resolve_primary' then
        if not state.md_reply then return end
        local raw = state.md_reply
        state.md_reply = nil
        local parts = split(raw, '|')
        assert(parts[1] == 'res-p' and parts[2] == 'Resolved', 'primary resolve failed: ' .. raw)
        local idx = tonumber(parts[3])
        local dec = assert(parse_hex_decimal(parts[4]), 'invalid primary hex')
        local actor = ConvertStringTo64Bit(dec)
        assert(actor ~= 0 and C.IsValidComponent(actor) and C.IsComponentOperational(actor), 'resolved primary invalid')
        state.total_registry = tonumber(parts[10]) or 0
        state.primary = {
            index = idx, actor = actor, actor_key = dec,
            sector_macro = parts[7], idcode = parts[8], owner = parts[9],
        }
        state.by_index[idx] = state.primary
        state.md_reply = nil
        state.stage = 'resolve_secondary'
        AddUITriggeredEvent('X4AuthorityNpcMotion', 'ResolveSavedShip', {
            request_id = 'res-s',
            epoch = state.epoch,
            index = assert(cfg.secondary_index, 'secondary_index required'),
        })
        return
    end
    if state.stage == 'resolve_secondary' then
        if not state.md_reply then return end
        local raw = state.md_reply
        state.md_reply = nil
        local parts = split(raw, '|')
        assert(parts[1] == 'res-s' and parts[2] == 'Resolved', 'secondary resolve failed: ' .. raw)
        local idx = tonumber(parts[3])
        local dec = assert(parse_hex_decimal(parts[4]), 'invalid secondary hex')
        local actor = ConvertStringTo64Bit(dec)
        assert(actor ~= 0 and C.IsValidComponent(actor) and C.IsComponentOperational(actor), 'resolved secondary invalid')
        state.secondary = {
            index = idx, actor = actor, actor_key = dec,
            sector_macro = parts[7], idcode = parts[8], owner = parts[9],
        }
        state.by_index[idx] = state.secondary
        state.md_reply = nil
        state.stage = 'bind_playback'
        AddUITriggeredEvent('X4AuthorityNpcMotion', 'PreparePlayback', {
            request_id = 'bind-p',
            epoch = state.epoch,
            index = state.primary.index,
            expected_sector = state.primary.sector_macro,
            vx = 0, vy = 0, vz = 0,
        })
        return
    end
    if state.stage == 'bind_playback' then
        if not state.md_reply then return end
        local raw = state.md_reply
        state.md_reply = nil
        local parts = split(raw, '|')
        assert(parts[1] == 'bind-p' and parts[2] == 'PlaybackReady', 'bind playback failed: ' .. raw)
        init_observe_playback()
        state.stage = 'udp_handshake'
        state.udp_last_hello = 0
        return
    end
    if state.stage == 'udp_handshake' then
        local now = state.mono_clock()
        if now - state.udp_last_hello >= 0.1 then
            state.udp_last_hello = now
            udp_send('HELLO')
        end
        drain_observe_udp()
        if state.udp_bound then
            local p0 = read_pose(state.primary.actor)
            state.stage = 'ready'
            log((
                'READY mode=observe total_registry=%d ' ..
                'primary_index=%d primary_idcode=%s primary_actor=%s primary_sector=%s ' ..
                'primary_init_x=%.4f primary_init_y=%.4f primary_init_z=%.4f ' ..
                'secondary_index=%d secondary_idcode=%s secondary_actor=%s secondary_sector=%s ' ..
                'udp_bound=true'
            ):format(
                state.total_registry,
                state.primary.index, state.primary.idcode, state.primary.actor_key, state.primary.sector_macro,
                p0.x, p0.y, p0.z,
                state.secondary.index, state.secondary.idcode, state.secondary.actor_key, state.secondary.sector_macro))
        end
        return
    end
end

local function frame()
    if state.stage == 'done' or state.stage == 'failed' then return end
    local t = getElapsedTime()
    if state.stage ~= 'ready' then
        if MODE == 'observe' then
            step_observe_startup()
        else
            step_capture_startup(t)
        end
        return
    end

    if MODE == 'observe' then
        drain_observe_udp()
        if state.npc_controller then
            state.npc_controller:tick()
        end
    end

    if state.active_req or state.trace then
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
