-- Disposable native Windows X4 9.00 driver for overlapping primary-gun hits on one registered NPC.
-- Reuses the read-only X4AuthorityCombatObserver and X4LAN_COMBAT_OBSERVER, plus the test-only
-- X4AuthorityCombatOverlapTrace (stock weapon_fired bullets and impact callbacks).
--   control_pilot:  player-controlled Company Pulsar #1 alone -> Xenon #4
--   control_second: unoccupied Company Pulsar #2 alone -> Xenon #4 (same gun)
--   overlap:        #1 and #2 together -> Xenon #4
--   miss_pilot:     #1's first salvo misses because #4 moves aside while the bullets fly
local cfg = X4NEON_COMBAT_OBS
if type(cfg) ~= 'table' or cfg.enabled ~= true then return end

local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
if not pcall(ffi.typeof, 'UIPosRot') then
    ffi.cdef('typedef struct { float x,y,z,yaw,pitch,roll; } UIPosRot;')
end
declare([[typedef struct {
    const char* Name; const char* RawName; const char* Ware; uint32_t Quality; const char* PropertyType;
    float DamageFactor; float CoolingFactor; float ReloadFactor; float SpeedFactor; float LifeTimeFactor;
    float MiningFactor; float StickTimeFactor; float ChargeTimeFactor; float BeamLengthFactor;
    uint32_t AddedAmount; float RotationSpeedFactor; float SurfaceElementFactor;
} UIWeaponMod;]])
for _, text in ipairs({
    'bool IsStartmenu(void);',
    'bool IsPlayerValid(void);',
    'bool IsGamePaused(void);',
    'const char* GetSaveFolderPath(void);',
    'double GetCurrentGameTime(void);',
    'UniverseID GetPlayerControlledShipID(void);',
    'UniverseID GetPlayerOccupiedShipID(void);',
    'bool IsValidComponent(UniverseID componentid);',
    'bool IsComponentOperational(UniverseID componentid);',
    'UIPosRot GetObjectPositionInSector(UniverseID componentid);',
    'size_t GetNumUpgradeSlots(UniverseID componentid, const char* macroname, const char* upgradetypename);',
    'UniverseID GetUpgradeSlotCurrentComponent(UniverseID componentid, const char* upgradetypename, size_t slot);',
    'bool GetInstalledWeaponMod(UniverseID weaponid, UIWeaponMod* weaponmod);',
    'const char* GetWeaponLaunchedMacro(UniverseID weaponid);',
}) do declare(text) end

local combat_obs = assert(X4LAN_COMBAT_OBSERVER, 'X4LAN_COMBAT_OBSERVER required')

local function log(msg) DebugError('[X4LAN] AUTH_COMBAT_DRIVER ' .. msg) end
local function log_lua_rb(msg) DebugError('[X4LAN] AUTH_COMBAT_LUA ' .. msg) end
local function now() return tonumber(C.GetCurrentGameTime()) or 0 end

