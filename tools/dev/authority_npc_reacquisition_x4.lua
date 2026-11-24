-- Opt-in Windows X4 NPC sector-change reacquisition callback harness (disposable profile only).
-- Exercises X4AuthorityShipRegistry + X4AuthorityNpcReacquisition prepare/cancel/align/readback/step
-- driven by the real ExperimentalTCPParticipantClient reentry protocol over loopback.
local cfg = X4NEON_NPC_REACQ
if type(cfg) ~= 'table' or cfg.enabled ~= true then return end

local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
declare('typedef struct { float x; float y; float z; float yaw; float pitch; float roll; } UIPosRot;')
for _, text in ipairs({
    'const char* GetGameStartName(void);',
    'bool IsStartmenu(void);',
    'bool IsPlayerValid(void);',
    'bool IsGamePaused(void);',
    'const char* GetSaveFolderPath(void);',
    'double GetCurrentGameTime(void);',
    'UniverseID GetPlayerControlledShipID(void);',
    'UniverseID GetPlayerOccupiedShipID(void);',
    'UniverseID GetPlayerObjectID(void);',
    'UniverseID GetPlayerContainerID(void);',
    'UniverseID GetContextByClass(UniverseID componentid, const char* classname, bool includeself);',
    'bool IsValidComponent(UniverseID componentid);',
    'bool IsComponentClass(UniverseID componentid, const char* classname);',
    'bool IsComponentOperational(UniverseID componentid);',
    'const char* GetObjectIDCode(UniverseID objectid);',
    'UIPosRot GetObjectPositionInSector(UniverseID objectid);',
    'void SetObjectSectorPos(UniverseID objectid, UniverseID sectorid, UIPosRot pos);',
    'uint32_t GetNumAllFactions(bool includehidden);',
    'uint32_t GetAllFactions(const char** result, uint32_t resultlen, bool includehidden);',
}) do declare(text) end

local EXT_DIR = './extensions/' .. tostring(cfg.extension_id or 'x4_neon_npc_reacq') .. '/'
local MAX_STEP_METRES = 50.0

local function log(msg)
    DebugError('[X4LAN] AUTH_NPC_REACQ ' .. msg)
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

local function factions()
    local count = C.GetNumAllFactions(true)
    local buf = ffi.new('const char*[?]', math.max(count, 1))
    count = C.GetAllFactions(buf, count, true)
    local list = {}
    for i = 0, count - 1 do list[#list + 1] = ffi.string(buf[i]) end
    return list
end

local function pick_npc()
    local pship = C.GetPlayerOccupiedShipID()
    if pship == 0 then pship = C.GetPlayerObjectID() end
    local psector = C.GetContextByClass(pship, 'sector', false)
    local psector_text = id_text(psector)
    local pship_text = id_text(pship)
    local pcont_text = id_text(C.GetPlayerContainerID())
    local fallback_ref, fallback_id

    for _, faction in ipairs(factions()) do
        if faction ~= 'player' then
            for _, obj in ipairs(GetContainedObjectsByOwner(faction) or {}) do
                local ship = ConvertStringTo64Bit(tostring(obj))
                local sid = ship and id_text(ship)
                if sid and sid ~= '0' and sid ~= pship_text and sid ~= pcont_text and
                   C.IsValidComponent(ship) and C.IsComponentOperational(ship) and C.IsComponentClass(ship, 'ship') then
                    local sec = C.GetContextByClass(ship, 'sector', false)
                    local sec_text = id_text(sec)
                    local zone = C.GetContextByClass(ship, 'zone', false)
                    local zone_text = id_text(zone)
                    if sec_text ~= '0' and zone_text ~= '0' and C.IsValidComponent(sec) and C.IsValidComponent(zone) then
                        local docked, docking, stored = GetComponentData(
                            obj, 'isdocked', 'isdocking', 'isinternallystored')
                        if docked == false and docking == false and stored == false then
                            local code_ptr = C.GetObjectIDCode(ship)
                            local code = code_ptr ~= nil and code_ptr ~= ffi.NULL and ffi.string(code_ptr) or ''
                            if #code > 0 then
                                if sec_text == psector_text then
                                    return { ref = obj, actor = ship, actor_key = sid }
                                elseif not fallback_id then
                                    fallback_ref, fallback_id = obj, ship
                                end
                            end
                        end
                    end
                end
            end
        end
    end
    if fallback_id then
        return { ref = fallback_ref, actor = fallback_id, actor_key = id_text(fallback_id) }
    end
    error('no operational undocked NPC ship found')
end

local function read_pose(actor)
    local p = C.GetObjectPositionInSector(actor)
    return {
        x = tonumber(p.x), y = tonumber(p.y), z = tonumber(p.z),
        yaw = tonumber(p.yaw), pitch = tonumber(p.pitch), roll = tonumber(p.roll),
    }
end

local function fmt_pose(p)
    return ('x=%.4f y=%.4f z=%.4f yaw=%.4f pitch=%.4f roll=%.4f'):format(p.x, p.y, p.z, p.yaw, p.pitch, p.roll)
end

local state = {
    stage = 'initial',
    epoch = cfg.epoch,
    npc = nil,
    registry_index = nil,
    next_seq = 1,
    active_req = nil,
    md_reply = nil,
    reg_reply = nil,
    last_poll = 0,
}

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.reg_reply = tostring(value)
end)

