-- Disposable opt-in native Windows X4 9.00 harness for the registered-NPC
-- receive/playback check with one unrelated visible ship.
--
-- Phase 1 ('capture'): populates X4AuthorityShipRegistry, spawns & enrolls one
--   unrelated visible ship 650 m ahead of the player, selects a real moving
--   non-player AI-piloted NPC in player.sector, saves disposable save_009.xml.gz,
--   and captures ~9.5 s of genuine AI flight using packaged x4lan_net_64.dll
--   udp.monotonic() (QPC).
-- Phase 2 ('playback'): cold-reloads save_009.xml.gz, verifies unmodified
--   X4LAN_COMPANY_HULL_PLAYBACK_X4 refuses the NPC target, locks the unrelated
--   visible ship on the stock HUD, holds local AI on the NPC, and exercises
--   unmodified X4LAN_LOCAL_UDP_MODULE (udp.monotonic QPC),
--   X4LAN_COMPANY_OBSERVER_STREAM, X4LAN_COMPANY_COCKPIT_PLAYBACK,
--   X4LAN_COMPANY_HULL_PLAYBACK, X4LAN_MOTION_BUFFER, and X4LAN_MOTION_PLAYBACK
--   over real PID-validated Windows UDP while verifying the unrelated visible
--   ship and local own ship remain untouched at 0.000000 m displacement.
local cfg = X4NEON_WINDOWS_NPC_PLAYBACK
if type(cfg) ~= 'table' or cfg.enabled ~= true then return end

local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
declare('typedef struct { float x, y, z, yaw, pitch, roll; } UIPosRot;')
declare('typedef struct { float x, y; bool onScreen; } ScreenPos;')
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
    'void SetObjectSectorPos(UniverseID objectid, UniverseID sectorid, UIPosRot offset);',
    'bool SetSofttarget(UniverseID componentid, const char* const connectionname);',
    'ScreenPos GetComponentScreenPosition(UniverseID componentid, uint32_t width, uint32_t height);',
    'float GetDistanceBetween(UniverseID componentid1, UniverseID componentid2);',
}) do declare(text) end

local resolve = assert(X4LAN_RESOLVE_STRING, 'X4LAN_RESOLVE_STRING required')
local EXT_DIR = './extensions/x4_lan_lab/'
local SAVE_SLOT = cfg.save_slot or 'save_009'

local function log(tag, msg)
    DebugError('[X4LAN] ' .. tag .. (msg and (' ' .. msg) or ''))
end

local function split(text, sep)
    sep = sep or '|'
    local parts = {}
    for item in (text .. sep):gmatch('(.-)' .. sep) do parts[#parts + 1] = item end
    return parts
end

local function format_hex_u64(id)
    local hi = tonumber(id / ffi.new('uint64_t', 4294967296ULL))
    local lo = tonumber(id % ffi.new('uint64_t', 4294967296ULL))
    if hi > 0 then
        return string.format('0x%x%08x', hi, lo)
    end
    return string.format('0x%x', lo)
end

local function ref_from_u64(id)
    local dec = tostring(id):gsub('ULL$', '')
    local value = ConvertStringToLuaID(dec)
    if value and resolve.actor_u64(ConvertIDTo64Bit(value)) == id then
        return value
    end
    return nil
end

local function check_profile()
    local path = C.GetSaveFolderPath()
    assert(path ~= nil and path ~= ffi.NULL, 'save folder unavailable')
    local text = ffi.string(path):gsub('\\', '/'):gsub('/+$', '')
    assert(text:match('/' .. tostring(cfg.profile) .. '/save$'),
        'not disposable profile ' .. tostring(cfg.profile) .. ': ' .. text)
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

local function deg_diff(a_rad, b_rad)
    local d = math.abs(math.deg(a_rad) - math.deg(b_rad)) % 360.0
    if d > 180.0 then d = 360.0 - d end
    return d
end

local function max_att_diff_deg(a, b)
    return math.max(deg_diff(a.yaw, b.yaw), deg_diff(a.pitch, b.pitch), deg_diff(a.roll, b.roll))
end

local function fmt_xyz(p)
    return ('%.17g,%.17g,%.17g'):format(p.x, p.y, p.z)
end

local function fmt_att_deg(p)
    return ('%.6f,%.6f,%.6f'):format(math.deg(p.yaw), math.deg(p.pitch), math.deg(p.roll))
end

local function stop_flag_present()
    local ok, chunk = pcall(loadfile, EXT_DIR .. 'stop_playback.lua')
    if not (ok and type(chunk) == 'function') then return false end
    local rok, val = pcall(chunk)
    return rok and val == true
end

local state = {
    stage = 'initial',
    failed = false,
    started_game = false,
    save_list_reloaded = false,
    udp_lib = nil,
    udp_net = nil,
    reg_reply = nil,
    probe_reply = nil,
    game_saved = nil,
    req_seq = 0,
    -- Capture state
    total_registry = 0,
    own = nil,
    unrelated = nil,
    candidates = {},
    scout_started = nil,
    radar_range = 40000,
    npc = nil,
    pre_save_poses = nil,
    capture_started = nil,
    last_sample_t = nil,
    last_sample_pose = nil,
    sample_seq = 0,
    peak_speed = 0.0,
    capture_origin = nil,
    -- Playback state
    npc_init_pose = nil,
    unrelated_init_pose = nil,
    own_init_pose = nil,
    stream = nil,
    playback_ctrl = nil,
    npc_writes = 0,
    readback_count = 0,
    last_readback = nil,
    max_readback_err = 0.0,
    min_elapsed_ms = nil,
    max_elapsed_ms = 0.0,
    max_att_delta_deg = 0.0,
    last_tick_qpc = nil,
    max_frame_gap_s = 0.0,
    tick_count = 0,
}

local function next_req(tag)
    state.req_seq = state.req_seq + 1
    return tag .. '-' .. tostring(state.req_seq)
end

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.reg_reply = tostring(value)
end)