local function split(text, sep)
    local parts = {}
    for item in (tostring(text) .. sep):gmatch('(.-)' .. sep) do parts[#parts + 1] = item end
    return parts
end

local function check_profile()
    local path = C.GetSaveFolderPath()
    assert(path ~= nil and path ~= ffi.NULL, 'save folder unavailable')
    local text = ffi.string(path):gsub('\\', '/'):gsub('/+$', '')
    assert(text:match('/' .. tostring(cfg.profile) .. '/save$'), 'not disposable profile ' .. tostring(cfg.profile))
end

local function dodge_position(ship, x)
    local actor = ConvertIDTo64Bit(ship)
    assert(C.IsValidComponent(actor) and C.IsComponentOperational(actor), 'miss target is unavailable')
    local p = C.GetObjectPositionInSector(actor)
    local ok = math.abs(p.x - x) < 1 and math.abs(p.y - 100000) < 1 and math.abs(p.z) < 1
    return ok, string.format('miss target position was not observed want_x=%.1f at=(%.2f,%.2f,%.2f)', x, p.x, p.y, p.z)
end

local function check_dodge_position(ship, x)
    local ok, msg = dodge_position(ship, x)
    assert(ok, msg)
end

-- Target #4 sits at (-6000, 100000, 0). #1 faces it along +z; #2 faces it along +x (yaw 90),
-- and the driver retries #2 at yaw -90 if its bullets miss in the control phase. Both fire from
-- 200 m, so equal flight times put both shooters' salvos into the same frames.
local SECOND_POS = { -6000 - (cfg.second_range or 200), 100000, 0 }
local mixed = cfg.second_loadout ~= nil
local PHASES = {
    { name = 'control_pilot', pre = { { 1, -6000, 100000, -200, 0 }, { 2, SECOND_POS[1], SECOND_POS[2], SECOND_POS[3], 90 } },
      pilot = 'shoot_at', max = 1.2, impacts = mixed and 8 or 10 },
    { name = 'control_second', second = true, max = 1.2, impacts = mixed and 8 or 10, aim_check = true },
    { name = 'overlap', pilot = 'shoot_at', second = true, max = 1.5, impacts = mixed and 34 or 30 },
    -- Stock shoot refuses a weapon that already had a shoot_at controller, shoot_at still hits
    -- from a 30 m lateral offset and holds fire at 120 m. So #1 fires aligned and, once the first
    -- salvo is seen, stops while #4 moves 150 m aside: the bullets in flight pass it.
    -- #4 is put back first: hull hits left in place (no restore) can move it off its spawn pose.
    { name = 'miss_pilot', pre = { { 4, -6000, 100000, 0, 90 }, { 1, -6000, 100000, -200, 0 } }, pilot = 'shoot_at', max = 1.0,
      dodge = { 4, -5850, 100000, 0, 90 } },
    -- Diagnostic crossfire into hull: #4 returns to its spawn pose and both shooters fire again with
    -- the accumulated local damage left in place (never restored), until hull callbacks appear.
    { name = 'overflow_overlap', pre = { { 4, -6000, 100000, 0, 90 }, { 1, -6000, 100000, -200, 0 } },
      pilot = 'shoot_at', second = true, max = 3.0, hull = 12, keep_damage = true },
}

local WATCH_PLAN = {
    { index = 1, label = 'company_player', as_target = true, as_source = true, watch_weapons = true },
    { index = 2, label = 'second_shooter', as_target = true, as_source = true, watch_weapons = true },
    { index = 3, label = 'company_npc_target', as_target = true },
    { index = 4, label = 'npc_target', as_target = true, as_source = false, watch_weapons = false },
    { index = 5, label = 'npc_attacker', as_target = true },
}

local state = {
    step = 'boot', req_id = 0,
    reg_reply = nil, probe_reply = nil, obs_status = nil, trace_reply = nil,
    ships = {}, codes = {}, watch_idx = 0,
    phase_idx = 0, pre_idx = 0, phase = 'init', phase_start = 0, stop_sent = false, settle_until = 0,
    counts = nil, second_mode = 'trace_shoot_at', second_yaw = 90, aim_retried = false, fallback2 = false,
    done = false,
}

local function reset_counts()
    state.counts = { shots1 = 0, shots2 = 0, shield = 0, hull = 0, attacked = 0, ff = 0 }
end
reset_counts()

local observer = combat_obs.new({
    log = log_lua_rb,
    on_event = function(ev)
        local c = state.counts
        if ev.kind == 'weapon_fired' then
            if ev.ev_att_label == 'company_player' then c.shots1 = c.shots1 + 1
            elseif ev.ev_att_label == 'second_shooter' then c.shots2 = c.shots2 + 1 end
            return
        end
        if ev.kind == 'shield_damaged' or ev.kind == 'hull_damaged' then
            if ev.victim_label == 'npc_target' then
                if ev.kind == 'shield_damaged' then c.shield = c.shield + 1 else c.hull = c.hull + 1 end
            else
                c.ff = c.ff + 1
            end
        elseif ev.kind == 'object_attacked' and ev.victim_label == 'npc_target' then
            c.attacked = c.attacked + 1
        end
    end,
})

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value) state.reg_reply = tostring(value) end)
RegisterEvent('X4AuthorityCombatProbe.Result', function(_, value)
    state.probe_reply = tostring(value)
    if state.dodge_request and state.probe_reply:match('^' .. state.dodge_request .. '|') then
        state.dodge_reply = state.probe_reply
    end
end)
RegisterEvent('X4AuthorityCombatObserver.Status', function(_, value) state.obs_status = tostring(value) end)
RegisterEvent('X4AuthorityCombatOverlapTrace.Status', function(_, value) state.trace_reply = tostring(value) end)
RegisterEvent('X4AuthorityCombatObserver.Event', function(_, value)
    local ok, err = pcall(observer.ingest, tostring(value))
    if not ok then log('ERROR observer_ingest=' .. tostring(err)) end
end)

