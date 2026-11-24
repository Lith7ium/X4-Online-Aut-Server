-- Opt-in native Windows X4 9.00 multi-NPC live source capture, hull playback, and profiling harness.
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
    'const char* GetSaveFolderPath(void);',
    'double GetCurrentGameTime(void);',
    'UniverseID GetContextByClass(UniverseID componentid, const char* classname, bool includeself);',
    'bool IsValidComponent(UniverseID componentid);',
    'bool IsComponentClass(UniverseID componentid, const char* classname);',
    'bool IsComponentOperational(UniverseID componentid);',
    'const char* GetObjectIDCode(UniverseID objectid);',
    'UIPosRot GetObjectPositionInSector(UniverseID objectid);',
    'UniverseID GetZoneAt(UniverseID sectorid, UIPosRot* offset);',
    'void SetObjectSectorPos(UniverseID objectid, UniverseID sectorid, UIPosRot pos);',
}) do declare(text) end

local EXT_DIR = './extensions/' .. tostring(cfg.extension_id or 'x4_neon_npc_motion') .. '/'
local MAX_STEP_METRES = 50.0

local function log(msg)
    DebugError('[X4LAN] AUTH_NPC_MOTION ' .. msg)
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

local function fmt_pose(p)
    return ('x=%.4f y=%.4f z=%.4f yaw=%.4f pitch=%.4f roll=%.4f'):format(
        p.x, p.y, p.z, p.yaw, p.pitch, p.roll)
end

local state = {
    stage = 'initial',
    epoch = cfg.epoch,
    md_reply = nil,
    total_registry = 0,
    candidates = {},      -- array of {index, actor, actor_key, sector_macro, idcode, owner}
    by_index = {},        -- index -> candidate
    scout_start_gt = nil,
    scout_poses = {},
    next_seq = 1,
    active_req = nil,
    last_poll = 0,
    pop_started = 0,
    pop_ms = 0,
}

