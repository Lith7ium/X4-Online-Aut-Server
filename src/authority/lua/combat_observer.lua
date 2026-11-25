-- Reusable READ-ONLY Lua combat observation module pairing MD combat events
-- (X4AuthorityCombatObserver) with independent Lua GetComponentData health readbacks
-- and stock weapon-library profile inspection (reusing LAN shot_game.lua read-only
-- GetLibraryEntry / GetInstalledWeaponMod patterns).
--
-- Strictly read-only: NEVER writes hull, shield, min-hull, weapon macro, or damage state.
local ok_ffi, ffi_lib = pcall(require, 'ffi')
if not ok_ffi then ffi_lib = nil end
if ffi_lib then
    pcall(ffi_lib.cdef, 'typedef uint64_t UniverseID;')
    pcall(ffi_lib.cdef, [[typedef struct {
        const char* Name; const char* RawName; const char* Ware; uint32_t Quality; const char* PropertyType;
        float DamageFactor; float CoolingFactor; float ReloadFactor; float SpeedFactor; float LifeTimeFactor;
        float MiningFactor; float StickTimeFactor; float ChargeTimeFactor; float BeamLengthFactor;
        uint32_t AddedAmount; float RotationSpeedFactor; float SurfaceElementFactor;
    } UIWeaponMod;]])
    for _, decl in ipairs({
        'size_t GetNumUpgradeSlots(UniverseID componentid, const char* macroname, const char* upgradetypename);',
        'UniverseID GetUpgradeSlotCurrentComponent(UniverseID componentid, const char* upgradetypename, size_t slot);',
        'bool GetInstalledWeaponMod(UniverseID weaponid, UIWeaponMod* weaponmod);',
        'const char* GetWeaponLaunchedMacro(UniverseID weaponid);',
    }) do
        pcall(ffi_lib.cdef, decl)
    end
end

local M = {}

local OTHER_DAMAGE_CHANNELS = {
    'shieldonlydpshot', 'hullonlydpshot', 'hullnoshielddpshot',
    'hullshieldareadpshot', 'shieldonlyareadpshot', 'hullonlyareadpshot',
    'hullnoshieldareadpshot',
}

