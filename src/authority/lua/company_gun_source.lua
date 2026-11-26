-- Only emit sanitized records for exact, unmodified primary-gun contacts.
local cfg = X4LAN_CONFIG and X4LAN_CONFIG.authority_company_gun
if not cfg then return end
local ffi = require('ffi')
local C = ffi.C
for _, definition in ipairs({
    'typedef uint64_t UniverseID;',
    [[typedef struct {
        const char* Name; const char* RawName; const char* Ware; uint32_t Quality; const char* PropertyType;
        float DamageFactor; float CoolingFactor; float ReloadFactor; float SpeedFactor; float LifeTimeFactor;
        float MiningFactor; float StickTimeFactor; float ChargeTimeFactor; float BeamLengthFactor;
        uint32_t AddedAmount; float RotationSpeedFactor; float SurfaceElementFactor;
    } UIWeaponMod;]],
    'bool IsGamePaused(void);',
    'UniverseID GetPlayerControlledShipID(void);',
    'UniverseID GetPlayerOccupiedShipID(void);',
    'size_t GetNumUpgradeSlots(UniverseID componentid, const char* macroname, const char* upgradetypename);',
    'UniverseID GetUpgradeSlotCurrentComponent(UniverseID componentid, const char* upgradetypename, size_t slot);',
    'bool GetInstalledWeaponMod(UniverseID weaponid, UIWeaponMod* weaponmod);',
    'const char* GetWeaponLaunchedMacro(UniverseID weaponid);',
}) do pcall(ffi.cdef, definition) end

local M = {}
local actor, admitted_actor, armed, request, sequence, disabled = nil, nil, false, 0, 0, false
local screen = 'X4AuthorityCompanyGunSource'
local stock_weapon = 'weapon_gen_s_gatling_01_mk2_macro'
local other_channels = {
    'shieldonlydpshot', 'hullonlydpshot', 'hullnoshielddpshot',
    'hullshieldareadpshot', 'shieldonlyareadpshot', 'hullonlyareadpshot', 'hullnoshieldareadpshot',
}

function M.stop()
    if not actor then return end
    actor, armed = nil, false
    AddUITriggeredEvent(screen, 'Disarm', {})
end

function M.tick(seat)
    local controlled = seat and seat:controlled_actor()
    if controlled and admitted_actor and controlled ~= admitted_actor and not disabled then
        disabled = true
        DebugError('[X4LAN] AUTH_COMPANY_GUN_STATUS unavailable')
    end
    if disabled or not controlled then
        M.stop()
        return
    end
    if not actor then
        actor = controlled
        admitted_actor = controlled
        request = request + 1
        assert(AddUITriggeredEvent(screen, 'Arm', {epoch=cfg.epoch, index=cfg.index, request=request}) ~= false)
    end
end

RegisterEvent(screen .. '.Ready', function(_, receipt)
    if actor and receipt == string.format('%d|%s|%d', request, cfg.epoch, cfg.index) then
        if not armed then
            armed = true
            DebugError(string.format('[X4LAN] AUTH_COMPANY_GUN_STATUS armed|%s|%d', cfg.epoch, cfg.index))
        end
    end
end)

local function stock_primary(source, weapon)
    local ship = ConvertIDTo64Bit(source)
    local gun = ConvertIDTo64Bit(weapon)
    if not ship or ship == 0 or not gun or gun == 0
            or C.GetPlayerControlledShipID() ~= ship or C.GetPlayerOccupiedShipID() ~= ship then return false end
    local slots = tonumber(C.GetNumUpgradeSlots(ship, '', 'weapon'))
    if not slots or slots < 1 or slots > 64 then return false end
    local primary = false
    for slot = 1, slots do
        if C.GetUpgradeSlotCurrentComponent(ship, 'weapon', slot) == gun then
            primary = true
            break
        end
    end
    if not primary or C.GetInstalledWeaponMod(gun, ffi.new('UIWeaponMod')) then return false end
    local bullet = C.GetWeaponLaunchedMacro(gun)
    if bullet == nil or bullet == ffi.NULL or ffi.string(bullet) ~= 'bullet_gen_s_gatling_01_mk2_macro' then return false end
    local entry = GetLibraryEntry(GetMacroData(stock_weapon, 'infolibrary'), stock_weapon)
    if type(entry) ~= 'table' or tonumber(entry.hullshielddpshot) ~= 26 or entry.isbeamweapon == true
            or (tonumber(entry.isbeamweapon) or 0) ~= 0 then return false end
    for _, channel in ipairs(other_channels) do
        if (tonumber(entry[channel]) or 0) ~= 0 then return false end
    end
    return true
end

local function contact(value)
    if not actor or not armed or C.IsGamePaused() or type(value) ~= 'string' then return end
    local epoch, shooter, target, source, weapon = value:match('^([0-9a-f]+)|(%d+)|(%d+)|(0x%x+)|(0x%x+)$')
    target = tonumber(target)
    if epoch ~= cfg.epoch or tonumber(shooter) ~= cfg.index or source ~= actor
            or not target or target < 1 or target > 200000 or target == cfg.index then return end
    if not stock_primary(source, weapon) then return end
    assert(sequence < 9007199254740991)
    sequence = sequence + 1
    DebugError(string.format('[X4LAN] AUTH_COMPANY_GUN %s|%d|%d|%.0f', epoch, cfg.index, target, sequence))
end

RegisterEvent(screen .. '.Contact', function(_, value)
    if disabled then return end
    local ok = pcall(contact, value)
    if not ok then
        disabled = true
        pcall(M.stop)
        DebugError('[X4LAN] AUTH_COMPANY_GUN_STATUS unavailable')
    end
end)

X4LAN_COMPANY_GUN_SOURCE = M
return M