local function next_req() state.req_id = state.req_id + 1; return state.req_id end

local function probe(control, args)
    state.probe_reply = nil
    args.request_id = next_req()
    AddUITriggeredEvent('X4AuthorityCombatProbe', control, args)
end

local function trace(control, args)
    state.trace_reply = nil
    args = args or {}
    args.request_id = next_req()
    AddUITriggeredEvent('X4AuthorityCombatOverlapTrace', control, args)
    return args.request_id
end

local function observer_ctrl(control, args)
    state.obs_status = nil
    args.request_id = next_req()
    AddUITriggeredEvent('X4AuthorityCombatObserver', control, args)
    return args.request_id
end

local function log_weapon_profile(label, actor_str)
    local prof = combat_obs.inspect_weapon_profile(actor_str)
    if not prof.ok then
        log('WEAPON_PROFILE label=' .. label .. ' ok=false reason=' .. tostring(prof.reason))
        return
    end
    local w1 = prof.weapons[1] or {}
    local root_only = #prof.weapons == 6 and w1.bullet_macro ~= nil and (w1.hullshielddpshot or 0) > 0
    for _, gun in ipairs(prof.weapons) do
        if gun.isbeamweapon or #gun.other_channels ~= 0 or gun.bullet_macro ~= w1.bullet_macro
                or gun.hullshielddpshot ~= w1.hullshielddpshot then
            root_only = false
        end
    end
    log(string.format('WEAPON_PROFILE label=%s ok=true slots=%d weapons=%d uniform=%s mixed=%s modified=%s bullet=%s hs_shot=%s speed=%s root_hullshield_only=%s',
        label, prof.slot_count or 0, prof.weapon_count or 0, tostring(prof.uniform_macro), tostring(prof.mixed),
        tostring(prof.any_modified), tostring(w1.bullet_macro), tostring(w1.hullshielddpshot), tostring(w1.bulletspeed),
        tostring(root_only)))
end

-- Each shoot_at script ends shortly after its phase, so the next phase starts a fresh script.
local function fire_second(ph)
    if state.second_mode == 'mindless_shoot' then
        probe('FireControl', { mode = 'mindless_shoot', shooter = 2 })
    else
        trace('Fire2', { duration = ph.max + 0.3 })
    end
end

local function start_phase_fire(ph)
    state.phase_start = now()
    if ph.pilot == 'shoot_at' then
        probe('FireControl', { mode = 'shoot_at', shooter = 1, target = 4, duration = ph.max + 0.3 })
    elseif ph.pilot == 'mindless_shoot' then
        probe('FireControl', { mode = 'mindless_shoot', shooter = 1 })
    end
    if ph.second then fire_second(ph) end
end

local function stop_phase_fire(ph)
    if ph.pilot then probe('FireControl', { mode = 'stop', shooter = 1 }) end
    if ph.second then probe('FireControl', { mode = 'stop', shooter = 2 }) end
end

local function phase_line(tag, ph, elapsed)
    local c = state.counts
    log(string.format('%s phase=%s elapsed=%.3f shots1=%d shots2=%d shield=%d hull=%d attacked=%d other_victims=%d second_mode=%s second_yaw=%d',
        tag, state.phase, elapsed, c.shots1, c.shots2, c.shield, c.hull, c.attacked, c.ff, state.second_mode, state.second_yaw))
