-- The actual saved-baseline resolver receives a correlated synthetic native descriptor.
local ffi = require('ffi')
ffi.cdef('typedef uint64_t UniverseID;')
local resolve = dofile('tools/dev/authority_registry_resolve_string.lua')
local old, actor, captain = resolve.parse_hex('0x101'), resolve.parse_hex('0x201'), resolve.parse_hex('0x202')
local sector, zero = resolve.parse_hex('0x302'), ffi.new('UniverseID', 0)
local epoch, code, owner = ('a'):rep(32), 'ABC-401', 'argon'
local handler, request, registered, messages, logs
local live_code, live_owner, now, paused = code, owner, 0, false

ConvertStringToLuaID = function(id) return ffi.new('uint64_t', tonumber(id)) end
ConvertIDTo64Bit = function(id) return id end
GetComponentData = function(id, field)
    if id == sector then assert(field == 'macro'); return 'sector_a' end
    assert(id == actor)
    if field == 'owner' then return live_owner, captain end
    assert(field == 'isdocked')
    return false, false, false
end
GetCurRealTime = function() return now end
RegisterEvent = function(event, callback) assert(event == 'X4AuthorityJobDescriptorProbe.Result'); handler = callback end
AddUITriggeredEvent = function(screen, action, args)
    assert(screen == 'X4AuthorityJobDescriptorProbe' and action == 'Read')
    assert(args.epoch == epoch and args.index == 40)
    request = args.request_id
end
DebugError = function(row) logs[#logs + 1] = row end

package.loaded.ffi = {typeof=ffi.typeof, new=ffi.new, cdef=function() end, NULL=ffi.NULL,
    string=function(value) return value end, C={
        IsPlayerValid=function() return true end, IsGamePaused=function() return paused end,
        GetSaveFolderPath=function() return '/isolated/save/' end,
        IsValidComponent=function(id) return id == actor or id == sector or id == captain end,
        IsComponentOperational=function(id) return id == actor end,
        IsComponentClass=function(id, class)
            return (id == actor and class == 'ship') or (id == sector and class == 'sector')
                or (id == captain and class == 'npc')
        end,
        GetObjectIDCode=function(id) assert(id == actor); return live_code end,
        GetContextByClass=function(id) assert(id == actor); return sector end,
        GetPlayerControlledShipID=function() return zero end, GetPlayerOccupiedShipID=function() return zero end,
    },
}
dofile('src/authority/lua/saved_baseline_source_bootstrap.lua')
X4LAN_AUTHORITY_JOB_DESCRIPTOR_PROBE = {}
X4LAN_AUTHORITY_SOURCE_MOTION_SAMPLER = {new_x4=function(config)
    return {
        register=function(_, row, id)
            assert(id == actor and id ~= old and config.saved_baseline_guard(row, id))
            registered = id
            return true
        end,
        tick=function() return true end,
    }
end}
local source = dofile('tools/dev/authority_registered_npc_source.lua')

local function boot()
    messages, logs, registered, request = {}, {}, nil, nil
    local tick = source.new_x4({epoch=epoch, index=40, code=code, owner=owner, count=100,
        actor='0x101', save_path='/isolated/save', lease=('d'):rep(32), generation=('e'):rep(32),
        nonce=('f'):rep(32), lookup_nonce=('b'):rep(32)},
        {send=function(_, row) messages[#messages + 1] = row; return true end},
        GetCurRealTime, function() return true end)
    tick()
    assert(request ~= nil)
    handler(nil, table.concat({request, epoch, '40', 'ok', '0x201', 'ship_s_macro', 'argon',
        '0x302', 'sector_a'}, '|'))
    now = now + .15
    tick()
    return tick
end

local tick = boot()
assert(registered == actor and #messages == 0)
assert(#logs == 1 and logs[1]:find('|ABC%-401|argon|0x201$'))
paused = true
tick(); tick()
assert(#messages == 1 and messages[1]:find('|END|paused$'))
assert(#logs == 2)

paused, live_code = false, 'ABC-402'
boot()
assert(not registered and #messages == 1 and messages[1]:find('|END|code_mismatch$'))
live_code, live_owner = code, 'teladi'
boot()
assert(not registered and #messages == 1 and messages[1]:find('|END|owner_mismatch$'))
