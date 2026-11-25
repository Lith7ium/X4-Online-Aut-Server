-- Disposable opt-in single-session live native Windows X4 9.00 harness for the
-- end-to-end registered-NPC path:
--   - Samples one genuine live AI-piloted non-player NPC S in player.sector using
--     packaged x4lan_net_64.dll udp.monotonic() (QPC) and streams live SAMPLE3
--     datagrams over UDP #1 -> NpcMotionSource -> CompanyMotionClient ->
--     CompanyMotionService (translating S's live displacement + velocity onto
--     in-radar registered Argon NPC replica R's origin so one X4.exe instance
--     serves as both live authority source and receiving cockpit; explicitly labeled).
--   - Exercises unmodified X4LAN_COMPANY_OBSERVER_STREAM +
--     X4LAN_COMPANY_COCKPIT_PLAYBACK (allow_reacquire_align=true) +
--     X4LAN_COMPANY_HULL_PLAYBACK + X4LAN_MOTION_BUFFER + X4LAN_MOTION_PLAYBACK
--     wired to opt-in X4LAN_NPC_COCKPIT_OBSERVER (verified radar + map interest)
--     and X4LAN_NPC_HULL_PLAYBACK_X4 over UDP #2.
--   - Stages:
--       0: Live player-ship gate preservation & mutual exclusion check
--       1: Pre-interest (live authority streaming, observer unsubscribed -> 0 delivery, 0 drift)
--       2: Verified cockpit radar interest (~2.1 km <= 40 km radar) -> live displacement
--       3: Withdrawal -> 0 delivery & 0 drift while live authority keeps streaming > 60 m
--       4: Reacquisition -> single authorized alignment + resumed live velocity playback
--       5: Verified map interest slot -> live map SEEN delivery + withdrawal
--       6: Independent final MD + FFI readback on R, unrelated visible ship U, and own ship
local cfg = X4NEON_REGISTERED_NPC_LIVE
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
    'ScreenPos GetComponentScreenPosition(UniverseID componentid, uint32_t width, uint32_t height);',
    'float GetDistanceBetween(UniverseID componentid1, UniverseID componentid2);',
}) do declare(text) end

local resolve = assert(X4LAN_RESOLVE_STRING, 'X4LAN_RESOLVE_STRING required')
local EXT_DIR = './extensions/x4_lan_lab/'

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
    if hi > 0 then return string.format('0x%x%08x', hi, lo) end
    return string.format('0x%x', lo)
end

local function check_profile()
    local path = C.GetSaveFolderPath()
    assert(path ~= nil and path ~= ffi.NULL, 'save folder unavailable')
    local text = ffi.string(path):gsub('\\', '/'):gsub('/+$', '')
    assert(text:match('/' .. tostring(cfg.profile) .. '/save$'),
        'not disposable profile ' .. tostring(cfg.profile) .. ': ' .. text)
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

local function load_cmd(seq)
    local ok, chunk = pcall(loadfile, EXT_DIR .. 'cmd_' .. tostring(seq) .. '.lua')
    if not (ok and type(chunk) == 'function') then return nil end
    local rok, val = pcall(chunk)
    if rok and type(val) == 'table' and val.seq == seq then return val end
    return nil
end

local state = {
    stage = 'initial',
    failed = false,
    started_game = false,
    udp_lib = nil,
    src_net = nil,
    obs_net = nil,
    src_prefix = nil,
    obs_prefix = nil,
    reg_reply = nil,
    probe_reply = nil,
    req_seq = 0,
    total_registry = 0,
    radar_range = 40000,
    own = nil,
    unrelated = nil,
    replica = nil,
    live_src = nil,
    candidates = {},
    scout_started = nil,
    -- Initial reference poses
    own_init = nil,
    unrelated_init = nil,
    replica_init = nil,
    src_origin = nil,
    -- Live authority sampler state
    sampling_active = false,
    sample_seq = 0,
    last_sample_t = nil,
    last_src_pose = nil,
    peak_src_speed = 0.0,
    -- Observer & playback state
    map_open_flag = false,
    radar_obs = nil,
    map_obs = nil,
    playback_ctrl = nil,
    stream = nil,
    readbacks = 0,
    align_readbacks = 0,
    max_rb_err = 0.0,
    min_rb_ms = nil,
    max_rb_ms = 0.0,
    max_att_delta = 0.0,
    last_rb = nil,
    last_align_rb = nil,
    last_tick_qpc = nil,
    max_frame_gap_s = 0.0,
    ticks = 0,
    -- Host command loop
    next_cmd_seq = 1,
    active_cmd = nil,
}

