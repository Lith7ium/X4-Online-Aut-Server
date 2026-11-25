-- Disposable native Windows X4 9.00 driver exercising the reusable READ-ONLY
-- X4AuthorityCombatObserver + X4LAN_COMBAT_OBSERVER across 4 real-weapon-hit cases:
--   Case 1a: Same-Company friendly fire (player.controlled Company Pulsar #1 -> unoccupied Company Pulsar #2) — shield-absorbed only
--   Case 1b: Same-Company friendly fire (#1 -> #2) — shield depletion + hull overflow
--   Case 2:  Hostile NPC (Xenon fighter #5) -> unoccupied registered Company Pulsar #3 (stock AI Attack order)
--   Case 3a: Player Company Pulsar #1 -> Xenon NPC fighter #4 — shield-absorbed only
--   Case 3b: Player Company Pulsar #1 -> Xenon NPC fighter #4 — shield depletion + hull overflow
--   Case 4:  Hostile NPC (Xenon fighter #5) -> player-occupied Company Pulsar #1 (verifying event_player_ship_hit)
local cfg = X4NEON_COMBAT_OBS
if type(cfg) ~= 'table' or cfg.enabled ~= true then return end

local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
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
    'size_t GetNumUpgradeSlots(UniverseID componentid, const char* macroname, const char* upgradetypename);',
    'UniverseID GetUpgradeSlotCurrentComponent(UniverseID componentid, const char* upgradetypename, size_t slot);',
    'bool GetInstalledWeaponMod(UniverseID weaponid, UIWeaponMod* weaponmod);',
    'const char* GetWeaponLaunchedMacro(UniverseID weaponid);',
}) do declare(text) end

local combat_obs = assert(X4LAN_COMBAT_OBSERVER, 'X4LAN_COMBAT_OBSERVER required')

local function log(msg)
    DebugError('[X4LAN] AUTH_COMBAT_DRIVER ' .. msg)
end

local function log_lua_rb(msg)
    DebugError('[X4LAN] AUTH_COMBAT_LUA ' .. msg)
end

local function split(text, sep)
    sep = sep or '|'
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

local state = {
    step = 'boot',
    req_id = 0,
    reg_reply = nil,
    probe_reply = nil,
    obs_status = nil,
    ships = {},
    codes = {},
    sector = nil,
    phase = 'init',
    phase_start = 0,
    phase_counts = { shield = 0, hull = 0, attacked = 0, player_hit = 0, shots = 0 },
    phase_min_hullpct = 100.0,
    phase_fallback_used = false,
    stop_sent = false,
    settle_until = 0,
    watch_idx = 0,
    done = false,
}

local function reset_phase_counts(name)
    state.phase = name
    state.phase_start = C.GetCurrentGameTime()
    state.phase_counts = { shield = 0, hull = 0, attacked = 0, player_hit = 0, shots = 0 }
    state.phase_min_hullpct = 100.0
    state.phase_fallback_used = false
    state.stop_sent = false
    state.settle_until = 0
end

local observer = combat_obs.new({
    log = log_lua_rb,
    on_event = function(ev)
        if ev.kind == 'weapon_fired' then
            state.phase_counts.shots = state.phase_counts.shots + 1
            return
        end
        local target_label = nil
        if state.phase == 'ff_shield_only' or state.phase == 'ff_hull_overflow' then
            target_label = 'company_ff_target'
        elseif state.phase == 'npc_to_company' then
            target_label = 'company_npc_target'
        elseif state.phase == 'npc_shield_only' or state.phase == 'npc_hull_overflow' then
            target_label = 'npc_target'
        elseif state.phase == 'npc_to_player_occupied' then
            target_label = 'company_player'
        end
        if target_label and ev.victim_label == target_label then
            if ev.kind == 'shield_damaged' then
                state.phase_counts.shield = state.phase_counts.shield + 1
            elseif ev.kind == 'hull_damaged' then
                state.phase_counts.hull = state.phase_counts.hull + 1
                if ev.hullpct >= 0 and ev.hullpct < state.phase_min_hullpct then
                    state.phase_min_hullpct = ev.hullpct
                end
            elseif ev.kind == 'object_attacked' then
                state.phase_counts.attacked = state.phase_counts.attacked + 1
            elseif ev.kind == 'player_ship_hit' then
                state.phase_counts.player_hit = state.phase_counts.player_hit + 1
            end
        end
    end,
})

RegisterEvent('X4AuthorityShipRegistry.Correlated', function(_, value)
    state.reg_reply = tostring(value)
end)

RegisterEvent('X4AuthorityCombatProbe.Result', function(_, value)
    state.probe_reply = tostring(value)
end)

RegisterEvent('X4AuthorityCombatObserver.Status', function(_, value)
    state.obs_status = tostring(value)
end)

RegisterEvent('X4AuthorityCombatObserver.Event', function(_, value)
    local ok, err = pcall(observer.ingest, tostring(value))
    if not ok then
        log('ERROR observer_ingest=' .. tostring(err))
    end
end)

local function next_req()
    state.req_id = state.req_id + 1
    return state.req_id
end

local function log_weapon_profile(label, actor_str)
    local prof = combat_obs.inspect_weapon_profile(actor_str)
    if not prof.ok then
        log('WEAPON_PROFILE label=' .. label .. ' ok=false reason=' .. tostring(prof.reason))
        return
    end
    local w1 = prof.weapons[1] or {}
    log(string.format(
        'WEAPON_PROFILE label=%s ok=true slots=%d weapons=%d uniform=%s mixed=%s modified=%s beam=%s bullet=%s hs_shot=%s speed=%s',
        label, prof.slot_count or 0, prof.weapon_count or 0,
        tostring(prof.uniform_macro), tostring(prof.mixed), tostring(prof.any_modified),
        tostring(prof.any_beam), tostring(w1.bullet_macro),
        tostring(w1.hullshielddpshot), tostring(w1.bulletspeed)
    ))
end

local WATCH_PLAN = {
    { index = 1, label = 'company_player', as_target = true, as_source = true, watch_weapons = true },
    { index = 2, label = 'company_ff_target', as_target = true, as_source = false, watch_weapons = false },
    { index = 3, label = 'company_npc_target', as_target = true, as_source = false, watch_weapons = false },
    { index = 4, label = 'npc_target', as_target = true, as_source = false, watch_weapons = false },
    { index = 5, label = 'npc_attacker', as_target = false, as_source = true, watch_weapons = true },
}

local function send_phase(name)
    reset_phase_counts(name)
    state.probe_reply = nil
    AddUITriggeredEvent('X4AuthorityCombatProbe', 'Phase', {
        request_id = next_req(),
        phase = name,
    })
end

local function send_snapshot(tag)
    state.obs_status = nil
    AddUITriggeredEvent('X4AuthorityCombatObserver', 'Snapshot', {
        request_id = next_req(),
        tag = tag,
    })
end

local function send_fire(mode, shooter, target, duration)
    state.probe_reply = nil
    AddUITriggeredEvent('X4AuthorityCombatProbe', 'FireControl', {
        request_id = next_req(),
        mode = mode,
        shooter = shooter,
        target = target,
        duration = duration,
    })
end

local function send_reposition(index, x, y, z, yaw)
    state.probe_reply = nil
    AddUITriggeredEvent('X4AuthorityCombatProbe', 'Reposition', {
        request_id = next_req(),
        index = index,
        x = x, y = y, z = z, yaw = yaw,
    })
end

local function tick()
    if state.done then return end
    if state.step == 'boot' then
        check_profile()
        if C.IsStartmenu() then
            log('STARTMENU launching gamestart=' .. tostring(cfg.gamestart))
            state.step = 'starting'
            Helper.addDelayedOneTimeCallbackOnUpdate(function()
                NewGame(cfg.gamestart)
            end, true, getElapsedTime() + 2)
            return
        end
        if not C.IsPlayerValid() or C.IsGamePaused() then return end
        local gt = tonumber(C.GetCurrentGameTime()) or 0
        if gt < 2.0 then return end
        state.step = 'init_registry'
        return
    end

    if state.step == 'starting' then
        return
    end

    if state.step == 'init_registry' then
        state.reg_reply = nil
        AddUITriggeredEvent('X4AuthorityShipRegistry', 'Initialize', {
            request_id = next_req(),
            epoch = cfg.epoch,
        })
        state.step = 'wait_registry'
        return
    end

    if state.step == 'wait_registry' then
        if not state.reg_reply then return end
        log('REGISTRY ' .. state.reg_reply)
        state.probe_reply = nil
        AddUITriggeredEvent('X4AuthorityCombatProbe', 'Setup', {
            request_id = next_req(),
            epoch = cfg.epoch,
        })
        state.step = 'wait_setup'
        return
    end

    if state.step == 'wait_setup' then
        if not state.probe_reply then return end
        local parts = split(state.probe_reply, '|')
        if parts[2] ~= 'Ready' then
            log('ERROR setup_failed=' .. state.probe_reply)
            state.done = true
            return
        end
        state.sector = parts[3]
        state.ships = { parts[4], parts[6], parts[8], parts[10], parts[12] }
        state.codes = { parts[5], parts[7], parts[9], parts[11], parts[13] }
        log('SETUP_READY sector=' .. state.sector .. ' p1=' .. state.ships[1] .. '(' .. state.codes[1] .. ') p2=' .. state.ships[2] .. '(' .. state.codes[2] .. ') p3=' .. state.ships[3] .. '(' .. state.codes[3] .. ') npc4=' .. state.ships[4] .. '(' .. state.codes[4] .. ') npc5=' .. state.ships[5] .. '(' .. state.codes[5] .. ')')
        state.settle_until = C.GetCurrentGameTime() + 0.5
        state.step = 'wait_seat_settle'
        return
    end

    if state.step == 'wait_seat_settle' then
        if C.GetCurrentGameTime() < state.settle_until then return end
        local p1_u64 = ConvertIDTo64Bit(state.ships[1])
        local ctrl = C.GetPlayerControlledShipID()
        local occ = C.GetPlayerOccupiedShipID()
        log('SEAT_CHECK p1_match_ctrl=' .. tostring(ctrl == p1_u64) .. ' p1_match_occ=' .. tostring(occ == p1_u64))
        log_weapon_profile('company_player', state.ships[1])
        log_weapon_profile('npc_attacker', state.ships[5])
        state.obs_status = nil
        AddUITriggeredEvent('X4AuthorityCombatObserver', 'Arm', {
            request_id = next_req(),
            epoch = cfg.epoch,
            watch_player_ship = true,
            watch_player_owned = true,
        })
        state.step = 'wait_arm'
        return
    end

    if state.step == 'wait_arm' then
        if not state.obs_status then return end
        log('OBS_STATUS ' .. state.obs_status)
        state.watch_idx = 0
        state.step = 'watch_next'
        return
    end

    if state.step == 'watch_next' then
        state.watch_idx = state.watch_idx + 1
        local w = WATCH_PLAN[state.watch_idx]
        if not w then
            send_phase('baseline')
            state.step = 'wait_baseline_phase'
            return
        end
        state.obs_status = nil
        AddUITriggeredEvent('X4AuthorityCombatObserver', 'Watch', {
            request_id = next_req(),
            registry_index = w.index,
            label = w.label,
            as_target = w.as_target,
            as_source = w.as_source,
            watch_weapons = w.watch_weapons,
        })
        state.step = 'wait_watch'
        return
    end

    if state.step == 'wait_watch' then
        if not state.obs_status then return end
        log('OBS_STATUS ' .. state.obs_status)
        state.step = 'watch_next'
        return
    end

    if state.step == 'wait_baseline_phase' then
        if not state.probe_reply then return end
        send_snapshot('baseline')
        state.step = 'wait_baseline_snap'
        return
    end

    if state.step == 'wait_baseline_snap' then
        if not state.obs_status then return end
        -- CASE 1A: Same-Company friendly fire (#1 -> #2), shield-absorbed only.
        send_phase('ff_shield_only')
        state.step = 'start_1a'
        return
    end

    if state.step == 'start_1a' then
        if not state.probe_reply then return end
        send_fire('shoot_at', 1, 2, 1.5)
        state.step = 'run_1a'
        return
    end

    if state.step == 'run_1a' then
        local elapsed = C.GetCurrentGameTime() - state.phase_start
        if not state.stop_sent then
            if state.phase_counts.shield >= 4 or elapsed >= 2.2 then
                send_fire('stop', 1)
                state.stop_sent = true
                state.settle_until = C.GetCurrentGameTime() + 1.5
                log(string.format('PHASE_STOP phase=ff_shield_only elapsed=%.3f shots=%d shield=%d hull=%d fallback=%s',
                    elapsed, state.phase_counts.shots, state.phase_counts.shield, state.phase_counts.hull, tostring(state.phase_fallback_used)))
            elseif not state.phase_fallback_used and elapsed >= 0.7 and state.phase_counts.shots == 0 then
                state.phase_fallback_used = true
                log('FALLBACK phase=ff_shield_only reason=shoot_at_no_shots_same_faction switching=mindless_shoot')
                send_fire('mindless_shoot', 1)
            end
            return
        end
        if C.GetCurrentGameTime() < state.settle_until then return end
        send_snapshot('after_ff_shield_only')
        state.step = 'snap_1a'
        return
    end

    if state.step == 'snap_1a' then
        if not state.obs_status then return end
        -- CASE 1B: Same-Company friendly fire (#1 -> #2), shield depletion + hull overflow.
        send_phase('ff_hull_overflow')
        state.step = 'start_1b'
        return
    end

    if state.step == 'start_1b' then
        if not state.probe_reply then return end
        send_fire('shoot_at', 1, 2, 7.0)
        state.step = 'run_1b'
        return
    end

    if state.step == 'run_1b' then
        local elapsed = C.GetCurrentGameTime() - state.phase_start
        if not state.stop_sent then
            if state.phase_counts.hull >= 5 or state.phase_min_hullpct <= 60.0 or elapsed >= 7.0 then
                send_fire('stop', 1)
                state.stop_sent = true
                state.settle_until = C.GetCurrentGameTime() + 1.5
                log(string.format('PHASE_STOP phase=ff_hull_overflow elapsed=%.3f shots=%d shield=%d hull=%d min_hp=%.2f fallback=%s',
                    elapsed, state.phase_counts.shots, state.phase_counts.shield, state.phase_counts.hull,
                    state.phase_min_hullpct, tostring(state.phase_fallback_used)))
            elseif not state.phase_fallback_used and elapsed >= 0.7 and state.phase_counts.shots == 0 then
                state.phase_fallback_used = true
                log('FALLBACK phase=ff_hull_overflow reason=shoot_at_no_shots_same_faction switching=mindless_shoot')
                send_fire('mindless_shoot', 1)
            end
            return
        end
        if C.GetCurrentGameTime() < state.settle_until then return end
        send_snapshot('after_ff_hull_overflow')
        state.step = 'snap_1b'
        return
    end

    if state.step == 'snap_1b' then
        if not state.obs_status then return end
        -- CASE 2: Hostile NPC (#5) -> unoccupied registered Company target (#3) via stock AI Attack.
        send_phase('npc_to_company')
        state.step = 'start_2'
        return
    end

    if state.step == 'start_2' then
        if not state.probe_reply then return end
        send_fire('ai_attack', 5, 3)
        state.step = 'run_2'
        return
    end

    if state.step == 'run_2' then
        local elapsed = C.GetCurrentGameTime() - state.phase_start
        if not state.stop_sent then
            if (state.phase_counts.shield >= 6 and elapsed >= 2.0) or state.phase_counts.hull >= 3 or elapsed >= 9.0 then
                send_fire('stop', 5)
                state.stop_sent = true
                state.settle_until = C.GetCurrentGameTime() + 1.5
                log(string.format('PHASE_STOP phase=npc_to_company elapsed=%.3f shots=%d shield=%d hull=%d attacked=%d',
                    elapsed, state.phase_counts.shots, state.phase_counts.shield, state.phase_counts.hull, state.phase_counts.attacked))
            end
            return
        end
        if C.GetCurrentGameTime() < state.settle_until then return end
        send_snapshot('after_npc_to_company')
        state.step = 'snap_2'
        return
    end

    if state.step == 'snap_2' then
        if not state.obs_status then return end
        -- Reposition Company player #1 to (-6000, 100000, -200) yaw=0 facing NPC target #4 at (-6000, 100000, 0).
        send_reposition(1, -6000, 100000, -200, 0)
        state.step = 'wait_repo_3'
        return
    end

    if state.step == 'wait_repo_3' then
        if not state.probe_reply then return end
        state.settle_until = C.GetCurrentGameTime() + 0.5
        state.step = 'settle_repo_3'
        return
    end

    if state.step == 'settle_repo_3' then
        if C.GetCurrentGameTime() < state.settle_until then return end
        -- CASE 3A: Player Company Pulsar #1 -> Xenon NPC fighter #4, shield-absorbed only.
        send_phase('npc_shield_only')
        state.step = 'start_3a'
        return
    end

    if state.step == 'start_3a' then
        if not state.probe_reply then return end
        send_fire('shoot_at', 1, 4, 1.5)
        state.step = 'run_3a'
        return
    end

    if state.step == 'run_3a' then
        local elapsed = C.GetCurrentGameTime() - state.phase_start
        if not state.stop_sent then
            if state.phase_counts.shield >= 3 or elapsed >= 2.0 then
                send_fire('stop', 1)
                state.stop_sent = true
                state.settle_until = C.GetCurrentGameTime() + 1.5
                log(string.format('PHASE_STOP phase=npc_shield_only elapsed=%.3f shots=%d shield=%d hull=%d fallback=%s',
                    elapsed, state.phase_counts.shots, state.phase_counts.shield, state.phase_counts.hull, tostring(state.phase_fallback_used)))
            elseif not state.phase_fallback_used and elapsed >= 0.7 and state.phase_counts.shots == 0 then
                state.phase_fallback_used = true
                log('FALLBACK phase=npc_shield_only reason=shoot_at_no_shots switching=mindless_shoot')
                send_fire('mindless_shoot', 1)
            end
            return
        end
        if C.GetCurrentGameTime() < state.settle_until then return end
        send_snapshot('after_npc_shield_only')
        state.step = 'snap_3a'
        return
    end

    if state.step == 'snap_3a' then
        if not state.obs_status then return end
        -- CASE 3B: Player Company Pulsar #1 -> Xenon NPC fighter #4, shield depletion + hull overflow.
        send_phase('npc_hull_overflow')
        state.step = 'start_3b'
        return
    end

    if state.step == 'start_3b' then
        if not state.probe_reply then return end
        send_fire('shoot_at', 1, 4, 6.0)
        state.step = 'run_3b'
        return
    end

    if state.step == 'run_3b' then
        local elapsed = C.GetCurrentGameTime() - state.phase_start
        if not state.stop_sent then
            if state.phase_counts.hull >= 4 or state.phase_min_hullpct <= 55.0 or elapsed >= 6.0 then
                send_fire('stop', 1)
                state.stop_sent = true
                state.settle_until = C.GetCurrentGameTime() + 1.5
                log(string.format('PHASE_STOP phase=npc_hull_overflow elapsed=%.3f shots=%d shield=%d hull=%d min_hp=%.2f fallback=%s',
                    elapsed, state.phase_counts.shots, state.phase_counts.shield, state.phase_counts.hull,
                    state.phase_min_hullpct, tostring(state.phase_fallback_used)))
            elseif not state.phase_fallback_used and elapsed >= 0.7 and state.phase_counts.shots == 0 then
                state.phase_fallback_used = true
                log('FALLBACK phase=npc_hull_overflow reason=shoot_at_no_shots switching=mindless_shoot')
                send_fire('mindless_shoot', 1)
            end
            return
        end
        if C.GetCurrentGameTime() < state.settle_until then return end
        send_snapshot('after_npc_hull_overflow')
        state.step = 'snap_3b'
        return
    end

    if state.step == 'snap_3b' then
        if not state.obs_status then return end
        -- CASE 4: Reposition Xenon attacker #5 to (-6000, 100000, -480) yaw=0 facing player-occupied Company #1 at (-6000, 100000, -200).
        send_reposition(5, -6000, 100000, -480, 0)
        state.step = 'wait_repo_4'
        return
    end

    if state.step == 'wait_repo_4' then
        if not state.probe_reply then return end
        state.settle_until = C.GetCurrentGameTime() + 0.5
        state.step = 'settle_repo_4'
        return
    end

    if state.step == 'settle_repo_4' then
        if C.GetCurrentGameTime() < state.settle_until then return end
        send_phase('npc_to_player_occupied')
        state.step = 'start_4'
        return
    end

    if state.step == 'start_4' then
        if not state.probe_reply then return end
        send_fire('ai_attack', 5, 1)
        state.step = 'run_4'
        return
    end

    if state.step == 'run_4' then
        local elapsed = C.GetCurrentGameTime() - state.phase_start
        if not state.stop_sent then
            if (state.phase_counts.shield >= 5 and elapsed >= 1.5) or state.phase_counts.player_hit >= 5 or elapsed >= 8.0 then
                send_fire('stop', 5)
                state.stop_sent = true
                state.settle_until = C.GetCurrentGameTime() + 1.5
                log(string.format('PHASE_STOP phase=npc_to_player_occupied elapsed=%.3f shots=%d shield=%d player_hit=%d attacked=%d',
                    elapsed, state.phase_counts.shots, state.phase_counts.shield, state.phase_counts.player_hit, state.phase_counts.attacked))
            end
            return
        end
        if C.GetCurrentGameTime() < state.settle_until then return end
        send_snapshot('after_npc_to_player_occupied')
        state.step = 'snap_4'
        return
    end

    if state.step == 'snap_4' then
        if not state.obs_status then return end
        state.obs_status = nil
        AddUITriggeredEvent('X4AuthorityCombatObserver', 'Disarm', { request_id = next_req() })
        state.step = 'wait_disarm'
        return
    end

    if state.step == 'wait_disarm' then
        if not state.obs_status then return end
        local s = observer.summary()
        log(string.format(
            'SUMMARY total=%d dropped=%d lag=%d lua_ok=%d lua_fail=%d max_hp_diff=%.6f max_sh_diff=%.6f',
            s.total_events, s.dropped_events, s.lastattacktime_lag_events,
            s.lua_readback_ok, s.lua_readback_fail, s.max_hullpct_diff, s.max_shieldpct_diff
        ))
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