end

local function begin_marker(name)
    state.phase = name
    reset_counts()
    state.stop_sent = false
    probe('Phase', { phase = name })
end

local function tick()
    if state.done then return end
    local s = state.step

    if s == 'boot' then
        check_profile()
        if C.IsStartmenu() then
            log('STARTMENU launching gamestart=' .. tostring(cfg.gamestart))
            state.step = 'starting'
            Helper.addDelayedOneTimeCallbackOnUpdate(function() NewGame(cfg.gamestart) end, true, getElapsedTime() + 2)
            return
        end
        if not C.IsPlayerValid() or C.IsGamePaused() or now() < 2.0 then return end
        state.step = 'init_registry'
        return
    end
    if s == 'starting' then return end

    if s == 'init_registry' then
        state.reg_reply = nil
        AddUITriggeredEvent('X4AuthorityShipRegistry', 'Initialize', { request_id = next_req(), epoch = cfg.epoch })
        state.step = 'wait_registry'
        return
    end
    if s == 'wait_registry' then
        if not state.reg_reply then return end
        log('REGISTRY ' .. state.reg_reply)
        probe('Setup', { epoch = cfg.epoch, second_loadout = cfg.second_loadout })
        state.step = 'wait_setup'
        return
    end
    if s == 'wait_setup' then
        if not state.probe_reply then return end
        local p = split(state.probe_reply, '|')
        if p[2] ~= 'Ready' then
            log('ERROR setup_failed=' .. state.probe_reply)
            state.done = true
            return
        end
        state.ships = { p[4], p[6], p[8], p[10], p[12] }
        state.codes = { p[5], p[7], p[9], p[11], p[13] }
        log('SETUP_READY sector=' .. p[3] .. ' p1=' .. p[4] .. '(' .. p[5] .. ') p2=' .. p[6] .. '(' .. p[7] .. ') npc4=' .. p[10] .. '(' .. p[11] .. ')')
        state.settle_until = now() + 0.5
        state.step = 'wait_seat_settle'
        return
    end
    if s == 'wait_seat_settle' then
        if now() < state.settle_until then return end
        local p1 = ConvertIDTo64Bit(state.ships[1])
        assert(C.GetPlayerControlledShipID() == p1 and C.GetPlayerOccupiedShipID() == p1, 'pilot helm was not confirmed')
        log('SEAT_CHECK p1_match_ctrl=true p1_match_occ=true')
        log_weapon_profile('company_player', state.ships[1])
        log_weapon_profile('second_shooter', state.ships[2])
        state.obs_request = observer_ctrl('Arm', { epoch = cfg.epoch, watch_player_ship = true, watch_player_owned = true })
        state.step = 'wait_arm'
        return
    end
    if s == 'wait_arm' then
        if not state.obs_status then return end
        assert(state.obs_status == string.format('%s|Armed|%s|%s', state.obs_request, cfg.epoch, cfg.epoch),
            'observer registry epoch was not confirmed')
        log('OBS_STATUS ' .. state.obs_status)
        state.watch_idx = 0
        state.step = 'watch_next'
        return
    end
    if s == 'watch_next' then
        state.watch_idx = state.watch_idx + 1
        local w = WATCH_PLAN[state.watch_idx]
        if not w then
            trace('Arm', { restore = cfg.restore_target == true, epoch = cfg.epoch })
            state.step = 'wait_trace_arm'
            return
        end
        state.obs_request = observer_ctrl('Watch', { registry_index = w.index, label = w.label, as_target = w.as_target,
            as_source = w.as_source, watch_weapons = w.watch_weapons })
        state.step = 'wait_watch'
        return
    end
    if s == 'wait_watch' then
        if not state.obs_status then return end
        local w, reply = WATCH_PLAN[state.watch_idx], split(state.obs_status, '|')
        assert(tonumber(reply[1]) == state.obs_request and reply[2] == 'Watched' and reply[3] == w.label
            and reply[4] == state.ships[w.index] and reply[5] == state.codes[w.index] and tonumber(reply[7]) == w.index,
            'observer did not watch the registered fixture ship')
        log('OBS_STATUS ' .. state.obs_status)
        state.step = 'watch_next'
        return
    end
    if s == 'wait_trace_arm' then
        if not state.trace_reply then return end
        log('TRACE_STATUS ' .. state.trace_reply)
        local armed = split(state.trace_reply, '|')[3]
        if armed ~= '1' and armed ~= 'true' then
            log('ERROR trace_not_armed=' .. state.trace_reply)
            state.done = true
            return
        end
        state.phase_idx = 0
        state.step = 'phase_next'
        return
    end

    local ph = PHASES[state.phase_idx]

    if s == 'phase_next' then
        state.phase_idx = state.phase_idx + 1
        if not PHASES[state.phase_idx] then
            trace('Disarm')
            state.step = 'wait_trace_disarm'
            return
        end
        state.pre_idx = 0
        state.marker_name = nil
        state.step = 'pre_next'
        return
    end
    if s == 'pre_next' then
        state.pre_idx = state.pre_idx + 1
        local r = ph.pre and ph.pre[state.pre_idx]
        if not r then
            state.settle_until = now() + 0.6
            state.step = 'pre_settle'
            return
        end
        probe('Reposition', { index = r[1], x = r[2], y = r[3], z = r[4], yaw = r[5] })
        state.step = 'wait_pre'
        return
    end
    if s == 'wait_pre' then
        if not state.probe_reply then return end
        if split(state.probe_reply, '|')[2] ~= 'Repositioned' then
            log('ERROR reposition=' .. state.probe_reply)
            state.done = true
            return
        end
        state.step = 'pre_next'
        return
    end
    if s == 'pre_settle' then
        if now() < state.settle_until then return end
        if cfg.restore_target == true and not ph.keep_damage then
            state.restore_request = trace('Restore')
            state.step = 'wait_restore'
            return
        end
        begin_marker(state.marker_name or ph.name)
        state.step = 'wait_marker'
        return
    end
    if s == 'wait_restore' then
        if not state.trace_reply then return end
        local reply = split(state.trace_reply, '|')
        assert(tonumber(reply[1]) == state.restore_request and reply[2] == 'Restored',
            'fresh fixture target health was not confirmed')
        begin_marker(state.marker_name or ph.name)
        state.step = 'wait_marker'
        return
    end
    if s == 'wait_marker' then
        if not state.probe_reply then return end
        if ph.dodge then
            check_dodge_position(state.ships[4], -6000)
            state.obs_request = observer_ctrl('Snapshot', { tag = 'before_miss_pilot' })
            state.step = 'wait_miss_snapshot'
            return
        end
        start_phase_fire(ph)
        state.step = 'run'
        return
    end
    if s == 'wait_miss_snapshot' then
        if not state.obs_status then return end
        local reply = split(state.obs_status, '|')
        assert(tonumber(reply[1]) == state.obs_request and reply[2] == 'SnapshotDone' and reply[3] == 'before_miss_pilot',
            'miss baseline snapshot was not confirmed')
        start_phase_fire(ph)
        state.step = 'run'
        return
    end
    if s == 'run' then
        local elapsed = now() - state.phase_start
        local c = state.counts
        local impacts = c.shield + c.hull
        if state.dodge_request then
            if not state.dodge_reply then
                assert(elapsed < ph.max, 'miss target movement was not acknowledged')
                return
            end
            assert(state.dodge_reply == state.dodge_request .. '|Repositioned|4', 'miss target movement was refused')
            -- The warp is acknowledged in the frame it is issued; the sector position follows a frame later.
            local ok, msg = dodge_position(state.ships[4], ph.dodge[2])
            if not ok then
                assert(elapsed < ph.max, msg)
                return
            end
            state.dodge_request = nil
            log(string.format('DODGE_POSITION phase=%s elapsed=%.3f', state.phase, elapsed))
            trace('DodgeVerified')
        end
        if not state.stop_sent then
            if ph.dodge and not state.dodged and c.shots1 > 0 then
                state.dodged = true
                probe('FireControl', { mode = 'stop', shooter = 1 })
                local d = ph.dodge
                state.dodge_request = state.req_id + 1
                probe('Reposition', { index = d[1], x = d[2], y = d[3], z = d[4], yaw = d[5] })
                log(string.format('DODGE phase=%s elapsed=%.3f shots1=%d', state.phase, elapsed, c.shots1))
                return
            end
            if ph.second and not state.fallback2 and state.second_mode ~= 'mindless_shoot' and elapsed >= 0.7 and c.shots2 == 0 then
                state.fallback2 = true
                state.second_mode = 'mindless_shoot'
                log('FALLBACK phase=' .. state.phase .. ' reason=second_shoot_at_no_shots switching=mindless_shoot')
                fire_second(ph)
                return
            end
            if ph.aim_check and not state.aim_retried and elapsed >= 0.8 and c.shots2 > 0 and impacts == 0 then
                -- #2 fired but hit nothing: keep this segment as a miss and retry with the other yaw sign.
                state.aim_retried = true
                stop_phase_fire(ph)
                phase_line('PHASE_STOP', ph, elapsed)
                log('AIM_RETRY phase=' .. state.phase .. ' second_yaw=-90')
                state.marker_name = 'control_second_yawfix'
                state.second_yaw = -90
                state.step = 'aim_retry'
                state.settle_until = now() + 1.5
                return
            end
            if (ph.impacts and impacts >= ph.impacts) or (ph.hull and c.hull >= ph.hull) or elapsed >= ph.max then
                stop_phase_fire(ph)
                state.stop_sent = true
                state.settle_until = now() + 1.5
                phase_line('PHASE_STOP', ph, elapsed)
                -- Bullets vanish one frame after their impact: sweep now so the last frame resolves
                -- before expiring misses join it.
                trace('Sweep', { tag = state.phase .. '_stop' })
            end
            return
        end
        if now() < state.settle_until then return end
        trace('Sweep', { tag = state.phase .. '_end' })
        state.step = 'wait_sweep'
        return
    end
    if s == 'aim_retry' then
        if now() < state.settle_until then return end
        trace('Sweep', { tag = state.phase .. '_end' })
        state.step = 'aim_retry_sweep'
        return
    end
    if s == 'aim_retry_sweep' then
        if not state.trace_reply then return end
        log('TRACE_STATUS ' .. state.trace_reply)
        probe('Reposition', { index = 2, x = SECOND_POS[1], y = SECOND_POS[2], z = SECOND_POS[3], yaw = -90 })
        state.step = 'aim_retry_wait'
        return
    end
    if s == 'aim_retry_wait' then
        if not state.probe_reply then return end
        log('AIM_RETRY repositioned reply=' .. state.probe_reply)
        state.settle_until = now() + 0.6
        state.step = 'pre_settle'
        return
    end
    if s == 'wait_sweep' then
        if not state.trace_reply then return end
        log('TRACE_STATUS ' .. state.trace_reply)
        observer_ctrl('Snapshot', { tag = 'after_' .. state.phase })
        state.step = 'wait_snap'
        return
    end
    if s == 'wait_snap' then
        if not state.obs_status then return end
        state.step = 'phase_next'
        return
    end
    if s == 'wait_trace_disarm' then
        if not state.trace_reply then return end
        observer_ctrl('Disarm', {})
        state.step = 'wait_disarm'
        return
    end
    if s == 'wait_disarm' then
        if not state.obs_status then return end
        local sm = observer.summary()
        log(string.format('SUMMARY total=%d dropped=%d lag=%d lua_ok=%d lua_fail=%d max_hp_diff=%.6f max_sh_diff=%.6f',
            sm.total_events, sm.dropped_events, sm.lastattacktime_lag_events,
            sm.lua_readback_ok, sm.lua_readback_fail, sm.max_hullpct_diff, sm.max_shieldpct_diff))
        log('DONE')
        state.done = true
    end
end

SetScript('onUpdate', function()
    local ok, err = pcall(tick)
    if not ok and not state.done then
        log('ERROR tick=' .. tostring(err):gsub('%s+', ' '))
        state.done = true
    end
end)