RegisterEvent('X4AuthorityWindowsNpcPlayback.Result', function(_, value)
    state.probe_reply = tostring(value)
end)

RegisterEvent('gameSaved', function(_, ok)
    state.game_saved = (ok == true)
end)

local function load_packaged_udp()
    if state.udp_lib then return state.udp_lib end
    local loader = assert(X4LAN_LOCAL_UDP_MODULE, 'X4LAN_LOCAL_UDP_MODULE required')
    local lib = assert(loader.load(ffi.os, package.loadlib), 'packaged local_udp_module load failed')
    assert(type(lib.open) == 'function' and type(lib.monotonic) == 'function',
        'packaged x4lan_net_64.dll missing open/monotonic')
    local t1 = lib.monotonic()
    local t2 = lib.monotonic()
    assert(type(t1) == 'number' and type(t2) == 'number' and t2 >= t1 and t1 > 0,
        'packaged udp.monotonic invalid')
    log('AUTH_NPC_DLL_CLOCK', ('phase=%s mono1=%.9f mono2=%.9f delta_ns=%.0f'):format(
        tostring(cfg.phase), t1, t2, (t2 - t1) * 1e9))
    state.udp_lib = lib
    return lib
end

-- =========================================================================
-- PHASE 1: Authoritative Capture (real native Windows X4 9.00 AI NPC flight)
-- =========================================================================
local function step_capture()
    if C.IsStartmenu() then
        if not state.started_game then
            check_profile()
            state.started_game = true
            log('AUTH_NPC_CAPTURE_START_NEWGAME', 'gamestart=' .. tostring(cfg.gamestart))
            local t = getElapsedTime()
            Helper.addDelayedOneTimeCallbackOnUpdate(function()
                NewGame(cfg.gamestart)
            end, true, t + 2)
        end
        return
    end
    if not C.IsPlayerValid() then return end

    if state.stage == 'initial' then
        if C.IsGamePaused() then return end
        local gt = tonumber(C.GetCurrentGameTime()) or 0
        if gt < 2.0 then return end
        check_profile()
        load_packaged_udp()
        state.reg_reply = nil
        local rid = next_req('init')
        assert(AddUITriggeredEvent('X4AuthorityShipRegistry', 'Initialize', {
            request_id = rid, epoch = cfg.epoch,
        }) ~= false)
        state.stage = 'wait_init'
        return
    end

    if state.stage == 'wait_init' then
        if not state.reg_reply then return end
        local parts = split(state.reg_reply, '|')
        assert(parts[2] == 'Initialized' and parts[3] == cfg.epoch, 'registry init failed: ' .. state.reg_reply)
        state.probe_reply = nil
        local rid = next_req('setup')
        assert(AddUITriggeredEvent('X4AuthorityWindowsNpcPlayback', 'SetupCapture', {
            request_id = rid, epoch = cfg.epoch,
        }) ~= false)
        state.stage = 'wait_setup'
        return
    end

    if state.stage == 'wait_setup' then
        if not state.probe_reply then return end
        local parts = split(state.probe_reply, '|')
        assert(parts[2] == 'CaptureSetup', 'SetupCapture refused: ' .. state.probe_reply)
        state.total_registry = assert(tonumber(parts[3]))
        local own_actor = assert(resolve.parse_hex(parts[5]))
        state.own = {
            index = assert(tonumber(parts[4])),
            actor = own_actor,
            hex = format_hex_u64(own_actor),
            code = parts[6],
            sector = parts[7],
        }
        local unrel_actor = assert(resolve.parse_hex(parts[9]))
        state.unrelated = {
            index = assert(tonumber(parts[8])),
            actor = unrel_actor,
            hex = format_hex_u64(unrel_actor),
            code = parts[10],
            dist_m = tonumber(parts[11]) or 0,
            sector = parts[7],
        }
        local radar_range = tonumber(parts[12]) or 40000
        local cands_raw = parts[13] or ''
        local now = state.udp_lib.monotonic()
        state.candidates = {}
        for entry in (cands_raw .. ';'):gmatch('([^;]+);') do
            local f = split(entry, ',')
            local idx = tonumber(f[1])
            local actor = f[2] and resolve.parse_hex(f[2]) or nil
            if idx and actor and C.IsValidComponent(actor) and C.IsComponentOperational(actor) then
                local zone = C.GetContextByClass(actor, 'zone', false)
                state.candidates[#state.candidates + 1] = {
                    index = idx,
                    actor = actor,
                    hex = format_hex_u64(actor),
                    code = f[3],
                    owner = f[4],
                    macro = f[5],
                    dist_m = tonumber(f[6]) or 999999,
                    zone = zone,
                    p0 = read_pose(actor),
                    t0 = now,
                }
            end
        end
        assert(#state.candidates >= 1, 'no non-player AI NPC candidates found in player.sector')
        log('AUTH_NPC_CAPTURE_SETUP', ('total_registry=%d own_index=%d own_code=%s sector=%s unrelated_index=%d unrelated_code=%s unrelated_dist_m=%.3f radar_m=%.1f candidates=%d'):format(
            state.total_registry, state.own.index, state.own.code, state.own.sector,
            state.unrelated.index, state.unrelated.code, state.unrelated.dist_m, radar_range, #state.candidates))
        state.radar_range = radar_range
        state.scout_started = now
        state.stage = 'scouting'
        return
    end

    if state.stage == 'scouting' then
        if C.IsGamePaused() then return end
        local now = state.udp_lib.monotonic()
        if now - state.scout_started < 1.5 then return end
        local best_near, best_any = nil, nil
        for _, c in ipairs(state.candidates) do
            if C.IsValidComponent(c.actor) and C.IsComponentOperational(c.actor) then
                local sec = C.GetContextByClass(c.actor, 'sector', false)
                local own_sec = C.GetContextByClass(state.own.actor, 'sector', false)
                local zone = C.GetContextByClass(c.actor, 'zone', false)
                if sec ~= 0 and sec == own_sec then
                    local p1 = read_pose(c.actor)
                    local dt = now - c.t0
                    local moved = dist3(p1, c.p0)
                    local speed = dt > 0 and (moved / dt) or 0
                    local same_zone = (zone == c.zone)
                    c.scout_speed = speed
                    c.scout_moved = moved
                    c.same_zone = same_zone
                    if speed >= 4.0 and speed <= 30.0 then
                        local score = c.dist_m + (same_zone and 0 or 100000)
                        if c.dist_m <= state.radar_range then
                            if not best_near or score < best_near.score then
                                c.score = score
                                best_near = c
                            end
                        end
                        if not best_any or score < best_any.score then
                            c.score = score
                            best_any = c
                        end
                    elseif speed >= 2.0 and speed <= 40.0 then
                        local score = 500000 + c.dist_m + (same_zone and 0 or 100000)
                        if not best_any or score < best_any.score then
                            c.score = score
                            best_any = c
                        end
                    end
                end
            end
        end
        local chosen = best_near or best_any
        assert(chosen ~= nil, 'no moving non-player AI NPC found in player.sector')
        state.npc = chosen
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
        if not IsSavingPossible(false) then return end
        if not C.IsGamePaused() then
            Pause()
            return
        end
        local chosen = state.npc
        local now = state.udp_lib.monotonic()
        state.pre_save_poses = {
            npc = read_pose(chosen.actor),
            unrelated = read_pose(state.unrelated.actor),
            own = read_pose(state.own.actor),
            game_time = C.GetCurrentGameTime(),
            capture_time = now,
        }
        log('AUTH_NPC_CAPTURE_SELECTED', table.concat({
            'index=' .. tostring(chosen.index),
            'actor=' .. chosen.hex,
            'code=' .. tostring(chosen.code),
            'owner=' .. tostring(chosen.owner),
            'macro=' .. tostring(chosen.macro),
            'sector=' .. tostring(state.own.sector),
            ('dist_m=%.3f'):format(chosen.dist_m),
            ('within_radar=%s'):format(tostring(chosen.dist_m <= state.radar_range)),
            ('scout_speed_mps=%.3f'):format(chosen.scout_speed),
            'pre_save_npc=' .. fmt_xyz(state.pre_save_poses.npc),
            'pre_save_npc_att_deg=' .. fmt_att_deg(state.pre_save_poses.npc),
            'pre_save_unrelated=' .. fmt_xyz(state.pre_save_poses.unrelated),
            'pre_save_own=' .. fmt_xyz(state.pre_save_poses.own),
        }, ' '))
        state.game_saved = nil
        SaveGame(SAVE_SLOT, 'Neon Windows NPC receive playback baseline')
        state.stage = 'wait_save'
        return
    end

    if state.stage == 'wait_save' then
        if state.game_saved == nil then return end
        assert(state.game_saved == true, 'SaveGame failed')
        log('AUTH_NPC_CAPTURE_SAVED', 'slot=' .. SAVE_SLOT)
        if C.IsGamePaused() then Unpause() end
        state.stage = 'wait_unpause'
        return
    end

    if state.stage == 'wait_unpause' then
        if C.IsGamePaused() then
            Unpause()
            return
        end
        state.capture_started = state.udp_lib.monotonic()
        state.last_sample_t = nil
        state.last_sample_pose = nil
        state.sample_seq = 0
        state.stage = 'capturing'
        return
    end

    if state.stage == 'capturing' then
        if C.IsGamePaused() then return end
        local now = state.udp_lib.monotonic()
        if state.last_sample_t ~= nil and (now - state.last_sample_t) < 0.095 then
            return
        end
        local npc = state.npc
        local valid = C.IsValidComponent(npc.actor) and C.IsComponentOperational(npc.actor)
            and C.GetContextByClass(npc.actor, 'sector', false) == C.GetContextByClass(state.own.actor, 'sector', false)
        if not valid or state.sample_seq >= 90 or (now - state.capture_started) >= 9.5 then
            local end_unrel = read_pose(state.unrelated.actor)
            local end_own = read_pose(state.own.actor)
            local total_disp = state.capture_origin and state.last_sample_pose
                and dist3(state.last_sample_pose, state.capture_origin) or 0.0
            log('AUTH_NPC_CAPTURE_DONE', table.concat({
                'samples=' .. tostring(state.sample_seq),
                ('displacement_m=%.6f'):format(total_disp),
                ('peak_speed_mps=%.6f'):format(state.peak_speed),
                ('unrelated_capture_disp_m=%.6f'):format(dist3(end_unrel, state.pre_save_poses.unrelated)),
                ('own_capture_disp_m=%.6f'):format(dist3(end_own, state.pre_save_poses.own)),
            }, ' '))
            state.stage = 'done'
            return
        end
        local pose = read_pose(npc.actor)
        local gt = C.GetCurrentGameTime()
        local zone = C.GetContextByClass(npc.actor, 'zone', false)
        state.sample_seq = state.sample_seq + 1
        local vx, vy, vz, speed = '-', '-', '-', 0.0
        if state.last_sample_t ~= nil and state.last_sample_pose ~= nil then
            local dt = now - state.last_sample_t
            if dt > 0 then
                local rvx = (pose.x - state.last_sample_pose.x) / dt
                local rvy = (pose.y - state.last_sample_pose.y) / dt
                local rvz = (pose.z - state.last_sample_pose.z) / dt
                speed = math.sqrt(rvx * rvx + rvy * rvy + rvz * rvz)
                if speed > state.peak_speed then state.peak_speed = speed end
                vx = ('%.17g'):format(rvx)
                vy = ('%.17g'):format(rvy)
                vz = ('%.17g'):format(rvz)
            end
        else
            state.capture_origin = pose
        end
        state.last_sample_t = now
        state.last_sample_pose = pose
        log('AUTH_NPC_CAPTURE_SAMPLE', table.concat({
            'seq=' .. tostring(state.sample_seq),
            'index=' .. tostring(npc.index),
            'actor=' .. npc.hex,
            'code=' .. tostring(npc.code),
            'sector=' .. tostring(state.own.sector),
            'zone=' .. format_hex_u64(zone),
            ('game_time=%.17g'):format(gt),
            ('capture_time=%.17g'):format(now),
            ('x=%.17g'):format(pose.x),
            ('y=%.17g'):format(pose.y),
            ('z=%.17g'):format(pose.z),
            ('yaw=%.9f'):format(pose.yaw),
            ('pitch=%.9f'):format(pose.pitch),
            ('roll=%.9f'):format(pose.roll),
            'vx=' .. vx,
            'vy=' .. vy,
            'vz=' .. vz,
            ('speed=%.6f'):format(speed),
        }, ' '))
        return
    end
end

-- =========================================================================
-- PHASE 2: Ordinary Windows Receive/Playback Client (cold reload save_009)
-- =========================================================================
local function make_npc_reader()
    local function inspect_npc()
        if not C.IsPlayerValid() or C.IsGamePaused() then return nil end
        if C.GetPlayerControlledShipID() ~= state.own.actor then return nil end
        local actor = state.npc.actor
        if actor == state.own.actor or actor == state.unrelated.actor then return nil end
        if not C.IsValidComponent(actor) or not C.IsComponentOperational(actor)
                or not C.IsComponentClass(actor, 'ship') then return nil end
        local sector = C.GetContextByClass(actor, 'sector', false)
        local own_sector = C.GetContextByClass(state.own.actor, 'sector', false)
        if sector == 0 or sector ~= own_sector then return nil end
        local ship_ref = ref_from_u64(actor)
        local sec_ref = ref_from_u64(sector)
        if not ship_ref or not sec_ref then return nil end
        local owner, docked, docking, stored = GetComponentData(
            ship_ref, 'owner', 'isdocked', 'isdocking', 'isinternallystored')
        if owner == 'player' or docked ~= false or docking ~= false or stored ~= false then return nil end
        local sec_macro = GetComponentData(sec_ref, 'macro')
        local code_ptr = C.GetObjectIDCode(actor)
        if sec_macro ~= state.npc.sector or code_ptr == nil or code_ptr == ffi.NULL then return nil end
        local code = ffi.string(code_ptr)
        if code ~= state.npc.code then return nil end
        return {
            epoch = cfg.epoch,
            sector_macro = sec_macro,
            index = state.npc.index,
            actor = state.npc.hex,
            code = code,
            mode = 'npc_registered',
        }
    end

    local reader = {}
    function reader:tick()
        return inspect_npc()
    end
    function reader:matches(epoch, sector_macro, index, actor_hex, code)
        local cur = inspect_npc()
        return cur ~= nil and cur.epoch == epoch and cur.sector_macro == sector_macro
            and cur.index == index and cur.actor == actor_hex and cur.code == code
    end
    function reader:mode()
        return 'npc_registered'
    end
    function reader:close() end
    return reader
end

local function make_npc_adapter(npc_reader)
    local adapter = {}
    function adapter:bind(binding)
        if not binding or binding.epoch ~= cfg.epoch or binding.index ~= state.npc.index
                or binding.actor ~= state.npc.hex or binding.code ~= state.npc.code
                or binding.sector_macro ~= state.npc.sector then
            return nil
        end
        if not npc_reader:matches(binding.epoch, binding.sector_macro, binding.index, binding.actor, binding.code) then
            return nil
        end
        local closed = false
        local session = {}
        function session:guard()
            if closed then return false end
            return npc_reader:matches(binding.epoch, binding.sector_macro, binding.index, binding.actor, binding.code)
        end
        function session:read()
            if not session:guard() then return nil end
            return read_pose(state.npc.actor)
        end
        function session:write(target, _before)
            if not session:guard() then return false end
            assert(state.npc.actor ~= state.unrelated.actor and state.npc.actor ~= state.own.actor,
                'refused write outside registered NPC')
            local sector = C.GetContextByClass(state.npc.actor, 'sector', false)
            if sector == 0 then return false end
            local live = read_pose(state.npc.actor)
            -- Preserve native orientation (SetObjectSectorPos expects degrees).
            C.SetObjectSectorPos(state.npc.actor, sector, ffi.new('UIPosRot', {
                x = target.x, y = target.y, z = target.z,
                yaw = math.deg(live.yaw), pitch = math.deg(live.pitch), roll = math.deg(live.roll),
            }))
            state.npc_writes = state.npc_writes + 1
            return true
        end
        function session:close()
            closed = true
        end
        return session
    end
    return adapter
end

local function step_playback()
    if state.stage == 'save_list' then
        if not C.IsStartmenu() then error('left start menu before save list loaded') end
        if not C.IsSaveListLoadingComplete() then return end
        local entries = GetSaveList({[SAVE_SLOT] = true})
        assert(type(entries) == 'table', 'save list unavailable')
        local matches = 0
        for _, entry in pairs(entries) do
            if entry.filename == SAVE_SLOT then matches = matches + 1 end
        end
        assert(matches == 1, 'expected saved checkpoint ' .. SAVE_SLOT)
        state.stage = 'loading'
        log('AUTH_NPC_PLAYBACK_LOAD', 'slot=' .. SAVE_SLOT)
        LoadGame(SAVE_SLOT)
        return
    end
    if state.stage == 'loading' then return end

    if C.IsStartmenu() then
        check_profile()
        C.ReloadSaveList()
        state.stage = 'save_list'
        log('AUTH_NPC_PLAYBACK_SAVE_LIST', 'slot=' .. SAVE_SLOT)
        return
    end
    if not C.IsPlayerValid() then return end

    if state.stage == 'initial' then
        check_profile()
        if C.IsGamePaused() then
            Unpause()
            return
        end
        load_packaged_udp()
        state.udp_net = assert(state.udp_lib.open('127.0.0.1', cfg.udp_port), 'udp open failed')
        state.probe_reply = nil
        local rid = next_req('resolve')
        assert(AddUITriggeredEvent('X4AuthorityWindowsNpcPlayback', 'ResolveSaved', {
            request_id = rid,
            epoch = cfg.epoch,
            own_index = cfg.own_index,
            npc_index = cfg.npc_index,
            unrelated_index = cfg.unrelated_index,
        }) ~= false)
        state.stage = 'wait_resolve'
        return
    end

    if state.stage == 'wait_resolve' then
        if not state.probe_reply then return end
        local p = split(state.probe_reply, '|')
        assert(p[2] == 'Resolved', 'ResolveSaved refused: ' .. state.probe_reply)
        local reg_last = assert(tonumber(p[3]))
        local own_actor = assert(resolve.parse_hex(p[5]))
        local npc_actor = assert(resolve.parse_hex(p[9]))
        local unrel_actor = assert(resolve.parse_hex(p[15]))
        state.own = {
            index = assert(tonumber(p[4])), actor = own_actor, hex = format_hex_u64(own_actor),
            code = p[6], sector = p[7],
        }
        state.npc = {
            index = assert(tonumber(p[8])), actor = npc_actor, hex = format_hex_u64(npc_actor),
            code = p[10], owner = p[11], sector = p[12], dist_m = tonumber(p[13]) or 0,
        }
        state.unrelated = {
            index = assert(tonumber(p[14])), actor = unrel_actor, hex = format_hex_u64(unrel_actor),
            code = p[16], sector = p[17], dist_m = tonumber(p[18]) or 0,
        }
        state.own_init_pose = read_pose(own_actor)
        state.npc_init_pose = read_pose(npc_actor)
        state.unrelated_init_pose = read_pose(unrel_actor)

        -- Lock the unrelated visible ship on the player's stock HUD so it is actively
        -- targeted and visible in the cockpit while the registered NPC moves.
        C.SetSofttarget(unrel_actor, '')
        local tgt_ref = GetPlayerTarget()
        local tgt_u64 = tgt_ref and resolve.actor_u64(ConvertIDTo64Bit(tgt_ref)) or nil
        local screen = C.GetComponentScreenPosition(unrel_actor, 32, 32)
        local hud_locked = (tgt_u64 == unrel_actor)

        -- Empirically verify that b418a409's unmodified Company-only hull playback gate
        -- refuses a registered non-player AI-piloted NPC target.
        local company_reader_stub = {
            matches = function(_, epoch, sector, index, actor, code)
                return epoch == cfg.epoch and sector == state.npc.sector and index == state.npc.index
                    and actor == state.npc.hex and code == state.npc.code
            end,
        }
        local company_adapter = assert(X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4({
            enabled = true,
            pinned = {epoch = cfg.epoch, nonce = cfg.nonce, issued_count = reg_last},
            own_index = state.own.index,
            observer = company_reader_stub,
            guard = function() return not C.IsGamePaused() end,
        }))
        local company_bind = company_adapter:bind({
            slot = 'cockpit', serial = 1, epoch = cfg.epoch, index = state.npc.index,
            actor = state.npc.hex, code = state.npc.code, sector_macro = state.npc.sector,
            generation = cfg.authority_generation,
        })
        assert(company_bind == nil, 'unmodified Company hull playback unexpectedly accepted NPC')
        log('AUTH_NPC_COMPANY_GATE_CHECK', ('refused=true index=%d code=%s owner=%s'):format(
            state.npc.index, state.npc.code, state.npc.owner))

        log('AUTH_NPC_PLAYBACK_RESOLVED', table.concat({
            'total_registry=' .. tostring(reg_last),
            'own_index=' .. tostring(state.own.index),
            'own_code=' .. tostring(state.own.code),
            'own_init=' .. fmt_xyz(state.own_init_pose),
            'npc_index=' .. tostring(state.npc.index),
            'npc_actor=' .. state.npc.hex,
            'npc_code=' .. tostring(state.npc.code),
            'npc_owner=' .. tostring(state.npc.owner),
            'npc_sector=' .. tostring(state.npc.sector),
            ('npc_dist_m=%.3f'):format(state.npc.dist_m),
            'npc_init=' .. fmt_xyz(state.npc_init_pose),
            'npc_init_att_deg=' .. fmt_att_deg(state.npc_init_pose),
            'unrelated_index=' .. tostring(state.unrelated.index),
            'unrelated_actor=' .. state.unrelated.hex,
            'unrelated_code=' .. tostring(state.unrelated.code),
            ('unrelated_dist_m=%.3f'):format(state.unrelated.dist_m),
            'unrelated_hud_locked=' .. tostring(hud_locked),
            'unrelated_onscreen=' .. tostring(screen and screen.onScreen == true),
            'unrelated_init=' .. fmt_xyz(state.unrelated_init_pose),
        }, ' '))

        state.probe_reply = nil
        local rid = next_req('prepare')
        assert(AddUITriggeredEvent('X4AuthorityWindowsNpcPlayback', 'PreparePlayback', {
            request_id = rid,
            epoch = cfg.epoch,
            npc_index = state.npc.index,
            expected_sector = state.npc.sector,
        }) ~= false)
        state.stage = 'wait_prepare'
        return
    end

    if state.stage == 'wait_prepare' then
        if not state.probe_reply then return end
        local p = split(state.probe_reply, '|')
        assert(p[2] == 'PlaybackReady', 'PreparePlayback refused: ' .. state.probe_reply)
        state.npc_init_pose = read_pose(state.npc.actor)
        state.unrelated_init_pose = read_pose(state.unrelated.actor)
        state.own_init_pose = read_pose(state.own.actor)
        local tgt_ref = GetPlayerTarget()
        local tgt_u64 = tgt_ref and resolve.actor_u64(ConvertIDTo64Bit(tgt_ref)) or nil
        local hud_locked_ready = (tgt_u64 == state.unrelated.actor)
        log('AUTH_NPC_PLAYBACK_READY', table.concat({
            'npc_index=' .. tostring(state.npc.index),
            'npc_post_hold=' .. fmt_xyz(state.npc_init_pose),
            'npc_post_hold_att_deg=' .. fmt_att_deg(state.npc_init_pose),
            'unrelated_post_hold=' .. fmt_xyz(state.unrelated_init_pose),
            'unrelated_hud_locked=' .. tostring(hud_locked_ready),
            'own_post_hold=' .. fmt_xyz(state.own_init_pose),
        }, ' '))

        local npc_reader = make_npc_reader()
        local noop_map_reader = {
            tick = function() return nil end,
            matches = function() return false end,
            close = function() end,
        }
        local npc_adapter = make_npc_adapter(npc_reader)
        local last_logged_rb_t = 0.0
        local function record_fn(ev)
            if ev.kind ~= 'readback' then return true end
            state.readback_count = state.readback_count + 1
            local elapsed_ms = ev.elapsed * 1000.0
            if state.min_elapsed_ms == nil or elapsed_ms < state.min_elapsed_ms then
                state.min_elapsed_ms = elapsed_ms
            end
            if elapsed_ms > state.max_elapsed_ms then state.max_elapsed_ms = elapsed_ms end
            if ev.error > state.max_readback_err then state.max_readback_err = ev.error end
            local live_pose = read_pose(state.npc.actor)
            local att_delta = max_att_diff_deg(live_pose, state.npc_init_pose)
            if att_delta > state.max_att_delta_deg then state.max_att_delta_deg = att_delta end
            local disp = dist3(ev.actual, state.npc_init_pose)
            local row = {
                kind = ev.kind, slot = ev.slot, serial = ev.serial, epoch = ev.epoch,
                index = ev.index, code = ev.code, sector_macro = ev.sector_macro,
                generation = ev.generation, sequence = ev.source_sequence,
                source_time = ev.source_time, capture_time = ev.capture_time,
                elapsed = ev.elapsed, error = ev.error, displacement = disp,
                target = ev.target, actual = ev.actual, att_delta_deg = att_delta,
                att_deg = fmt_att_deg(live_pose),
            }
            state.last_readback = row
            local now = state.udp_lib.monotonic()
            if state.readback_count <= 5 or (now - last_logged_rb_t) >= 0.35 then
                last_logged_rb_t = now
                log('AUTH_NPC_PLAYBACK_READBACK', table.concat({
                    'n=' .. tostring(state.readback_count),
                    'kind=' .. tostring(row.kind),
                    'slot=' .. tostring(row.slot),
                    ('serial=%.0f'):format(row.serial),
                    'epoch=' .. tostring(row.epoch),
                    'index=' .. tostring(row.index),
                    'ship=' .. tostring(row.code),
                    'sector=' .. tostring(row.sector_macro),
                    'generation=' .. tostring(row.generation),
                    'sequence=' .. tostring(row.sequence),
                    ('source_time=%.17g'):format(row.source_time),
                    ('capture_time=%.17g'):format(row.capture_time),
                    ('elapsed=%.17g'):format(row.elapsed),
                    'target=' .. fmt_xyz(row.target),
                    'actual=' .. fmt_xyz(row.actual),
                    ('error=%.17g'):format(row.error),
                    ('displacement=%.17g'):format(row.displacement),
                    ('att_delta_deg=%.6f'):format(row.att_delta_deg),
                    'att_deg=' .. row.att_deg,
                }, ' '))
            end
            return true
        end

        state.playback_ctrl = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK.new({
            enabled = true,
            slot = 'cockpit',
            own_index = state.own.index,
            target_index = state.npc.index,
            allow_reacquire_align = false,
            adapter = npc_adapter,
            clock = state.udp_lib.monotonic,
            game = function() return C.GetCurrentGameTime() end,
            paused = function() return C.IsGamePaused() end,
            record = record_fn,
            fault = function(reason, uncertain, stage_name)
                log('AUTH_NPC_PLAYBACK_STOP', ('reason=%s uncertain=%s stage=%s'):format(
                    tostring(reason), tostring(uncertain), tostring(stage_name)))
            end,
            on_event = function(kind, detail)
                local parts = {'kind=' .. tostring(kind)}
                for k, v in pairs(detail or {}) do
                    parts[#parts + 1] = tostring(k) .. '=' .. tostring(v)
                end
                log('AUTH_NPC_PLAYBACK_EVENT', table.concat(parts, ' '))
            end,
        }))

        local prefix = 'C|' .. cfg.udp_lease .. '|' .. cfg.udp_generation .. '|'
        state.stream = assert(X4LAN_COMPANY_OBSERVER_STREAM.new(
            {cockpit = npc_reader, map = noop_map_reader},
            cfg.epoch, state.udp_net, prefix, state.udp_lib.monotonic, state.playback_ctrl))
        state.last_tick_qpc = state.udp_lib.monotonic()
        state.stage = 'streaming'
        return
    end

    if state.stage == 'streaming' then
        local now = state.udp_lib.monotonic()
        if state.last_tick_qpc ~= nil then
            local gap = now - state.last_tick_qpc
            if gap > state.max_frame_gap_s then state.max_frame_gap_s = gap end
        end
        state.last_tick_qpc = now
        state.tick_count = state.tick_count + 1
        state.stream:tick(true)

        if stop_flag_present() then
            if state.last_readback then
                local row = state.last_readback
                log('AUTH_NPC_PLAYBACK_READBACK_FINAL', table.concat({
                    'n=' .. tostring(state.readback_count),
                    'kind=' .. tostring(row.kind),
                    'slot=' .. tostring(row.slot),
                    ('serial=%.0f'):format(row.serial),
                    'epoch=' .. tostring(row.epoch),
                    'index=' .. tostring(row.index),
                    'ship=' .. tostring(row.code),
                    'sector=' .. tostring(row.sector_macro),
                    'generation=' .. tostring(row.generation),
                    'sequence=' .. tostring(row.sequence),
                    ('source_time=%.17g'):format(row.source_time),
                    ('capture_time=%.17g'):format(row.capture_time),
                    ('elapsed=%.17g'):format(row.elapsed),
                    'target=' .. fmt_xyz(row.target),
                    'actual=' .. fmt_xyz(row.actual),
                    ('error=%.17g'):format(row.error),
                    ('displacement=%.17g'):format(row.displacement),
                    ('att_delta_deg=%.6f'):format(row.att_delta_deg),
                    'att_deg=' .. row.att_deg,
                }, ' '))
            end
            local st = state.playback_ctrl:stats()
            log('AUTH_NPC_PLAYBACK_STATS', table.concat({
                'binds=' .. tostring(st.binds),
                'writes=' .. tostring(st.writes),
                'adapter_writes=' .. tostring(state.npc_writes),
                'readbacks=' .. tostring(st.readbacks),
                'stale_cutoffs=' .. tostring(st.stale_cutoffs),
                'withdrawals=' .. tostring(st.withdrawals),
                'alignments=' .. tostring(st.alignments),
                'refusals_over_50m=' .. tostring(st.refusals_over_50m),
                'generation_rejections=' .. tostring(st.generation_rejections),
                ('max_readback_err_m=%.6f'):format(state.max_readback_err),
                ('min_elapsed_ms=%.3f'):format(state.min_elapsed_ms or 0),
                ('max_elapsed_ms=%.3f'):format(state.max_elapsed_ms),
                ('max_att_delta_deg=%.6f'):format(state.max_att_delta_deg),
                'ticks=' .. tostring(state.tick_count),
                ('max_frame_gap_ms=%.3f'):format(state.max_frame_gap_s * 1000.0),
            }, ' '))
            state.stream:close()
            state.stream = nil
            state.probe_reply = nil
            local rid = next_req('final_rb')
            assert(AddUITriggeredEvent('X4AuthorityWindowsNpcPlayback', 'FinalReadback', {
                request_id = rid,
                epoch = cfg.epoch,
                own_index = state.own.index,
                npc_index = state.npc.index,
                unrelated_index = state.unrelated.index,
            }) ~= false)
            state.stage = 'wait_final_readback'
        end
        return
    end

    if state.stage == 'wait_final_readback' then
        if not state.probe_reply then return end
        local p = split(state.probe_reply, '|')
        assert(p[2] == 'FinalReadback', 'FinalReadback refused: ' .. state.probe_reply)
        local npc_final = read_pose(state.npc.actor)
        local unrel_final = read_pose(state.unrelated.actor)
        local own_final = read_pose(state.own.actor)
        local tgt_ref = GetPlayerTarget()
        local tgt_u64 = tgt_ref and resolve.actor_u64(ConvertIDTo64Bit(tgt_ref)) or nil
        local hud_still_locked = (tgt_u64 == state.unrelated.actor)
        log('AUTH_NPC_FINAL_READBACK', table.concat({
            'npc_index=' .. tostring(state.npc.index),
            'npc_code=' .. tostring(state.npc.code),
            'npc_final=' .. fmt_xyz(npc_final),
            'npc_final_att_deg=' .. fmt_att_deg(npc_final),
            ('npc_disp_m=%.6f'):format(dist3(npc_final, state.npc_init_pose)),
            ('npc_att_delta_deg=%.6f'):format(max_att_diff_deg(npc_final, state.npc_init_pose)),
            'unrelated_index=' .. tostring(state.unrelated.index),
            'unrelated_code=' .. tostring(state.unrelated.code),
            'unrelated_final=' .. fmt_xyz(unrel_final),
            ('unrelated_disp_m=%.6f'):format(dist3(unrel_final, state.unrelated_init_pose)),
            'unrelated_hud_still_locked=' .. tostring(hud_still_locked),
            'own_index=' .. tostring(state.own.index),
            'own_code=' .. tostring(state.own.code),
            'own_final=' .. fmt_xyz(own_final),
            ('own_disp_m=%.6f'):format(dist3(own_final, state.own_init_pose)),
        }, ' '))
        state.probe_reply = nil
        local rid = next_req('release')
        assert(AddUITriggeredEvent('X4AuthorityWindowsNpcPlayback', 'Release', {
            request_id = rid,
        }) ~= false)
        state.stage = 'wait_release'
        return
    end

    if state.stage == 'wait_release' then
        if not state.probe_reply then return end
        local p = split(state.probe_reply, '|')
        assert(p[2] == 'Released', 'Release refused: ' .. state.probe_reply)
        log('AUTH_NPC_PLAYBACK_DONE', 'status=ok')
        state.stage = 'done'
        return
    end
end

SetScript('onUpdate', function()
    if state.failed or state.stage == 'done' then return end
    local ok, err = pcall(cfg.phase == 'capture' and step_capture or step_playback)
    if not ok then
        state.failed = true
        if state.stream then pcall(state.stream.close, state.stream) end
        log('AUTH_NPC_ERROR', 'phase=' .. tostring(cfg.phase) .. ' stage=' .. tostring(state.stage) .. ' err=' .. tostring(err))
    end
end)