local function next_req(tag)
    state.req_seq = state.req_seq + 1
    return tag .. '-' .. tostring(state.req_seq)
end

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.reg_reply = tostring(value)
end)

RegisterEvent('X4AuthorityRegisteredNpcLive.Result', function(_, value)
    state.probe_reply = tostring(value)
end)

local function load_packaged_udp()
    if state.udp_lib then return state.udp_lib end
    local loader = assert(X4LAN_LOCAL_UDP_MODULE, 'X4LAN_LOCAL_UDP_MODULE required')
    local lib = assert(loader.load(ffi.os, package.loadlib), 'packaged local_udp_module load failed')
    assert(type(lib.open) == 'function' and type(lib.monotonic) == 'function', 'packaged DLL missing open/monotonic')
    local t1 = lib.monotonic()
    local t2 = lib.monotonic()
    assert(t1 > 0 and t2 >= t1, 'invalid udp.monotonic')
    log('AUTH_LIVE_NPC_DLL_CLOCK', ('mono1=%.9f mono2=%.9f delta_ns=%.0f'):format(t1, t2, (t2 - t1) * 1e9))
    state.udp_lib = lib
    return lib
end

local function tick_live_authority_sampler(now)
    if not state.sampling_active or not state.src_net or C.IsGamePaused() then return end
    if state.last_sample_t ~= nil and (now - state.last_sample_t) < 0.095 then return end
    local src = state.live_src
    if not C.IsValidComponent(src.actor) or not C.IsComponentOperational(src.actor) then return end
    local src_pose = read_pose(src.actor)
    local gt = tonumber(C.GetCurrentGameTime()) or 0
    if state.src_origin == nil then
        state.src_origin = src_pose
    end
    -- Translate live AI NPC S's real-time displacement onto in-radar replica R's origin
    -- (single-instance local-origin translation link; explicitly labeled in evidence).
    local tx = state.replica_init.x + (src_pose.x - state.src_origin.x)
    local ty = state.replica_init.y + (src_pose.y - state.src_origin.y)
    local tz = state.replica_init.z + (src_pose.z - state.src_origin.z)
    local vx_s, vy_s, vz_s, speed = '-', '-', '-', 0.0
    if state.last_sample_t ~= nil and state.last_src_pose ~= nil then
        local dt = now - state.last_sample_t
        if dt > 0 then
            local vx = (src_pose.x - state.last_src_pose.x) / dt
            local vy = (src_pose.y - state.last_src_pose.y) / dt
            local vz = (src_pose.z - state.last_src_pose.z) / dt
            speed = math.sqrt(vx * vx + vy * vy + vz * vz)
            if speed > state.peak_src_speed then state.peak_src_speed = speed end
            vx_s = ('%.17g'):format(vx)
            vy_s = ('%.17g'):format(vy)
            vz_s = ('%.17g'):format(vz)
        end
    end
    state.sample_seq = state.sample_seq + 1
    state.last_sample_t = now
    state.last_src_pose = src_pose
    local pkt = state.src_prefix .. table.concat({
        'SAMPLE3', cfg.epoch, tostring(state.replica.index), tostring(state.sample_seq),
        state.replica.sector, ('%.17g'):format(gt), ('%.17g'):format(now),
        ('%.17g'):format(tx), ('%.17g'):format(ty), ('%.17g'):format(tz),
        vx_s, vy_s, vz_s, '',
    }, '|')
    state.src_net:send(pkt)
end