RegisterEvent('X4AuthorityNpcMotion.Result', function(_, value)
    state.md_reply = tostring(value)
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
    local rt = getElapsedTime()
    log(('RESP seq=%d status=%s game_time=%.4f real_time=%.4f %s'):format(
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
        sector_key = id_text(sec),
        zone_key = id_text(zone),
        sector_macro = tostring(smacro),
        pose = p,
    }
end

local function step_active_req()
    local req = state.active_req
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    if req.wait_until_game_time and gt < req.wait_until_game_time then
        return
    end
    if not req.dispatched then
        req.dispatched = true
        state.md_reply = nil
        local op = req.op
        if op == 'capture' then
            local entry = state.by_index[req.index]
            assert(entry, 'unknown capture index ' .. tostring(req.index))
            local snap = sample_one(entry)
            assert(snap, 'capture actor unavailable')
            finish_req(req.seq, 'captured', ('index=%d idcode=%s actor_key=%s sector_key=%s zone_key=%s sector_macro=%s %s'):format(
                snap.index, snap.idcode, snap.actor_key, snap.sector_key, snap.zone_key, snap.sector_macro, fmt_pose(snap.pose)))
            return
        elseif op == 'capture_multi' then
            local parts = {}
            for _, idx in ipairs(req.indices or {}) do
                local entry = state.by_index[idx]
                assert(entry, 'unknown multi index ' .. tostring(idx))
                local snap = sample_one(entry)
                assert(snap, 'multi actor unavailable')
                parts[#parts + 1] = ('%d,%s,%.4f,%.4f,%.4f'):format(
                    snap.index, snap.sector_macro, snap.pose.x, snap.pose.y, snap.pose.z)
            end
            finish_req(req.seq, 'captured_multi', 'count=' .. tostring(#parts) .. ' rows=' .. table.concat(parts, ';'))
            return
        elseif op == 'profile_capture' then
            local n = math.min(req.count or 1, #state.candidates)
            local iters = req.iters or 20
            local t0 = getElapsedTime()
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
            local elapsed_ms = (getElapsedTime() - t0) * 1000.0
            finish_req(req.seq, 'profiled', ('n=%d iters=%d reads=%d total_ms=%.4f per_tick_ms=%.4f checksum=%.2f'):format(
                n, iters, reads, elapsed_ms, elapsed_ms / iters, checksum))
            return
        elseif op == 'playback_step' then
            local entry = state.by_index[req.index]
            assert(entry, 'unknown playback index')
            local before = read_pose(entry.actor)
            local dx, dy, dz = req.x - before.x, req.y - before.y, req.z - before.z
            local dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist > MAX_STEP_METRES then
                finish_req(req.seq, 'refused_over_50m', ('index=%d dist=%.4f max_step=%.1f %s'):format(
                    req.index, dist, MAX_STEP_METRES, fmt_pose(before)))
                return
            end
            req.before_pose = before
            AddUITriggeredEvent('X4AuthorityNpcMotion', 'PreparePlayback', {
                request_id = req.request_id,
                epoch = state.epoch,
                index = req.index,
                expected_sector = req.expected_sector,
                vx = req.vx, vy = req.vy, vz = req.vz,
            })
            return
        elseif op == 'readback' then
            AddUITriggeredEvent('X4AuthorityNpcMotion', 'Readback', {
                request_id = req.request_id,
                epoch = state.epoch,
                index = req.index,
            })
            return
        elseif op == 'done' then
            AddUITriggeredEvent('X4AuthorityNpcMotion', 'Release', {
                request_id = req.request_id,
            })
            return
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

    local idx = tonumber(parts[3])
    local entry = state.by_index[idx]
    assert(entry, 'md returned unknown index')
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

    if req.op == 'playback_step' and kind == 'PlaybackReady' then
        local before = read_pose(entry.actor)
        local dx, dy, dz = req.x - before.x, req.y - before.y, req.z - before.z
        local dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist > MAX_STEP_METRES then
            finish_req(req.seq, 'refused_over_50m', ('index=%d dist=%.4f max_step=%.1f %s'):format(
                idx, dist, MAX_STEP_METRES, fmt_pose(before)))
            return
        end
        local offset = ffi.new('UIPosRot[1]')
        offset[0].x, offset[0].y, offset[0].z = req.x, req.y, req.z
        local same_zone = (C.GetZoneAt(ffi_sec, offset) == ffi_zone)
        if not same_zone then
            finish_req(req.seq, 'refused_zone_boundary', ('index=%d dist=%.4f'):format(idx, dist))
            return
        end
        C.SetObjectSectorPos(entry.actor, ffi_sec, ffi.new('UIPosRot', {
            x = req.x, y = req.y, z = req.z,
            yaw = before.yaw * 180 / math.pi,
            pitch = before.pitch * 180 / math.pi,
            roll = before.roll * 180 / math.pi,
        }))
        local after = read_pose(entry.actor)
        local ex, ey, ez = after.x - req.x, after.y - req.y, after.z - req.z
        local err_m = math.sqrt(ex * ex + ey * ey + ez * ez)
        finish_req(req.seq, 'playback_applied', ('index=%d idcode=%s actor_key=%s sector_key=%s zone_key=%s sector_macro=%s step_dist=%.4f err_m=%.4f %s'):format(
            idx, idcode, actor_dec, sec_dec, zone_dec, sec_macro, dist, err_m, fmt_pose(after)))
    elseif req.op == 'readback' and kind == 'Readback' then
        local pose = read_pose(entry.actor)
        finish_req(req.seq, 'readback', ('index=%d idcode=%s actor_key=%s sector_key=%s zone_key=%s sector_macro=%s %s'):format(
            idx, idcode, actor_dec, sec_dec, zone_dec, sec_macro, fmt_pose(pose)))
    else
        error('unexpected md reply: ' .. raw)
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
        state.md_reply = nil
        state.stage = 'populate_universe'
        state.pop_started = t
        AddUITriggeredEvent('X4AuthorityNpcMotion', 'PopulateUniverse', {
            request_id = 'pop-1',
            epoch = state.epoch,
        })
        return
    end
    if state.stage == 'starting' then return end
    if state.stage == 'populate_universe' then
        if not state.md_reply then return end
        local raw = state.md_reply
        state.md_reply = nil
        state.pop_ms = (t - state.pop_started) * 1000.0
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
                moving[#moving + 1] = { entry = entry, speed = speed }
            end
        end
        table.sort(moving, function(a, b) return a.speed > b.speed end)
        assert(#moving >= 3 and moving[1].speed > 1.0, 'no moving NPC found during scout')
        local primary = moving[1]
        local secondary = nil
        for i = 2, #moving do
            if moving[i].speed > 1.0 and moving[i].entry.sector_macro ~= primary.entry.sector_macro then
                secondary = moving[i]
                break
            end
        end
        if not secondary then secondary = moving[2] end
        local playback_target = nil
        for i = 2, #moving do
            if moving[i].entry.index ~= secondary.entry.index then
                playback_target = moving[i]
                break
            end
        end
        assert(primary and secondary and playback_target, 'unable to pick 3 distinct NPCs')
        state.stage = 'ready'
        log(('READY total_registry=%d populate_ms=%.2f candidates=%d primary_index=%d primary_idcode=%s primary_actor=%s primary_sector=%s primary_speed=%.2f secondary_index=%d secondary_idcode=%s secondary_actor=%s secondary_sector=%s secondary_speed=%.2f playback_index=%d playback_idcode=%s playback_actor=%s playback_sector=%s'):format(
            state.total_registry, state.pop_ms, #state.candidates,
            primary.entry.index, primary.entry.idcode, primary.entry.actor_key, primary.entry.sector_macro, primary.speed,
            secondary.entry.index, secondary.entry.idcode, secondary.entry.actor_key, secondary.entry.sector_macro, secondary.speed,
            playback_target.entry.index, playback_target.entry.idcode, playback_target.entry.actor_key, playback_target.entry.sector_macro))
        return
    end
    if state.stage == 'ready' then
        if state.active_req then
            step_active_req()
            return
        end
        if t - state.last_poll < 0.04 then return end
        state.last_poll = t
        local req = load_request(state.next_seq)
        if req then
            if req.min_game_delay and req.min_game_delay > 0 then
                req.wait_until_game_time = (tonumber(C.GetCurrentGameTime()) or 0) + req.min_game_delay
            end
            state.active_req = req
        end
    end
end

SetScript('onUpdate', function()
    local ok, err = pcall(frame)
    if not ok and state.stage ~= 'failed' then
        state.stage = 'failed'
        log('ERROR ' .. tostring(err):gsub('%s+', ' '))
    end
end)