local function split(text, sep)
    sep = sep or '|'
    local parts = {}
    for item in (tostring(text) .. sep):gmatch('(.-)' .. sep) do
        parts[#parts + 1] = item
    end
    return parts
end

local function num(v)
    local n = tonumber(v)
    if n == nil or n ~= n or n == math.huge or n == -math.huge then return nil end
    return n
end

local function flag(v)
    return tostring(v) == '1'
end

local function fmt_opt(v)
    if v == nil then return 'nil' end
    if type(v) == 'number' then return string.format('%.6g', v) end
    return tostring(v)
end

-- Parse one pipe-delimited X4AuthorityCombatObserver.Event payload (39 fields).
function M.parse_event(payload)
    if type(payload) ~= 'string' then return nil end
    local p = split(payload, '|')
    if #p < 39 then return nil end
    local seq = num(p[1])
    local age = num(p[3])
    if not seq or not age then return nil end
    return {
        seq = seq,
        kind = p[2],
        age = age,
        victim = p[4],
        victim_label = p[5],
        victim_code = p[6],
        victim_owner = p[7],
        victim_reg = num(p[8]) or 0,
        victim_ctrl = flag(p[9]),
        victim_occ = flag(p[10]),
        ev_att = p[11],
        ev_att_label = p[12],
        ev_att_code = p[13],
        ev_att_owner = p[14],
        ev_att_reg = num(p[15]) or 0,
        ev_att_ctrl = flag(p[16]),
        ev_att_isship = flag(p[17]),
        ev_att_pilot = p[18],
        last_att = p[19],
        last_att_label = p[20],
        last_att_code = p[21],
        last_att_owner = p[22],
        last_att_reg = num(p[23]) or 0,
        last_att_ctrl = flag(p[24]),
        last_att_isship = flag(p[25]),
        last_att_time = num(p[26]) or -1,
        comp = p[27],
        is_root = flag(p[28]),
        comp_macro = p[29],
        param2 = p[30],
        param3 = p[31],
        amount = num(p[32]) or 0,
        shield = num(p[33]) or -1,
        maxshield = num(p[34]) or -1,
        shieldpct = num(p[35]) or -1,
        hull = num(p[36]) or -1,
        maxhull = num(p[37]) or -1,
        hullpct = num(p[38]) or -1,
        collided = flag(p[39]),
    }
end

-- Classify a parsed shield_damaged / hull_damaged callback without mutating state.
function M.classify_damage(ev, prev)
    if type(ev) ~= 'table' then return 'invalid' end
    if ev.collided then return 'collision_flagged' end
    if ev.kind == 'shield_damaged' then
        if not ev.is_root then return 'subcomponent_shield' end
        if ev.shield <= 0 then return 'shield_depleted' end
        return 'shield_absorbed'
    elseif ev.kind == 'hull_damaged' then
        if not ev.is_root then return 'subcomponent_hull' end
        if prev and prev.kind == 'shield_damaged' and prev.victim == ev.victim
                and math.abs(prev.age - ev.age) <= 1e-6 then
            return 'hull_overflow_same_age'
        end
        if ev.shield <= 0 then return 'hull_zero_shield' end
        return 'hull_with_shield_remaining'
    end
    return ev.kind or 'other'
end

-- Read-only Lua health readback via GetComponentData for an MD actor string or UniverseID.
-- Records which keys return numeric/string values vs nil on the host X4 build.
function M.read_lua_health(actor_id, deps)
    deps = deps or {}
    local get_data = deps.GetComponentData or GetComponentData
    local to_u64 = deps.ConvertIDTo64Bit or ConvertIDTo64Bit
    local to_lua_id = deps.ConvertStringToLuaID or ConvertStringToLuaID
    if not get_data or not actor_id or actor_id == '0' or actor_id == 0 then
        return { ok = false, reason = 'unavailable' }
    end
    local ref = actor_id
    if to_u64 and to_lua_id then
        local ok_conv, conv = pcall(function()
            local u64 = type(actor_id) == 'string' and to_u64(actor_id) or actor_id
            if not u64 or u64 == 0 then return nil end
            local dec = tostring(u64):gsub('ULL$', '')
            return to_lua_id(dec)
        end)
        if not ok_conv or not conv then
            return { ok = false, reason = 'id_convert_failed' }
        end
        ref = conv
    end
    local ok, hp_pct, sh_pct, hull, hullmax, shield, shieldmax, owner, macro = pcall(
        get_data, ref,
        'hullpercent', 'shieldpercent', 'hull', 'hullmax', 'shield', 'shieldmax', 'owner', 'macro'
    )
    if not ok then
        return { ok = false, reason = 'getter_error' }
    end
    return {
        ok = true,
        hullpercent = num(hp_pct),
        shieldpercent = num(sh_pct),
        hull = num(hull),
        hullmax = num(hullmax),
        shield = num(shield),
        shieldmax = num(shieldmax),
        owner = type(owner) == 'string' and owner or nil,
        macro = type(macro) == 'string' and macro or nil,
    }
end

-- Read-only weapon loadout & per-shot damage library inspection (reusing LAN shot_game.lua).
function M.inspect_weapon_profile(actor_id, deps)
    deps = deps or {}
    local ffi_mod = deps.ffi or ffi_lib
    local C = deps.C or (ffi_mod and ffi_mod.C)
    local get_data = deps.GetComponentData or GetComponentData
    local get_macro_data = deps.GetMacroData or GetMacroData
    local get_lib_entry = deps.GetLibraryEntry or GetLibraryEntry
    local to_u64 = deps.ConvertIDTo64Bit or ConvertIDTo64Bit
    local to_lua_id = deps.ConvertStringToLuaID or ConvertStringToLuaID
    if not C or not get_data or not get_macro_data or not get_lib_entry then
        return { ok = false, reason = 'apis_unavailable', weapons = {} }
    end
    local u64 = actor_id
    if type(actor_id) == 'string' and to_u64 then
        local ok_u, v = pcall(to_u64, actor_id)
        if not ok_u or not v or v == 0 then
            return { ok = false, reason = 'actor_invalid', weapons = {} }
        end
        u64 = v
    end
    local ok_n, slot_count = pcall(function() return tonumber(C.GetNumUpgradeSlots(u64, '', 'weapon')) end)
    if not ok_n or not slot_count or slot_count < 0 or slot_count > 64 then
        return { ok = false, reason = 'slot_count_failed', weapons = {} }
    end
    local weapons = {}
    local uniform_macro = nil
    local mixed = false
    local any_modified = false
    local any_beam = false
    for slot = 1, slot_count do
        local ok_w, wid = pcall(C.GetUpgradeSlotCurrentComponent, u64, 'weapon', slot)
        if ok_w and wid and wid ~= 0 then
            local wref = to_lua_id and to_lua_id((tostring(wid):gsub('ULL$', ''))) or wid
            local ok_m, wmacro = pcall(get_data, wref, 'macro')
            if ok_m and type(wmacro) == 'string' and wmacro ~= '' then
                if uniform_macro == nil then
                    uniform_macro = wmacro
                elseif uniform_macro ~= wmacro then
                    mixed = true
                end
                local modified = false
                if C.GetInstalledWeaponMod and ffi_mod and ffi_mod.new then
                    local ok_mod, has_mod = pcall(function()
                        return C.GetInstalledWeaponMod(wid, ffi_mod.new('UIWeaponMod', {}))
                    end)
                    if ok_mod and has_mod then modified = true end
                end
                if modified then any_modified = true end
                local bullet_macro = nil
                if C.GetWeaponLaunchedMacro and ffi_mod and ffi_mod.string then
                    local ok_b, ptr = pcall(C.GetWeaponLaunchedMacro, wid)
                    if ok_b and ptr ~= nil and ptr ~= ffi_mod.NULL then
                        bullet_macro = ffi_mod.string(ptr)
                    end
                end
                local ok_e, entry = pcall(function()
                    local lib = get_macro_data(wmacro, 'infolibrary')
                    return lib and get_lib_entry(lib, wmacro) or nil
                end)
                local hs_shot = ok_e and type(entry) == 'table' and num(entry.hullshielddpshot) or nil
                local beam = ok_e and type(entry) == 'table' and (num(entry.isbeamweapon) or 0) ~= 0 or false
                local speed = ok_e and type(entry) == 'table' and num(entry.bulletspeed) or nil
                local other_nonzero = {}
                if ok_e and type(entry) == 'table' then
                    for _, ch in ipairs(OTHER_DAMAGE_CHANNELS) do
                        local cv = num(entry[ch])
                        if cv and cv ~= 0 then other_nonzero[ch] = cv end
                    end
                end
                if beam then any_beam = true end
                weapons[#weapons + 1] = {
                    slot = slot,
                    macro = wmacro,
                    bullet_macro = bullet_macro,
                    modified = modified,
                    hullshielddpshot = hs_shot,
                    isbeamweapon = beam,
                    bulletspeed = speed,
                    other_channels = other_nonzero,
                }
            end
        end
    end
    return {
        ok = true,
        slot_count = slot_count,
        weapon_count = #weapons,
        uniform_macro = (not mixed) and uniform_macro or nil,
        mixed = mixed,
        any_modified = any_modified,
        any_beam = any_beam,
        weapons = weapons,
    }
end

-- Create a strictly read-only combat observer collector.
function M.new(options)
    options = options or {}
    local max_events = options.max_events or 4096
    local deps = options.deps or {}
    local logger = options.log
    local on_event = options.on_event
    local events = {}
    local last_by_victim = {}
    local counts_by_kind = {}
    local counts_by_class = {}
    local stats = {
        total_events = 0,
        dropped_events = 0,
        lastattacktime_lag_events = 0,
        lua_readback_ok = 0,
        lua_readback_fail = 0,
        max_hullpct_diff = 0,
        max_shieldpct_diff = 0,
    }

    local function ingest(payload)
        local ev = type(payload) == 'table' and payload or M.parse_event(payload)
        if not ev then return nil end
        local prev = ev.victim ~= '0' and last_by_victim[ev.victim] or nil
        local cls = M.classify_damage(ev, prev)
        ev.classification = cls

        if ev.victim ~= '0' then
            local rb = M.read_lua_health(ev.victim, deps)
            ev.lua_readback = rb
            if rb.ok then
                stats.lua_readback_ok = stats.lua_readback_ok + 1
                if rb.hullpercent ~= nil and ev.hullpct >= 0 then
                    local dh = math.abs(rb.hullpercent - ev.hullpct)
                    if dh > stats.max_hullpct_diff then stats.max_hullpct_diff = dh end
                end
                if rb.shieldpercent ~= nil and ev.shieldpct >= 0 then
                    local ds = math.abs(rb.shieldpercent - ev.shieldpct)
                    if ds > stats.max_shieldpct_diff then stats.max_shieldpct_diff = ds end
                end
            else
                stats.lua_readback_fail = stats.lua_readback_fail + 1
            end
        end

        if (ev.kind == 'shield_damaged' or ev.kind == 'hull_damaged')
                and ev.last_att_time >= 0 and (ev.age - ev.last_att_time) > 1e-3 then
            stats.lastattacktime_lag_events = stats.lastattacktime_lag_events + 1
        end

        stats.total_events = stats.total_events + 1
        counts_by_kind[ev.kind] = (counts_by_kind[ev.kind] or 0) + 1
        counts_by_class[cls] = (counts_by_class[cls] or 0) + 1
        if ev.victim ~= '0' and (ev.kind == 'shield_damaged' or ev.kind == 'hull_damaged') then
            last_by_victim[ev.victim] = ev
        end
        if #events < max_events then
            events[#events + 1] = ev
        else
            stats.dropped_events = stats.dropped_events + 1
        end

        if logger and ev.lua_readback then
            local rb = ev.lua_readback
            logger(string.format(
                'seq=%d kind=%s class=%s victim=%s md_sh=%.3f md_hp=%.3f lua_ok=%s lua_shpct=%s lua_hppct=%s lua_sh=%s lua_shmax=%s lua_hp=%s lua_hpmax=%s',
                ev.seq, ev.kind, cls, ev.victim_label,
                ev.shieldpct, ev.hullpct, tostring(rb.ok),
                fmt_opt(rb.shieldpercent), fmt_opt(rb.hullpercent),
                fmt_opt(rb.shield), fmt_opt(rb.shieldmax),
                fmt_opt(rb.hull), fmt_opt(rb.hullmax)
            ))
        end
        if on_event then on_event(ev) end
        return ev
    end

    local function summary()
        return {
            total_events = stats.total_events,
            dropped_events = stats.dropped_events,
            lastattacktime_lag_events = stats.lastattacktime_lag_events,
            lua_readback_ok = stats.lua_readback_ok,
            lua_readback_fail = stats.lua_readback_fail,
            max_hullpct_diff = stats.max_hullpct_diff,
            max_shieldpct_diff = stats.max_shieldpct_diff,
            counts_by_kind = counts_by_kind,
            counts_by_class = counts_by_class,
        }
    end

    return {
        ingest = ingest,
        events = events,
        summary = summary,
    }
end

X4LAN_COMBAT_OBSERVER = M
return M