RegisterEvent('X4AuthorityNpcReacquisition.Result', function(_, value)
    state.md_reply = tostring(value)
end)

local function split(text)
    local parts = {}
    for item in (text .. '|'):gmatch('(.-)|') do parts[#parts + 1] = item end
    return parts
end

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

local function step_active_req()
    local req = state.active_req
    local npc = state.npc
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    if req.wait_until_game_time and gt < req.wait_until_game_time then
        return
    end
    if req.align_settling then
        local rt = getElapsedTime()
        if rt - req.align_started > 8.0 then
            finish_req(req.seq, 'refused', 'reason=align_settle_timeout_' .. tostring(req.last_settle_diag))
            return
        end
        if not req.settle_pending and rt - (req.last_settle_poll or 0) >= 0.1 then
            req.last_settle_poll = rt
            req.settle_seq = (req.settle_seq or 0) + 1
            req.settle_req_id = req.request_id .. '-settle-' .. tostring(req.settle_seq)
            req.settle_pending = true
            state.md_reply = nil
            AddUITriggeredEvent('X4AuthorityNpcReacquisition', 'Readback', {
                request_id = req.settle_req_id,
                epoch = state.epoch,
                index = state.registry_index,
            })
            return
        end
        if not state.md_reply then return end
        local raw = state.md_reply
        state.md_reply = nil
        req.settle_pending = false
        local parts = split(raw)
        if parts[1] ~= req.settle_req_id then return end
        req.last_settle_diag = raw
        if parts[2] == 'Readback' and parts[7] == req.target_sector then
            local actor_dec = parse_hex_decimal(parts[4])
            local sec_dec = parse_hex_decimal(parts[5])
            local zone_dec = parse_hex_decimal(parts[6])
            local ffi_sec = C.GetContextByClass(npc.actor, 'sector', false)
            local ffi_zone = C.GetContextByClass(npc.actor, 'zone', false)
            if actor_dec == npc.actor_key and id_text(ffi_sec) == sec_dec and id_text(ffi_zone) == zone_dec and zone_dec ~= '0' then
                local pose = read_pose(npc.actor)
                finish_req(req.seq, 'aligned', ('index=%d actor_key=%s sector_key=%s zone_key=%s sector_macro=%s settle_polls=%d %s'):format(
                    state.registry_index, actor_dec, sec_dec, zone_dec, tostring(parts[7]), req.settle_seq, fmt_pose(pose)))
                return
            end
        end
        return
    end
    if not req.dispatched then
        req.dispatched = true
        state.md_reply = nil
        local op = req.op
        if op == 'bind_initial' then
            local sec = C.GetContextByClass(npc.actor, 'sector', false)
            local zone = C.GetContextByClass(npc.actor, 'zone', false)
            local smacro = GetComponentData(ConvertStringToLuaID(id_text(sec)), 'macro')
            local pose = read_pose(npc.actor)
            finish_req(req.seq, 'bound', ('index=%d actor_key=%s sector_key=%s zone_key=%s sector_macro=%s %s'):format(
                state.registry_index, id_text(npc.actor), id_text(sec), id_text(zone), tostring(smacro), fmt_pose(pose)))
            return
        elseif op == 'prepare' then
            req.before_sector_key = id_text(C.GetContextByClass(npc.actor, 'sector', false))
            req.before_pose = read_pose(npc.actor)
            AddUITriggeredEvent('X4AuthorityNpcReacquisition', 'Prepare', {
                request_id = req.request_id,
                epoch = state.epoch,
                index = state.registry_index,
                previous_sector = req.previous_sector,
                target_sector = req.target_sector,
            })
            return
        elseif op == 'cancel' then
            AddUITriggeredEvent('X4AuthorityNpcReacquisition', 'Cancel', {
                request_id = req.request_id,
                prepared_request = req.prepared_request,
                epoch = state.epoch,
                index = state.registry_index,
            })
            return
        elseif op == 'align' then
            AddUITriggeredEvent('X4AuthorityNpcReacquisition', 'Align', {
                request_id = req.request_id,
                prepared_request = req.prepared_request,
                epoch = state.epoch,
                index = state.registry_index,
                target_sector = req.target_sector,
                x = req.x, y = req.y, z = req.z,
                yaw = req.yaw or 0, pitch = req.pitch or 0, roll = req.roll or 0,
                vx = req.vx, vy = req.vy, vz = req.vz,
            })
            return
        elseif op == 'readback' then
            AddUITriggeredEvent('X4AuthorityNpcReacquisition', 'Readback', {
                request_id = req.request_id,
                epoch = state.epoch,
                index = state.registry_index,
            })
            return
        elseif op == 'apply_step' then
            local before = read_pose(npc.actor)
            local dx = req.x - before.x
            local dy = req.y - before.y
            local dz = req.z - before.z
            local dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            req.step_dist = dist
            if dist > MAX_STEP_METRES then
                finish_req(req.seq, 'refused_over_50m', ('dist=%.4f max_step=%.1f %s'):format(
                    dist, MAX_STEP_METRES, fmt_pose(before)))
                return
            end
            AddUITriggeredEvent('X4AuthorityNpcReacquisition', 'StepGuard', {
                request_id = req.request_id,
                epoch = state.epoch,
                index = state.registry_index,
                expected_sector = req.expected_sector,
                vx = req.vx, vy = req.vy, vz = req.vz,
            })
            return
        elseif op == 'done' then
            AddUITriggeredEvent('X4AuthorityNpcReacquisition', 'Release', {
                request_id = req.request_id,
            })
            return
        else
            error('unknown request op: ' .. tostring(op))
        end
    end

    if not state.md_reply then return end
    local raw = state.md_reply
    state.md_reply = nil
    local parts = split(raw)
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
    if req.op == 'align' and kind == 'AlignDispatched' then
        log(('ALIGN_DISPATCHED seq=%d raw=%s'):format(req.seq, raw))
        req.align_settling = true
        req.align_started = getElapsedTime()
        req.last_settle_diag = raw
        return
    end

    local actor_dec = parse_hex_decimal(parts[4])
    local sec_dec = parse_hex_decimal(parts[5])
    local zone_dec = parse_hex_decimal(parts[6])
    local sec_macro = parts[7]
    assert(actor_dec == npc.actor_key, 'actor identity changed: ' .. tostring(actor_dec) .. ' vs ' .. npc.actor_key)
    local ffi_sec = C.GetContextByClass(npc.actor, 'sector', false)
    local ffi_zone = C.GetContextByClass(npc.actor, 'zone', false)
    assert(id_text(ffi_sec) == sec_dec, 'ffi sector mismatch')
    assert(id_text(ffi_zone) == zone_dec, 'ffi zone mismatch')
    assert(C.IsValidComponent(npc.actor) and C.IsComponentOperational(npc.actor), 'actor no longer operational')
    local pose = read_pose(npc.actor)

    if req.op == 'prepare' and kind == 'Prepared' then
        local target_sec_dec = parse_hex_decimal(parts[8])
        assert(sec_dec == req.before_sector_key, 'actor sector changed during prepare')
        finish_req(req.seq, 'prepared', ('index=%d actor_key=%s sector_key=%s zone_key=%s sector_macro=%s target_sector_key=%s %s'):format(
            state.registry_index, actor_dec, sec_dec, zone_dec, tostring(sec_macro), tostring(target_sec_dec), fmt_pose(pose)))
    elseif req.op == 'cancel' and kind == 'Canceled' then
        finish_req(req.seq, 'canceled', ('index=%d actor_key=%s sector_key=%s zone_key=%s sector_macro=%s %s'):format(
            state.registry_index, actor_dec, sec_dec, zone_dec, tostring(sec_macro), fmt_pose(pose)))
    elseif req.op == 'align' and kind == 'Aligned' then
        finish_req(req.seq, 'aligned', ('index=%d actor_key=%s sector_key=%s zone_key=%s sector_macro=%s %s'):format(
            state.registry_index, actor_dec, sec_dec, zone_dec, tostring(sec_macro), fmt_pose(pose)))
    elseif req.op == 'readback' and kind == 'Readback' then
        local idcode = parts[8]
        assert(idcode == npc.idcode, 'idcode changed on readback')
        finish_req(req.seq, 'readback', ('index=%d idcode=%s actor_key=%s sector_key=%s zone_key=%s sector_macro=%s %s'):format(
            state.registry_index, tostring(idcode), actor_dec, sec_dec, zone_dec, tostring(sec_macro), fmt_pose(pose)))
    elseif req.op == 'apply_step' and kind == 'StepReady' then
        local before = read_pose(npc.actor)
        local dx, dy, dz = req.x - before.x, req.y - before.y, req.z - before.z
        local dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dist > MAX_STEP_METRES then
            finish_req(req.seq, 'refused_over_50m', ('dist=%.4f max_step=%.1f %s'):format(dist, MAX_STEP_METRES, fmt_pose(before)))
            return
        end
        C.SetObjectSectorPos(npc.actor, ffi_sec, ffi.new('UIPosRot', {
            x = req.x, y = req.y, z = req.z,
            yaw = before.yaw * 180 / math.pi,
            pitch = before.pitch * 180 / math.pi,
            roll = before.roll * 180 / math.pi,
        }))
        local after = read_pose(npc.actor)
        local ex, ey, ez = after.x - req.x, after.y - req.y, after.z - req.z
        local err_m = math.sqrt(ex * ex + ey * ey + ez * ez)
        finish_req(req.seq, 'step_applied', ('index=%d actor_key=%s sector_key=%s zone_key=%s sector_macro=%s step_dist=%.4f err_m=%.4f %s'):format(
            state.registry_index, actor_dec, sec_dec, zone_dec, tostring(sec_macro), dist, err_m, fmt_pose(after)))
    else
        error('unexpected md reply for op ' .. tostring(req.op) .. ': ' .. raw)
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
        state.npc = pick_npc()
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
        local parts = split(state.reg_reply)
        state.reg_reply = nil
        assert(parts[1] == 'init-1' and parts[2] == 'Initialized' and parts[3] == state.epoch, 'registry init failed')
        state.stage = 'enroll_npc'
        AddUITriggeredEvent('X4AuthorityShipRegistry', 'Enroll', {
            request_id = 'enroll-1',
            epoch = state.epoch,
            ship = state.npc.ref,
        })
        return
    end
    if state.stage == 'enroll_npc' then
        if not state.reg_reply then return end
        local parts = split(state.reg_reply)
        state.reg_reply = nil
        assert(parts[1] == 'enroll-1' and parts[2] == 'Enrolled', 'registry enroll failed')
        state.registry_index = tonumber(parts[3])
        assert(state.registry_index and state.registry_index >= 1, 'invalid registry index')
        state.md_reply = nil
        state.stage = 'inspect_initial'
        AddUITriggeredEvent('X4AuthorityNpcReacquisition', 'InspectInitial', {
            request_id = 'initial-1',
            epoch = state.epoch,
            index = state.registry_index,
        })
        return
    end
    if state.stage == 'inspect_initial' then
        if not state.md_reply then return end
        local raw = state.md_reply
        state.md_reply = nil
        local parts = split(raw)
        assert(parts[1] == 'initial-1' and parts[2] == 'Initial', 'initial inspect failed: ' .. raw)
        local actor_dec = parse_hex_decimal(parts[4])
        local sec_dec = parse_hex_decimal(parts[5])
        local zone_dec = parse_hex_decimal(parts[6])
        assert(actor_dec == state.npc.actor_key, 'initial actor mismatch')
        state.npc.origin_sector_key = sec_dec
        state.npc.origin_zone_key = zone_dec
        state.npc.origin_sector_macro = parts[7]
        state.npc.target_sector_macro = parts[8]
        state.npc.idcode = parts[9]
        state.npc.ship_macro = parts[10]
        state.npc.owner = parts[11]
        local pose = read_pose(state.npc.actor)
        state.stage = 'ready'
        log(('READY epoch=%s index=%d idcode=%s ship_macro=%s owner=%s actor_key=%s origin_sector_macro=%s origin_sector_key=%s origin_zone_key=%s target_sector_macro=%s %s'):format(
            state.epoch, state.registry_index, state.npc.idcode, state.npc.ship_macro, state.npc.owner,
            state.npc.actor_key, state.npc.origin_sector_macro, state.npc.origin_sector_key,
            state.npc.origin_zone_key, state.npc.target_sector_macro, fmt_pose(pose)))
        return
    end
    if state.stage == 'ready' then
        if state.active_req then
            step_active_req()
            return
        end
        if t - state.last_poll < 0.05 then return end
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
