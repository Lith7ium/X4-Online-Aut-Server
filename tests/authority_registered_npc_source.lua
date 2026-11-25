-- Drive the production resolver, sampler and descriptor with simulated X4 APIs.
local ffi = require('ffi')
ffi.cdef('typedef uint64_t UniverseID;')
local resolve = dofile('tools/dev/authority_registry_resolve_string.lua')
local actor, captain = resolve.parse_hex('0x201'), resolve.parse_hex('0x202')
local sector, zero = resolve.parse_hex('0x302'), ffi.new('UniverseID', 0)
local epoch, code, owner = ('a'):rep(32), 'ABC-401', 'argon'
local handler, request, messages, logs
local live_code, live_owner, now, host_time, paused = code, owner, 0, 999.95, false
local x, inline_sample = 100, false

local function reply()
    assert(request)
    local id = request
    request = nil
    handler(nil, table.concat({id, epoch, '40', 'ok', '0x201', 'ship_s_macro', owner,
        '0x302', 'sector_a'}, '|'))
end

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
    assert(args.epoch == epoch and args.index == 40 and request == nil)
    request = args.request_id
    if request:sub(1, 1) == 'f' then
        host_time = host_time + .001
        if inline_sample then reply() end
    end
end
DebugError = function(row) logs[#logs + 1] = row end

package.loaded.ffi = {typeof=ffi.typeof, new=ffi.new, cdef=function() end, NULL=ffi.NULL,
    string=function(value) return value end, C={
        IsPlayerValid=function() return true end, IsGamePaused=function() return paused end,
        GetSaveFolderPath=function() return '/isolated/45841/save/' end,
        IsValidComponent=function(id) return id == actor or id == sector or id == captain end,
        IsComponentOperational=function(id) return id == actor end,
        IsComponentClass=function(id, class)
            return (id == actor and class == 'ship') or (id == sector and class == 'sector')
                or (id == captain and class == 'npc')
        end,
        GetObjectIDCode=function(id) assert(id == actor); return live_code end,
        GetContextByClass=function(id) assert(id == actor); return sector end,
        GetPlayerControlledShipID=function() return zero end, GetPlayerOccupiedShipID=function() return zero end,
        GetCurrentGameTime=function() return 500 end,
        GetObjectPositionInSector=function(id) assert(id == actor); return {x=x, y=200, z=300} end,
    },
}
dofile('src/authority/lua/saved_baseline_source_bootstrap.lua')
dofile('tools/dev/authority_job_descriptor_probe.lua')
dofile('src/authority/lua/source_motion_sampler.lua')
local source = dofile('tools/dev/authority_registered_npc_source.lua')

local function boot()
    messages, logs, request = {}, {}, nil
    local tick = source.new_x4({epoch=epoch, index=40, code=code, owner=owner, count=100,
        actor='0x101', save_path='/isolated/45841/save', lease=('d'):rep(32), generation=('e'):rep(32),
        nonce=('f'):rep(32), lookup_nonce=('b'):rep(32)},
        {send=function(_, row) messages[#messages + 1] = row; return true end},
        function() return host_time end, function() return true end)
    tick()
    reply()
    now = now + .15
    tick()
    return tick
end

local tick = boot()
assert(request and #messages == 0)
assert(#logs == 1 and logs[1]:find('|ABC%-401|argon|0x201$'))

-- MD replies on later frames. V3 must continue even at a repeated game time.
local function delayed_reply()
    now = now + .05
    tick()
    reply()
end
local function next_sample()
    now = now + .09
    tick()
    assert(request == nil)
    now = now + .02
    tick()
    assert(request)
end
host_time = 1000
delayed_reply()
assert(#messages == 1 and messages[1]:find('|SAMPLE3|%x+|40|1|ABC%-401|argon|0x201|sector_a|500|1000|100|200|300|%-|%-|%-$'))
assert(logs[2]:find('SENT|seq=1|capture=1000|velocity=absent|updates=2|max_update_gap_ms=50.000'
    .. '|capture_to_dispatch_ms=-|dispatch_tick_ms=1.000|md_reply_wait_ms=49.000|reply_to_capture_ms=0.000', 1, true))
host_time = 1000.004
next_sample()
-- Pose 2 is still sent when its capture interval is too short for velocity.
host_time, x = 1000.01, 101
delayed_reply()
assert(#messages == 2 and messages[2]:find('|500|1000%.01|101|200|300|%-|%-|%-$'))
assert(logs[3]:find('SENT|seq=2|capture=1000.01|velocity=absent|updates=3|max_update_gap_ms=6.000'
    .. '|capture_to_dispatch_ms=4.000|dispatch_tick_ms=1.000|md_reply_wait_ms=5.000|reply_to_capture_ms=0.000', 1, true))
next_sample()
host_time, x = 1000.5, 110
delayed_reply()
assert(#messages == 3 and messages[3]:find('|500|1000%.5|110|200|300|20|0|0$'))
assert(logs[4]:find('SENT|seq=3|capture=1000%.5|velocity=present$'))
next_sample()
host_time, x = 1000.75, 115
delayed_reply()
assert(#messages == 4 and #logs == 4)

paused = true
tick(); tick()
assert(#messages == 5 and messages[5]:find('|END|paused$'))
assert(#logs == 5)

-- A truly inline MD reply would fail at once, unlike the native run's later sector loss.
paused, inline_sample = false, true
boot()
assert(#messages == 1 and messages[1]:find('|END|sampler_event$'))

inline_sample, live_code = false, 'ABC-402'
boot()
assert(request == nil and #messages == 1 and messages[1]:find('|END|code_mismatch$'))
live_code, live_owner = code, 'teladi'
boot()
assert(request == nil and #messages == 1 and messages[1]:find('|END|owner_mismatch$'))
