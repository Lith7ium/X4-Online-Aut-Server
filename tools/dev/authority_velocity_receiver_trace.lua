-- Test-only tracer for the native Windows velocity receiver run.
-- Wraps the REAL X4LAN_COMPANY_HULL_PLAYBACK.new options that X4LAN_COMPANY_COCKPIT_PLAYBACK passes, so each
-- bind, observation, dispatched write and delayed readback is logged. Readbacks also carry an independent FFI
-- position read of the bound hull. Production behaviour is unchanged: every wrapper returns the original result.
local cfg = X4NEON_COMPANY_COCKPIT
if type(cfg) ~= 'table' or cfg.enabled ~= true or cfg.velocity_trace ~= true then return end

local hull_playback = assert(X4LAN_COMPANY_HULL_PLAYBACK, 'hull playback module missing')
local cockpit_playback = assert(X4LAN_COMPANY_COCKPIT_PLAYBACK, 'cockpit playback module missing')
local ffi = require('ffi')
local C = ffi.C
local function declare(text) pcall(ffi.cdef, text) end
declare('typedef uint64_t UniverseID;')
declare('typedef struct { float x; float y; float z; float yaw; float pitch; float roll; } UIPosRot;')
declare('UIPosRot GetObjectPositionInSector(UniverseID objectid);')
declare('UniverseID GetPlayerOccupiedShipID(void);')

local function log(msg)
    DebugError('[X4LAN] AUTH_COMPANY_COCKPIT TRACE ' .. msg)
end

local function dist3(a, b)
    local dx, dy, dz = a.x - b.x, a.y - b.y, a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)
end

-- The offline simulation replaces these two engine reads; X4 always uses the FFI.
local ffi_pose = cfg.trace_position or function(id)
    local p = C.GetObjectPositionInSector(id)
    return {x = tonumber(p.x), y = tonumber(p.y), z = tonumber(p.z)}
end
local occupied = cfg.trace_occupied or function() return tonumber(C.GetPlayerOccupiedShipID()) end
declare('UniverseID GetContextByClass(UniverseID componentid, const char* classname, bool includeself);')
declare('UniverseID GetZoneAt(UniverseID sectorid, UIPosRot* uioffset);')
-- Zone of the hull now, or of a sector destination; -1 when the engine read is unavailable.
local function zone_of(id, destination)
    local ok, zone = pcall(function()
        if not destination then return tonumber(C.GetContextByClass(id, 'zone', false)) end
        local offset = ffi.new('UIPosRot[1]')
        offset[0].x, offset[0].y, offset[0].z = destination.x, destination.y, destination.z
        return tonumber(C.GetZoneAt(C.GetContextByClass(id, 'sector', false), offset))
    end)
    return ok and zone or -1
end

local function native_identity(actor)
    local ok, sector, code = pcall(function()
        return tostring(C.GetContextByClass(actor, 'sector', false)), ffi.string(C.GetObjectIDCode(actor))
    end)
    return ok and sector or '-', ok and code or '-'
end

local function xyz(p)
    return ('%.4f,%.4f,%.4f'):format(p.x, p.y, p.z)
end

-- The occupied ship's sector XYZ, so spawn drift can be told apart from playback.
local function own_pose()
    local own = occupied()
    if not own or own == 0 then return 'none' end
    local ok, pose = pcall(ffi_pose, own)
    return ok and xyz(pose) or 'none'
end

local original_cockpit_new = cockpit_playback.new
cockpit_playback.new = function(options)
    -- Logged once when the stream starts, before any sample, so the runner can place the trajectory.
    local own = occupied()
    local range = -1
    if own and own ~= 0 then
        local ok, value = pcall(function()
            return GetComponentData(ConvertStringToLuaID(tostring(own)), 'maxradarrange')
        end)
        if ok and type(value) == 'number' then range = value end
        log(('OWN pose=%s radar_range=%.1f'):format(xyz(ffi_pose(own)), range))
    end
    return original_cockpit_new(options)
end

local original_hull_new = hull_playback.new
hull_playback.new = function(options)
    local binding = options.binding or {}
    local actor = tonumber(binding.actor)
    local clock = options.clock
    local bind_zone = actor and zone_of(actor) or -1
    log(('BIND index=%s actor=%s code=%s sector=%s slot=%s velocity_steps=%s zone=%s t=%.6f'):format(
        tostring(binding.index), tostring(binding.actor), tostring(binding.code), tostring(binding.sector_macro),
        tostring(binding.slot), tostring(options.velocity_steps), tostring(bind_zone), clock()))

    local record, write = options.record, options.write
    options.record = function(ev)
        if ev.kind == 'observation' then
            log(('OBS seq=%s phase=%s t=%.6f step=%.4f actual=%s target=%s'):format(
                tostring(ev.source_sequence), tostring(ev.phase), ev.local_time, dist3(ev.actual, ev.target),
                xyz(ev.actual), xyz(ev.target)))
        elseif ev.kind == 'readback' then
            local independent = actor and ffi_pose(actor)
            local sector, code = native_identity(actor)
            log(('READBACK seq=%s t=%.6f elapsed=%.6f error=%.4f ffi_vs_actual=%.4f ffi_error=%.4f ffi=%s' ..
                ' zone=%s actor=%s sector=%s code=%s own=%s'):format(
                tostring(ev.source_sequence), ev.local_time, ev.elapsed, ev.error,
                independent and dist3(independent, ev.actual) or -1,
                independent and dist3(independent, ev.target) or -1,
                independent and xyz(independent) or 'none', tostring(zone_of(actor)), binding.actor, sector, code,
                own_pose()))
        end
        return record(ev)
    end
    options.write = function(self, target, before, recheck)
        local zone_target = actor and zone_of(actor, target) or -1
        local zone_before = actor and zone_of(actor) or -1
        local sector, code = native_identity(actor)
        local own_before = own_pose()
        local ok = write(self, target, before, recheck)
        log(('WRITE ok=%s t=%.6f step=%.4f recheck=%s zone_bind=%s zone_now=%s zone_target=%s target=%s' ..
            ' zone_before=%s actor=%s sector=%s code=%s own_before=%s own_after=%s'):format(
            tostring(ok), clock(), before and dist3(before, target) or -1, tostring(recheck ~= nil),
            tostring(bind_zone), tostring(actor and zone_of(actor) or -1), tostring(zone_target), xyz(target), tostring(zone_before), binding.actor, sector, code,
            own_before, own_pose()))
        return ok
    end
    return original_hull_new(options)
end