local function emit_cmd_resp(seq, kv)
    local parts = {'seq=' .. tostring(seq)}
    for k, v in pairs(kv) do parts[#parts + 1] = tostring(k) .. '=' .. tostring(v) end
    log('AUTH_LIVE_NPC_RESP', table.concat(parts, ' '))
end

local function run_command(cmd)
    local op = cmd.op
    if op == 'configure_links' then
        state.src_net = assert(state.udp_lib.open('127.0.0.1', assert(cmd.src_port)), 'src udp open failed')
        state.obs_net = assert(state.udp_lib.open('127.0.0.1', assert(cmd.obs_port)), 'obs udp open failed')
        state.src_prefix = 'N|' .. assert(cmd.src_lease) .. '|' .. assert(cmd.src_generation) .. '|'
        state.obs_prefix = 'C|' .. assert(cmd.obs_lease) .. '|' .. assert(cmd.obs_generation) .. '|'

        local pinned = {epoch = cfg.epoch, nonce = cfg.nonce, issued_count = state.total_registry}
        local function reg_ready() return C.IsPlayerValid() and not C.IsGamePaused() end

        -- Stage 0: Live mutual-exclusion & player-ship gate check in X4.
        local rep_stub_obs = {
            matches = function(_, ep, sec, idx, act, code)
                return ep == cfg.epoch and sec == state.replica.sector and idx == state.replica.index
                    and act == state.replica.hex and code == state.replica.code
            end,
        }
        local unrel_stub_obs = {
            matches = function(_, ep, sec, idx, act, code)
                return ep == cfg.epoch and sec == state.unrelated.sector and idx == state.unrelated.index
                    and act == state.unrelated.hex and code == state.unrelated.code
            end,
        }
        local company_adapter = assert(X4LAN_COMPANY_HULL_PLAYBACK_X4.new_x4({
            enabled = true, pinned = pinned, own_index = state.own.index,
            observer = rep_stub_obs, guard = reg_ready,
        }))
        local company_bind_npc = company_adapter:bind({
            slot = 'cockpit', serial = 1, epoch = cfg.epoch, index = state.replica.index,
            actor = state.replica.hex, code = state.replica.code, sector_macro = state.replica.sector,
        })
        assert(company_bind_npc == nil, 'unmodified Company adapter unexpectedly accepted NPC')

        local npc_check_adapter = assert(X4LAN_NPC_HULL_PLAYBACK_X4.new_x4({
            enabled = true, pinned = pinned, own_index = state.own.index,
            observer = unrel_stub_obs, guard = reg_ready,
        }))
        local npc_bind_player = npc_check_adapter:bind({
            slot = 'cockpit', serial = 1, epoch = cfg.epoch, index = state.unrelated.index,
            actor = state.unrelated.hex, code = state.unrelated.code, sector_macro = state.unrelated.sector,
        })
        assert(npc_bind_player == nil, 'opt-in NPC adapter unexpectedly accepted player-owned ship')

        -- Wire opt-in radar + map observers and NPC playback adapter into unmodified
        -- X4LAN_COMPANY_COCKPIT_PLAYBACK + X4LAN_COMPANY_OBSERVER_STREAM.
        state.radar_obs = assert(X4LAN_NPC_COCKPIT_OBSERVER.new_x4(pinned, state.own.index, reg_ready, {
            enabled = true,
            target_index = state.replica.index,
            mode = 'radar',
            initial_interest = false,
            clock = state.udp_lib.monotonic,
            on_withdrawal = function(reason)
                log('AUTH_LIVE_NPC_OBSERVER_EVENT', 'slot=cockpit kind=withdrawal reason=' .. tostring(reason))
            end,
            on_reacquire = function(row)
                log('AUTH_LIVE_NPC_OBSERVER_EVENT', ('slot=cockpit kind=reacquire index=%d dist_m=%.3f radar_m=%.1f'):format(
                    row.index, row.dist_m or -1, row.radar_range_m or -1))
            end,
        }))
        state.map_obs = assert(X4LAN_NPC_COCKPIT_OBSERVER.new_x4(pinned, state.own.index, reg_ready, {
            enabled = true,
            target_index = state.replica.index,
            mode = 'map',
            initial_interest = false,
            map_open = function() return state.map_open_flag end,
            clock = state.udp_lib.monotonic,
            on_withdrawal = function(reason)
                log('AUTH_LIVE_NPC_OBSERVER_EVENT', 'slot=map kind=withdrawal reason=' .. tostring(reason))
            end,
        }))

        local npc_adapter = assert(X4LAN_NPC_HULL_PLAYBACK_X4.new_x4({
            enabled = true,
            pinned = pinned,
            own_index = state.own.index,
            observer = state.radar_obs,
            guard = reg_ready,
        }))

        local last_rb_log = 0.0
        local function record_fn(ev)
            if ev.kind ~= 'readback' and ev.kind ~= 'alignment_readback' then return true end
            local is_align = (ev.kind == 'alignment_readback')
            local elapsed_ms = ev.elapsed * 1000.0
            if state.min_rb_ms == nil or elapsed_ms < state.min_rb_ms then state.min_rb_ms = elapsed_ms end
            if elapsed_ms > state.max_rb_ms then state.max_rb_ms = elapsed_ms end
            if ev.error > state.max_rb_err then state.max_rb_err = ev.error end
            local live_pose = read_pose(state.replica.actor)
            local att_delta = max_att_diff_deg(live_pose, state.replica_init)
            if att_delta > state.max_att_delta then state.max_att_delta = att_delta end
            local disp = dist3(ev.actual, state.replica_init)
            if is_align then
                state.align_readbacks = state.align_readbacks + 1
                state.last_align_rb = {
                    sequence = ev.source_sequence, error = ev.error, elapsed_ms = elapsed_ms,
                    displacement = disp, target = ev.target, actual = ev.actual, serial = ev.serial,
                    generation = ev.generation,
                }
            else
                state.readbacks = state.readbacks + 1
                state.last_rb = {
                    sequence = ev.source_sequence, error = ev.error, elapsed_ms = elapsed_ms,
                    displacement = disp, target = ev.target, actual = ev.actual, serial = ev.serial,
                    generation = ev.generation,
                }
            end
            local now = state.udp_lib.monotonic()
            if is_align or state.readbacks <= 4 or (now - last_rb_log) >= 0.4 then
                last_rb_log = now
                log('AUTH_LIVE_NPC_READBACK', table.concat({
                    'kind=' .. tostring(ev.kind),
                    'n=' .. tostring(is_align and state.align_readbacks or state.readbacks),
                    ('serial=%.0f'):format(ev.serial),
                    'epoch=' .. tostring(ev.epoch),
                    'index=' .. tostring(ev.index),
                    'ship=' .. tostring(ev.code),
                    'sector=' .. tostring(ev.sector_macro),
                    'generation=' .. tostring(ev.generation),
                    'sequence=' .. tostring(ev.source_sequence),
                    ('elapsed_ms=%.3f'):format(elapsed_ms),
                    ('error_m=%.6f'):format(ev.error),
                    ('displacement_m=%.6f'):format(disp),
                    ('att_delta_deg=%.6f'):format(att_delta),
                    'target=' .. fmt_xyz(ev.target),
                    'actual=' .. fmt_xyz(ev.actual),
                }, ' '))
            end
            return true
        end

        state.playback_ctrl = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK.new({
            enabled = true,
            slot = 'cockpit',
            own_index = state.own.index,
            target_index = state.replica.index,
            allow_reacquire_align = true,
            adapter = npc_adapter,
            clock = state.udp_lib.monotonic,
            game = function() return C.GetCurrentGameTime() end,
            paused = function() return C.IsGamePaused() end,
            record = record_fn,
            fault = function(reason, uncertain, stage_name)
                log('AUTH_LIVE_NPC_PLAYBACK_STOP', ('reason=%s uncertain=%s stage=%s'):format(
                    tostring(reason), tostring(uncertain), tostring(stage_name)))
            end,
            on_event = function(kind, detail)
                local parts = {'kind=' .. tostring(kind)}
                for k, v in pairs(detail or {}) do parts[#parts + 1] = tostring(k) .. '=' .. tostring(v) end
                log('AUTH_LIVE_NPC_PLAYBACK_EVENT', table.concat(parts, ' '))
            end,
        }))

        state.stream = assert(X4LAN_COMPANY_OBSERVER_STREAM.new(
            {cockpit = state.radar_obs, map = state.map_obs},
            cfg.epoch, state.obs_net, state.obs_prefix, state.udp_lib.monotonic, state.playback_ctrl))
        state.last_tick_qpc = state.udp_lib.monotonic()
        state.sampling_active = true
        emit_cmd_resp(cmd.seq, {
            status = 'links_configured',
            company_refused_npc = 'true',
            npc_refused_player_ship = 'true',
        })
        return
    end

    if op == 'set_radar_interest' then
        local active = (cmd.active == true)
        state.radar_obs:set_interest(active)
        emit_cmd_resp(cmd.seq, {status = 'radar_interest_set', active = tostring(active)})
        return
    end

    if op == 'set_map_interest' then
        local active = (cmd.active == true)
        state.map_open_flag = active
        state.map_obs:set_interest(active)
        emit_cmd_resp(cmd.seq, {status = 'map_interest_set', active = tostring(active)})
        return
    end

    if op == 'probe_state' then
        local rep_p = read_pose(state.replica.actor)
        local unrel_p = read_pose(state.unrelated.actor)
        local own_p = read_pose(state.own.actor)
        local src_p = read_pose(state.live_src.actor)
        local pst = state.playback_ctrl:stats()
        local rost = state.radar_obs:stats()
        local most = state.map_obs:stats()
        emit_cmd_resp(cmd.seq, {
            status = 'probed',
            sample_seq = state.sample_seq,
            src_disp_m = ('%.6f'):format(state.src_origin and dist3(src_p, state.src_origin) or 0.0),
            peak_src_speed_mps = ('%.6f'):format(state.peak_src_speed),
            rep_x = ('%.6f'):format(rep_p.x),
            rep_y = ('%.6f'):format(rep_p.y),
            rep_z = ('%.6f'):format(rep_p.z),
            rep_disp_m = ('%.6f'):format(dist3(rep_p, state.replica_init)),
            rep_att_delta_deg = ('%.6f'):format(max_att_diff_deg(rep_p, state.replica_init)),
            unrelated_disp_m = ('%.6f'):format(dist3(unrel_p, state.unrelated_init)),
            own_disp_m = ('%.6f'):format(dist3(own_p, state.own_init)),
            writes = pst.writes,
            readbacks = state.readbacks,
            align_readbacks = state.align_readbacks,
            alignments = pst.alignments,
            reacquisitions = pst.reacquisitions,
            withdrawals = pst.withdrawals,
            refusals_over_50m = pst.refusals_over_50m,
            radar_observed = tostring(rost.currently_observed),
            radar_dist_m = ('%.3f'):format(rost.last_dist_m or -1),
            radar_range_m = ('%.1f'):format(rost.last_radar_range_m or state.radar_range),
            map_observed = tostring(most.currently_observed),
            max_rb_err_m = ('%.6f'):format(state.max_rb_err),
            min_rb_ms = ('%.3f'):format(state.min_rb_ms or 0),
            max_rb_ms = ('%.3f'):format(state.max_rb_ms),
            max_att_delta_deg = ('%.6f'):format(state.max_att_delta),
            ticks = state.ticks,
            max_frame_gap_ms = ('%.3f'):format(state.max_frame_gap_s * 1000.0),
        })
        return
    end

    if op == 'finish' then
        state.sampling_active = false
        if state.stream then state.stream:close(); state.stream = nil end
        state.probe_reply = nil
        state.active_cmd = cmd
        local rid = next_req('final_rb')
        assert(AddUITriggeredEvent('X4AuthorityRegisteredNpcLive', 'FinalReadback', {
            request_id = rid,
            epoch = cfg.epoch,
            own_index = state.own.index,
            replica_index = state.replica.index,
            unrelated_index = state.unrelated.index,
        }) ~= false)
        state.stage = 'wait_final_readback'
        return
    end
end

local function frame()
    if state.failed or state.stage == 'done' then return end
    if C.IsStartmenu() then
        if not state.started_game then
            check_profile()
            state.started_game = true
            log('AUTH_LIVE_NPC_NEWGAME', 'gamestart=' .. tostring(cfg.gamestart))
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
        local p = split(state.reg_reply, '|')
        assert(p[2] == 'Initialized' and p[3] == cfg.epoch, 'registry init failed: ' .. state.reg_reply)
        state.probe_reply = nil
        local rid = next_req('setup')
        assert(AddUITriggeredEvent('X4AuthorityRegisteredNpcLive', 'Setup', {
            request_id = rid, epoch = cfg.epoch,
        }) ~= false)
        state.stage = 'wait_setup'
        return
    end

    if state.stage == 'wait_setup' then
        if not state.probe_reply then return end
        local p = split(state.probe_reply, '|')
        assert(p[2] == 'Ready', 'Setup refused: ' .. state.probe_reply)
        state.total_registry = assert(tonumber(p[3]))
        local own_actor = assert(resolve.parse_hex(p[5]))
        state.own = {
            index = assert(tonumber(p[4])), actor = own_actor, hex = format_hex_u64(own_actor),
            code = p[6], sector = p[7],
        }
        local unrel_actor = assert(resolve.parse_hex(p[9]))
        state.unrelated = {
            index = assert(tonumber(p[8])), actor = unrel_actor, hex = format_hex_u64(unrel_actor),
            code = p[10], dist_m = tonumber(p[11]) or 0, sector = p[7],
        }
        local rep_actor = assert(resolve.parse_hex(p[13]))
        state.replica = {
            index = assert(tonumber(p[12])), actor = rep_actor, hex = format_hex_u64(rep_actor),
            code = p[14], owner = p[15], dist_m = tonumber(p[16]) or 0, sector = p[7],
        }
        state.radar_range = tonumber(p[17]) or 40000
        local cands_raw = p[18] or ''
        local now = state.udp_lib.monotonic()
        state.candidates = {}
        for entry in (cands_raw .. ';'):gmatch('([^;]+);') do
            local f = split(entry, ',')
            local idx = tonumber(f[1])
            local actor = f[2] and resolve.parse_hex(f[2]) or nil
            if idx and actor and C.IsValidComponent(actor) and C.IsComponentOperational(actor) then
                state.candidates[#state.candidates + 1] = {
                    index = idx, actor = actor, hex = format_hex_u64(actor),
                    code = f[3], owner = f[4], macro = f[5],
                    dist_m = tonumber(f[6]) or 999999,
                    p0 = read_pose(actor), t0 = now,
                }
            end
        end
        assert(#state.candidates >= 1, 'no live AI NPC candidates found in player.sector')
        state.scout_started = now
        state.stage = 'scouting'
        return
    end

    if state.stage == 'scouting' then
        if C.IsGamePaused() then return end
        local now = state.udp_lib.monotonic()
        if now - state.scout_started < 1.5 then return end
        local best_ideal, best_any = nil, nil
        for _, c in ipairs(state.candidates) do
            if C.IsValidComponent(c.actor) and C.IsComponentOperational(c.actor) then
                local sec = C.GetContextByClass(c.actor, 'sector', false)
                local own_sec = C.GetContextByClass(state.own.actor, 'sector', false)
                if sec ~= 0 and sec == own_sec then
                    local p1 = read_pose(c.actor)
                    local dt = now - c.t0
                    local speed = dt > 0 and (dist3(p1, c.p0) / dt) or 0
                    c.scout_speed = speed
                    if speed >= 15.0 and speed <= 40.0 then
                        if not best_ideal or math.abs(speed - 28.0) < math.abs(best_ideal.scout_speed - 28.0) then
                            best_ideal = c
                        end
                    elseif speed >= 10.0 and speed <= 110.0 then
                        if not best_any or math.abs(speed - 35.0) < math.abs(best_any.scout_speed - 35.0) then
                            best_any = c
                        end
                    end
                end
            end
        end
        local chosen = best_ideal or best_any
        assert(chosen ~= nil, 'no moving live AI NPC found in player.sector')
        state.live_src = chosen
        state.own_init = read_pose(state.own.actor)
        state.unrelated_init = read_pose(state.unrelated.actor)
        state.replica_init = read_pose(state.replica.actor)
        local unrel_scr = C.GetComponentScreenPosition(state.unrelated.actor, 32, 32)
        log('AUTH_LIVE_NPC_READY', table.concat({
            'total_registry=' .. tostring(state.total_registry),
            'sector=' .. tostring(state.own.sector),
            ('radar_range_m=%.1f'):format(state.radar_range),
            'own_index=' .. tostring(state.own.index),
            'own_code=' .. tostring(state.own.code),
            'unrelated_index=' .. tostring(state.unrelated.index),
            'unrelated_actor=' .. state.unrelated.hex,
            'unrelated_code=' .. tostring(state.unrelated.code),
            ('unrelated_dist_m=%.3f'):format(state.unrelated.dist_m),
            'unrelated_onscreen=' .. tostring(unrel_scr and unrel_scr.onScreen == true),
            'unrelated_init=' .. fmt_xyz(state.unrelated_init),
            'replica_index=' .. tostring(state.replica.index),
            'replica_actor=' .. state.replica.hex,
            'replica_code=' .. tostring(state.replica.code),
            'replica_owner=' .. tostring(state.replica.owner),
            ('replica_dist_m=%.3f'):format(state.replica.dist_m),
            'replica_within_radar=' .. tostring(state.replica.dist_m <= state.radar_range),
            'replica_init=' .. fmt_xyz(state.replica_init),
            'replica_init_att_deg=' .. fmt_att_deg(state.replica_init),
            'src_index=' .. tostring(chosen.index),
            'src_actor=' .. chosen.hex,
            'src_code=' .. tostring(chosen.code),
            'src_owner=' .. tostring(chosen.owner),
            'src_macro=' .. tostring(chosen.macro),
            ('src_scout_speed_mps=%.3f'):format(chosen.scout_speed),
        }, ' '))
        state.stage = 'running'
        return
    end

    if state.stage == 'running' then
        local now = state.udp_lib.monotonic()
        if state.last_tick_qpc ~= nil then
            local gap = now - state.last_tick_qpc
            if gap > state.max_frame_gap_s then state.max_frame_gap_s = gap end
        end
        state.last_tick_qpc = now
        state.ticks = state.ticks + 1

        tick_live_authority_sampler(now)
        if state.stream then state.stream:tick(not C.IsGamePaused()) end

        local cmd = load_cmd(state.next_cmd_seq)
        if cmd then
            state.next_cmd_seq = state.next_cmd_seq + 1
            run_command(cmd)
        end
        return
    end

    if state.stage == 'wait_final_readback' then
        if not state.probe_reply then return end
        local p = split(state.probe_reply, '|')
        assert(p[2] == 'FinalReadback', 'FinalReadback refused: ' .. state.probe_reply)
        local rep_final = read_pose(state.replica.actor)
        local unrel_final = read_pose(state.unrelated.actor)
        local own_final = read_pose(state.own.actor)
        log('AUTH_LIVE_NPC_FINAL', table.concat({
            'replica_index=' .. tostring(state.replica.index),
            'replica_code=' .. tostring(state.replica.code),
            'replica_final=' .. fmt_xyz(rep_final),
            'replica_final_att_deg=' .. fmt_att_deg(rep_final),
            ('replica_disp_m=%.6f'):format(dist3(rep_final, state.replica_init)),
            ('replica_att_delta_deg=%.6f'):format(max_att_diff_deg(rep_final, state.replica_init)),
            'unrelated_index=' .. tostring(state.unrelated.index),
            'unrelated_code=' .. tostring(state.unrelated.code),
            'unrelated_final=' .. fmt_xyz(unrel_final),
            ('unrelated_disp_m=%.6f'):format(dist3(unrel_final, state.unrelated_init)),
            'own_index=' .. tostring(state.own.index),
            'own_code=' .. tostring(state.own.code),
            'own_final=' .. fmt_xyz(own_final),
            ('own_disp_m=%.6f'):format(dist3(own_final, state.own_init)),
        }, ' '))
        state.probe_reply = nil
        local rid = next_req('release')
        assert(AddUITriggeredEvent('X4AuthorityRegisteredNpcLive', 'Release', {request_id = rid}) ~= false)
        state.stage = 'wait_release'
        return
    end

    if state.stage == 'wait_release' then
        if not state.probe_reply then return end
        local p = split(state.probe_reply, '|')
        assert(p[2] == 'Released', 'Release refused: ' .. state.probe_reply)
        if state.active_cmd then
            emit_cmd_resp(state.active_cmd.seq, {status = 'finished'})
        end
        log('AUTH_LIVE_NPC_DONE', 'status=ok')
        state.stage = 'done'
        return
    end
end

SetScript('onUpdate', function()
    if state.failed or state.stage == 'done' then return end
    local ok, err = pcall(frame)
    if not ok then
        state.failed = true
        state.sampling_active = false
        if state.stream then pcall(state.stream.close, state.stream) end
        log('AUTH_LIVE_NPC_ERROR', 'stage=' .. tostring(state.stage) .. ' err=' .. tostring(err))
    end
end)
